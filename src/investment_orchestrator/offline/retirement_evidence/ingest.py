"""Offline ingestion library for Step 1A retirement observations.

Reads one explicit source observation path, validates it against the full
committed v1 contract, and appends one archive record beneath one explicit
destination archive root.  Append-only *by tool behavior*: it never overwrites
an existing final record (exclusive hard-link create).  Temporary cleanup is
best effort: partial temporary or initialization artifacts may remain when a
cleanup-incomplete or indeterminate failure explicitly reports that possibility.
An exit-code-2 CLI result therefore does not prove that nothing was written.

It authorizes nothing, computes no coverage/sufficiency, and is never imported
by production runtime code.
"""

from __future__ import annotations

import json
import os
import stat
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from investment_orchestrator.research import step1a_retirement_observation as p1a

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence import archive_coordination as coord
from investment_orchestrator.offline.retirement_evidence import archive_record_contract as rc
from investment_orchestrator.offline.retirement_evidence.source_validation import (
    classify_observation,
)


def _safe_record_basename(path: Path) -> str:
    """Return a basename safe to surface in a fail-closed error.

    Candidate discovery may encounter an operator-supplied filename.  Never
    echo an arbitrary basename from such a file through the library or CLI.
    """
    return rc.safe_record_basename(path.name)


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


class IndeterminatePostPublicationError(Exception):
    """An ordinary failure followed a possible or confirmed visible mutation.

    The caller must treat the attempt as indeterminate and inspect explicitly;
    it must not claim that nothing was written or retry automatically.
    """

    def __init__(
        self,
        coordination_token: str,
        *,
        mutation_state: str,
        layout_published: bool,
        record_published: bool,
        cleanup_incomplete: bool,
    ) -> None:
        self.token = "coordination_indeterminate_post_publication"
        self.coordination_token = coordination_token
        self.mutation_state = mutation_state
        self.layout_published = layout_published
        self.record_published = record_published
        self.cleanup_incomplete = cleanup_incomplete
        super().__init__(self.token)


PUBLICATION_NO_VISIBLE_MUTATION = "no_visible_mutation"
PUBLICATION_INITIALIZATION_STARTED = "root_or_directory_initialization_started"
PUBLICATION_PARTIAL_INITIALIZATION = "partial_initialization"
PUBLICATION_LAYOUT_PUBLISHED = "layout_published"
PUBLICATION_RECORD_PUBLISHED = "record_published"
PUBLICATION_CLEANUP_INCOMPLETE = "cleanup_incomplete"
PUBLICATION_OUTCOME_INDETERMINATE = "publication_outcome_indeterminate"


@dataclass
class _PublicationTracker:
    state: str = PUBLICATION_NO_VISIBLE_MUTATION
    layout_published: bool = False
    record_published: bool = False
    cleanup_incomplete: bool = False

    @property
    def published(self) -> bool:
        return self.layout_published or self.record_published

    @property
    def visible_mutation(self) -> bool:
        return self.state != PUBLICATION_NO_VISIBLE_MUTATION

    def mark_initialization_started(self) -> None:
        if self.state == PUBLICATION_NO_VISIBLE_MUTATION:
            self.state = PUBLICATION_INITIALIZATION_STARTED

    def mark_partial_initialization(self) -> None:
        if self.state in {
            PUBLICATION_NO_VISIBLE_MUTATION,
            PUBLICATION_INITIALIZATION_STARTED,
        }:
            self.state = PUBLICATION_PARTIAL_INITIALIZATION

    def mark_layout_published(self) -> None:
        self.layout_published = True
        self.state = PUBLICATION_LAYOUT_PUBLISHED

    def mark_record_published(self) -> None:
        self.record_published = True
        self.state = PUBLICATION_RECORD_PUBLISHED

    def mark_cleanup_incomplete(self) -> None:
        self.cleanup_incomplete = True
        if self.state != PUBLICATION_OUTCOME_INDETERMINATE:
            self.state = PUBLICATION_CLEANUP_INCOMPLETE

    def mark_indeterminate(self) -> None:
        self.state = PUBLICATION_OUTCOME_INDETERMINATE

    def indeterminate_error(self, coordination_token: str) -> IndeterminatePostPublicationError:
        return IndeterminatePostPublicationError(
            coordination_token,
            mutation_state=self.state,
            layout_published=self.layout_published,
            record_published=self.record_published,
            cleanup_incomplete=self.cleanup_incomplete,
        )


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
    coordination_path: Path | str | None = None,
    claimed_provenance: str = c.DEFAULT_PROVENANCE,
    provenance_claim_source: str = c.PROVENANCE_CLAIM_SOURCE_DEFAULT,
    tool_identity: Mapping[str, str] | None = None,
    archived_at: str | None = None,
    _operation_runner: Any = None,
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

    tracker = _PublicationTracker()
    with coord.acquire_coordination_lease(
        coordination_path,
        archive_root=dest_root,
        mode=coord.LOCK_MODE_EXCLUSIVE,
    ) as lease:
        try:
            coord.begin_coordination_operation(
                lease,
                archive_root=dest_root,
                expected_mode=coord.LOCK_MODE_EXCLUSIVE,
            )
            try:
                lexical_dest = Path(
                    os.path.abspath(os.path.normpath(os.fspath(dest_root)))
                )
                lexical_source = Path(
                    os.path.abspath(os.path.normpath(os.fspath(source_path)))
                )
            except (OSError, TypeError, ValueError):
                raise ArchiveIngestionError("source_unreadable") from None
            if lexical_source == lexical_dest or lexical_source.is_relative_to(
                lexical_dest
            ):
                raise ArchiveIngestionError("source_inside_archive_root")
            resolved_dest = dest_root.resolve(strict=False)
            try:
                resolved_source = source_path.resolve(strict=True)
            except (OSError, RuntimeError):
                raise ArchiveIngestionError("source_unreadable") from None
            if resolved_source == resolved_dest or resolved_source.is_relative_to(
                resolved_dest
            ):
                raise ArchiveIngestionError("source_inside_archive_root")
            raw = _read_source_bytes(source_path)
            source_file_sha256 = _sha256_bytes(raw)
            if _operation_runner is None:
                raise ArchiveIngestionError("archive_operation_authority_missing")
            result = _operation_runner(
                raw=raw,
                source_path=source_path,
                dest_root=dest_root,
                source_file_sha256=source_file_sha256,
                claimed_provenance=claimed_provenance,
                provenance_claim_source=provenance_claim_source,
                identity=identity,
                stamp=stamp,
                lease=lease,
                tracker=tracker,
            )
            coord.complete_coordination_operation(
                lease,
                archive_root=dest_root,
                expected_mode=coord.LOCK_MODE_EXCLUSIVE,
            )
            return result
        except coord.CoordinationError as exc:
            if tracker.published or tracker.visible_mutation:
                raise tracker.indeterminate_error(exc.token) from None
            raise
        except Exception as exc:
            if tracker.published or tracker.visible_mutation:
                raise tracker.indeterminate_error(
                    "archive_visible_mutation_indeterminate"
                ) from None
            try:
                coord.validate_coordination_operation(
                    lease,
                    archive_root=dest_root,
                    expected_mode=coord.LOCK_MODE_EXCLUSIVE,
                )
            except coord.CoordinationError as validation_exc:
                raise
            if isinstance(
                exc,
                (
                    ArchiveIngestionError,
                    ArchiveLayoutError,
                    ExistingRecordIntegrityError,
                    IndeterminatePostPublicationError,
                ),
            ):
                raise
            raise ArchiveIngestionError("archive_operation_failed") from None


