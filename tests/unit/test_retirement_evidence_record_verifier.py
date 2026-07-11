"""Phase 2B-1 tests for the pure single-record archive verifier."""

from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence import archive_coordination as coord
from investment_orchestrator.offline.retirement_evidence import archive_record_contract as rc
from investment_orchestrator.offline.retirement_evidence import record_verifier as verifier
from investment_orchestrator.offline.retirement_evidence.ingest import ingest_observation
from investment_orchestrator.research.step1a_retirement_observation import (
    _minimal_incomplete_observation,
    build_step1a_retirement_observation,
    canonical_sha256,
)

from test_step1a_retirement_observation import _builder_inputs


_TOOL = {"tool_version": c.ARCHIVE_TOOL_VERSION, "tool_commit": "unavailable"}
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


def _ingest(source: Path, root: Path):
    anchor = root.parent / "retirement-archive-coordination.anchor"
    if not anchor.exists():
        anchor.write_bytes(coord.COORDINATION_ANCHOR_BYTES)
    return ingest_observation(
        source_path=source,
        dest_root=root,
        coordination_path=anchor,
        tool_identity=_TOOL,
        archived_at=_STAMP,
    )


def _record_bytes_and_name(result: Any) -> tuple[bytes, str]:
    path = Path(result.archived_path)
    return path.read_bytes(), path.name


def _record(result: Any) -> dict[str, Any]:
    return json.loads(Path(result.archived_path).read_text(encoding="utf-8"))


