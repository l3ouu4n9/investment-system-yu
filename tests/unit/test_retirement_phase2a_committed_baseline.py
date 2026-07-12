"""Frozen pre-coordination Phase 2A compatibility evidence.

The Phase 2A/2B coordination change was committed in ``455d76d``.  This test
must therefore never load ``HEAD`` (or a relative revision) as a supposed
pre-coordination implementation.  Its expected results are immutable fixtures
derived once from the verified immediately-pre-coordination ingestion source at
``d206d76``.  Normal test execution neither reads Git history nor regenerates
or reblesses those fixtures.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import stat
import zlib
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import pytest

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence import archive_coordination as coord
from investment_orchestrator.offline.retirement_evidence import archive_record_contract as rc
from investment_orchestrator.offline.retirement_evidence import ingest as current
from investment_orchestrator.research.step1a_retirement_observation import (
    _minimal_incomplete_observation,
)

from test_retirement_evidence_archive import _STAMP, _TOOL, _obs
from test_step1a_retirement_observation import _builder_inputs


_FIXTURE_SCHEMA_VERSION = "retirement_phase2a_precoord_baseline_v1"
_MANIFEST_SCHEMA_VERSION = "retirement_phase2a_precoord_baseline_manifest_v1"
_BASELINE_COMMIT = "d206d7686240c1c176a77d217416429d09db7858"
_COORDINATION_COMMIT = "455d76d299996fe3f156d5ba0a6dc62150ce3853"
_BASELINE_SOURCE_PATH = "src/investment_orchestrator/offline/retirement_evidence/ingest.py"
_FIXTURE_ENCODING = "base64(gzip(canonical UTF-8 JSON))"
_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "retirement_phase2a_precoord_baseline"
_MANIFEST_PATH = _FIXTURE_DIR / "provenance.json"
_FIXTURE_PATH = _FIXTURE_DIR / "precoord_scenarios_v1.json.gz.b64"
_MAX_DECOMPRESSED_FIXTURE_BYTES = 256 * 1024
_PATH_TYPE_DIRECTORY = "directory"
_PATH_TYPE_REGULAR_FILE = "file"
_PATH_TYPE_SYMBOLIC_LINK = "symbolic_link"
_PATH_TYPE_FIFO = "fifo"
_PATH_TYPE_SOCKET = "socket"
_PATH_TYPE_BLOCK_DEVICE = "block_device"
_PATH_TYPE_CHARACTER_DEVICE = "character_device"
_PATH_TYPE_OTHER = "other"
_ALLOWED_ARCHIVE_PATH_TYPES = frozenset({_PATH_TYPE_DIRECTORY, _PATH_TYPE_REGULAR_FILE})
_PROHIBITED_COORDINATION_FIELDS = frozenset(
    {
        "coordination_contract_version",
        "coordination_status",
        "coordination_lock_mode",
        "coordination_scope",
        "repository_writer_quiescence_verified",
        "external_filesystem_quiescence_verified",
    }
)
_PROHIBITED_COORDINATION_PATH_TOKENS = frozenset(
    {
        "archive_coordination",
        "coordination.anchor",
        "coordination_contract_version",
        "coordination_status",
        "coordination_lock_mode",
        "coordination_scope",
        "repository_writer_quiescence_verified",
        "external_filesystem_quiescence_verified",
    }
)
_SCENARIOS = (
    "accepted_default",
    "accepted_explicit",
    "quarantined",
    "dirty_identity",
    "unavailable_identity",
    "minimal_builder_error",
    "malformed_rejection",
    "unknown_schema_rejection",
    "completeness_contradiction",
    "duplicate_noop",
    "canonical_payload_duplicate",
    "observation_id_conflict",
    "filename_collision",
    "existing_record_integrity_failure",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(f"invalid frozen Phase 2A baseline fixture: {message}")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _DuplicateJsonKeyError(ValueError):
    """Raised only inside the fixture parser for duplicate JSON object keys."""


def _no_duplicate_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(_: str) -> None:
    raise ValueError("nonstandard JSON constant")


def _strict_json_load(value: bytes | str) -> Any:
    """Decode fixture JSON without duplicate-key or nonstandard-value ambiguity."""
    try:
        return json.loads(
            value,
            object_pairs_hook=_no_duplicate_json_object,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (TypeError, UnicodeDecodeError, ValueError) as exc:
        raise AssertionError("invalid frozen Phase 2A baseline fixture: strict JSON decoding failed") from exc


def _bounded_gzip_decompress(
    compressed: bytes,
    *,
    maximum_bytes: int = _MAX_DECOMPRESSED_FIXTURE_BYTES,
) -> bytes:
    """Decode exactly one complete gzip member within a strict output bound."""
    _require(isinstance(maximum_bytes, int) and maximum_bytes >= 0, "invalid decompression limit")
    try:
        decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
        payload = decompressor.decompress(compressed, maximum_bytes + 1)
    except zlib.error as exc:
        raise AssertionError("invalid frozen Phase 2A baseline fixture: gzip decoding failed") from exc
    if len(payload) > maximum_bytes:
        raise AssertionError(
            "invalid frozen Phase 2A baseline fixture: decompressed fixture exceeds maximum size"
        )
    if not decompressor.eof:
        raise AssertionError("invalid frozen Phase 2A baseline fixture: gzip decoding failed")
    _require(
        not decompressor.unused_data and not decompressor.unconsumed_tail,
        "gzip fixture contains trailing member or bytes",
    )
    return payload


def _path_type_from_lstat(path: Path) -> str:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise AssertionError("invalid frozen Phase 2A baseline fixture: archive path inspection failed") from exc
    if stat.S_ISREG(mode):
        return _PATH_TYPE_REGULAR_FILE
    if stat.S_ISDIR(mode):
        return _PATH_TYPE_DIRECTORY
    if stat.S_ISLNK(mode):
        return _PATH_TYPE_SYMBOLIC_LINK
    if stat.S_ISFIFO(mode):
        return _PATH_TYPE_FIFO
    if stat.S_ISSOCK(mode):
        return _PATH_TYPE_SOCKET
    if stat.S_ISBLK(mode):
        return _PATH_TYPE_BLOCK_DEVICE
    if stat.S_ISCHR(mode):
        return _PATH_TYPE_CHARACTER_DEVICE
    return _PATH_TYPE_OTHER


def _read_regular_file_no_follow(path: Path) -> bytes:
    """Read only the exact regular file inspected by ``lstat``."""
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode), "archive snapshot path is not a regular file")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    _require(isinstance(nofollow, int), "no-follow regular-file reads unsupported")
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise AssertionError("invalid frozen Phase 2A baseline fixture: archive file read failed") from exc
    try:
        opened = os.fstat(descriptor)
        _require(
            stat.S_ISREG(opened.st_mode)
            and (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
            "archive file changed during no-follow snapshot",
        )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _load_frozen_precoord_baseline() -> dict[str, dict[str, Any]]:
    """Load the checked-in, hash-verified pre-coordination fixture.

    This deliberately has no Git dependency.  Any missing, malformed, altered,
    unsupported, incomplete, or unexpectedly expanded fixture fails closed.
    """
    try:
        manifest_text = _MANIFEST_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise AssertionError("invalid frozen Phase 2A baseline fixture: provenance manifest unavailable") from exc
    manifest = _strict_json_load(manifest_text)
    _require(isinstance(manifest, Mapping), "provenance manifest must be an object")
    _require(manifest.get("manifest_schema_version") == _MANIFEST_SCHEMA_VERSION, "unsupported manifest schema")
    _require(manifest.get("fixture_schema_version") == _FIXTURE_SCHEMA_VERSION, "unsupported fixture schema")
    _require(manifest.get("baseline_commit") == _BASELINE_COMMIT, "unexpected baseline commit")
    _require(manifest.get("coordination_commit") == _COORDINATION_COMMIT, "unexpected coordination commit")
    _require(manifest.get("baseline_source_path") == _BASELINE_SOURCE_PATH, "unexpected baseline source path")
    _require(
        isinstance(manifest.get("generation_method"), str) and manifest["generation_method"].strip(),
        "generation method description missing",
    )
    _require(manifest.get("coordination_metadata_absent") is True, "coordination absence statement missing")
    _require(manifest.get("covered_scenarios") == list(_SCENARIOS), "covered scenario matrix differs")

    fixtures = manifest.get("fixtures")
    _require(isinstance(fixtures, Mapping) and set(fixtures) == {_FIXTURE_PATH.name}, "unexpected fixture files")
    fixture_metadata = fixtures.get(_FIXTURE_PATH.name)
    _require(isinstance(fixture_metadata, Mapping), "fixture metadata missing")
    _require(fixture_metadata.get("encoding") == _FIXTURE_ENCODING, "unsupported fixture encoding")

    try:
        encoded = _FIXTURE_PATH.read_bytes()
    except OSError as exc:
        raise AssertionError("invalid frozen Phase 2A baseline fixture: fixture file unavailable") from exc
    _require(_sha256(encoded) == fixture_metadata.get("file_sha256"), "fixture file hash differs")

    try:
        compressed = base64.b64decode(b"".join(encoded.split()), validate=True)
    except ValueError as exc:
        raise AssertionError("invalid frozen Phase 2A baseline fixture: fixture decoding failed") from exc
    payload_bytes = _bounded_gzip_decompress(compressed)
    payload = _strict_json_load(payload_bytes)
    _require(_sha256(compressed) == fixture_metadata.get("compressed_payload_sha256"), "compressed payload hash differs")
    _require(_sha256(payload_bytes) == fixture_metadata.get("decompressed_json_sha256"), "fixture JSON hash differs")
    _require(isinstance(payload, Mapping), "fixture payload must be an object")
    _require(payload.get("fixture_schema_version") == _FIXTURE_SCHEMA_VERSION, "payload schema differs")
    scenarios = payload.get("scenarios")
    _require(isinstance(scenarios, Mapping), "scenario payload missing")
    _require(set(scenarios) == set(_SCENARIOS), "required scenario missing or unexpected scenario present")

    decoded: dict[str, dict[str, Any]] = {}
    for scenario in _SCENARIOS:
        fixture = scenarios[scenario]
        _require(isinstance(fixture, Mapping), f"{scenario} fixture must be an object")
        outcomes = fixture.get("outcomes")
        snapshot = fixture.get("snapshot")
        _require(isinstance(outcomes, list), f"{scenario} outcomes missing")
        _require(isinstance(snapshot, Mapping), f"{scenario} snapshot missing")
        decoded_snapshot: dict[str, tuple[str, bytes | None]] = {}
        for relative_path, entry in snapshot.items():
            _require(isinstance(relative_path, str) and isinstance(entry, Mapping), f"{scenario} snapshot entry malformed")
            kind = entry.get("kind")
            encoded_bytes = entry.get("bytes_base64")
            _require(kind in {"directory", "file"}, f"{scenario} snapshot kind malformed")
            if kind == "directory":
                _require(encoded_bytes is None, f"{scenario} directory unexpectedly has bytes")
                decoded_snapshot[relative_path] = (kind, None)
            else:
                _require(isinstance(encoded_bytes, str), f"{scenario} file bytes missing")
                try:
                    decoded_snapshot[relative_path] = (kind, base64.b64decode(encoded_bytes, validate=True))
                except ValueError as exc:
                    raise AssertionError(
                        f"invalid frozen Phase 2A baseline fixture: {scenario} file bytes malformed"
                    ) from exc
        _assert_coordination_metadata_absent(decoded_snapshot, scenario=scenario)
        decoded[scenario] = {"outcomes": outcomes, "snapshot": decoded_snapshot}
    return decoded


def _write(path: Path, payload: Any, *, compact: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    elif compact:
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _call(source: Path, root: Path, anchor: Path, **kwargs: Any):
    arguments = {
        "source_path": source,
        "dest_root": root,
        "tool_identity": _TOOL,
        "archived_at": _STAMP,
        "coordination_path": anchor,
        **kwargs,
    }
    return current.ingest_observation(**arguments)


def _normalized_result(result: Any, root: Path) -> dict[str, Any]:
    archived = Path(result.archived_path) if result.archived_path is not None else None
    return {
        "decision": result.decision,
        "reason_tokens": list(result.reason_tokens),
        "archived_path": str(archived.relative_to(root)) if archived is not None else None,
        "duplicate": result.duplicate,
        "conflict": result.conflict,
        "record_content_sha256": result.record_content_sha256,
        "source_file_sha256": result.source_file_sha256,
        "source_canonical_payload_sha256": result.source_canonical_payload_sha256,
    }


def _normalized_exception(exc: Exception) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "token": getattr(exc, "token", None),
        "record_basename": getattr(exc, "record_basename", None),
    }


def _snapshot(root: Path) -> dict[str, tuple[str, bytes | None]]:
    if not root.exists():
        return {}
    result: dict[str, tuple[str, bytes | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        path_type = _path_type_from_lstat(path)
        _require(
            path_type in _ALLOWED_ARCHIVE_PATH_TYPES,
            f"archive snapshot contains unsupported path type {path_type}",
        )
        if path_type == _PATH_TYPE_DIRECTORY:
            result[relative] = (path_type, None)
        else:
            result[relative] = (path_type, _read_regular_file_no_follow(path))
    return result


def _assert_coordination_metadata_absent(
    snapshot: Mapping[str, tuple[str, bytes | None]],
    *,
    scenario: str,
) -> None:
    for relative_path, (path_type, content) in snapshot.items():
        for component in PurePosixPath(relative_path).parts:
            normalized_component = component.lower()
            _require(
                not any(token in normalized_component for token in _PROHIBITED_COORDINATION_PATH_TOKENS),
                f"{scenario} archive snapshot has a prohibited coordination path",
            )
        if path_type != _PATH_TYPE_REGULAR_FILE or content is None:
            continue
        if relative_path == c.ARCHIVE_LAYOUT_VERSION_FILENAME:
            _require(
                content == (c.ARCHIVE_LAYOUT_VERSION + "\n").encode("utf-8"),
                f"{scenario} layout bytes are not the canonical pre-coordination layout",
            )
            continue
        _require(relative_path.endswith(".json"), f"{scenario} archive file is not a JSON record")
        payload = _strict_json_load(content)
        _assert_no_prohibited_coordination_keys(payload, scenario=scenario)


def _assert_no_prohibited_coordination_keys(value: Any, *, scenario: str) -> None:
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            _require(
                key not in _PROHIBITED_COORDINATION_FIELDS,
                f"{scenario} archive JSON has prohibited coordination metadata",
            )
            _assert_no_prohibited_coordination_keys(nested_value, scenario=scenario)
    elif isinstance(value, list):
        for nested_value in value:
            _assert_no_prohibited_coordination_keys(nested_value, scenario=scenario)


def _run_scenario(base: Path, scenario: str) -> dict[str, Any]:
    root = base / "archive"
    anchor = base / "coordination.anchor"
    base.mkdir(parents=True, exist_ok=True)
    anchor.write_bytes(coord.COORDINATION_ANCHOR_BYTES)
    outcomes: list[dict[str, Any]] = []

    def ingest(payload: Any, name: str, **kwargs: Any) -> Any:
        source = _write(base / "sources" / name, payload)
        try:
            value = _call(source, root, anchor, **kwargs)
        except Exception as exc:  # expected integrity scenarios are normalized
            outcomes.append({"exception": _normalized_exception(exc)})
            return exc
        outcomes.append({"result": _normalized_result(value, root)})
        return value

    if scenario == "accepted_default":
        ingest(_obs(), "source.json")
    elif scenario == "accepted_explicit":
        ingest(
            _obs(),
            "source.json",
            claimed_provenance=c.PROVENANCE_INTEGRATION_TEST,
            provenance_claim_source=c.PROVENANCE_CLAIM_SOURCE_OPERATOR,
        )
    elif scenario == "quarantined":
        packet = deepcopy(_builder_inputs()["evidence_packet"])
        packet.pop("strategy_settings_hash")
        ingest(_obs(evidence_packet=packet), "source.json")
    elif scenario == "dirty_identity":
        ingest(
            _obs(code_identity={"git_commit": "1" * 40, "git_state": "dirty"}),
            "source.json",
        )
    elif scenario == "unavailable_identity":
        ingest(
            _obs(code_identity={"git_commit": None, "git_state": "unavailable"}),
            "source.json",
        )
    elif scenario == "minimal_builder_error":
        ingest(_minimal_incomplete_observation(_STAMP), "source.json")
    elif scenario == "malformed_rejection":
        ingest(b"{ not valid json ", "source.json")
    elif scenario == "unknown_schema_rejection":
        payload = _obs()
        payload["schema_version"] = "other_schema_v9"
        ingest(payload, "source.json")
    elif scenario == "completeness_contradiction":
        payload = _obs()
        payload["missing_observation_fields"] = ["missing_one"]
        ingest(payload, "source.json")
    elif scenario == "duplicate_noop":
        payload = _obs()
        ingest(payload, "first.json")
        ingest(payload, "second.json")
    elif scenario == "canonical_payload_duplicate":
        payload = _obs()
        first = _write(base / "sources" / "pretty.json", payload)
        second = _write(base / "sources" / "compact.json", payload, compact=True)
        for source in (first, second):
            value = _call(source, root, anchor)
            outcomes.append({"result": _normalized_result(value, root)})
    elif scenario == "observation_id_conflict":
        payload = _obs()
        ingest(payload, "first.json")
        changed = deepcopy(payload)
        changed["guard_summaries"]["evidence_packet"]["differences_count"] = 1
        ingest(changed, "changed.json")
    elif scenario == "filename_collision":
        original = _obs()
        first = ingest(original, "first.json")
        assert not isinstance(first, Exception)
        changed = _obs(generated_at="2026-07-10T12:00:01+00:00")
        changed_sha = current.p1a.canonical_sha256(changed)
        assert changed_sha is not None
        collision_name = rc.expected_observation_record_filename(
            changed,
            rc.stored_observation_id(changed),
            changed_sha,
        )
        collision = root / c.PARTITION_ACCEPTED / collision_name
        collision.write_bytes(Path(first.archived_path).read_bytes())
        ingest(changed, "changed.json")
    elif scenario == "existing_record_integrity_failure":
        payload = _obs()
        first = ingest(payload, "first.json")
        assert not isinstance(first, Exception)
        archived = Path(first.archived_path)
        record = json.loads(archived.read_text(encoding="utf-8"))
        record["archive_record_content_sha256"] = "b" * 64
        archived.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        ingest(payload, "second.json")
    else:  # pragma: no cover - fixed code-owned scenario list
        raise AssertionError(scenario)

    snapshot = _snapshot(root)
    _assert_coordination_metadata_absent(snapshot, scenario=scenario)
    return {"outcomes": outcomes, "snapshot": snapshot}


def test_complete_frozen_precoord_phase2a_behavior_matches_coordinated_implementation(
    tmp_path: Path,
) -> None:
    baseline = _load_frozen_precoord_baseline()
    for scenario in _SCENARIOS:
        coordinated = _run_scenario(tmp_path / "coordinated" / scenario, scenario)
        assert coordinated == baseline[scenario], scenario


def test_single_member_bounded_gzip_decompression_accepts_exact_limit_and_rejects_invalid_inputs() -> None:
    exact_limit_payload = b"x" * _MAX_DECOMPRESSED_FIXTURE_BYTES
    assert _bounded_gzip_decompress(gzip.compress(exact_limit_payload, mtime=0)) == exact_limit_payload

    with pytest.raises(AssertionError, match="decompressed fixture exceeds maximum size"):
        _bounded_gzip_decompress(gzip.compress(exact_limit_payload + b"x", mtime=0))
    with pytest.raises(AssertionError, match="gzip decoding failed"):
        _bounded_gzip_decompress(b"not-a-gzip-payload")
    with pytest.raises(AssertionError, match="gzip decoding failed"):
        _bounded_gzip_decompress(gzip.compress(b"valid", mtime=0)[:-1])
    with pytest.raises(AssertionError, match="trailing member or bytes"):
        _bounded_gzip_decompress(gzip.compress(b"valid", mtime=0) + b"trailing-data")
    with pytest.raises(AssertionError, match="trailing member or bytes"):
        _bounded_gzip_decompress(gzip.compress(b"first", mtime=0) + gzip.compress(b"second", mtime=0))
    with pytest.raises(AssertionError, match="trailing member or bytes"):
        _bounded_gzip_decompress(gzip.compress(b"first", mtime=0) + gzip.compress(b"", mtime=0))


@pytest.mark.parametrize(
    "payload",
    (
        '{"manifest_schema_version":"one","manifest_schema_version":"two"}',
        '{"fixtures":{"fixture":{"file_sha256":"one","file_sha256":"two"}}}',
        '{"fixture_schema_version":"one","fixture_schema_version":"two"}',
        '{"scenarios":{"accepted":{"snapshot":{"entry":{"kind":"file","kind":"directory"}}}}}',
    ),
)
def test_strict_json_loader_rejects_duplicate_keys_at_every_fixture_level(payload: str) -> None:
    with pytest.raises(AssertionError, match="strict JSON decoding failed"):
        _strict_json_load(payload)


def test_snapshot_uses_no_follow_exact_regular_file_and_directory_types(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    accepted = archive / c.PARTITION_ACCEPTED
    accepted.mkdir(parents=True)
    record = accepted / "record.json"
    record.write_bytes(b"{}")

    assert _snapshot(archive) == {
        c.PARTITION_ACCEPTED: (_PATH_TYPE_DIRECTORY, None),
        f"{c.PARTITION_ACCEPTED}/record.json": (_PATH_TYPE_REGULAR_FILE, b"{}"),
    }


def test_snapshot_rejects_symbolic_links_and_nonregular_entries(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    target_file = tmp_path / "target.json"
    target_file.write_bytes(b"{}")
    target_directory = tmp_path / "target-directory"
    target_directory.mkdir()

    try:
        os.symlink(target_file, archive / "file-link")
    except (AttributeError, NotImplementedError, OSError):
        pytest.skip("symbolic links unsupported in this test environment")
    with pytest.raises(AssertionError, match="unsupported path type symbolic_link"):
        _snapshot(archive)

    (archive / "file-link").unlink()
    os.symlink(target_directory, archive / "directory-link")
    with pytest.raises(AssertionError, match="unsupported path type symbolic_link"):
        _snapshot(archive)

    (archive / "directory-link").unlink()
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs unsupported in this test environment")
    try:
        os.mkfifo(archive / "fifo")
    except OSError:
        pytest.skip("FIFO creation unavailable in this test environment")
    with pytest.raises(AssertionError, match="unsupported path type fifo"):
        _snapshot(archive)


def _coordination_check_snapshot(
    record_payload: Mapping[str, Any],
    *,
    record_path: str = "accepted/record.json",
) -> dict[str, tuple[str, bytes | None]]:
    return {
        c.ARCHIVE_LAYOUT_VERSION_FILENAME: (
            _PATH_TYPE_REGULAR_FILE,
            (c.ARCHIVE_LAYOUT_VERSION + "\n").encode("utf-8"),
        ),
        c.PARTITION_ACCEPTED: (_PATH_TYPE_DIRECTORY, None),
        record_path: (
            _PATH_TYPE_REGULAR_FILE,
            json.dumps(record_payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        ),
    }


def test_coordination_metadata_exclusion_is_structural_and_path_specific() -> None:
    with pytest.raises(AssertionError, match="prohibited coordination metadata"):
        _assert_coordination_metadata_absent(
            _coordination_check_snapshot({"coordination_status": "verified"}),
            scenario="field",
        )
    with pytest.raises(AssertionError, match="prohibited coordination metadata"):
        _assert_coordination_metadata_absent(
            _coordination_check_snapshot({"nested": {"coordination_scope": "same_anchor"}}),
            scenario="nested-field",
        )
    with pytest.raises(AssertionError, match="prohibited coordination path"):
        _assert_coordination_metadata_absent(
            _coordination_check_snapshot({}, record_path="accepted/coordination_status.json"),
            scenario="path",
        )

    _assert_coordination_metadata_absent(
        _coordination_check_snapshot({"note": "ordinary coordination prose is permitted"}),
        scenario="legitimate-prose",
    )