def _ingest_under_exclusive_lease(
    *,
    raw: bytes,
    source_path: Path,
    dest_root: Path,
    source_file_sha256: str,
    claimed_provenance: str,
    provenance_claim_source: str,
    identity: Mapping[str, str],
    stamp: str,
    lease: coord.VerifiedCoordinationLease,
    tracker: _PublicationTracker,
    mutation_operation: object,
) -> IngestResult:
    """Perform every archive observation and mutation under one live lease."""
    _validate_archive_mutation_operation(
        mutation_operation, lease=lease, archive_root=dest_root
    )
    _ensure_layout(
        dest_root,
        lease=lease,
        tracker=tracker,
        mutation_operation=mutation_operation,
    )

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
            lease=lease,
            tracker=tracker,
            mutation_operation=mutation_operation,
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
            lease=lease,
            tracker=tracker,
            mutation_operation=mutation_operation,
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
        lease=lease,
        tracker=tracker,
        mutation_operation=mutation_operation,
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
    lease: coord.VerifiedCoordinationLease,
    tracker: _PublicationTracker,
    mutation_operation: object,
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
            lease=lease,
            tracker=tracker,
            mutation_operation=mutation_operation,
        )

    observation_id = rc.stored_observation_id(payload)

    # Cross-record conflict: same observation id, different canonical content.
    conflict_path = _find_observation_id_conflict(
        dest_root, observation_id, canonical_payload_sha256, lease=lease
    )
    if conflict_path is not None:
        return _write_rejected(
            dest_root=dest_root,
            source_path=source_path,
            source_file_sha256=source_file_sha256,
            reason_tokens=(c.REASON_OBSERVATION_ID_CONTENT_CONFLICT,),
            identity=identity,
            stamp=stamp,
            lease=lease,
            tracker=tracker,
            mutation_operation=mutation_operation,
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

    filename = rc.expected_observation_record_filename(
        payload, observation_id, canonical_payload_sha256
    )
    target = dest_root / decision / filename
    written = _publish_canonical_record(
        envelope,
        archive_root=dest_root,
        lease=lease,
        tracker=tracker,
        mutation_operation=mutation_operation,
    )
    duplicate = not written
    if duplicate:
        # Same content-addressed filename already present: fully re-verify the
        # existing record (F2), then confirm it is a true duplicate (same
        # canonical payload), else flag a collision (no overwrite).
        if not _existing_is_same_payload(
            target, canonical_payload_sha256, decision, lease=lease
        ):
            return _write_rejected(
                dest_root=dest_root,
                source_path=source_path,
                source_file_sha256=source_file_sha256,
                reason_tokens=(c.REASON_ARCHIVE_FILENAME_COLLISION,),
                identity=identity,
                stamp=stamp,
                lease=lease,
                tracker=tracker,
                mutation_operation=mutation_operation,
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
    digest = rc.compute_archive_record_content_sha256(envelope)
    if digest is None:  # pragma: no cover - envelope is always serializable
        raise ArchiveIngestionError("archive_record_not_serializable")
    return digest


def _safe_source_metadata(payload: Mapping[str, Any], source_path: Path) -> dict[str, Any]:
    coverage = payload.get("coverage_identity")
    return {
        "source_basename": source_path.name,
        "source_observation_id": rc.stored_observation_id(payload),
        "source_coverage_key": coverage.get("coverage_key")
        if isinstance(coverage, Mapping)
        else None,
        "source_git_commit": rc.clean_payload_git_commit(payload),
        "source_schema_version": payload.get("schema_version"),
    }


# --- rejected ----------------------------------------------------------------
def _write_rejected(
    *,
    dest_root: Path,
    source_path: Path,
    source_file_sha256: str,
    reason_tokens: tuple[str, ...],
    identity: Mapping[str, str],
    stamp: str,
    lease: coord.VerifiedCoordinationLease,
    tracker: _PublicationTracker,
    mutation_operation: object,
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
    filename = rc.expected_rejected_record_filename(source_file_sha256)
    target = dest_root / c.DECISION_REJECTED / filename
    written = _publish_canonical_record(
        record,
        archive_root=dest_root,
        lease=lease,
        tracker=tracker,
        mutation_operation=mutation_operation,
    )
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
def _ensure_layout(
    dest_root: Path,
    *,
    lease: coord.VerifiedCoordinationLease,
    tracker: _PublicationTracker,
    mutation_operation: object,
) -> None:
    _validate_archive_mutation_operation(
        mutation_operation, lease=lease, archive_root=dest_root
    )
    _ensure_archive_root_directory(
        dest_root,
        lease=lease,
        tracker=tracker,
        mutation_operation=mutation_operation,
    )
    version_path = dest_root / c.ARCHIVE_LAYOUT_VERSION_FILENAME
    try:
        layout_stat = os.lstat(version_path)
    except FileNotFoundError:
        layout_stat = None
    except OSError:
        raise ArchiveLayoutError("archive_layout_version_unreadable") from None
    if layout_stat is not None and not stat.S_ISREG(layout_stat.st_mode):
        raise ArchiveLayoutError("archive_layout_entry_unsafe")
    if layout_stat is None:
        _publish_canonical_layout(
            archive_root=dest_root,
            lease=lease,
            tracker=tracker,
            mutation_operation=mutation_operation,
        )
        # A false result is permitted only for a target proven to have existed
        # before this operation's link attempt.  Either way, trust no content
        # until the exact no-follow descriptor/path validation below succeeds.
        _validate_layout_version(version_path, lease=lease)
    else:
        _validate_layout_version(version_path, lease=lease)
    for partition in c.PARTITIONS:
        _ensure_partition_directory(
            dest_root,
            partition,
            lease=lease,
            tracker=tracker,
            mutation_operation=mutation_operation,
        )


def _ensure_archive_root_directory(
    dest_root: Path,
    *,
    lease: coord.VerifiedCoordinationLease,
    tracker: _PublicationTracker,
    mutation_operation: object,
) -> None:
    _validate_archive_mutation_operation(
        mutation_operation, lease=lease, archive_root=dest_root
    )
    try:
        root_stat = os.lstat(dest_root)
    except FileNotFoundError:
        tracker.mark_initialization_started()
        dest_root.mkdir(parents=True, exist_ok=False)
        tracker.mark_partial_initialization()
    except OSError:
        raise ArchiveLayoutError("archive_root_unreadable") from None
    else:
        if stat.S_ISLNK(root_stat.st_mode):
            try:
                resolved = dest_root.resolve(strict=True)
                resolved_stat = os.lstat(resolved)
            except (OSError, RuntimeError):
                raise ArchiveLayoutError("archive_root_unsafe") from None
            if not stat.S_ISDIR(resolved_stat.st_mode):
                raise ArchiveLayoutError("archive_root_unsafe")
        elif not stat.S_ISDIR(root_stat.st_mode):
            raise ArchiveLayoutError("archive_root_unsafe")
    _validate_archive_mutation_operation(
        mutation_operation, lease=lease, archive_root=dest_root
    )


def _ensure_partition_directory(
    dest_root: Path,
    partition: str,
    *,
    lease: coord.VerifiedCoordinationLease,
    tracker: _PublicationTracker,
    mutation_operation: object,
) -> None:
    if partition not in c.PARTITIONS:
        raise ArchiveLayoutError("archive_partition_unsafe")
    _validate_archive_mutation_operation(
        mutation_operation, lease=lease, archive_root=dest_root
    )
    path = dest_root / partition
    try:
        partition_stat = os.lstat(path)
    except FileNotFoundError:
        tracker.mark_initialization_started()
        os.mkdir(path)
        tracker.mark_partial_initialization()
        try:
            partition_stat = os.lstat(path)
        except OSError:
            raise ArchiveLayoutError("archive_partition_unreadable") from None
    except OSError:
        raise ArchiveLayoutError("archive_partition_unreadable") from None
    if stat.S_ISLNK(partition_stat.st_mode) or not stat.S_ISDIR(
        partition_stat.st_mode
    ):
        raise ArchiveLayoutError("archive_partition_unsafe")
    try:
        resolved_root = dest_root.resolve(strict=True)
        resolved_partition = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ArchiveLayoutError("archive_partition_unreadable") from None
    if resolved_partition.parent != resolved_root:
        raise ArchiveLayoutError("archive_partition_unsafe")
    _validate_archive_mutation_operation(
        mutation_operation, lease=lease, archive_root=dest_root
    )


def _validate_layout_version(
    version_path: Path, *, lease: coord.VerifiedCoordinationLease
) -> None:
    coord.validate_coordination_operation(
        lease,
        archive_root=version_path.parent,
        expected_mode=coord.LOCK_MODE_EXCLUSIVE,
    )
    try:
        initial_stat = os.lstat(version_path)
    except OSError:
        raise ArchiveLayoutError("archive_layout_version_unreadable") from None
    if not stat.S_ISREG(initial_stat.st_mode):
        raise ArchiveLayoutError("archive_layout_entry_unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(version_path, flags)
    except OSError:
        raise ArchiveLayoutError("archive_layout_version_unreadable") from None
    try:
        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode) or _stat_identity_signature(
            opened_stat
        ) != _stat_identity_signature(initial_stat):
            raise ArchiveLayoutError("archive_layout_entry_unsafe")
        if opened_stat.st_size > 4096:
            raise ArchiveLayoutError("archive_layout_version_incompatible")
        data = os.read(fd, 4097)
        final_descriptor_stat = os.fstat(fd)
        final_path_stat = os.lstat(version_path)
        if (
            _stat_identity_signature(final_descriptor_stat)
            != _stat_identity_signature(opened_stat)
            or _stat_identity_signature(final_path_stat)
            != _stat_identity_signature(opened_stat)
        ):
            raise ArchiveLayoutError("archive_layout_entry_unsafe")
        if len(data) != opened_stat.st_size:
            raise ArchiveLayoutError("archive_layout_entry_unsafe")
    except ArchiveLayoutError:
        raise
    except OSError:
        raise ArchiveLayoutError("archive_layout_version_unreadable") from None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        content = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ArchiveLayoutError("archive_layout_version_incompatible") from None
    if content != c.ARCHIVE_LAYOUT_VERSION:
        raise ArchiveLayoutError("archive_layout_version_incompatible")
    coord.validate_coordination_operation(
        lease,
        archive_root=version_path.parent,
        expected_mode=coord.LOCK_MODE_EXCLUSIVE,
    )


def _stat_identity_signature(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


# --- existing-record verification (F2) ----------------------------------------
# Before any existing archive record may influence a duplicate / conflict /
# collision decision, it is independently re-verified end to end.  Stored
# metadata (observation id, canonical hash, self-hash) is never trusted alone;
# filename prefixes are candidate discovery only, never identity.

def _verify_existing_record(
    path: Path,
    expected_partition: str,
    *,
    lease: coord.VerifiedCoordinationLease,
) -> Mapping[str, Any]:
    """Fully re-verify an existing archive record before trusting it.

    Raises :class:`ExistingRecordIntegrityError` (fail-closed for the current
    ingestion attempt) on any envelope, integrity, payload-identity,
    classification, or envelope-to-payload consistency failure.  The exception
    carries only the code-owned token and the safe record basename.
    """
    record = _read_json_or_none(path, lease=lease)
    if not _existing_record_is_valid(record, expected_partition):
        raise ExistingRecordIntegrityError(_safe_record_basename(path))
    return record


def _existing_record_is_valid(record: Any, expected_partition: str) -> bool:
    # Envelope shape and versions.
    if not isinstance(record, Mapping) or frozenset(record.keys()) != rc.OBSERVATION_RECORD_ENVELOPE_KEYS:
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
    if not isinstance(metadata, Mapping) or frozenset(metadata.keys()) != rc.SOURCE_METADATA_KEYS:
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
    if not isinstance(recomputed, Mapping) or frozenset(recomputed.keys()) != rc.RECOMPUTED_IDENTITY_KEYS:
        return False
    for key in rc.RECOMPUTED_IDENTITY_KEYS:
        if recomputed[key] != result.recomputed_identity.get(key):
            return False
    # Envelope-to-payload consistency.
    if metadata["source_observation_id"] != rc.stored_observation_id(payload):
        return False
    coverage = payload.get("coverage_identity")
    payload_coverage_key = coverage.get("coverage_key") if isinstance(coverage, Mapping) else None
    if metadata["source_coverage_key"] != payload_coverage_key:
        return False
    if metadata["source_schema_version"] != payload.get("schema_version"):
        return False
    if metadata["source_git_commit"] != rc.clean_payload_git_commit(payload):
        return False
    return True


# --- duplicate / conflict scanning -------------------------------------------
def _find_observation_id_conflict(
    dest_root: Path,
    observation_id: str | None,
    canonical_payload_sha256: str,
    *,
    lease: coord.VerifiedCoordinationLease,
) -> Path | None:
    """Return an existing record path iff same observation id, different content.

    Bounded, deterministic destination-candidate scan (globbed by the
    observation-id prefix); it builds no cumulative index.  Every candidate is
    fully re-verified via :func:`_verify_existing_record` before any of its
    identity fields are trusted; a candidate failing verification fails the
    current ingestion closed.  Final decisions compare complete observation ids
    and full canonical hashes - never filename prefixes.
    """
    coord.validate_coordination_operation(
        lease, archive_root=dest_root, expected_mode=coord.LOCK_MODE_EXCLUSIVE
    )
    if observation_id is None:
        return None
    prefix = observation_id[:12]
    for partition in (c.PARTITION_ACCEPTED, c.PARTITION_QUARANTINED):
        directory = dest_root / partition
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.glob(f"*__{prefix}__*.json")):
            record = _verify_existing_record(candidate, partition, lease=lease)
            existing_id = rc.stored_observation_id(record["observation_payload"])
            if existing_id != observation_id:
                continue
            if record["source_canonical_payload_sha256"] != canonical_payload_sha256:
                return candidate
    return None


def _existing_is_same_payload(
    target: Path,
    canonical_payload_sha256: str,
    expected_partition: str,
    *,
    lease: coord.VerifiedCoordinationLease,
) -> bool:
    """True iff the fully re-verified existing record holds the same payload.

    Raises :class:`ExistingRecordIntegrityError` if the existing record fails
    verification - a corrupted/forged record must never yield a silent
    duplicate no-op that swallows a genuine source observation.
    """
    coord.validate_coordination_operation(
        lease,
        archive_root=target.parent.parent,
        expected_mode=coord.LOCK_MODE_EXCLUSIVE,
    )
    record = _verify_existing_record(target, expected_partition, lease=lease)
    return record["source_canonical_payload_sha256"] == canonical_payload_sha256


# --- append-only, no-overwrite atomic write ----------------------------------
def _lexical_absolute(path: Path) -> Path:
    try:
        return Path(os.path.abspath(os.path.normpath(os.fspath(path))))
    except (OSError, TypeError, ValueError):
        raise ArchiveIngestionError("archive_target_invalid") from None


def _expected_record_destination(data: Mapping[str, Any]) -> tuple[str, str]:
    schema = data.get("archive_record_schema_version")
    if schema == c.ARCHIVE_RECORD_SCHEMA_VERSION:
        partition = data.get("ingestion_decision")
        payload = data.get("observation_payload")
        payload_sha = data.get("source_canonical_payload_sha256")
        if (
            partition not in (c.PARTITION_ACCEPTED, c.PARTITION_QUARANTINED)
            or not isinstance(payload, Mapping)
            or not isinstance(payload_sha, str)
        ):
            raise ArchiveIngestionError("archive_record_target_invalid")
        return (
            partition,
            rc.expected_observation_record_filename(
                payload,
                rc.stored_observation_id(payload),
                payload_sha,
            ),
        )
    if schema == c.ARCHIVE_REJECTED_RECORD_SCHEMA_VERSION:
        source_sha = data.get("source_file_sha256")
        if not isinstance(source_sha, str):
            raise ArchiveIngestionError("archive_record_target_invalid")
        return (
            c.PARTITION_REJECTED,
            rc.expected_rejected_record_filename(source_sha),
        )
    raise ArchiveIngestionError("archive_record_target_invalid")


def _validate_publication_target(
    target: Path,
    *,
    archive_root: Path,
    publication_kind: str,
    expected_partition: str | None,
    expected_filename: str,
    lease: coord.VerifiedCoordinationLease,
    mutation_operation: object,
) -> None:
    _validate_archive_mutation_operation(
        mutation_operation, lease=lease, archive_root=archive_root
    )
    try:
        resolved_root = archive_root.resolve(strict=True)
        root_stat = os.lstat(resolved_root)
    except (OSError, RuntimeError):
        raise ArchiveLayoutError("archive_root_unreadable") from None
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ArchiveLayoutError("archive_root_unsafe")

    if publication_kind == "layout":
        if (
            expected_partition is not None
            or expected_filename != c.ARCHIVE_LAYOUT_VERSION_FILENAME
            or target.name != expected_filename
        ):
            raise ArchiveLayoutError("archive_layout_entry_unsafe")
        expected_target = archive_root / c.ARCHIVE_LAYOUT_VERSION_FILENAME
        if _lexical_absolute(target) != _lexical_absolute(expected_target):
            raise ArchiveLayoutError("archive_layout_entry_unsafe")
        try:
            resolved_parent = target.parent.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ArchiveLayoutError("archive_root_unreadable") from None
        if resolved_parent != resolved_root:
            raise ArchiveLayoutError("archive_layout_entry_unsafe")
    elif publication_kind == "record":
        if expected_partition not in c.PARTITIONS:
            raise ArchiveIngestionError("archive_record_target_invalid")
        if target.name != expected_filename:
            raise ArchiveIngestionError("archive_record_target_invalid")
        expected_parent = archive_root / expected_partition
        if _lexical_absolute(target.parent) != _lexical_absolute(expected_parent):
            raise ArchiveIngestionError("archive_record_target_invalid")
        try:
            partition_stat = os.lstat(expected_parent)
            resolved_parent = expected_parent.resolve(strict=True)
        except OSError:
            raise ArchiveLayoutError("archive_partition_unreadable") from None
        if stat.S_ISLNK(partition_stat.st_mode) or not stat.S_ISDIR(
            partition_stat.st_mode
        ):
            raise ArchiveLayoutError("archive_partition_unsafe")
        if resolved_parent.parent != resolved_root or resolved_parent.name != expected_partition:
            raise ArchiveLayoutError("archive_partition_unsafe")
    else:
        raise ArchiveIngestionError("archive_target_invalid")

    try:
        target_stat = os.lstat(target)
    except FileNotFoundError:
        return
    except OSError:
        raise ArchiveIngestionError("archive_target_unreadable") from None
    if not stat.S_ISREG(target_stat.st_mode):
        token = (
            "archive_layout_entry_unsafe"
            if publication_kind == "layout"
            else "archive_record_target_unsafe"
        )
        if publication_kind == "layout":
            raise ArchiveLayoutError(token)
        raise ArchiveIngestionError(token)


def _make_archive_mutation_api(run_ingestion: Any) -> tuple[Any, ...]:
    """Own ingestion-operation, publication, and temporary-file authority."""

    class MutationOperation:
        __slots__ = ("__token", "__weakref__")

        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            raise TypeError("archive mutation operations cannot be constructed")

        def __repr__(self) -> str:
            return "<ArchiveMutationOperation>"

    class PublicationDescriptor:
        __slots__ = ("__token", "__weakref__")

        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            raise TypeError("publication descriptors cannot be constructed")

        def __repr__(self) -> str:
            return "<ArchivePublicationDescriptor>"

    @dataclass(frozen=True, slots=True)
    class OperationRecord:
        lease: weakref.ReferenceType[coord.VerifiedCoordinationLease]
        archive_root: Path
        pid: int
        operation_binding: object
        token: object
        state: str

    @dataclass(frozen=True, slots=True)
    class DescriptorRecord:
        operation: weakref.ReferenceType[MutationOperation]
        operation_binding: object
        archive_root: Path
        publication_kind: str
        partition: str | None
        filename: str
        serialized_bytes: bytes
        serialized_sha256: str
        token: object
        state: str

    @dataclass(frozen=True, slots=True)
    class TempAuthority:
        operation: weakref.ReferenceType[MutationOperation]
        operation_binding: object
        creator_pid: int
        archive_root: Path
        partition: str | None
        path: Path
        identity: tuple[int, int]
        state: str

    operations: weakref.WeakKeyDictionary[MutationOperation, OperationRecord] = (
        weakref.WeakKeyDictionary()
    )
    descriptors: weakref.WeakKeyDictionary[
        PublicationDescriptor, DescriptorRecord
    ] = weakref.WeakKeyDictionary()
    temp_authorities: dict[tuple[int, Path], TempAuthority] = {}

    def operation_record(
        value: object,
        *,
        lease: coord.VerifiedCoordinationLease,
        archive_root: Path,
    ) -> OperationRecord:
        if type(value) is not MutationOperation:
            raise coord.CoordinationError(coord.TOKEN_LEASE_INVALID)
        try:
            record = operations.get(value)  # type: ignore[arg-type]
            token = object.__getattribute__(value, "_MutationOperation__token")
        except (AttributeError, TypeError):
            record = None
            token = None
        if (
            record is None
            or token is not record.token
            or record.state != "active"
            or record.pid != os.getpid()
            or record.lease() is not lease
            or _lexical_absolute(record.archive_root)
            != _lexical_absolute(archive_root)
        ):
            raise coord.CoordinationError(coord.TOKEN_LEASE_INVALID)
        actual_binding = coord._coordination_operation_identity(
            lease,
            archive_root=archive_root,
            expected_mode=coord.LOCK_MODE_EXCLUSIVE,
        )
        if actual_binding is not record.operation_binding:
            raise coord.CoordinationError(coord.TOKEN_LEASE_INVALID)
        return record

    def validate_operation(
        value: object,
        *,
        lease: coord.VerifiedCoordinationLease,
        archive_root: Path,
    ) -> None:
        operation_record(value, lease=lease, archive_root=archive_root)

    def make_descriptor(
        operation: object,
        *,
        lease: coord.VerifiedCoordinationLease,
        archive_root: Path,
        publication_kind: str,
        partition: str | None,
        filename: str,
        serialized_bytes: bytes,
    ) -> PublicationDescriptor:
        op_record = operation_record(
            operation, lease=lease, archive_root=archive_root
        )
        descriptor = object.__new__(PublicationDescriptor)
        token = object()
        object.__setattr__(descriptor, "_PublicationDescriptor__token", token)
        descriptors[descriptor] = DescriptorRecord(
            operation=weakref.ref(operation),  # type: ignore[arg-type]
            operation_binding=op_record.operation_binding,
            archive_root=op_record.archive_root,
            publication_kind=publication_kind,
            partition=partition,
            filename=filename,
            serialized_bytes=serialized_bytes,
            serialized_sha256=_sha256_bytes(serialized_bytes),
            token=token,
            state="active",
        )
        return descriptor

    def consume_descriptor(
        descriptor: object,
        *,
        operation: object,
        lease: coord.VerifiedCoordinationLease,
        archive_root: Path,
    ) -> DescriptorRecord:
        op_record = operation_record(
            operation, lease=lease, archive_root=archive_root
        )
        if type(descriptor) is not PublicationDescriptor:
            raise ArchiveIngestionError("archive_publication_descriptor_invalid")
        try:
            record = descriptors.get(descriptor)  # type: ignore[arg-type]
            token = object.__getattribute__(
                descriptor, "_PublicationDescriptor__token"
            )
        except (AttributeError, TypeError):
            record = None
            token = None
        if (
            record is None
            or token is not record.token
            or record.state != "active"
            or record.operation() is not operation
            or record.operation_binding is not op_record.operation_binding
            or record.archive_root != op_record.archive_root
            or _sha256_bytes(record.serialized_bytes) != record.serialized_sha256
        ):
            raise ArchiveIngestionError("archive_publication_descriptor_invalid")
        consumed = replace(record, state="consumed")
        descriptors[descriptor] = consumed
        return consumed

    def register_temp(
        operation: object,
        descriptor: DescriptorRecord,
        path: Path,
        st: os.stat_result,
    ) -> None:
        key = (id(operation), path)
        if key in temp_authorities:
            raise ArchiveIngestionError("archive_temporary_authority_invalid")
        temp_authorities[key] = TempAuthority(
            operation=weakref.ref(operation),  # type: ignore[arg-type]
            operation_binding=descriptor.operation_binding,
            creator_pid=os.getpid(),
            archive_root=descriptor.archive_root,
            partition=descriptor.partition,
            path=path,
            identity=(st.st_dev, st.st_ino),
            state="active",
        )

    def cleanup_temp(
        operation: object,
        path: Path,
        *,
        descriptor: DescriptorRecord,
        target: Path,
        lease: coord.VerifiedCoordinationLease,
    ) -> bool:
        try:
            op_record = operation_record(
                operation, lease=lease, archive_root=descriptor.archive_root
            )
        except Exception:
            return False
        key = (id(operation), path)
        authority = temp_authorities.get(key)
        if (
            authority is None
            or authority.state != "active"
            or authority.operation() is not operation
            or authority.operation_binding is not op_record.operation_binding
            or authority.creator_pid != os.getpid()
            or authority.archive_root != op_record.archive_root
            or authority.partition != descriptor.partition
            or authority.path != path
        ):
            return False
        temp_authorities[key] = replace(authority, state="cleanup_started")
        try:
            _validate_publication_target(
                target,
                archive_root=descriptor.archive_root,
                publication_kind=descriptor.publication_kind,
                expected_partition=descriptor.partition,
                expected_filename=descriptor.filename,
                lease=lease,
                mutation_operation=operation,
            )
            if path.parent != target.parent:
                return False
            if not path.name.startswith(f".{target.name}.tmp."):
                return False
            try:
                resolved_archive = descriptor.archive_root.resolve(strict=False)
                resolved_parent = path.parent.resolve(strict=True)
            except (OSError, RuntimeError):
                return False
            expected_parent = (
                resolved_archive
                if descriptor.partition is None
                else resolved_archive / descriptor.partition
            )
            if resolved_parent != expected_parent:
                return False
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                return True
            except (OSError, ValueError):
                return False
            if not stat.S_ISREG(st.st_mode):
                return False
            if (st.st_dev, st.st_ino) != authority.identity:
                return False
            try:
                path.unlink()
            except FileNotFoundError:
                return True
            except (OSError, ValueError):
                return False
            return True
        finally:
            current = temp_authorities.get(key)
            if current is not None:
                temp_authorities[key] = replace(current, state="consumed")

    def publish_descriptor(
        descriptor: object,
        *,
        operation: object,
        lease: coord.VerifiedCoordinationLease,
        tracker: _PublicationTracker,
    ) -> bool:
        record = consume_descriptor(
            descriptor,
            operation=operation,
            lease=lease,
            archive_root=operation_record(
                operation,
                lease=lease,
                archive_root=descriptors[descriptor].archive_root,  # type: ignore[index]
            ).archive_root,
        )
        target = (
            record.archive_root / record.filename
            if record.partition is None
            else record.archive_root / record.partition / record.filename
        )
        _validate_publication_target(
            target,
            archive_root=record.archive_root,
            publication_kind=record.publication_kind,
            expected_partition=record.partition,
            expected_filename=record.filename,
            lease=lease,
            mutation_operation=operation,
        )
        tmp = target.with_name(
            f".{target.name}.tmp.{os.getpid()}.{os.urandom(6).hex()}"
        )
        primary_error: BaseException | None = None
        temp_created = False
        temp_fd: int | None = None
        try:
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            fd = os.open(tmp, flags, 0o600)
            temp_fd = fd
            temp_created = True
            temp_stat = os.fstat(fd)
            register_temp(operation, record, tmp, temp_stat)
            with os.fdopen(fd, "wb") as handle:
                handle.write(record.serialized_bytes)
                handle.flush()
                _validate_publication_target(
                    target,
                    archive_root=record.archive_root,
                    publication_kind=record.publication_kind,
                    expected_partition=record.partition,
                    expected_filename=record.filename,
                    lease=lease,
                    mutation_operation=operation,
                )
                os.fsync(handle.fileno())
            temp_fd = None
        except BaseException as exc:
            primary_error = exc
            if temp_fd is not None:
                try:
                    os.close(temp_fd)
                except OSError:
                    pass
            if temp_created and not cleanup_temp(
                operation,
                tmp,
                descriptor=record,
                target=target,
                lease=lease,
            ):
                tracker.mark_cleanup_incomplete()
            raise
        try:
            _validate_publication_target(
                target,
                archive_root=record.archive_root,
                publication_kind=record.publication_kind,
                expected_partition=record.partition,
                expected_filename=record.filename,
                lease=lease,
                mutation_operation=operation,
            )
            target_identity_before_link = _path_identity_no_follow(target)
            os.link(tmp, target)
            if record.publication_kind == "layout":
                tracker.mark_layout_published()
            else:
                tracker.mark_record_published()
        except FileExistsError:
            target_identity_after_link = _path_identity_no_follow(target)
            if (
                target_identity_before_link is None
                or target_identity_after_link != target_identity_before_link
            ):
                tracker.mark_indeterminate()
                primary_error = RuntimeError("publication_outcome_indeterminate")
                raise primary_error from None
            _validate_archive_mutation_operation(
                operation, lease=lease, archive_root=record.archive_root
            )
            return False
        except BaseException as exc:
            primary_error = exc
            try:
                if target.exists() and os.path.samefile(tmp, target):
                    if record.publication_kind == "layout":
                        tracker.mark_layout_published()
                    else:
                        tracker.mark_record_published()
                else:
                    tracker.mark_indeterminate()
            except OSError:
                tracker.mark_indeterminate()
            raise
        finally:
            if not cleanup_temp(
                operation,
                tmp,
                descriptor=record,
                target=target,
                lease=lease,
            ):
                tracker.mark_cleanup_incomplete()
                if primary_error is None:
                    _validate_archive_mutation_operation(
                        operation, lease=lease, archive_root=record.archive_root
                    )
                    raise RuntimeError("temporary_cleanup_failed")
        _validate_archive_mutation_operation(
            operation, lease=lease, archive_root=record.archive_root
        )
        return True

    def publish_layout(
        *,
        archive_root: Path,
        lease: coord.VerifiedCoordinationLease,
        tracker: _PublicationTracker,
        mutation_operation: object,
    ) -> bool:
        serialized = (c.ARCHIVE_LAYOUT_VERSION + "\n").encode("utf-8")
        descriptor = make_descriptor(
            mutation_operation,
            lease=lease,
            archive_root=archive_root,
            publication_kind="layout",
            partition=None,
            filename=c.ARCHIVE_LAYOUT_VERSION_FILENAME,
            serialized_bytes=serialized,
        )
        return publish_descriptor(
            descriptor,
            operation=mutation_operation,
            lease=lease,
            tracker=tracker,
        )

    def publish_record(
        data: Mapping[str, Any],
        *,
        archive_root: Path,
        lease: coord.VerifiedCoordinationLease,
        tracker: _PublicationTracker,
        mutation_operation: object,
    ) -> bool:
        operation_record(
            mutation_operation, lease=lease, archive_root=archive_root
        )
        partition, filename = _expected_record_destination(data)
        if data.get("archive_record_schema_version") == c.ARCHIVE_RECORD_SCHEMA_VERSION:
            if data.get("archive_record_content_sha256") != _record_content_hash(data):
                raise ArchiveIngestionError("archive_record_identity_invalid")
        serialized = (
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        descriptor = make_descriptor(
            mutation_operation,
            lease=lease,
            archive_root=archive_root,
            publication_kind="record",
            partition=partition,
            filename=filename,
            serialized_bytes=serialized,
        )
        return publish_descriptor(
            descriptor,
            operation=mutation_operation,
            lease=lease,
            tracker=tracker,
        )

    def run_ingestion_operation(
        *,
        dest_root: Path,
        lease: coord.VerifiedCoordinationLease,
        **kwargs: Any,
    ) -> IngestResult:
        coord.validate_coordination_operation(
            lease,
            archive_root=dest_root,
            expected_mode=coord.LOCK_MODE_EXCLUSIVE,
        )
        operation_binding = coord._coordination_operation_identity(
            lease,
            archive_root=dest_root,
            expected_mode=coord.LOCK_MODE_EXCLUSIVE,
        )
        operation = object.__new__(MutationOperation)
        token = object()
        object.__setattr__(operation, "_MutationOperation__token", token)
        operations[operation] = OperationRecord(
            lease=weakref.ref(lease),
            archive_root=Path(dest_root),
            pid=os.getpid(),
            operation_binding=operation_binding,
            token=token,
            state="active",
        )
        try:
            return run_ingestion(
                dest_root=dest_root,
                lease=lease,
                mutation_operation=operation,
                **kwargs,
            )
        finally:
            record = operations.get(operation)
            if record is not None:
                operations[operation] = replace(record, state="complete")
                operations.pop(operation, None)
            for key, authority in tuple(temp_authorities.items()):
                if authority.operation() is operation:
                    temp_authorities.pop(key, None)

    return (
        run_ingestion_operation,
        validate_operation,
        publish_layout,
        publish_record,
    )


def _path_identity_no_follow(path: Path) -> tuple[int, int, int, int, int, int] | None:
    try:
        value = os.lstat(path)
        return _stat_identity_signature(value)
    except FileNotFoundError:
        return None
    except OSError:
        raise ArchiveIngestionError("archive_target_unreadable") from None


(
    _run_ingestion_under_exclusive_lease,
    _validate_archive_mutation_operation,
    _publish_canonical_layout,
    _publish_canonical_record,
) = _make_archive_mutation_api(_ingest_under_exclusive_lease)
del _make_archive_mutation_api


def _bind_ingestion_entrypoint(implementation: Any, operation_runner: Any) -> Any:
    """Bind the operation issuer into the public canonical ingestion entrypoint."""

    def bound_ingest_observation(
        *,
        source_path: Path,
        dest_root: Path,
        coordination_path: Path | str | None = None,
        claimed_provenance: str = c.DEFAULT_PROVENANCE,
        provenance_claim_source: str = c.PROVENANCE_CLAIM_SOURCE_DEFAULT,
        tool_identity: Mapping[str, str] | None = None,
        archived_at: str | None = None,
    ) -> IngestResult:
        return implementation(
            source_path=source_path,
            dest_root=dest_root,
            coordination_path=coordination_path,
            claimed_provenance=claimed_provenance,
            provenance_claim_source=provenance_claim_source,
            tool_identity=tool_identity,
            archived_at=archived_at,
            _operation_runner=operation_runner,
        )

    bound_ingest_observation.__name__ = "ingest_observation"
    bound_ingest_observation.__qualname__ = "ingest_observation"
    bound_ingest_observation.__doc__ = implementation.__doc__
    return bound_ingest_observation


ingest_observation = _bind_ingestion_entrypoint(
    ingest_observation, _run_ingestion_under_exclusive_lease
)
del _bind_ingestion_entrypoint
del _run_ingestion_under_exclusive_lease


def _read_source_bytes(source_path: Path) -> bytes:
    try:
        return source_path.read_bytes()
    except OSError:
        raise ArchiveIngestionError("source_unreadable") from None


def _read_json_or_none(
    path: Path, *, lease: coord.VerifiedCoordinationLease
) -> Any:
    coord.validate_coordination_operation(
        lease,
        archive_root=path.parent.parent,
        expected_mode=coord.LOCK_MODE_EXCLUSIVE,
    )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha256_bytes(raw: bytes) -> str:
    import hashlib

    return hashlib.sha256(raw).hexdigest()