def _bytes(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _rehash_record(record: dict[str, Any]) -> None:
    body = {key: value for key, value in record.items() if key != "archive_record_content_sha256"}
    digest = canonical_sha256(body)
    assert digest is not None
    record["archive_record_content_sha256"] = digest


def _rehash_payload_and_record(record: dict[str, Any]) -> None:
    digest = canonical_sha256(record["observation_payload"])
    assert digest is not None
    record["source_canonical_payload_sha256"] = digest
    _rehash_record(record)


def _all_findings(result: verifier.RecordVerificationResult) -> set[str]:
    return set(result.integrity_findings) | set(result.compatibility_findings) | set(result.placement_findings)


def _verify_observation_result(tmp_path: Path) -> tuple[Any, bytes, str]:
    result = _ingest(_write_source(tmp_path / "source", _obs()), tmp_path / "archive")
    record_bytes, filename = _record_bytes_and_name(result)
    return result, record_bytes, filename


def test_valid_accepted_record_returns_only_safe_verification_facts(tmp_path: Path) -> None:
    result, record_bytes, filename = _verify_observation_result(tmp_path)

    verified = verifier.verify_archive_record(
        record_bytes, filename=filename, expected_partition=c.PARTITION_ACCEPTED
    )

    assert verified.record_kind == verifier.RECORD_KIND_ACCEPTED
    assert verified.content_status == verifier.CONTENT_VALID
    assert verified.placement_status == verifier.PLACEMENT_CORRECT
    assert verified.verification_state == "valid_accepted_record"
    assert verified.identity_facts_valid is True
    assert verified.self_integrity_status == verifier.SELF_INTEGRITY_VERIFIED
    assert verified.integrity_findings == ()
    assert verified.archive_record_content_sha256 == result.record_content_sha256
    assert verified.source_canonical_payload_sha256 == result.source_canonical_payload_sha256
    assert verified.observation_id == _obs()["observation_identity"]["observation_id"]
    assert "observation_payload" not in asdict(verified)
    assert "source_basename" not in asdict(verified)


@pytest.mark.parametrize("kind", ("full", "minimal"))
def test_valid_quarantined_records_preserve_content_validity(
    tmp_path: Path, kind: str
) -> None:
    if kind == "full":
        packet = deepcopy(_builder_inputs()["evidence_packet"])
        packet.pop("strategy_settings_hash")
        payload = _obs(evidence_packet=packet)
    else:
        payload = _minimal_incomplete_observation("2026-07-10T12:00:00+00:00")
    result = _ingest(_write_source(tmp_path / "source", payload), tmp_path / "archive")
    record_bytes, filename = _record_bytes_and_name(result)

    verified = verifier.verify_archive_record(
        record_bytes, filename=filename, expected_partition=c.PARTITION_QUARANTINED
    )

    assert verified.record_kind == verifier.RECORD_KIND_QUARANTINED
    assert verified.content_status == verifier.CONTENT_VALID
    assert verified.placement_status == verifier.PLACEMENT_CORRECT
    assert verified.verification_state == "valid_quarantined_record"
    assert verified.identity_facts_valid is True
    assert verified.self_integrity_status == verifier.SELF_INTEGRITY_VERIFIED


def test_valid_rejected_record_has_no_claimed_embedded_self_integrity(tmp_path: Path) -> None:
    source = tmp_path / "source" / "invalid.json"
    source.parent.mkdir(parents=True)
    source.write_text("{ this is not valid json ", encoding="utf-8")
    result = _ingest(source, tmp_path / "archive")
    record_bytes, filename = _record_bytes_and_name(result)

    verified = verifier.verify_archive_record(
        record_bytes, filename=filename, expected_partition=c.PARTITION_REJECTED
    )

    assert verified.record_kind == verifier.RECORD_KIND_REJECTED
    assert verified.content_status == verifier.CONTENT_VALID
    assert verified.verification_state == "valid_rejected_reason_record"
    assert verified.self_integrity_status == verifier.SELF_INTEGRITY_NOT_AVAILABLE_IN_SCHEMA
    assert verified.identity_facts_valid is False
    assert verified.archive_record_content_sha256 is None
    assert verified.source_canonical_payload_sha256 is None


@pytest.mark.parametrize(
    "mutate",
    (
        lambda payload: payload["configuration_hashes"].__setitem__("strategy_settings_hash", "bad"),
        lambda payload: payload["code_identity"].__setitem__("unexpected_key", True),
    ),
)
def test_rejected_record_accepts_exact_phase2a_parameterized_reason_domains(
    tmp_path: Path, mutate: Any
) -> None:
    payload = _obs()
    mutate(payload)
    result = _ingest(_write_source(tmp_path / "source", payload), tmp_path / "archive")
    record_bytes, filename = _record_bytes_and_name(result)

    verified = verifier.verify_archive_record(
        record_bytes, filename=filename, expected_partition=c.PARTITION_REJECTED
    )

    assert result.decision == c.DECISION_REJECTED
    assert verified.verification_state == "valid_rejected_reason_record"


@pytest.mark.parametrize(
    "record_bytes, expected_finding",
    (
        (b"{\"x\": 1, \"x\": 2}", verifier.FINDING_RECORD_JSON_DUPLICATE_KEY),
        (b"{\"nested\": {\"x\": 1, \"x\": 2}}", verifier.FINDING_RECORD_JSON_DUPLICATE_KEY),
        (b"{\"x\": NaN}", verifier.FINDING_RECORD_JSON_NONSTANDARD_CONSTANT),
        (b"{\"x\": Infinity}", verifier.FINDING_RECORD_JSON_NONSTANDARD_CONSTANT),
        (b"{\"x\": -Infinity}", verifier.FINDING_RECORD_JSON_NONSTANDARD_CONSTANT),
        (b"\xff", verifier.FINDING_RECORD_INVALID_UTF8),
        (b"{ not valid", verifier.FINDING_RECORD_JSON_MALFORMED),
        (b"[]", verifier.FINDING_RECORD_TOP_LEVEL_NOT_MAPPING),
    ),
)
def test_strict_json_decoder_rejects_extensions_without_raw_errors(
    record_bytes: bytes, expected_finding: str
) -> None:
    verified = verifier.verify_archive_record(
        record_bytes, filename="record.json", expected_partition=c.PARTITION_ACCEPTED
    )

    assert verified.content_status == verifier.CONTENT_CORRUPT
    assert expected_finding in _all_findings(verified)
    assert "Expecting" not in json.dumps(asdict(verified))


@pytest.mark.parametrize(
    "name, mutate, expected_finding, placement_status",
    (
        (
            "layout",
            lambda r: (r.__setitem__("archive_layout_version", "other_layout_v9"), _rehash_record(r)),
            verifier.FINDING_ARCHIVE_LAYOUT_VERSION_INVALID,
            None,
        ),
        (
            "tool_version",
            lambda r: (r.__setitem__("archive_tool_version", "other_tool_v9"), _rehash_record(r)),
            verifier.FINDING_ARCHIVE_TOOL_VERSION_INVALID,
            None,
        ),
        (
            "tool_commit",
            lambda r: (r.__setitem__("archive_tool_commit", "not_a_commit"), _rehash_record(r)),
            verifier.FINDING_ARCHIVE_TOOL_COMMIT_INVALID,
            None,
        ),
        (
            "timestamp",
            lambda r: (r.__setitem__("archived_at", "not-a-timestamp"), _rehash_record(r)),
            verifier.FINDING_ARCHIVED_AT_INVALID,
            None,
        ),
        (
            "decision",
            lambda r: (r.__setitem__("ingestion_decision", c.DECISION_REJECTED), _rehash_record(r)),
            verifier.FINDING_INGESTION_DECISION_INVALID,
            None,
        ),
        (
            "reason_tokens",
            lambda r: (r.__setitem__("ingestion_reason_tokens", ["not_a_phase2a_token"]), _rehash_record(r)),
            verifier.FINDING_INGESTION_REASON_TOKENS_INVALID,
            None,
        ),
        (
            "provenance_token",
            lambda r: (r.__setitem__("claimed_evidence_provenance", "not_provenance"), _rehash_record(r)),
            verifier.FINDING_PROVENANCE_TOKEN_INVALID,
            None,
        ),
        (
            "provenance_token_nonstring",
            lambda r: (r.__setitem__("claimed_evidence_provenance", ["not_provenance"]), _rehash_record(r)),
            verifier.FINDING_PROVENANCE_TOKEN_INVALID,
            None,
        ),
        (
            "claim_source",
            lambda r: (r.__setitem__("provenance_claim_source", "not_claim_source"), _rehash_record(r)),
            verifier.FINDING_PROVENANCE_CLAIM_SOURCE_INVALID,
            None,
        ),
        (
            "provenance_verified",
            lambda r: (r.__setitem__("provenance_verified", True), _rehash_record(r)),
            verifier.FINDING_PROVENANCE_VERIFIED_INVALID,
            None,
        ),
        (
            "source_metadata",
            lambda r: (r["source_metadata"].__setitem__("source_basename", ""), _rehash_record(r)),
            verifier.FINDING_SOURCE_METADATA_INVALID,
            None,
        ),
        (
            "recomputed_identity",
            lambda r: (r["recomputed_identity"].__setitem__("observation_id", "b" * 64), _rehash_record(r)),
            verifier.FINDING_RECOMPUTED_IDENTITY_MISMATCH,
            None,
        ),
        (
            "self_hash",
            lambda r: r.__setitem__("archive_record_content_sha256", "b" * 64),
            verifier.FINDING_ARCHIVE_RECORD_CONTENT_HASH_MISMATCH,
            None,
        ),
        (
            "canonical_payload_hash",
            lambda r: (r.__setitem__("source_canonical_payload_sha256", "b" * 64), _rehash_record(r)),
            verifier.FINDING_SOURCE_CANONICAL_PAYLOAD_HASH_MISMATCH,
            None,
        ),
        (
            "payload",
            lambda r: r["observation_payload"]["guard_summaries"]["evidence_packet"].__setitem__(
                "differences_count", 1
            ),
            verifier.FINDING_ARCHIVE_RECORD_CONTENT_HASH_MISMATCH,
            None,
        ),
        (
            "payload_observation_id",
            lambda r: (
                r["observation_payload"]["observation_identity"].__setitem__("observation_id", "b" * 64),
                _rehash_payload_and_record(r),
            ),
            verifier.FINDING_SOURCE_PAYLOAD_CONTRACT_INVALID,
            None,
        ),
        (
            "payload_coverage_key",
            lambda r: (
                r["observation_payload"]["coverage_identity"].__setitem__("coverage_key", "b" * 64),
                _rehash_payload_and_record(r),
            ),
            verifier.FINDING_SOURCE_PAYLOAD_CONTRACT_INVALID,
            None,
        ),
        (
            "payload_composite_fingerprint",
            lambda r: (
                r["observation_payload"]["coverage_identity"].__setitem__(
                    "composite_config_fingerprint", "b" * 64
                ),
                _rehash_payload_and_record(r),
            ),
            verifier.FINDING_SOURCE_PAYLOAD_CONTRACT_INVALID,
            None,
        ),
        (
            "source_git_commit",
            lambda r: (r["source_metadata"].__setitem__("source_git_commit", "b" * 40), _rehash_record(r)),
            verifier.FINDING_SOURCE_METADATA_PAYLOAD_MISMATCH,
            None,
        ),
    ),
)
def test_observation_record_tampering_is_structured_and_raw_content_free(
    tmp_path: Path,
    name: str,
    mutate: Any,
    expected_finding: str,
    placement_status: str | None,
) -> None:
    result, _record_bytes, filename = _verify_observation_result(tmp_path)
    record = _record(result)
    record["attacker_controlled_value"] = "not used"
    record.pop("attacker_controlled_value")
    mutate(record)

    verified = verifier.verify_archive_record(
        _bytes(record), filename=filename, expected_partition=c.PARTITION_ACCEPTED
    )

    assert expected_finding in _all_findings(verified), name
    assert verified.content_status == verifier.CONTENT_CORRUPT, name
    assert verified.identity_facts_valid is False, name
    assert placement_status is None, name
    serialized = json.dumps(asdict(verified), ensure_ascii=False)
    assert "attacker_controlled_value" not in serialized
    assert "not used" not in serialized


def test_unknown_observation_record_schema_is_compatible_failure_not_payload_fact(
    tmp_path: Path,
) -> None:
    result, _record_bytes, filename = _verify_observation_result(tmp_path)
    record = _record(result)
    record["archive_record_schema_version"] = "other_archive_schema_v9"
    _rehash_record(record)

    verified = verifier.verify_archive_record(
        _bytes(record), filename=filename, expected_partition=c.PARTITION_ACCEPTED
    )

    assert verified.content_status == verifier.CONTENT_SCHEMA_INCOMPATIBLE
    assert verifier.FINDING_RECORD_SCHEMA_UNRECOGNIZED in verified.compatibility_findings
    assert verified.identity_facts_valid is False


def test_observation_envelope_and_payload_key_injection_are_not_trusted(tmp_path: Path) -> None:
    result, _record_bytes, filename = _verify_observation_result(tmp_path)
    envelope = _record(result)
    envelope["SENTINEL_envelope_key"] = "SENTINEL_envelope_value"
    injected_envelope = verifier.verify_archive_record(
        _bytes(envelope), filename=filename, expected_partition=c.PARTITION_ACCEPTED
    )

    payload = _record(result)
    payload["observation_payload"]["SENTINEL_payload_key"] = "SENTINEL_payload_value"
    _rehash_payload_and_record(payload)
    injected_payload = verifier.verify_archive_record(
        _bytes(payload), filename=filename, expected_partition=c.PARTITION_ACCEPTED
    )

    assert verifier.FINDING_OBSERVATION_RECORD_KEY_SET_INVALID in _all_findings(injected_envelope)
    assert verifier.FINDING_SOURCE_PAYLOAD_CONTRACT_INVALID in _all_findings(injected_payload)
    assert "SENTINEL" not in json.dumps(asdict(injected_envelope))
    assert "SENTINEL" not in json.dumps(asdict(injected_payload))


def test_canonical_but_wrong_quarantine_reason_tokens_are_not_trusted(tmp_path: Path) -> None:
    packet = deepcopy(_builder_inputs()["evidence_packet"])
    packet.pop("strategy_settings_hash")
    result = _ingest(
        _write_source(tmp_path / "source", _obs(evidence_packet=packet)), tmp_path / "archive"
    )
    record = _record(result)
    record["ingestion_reason_tokens"] = [c.REASON_CODE_IDENTITY_DIRTY]
    _rehash_record(record)

    verified = verifier.verify_archive_record(
        _bytes(record),
        filename=Path(result.archived_path).name,
        expected_partition=c.PARTITION_QUARANTINED,
    )

    assert verifier.FINDING_INGESTION_REASON_TOKENS_MISMATCH in _all_findings(verified)


@pytest.mark.parametrize(
    "metadata_field, replacement",
    (
        ("source_observation_id", "b" * 64),
        ("source_coverage_key", "b" * 64),
        ("source_schema_version", "other_source_schema_v9"),
        ("source_git_commit", "b" * 40),
    ),
)
def test_each_source_metadata_identity_field_must_match_payload(
    tmp_path: Path, metadata_field: str, replacement: str
) -> None:
    result, _record_bytes, filename = _verify_observation_result(tmp_path)
    record = _record(result)
    record["source_metadata"][metadata_field] = replacement
    _rehash_record(record)

    verified = verifier.verify_archive_record(
        _bytes(record), filename=filename, expected_partition=c.PARTITION_ACCEPTED
    )

    assert verified.content_status == verifier.CONTENT_CORRUPT
    assert verifier.FINDING_SOURCE_METADATA_PAYLOAD_MISMATCH in _all_findings(verified)


@pytest.mark.parametrize(
    "identity_field",
    ("observation_id", "coverage_key", "composite_config_fingerprint"),
)
def test_each_recomputed_identity_field_must_match_classification(
    tmp_path: Path, identity_field: str
) -> None:
    result, _record_bytes, filename = _verify_observation_result(tmp_path)
    record = _record(result)
    record["recomputed_identity"][identity_field] = "b" * 64
    _rehash_record(record)

    verified = verifier.verify_archive_record(
        _bytes(record), filename=filename, expected_partition=c.PARTITION_ACCEPTED
    )

    assert verified.content_status == verifier.CONTENT_CORRUPT
    assert verifier.FINDING_RECOMPUTED_IDENTITY_MISMATCH in _all_findings(verified)


def test_partition_and_filename_mismatches_preserve_valid_identity_facts(tmp_path: Path) -> None:
    _result, record_bytes, filename = _verify_observation_result(tmp_path)

    partition = verifier.verify_archive_record(
        record_bytes, filename=filename, expected_partition=c.PARTITION_QUARANTINED
    )
    filename_mismatch = verifier.verify_archive_record(
        record_bytes, filename="other_record.json", expected_partition=c.PARTITION_ACCEPTED
    )

    assert partition.content_status == verifier.CONTENT_VALID
    assert partition.placement_status == verifier.PLACEMENT_PARTITION_MISMATCH
    assert partition.identity_facts_valid is True
    assert verifier.FINDING_RECORD_PARTITION_MISMATCH in partition.placement_findings
    assert filename_mismatch.content_status == verifier.CONTENT_VALID
    assert filename_mismatch.placement_status == verifier.PLACEMENT_FILENAME_MISMATCH
    assert filename_mismatch.identity_facts_valid is True
    assert verifier.FINDING_RECORD_FILENAME_MISMATCH in filename_mismatch.placement_findings


def test_unsafe_entry_metadata_does_not_hide_valid_content_facts(tmp_path: Path) -> None:
    _result, record_bytes, _filename = _verify_observation_result(tmp_path)

    verified = verifier.verify_archive_record(
        record_bytes, filename="unsafe/path.json", expected_partition=c.PARTITION_ACCEPTED
    )

    assert verified.content_status == verifier.CONTENT_VALID
    assert verified.placement_status == verifier.PLACEMENT_UNSAFE_ENTRY_METADATA
    assert verified.identity_facts_valid is True
    assert verifier.FINDING_ENTRY_FILENAME_UNSAFE in verified.placement_findings


@pytest.mark.parametrize(
    "mutate, expected_finding",
    (
        (lambda r: r.__setitem__("unexpected", "SENTINEL_raw_key"), verifier.FINDING_REJECTED_RECORD_KEY_SET_INVALID),
        (lambda r: r.__setitem__("observation_payload", {"raw": "SENTINEL_payload"}), verifier.FINDING_REJECTED_RECORD_KEY_SET_INVALID),
        (lambda r: r.__setitem__("claimed_evidence_provenance", c.PROVENANCE_REAL_CURRENT), verifier.FINDING_REJECTED_RECORD_KEY_SET_INVALID),
        (lambda r: r.__setitem__("retirement_ready", True), verifier.FINDING_REJECTED_RECORD_KEY_SET_INVALID),
        (lambda r: r.__setitem__("ingestion_reason_tokens", ["not_a_phase2a_token"]), verifier.FINDING_INGESTION_REASON_TOKENS_INVALID),
        (lambda r: r.__setitem__("ingestion_reason_tokens", [c.REASON_SOURCE_NOT_VALID_JSON, c.REASON_SOURCE_NOT_VALID_JSON]), verifier.FINDING_INGESTION_REASON_TOKENS_INVALID),
        (lambda r: r.__setitem__("source_file_sha256", "bad"), verifier.FINDING_SOURCE_FILE_SHA256_INVALID),
        (lambda r: r.__setitem__("archived_at", "not-a-timestamp"), verifier.FINDING_ARCHIVED_AT_INVALID),
    ),
)
def test_rejected_record_tampering_is_contained(
    tmp_path: Path, mutate: Any, expected_finding: str
) -> None:
    source = tmp_path / "source" / "invalid.json"
    source.parent.mkdir(parents=True)
    source.write_text("{ invalid json", encoding="utf-8")
    result = _ingest(source, tmp_path / "archive")
    record = _record(result)
    mutate(record)

    verified = verifier.verify_archive_record(
        _bytes(record), filename=Path(result.archived_path).name, expected_partition=c.PARTITION_REJECTED
    )

    assert verified.content_status == verifier.CONTENT_CORRUPT
    assert expected_finding in _all_findings(verified)
    serialized = json.dumps(asdict(verified), ensure_ascii=False)
    assert "SENTINEL" not in serialized
    assert "retirement_ready" not in serialized


def test_rejected_filename_mismatch_and_phase2a_basename_compatibility(tmp_path: Path) -> None:
    source = tmp_path / "source" / "résumé source.json"
    source.parent.mkdir(parents=True)
    source.write_text("{ invalid json", encoding="utf-8")
    result = _ingest(source, tmp_path / "archive")
    record_bytes, filename = _record_bytes_and_name(result)

    valid = verifier.verify_archive_record(
        record_bytes, filename=filename, expected_partition=c.PARTITION_REJECTED
    )
    mismatch = verifier.verify_archive_record(
        record_bytes, filename="other_record.json", expected_partition=c.PARTITION_REJECTED
    )

    assert valid.verification_state == "valid_rejected_reason_record"
    assert mismatch.content_status == verifier.CONTENT_VALID
    assert mismatch.placement_status == verifier.PLACEMENT_FILENAME_MISMATCH
    assert "résumé" not in json.dumps(asdict(valid), ensure_ascii=False)


def test_observation_record_preserves_phase2a_unicode_basename_compatibility(
    tmp_path: Path,
) -> None:
    result = _ingest(
        _write_source(tmp_path / "source", _obs(), name="résumé source.json"),
        tmp_path / "archive",
    )
    record_bytes, filename = _record_bytes_and_name(result)

    verified = verifier.verify_archive_record(
        record_bytes, filename=filename, expected_partition=c.PARTITION_ACCEPTED
    )

    assert verified.verification_state == "valid_accepted_record"
    assert "résumé" not in json.dumps(asdict(verified), ensure_ascii=False)


def test_rejected_duplicate_keys_and_nonstandard_constants_are_strictly_rejected() -> None:
    duplicate = b'{"archive_record_schema_version":"retirement_archive_rejected_record_v1","archive_record_schema_version":"retirement_archive_rejected_record_v1"}'
    nonstandard = b'{"archive_record_schema_version":NaN}'

    duplicate_result = verifier.verify_archive_record(
        duplicate, filename="record.json", expected_partition=c.PARTITION_REJECTED
    )
    nonstandard_result = verifier.verify_archive_record(
        nonstandard, filename="record.json", expected_partition=c.PARTITION_REJECTED
    )

    assert verifier.FINDING_RECORD_JSON_DUPLICATE_KEY in _all_findings(duplicate_result)
    assert verifier.FINDING_RECORD_JSON_NONSTANDARD_CONSTANT in _all_findings(nonstandard_result)


# SHA-256 byte goldens were produced directly from committed Phase 2A baseline
# 5d2ff20 before the pure-helper extraction.  Together with the exact filename,
# they guard byte-for-byte record serialization without consulting git history.
@pytest.mark.parametrize(
    "kind, expected_filename, expected_bytes_sha256, expected_decision, expected_reasons",
    (
        (
            "accepted",
            "202607101200000000__93fe8f99f919__72b4865522a54d688576977ca25fff7b4489676d63fe9dc728f47b118bf9a785.json",
            "70119b8eafca9abca6f2f70f2dc8fab5efc58658225a6f6e22d6ac933fafbe1e",
            c.DECISION_ACCEPTED,
            (),
        ),
        (
            "quarantined_full",
            "202607101200000000__bbaaa704a3d1__a758df6014fa20270ff5d3c6852f161bbc44199e99d4d038245240890da301e2.json",
            "9e092d2c320a247da4051dfc6ece65c27b6b65531e70ee59a9934bca9ea54a78",
            c.DECISION_QUARANTINED,
            (c.REASON_OBSERVATION_INCOMPLETE,),
        ),
        (
            "quarantined_minimal",
            "202607101200000000__noid__83b5d0ec8c4cd46c78b00b84699b5651960000c0b50b618c86844f29867c2ff4.json",
            "0ab35de24554bfc8dc7f08febc97da7b32aba4175600f54bbd8539b8b52cce04",
            c.DECISION_QUARANTINED,
            (c.REASON_BUILDER_INTERNAL_ERROR_OBSERVATION,),
        ),
        (
            "rejected_json",
            "rejected__a0b8e93c2b24cf18__a0b8e93c2b24cf18a8a013f4dbe0b0116d3b3be50153bc22509a793bc3cf976f.json",
            "c682b8d2a7c448aeb82e78b496a68cf7a500e7f11f73de8b97dc7f8341693bf6",
            c.DECISION_REJECTED,
            (c.REASON_SOURCE_NOT_VALID_JSON,),
        ),
    ),
)
def test_phase2a_pre_extraction_record_byte_goldens(
    tmp_path: Path,
    kind: str,
    expected_filename: str,
    expected_bytes_sha256: str,
    expected_decision: str,
    expected_reasons: tuple[str, ...],
) -> None:
    if kind == "accepted":
        source = _write_source(tmp_path / "source", _obs())
    elif kind == "quarantined_full":
        packet = deepcopy(_builder_inputs()["evidence_packet"])
        packet.pop("strategy_settings_hash")
        source = _write_source(tmp_path / "source", _obs(evidence_packet=packet))
    elif kind == "quarantined_minimal":
        source = _write_source(
            tmp_path / "source", _minimal_incomplete_observation("2026-07-10T12:00:00+00:00")
        )
    else:
        source = tmp_path / "source" / "step1a_retirement_observation.json"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"{ this is not valid json ")

    result = _ingest(source, tmp_path / "archive")
    record_bytes, filename = _record_bytes_and_name(result)

    assert filename == expected_filename
    assert hashlib.sha256(record_bytes).hexdigest() == expected_bytes_sha256
    assert result.decision == expected_decision
    assert result.reason_tokens == expected_reasons


@pytest.mark.parametrize("module", (rc, verifier))
def test_phase2b1_modules_are_staticly_pure(module: Any) -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_from = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    forbidden_modules = {"os", "pathlib", "subprocess", "datetime", "time", "socket"}

    assert imported_modules.isdisjoint(forbidden_modules)
    assert all(module.split(".")[0] not in forbidden_modules for module in imported_from)
    assert "open(" not in source
    assert ".write(" not in source
    assert ".read(" not in source
    assert "getenv" not in source


def test_verifier_returns_no_payload_and_does_not_import_workflow() -> None:
    source = Path(verifier.__file__).read_text(encoding="utf-8")

    assert "investment_orchestrator.workflow" not in source
    assert "observation_payload" not in RecordVerificationResult_fields()


def RecordVerificationResult_fields() -> set[str]:
    return set(verifier.RecordVerificationResult.__dataclass_fields__)
