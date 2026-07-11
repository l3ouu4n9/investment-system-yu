"""Pure Phase 2B-1 verifier for one supplied Phase 2A archive-record byte string.

The verifier never opens paths, writes files, reads the environment, invokes a
subprocess, or returns an observation payload.  Filesystem enumeration and any
archive-level inventory are deliberately deferred to a later Phase 2B slice.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from investment_orchestrator.research import step1a_retirement_observation as p1a

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence import archive_record_contract as rc
from investment_orchestrator.offline.retirement_evidence.source_validation import (
    classify_observation,
)


# Public result domains.  These strings are code-owned report-only labels, not
# permissions, readiness claims, archive-repair instructions, or runtime input.
CONTENT_VALID = "valid"
CONTENT_CORRUPT = "corrupt"
CONTENT_SCHEMA_INCOMPATIBLE = "schema_incompatible"
CONTENT_UNREADABLE = "unreadable"
CONTENT_VERIFICATION_LIMIT_EXCEEDED = "verification_limit_exceeded"

PLACEMENT_CORRECT = "correct"
PLACEMENT_PARTITION_MISMATCH = "partition_mismatch"
PLACEMENT_FILENAME_MISMATCH = "filename_mismatch"
PLACEMENT_UNSAFE_ENTRY_METADATA = "unsafe_entry_metadata"

SELF_INTEGRITY_VERIFIED = "verified"
SELF_INTEGRITY_INVALID_OR_MISMATCH = "invalid_or_mismatch"
SELF_INTEGRITY_NOT_AVAILABLE_IN_SCHEMA = "not_available_in_schema"
SELF_INTEGRITY_NOT_CHECKED = "not_checked"

RECORD_KIND_UNKNOWN_SCHEMA = "unknown_schema"
RECORD_KIND_ACCEPTED = "accepted_observation_record"
RECORD_KIND_QUARANTINED = "quarantined_observation_record"
RECORD_KIND_REJECTED = "rejected_reason_record"
RECORD_KIND_UNREADABLE_BYTES = "unreadable_bytes"


# Findings never carry a path, filename, parser text, JSON key, payload value,
# source basename, or any other attacker-controlled content.
FINDING_ENTRY_FILENAME_UNSAFE = "entry_filename_unsafe"
FINDING_EXPECTED_PARTITION_INVALID = "expected_partition_invalid"
FINDING_RECORD_BYTES_UNREADABLE = "record_bytes_unreadable"
FINDING_RECORD_INVALID_UTF8 = "record_invalid_utf8"
FINDING_RECORD_JSON_MALFORMED = "record_json_malformed"
FINDING_RECORD_JSON_DUPLICATE_KEY = "record_json_duplicate_key"
FINDING_RECORD_JSON_NONSTANDARD_CONSTANT = "record_json_nonstandard_constant"
FINDING_RECORD_TOP_LEVEL_NOT_MAPPING = "record_top_level_not_mapping"
FINDING_RECORD_SCHEMA_UNRECOGNIZED = "record_schema_unrecognized"
FINDING_RECORD_VERIFICATION_EXCEPTION = "record_verification_exception"
FINDING_OBSERVATION_RECORD_KEY_SET_INVALID = "observation_record_key_set_invalid"
FINDING_REJECTED_RECORD_KEY_SET_INVALID = "rejected_record_key_set_invalid"
FINDING_ARCHIVE_LAYOUT_VERSION_INVALID = "archive_layout_version_invalid"
FINDING_ARCHIVE_TOOL_VERSION_INVALID = "archive_tool_version_invalid"
FINDING_ARCHIVE_TOOL_COMMIT_INVALID = "archive_tool_commit_invalid"
FINDING_ARCHIVED_AT_INVALID = "archived_at_invalid"
FINDING_INGESTION_DECISION_INVALID = "ingestion_decision_invalid"
FINDING_INGESTION_REASON_TOKENS_INVALID = "ingestion_reason_tokens_invalid"
FINDING_PROVENANCE_TOKEN_INVALID = "provenance_token_invalid"
FINDING_PROVENANCE_CLAIM_SOURCE_INVALID = "provenance_claim_source_invalid"
FINDING_PROVENANCE_VERIFIED_INVALID = "provenance_verified_invalid"
FINDING_SOURCE_METADATA_INVALID = "source_metadata_invalid"
FINDING_RECOMPUTED_IDENTITY_INVALID = "recomputed_identity_invalid"
FINDING_SOURCE_FILE_SHA256_INVALID = "source_file_sha256_invalid"
FINDING_ARCHIVE_RECORD_CONTENT_SHA256_INVALID = "archive_record_content_sha256_invalid"
FINDING_ARCHIVE_RECORD_CONTENT_HASH_MISMATCH = "archive_record_content_hash_mismatch"
FINDING_OBSERVATION_PAYLOAD_NOT_MAPPING = "observation_payload_not_mapping"
FINDING_SOURCE_CANONICAL_PAYLOAD_SHA256_INVALID = "source_canonical_payload_sha256_invalid"
FINDING_SOURCE_CANONICAL_PAYLOAD_HASH_MISMATCH = "source_canonical_payload_hash_mismatch"
FINDING_SOURCE_PAYLOAD_CONTRACT_INVALID = "source_payload_contract_invalid"
FINDING_INGESTION_DECISION_MISMATCH = "ingestion_decision_mismatch"
FINDING_INGESTION_REASON_TOKENS_MISMATCH = "ingestion_reason_tokens_mismatch"
FINDING_RECOMPUTED_IDENTITY_MISMATCH = "recomputed_identity_mismatch"
FINDING_SOURCE_METADATA_PAYLOAD_MISMATCH = "source_metadata_payload_mismatch"
FINDING_RECORD_PARTITION_MISMATCH = "record_partition_mismatch"
FINDING_RECORD_FILENAME_MISMATCH = "record_filename_mismatch"


@dataclass(frozen=True)
class RecordVerificationResult:
    """Raw-content-free verification facts for exactly one supplied record."""

    record_kind: str
    content_status: str
    placement_status: str
    verification_state: str
    identity_facts_valid: bool
    self_integrity_status: str
    integrity_findings: tuple[str, ...]
    compatibility_findings: tuple[str, ...]
    placement_findings: tuple[str, ...]
    informational_findings: tuple[str, ...]
    archive_record_content_sha256: str | None
    source_canonical_payload_sha256: str | None
    observation_id: str | None
    coverage_key: str | None
    source_git_commit: str | None
    ingestion_decision: str | None
    claimed_evidence_provenance: str | None


@dataclass
class _State:
    record_kind: str = RECORD_KIND_UNKNOWN_SCHEMA
    content_status: str = CONTENT_VALID
    placement_status: str = PLACEMENT_CORRECT
    self_integrity_status: str = SELF_INTEGRITY_NOT_CHECKED
    integrity_findings: set[str] = field(default_factory=set)
    compatibility_findings: set[str] = field(default_factory=set)
    placement_findings: set[str] = field(default_factory=set)
    informational_findings: set[str] = field(default_factory=set)
    archive_record_content_sha256: str | None = None
    source_canonical_payload_sha256: str | None = None
    observation_id: str | None = None
    coverage_key: str | None = None
    source_git_commit: str | None = None
    ingestion_decision: str | None = None
    claimed_evidence_provenance: str | None = None

    def corrupt(self, finding: str) -> None:
        self.content_status = CONTENT_CORRUPT
        self.integrity_findings.add(finding)

    def schema_incompatible(self, finding: str) -> None:
        self.content_status = CONTENT_SCHEMA_INCOMPATIBLE
        self.compatibility_findings.add(finding)

    def unsafe_entry(self, finding: str) -> None:
        self.placement_status = PLACEMENT_UNSAFE_ENTRY_METADATA
        self.placement_findings.add(finding)

    def partition_mismatch(self) -> None:
        if self.placement_status != PLACEMENT_UNSAFE_ENTRY_METADATA:
            self.placement_status = PLACEMENT_PARTITION_MISMATCH
        self.placement_findings.add(FINDING_RECORD_PARTITION_MISMATCH)

    def filename_mismatch(self) -> None:
        if self.placement_status == PLACEMENT_CORRECT:
            self.placement_status = PLACEMENT_FILENAME_MISMATCH
        self.placement_findings.add(FINDING_RECORD_FILENAME_MISMATCH)

    def result(self) -> RecordVerificationResult:
        identity_facts_valid = (
            self.content_status == CONTENT_VALID
            and self.record_kind in (RECORD_KIND_ACCEPTED, RECORD_KIND_QUARANTINED)
        )
        return RecordVerificationResult(
            record_kind=self.record_kind,
            content_status=self.content_status,
            placement_status=self.placement_status,
            verification_state=_verification_state(self),
            identity_facts_valid=identity_facts_valid,
            self_integrity_status=self.self_integrity_status,
            integrity_findings=tuple(sorted(self.integrity_findings)),
            compatibility_findings=tuple(sorted(self.compatibility_findings)),
            placement_findings=tuple(sorted(self.placement_findings)),
            informational_findings=tuple(sorted(self.informational_findings)),
            archive_record_content_sha256=(
                self.archive_record_content_sha256 if identity_facts_valid else None
            ),
            source_canonical_payload_sha256=(
                self.source_canonical_payload_sha256 if identity_facts_valid else None
            ),
            observation_id=self.observation_id if identity_facts_valid else None,
            coverage_key=self.coverage_key if identity_facts_valid else None,
            source_git_commit=self.source_git_commit if identity_facts_valid else None,
            ingestion_decision=self.ingestion_decision
            if self.content_status == CONTENT_VALID
            else None,
            claimed_evidence_provenance=self.claimed_evidence_provenance
            if self.content_status == CONTENT_VALID
            else None,
        )


class _DuplicateJsonKey(Exception):
    pass


class _NonstandardJsonConstant(Exception):
    pass


def verify_archive_record(
    record_bytes: bytes,
    *,
    filename: Any,
    expected_partition: Any,
) -> RecordVerificationResult:
    """Verify one supplied Phase 2A archive record without touching a filesystem.

    ``filename`` must be a basename supplied by a future scanner or an operator
    test harness.  It is never returned or used as authority; only a safe
    filename can receive a placement comparison.
    """
    state = _State()
    safe_filename = filename if rc.is_safe_entry_filename(filename) else None
    if safe_filename is None:
        state.unsafe_entry(FINDING_ENTRY_FILENAME_UNSAFE)
    if expected_partition not in c.PARTITIONS:
        state.unsafe_entry(FINDING_EXPECTED_PARTITION_INVALID)

    parsed = _strict_json_mapping(record_bytes, state)
    if parsed is None:
        return state.result()

    try:
        schema = parsed.get("archive_record_schema_version")
        if schema == c.ARCHIVE_RECORD_SCHEMA_VERSION:
            _verify_observation_record(
                parsed,
                safe_filename=safe_filename,
                expected_partition=expected_partition,
                state=state,
            )
        elif schema == c.ARCHIVE_REJECTED_RECORD_SCHEMA_VERSION:
            _verify_rejected_record(
                parsed,
                safe_filename=safe_filename,
                expected_partition=expected_partition,
                state=state,
            )
        else:
            state.record_kind = RECORD_KIND_UNKNOWN_SCHEMA
            state.schema_incompatible(FINDING_RECORD_SCHEMA_UNRECOGNIZED)
    except Exception:  # noqa: BLE001 - hostile record values must never escape
        state.corrupt(FINDING_RECORD_VERIFICATION_EXCEPTION)
    return state.result()


def _strict_json_mapping(record_bytes: Any, state: _State) -> Mapping[str, Any] | None:
    if not isinstance(record_bytes, bytes):
        state.record_kind = RECORD_KIND_UNREADABLE_BYTES
        state.content_status = CONTENT_UNREADABLE
        state.integrity_findings.add(FINDING_RECORD_BYTES_UNREADABLE)
        return None
    try:
        text = record_bytes.decode("utf-8")
    except UnicodeDecodeError:
        state.corrupt(FINDING_RECORD_INVALID_UTF8)
        return None
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except _DuplicateJsonKey:
        state.corrupt(FINDING_RECORD_JSON_DUPLICATE_KEY)
        return None
    except _NonstandardJsonConstant:
        state.corrupt(FINDING_RECORD_JSON_NONSTANDARD_CONSTANT)
        return None
    except (json.JSONDecodeError, RecursionError, ValueError):
        state.corrupt(FINDING_RECORD_JSON_MALFORMED)
        return None
    if not isinstance(parsed, Mapping):
        state.corrupt(FINDING_RECORD_TOP_LEVEL_NOT_MAPPING)
        return None
    return parsed


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonstandard_constant(_value: str) -> None:
    raise _NonstandardJsonConstant


def _verify_observation_record(
    record: Mapping[str, Any],
    *,
    safe_filename: str | None,
    expected_partition: Any,
    state: _State,
) -> None:
    state.record_kind = "observation_record"
    if frozenset(record.keys()) != rc.OBSERVATION_RECORD_ENVELOPE_KEYS:
        state.corrupt(FINDING_OBSERVATION_RECORD_KEY_SET_INVALID)
        return
    if record["archive_layout_version"] != c.ARCHIVE_LAYOUT_VERSION:
        state.corrupt(FINDING_ARCHIVE_LAYOUT_VERSION_INVALID)
        return
    if not rc.is_valid_archive_tool_version(record["archive_tool_version"]):
        state.corrupt(FINDING_ARCHIVE_TOOL_VERSION_INVALID)
        return
    if not rc.is_valid_archive_tool_commit(record["archive_tool_commit"]):
        state.corrupt(FINDING_ARCHIVE_TOOL_COMMIT_INVALID)
        return
    if not p1a.is_valid_generated_at(record["archived_at"]):
        state.corrupt(FINDING_ARCHIVED_AT_INVALID)
        return

    decision = record["ingestion_decision"]
    if decision not in (c.DECISION_ACCEPTED, c.DECISION_QUARANTINED):
        state.corrupt(FINDING_INGESTION_DECISION_INVALID)
        return
    state.ingestion_decision = decision
    state.record_kind = (
        RECORD_KIND_ACCEPTED if decision == c.DECISION_ACCEPTED else RECORD_KIND_QUARANTINED
    )
    if expected_partition in c.PARTITIONS and expected_partition != decision:
        state.partition_mismatch()
    if not rc.is_canonical_reason_token_list(record["ingestion_reason_tokens"], decision=decision):
        state.corrupt(FINDING_INGESTION_REASON_TOKENS_INVALID)
        return

    if not (
        isinstance(record["claimed_evidence_provenance"], str)
        and record["claimed_evidence_provenance"] in c.PROVENANCE_VALUES
    ):
        state.corrupt(FINDING_PROVENANCE_TOKEN_INVALID)
        return
    state.claimed_evidence_provenance = record["claimed_evidence_provenance"]
    if not (
        isinstance(record["provenance_claim_source"], str)
        and record["provenance_claim_source"] in rc.PROVENANCE_CLAIM_SOURCES
    ):
        state.corrupt(FINDING_PROVENANCE_CLAIM_SOURCE_INVALID)
        return
    if record["provenance_verified"] is not False:
        state.corrupt(FINDING_PROVENANCE_VERIFIED_INVALID)
        return

    metadata = record["source_metadata"]
    if not _valid_source_metadata(metadata):
        state.corrupt(FINDING_SOURCE_METADATA_INVALID)
        return
    recomputed = record["recomputed_identity"]
    if not _valid_recomputed_identity(recomputed):
        state.corrupt(FINDING_RECOMPUTED_IDENTITY_INVALID)
        return
    if not p1a.is_canonical_sha256(record["source_file_sha256"]):
        state.corrupt(FINDING_SOURCE_FILE_SHA256_INVALID)
        return

    stored_record_hash = record["archive_record_content_sha256"]
    if not p1a.is_canonical_sha256(stored_record_hash):
        state.self_integrity_status = SELF_INTEGRITY_INVALID_OR_MISMATCH
        state.corrupt(FINDING_ARCHIVE_RECORD_CONTENT_SHA256_INVALID)
        return
    computed_record_hash = rc.compute_archive_record_content_sha256(record)
    if computed_record_hash != stored_record_hash:
        state.self_integrity_status = SELF_INTEGRITY_INVALID_OR_MISMATCH
        state.corrupt(FINDING_ARCHIVE_RECORD_CONTENT_HASH_MISMATCH)
        return
    state.self_integrity_status = SELF_INTEGRITY_VERIFIED

    payload = record["observation_payload"]
    if not isinstance(payload, Mapping):
        state.corrupt(FINDING_OBSERVATION_PAYLOAD_NOT_MAPPING)
        return
    stored_payload_hash = record["source_canonical_payload_sha256"]
    if not p1a.is_canonical_sha256(stored_payload_hash):
        state.corrupt(FINDING_SOURCE_CANONICAL_PAYLOAD_SHA256_INVALID)
        return
    if p1a.canonical_sha256(payload) != stored_payload_hash:
        state.corrupt(FINDING_SOURCE_CANONICAL_PAYLOAD_HASH_MISMATCH)
        return

    classification = classify_observation(payload)
    if classification.decision == c.DECISION_REJECTED:
        state.corrupt(FINDING_SOURCE_PAYLOAD_CONTRACT_INVALID)
        return
    if classification.decision != decision:
        state.corrupt(FINDING_INGESTION_DECISION_MISMATCH)
        return
    if tuple(record["ingestion_reason_tokens"]) != classification.reason_tokens:
        state.corrupt(FINDING_INGESTION_REASON_TOKENS_MISMATCH)
        return
    if any(
        recomputed[key] != classification.recomputed_identity.get(key)
        for key in rc.RECOMPUTED_IDENTITY_KEYS
    ):
        state.corrupt(FINDING_RECOMPUTED_IDENTITY_MISMATCH)
        return

    coverage = payload.get("coverage_identity")
    payload_coverage_key = coverage.get("coverage_key") if isinstance(coverage, Mapping) else None
    if (
        metadata["source_observation_id"] != rc.stored_observation_id(payload)
        or metadata["source_coverage_key"] != payload_coverage_key
        or metadata["source_schema_version"] != payload.get("schema_version")
        or metadata["source_git_commit"] != rc.clean_payload_git_commit(payload)
    ):
        state.corrupt(FINDING_SOURCE_METADATA_PAYLOAD_MISMATCH)
        return

    state.archive_record_content_sha256 = stored_record_hash
    state.source_canonical_payload_sha256 = stored_payload_hash
    state.observation_id = rc.stored_observation_id(payload)
    state.coverage_key = payload_coverage_key if isinstance(payload_coverage_key, str) else None
    state.source_git_commit = rc.clean_payload_git_commit(payload)
    if safe_filename is not None:
        expected_filename = rc.expected_observation_record_filename(
            payload, state.observation_id, stored_payload_hash
        )
        if safe_filename != expected_filename:
            state.filename_mismatch()


def _verify_rejected_record(
    record: Mapping[str, Any],
    *,
    safe_filename: str | None,
    expected_partition: Any,
    state: _State,
) -> None:
    state.record_kind = RECORD_KIND_REJECTED
    state.self_integrity_status = SELF_INTEGRITY_NOT_AVAILABLE_IN_SCHEMA
    if frozenset(record.keys()) != rc.REJECTED_RECORD_KEYS:
        state.corrupt(FINDING_REJECTED_RECORD_KEY_SET_INVALID)
        return
    if record["archive_layout_version"] != c.ARCHIVE_LAYOUT_VERSION:
        state.corrupt(FINDING_ARCHIVE_LAYOUT_VERSION_INVALID)
        return
    if not rc.is_valid_archive_tool_version(record["archive_tool_version"]):
        state.corrupt(FINDING_ARCHIVE_TOOL_VERSION_INVALID)
        return
    if not rc.is_valid_archive_tool_commit(record["archive_tool_commit"]):
        state.corrupt(FINDING_ARCHIVE_TOOL_COMMIT_INVALID)
        return
    if not p1a.is_valid_generated_at(record["archived_at"]):
        state.corrupt(FINDING_ARCHIVED_AT_INVALID)
        return
    if record["ingestion_decision"] != c.DECISION_REJECTED:
        state.corrupt(FINDING_INGESTION_DECISION_INVALID)
        return
    state.ingestion_decision = c.DECISION_REJECTED
    if expected_partition in c.PARTITIONS and expected_partition != c.DECISION_REJECTED:
        state.partition_mismatch()
    if not rc.is_canonical_reason_token_list(
        record["ingestion_reason_tokens"], decision=c.DECISION_REJECTED
    ):
        state.corrupt(FINDING_INGESTION_REASON_TOKENS_INVALID)
        return
    if not rc.is_phase2a_source_basename(record["source_basename"]):
        state.corrupt(FINDING_SOURCE_METADATA_INVALID)
        return
    if not p1a.is_canonical_sha256(record["source_file_sha256"]):
        state.corrupt(FINDING_SOURCE_FILE_SHA256_INVALID)
        return
    if safe_filename is not None:
        expected_filename = rc.expected_rejected_record_filename(record["source_file_sha256"])
        if safe_filename != expected_filename:
            state.filename_mismatch()


def _valid_source_metadata(value: Any) -> bool:
    if not isinstance(value, Mapping) or frozenset(value.keys()) != rc.SOURCE_METADATA_KEYS:
        return False
    return (
        rc.is_phase2a_source_basename(value["source_basename"])
        and _is_sha256_or_none(value["source_observation_id"])
        and _is_sha256_or_none(value["source_coverage_key"])
        and (value["source_git_commit"] is None or p1a.is_git_commit(value["source_git_commit"]))
        and isinstance(value["source_schema_version"], str)
    )


def _valid_recomputed_identity(value: Any) -> bool:
    return isinstance(value, Mapping) and frozenset(value.keys()) == rc.RECOMPUTED_IDENTITY_KEYS and all(
        _is_sha256_or_none(value[key]) for key in rc.RECOMPUTED_IDENTITY_KEYS
    )


def _is_sha256_or_none(value: Any) -> bool:
    return value is None or p1a.is_canonical_sha256(value)


def _verification_state(state: _State) -> str:
    if state.content_status == CONTENT_UNREADABLE:
        return "unreadable_record"
    if state.content_status == CONTENT_VERIFICATION_LIMIT_EXCEEDED:
        return "verification_limit_exceeded"
    if state.content_status == CONTENT_SCHEMA_INCOMPATIBLE:
        return "schema_incompatible_record"
    if state.content_status == CONTENT_CORRUPT:
        return "corrupt_record"
    if state.placement_status == PLACEMENT_UNSAFE_ENTRY_METADATA:
        return "unsafe_entry_metadata"
    if state.placement_status == PLACEMENT_PARTITION_MISMATCH:
        return "partition_mismatch"
    if state.placement_status == PLACEMENT_FILENAME_MISMATCH:
        return "filename_mismatch"
    if state.record_kind == RECORD_KIND_ACCEPTED:
        return "valid_accepted_record"
    if state.record_kind == RECORD_KIND_QUARANTINED:
        return "valid_quarantined_record"
    if state.record_kind == RECORD_KIND_REJECTED:
        return "valid_rejected_reason_record"
    return "corrupt_record"
