"""Offline ingestion library for Step 1A retirement observations.

Reads one explicit source observation path, validates it against the full
committed v1 contract, and appends one archive record beneath one explicit
destination archive root.  Append-only *by tool behavior*: it never overwrites
an existing record (exclusive hard-link create), and on any failure leaves no
partial record visible.

It authorizes nothing, computes no coverage/sufficiency, and is never imported
by production runtime code.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from investment_orchestrator.research import step1a_retirement_observation as p1a

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence.source_validation import (
    classify_observation,
)


_SAFE_RECORD_BASENAME_RE = re.compile(
    r"(?:[0-9]+|nogen)__(?:[0-9a-f]{12}|noid)__[0-9a-f]{64}\.json\Z"
)


def _safe_record_basename(path: Path) -> str:
    """Return a basename safe to surface in a fail-closed error.

    Candidate discovery may encounter an operator-supplied filename.  Never
    echo an arbitrary basename from such a file through the library or CLI.
    """
    return path.name if _SAFE_RECORD_BASENAME_RE.fullmatch(path.name) else "invalid_archive_record_basename"


class ArchiveLayoutError(Exception):
    """Raised when the archive layout version is missing-incompatible/malformed.

    Carries a safe token only; never raw paths or exception text.
    """

    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(token)


class ArchiveIngestionError(Exception):
    """Raised for operator-side errors (e.g. unreadable source); safe token only."""

    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(token)


class ExistingRecordIntegrityError(Exception):
    """An already-archived record consulted for a duplicate/conflict decision
    failed independent re-verification (F2).

    Fail-closed: the current ingestion attempt stops, nothing is written, the
    existing record is never overwritten, and none of its (possibly corrupted)
    content is copied anywhere.  Carries only the code-owned token and the safe
    hash-derived record basename - never raw record content.  Formal corruption
    handling belongs to a future verifier/indexer design.
    """

    def __init__(self, record_basename: str) -> None:
        self.token = c.EXISTING_RECORD_INTEGRITY_FAILED
        self.record_basename = record_basename
        super().__init__(c.EXISTING_RECORD_INTEGRITY_FAILED)


@dataclass(frozen=True)
class IngestResult:
    decision: str
    reason_tokens: tuple[str, ...]
    archived_path: str | None
    duplicate: bool
    conflict: bool
    record_content_sha256: str | None
    source_file_sha256: str
    source_canonical_payload_sha256: str | None


def ingest_observation(
    *,
    source_path: Path,
    dest_root: Path,
    claimed_provenance: str = c.DEFAULT_PROVENANCE,
    provenance_claim_source: str = c.PROVENANCE_CLAIM_SOURCE_DEFAULT,
    tool_identity: Mapping[str, str] | None = None,
    archived_at: str | None = None,
) -> IngestResult:
    """Validate one source observation and append it to the archive.

    ``tool_identity`` and ``archived_at`` are injectable for deterministic tests;
    the CLI never exposes them.  ``claimed_provenance`` is stored verbatim as an
    UNVERIFIED claim - never verified, never inferred, never evaluated here.
    """
    source_path = Path(source_path)
    dest_root = Path(dest_root)
    if claimed_provenance not in c.PROVENANCE_VALUES:
        raise ArchiveIngestionError("provenance_claim_invalid")
    identity = dict(tool_identity) if tool_identity is not None else c.resolve_tool_identity()
    stamp = archived_at if archived_at is not None else datetime.now(timezone.utc).isoformat()

    raw = _read_source_bytes(source_path)
    source_file_sha256 = _sha256_bytes(raw)

    # Layout must be initialized/compatible before writing any record.
    _ensure_layout(dest_root)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _write_rejected(
            dest_root=dest_root,
            source_path=source_path,
            source_file_sha256=source_file_sha256,
            reason_tokens=(c.REASON_SOURCE_NOT_VALID_JSON,),
            identity=identity,
            stamp=stamp,
        )

    result = classify_observation(payload)

    if result.decision == c.DECISION_REJECTED:
        return _write_rejected(
            dest_root=dest_root,
            source_path=source_path,
            source_file_sha256=source_file_sha256,
            reason_tokens=result.reason_tokens,
            identity=identity,
            stamp=stamp,
        )

    return _write_accepted_or_quarantined(
        dest_root=dest_root,
        source_path=source_path,
        payload=payload,
        result=result,
        source_file_sha256=source_file_sha256,
        claimed_provenance=claimed_provenance,
        provenance_claim_source=provenance_claim_source,
        identity=identity,
        stamp=stamp,
    )


# --- accepted / quarantined --------------------------------------------------
def _write_accepted_or_quarantined(
    *,
    dest_root: Path,
    source_path: Path,
    payload: Mapping[str, Any],
    result: Any,
    source_file_sha256: str,
    claimed_provenance: str,
    provenance_claim_source: str,
    identity: Mapping[str, str],
    stamp: str,
) -> IngestResult:
    decision = result.decision
    canonical_payload_sha256 = p1a.canonical_sha256(payload)
    if canonical_payload_sha256 is None:
        # Non-serializable payload cannot be safely preserved; reject.
        return _write_rejected(
            dest_root=dest_root,
            source_path=source_path,
            source_file_sha256=source_file_sha256,
            reason_tokens=(c.REASON_RAW_CONTENT_UNSAFE,),
            identity=identity,
            stamp=stamp,
        )

    observation_id = _stored_observation_id(payload)

    # Cross-record conflict: same observation id, different canonical content.
    conflict_path = _find_observation_id_conflict(
        dest_root, observation_id, canonical_payload_sha256
    )
    if conflict_path is not None:
        return _write_rejected(
            dest_root=dest_root,
            source_path=source_path,
            source_file_sha256=source_file_sha256,
            reason_tokens=(c.REASON_OBSERVATION_ID_CONTENT_CONFLICT,),
            identity=identity,
            stamp=stamp,
        )

    envelope = _build_envelope(
        decision=decision,
        reason_tokens=result.reason_tokens,
        payload=payload,
        recomputed=result.recomputed_identity,
        source_path=source_path,
        source_file_sha256=source_file_sha256,
        canonical_payload_sha256=canonical_payload_sha256,
        claimed_provenance=claimed_provenance,
        provenance_claim_source=provenance_claim_source,
        identity=identity,
        stamp=stamp,
    )
    record_content_sha256 = envelope["archive_record_content_sha256"]

    filename = _record_filename(payload, observation_id, canonical_payload_sha256)
    target = dest_root / decision / filename
    written = _exclusive_write_json(target, envelope)
    duplicate = not written
    if duplicate:
        # Same content-addressed filename already present: fully re-verify the
        # existing record (F2), then confirm it is a true duplicate (same
        # canonical payload), else flag a collision (no overwrite).
        if not _existing_is_same_payload(target, canonical_payload_sha256, decision):
            return _write_rejected(
                dest_root=dest_root,
                source_path=source_path,
                source_file_sha256=source_file_sha256,
                reason_tokens=(c.REASON_ARCHIVE_FILENAME_COLLISION,),
                identity=identity,
                stamp=stamp,
            )
    return IngestResult(
        decision=decision,
        reason_tokens=tuple(result.reason_tokens),
        archived_path=str(target),
        duplicate=duplicate,
        conflict=False,
        record_content_sha256=record_content_sha256,
        source_file_sha256=source_file_sha256,
        source_canonical_payload_sha256=canonical_payload_sha256,
    )


def _build_envelope(
    *,
    decision: str,
    reason_tokens: tuple[str, ...],
    payload: Mapping[str, Any],
    recomputed: Mapping[str, Any],
    source_path: Path,
    source_file_sha256: str,
    canonical_payload_sha256: str,
    claimed_provenance: str,
    provenance_claim_source: str,
    identity: Mapping[str, str],
    stamp: str,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "archive_record_schema_version": c.ARCHIVE_RECORD_SCHEMA_VERSION,
        "archive_layout_version": c.ARCHIVE_LAYOUT_VERSION,
        "archive_tool_version": identity.get("tool_version"),
        "archive_tool_commit": identity.get("tool_commit"),
        "archived_at": stamp,
        "ingestion_decision": decision,
        "ingestion_reason_tokens": list(reason_tokens),
        "claimed_evidence_provenance": claimed_provenance,
        "provenance_claim_source": provenance_claim_source,
        "provenance_verified": False,
        "source_metadata": _safe_source_metadata(payload, source_path),
        "source_file_sha256": source_file_sha256,
        "source_canonical_payload_sha256": canonical_payload_sha256,
        "recomputed_identity": {
            "composite_config_fingerprint": recomputed.get("composite_config_fingerprint"),
            "coverage_key": recomputed.get("coverage_key"),
            "observation_id": recomputed.get("observation_id"),
        },
        # Preserved WITHOUT semantic repair or field mutation.  This is the
        # parsed observation mapping, not the original JSON bytes.
        "observation_payload": payload,
    }
    envelope["archive_record_content_sha256"] = _record_content_hash(envelope)
    return envelope


def _record_content_hash(envelope: Mapping[str, Any]) -> str:
    without_self = {
        k: v for k, v in envelope.items() if k != "archive_record_content_sha256"
    }
    digest = p1a.canonical_sha256(without_self)
    if digest is None:  # pragma: no cover - envelope is always serializable
        raise ArchiveIngestionError("archive_record_not_serializable")
    return digest


def _safe_source_metadata(payload: Mapping[str, Any], source_path: Path) -> dict[str, Any]:
    coverage = payload.get("coverage_identity")
    return {
        "source_basename": source_path.name,
        "source_observation_id": _stored_observation_id(payload),
        "source_coverage_key": coverage.get("coverage_key")
        if isinstance(coverage, Mapping)
        else None,
        "source_git_commit": _clean_payload_commit(payload),
        "source_schema_version": payload.get("schema_version"),
    }


def _clean_payload_commit(payload: Mapping[str, Any]) -> str | None:
    """The payload's clean git commit, or ``None`` - one source of truth shared
    by envelope construction and existing-record verification."""
    code = payload.get("code_identity")
    if isinstance(code, Mapping) and code.get("git_state") == "clean":
        candidate = code.get("git_commit")
        if p1a.is_git_commit(candidate):
            return candidate
    return None


# --- rejected ----------------------------------------------------------------
def _write_rejected(
    *,
    dest_root: Path,
    source_path: Path,
    source_file_sha256: str,
    reason_tokens: tuple[str, ...],
    identity: Mapping[str, str],
    stamp: str,
) -> IngestResult:
    record = {
        "archive_record_schema_version": c.ARCHIVE_REJECTED_RECORD_SCHEMA_VERSION,
        "archive_layout_version": c.ARCHIVE_LAYOUT_VERSION,
        "archive_tool_version": identity.get("tool_version"),
        "archive_tool_commit": identity.get("tool_commit"),
        "archived_at": stamp,
        "ingestion_decision": c.DECISION_REJECTED,
        "ingestion_reason_tokens": list(reason_tokens),
        "source_basename": source_path.name,
        "source_file_sha256": source_file_sha256,
    }
    filename = f"rejected__{source_file_sha256[:16]}__{source_file_sha256}.json"
    target = dest_root / c.DECISION_REJECTED / filename
    written = _exclusive_write_json(target, record)
    return IngestResult(
        decision=c.DECISION_REJECTED,
        reason_tokens=tuple(reason_tokens),
        archived_path=str(target),
        duplicate=not written,
        conflict=c.REASON_OBSERVATION_ID_CONTENT_CONFLICT in reason_tokens,
        record_content_sha256=None,
        source_file_sha256=source_file_sha256,
        source_canonical_payload_sha256=None,
    )


# --- layout ------------------------------------------------------------------
def _ensure_layout(dest_root: Path) -> None:
    version_path = dest_root / c.ARCHIVE_LAYOUT_VERSION_FILENAME
    if version_path.exists():
        _validate_layout_version(version_path)
    else:
        dest_root.mkdir(parents=True, exist_ok=True)
        try:
            _exclusive_write_text(version_path, c.ARCHIVE_LAYOUT_VERSION + "\n")
        except FileExistsError:
            # Concurrent initializer won the race; validate what they wrote.
            _validate_layout_version(version_path)
    for partition in c.PARTITIONS:
        (dest_root / partition).mkdir(parents=True, exist_ok=True)


def _validate_layout_version(version_path: Path) -> None:
    try:
        content = version_path.read_text(encoding="utf-8").strip()
    except OSError:
        raise ArchiveLayoutError("archive_layout_version_unreadable") from None
    if content != c.ARCHIVE_LAYOUT_VERSION:
        raise ArchiveLayoutError("archive_layout_version_incompatible")


# --- existing-record verification (F2) ----------------------------------------
# Before any existing archive record may influence a duplicate / conflict /
# collision decision, it is independently re-verified end to end.  Stored
# metadata (observation id, canonical hash, self-hash) is never trusted alone;
# filename prefixes are candidate discovery only, never identity.

_RECORD_ENVELOPE_KEYS = frozenset(
    {
        "archive_record_schema_version",
        "archive_layout_version",
        "archive_tool_version",
        "archive_tool_commit",
        "archived_at",
        "ingestion_decision",
        "ingestion_reason_tokens",
        "claimed_evidence_provenance",
        "provenance_claim_source",
        "provenance_verified",
        "source_metadata",
        "source_file_sha256",
        "source_canonical_payload_sha256",
        "recomputed_identity",
        "observation_payload",
        "archive_record_content_sha256",
    }
)
_SOURCE_METADATA_KEYS = frozenset(
    {
        "source_basename",
        "source_observation_id",
        "source_coverage_key",
        "source_git_commit",
        "source_schema_version",
    }
)
_RECOMPUTED_IDENTITY_KEYS = frozenset(
    {"composite_config_fingerprint", "coverage_key", "observation_id"}
)


def _verify_existing_record(path: Path, expected_partition: str) -> Mapping[str, Any]:
    """Fully re-verify an existing archive record before trusting it.

    Raises :class:`ExistingRecordIntegrityError` (fail-closed for the current
    ingestion attempt) on any envelope, integrity, payload-identity,
    classification, or envelope-to-payload consistency failure.  The exception
    carries only the code-owned token and the safe record basename.
    """
    record = _read_json_or_none(path)
    if not _existing_record_is_valid(record, expected_partition):
        raise ExistingRecordIntegrityError(_safe_record_basename(path))
    return record


def _existing_record_is_valid(record: Any, expected_partition: str) -> bool:
    # Envelope shape and versions.
    if not isinstance(record, Mapping) or frozenset(record.keys()) != _RECORD_ENVELOPE_KEYS:
        return False
    if record["archive_record_schema_version"] != c.ARCHIVE_RECORD_SCHEMA_VERSION:
        return False
    if record["archive_layout_version"] != c.ARCHIVE_LAYOUT_VERSION:
        return False
    tool_version = record["archive_tool_version"]
    if not isinstance(tool_version, str) or not tool_version:
        return False
    tool_commit = record["archive_tool_commit"]
    if tool_commit != "unavailable" and not p1a.is_git_commit(tool_commit):
        return False
    if not p1a.is_valid_generated_at(record["archived_at"]):
        return False
    decision = record["ingestion_decision"]
    if decision != expected_partition or decision not in (
        c.DECISION_ACCEPTED,
        c.DECISION_QUARANTINED,
    ):
        return False
    # Provenance stays an unverified claim with valid domain values.
    if record["provenance_verified"] is not False:
        return False
    if record["claimed_evidence_provenance"] not in c.PROVENANCE_VALUES:
        return False
    claim_source = record["provenance_claim_source"]
    if not isinstance(claim_source, str) or not claim_source:
        return False
    metadata = record["source_metadata"]
    if not isinstance(metadata, Mapping) or frozenset(metadata.keys()) != _SOURCE_METADATA_KEYS:
        return False
    basename = metadata["source_basename"]
    if not isinstance(basename, str) or not basename:
        return False
    if not p1a.is_canonical_sha256(record["source_file_sha256"]):
        return False
    # Record integrity: the self-hash must recompute exactly.
    stored_record_hash = record["archive_record_content_sha256"]
    if not p1a.is_canonical_sha256(stored_record_hash):
        return False
    if _record_content_hash(record) != stored_record_hash:
        return False
    # Payload identity: the stored canonical hash must recompute exactly.
    payload = record["observation_payload"]
    if not isinstance(payload, Mapping):
        return False
    stored_payload_hash = record["source_canonical_payload_sha256"]
    if not p1a.is_canonical_sha256(stored_payload_hash):
        return False
    if p1a.canonical_sha256(payload) != stored_payload_hash:
        return False
    # Source-observation contract: strict Phase 1A validation, including the
    # recomputation of composite fingerprint / coverage key / observation id
    # against the payload's own fields, must reproduce the stored decision.
    result = classify_observation(payload)
    if result.decision != decision:
        return False
    reason_tokens = record["ingestion_reason_tokens"]
    if not isinstance(reason_tokens, list) or tuple(reason_tokens) != result.reason_tokens:
        return False
    recomputed = record["recomputed_identity"]
    if not isinstance(recomputed, Mapping) or frozenset(recomputed.keys()) != _RECOMPUTED_IDENTITY_KEYS:
        return False
    for key in _RECOMPUTED_IDENTITY_KEYS:
        if recomputed[key] != result.recomputed_identity.get(key):
            return False
    # Envelope-to-payload consistency.
    if metadata["source_observation_id"] != _stored_observation_id(payload):
        return False
    coverage = payload.get("coverage_identity")
    payload_coverage_key = coverage.get("coverage_key") if isinstance(coverage, Mapping) else None
    if metadata["source_coverage_key"] != payload_coverage_key:
        return False
    if metadata["source_schema_version"] != payload.get("schema_version"):
        return False
    if metadata["source_git_commit"] != _clean_payload_commit(payload):
        return False
    return True


# --- duplicate / conflict scanning -------------------------------------------
def _find_observation_id_conflict(
    dest_root: Path,
    observation_id: str | None,
    canonical_payload_sha256: str,
) -> Path | None:
    """Return an existing record path iff same observation id, different content.

    Bounded, deterministic destination-candidate scan (globbed by the
    observation-id prefix); it builds no cumulative index.  Every candidate is
    fully re-verified via :func:`_verify_existing_record` before any of its
    identity fields are trusted; a candidate failing verification fails the
    current ingestion closed.  Final decisions compare complete observation ids
    and full canonical hashes - never filename prefixes.
    """
    if observation_id is None:
        return None
    prefix = observation_id[:12]
    for partition in (c.PARTITION_ACCEPTED, c.PARTITION_QUARANTINED):
        directory = dest_root / partition
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.glob(f"*__{prefix}__*.json")):
            record = _verify_existing_record(candidate, partition)
            existing_id = _stored_observation_id(record["observation_payload"])
            if existing_id != observation_id:
                continue
            if record["source_canonical_payload_sha256"] != canonical_payload_sha256:
                return candidate
    return None


def _existing_is_same_payload(
    target: Path,
    canonical_payload_sha256: str,
    expected_partition: str,
) -> bool:
    """True iff the fully re-verified existing record holds the same payload.

    Raises :class:`ExistingRecordIntegrityError` if the existing record fails
    verification - a corrupted/forged record must never yield a silent
    duplicate no-op that swallows a genuine source observation.
    """
    record = _verify_existing_record(target, expected_partition)
    return record["source_canonical_payload_sha256"] == canonical_payload_sha256


# --- append-only, no-overwrite atomic write ----------------------------------
def _exclusive_write_json(target: Path, data: Mapping[str, Any]) -> bool:
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return _exclusive_write_text(target, text)


def _exclusive_write_text(target: Path, text: str) -> bool:
    """Append-only write: fully write a temp file, then atomically hard-link it.

    Returns ``True`` if the target was created, ``False`` if it already existed
    (``os.link`` raises ``FileExistsError`` rather than overwriting).  Temp files
    live in the destination directory (same filesystem), are fsync-ed before the
    link, and are removed on every path - so no partial or overwritten final
    record can ever become visible, even under concurrent writers.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}.{os.urandom(6).hex()}")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _best_effort_unlink(tmp)
        raise
    try:
        os.link(tmp, target)
    except FileExistsError:
        return False
    except BaseException:
        raise
    finally:
        _best_effort_unlink(tmp)
    return True


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# --- helpers -----------------------------------------------------------------
def _record_filename(
    payload: Mapping[str, Any],
    observation_id: str | None,
    canonical_payload_sha256: str,
) -> str:
    generated_at = None
    identity = payload.get("observation_identity")
    if isinstance(identity, Mapping):
        generated_at = identity.get("generated_at")
    gen = _compact_generated_at(generated_at)
    obs = observation_id[:12] if isinstance(observation_id, str) else "noid"
    return f"{gen}__{obs}__{canonical_payload_sha256}.json"


def _compact_generated_at(value: Any) -> str:
    if not (isinstance(value, str) and p1a.is_valid_generated_at(value)):
        return "nogen"
    return "".join(ch for ch in value if ch.isdigit())


def _stored_observation_id(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    identity = payload.get("observation_identity")
    if not isinstance(identity, Mapping):
        return None
    observation_id = identity.get("observation_id")
    return observation_id if isinstance(observation_id, str) else None


def _read_source_bytes(source_path: Path) -> bytes:
    try:
        return source_path.read_bytes()
    except OSError:
        raise ArchiveIngestionError("source_unreadable") from None


def _read_json_or_none(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha256_bytes(raw: bytes) -> str:
    import hashlib

    return hashlib.sha256(raw).hexdigest()
