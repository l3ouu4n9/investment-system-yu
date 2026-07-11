"""Phase 2B-2 tests for the deterministic archive-integrity index and CLI."""

from __future__ import annotations

import json
import os
import stat
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence import archive_coordination as coord
from investment_orchestrator.offline.retirement_evidence import archive_index as idx
from investment_orchestrator.offline.retirement_evidence import archive_scan as scan
from investment_orchestrator.offline.retirement_evidence import record_verifier as rv
from investment_orchestrator.offline.retirement_evidence import verify_cli
from investment_orchestrator.offline.retirement_evidence.ingest import ingest_observation
from investment_orchestrator.research import step1a_retirement_observation as p1a
from investment_orchestrator.research.step1a_retirement_observation import (
    _minimal_incomplete_observation,
    build_step1a_retirement_observation,
    canonical_sha256,
)
from investment_orchestrator.workflow import step1_research

from test_step1a_retirement_observation import _builder_inputs


_TOOL = {"tool_version": c.ARCHIVE_TOOL_VERSION, "tool_commit": "unavailable"}
_STAMP = "2026-07-10T00:00:00+00:00"


def _obs(**overrides: Any) -> dict[str, Any]:
    values = _builder_inputs()
    values.update(overrides)
    return build_step1a_retirement_observation(**values)


def _ingest_payload(root: Path, payload: Any, name: str = "step1a_retirement_observation.json"):
    source = root.parent / "sources" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (bytes, bytearray)):
        source.write_bytes(payload)
    else:
        source.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return ingest_observation(
        source_path=source,
        dest_root=root,
        coordination_path=_coordination_path(root),
        tool_identity=_TOOL,
        archived_at=_STAMP,
    )


def _coordination_path(root: Path) -> Path:
    anchor = root.parent / "retirement-archive-coordination.anchor"
    if not anchor.exists():
        anchor.write_bytes(coord.COORDINATION_ANCHOR_BYTES)
    return anchor


def _index(root: Path, limits: scan.ScanLimits | None = None) -> dict[str, Any]:
    return idx.index_archive(root, limits, coordination_path=_coordination_path(root))


def _verify(argv: list[str]) -> int:
    root = Path(argv[argv.index("--archive-root") + 1])
    return verify_cli.main(
        [*argv, "--coordination-file", str(_coordination_path(root))]
    )


def _archive(tmp_path: Path, name: str = "archive") -> Path:
    root = tmp_path / name
    _ingest_payload(root, _obs())
    return root


def _rehash_record(record: dict[str, Any]) -> None:
    body = {k: v for k, v in record.items() if k != "archive_record_content_sha256"}
    digest = canonical_sha256(body)
    assert digest is not None
    record["archive_record_content_sha256"] = digest


def _record_path(root: Path, partition: str = c.PARTITION_ACCEPTED) -> Path:
    return next((root / partition).iterdir())


def _renamed_copy(record: Path, dest_dir: Path | None = None) -> Path:
    """Copy one record under a different but still conventional basename."""
    dest_dir = dest_dir if dest_dir is not None else record.parent
    if record.name.startswith("rejected__"):
        new_name = "rejected__" + "0" * 16 + record.name[len("rejected__") + 16:]
    else:
        new_name = "1" + record.name
    target = dest_dir / new_name
    target.write_bytes(record.read_bytes())
    return target


def _groups(report: dict[str, Any], category: str) -> list[dict[str, Any]]:
    return [g for g in report["duplicate_groups"] if g["category"] == category]


def _tree_snapshot(root: Path) -> dict[str, tuple[Any, ...]]:
    snapshot: dict[str, tuple[Any, ...]] = {}
    for path in sorted(root.rglob("*")):
        st = os.lstat(path)
        rel = str(path.relative_to(root))
        if stat.S_ISDIR(st.st_mode):
            snapshot[rel] = ("dir", st.st_mode)
        else:
            snapshot[rel] = ("file", st.st_mode, st.st_size, st.st_mtime_ns, path.read_bytes())
    return snapshot


