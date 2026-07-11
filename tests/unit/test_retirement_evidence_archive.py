"""Phase 2A tests: offline archive ingestion for Step 1A retirement observations."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.research.step1a_retirement_observation import (
    build_step1a_retirement_observation,
    canonical_sha256,
    _minimal_incomplete_observation,
)
from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence.ingest import (
    ArchiveIngestionError,
    ArchiveLayoutError,
    ExistingRecordIntegrityError,
    ingest_observation,
)

from test_step1a_retirement_observation import _builder_inputs

_TOOL = {"tool_version": "retirement_archive_tool_v1", "tool_commit": "unavailable"}
_STAMP = "2026-07-10T00:00:00+00:00"


def _obs(**overrides: Any) -> dict[str, Any]:
    values = _builder_inputs()
    values.update(overrides)
    return build_step1a_retirement_observation(**values)


def _write_source(directory: Path, payload: Any, name: str = "step1a_retirement_observation.json") -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _ingest(source: Path, root: Path, **kwargs: Any):
    kwargs.setdefault("tool_identity", _TOOL)
    kwargs.setdefault("archived_at", _STAMP)
    return ingest_observation(source_path=source, dest_root=root, **kwargs)


def _record(result: Any) -> dict[str, Any]:
    return json.loads(Path(result.archived_path).read_text(encoding="utf-8"))


# --- accepted ----------------------------------------------------------------
def test_accepted_complete_clean_observation(tmp_path: Path) -> None:
    obs = _obs()
    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")

    assert result.decision == c.DECISION_ACCEPTED
    assert result.reason_tokens == ()
    assert result.duplicate is False and result.conflict is False
    assert Path(result.archived_path).parent.name == c.PARTITION_ACCEPTED

    record = _record(result)
    # Recomputed identity matches the stored observation identity/coverage.
    assert record["recomputed_identity"]["observation_id"] == obs["observation_identity"]["observation_id"]
    assert record["recomputed_identity"]["coverage_key"] == obs["coverage_identity"]["coverage_key"]
    assert record["recomputed_identity"]["composite_config_fingerprint"] == (
        obs["coverage_identity"]["composite_config_fingerprint"]
    )
    # Authority envelope + clean commit preserved verbatim.
    assert record["observation_payload"] == obs
    assert record["source_metadata"]["source_git_commit"] == obs["code_identity"]["git_commit"]
    assert record["source_metadata"]["source_schema_version"] == c.SOURCE_SCHEMA_VERSION


# --- quarantined -------------------------------------------------------------
def test_quarantine_full_incomplete_observation(tmp_path: Path) -> None:
    packet = deepcopy(_builder_inputs()["evidence_packet"])
    packet.pop("strategy_settings_hash")
    obs = _obs(evidence_packet=packet)
    assert obs["observation_completeness"] == "incomplete"

    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")

    assert result.decision == c.DECISION_QUARANTINED
    assert c.REASON_OBSERVATION_INCOMPLETE in result.reason_tokens
    assert Path(result.archived_path).parent.name == c.PARTITION_QUARANTINED
    # Payload preserved without repair.
    assert _record(result)["observation_payload"] == obs


def test_quarantine_minimal_builder_internal_error_observation(tmp_path: Path) -> None:
    obs = _minimal_incomplete_observation("2026-07-10T12:00:00+00:00")
    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")

    assert result.decision == c.DECISION_QUARANTINED
    assert result.reason_tokens == (c.REASON_BUILDER_INTERNAL_ERROR_OBSERVATION,)
    assert _record(result)["observation_payload"] == obs


def test_quarantine_dirty_code_identity(tmp_path: Path) -> None:
    obs = _obs(code_identity={"git_commit": "1" * 40, "git_state": "dirty"})
    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")

    assert result.decision == c.DECISION_QUARANTINED
    assert c.REASON_CODE_IDENTITY_DIRTY in result.reason_tokens


def test_quarantine_unavailable_code_identity(tmp_path: Path) -> None:
    obs = _obs(code_identity={"git_commit": None, "git_state": "unavailable"})
    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")

    assert result.decision == c.DECISION_QUARANTINED
    assert c.REASON_CODE_IDENTITY_UNAVAILABLE in result.reason_tokens


# --- F1: completeness is derived from diagnostics ---------------------------
@pytest.mark.parametrize(
    "diagnostic_field",
    (
        "missing_observation_fields",
        "malformed_observation_fields",
        "compatibility_blockers",
        "permission_context_inconsistencies",
    ),
)
def test_rejects_complete_observation_with_nonempty_diagnostic_collection(
    tmp_path: Path, diagnostic_field: str
) -> None:
    sentinel = "adversarial_diagnostic_content"
    obs = _obs()
    obs[diagnostic_field] = [sentinel]

    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")
    record = _record(result)

    assert result.decision == c.DECISION_REJECTED
    assert c.REASON_OBSERVATION_COMPLETENESS_INCONSISTENT in result.reason_tokens
    assert "observation_payload" not in record
    assert sentinel not in json.dumps(record)


def test_rejects_complete_observation_with_multiple_nonempty_diagnostics(tmp_path: Path) -> None:
    obs = _obs()
    obs["missing_observation_fields"] = ["missing_one"]
    obs["compatibility_blockers"] = ["blocker_one"]

    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")

    assert result.decision == c.DECISION_REJECTED
    assert result.reason_tokens == (c.REASON_OBSERVATION_COMPLETENESS_INCONSISTENT,)
    assert Path(result.archived_path).parent.name == c.PARTITION_REJECTED


def test_rejects_incomplete_observation_with_all_diagnostics_empty(tmp_path: Path) -> None:
    obs = _obs()
    obs["observation_completeness"] = "incomplete"

    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")

    assert result.decision == c.DECISION_REJECTED
    assert c.REASON_OBSERVATION_COMPLETENESS_INCONSISTENT in result.reason_tokens
    assert Path(result.archived_path).parent.name == c.PARTITION_REJECTED


def test_complete_observation_with_all_diagnostics_empty_is_accepted(tmp_path: Path) -> None:
    obs = _obs()
    assert obs["observation_completeness"] == "complete"
    assert all(not obs[field] for field in (
        "missing_observation_fields",
        "malformed_observation_fields",
        "compatibility_blockers",
        "permission_context_inconsistencies",
    ))

    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")

    assert result.decision == c.DECISION_ACCEPTED


def test_minimal_builder_error_completeness_mismatch_uses_canonical_token(tmp_path: Path) -> None:
    obs = _minimal_incomplete_observation("2026-07-10T12:00:00+00:00")
    obs["observation_completeness"] = "complete"

    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")

    assert result.decision == c.DECISION_REJECTED
    assert c.REASON_OBSERVATION_COMPLETENESS_INCONSISTENT in result.reason_tokens


# --- rejected ----------------------------------------------------------------
def test_reject_malformed_json_stores_no_parser_text_and_no_payload(tmp_path: Path) -> None:
    src = tmp_path / "src" / "bad.json"
    src.parent.mkdir(parents=True)
    src.write_text("{ this is not valid json ", encoding="utf-8")

    result = _ingest(src, tmp_path / "arch")
    record = _record(result)

    assert result.decision == c.DECISION_REJECTED
    assert result.reason_tokens == (c.REASON_SOURCE_NOT_VALID_JSON,)
    assert "observation_payload" not in record
    serialized = json.dumps(record)
    assert "not valid json" not in serialized
    assert "Expecting" not in serialized  # no raw parser exception text


@pytest.mark.parametrize(
    "mutate, expected_token",
    [
        (lambda o: o.__setitem__("schema_version", "other_v9"), c.REASON_SCHEMA_VERSION_UNRECOGNIZED),
        (lambda o: o.__setitem__("surprise_key", 1), c.REASON_TOP_LEVEL_KEYS_INVALID),
        (lambda o: o["code_identity"].__setitem__("EXTRA", 1), c.nested_structure_invalid("code_identity")),
        (lambda o: o.__setitem__("report_only", False), c.REASON_AUTHORITY_ENVELOPE_VIOLATION),
        (lambda o: o.__setitem__("safe_to_ignore", 1), c.REASON_AUTHORITY_ENVELOPE_VIOLATION),
        (
            lambda o: o["configuration_hashes"].__setitem__("strategy_settings_hash", "xyz"),
            c.field_domain_invalid("configuration_hashes.strategy_settings_hash"),
        ),
        (
            lambda o: o["writer_outcomes"]["evidence_packet"].__setitem__("final_writer_source", "bogus"),
            c.field_domain_invalid("writer_outcomes.evidence_packet.final_writer_source"),
        ),
        (
            lambda o: o["observation_identity"].__setitem__("observation_id", "a" * 64),
            c.REASON_OBSERVATION_ID_MISMATCH,
        ),
        (
            lambda o: o["coverage_identity"].__setitem__("coverage_key", "a" * 64),
            c.REASON_COVERAGE_KEY_MISMATCH,
        ),
        (
            lambda o: o["coverage_identity"].__setitem__("composite_config_fingerprint", "a" * 64),
            c.REASON_COMPOSITE_FINGERPRINT_MISMATCH,
        ),
    ],
)
def test_reject_contract_violations(tmp_path: Path, mutate: Any, expected_token: str) -> None:
    obs = _obs()
    mutate(obs)
    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")

    assert result.decision == c.DECISION_REJECTED
    assert expected_token in result.reason_tokens
    assert "observation_payload" not in _record(result)


def test_reject_unsafe_raw_sentinel_in_field_absent_from_record(tmp_path: Path) -> None:
    sentinel = "/secret/SENTINEL_raw_operator_content_zzz"
    obs = _obs()
    obs["permission_context_observation"]["research_availability_state"] = sentinel

    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")
    record = _record(result)

    assert result.decision == c.DECISION_REJECTED
    assert "observation_payload" not in record
    assert sentinel not in json.dumps(record)


# --- integrity ---------------------------------------------------------------
def test_canonical_payload_hash_recomputes_and_source_file_hash_is_distinct(
    tmp_path: Path,
) -> None:
    obs = _obs()
    src = _write_source(tmp_path / "src", obs)
    result = _ingest(src, tmp_path / "arch")
    record = _record(result)

    # Canonical payload hash is reproducible from the preserved payload.
    assert canonical_sha256(record["observation_payload"]) == record["source_canonical_payload_sha256"]
    # Exact source-file hash equals the raw bytes hash and is NOT the canonical
    # payload hash (pretty-printed file bytes differ from canonical bytes).
    assert record["source_file_sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert record["source_file_sha256"] != record["source_canonical_payload_sha256"]


def test_archive_record_content_hash_recomputes_and_detects_mutation(tmp_path: Path) -> None:
    obs = _obs()
    result = _ingest(_write_source(tmp_path / "src", obs), tmp_path / "arch")
    record = _record(result)

    without_self = {k: v for k, v in record.items() if k != "archive_record_content_sha256"}
    assert canonical_sha256(without_self) == record["archive_record_content_sha256"]

    # Mutating the payload breaks the canonical payload hash...
    tampered = deepcopy(record)
    tampered["observation_payload"]["permission_context_observation"]["new_buy_allowed"] = None
    assert canonical_sha256(tampered["observation_payload"]) != tampered["source_canonical_payload_sha256"]
    # ...and mutating any record field breaks the record content hash.
    without_self2 = {k: v for k, v in tampered.items() if k != "archive_record_content_sha256"}
    assert canonical_sha256(without_self2) != record["archive_record_content_sha256"]


def test_absolute_source_path_is_never_stored(tmp_path: Path) -> None:
    obs = _obs()
    leak_dir = tmp_path / "SENTINEL_LEAK_DIR_absolute_path"
    src = _write_source(leak_dir, obs)
    result = _ingest(src, tmp_path / "arch")
    record = _record(result)

    assert "SENTINEL_LEAK_DIR_absolute_path" not in json.dumps(record)
    assert record["source_metadata"]["source_basename"] == "step1a_retirement_observation.json"


# --- duplicate / conflict ----------------------------------------------------
def test_identical_source_reingestion_is_noop(tmp_path: Path) -> None:
    obs = _obs()
    root = tmp_path / "arch"
    first = _ingest(_write_source(tmp_path / "a", obs), root)
    second = _ingest(_write_source(tmp_path / "b", obs), root)

    assert first.duplicate is False and second.duplicate is True
    assert first.archived_path == second.archived_path
    assert len(list((root / c.PARTITION_ACCEPTED).glob("*.json"))) == 1


def test_same_content_different_source_filename_is_noop(tmp_path: Path) -> None:
    obs = _obs()
    root = tmp_path / "arch"
    first = _ingest(_write_source(tmp_path / "a", obs, name="one.json"), root)
    second = _ingest(_write_source(tmp_path / "b", obs, name="two.json"), root)

    assert second.duplicate is True
    assert first.archived_path == second.archived_path


def test_valid_same_observation_id_different_content_is_conflict(tmp_path: Path) -> None:
    obs = _obs()
    root = tmp_path / "arch"
    first = _ingest(_write_source(tmp_path / "a", obs), root)
    other = deepcopy(obs)
    # Guard summaries are contract-valid payload detail but not part of the
    # committed physical-observation identity material.
    other["guard_summaries"]["evidence_packet"]["differences_count"] = 1
    assert other["observation_identity"]["observation_id"] == obs["observation_identity"]["observation_id"]
    assert canonical_sha256(other) != canonical_sha256(obs)

    result = _ingest(_write_source(tmp_path / "c", other), root)

    assert result.decision == c.DECISION_REJECTED
    assert result.conflict is True
    assert c.REASON_OBSERVATION_ID_CONTENT_CONFLICT in result.reason_tokens
    assert Path(first.archived_path).exists()


def test_same_coverage_key_with_different_physical_observations_archives_both(tmp_path: Path) -> None:
    root = tmp_path / "arch"
    first = _obs()
    second = _obs(generated_at="2026-07-10T12:00:01+00:00")

    assert first["coverage_identity"]["coverage_key"] == second["coverage_identity"]["coverage_key"]
    assert first["observation_identity"]["observation_id"] != second["observation_identity"]["observation_id"]

    one = _ingest(_write_source(tmp_path / "a", first), root)
    two = _ingest(_write_source(tmp_path / "b", second), root)

    assert one.duplicate is False and two.duplicate is False
    assert len(list((root / c.PARTITION_ACCEPTED).glob("*.json"))) == 2


# --- F2: existing records are independently verified ------------------------
def _rehash_record(record: dict[str, Any]) -> None:
    without_self = {key: value for key, value in record.items() if key != "archive_record_content_sha256"}
    digest = canonical_sha256(without_self)
    assert digest is not None
    record["archive_record_content_sha256"] = digest


def _rehash_payload_and_record(record: dict[str, Any]) -> None:
    digest = canonical_sha256(record["observation_payload"])
    assert digest is not None
    record["source_canonical_payload_sha256"] = digest
    _rehash_record(record)


@pytest.mark.parametrize(
    "name, tamper",
    (
        (
            "payload_only",
            lambda r: r["observation_payload"]["guard_summaries"]["evidence_packet"].__setitem__(
                "differences_count", 1
            ),
        ),
        (
            "stored_canonical_payload_hash_only",
            lambda r: (r.__setitem__("source_canonical_payload_sha256", "b" * 64), _rehash_record(r)),
        ),
        ("archive_record_self_hash_only", lambda r: r.__setitem__("archive_record_content_sha256", "b" * 64)),
        (
            "stored_observation_id_only",
            lambda r: (
                r["source_metadata"].__setitem__("source_observation_id", "b" * 64),
                _rehash_record(r),
            ),
        ),
        (
            "payload_observation_id",
            lambda r: (
                r["observation_payload"]["observation_identity"].__setitem__("observation_id", "b" * 64),
                _rehash_payload_and_record(r),
            ),
        ),
        (
            "stored_coverage_key_only",
            lambda r: (
                r["source_metadata"].__setitem__("source_coverage_key", "b" * 64),
                _rehash_record(r),
            ),
        ),
        (
            "payload_coverage_key",
            lambda r: (
                r["observation_payload"]["coverage_identity"].__setitem__("coverage_key", "b" * 64),
                _rehash_payload_and_record(r),
            ),
        ),
        (
            "stored_source_schema_version_only",
            lambda r: (
                r["source_metadata"].__setitem__("source_schema_version", "other_source_schema_v9"),
                _rehash_record(r),
            ),
        ),
        ("archive_record_schema", lambda r: r.__setitem__("archive_record_schema_version", "other_archive_schema_v9")),
        (
            "provenance_metadata_without_rehash",
            lambda r: r.__setitem__("claimed_evidence_provenance", c.PROVENANCE_UNIT_TEST),
        ),
        (
            "provenance_metadata_with_forged_recomputed_self_hash",
            lambda r: (r.__setitem__("provenance_verified", True), _rehash_record(r)),
        ),
        ("unexpected_top_level_archive_key", lambda r: r.__setitem__("attacker_controlled_key", "SENTINEL")),
        (
            "unexpected_nested_payload_key",
            lambda r: (
                r["observation_payload"].__setitem__("attacker_controlled_payload_key", "SENTINEL"),
                _rehash_payload_and_record(r),
            ),
        ),
    ),
)
def test_tampered_existing_candidate_fails_closed_without_writing(
    tmp_path: Path, name: str, tamper: Any
) -> None:
    obs = _obs()
    root = tmp_path / "arch"
    first = _ingest(_write_source(tmp_path / "first", obs), root)
    archived = Path(first.archived_path)
    record = _record(first)
    tamper(record)
    archived.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    corrupted_bytes = archived.read_text(encoding="utf-8")

    with pytest.raises(ExistingRecordIntegrityError) as raised:
        _ingest(_write_source(tmp_path / "second", obs), root)

    assert raised.value.token == c.EXISTING_RECORD_INTEGRITY_FAILED, name
    assert raised.value.record_basename == archived.name
    assert archived.read_text(encoding="utf-8") == corrupted_bytes  # never overwritten
    assert list((root / c.PARTITION_ACCEPTED).glob("*.json")) == [archived]
    assert list((root / c.PARTITION_QUARANTINED).glob("*.json")) == []
    assert list((root / c.PARTITION_REJECTED).glob("*.json")) == []
    assert list(root.rglob(".*.tmp.*")) == []


def test_existing_record_integrity_error_never_echoes_an_unsafe_candidate_basename(
    tmp_path: Path,
) -> None:
    obs = _obs()
    root = tmp_path / "arch"
    first = _ingest(_write_source(tmp_path / "first", obs), root)
    archived = Path(first.archived_path)
    unsafe = archived.with_name(
        f"SENTINEL_raw_candidate_name__{obs['observation_identity']['observation_id'][:12]}__"
        f"{first.source_canonical_payload_sha256}.json"
    )
    archived.rename(unsafe)
    record = json.loads(unsafe.read_text(encoding="utf-8"))
    record["attacker_controlled_key"] = "SENTINEL_raw_record_content"
    unsafe.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ExistingRecordIntegrityError) as raised:
        _ingest(_write_source(tmp_path / "second", obs), root)

    assert raised.value.record_basename == "invalid_archive_record_basename"
    assert "SENTINEL" not in raised.value.record_basename
    assert list((root / c.PARTITION_ACCEPTED).glob("*.json")) == [unsafe]
    assert list((root / c.PARTITION_REJECTED).glob("*.json")) == []


# --- atomicity ---------------------------------------------------------------
def test_forced_link_failure_leaves_no_partial_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    obs = _obs()
    root = tmp_path / "arch"
    import investment_orchestrator.offline.retirement_evidence.ingest as ingest_mod

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("simulated link failure")

    monkeypatch.setattr(ingest_mod.os, "link", boom)
    with pytest.raises(OSError):
        _ingest(_write_source(tmp_path / "src", obs), root)

    accepted = root / c.PARTITION_ACCEPTED
    assert list(accepted.glob("*.json")) == []
    assert list(accepted.glob(".*.tmp.*")) == []  # temp cleaned up


def test_preexisting_record_unchanged_on_duplicate(tmp_path: Path) -> None:
    obs = _obs()
    root = tmp_path / "arch"
    first = _ingest(_write_source(tmp_path / "a", obs), root)
    original = Path(first.archived_path).read_text(encoding="utf-8")
    _ingest(_write_source(tmp_path / "b", obs), root)
    assert Path(first.archived_path).read_text(encoding="utf-8") == original


# --- provenance --------------------------------------------------------------
def test_provenance_defaults_to_unspecified_and_unverified(tmp_path: Path) -> None:
    result = _ingest(_write_source(tmp_path / "src", _obs()), tmp_path / "arch")
    record = _record(result)
    assert record["claimed_evidence_provenance"] == c.PROVENANCE_UNSPECIFIED
    assert record["provenance_claim_source"] == c.PROVENANCE_CLAIM_SOURCE_DEFAULT
    assert record["provenance_verified"] is False


def test_explicit_provenance_recorded_as_unverified_claim(tmp_path: Path) -> None:
    result = _ingest(
        _write_source(tmp_path / "src", _obs()),
        tmp_path / "arch",
        claimed_provenance=c.PROVENANCE_REAL_CURRENT,
        provenance_claim_source=c.PROVENANCE_CLAIM_SOURCE_OPERATOR,
    )
    record = _record(result)
    assert record["claimed_evidence_provenance"] == c.PROVENANCE_REAL_CURRENT
    assert record["provenance_claim_source"] == c.PROVENANCE_CLAIM_SOURCE_OPERATOR
    assert record["provenance_verified"] is False


def test_provenance_not_inferred_from_path_or_filename(tmp_path: Path) -> None:
    obs = _obs(code_identity={"git_commit": "1" * 40, "git_state": "dirty"})
    # A path/filename that "looks" real must not change the recorded claim.
    src = _write_source(tmp_path / "real_current" / "production", obs, name="real_current.json")
    result = _ingest(src, tmp_path / "arch")
    record = _record(result)
    assert record["claimed_evidence_provenance"] == c.PROVENANCE_UNSPECIFIED
    # No sufficiency / coverage / readiness evaluation anywhere in the record.
    serialized = json.dumps(record)
    for token in ("coverage_satisfied", "retirement_ready", "sufficient", "evidence_complete"):
        assert token not in serialized


def test_invalid_provenance_claim_is_rejected_by_library(tmp_path: Path) -> None:
    with pytest.raises(ArchiveIngestionError):
        _ingest(_write_source(tmp_path / "src", _obs()), tmp_path / "arch", claimed_provenance="bogus")


# --- layout ------------------------------------------------------------------
def test_layout_version_initialized_when_absent(tmp_path: Path) -> None:
    root = tmp_path / "arch"
    _ingest(_write_source(tmp_path / "src", _obs()), root)
    version_file = root / c.ARCHIVE_LAYOUT_VERSION_FILENAME
    assert version_file.read_text(encoding="utf-8").strip() == c.ARCHIVE_LAYOUT_VERSION


def test_incompatible_layout_version_fails_closed_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "arch"
    root.mkdir(parents=True)
    (root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).write_text("retirement_archive_layout_v999\n")

    with pytest.raises(ArchiveLayoutError):
        _ingest(_write_source(tmp_path / "src", _obs()), root)
    # No observation record was written.
    assert not (root / c.PARTITION_ACCEPTED).exists() or list(
        (root / c.PARTITION_ACCEPTED).glob("*.json")
    ) == []


def test_missing_source_raises_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "arch"
    with pytest.raises(ArchiveIngestionError):
        _ingest(tmp_path / "does_not_exist.json", root)


# --- no aggregation / readiness ----------------------------------------------
def test_no_aggregation_or_readiness_fields_in_any_record(tmp_path: Path) -> None:
    root = tmp_path / "arch"
    result = _ingest(_write_source(tmp_path / "src", _obs()), root)
    record = _record(result)
    forbidden = (
        "coverage_satisfied",
        "coverage_state",
        "retirement_ready",
        "evidence_complete",
        "logical_coverage_units",
        "aggregate",
    )
    serialized = json.dumps(record)
    for token in forbidden:
        assert token not in serialized