# --- clean archive, schema, and hashes -------------------------------------------
def test_clean_archive_index_schema_and_authority_envelope(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    _ingest_payload(root, b"{ not valid json ")

    report = _index(root)

    assert report["index_schema_version"] == "retirement_archive_index_v1"
    assert report["authority_envelope"] == {
        "is_llm_generated": False,
        "report_only": True,
        "not_authorization": True,
        "not_execution_authorization": True,
        "permission_effect": "none",
        "consumed_by_gates": False,
        "consumed_by_order_path": False,
        "consumed_by_downstream": False,
        "safe_to_ignore": True,
        "assessment_scope": "archive_integrity_only",
    }
    assert report["archive_assessment_state"] == idx.ASSESSMENT_CLEAN
    assert report["coordination_contract_version"] == coord.COORDINATION_CONTRACT_VERSION
    assert report["coordination_status"] == coord.STATUS_VERIFIED
    assert report["coordination_lock_mode"] == coord.LOCK_MODE_SHARED
    assert report["coordination_scope"] == (
        "repository_owned_compliant_writers_using_same_anchor"
    )
    assert report["repository_writer_quiescence_verified"] is True
    assert report["external_filesystem_quiescence_verified"] is False
    assert report["archive_root_label"] == "archive_root"
    assert report["archive_layout_version"] == c.ARCHIVE_LAYOUT_VERSION
    assert report["archive_layout_status"] == scan.LAYOUT_CANONICAL
    assert report["verification_limits"] == scan.ScanLimits().as_report_mapping()
    assert report["source_record_count"] == 2
    assert report["unread_record_count"] == 0
    assert report["counts_by_partition"] == {"accepted": 1, "quarantined": 0, "rejected": 1}
    assert report["counts_by_verification_state"] == {
        "valid_accepted_record": 1,
        "valid_rejected_reason_record": 1,
    }
    assert report["counts_by_stable_read_state"] == {scan.READ_STABLE: 2}
    assert report["counts_by_final_revalidation_state"] == {
        scan.REVALIDATION_STABLE: 2
    }
    assert report["duplicate_groups"] == []
    assert report["unexpected_entries"] == []
    assert all(
        entry["stable_read_state"] == scan.READ_STABLE
        and entry["final_revalidation_state"] == scan.REVALIDATION_STABLE
        for entry in report["source_set_manifest"]
    )
    assert report["non_authorization_note"] == idx.NON_AUTHORIZATION_NOTE
    assert [e["entry"] for e in report["record_entries"]] == sorted(
        e["entry"] for e in report["record_entries"]
    )

    accepted = [e for e in report["record_entries"] if e["ingestion_decision"] == "accepted"]
    assert len(accepted) == 1
    entry = accepted[0]
    assert entry["identity_facts_valid"] is True
    assert entry["self_integrity_status"] == rv.SELF_INTEGRITY_VERIFIED
    assert entry["provenance_verified"] is False
    assert entry["claimed_evidence_provenance"] == c.PROVENANCE_UNSPECIFIED
    assert entry["observed_contract_partition_id"] is not None
    assert entry["findings"] == []

    rejected = [e for e in report["record_entries"] if e["ingestion_decision"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["self_integrity_status"] == rv.SELF_INTEGRITY_NOT_AVAILABLE_IN_SCHEMA
    assert rejected[0]["observation_id"] is None
    assert rejected[0]["coverage_key"] is None
    assert rejected[0]["observed_contract_partition_id"] is None
    assert rejected[0]["observed_file_sha256"] is not None


def test_report_hashes_recompute_exactly(tmp_path: Path) -> None:
    report = _index(_archive(tmp_path))

    assert idx.compute_report_content_sha256(report) == report["report_content_sha256"]
    assert p1a.canonical_sha256(report["source_set_manifest"]) == report["indexed_source_set_sha256"]
    body = {k: v for k, v in report.items() if k != "report_content_sha256"}
    assert p1a.canonical_sha256(body) == report["report_content_sha256"]


def test_report_never_contains_raw_paths_basenames_or_timestamps(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    _ingest_payload(root, _obs(), name="résumé secret source.json")
    (root / c.PARTITION_ACCEPTED / "café notes.txt").write_text("x", encoding="utf-8")

    report = _index(root)
    serialized = idx.serialize_index_report(report)

    assert "résumé" not in serialized
    assert "secret" not in serialized
    assert "café" not in serialized
    assert str(root) not in serialized
    assert str(root.resolve()) not in serialized
    assert "/tmp" not in serialized
    assert '"generated_at"' not in serialized
    assert "2026-07-10T" not in serialized  # no archived-at or scan timestamp
    unsafe = [
        entry
        for entry in report["record_entries"]
        if entry["entry"].startswith("unsafe_name:accepted:")
    ]
    assert len(unsafe) == 1
    assert unsafe[0]["content_status"] == rv.CONTENT_CORRUPT
    assert unsafe[0]["placement_status"] == rv.PLACEMENT_UNSAFE_ENTRY_METADATA


def test_corrupt_content_severity_is_invariant_under_safe_filename_renames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    original = _record_path(root)
    corrupt_bytes = b"{ not valid archive JSON"
    names = (original.name, "corrupt_record.txt", ".hidden", "record.bak")
    original.write_bytes(corrupt_bytes)
    for name in names[1:]:
        (root / c.PARTITION_ACCEPTED / name).write_bytes(corrupt_bytes)

    calls: list[tuple[bytes, Any, Any]] = []
    real_verify = idx.rv.verify_archive_record

    def capture_verify(record_bytes: bytes, *, filename: Any, expected_partition: Any):
        calls.append((record_bytes, filename, expected_partition))
        return real_verify(
            record_bytes, filename=filename, expected_partition=expected_partition
        )

    monkeypatch.setattr(idx.rv, "verify_archive_record", capture_verify)
    report = _index(root)

    assert sorted(filename for _bytes, filename, _partition in calls) == sorted(names)
    assert all(data == corrupt_bytes for data, _filename, _partition in calls)
    assert all(partition == c.PARTITION_ACCEPTED for _data, _filename, partition in calls)
    entries = [
        entry
        for entry in report["record_entries"]
        if entry["expected_physical_partition"] == c.PARTITION_ACCEPTED
    ]
    assert len(entries) == len(names)
    assert {entry["content_status"] for entry in entries} == {rv.CONTENT_CORRUPT}
    assert all(entry["identity_facts_valid"] is False for entry in entries)
    assert report["archive_assessment_state"] == idx.ASSESSMENT_INTEGRITY_FAILURES
    assert idx.TOKEN_RECORD_CORRUPT in report["assessment_reason_tokens"]["integrity_failures"]
    assert report["source_record_count"] == len(names)
    assert len(report["source_set_manifest"]) == len(names)


def test_valid_misnamed_records_remain_valid_in_all_record_partitions(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    _ingest_payload(root, _minimal_incomplete_observation("2026-07-10T12:00:00+00:00"))
    _ingest_payload(root, b"{ not valid json ")
    renamed = {
        c.PARTITION_ACCEPTED: "accepted_record.txt",
        c.PARTITION_QUARANTINED: ".quarantined_record",
        c.PARTITION_REJECTED: "rejected_record.bak",
    }
    for partition, new_name in renamed.items():
        original = _record_path(root, partition)
        original.rename(original.with_name(new_name))

    report = _index(root)

    assert report["source_record_count"] == 3
    assert report["unread_record_count"] == 0
    assert report["archive_assessment_state"] == idx.ASSESSMENT_WARNINGS
    assert report["assessment_reason_tokens"]["integrity_failures"] == []
    assert idx.TOKEN_RECORD_FILENAME_MISMATCH in report["assessment_reason_tokens"]["warnings"]
    assert {entry["entry"] for entry in report["record_entries"]} == {
        f"accepted/{renamed[c.PARTITION_ACCEPTED]}",
        f"quarantined/{renamed[c.PARTITION_QUARANTINED]}",
        f"rejected/{renamed[c.PARTITION_REJECTED]}",
    }
    assert {entry["record_kind"] for entry in report["record_entries"]} == {
        rv.RECORD_KIND_ACCEPTED,
        rv.RECORD_KIND_QUARANTINED,
        rv.RECORD_KIND_REJECTED,
    }
    assert {entry["content_status"] for entry in report["record_entries"]} == {
        rv.CONTENT_VALID
    }
    assert {entry["placement_status"] for entry in report["record_entries"]} == {
        rv.PLACEMENT_FILENAME_MISMATCH
    }
    observations = [
        entry for entry in report["record_entries"] if entry["ingestion_decision"] != c.DECISION_REJECTED
    ]
    assert all(entry["identity_facts_valid"] is True for entry in observations)


def test_foreign_regular_partition_files_are_verified_not_tidy_warnings(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    foreign = {
        (c.PARTITION_ACCEPTED, "notes.md"): b"not JSON",
        (c.PARTITION_QUARANTINED, "unknown_schema.json"): (
            b'{"archive_record_schema_version":"other_archive_schema_v9"}'
        ),
        (c.PARTITION_REJECTED, ".wrong_top_level"): b"[]",
    }
    for (partition, name), data in foreign.items():
        (root / partition / name).write_bytes(data)

    report = _index(root)

    by_entry = {entry["entry"]: entry for entry in report["record_entries"]}
    assert report["source_record_count"] == 4
    assert report["unexpected_entries"] == []
    assert by_entry["accepted/notes.md"]["content_status"] == rv.CONTENT_CORRUPT
    assert by_entry["quarantined/unknown_schema.json"]["content_status"] == (
        rv.CONTENT_SCHEMA_INCOMPATIBLE
    )
    assert by_entry["rejected/.wrong_top_level"]["content_status"] == rv.CONTENT_CORRUPT
    assert all(
        entry["stable_read_state"] == scan.READ_STABLE
        for key, entry in by_entry.items()
        if key in {"accepted/notes.md", "quarantined/unknown_schema.json", "rejected/.wrong_top_level"}
    )
    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert idx.TOKEN_RECORD_SCHEMA_UNRECOGNIZED in report["assessment_reason_tokens"]["unverifiable"]


def test_report_carries_no_readiness_or_coverage_authority_fields(tmp_path: Path) -> None:
    serialized = idx.serialize_index_report(_index(_archive(tmp_path)))
    for token in c.READINESS_DENYLIST:
        assert token not in serialized


# --- determinism -------------------------------------------------------------------
def test_same_archive_bytes_in_different_roots_yield_identical_reports(tmp_path: Path) -> None:
    root_a = tmp_path / "first-location" / "archive"
    root_b = tmp_path / "second-location" / "elsewhere"
    for root in (root_a, root_b):
        _ingest_payload(root, _obs())
        _ingest_payload(root, b"{ not valid json ")

    serialized_a = idx.serialize_index_report(_index(root_a))
    serialized_b = idx.serialize_index_report(_index(root_b))

    assert serialized_a == serialized_b


def test_report_is_independent_of_filesystem_iteration_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    _ingest_payload(root, _obs(generated_at="2026-07-10T13:00:00+00:00"))
    baseline = idx.serialize_index_report(_index(root))

    real_scandir = os.scandir

    class _ReversedScandir:
        def __init__(self, path: Any) -> None:
            self._iterator = real_scandir(path)
            self._entries = list(self._iterator)

        def __enter__(self):
            return reversed(self._entries)

        def __exit__(self, *_args: Any) -> None:
            self._iterator.close()

    monkeypatch.setattr(os, "scandir", _ReversedScandir)
    reordered = idx.serialize_index_report(_index(root))

    assert reordered == baseline


def test_repeated_scans_are_byte_identical(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    first = idx.serialize_index_report(_index(root))
    second = idx.serialize_index_report(_index(root))
    assert first == second


# --- duplicate / conflict taxonomy ---------------------------------------------------
def test_exact_payload_and_physical_duplicates_are_warnings(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    original = _record_path(root)
    copy = _renamed_copy(original)

    report = _index(root)

    refs = sorted(
        (f"accepted/{original.name}", f"accepted/{copy.name}")
    )
    exact = _groups(report, idx.CATEGORY_EXACT_RECORD_DUPLICATE)
    payload = _groups(report, idx.CATEGORY_PAYLOAD_DUPLICATE)
    physical = _groups(report, idx.CATEGORY_PHYSICAL_OBSERVATION_DUPLICATE)
    assert [g["members"] for g in exact] == [refs]
    assert [g["members"] for g in payload] == [refs]
    assert [g["members"] for g in physical] == [refs]
    assert {g["severity"] for g in exact + payload + physical} == {idx.SEVERITY_WARNING}
    assert all(g["blocks_archive_clean"] is True for g in exact + payload + physical)
    assert physical[0]["key"]["observation_id"] is not None
    assert len(physical[0]["key"]["source_canonical_payload_sha256"]) == 64
    # The renamed copy also mismatches its derived filename - warning as well.
    assert report["archive_assessment_state"] == idx.ASSESSMENT_WARNINGS
    tokens = report["assessment_reason_tokens"]["warnings"]
    assert idx.TOKEN_EXACT_RECORD_DUPLICATE in tokens
    assert idx.TOKEN_PAYLOAD_DUPLICATE in tokens
    assert idx.TOKEN_PHYSICAL_OBSERVATION_DUPLICATE in tokens
    assert idx.TOKEN_RECORD_FILENAME_MISMATCH in tokens
    assert report["assessment_reason_tokens"]["integrity_failures"] == []


def test_valid_misnamed_duplicate_participates_in_duplicate_taxonomy(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    original = _record_path(root)
    duplicate = root / c.PARTITION_ACCEPTED / "duplicate_record.txt"
    duplicate.write_bytes(original.read_bytes())

    report = _index(root)

    duplicate_entry = [
        entry for entry in report["record_entries"] if entry["entry"] == "accepted/duplicate_record.txt"
    ]
    assert len(duplicate_entry) == 1
    assert duplicate_entry[0]["content_status"] == rv.CONTENT_VALID
    assert duplicate_entry[0]["identity_facts_valid"] is True
    assert duplicate_entry[0]["placement_status"] == rv.PLACEMENT_FILENAME_MISMATCH
    assert all(
        "accepted/duplicate_record.txt" in group["members"]
        for group in (
            _groups(report, idx.CATEGORY_EXACT_RECORD_DUPLICATE)
            + _groups(report, idx.CATEGORY_PAYLOAD_DUPLICATE)
            + _groups(report, idx.CATEGORY_PHYSICAL_OBSERVATION_DUPLICATE)
        )
    )
    assert report["archive_assessment_state"] == idx.ASSESSMENT_WARNINGS


def test_rejected_record_duplicate_uses_observed_byte_identity_only(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    _ingest_payload(root, b"{ not valid json ")
    original = _record_path(root, c.PARTITION_REJECTED)
    _renamed_copy(original)

    report = _index(root)

    exact = _groups(report, idx.CATEGORY_EXACT_RECORD_DUPLICATE)
    assert len(exact) == 1
    assert exact[0]["key_domain"] == "observed_file_sha256"
    assert len(exact[0]["members"]) == 2
    # Rejected reason records never contribute observation identities.
    assert _groups(report, idx.CATEGORY_PAYLOAD_DUPLICATE) == []
    assert _groups(report, idx.CATEGORY_PHYSICAL_OBSERVATION_DUPLICATE) == []
    for entry in report["record_entries"]:
        assert entry["self_integrity_status"] == rv.SELF_INTEGRITY_NOT_AVAILABLE_IN_SCHEMA
    assert report["archive_assessment_state"] == idx.ASSESSMENT_WARNINGS


def test_observation_id_conflict_is_integrity_failure(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    variant = _obs()
    variant["guard_summaries"]["evidence_packet"]["differences_count"] = 99
    other_root = tmp_path / "other-archive"
    _ingest_payload(other_root, variant)
    conflicting = _record_path(other_root)
    (root / c.PARTITION_ACCEPTED / conflicting.name).write_bytes(conflicting.read_bytes())

    report = _index(root)

    conflicts = _groups(report, idx.CATEGORY_OBSERVATION_ID_CONFLICT)
    assert len(conflicts) == 1
    assert conflicts[0]["severity"] == idx.SEVERITY_INTEGRITY_FAILURE
    assert len(conflicts[0]["distinct_payload_sha256"]) == 2
    assert len(conflicts[0]["members"]) == 2
    assert report["archive_assessment_state"] == idx.ASSESSMENT_INTEGRITY_FAILURES
    assert (
        idx.TOKEN_OBSERVATION_ID_CONFLICT
        in report["assessment_reason_tokens"]["integrity_failures"]
    )
    # Both records remain fully referenced - nothing collapsed or deleted.
    assert _groups(report, idx.CATEGORY_PAYLOAD_DUPLICATE) == []


def test_misplaced_valid_record_is_partition_conflict(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    original = _record_path(root)
    (root / c.PARTITION_QUARANTINED / original.name).write_bytes(original.read_bytes())

    report = _index(root)

    conflicts = _groups(report, idx.CATEGORY_PARTITION_CONFLICT)
    assert len(conflicts) == 1
    assert conflicts[0]["key"] == {
        "physical_partition": c.PARTITION_QUARANTINED,
        "ingestion_decision": c.DECISION_ACCEPTED,
    }
    assert conflicts[0]["members"] == [f"quarantined/{original.name}"]
    assert report["archive_assessment_state"] == idx.ASSESSMENT_INTEGRITY_FAILURES
    # Cross-partition byte-identical copy also groups as duplicates.
    assert len(_groups(report, idx.CATEGORY_EXACT_RECORD_DUPLICATE)) == 1
    misplaced = [
        e for e in report["record_entries"] if e["entry"].startswith("quarantined/")
    ]
    assert misplaced[0]["content_status"] == rv.CONTENT_VALID
    assert misplaced[0]["identity_facts_valid"] is True
    assert misplaced[0]["placement_status"] == rv.PLACEMENT_PARTITION_MISMATCH


def test_incompatible_decisions_for_one_identity_are_partition_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    _ingest_payload(root, _obs(generated_at="2026-07-10T13:00:00+00:00"))
    decisions = iter((c.DECISION_ACCEPTED, c.DECISION_QUARANTINED))

    def fake_verify(record_bytes: bytes, *, filename: Any, expected_partition: Any):
        decision = next(decisions)
        return rv.RecordVerificationResult(
            record_kind=rv.RECORD_KIND_ACCEPTED
            if decision == c.DECISION_ACCEPTED
            else rv.RECORD_KIND_QUARANTINED,
            content_status=rv.CONTENT_VALID,
            placement_status=rv.PLACEMENT_CORRECT,
            verification_state="valid_accepted_record",
            identity_facts_valid=True,
            self_integrity_status=rv.SELF_INTEGRITY_VERIFIED,
            integrity_findings=(),
            compatibility_findings=(),
            placement_findings=(),
            informational_findings=(),
            archive_record_content_sha256=("c" if decision == c.DECISION_ACCEPTED else "d") * 64,
            source_canonical_payload_sha256="b" * 64,
            observation_id="a" * 64,
            coverage_key="e" * 64,
            source_git_commit=None,
            ingestion_decision=decision,
            claimed_evidence_provenance=c.PROVENANCE_UNSPECIFIED,
        )

    monkeypatch.setattr(idx.rv, "verify_archive_record", fake_verify)
    report = _index(root)

    conflicts = _groups(report, idx.CATEGORY_PARTITION_CONFLICT)
    key_domains = sorted(g["key_domain"] for g in conflicts)
    assert key_domains == [
        "incompatible_decisions_by_observation_id",
        "incompatible_decisions_by_source_canonical_payload_sha256",
    ]
    assert all(g["distinct_decisions"] == ["accepted", "quarantined"] for g in conflicts)
    assert report["archive_assessment_state"] == idx.ASSESSMENT_INTEGRITY_FAILURES


def test_logical_coverage_repeat_is_informational_only(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    _ingest_payload(root, _obs(generated_at="2026-07-10T13:00:00+00:00"))

    report = _index(root)

    repeats = _groups(report, idx.CATEGORY_LOGICAL_COVERAGE_REPEAT)
    assert len(repeats) == 1
    assert repeats[0]["severity"] == idx.SEVERITY_INFORMATIONAL
    assert repeats[0]["blocks_archive_clean"] is False
    assert len(repeats[0]["distinct_observation_ids"]) == 2
    assert len(repeats[0]["key"]["coverage_key"]) == 64
    # Informational only: the archive is still clean.
    assert report["archive_assessment_state"] == idx.ASSESSMENT_CLEAN
    assert (
        idx.TOKEN_LOGICAL_COVERAGE_REPEAT
        in report["assessment_reason_tokens"]["informational"]
    )


def test_corrupt_records_never_contribute_trusted_duplicate_keys(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    original = _record_path(root)
    record = json.loads(original.read_text(encoding="utf-8"))
    record["observation_payload"]["guard_summaries"]["evidence_packet"]["differences_count"] = 7
    corrupt_name = "1" + original.name
    (root / c.PARTITION_ACCEPTED / corrupt_name).write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = _index(root)

    corrupt_ref = f"accepted/{corrupt_name}"
    for group in report["duplicate_groups"]:
        assert corrupt_ref not in group["members"]
    assert report["archive_assessment_state"] == idx.ASSESSMENT_INTEGRITY_FAILURES
    assert idx.TOKEN_RECORD_CORRUPT in report["assessment_reason_tokens"]["integrity_failures"]
    corrupt_entry = [e for e in report["record_entries"] if e["entry"] == corrupt_ref][0]
    assert corrupt_entry["identity_facts_valid"] is False
    assert corrupt_entry["observation_id"] is None
    assert corrupt_entry["observed_contract_partition_id"] is None


# --- provisional compatibility partitions ----------------------------------------------
def test_provisional_partitions_are_inventory_labels_only(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    _ingest_payload(
        root,
        _obs(
            code_identity={
                "git_commit": "1" * 40,
                "git_state": "dirty",
                "code_version_usable_for_evidence": False,
            }
        ),
    )
    _ingest_payload(root, _minimal_incomplete_observation("2026-07-10T12:00:00+00:00"))

    report = _index(root)

    partitions = report["provisional_contract_partitions"]
    assert len(partitions) == 3
    assert report["archive_assessment_state"] == idx.ASSESSMENT_CLEAN
    assert (
        idx.TOKEN_MULTIPLE_CONTRACT_PARTITIONS
        in report["assessment_reason_tokens"]["informational"]
    )
    schemas = sorted(p["material"]["material_schema"] for p in partitions)
    assert schemas == [
        idx.MINIMAL_MATERIAL_SCHEMA,
        idx.OBSERVED_CONTRACT_PARTITION_SCHEMA,
        idx.OBSERVED_CONTRACT_PARTITION_SCHEMA,
    ]
    for partition in partitions:
        material = partition["material"]
        assert material["observed_source_schema_version"] == p1a.SCHEMA_VERSION
        assert (
            material["verifier_recognized_classification_contract_version"]
            == p1a.CLASSIFICATION_CONTRACT_VERSION
        )
        assert partition["observed_contract_partition_id"] == p1a.canonical_sha256(material)
        assert partition["member_count"] == len(partition["members"])
    full = [
        p["material"]
        for p in partitions
        if p["material"]["material_schema"] == idx.OBSERVED_CONTRACT_PARTITION_SCHEMA
    ]
    states = sorted(m["observed_code_identity_state"] for m in full)
    assert states == ["clean", "dirty"]
    clean_material = [m for m in full if m["observed_code_identity_state"] == "clean"][0]
    dirty_material = [m for m in full if m["observed_code_identity_state"] == "dirty"][0]
    assert clean_material["observed_clean_source_git_commit"] == "1" * 40
    assert dirty_material["observed_clean_source_git_commit"] is None


def test_unknown_record_schema_prevents_full_verification(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    original = _record_path(root)
    record = json.loads(original.read_text(encoding="utf-8"))
    record["archive_record_schema_version"] = "other_archive_schema_v9"
    _rehash_record(record)
    original.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = _index(root)

    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert (
        idx.TOKEN_RECORD_SCHEMA_UNRECOGNIZED
        in report["assessment_reason_tokens"]["unverifiable"]
    )


def test_unread_nonconforming_candidate_is_in_manifest_and_state_counts(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    original = _record_path(root)
    limited = original.with_name("limited_record.txt")
    original.rename(limited)
    layout_bytes = len((root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).read_bytes())

    report = _index(
        root,
        scan.ScanLimits(max_total_read_bytes=layout_bytes + 1),
    )

    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert report["source_record_count"] == 1
    assert report["unread_record_count"] == 1
    assert report["counts_by_partition"] == {"accepted": 1, "quarantined": 0, "rejected": 0}
    assert report["counts_by_verification_state"] == {
        scan.READ_SKIPPED_TOTAL_LIMIT: 1
    }
    assert report["source_set_manifest"] == [
        {
            "entry": "accepted/limited_record.txt",
            "stable_read_state": scan.READ_SKIPPED_TOTAL_LIMIT,
            "final_revalidation_state": None,
            "verification_state": None,
            "observed_file_sha256": None,
            "observed_byte_length": None,
        }
    ]
    assert report["indexed_source_set_sha256"] == p1a.canonical_sha256(
        report["source_set_manifest"]
    )


def test_manifest_and_counts_reconcile_across_mixed_terminal_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    stable_foreign = root / c.PARTITION_ACCEPTED / ".stable_foreign"
    stable_foreign.write_bytes(b"{}")
    vanished = root / c.PARTITION_QUARANTINED / "vanished_record.txt"
    vanished.write_bytes(b"x")
    real_lstat = os.lstat
    calls = {"vanished": 0}

    def disappear_before_classification(path: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(path) == os.fspath(vanished):
            calls["vanished"] += 1
            if calls["vanished"] == 1:
                vanished.unlink()
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", disappear_before_classification)
    report = _index(root, scan.ScanLimits(record_max_bytes=64))

    manifest = report["source_set_manifest"]
    assert report["source_record_count"] == len(manifest) == len(report["record_entries"])
    assert report["source_record_count"] == sum(report["counts_by_partition"].values())
    assert report["source_record_count"] == sum(
        report["counts_by_verification_state"].values()
    )
    assert report["source_record_count"] == sum(
        report["counts_by_stable_read_state"].values()
    )
    initially_stable = sum(
        entry["stable_read_state"] == scan.READ_STABLE for entry in manifest
    )
    assert initially_stable == sum(report["counts_by_final_revalidation_state"].values())
    assert report["unread_record_count"] == sum(
        entry["verification_state"] is None for entry in manifest
    )
    assert {entry["stable_read_state"] for entry in manifest} == {
        scan.READ_STABLE,
        scan.READ_RECORD_OVERSIZE,
        scan.READ_DISAPPEARED_BEFORE_CLASSIFICATION,
    }
    assert {
        entry["final_revalidation_state"]
        for entry in manifest
        if entry["stable_read_state"] == scan.READ_STABLE
    } == {scan.REVALIDATION_STABLE}
    assert report["indexed_source_set_sha256"] == p1a.canonical_sha256(manifest)
    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE


def test_changed_duplicate_is_excluded_from_trusted_groups_but_retained_in_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    original = _record_path(root)
    changed = root / c.PARTITION_ACCEPTED / "changed_duplicate.bak"
    changed.write_bytes(original.read_bytes())
    original_changed_bytes = changed.read_bytes()
    real_lstat = os.lstat
    calls = {"changed": 0}

    def replace_before_final_read(path: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(path) == os.fspath(changed):
            calls["changed"] += 1
            if calls["changed"] == 4:
                changed.write_bytes(b"x" * len(original_changed_bytes))
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", replace_before_final_read)
    report = _index(root)

    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert report["source_record_count"] == len(report["source_set_manifest"]) == 2
    assert report["source_record_count"] == len(report["record_entries"])
    assert report["unread_record_count"] == 1
    assert report["source_record_count"] == sum(report["counts_by_partition"].values())
    assert report["source_record_count"] == sum(
        report["counts_by_verification_state"].values()
    )
    assert report["source_record_count"] == sum(
        report["counts_by_stable_read_state"].values()
    )
    assert report["counts_by_final_revalidation_state"] == {
        scan.REVALIDATION_CHANGED: 1,
        scan.REVALIDATION_STABLE: 1,
    }
    changed_entry = [
        entry for entry in report["record_entries"] if entry["entry"].endswith("changed_duplicate.bak")
    ][0]
    assert changed_entry["stable_read_state"] == scan.READ_STABLE
    assert changed_entry["final_revalidation_state"] == scan.REVALIDATION_CHANGED
    assert changed_entry["identity_facts_valid"] is None
    assert changed_entry["observation_id"] is None
    assert changed_entry["observed_contract_partition_id"] is None
    assert report["duplicate_groups"] == []
    assert all(
        changed_entry["entry"] not in partition["members"]
        for partition in report["provisional_contract_partitions"]
    )
    assert report["indexed_source_set_sha256"] == p1a.canonical_sha256(
        report["source_set_manifest"]
    )


def test_direct_or_malformed_scanner_state_cannot_authorize_clean(
    tmp_path: Path,
) -> None:
    assert not hasattr(scan._ScanState(scan.ScanLimits(), object(), tmp_path), "finish")
    assert not hasattr(
        scan._ScanState(scan.ScanLimits(), object(), tmp_path),
        "finish_validated",
    )

    fabricated = scan.ArchiveScan(
        scanner_version=scan.SCANNER_VERSION,
        effective_limits=scan.ScanLimits(),
        layout_status=scan.LAYOUT_CANONICAL,
        archive_layout_version=c.ARCHIVE_LAYOUT_VERSION,
        entries=(),
        unverifiable_tokens=(),
        warning_tokens=(),
        direct_entry_count=0,
        total_bytes_read=0,
        entry_inventory_truncated=False,
        _construction_token=object(),
    )

    report = idx.build_archive_index(fabricated)

    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert report["repository_writer_quiescence_verified"] is False
    assert report["duplicate_groups"] == []
    assert report["provisional_contract_partitions"] == []
    assert coord.TOKEN_LEASE_INVALID in report["assessment_reason_tokens"]["unverifiable"]

    malformed_entry = scan.ScannedEntry(
        location=c.PARTITION_ACCEPTED,
        safe_name="bad.json",
        safe_relative_path="accepted/bad.json",
        entry_path_sha256="a" * 64,
        entry_kind=scan.ENTRY_RECORD_CANDIDATE,
        stable_read_state=None,
        file_sha256=None,
        byte_length=None,
        source_candidate=True,
    )
    malformed = replace(fabricated, entries=(malformed_entry,))

    malformed_report = idx.build_archive_index(malformed)

    assert malformed_report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert malformed_report["repository_writer_quiescence_verified"] is False
    assert malformed_report["source_record_count"] == 0


def test_copied_scan_with_duplicates_loses_trusted_group_facts(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    original = _record_path(root)
    _renamed_copy(original)
    anchor = _coordination_path(root)

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    ) as lease:
        scanned = scan.scan_archive(root, lease=lease)
        copied = replace(scanned, unverifiable_tokens=(), warning_tokens=())
        report = idx.build_archive_index(copied, lease=lease)

    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert report["repository_writer_quiescence_verified"] is False
    assert report["duplicate_groups"] == []
    assert report["provisional_contract_partitions"] == []


def test_unreadable_final_inventory_cannot_mark_canonical_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    anchor = _coordination_path(root)
    real_snapshot = scan._bounded_directory_snapshot
    root_calls = 0

    def fail_final_root_snapshot(path: Path, limit: int):
        nonlocal root_calls
        if Path(path) == root:
            root_calls += 1
            if root_calls == 2:
                return None
        return real_snapshot(path, limit)

    monkeypatch.setattr(scan, "_bounded_directory_snapshot", fail_final_root_snapshot)
    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    ) as lease:
        scanned = scan.scan_archive(root, lease=lease)
        completion = scan.validated_scan_completion(scanned, lease)
        report = idx.build_archive_index(scanned, lease=lease)

    assert completion.final_inventory_completed is False
    assert completion.all_required_phases_completed is False
    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert scan.TOKEN_ARCHIVE_CHANGED in report["assessment_reason_tokens"]["unverifiable"]


def test_clean_predicate_independently_rejects_every_finding_class(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    anchor = _coordination_path(root)
    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    ) as lease:
        scanned = scan.scan_archive(root, lease=lease)
        completion = scan.validated_scan_completion(scanned, lease)
        common = {
            "scan": scanned,
            "completion": completion,
            "coordination_verified": True,
            "candidate_count": 1,
            "verified_count": 1,
            "unread_count": 0,
            "reconciliation_ok": True,
            "counts_by_final_revalidation_state": {scan.REVALIDATION_STABLE: 1},
        }
        assert idx._canonical_clean_prerequisites(
            **common, unverifiable=set(), integrity=set(), warnings=set()
        )
        for field in ("unverifiable", "integrity", "warnings"):
            findings = {"unverifiable": set(), "integrity": set(), "warnings": set()}
            findings[field].add("finding")
            assert not idx._canonical_clean_prerequisites(**common, **findings)


# --- read-only proof ---------------------------------------------------------------
def test_indexing_never_mutates_the_archive(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    _ingest_payload(root, b"{ not valid json ")
    _renamed_copy(_record_path(root))  # duplicates exercise the full pipeline
    (root / c.PARTITION_ACCEPTED / ".hidden").write_text("x", encoding="utf-8")
    before = _tree_snapshot(root)

    report = _index(root)

    assert _tree_snapshot(root) == before
    assert report["archive_assessment_state"] == idx.ASSESSMENT_INTEGRITY_FAILURES
    # No lock, cache, temp, or index file appeared anywhere beneath the root.
    assert sorted(before) == sorted(_tree_snapshot(root))


# --- CLI -----------------------------------------------------------------------------
def test_cli_clean_archive_exits_zero_with_report_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _archive(tmp_path)

    code = _verify(["--archive-root", str(root)])

    out = capsys.readouterr().out
    report = json.loads(out)
    assert code == 0
    assert report["archive_assessment_state"] == idx.ASSESSMENT_CLEAN
    assert idx.compute_report_content_sha256(report) == report["report_content_sha256"]
    assert out.endswith("\n")


def test_cli_exit_codes_track_assessment_states(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    warnings_root = _archive(tmp_path, "warnings-archive")
    _renamed_copy(_record_path(warnings_root))
    integrity_root = _archive(tmp_path, "integrity-archive")
    original = _record_path(integrity_root)
    (integrity_root / c.PARTITION_QUARANTINED / original.name).write_bytes(original.read_bytes())

    assert _verify(["--archive-root", str(warnings_root)]) == 3
    assert _verify(["--archive-root", str(integrity_root)]) == 4
    assert _verify(["--archive-root", str(tmp_path / "missing")]) == 5
    capsys.readouterr()


def test_cli_output_file_outside_root_is_written_exactly(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    output = tmp_path / "reports" / "index.json"
    output.parent.mkdir()

    code = _verify(["--archive-root", str(root), "--output", str(output)])

    assert code == 0
    written = output.read_text(encoding="utf-8")
    assert written == idx.serialize_index_report(json.loads(written))


def test_cli_output_inside_archive_root_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _archive(tmp_path)
    before = _tree_snapshot(root)
    inside = root / c.PARTITION_ACCEPTED / "index.json"

    code = _verify(["--archive-root", str(root), "--output", str(inside)])

    err = json.loads(capsys.readouterr().err)
    assert code == 2
    assert err == {"error": "output_inside_archive_root"}
    assert not inside.exists()
    assert _tree_snapshot(root) == before


def test_cli_output_containment_uses_coordinated_scan_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = _archive(tmp_path, "first-archive")
    second_root = _archive(tmp_path, "second-archive")
    archive_link = tmp_path / "archive-link"
    archive_link.symlink_to(first_root, target_is_directory=True)
    output = second_root / c.PARTITION_ACCEPTED / "index.json"
    anchor = _coordination_path(second_root)
    real_operation = verify_cli.index_archive_operation

    def retarget_before_coordinated_scan(*args: Any, **kwargs: Any):
        archive_link.unlink()
        archive_link.symlink_to(second_root, target_is_directory=True)
        return real_operation(*args, **kwargs)

    monkeypatch.setattr(
        verify_cli,
        "index_archive_operation",
        retarget_before_coordinated_scan,
    )

    code = verify_cli.main(
        [
            "--archive-root",
            str(archive_link),
            "--coordination-file",
            str(anchor),
            "--output",
            str(output),
        ]
    )

    err = json.loads(capsys.readouterr().err)
    assert code == 2
    assert err == {"error": "output_inside_archive_root"}
    assert not output.exists()


@pytest.mark.parametrize(
    "kind",
    ("missing_parent", "parent_file", "output_directory", "broken_parent_symlink"),
)
def test_cli_output_path_failures_are_sanitized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    root = _archive(tmp_path)
    if kind == "missing_parent":
        output = tmp_path / "missing-parent" / "index.json"
    elif kind == "parent_file":
        parent = tmp_path / "not-a-directory"
        parent.write_text("x", encoding="utf-8")
        output = parent / "index.json"
    elif kind == "output_directory":
        output = tmp_path / "output-directory"
        output.mkdir()
    else:
        parent = tmp_path / "broken-parent"
        parent.symlink_to(tmp_path / "missing-target")
        output = parent / "index.json"

    code = _verify(["--archive-root", str(root), "--output", str(output)])

    captured = capsys.readouterr()
    assert code == 2
    assert json.loads(captured.err) == {"error": "output_write_failed"}
    assert captured.out == ""
    assert str(root) not in captured.err
    assert str(output) not in captured.err
    assert "FileNotFoundError" not in captured.err


@pytest.mark.parametrize("target_location", ("inside", "outside"))
def test_cli_dangling_final_output_symlink_fails_without_creating_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    target_location: str,
) -> None:
    root = _archive(tmp_path)
    target = (
        root / c.PARTITION_ACCEPTED / "missing-index.json"
        if target_location == "inside"
        else tmp_path / "missing-outside-index.json"
    )
    output = tmp_path / f"dangling-{target_location}"
    output.symlink_to(target)
    before = _tree_snapshot(root)

    code = _verify(["--archive-root", str(root), "--output", str(output)])

    captured = capsys.readouterr()
    assert code == 2
    assert json.loads(captured.err) == {"error": "output_write_failed"}
    assert captured.out == ""
    assert target.exists() is False
    assert output.is_symlink()
    assert _tree_snapshot(root) == before
    assert str(root) not in captured.err
    assert str(target) not in captured.err


def test_cli_existing_valid_output_symlink_keeps_current_policy(
    tmp_path: Path,
) -> None:
    root = _archive(tmp_path)
    target = tmp_path / "existing-report.json"
    target.write_text("old", encoding="utf-8")
    output = tmp_path / "report-link"
    output.symlink_to(target)

    code = _verify(["--archive-root", str(root), "--output", str(output)])

    assert code == 0
    assert output.is_symlink()
    assert json.loads(target.read_text(encoding="utf-8"))["archive_assessment_state"] == (
        idx.ASSESSMENT_CLEAN
    )


def test_cli_existing_symlink_parent_allows_ordinary_new_output(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    output = linked_parent / "new-report.json"

    code = _verify(["--archive-root", str(root), "--output", str(output)])

    assert code == 0
    assert output.resolve() == real_parent / "new-report.json"
    assert json.loads(output.read_text(encoding="utf-8"))["archive_assessment_state"] == (
        idx.ASSESSMENT_CLEAN
    )


def test_cli_write_exception_is_sanitized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _archive(tmp_path)
    output = tmp_path / "outside.json"

    def fail_write(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("output_write_failure_sentinel")

    monkeypatch.setattr(verify_cli.Path, "write_text", fail_write)
    code = _verify(["--archive-root", str(root), "--output", str(output)])

    captured = capsys.readouterr()
    assert code == 2
    assert json.loads(captured.err) == {"error": "output_write_failed"}
    assert captured.out == ""
    assert "output_write_failure_sentinel" not in captured.err
    assert str(root) not in captured.err


def test_cli_stdout_write_exception_is_sanitized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _archive(tmp_path)

    class _FailingStdout:
        def write(self, _text: str) -> None:
            raise UnicodeError("stdout_write_failure_sentinel")

    monkeypatch.setattr(verify_cli.sys, "stdout", _FailingStdout())
    code = _verify(["--archive-root", str(root)])

    captured = capsys.readouterr()
    assert code == 2
    assert json.loads(captured.err) == {"error": "output_write_failed"}
    assert "stdout_write_failure_sentinel" not in captured.err
    assert str(root) not in captured.err


def test_cli_output_resolve_exception_is_sanitized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _archive(tmp_path)

    def fail_resolve(*_args: Any, **_kwargs: Any) -> Path:
        raise idx.ArchiveOutputError("output_write_failed")

    monkeypatch.setattr(idx, "_resolve_output_path_outside_archive", fail_resolve)
    code = _verify(
        ["--archive-root", str(root), "--output", str(tmp_path / "outside.json")]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert json.loads(captured.err) == {"error": "output_write_failed"}
    assert captured.out == ""
    assert "resolve_failure_sentinel" not in captured.err
    assert str(root) not in captured.err


def test_cli_rejects_limits_above_code_owned_maxima(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _archive(tmp_path)

    code = _verify(
        ["--archive-root", str(root), "--max-record-bytes", str(scan.RECORD_MAX_BYTES + 1)]
    )

    err = json.loads(capsys.readouterr().err)
    assert code == 2
    assert err["error"] == "invalid_scan_limit"
    assert err["token"] == "scan_limit_above_maximum:record_max_bytes"


def test_cli_lowered_limit_is_effective_and_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _archive(tmp_path)

    code = _verify(["--archive-root", str(root), "--max-record-bytes", "64"])

    report = json.loads(capsys.readouterr().out)
    assert code == 5
    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert report["verification_limits"]["record_max_bytes"] == 64
    assert scan.TOKEN_RECORD_OVERSIZE in report["assessment_reason_tokens"]["unverifiable"]


# --- consumer / runtime isolation ------------------------------------------------------
def test_no_production_module_references_the_archive_index(tmp_path: Path) -> None:
    package_root = Path(step1_research.__file__).resolve().parents[1]
    offline_root = package_root / "offline"
    needles = (
        "retirement_archive_index_v1",
        "archive_scan",
        "archive_index",
        "verify_cli",
        "verify-retirement-archive",
    )
    for path in sorted(package_root.rglob("*.py")):
        if offline_root in path.parents:
            continue
        source = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in source, (str(path), needle)


def test_step1_research_never_references_archive_or_index_paths() -> None:
    source = Path(step1_research.__file__).read_text(encoding="utf-8")
    assert "retirement_archive" not in source
    assert "archive_index" not in source


def test_phase2b2_modules_never_import_runtime_workflow() -> None:
    for module in (scan, idx, verify_cli):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "investment_orchestrator.workflow" not in source
        assert "import workflow" not in source


def test_record_verifier_remains_filesystem_free() -> None:
    source = Path(rv.__file__).read_text(encoding="utf-8")
    assert "archive_scan" not in source
    assert "os." not in source.replace("os.path", "")  # no os usage gained
    assert "open(" not in source
