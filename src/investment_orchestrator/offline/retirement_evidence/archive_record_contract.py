"""Pure shared contract helpers for Phase 2A archive records.

This module is deliberately neutral: it performs no filesystem, environment,
subprocess, clock, LLM, or runtime-workflow access.  Phase 2A ingestion and
the Phase 2B single-record verifier share these definitions so archive-record
shape, canonicalization, filenames, and safe reason-token domains cannot drift.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from investment_orchestrator.research import step1a_retirement_observation as p1a

from investment_orchestrator.offline.retirement_evidence import archive_contract as c


# --- exact archive-record shapes --------------------------------------------
OBSERVATION_RECORD_ENVELOPE_KEYS = frozenset(
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
SOURCE_METADATA_KEYS = frozenset(
    {
        "source_basename",
        "source_observation_id",
        "source_coverage_key",
        "source_git_commit",
        "source_schema_version",
    }
)
RECOMPUTED_IDENTITY_KEYS = frozenset(
    {"composite_config_fingerprint", "coverage_key", "observation_id"}
)
REJECTED_RECORD_KEYS = frozenset(
    {
        "archive_record_schema_version",
        "archive_layout_version",
        "archive_tool_version",
        "archive_tool_commit",
        "archived_at",
        "ingestion_decision",
        "ingestion_reason_tokens",
        "source_basename",
        "source_file_sha256",
    }
)

PROVENANCE_CLAIM_SOURCES = frozenset(
    {
        c.PROVENANCE_CLAIM_SOURCE_DEFAULT,
        c.PROVENANCE_CLAIM_SOURCE_OPERATOR,
    }
)


# --- safe, code-owned filename handling -------------------------------------
_SAFE_OBSERVATION_RECORD_BASENAME_RE = re.compile(
    r"(?:[0-9]+|nogen)__(?:[0-9a-f]{12}|noid)__[0-9a-f]{64}\.json\Z"
)
_SAFE_ENTRY_FILENAME_RE = re.compile(r"[A-Za-z0-9._-]{1,256}\Z")


def safe_record_basename(value: Any) -> str:
    """Return a safe record basename or a code-owned replacement token."""
    if isinstance(value, str) and _SAFE_OBSERVATION_RECORD_BASENAME_RE.fullmatch(value):
        return value
    return "invalid_archive_record_basename"


def is_safe_entry_filename(value: Any) -> bool:
    """True for a single non-path filename safe to receive from a caller."""
    return isinstance(value, str) and _SAFE_ENTRY_FILENAME_RE.fullmatch(value) is not None


def is_phase2a_source_basename(value: Any) -> bool:
    """Match the basename domain Phase 2A actually validates: non-empty text.

    Ingestion stores ``Path.name`` and its existing-record validator accepts a
    non-empty string.  Keep this deliberately compatibility-preserving; callers
    must never surface the value in a verifier result.
    """
    return isinstance(value, str) and bool(value)


def compact_generated_at(value: Any) -> str:
    """Return the Phase 2A filename timestamp segment for one payload value."""
    if not (isinstance(value, str) and p1a.is_valid_generated_at(value)):
        return "nogen"
    return "".join(ch for ch in value if ch.isdigit())


def stored_observation_id(payload: Any) -> str | None:
    """Return a stored observation id only when it is represented as text."""
    if not isinstance(payload, Mapping):
        return None
    identity = payload.get("observation_identity")
    if not isinstance(identity, Mapping):
        return None
    value = identity.get("observation_id")
    return value if isinstance(value, str) else None


def expected_observation_record_filename(
    payload: Mapping[str, Any],
    observation_id: str | None,
    canonical_payload_sha256: str,
) -> str:
    """Derive the exact Phase 2A accepted/quarantined filename."""
    identity = payload.get("observation_identity")
    generated_at = identity.get("generated_at") if isinstance(identity, Mapping) else None
    observation_segment = observation_id[:12] if isinstance(observation_id, str) else "noid"
    return (
        f"{compact_generated_at(generated_at)}__{observation_segment}__"
        f"{canonical_payload_sha256}.json"
    )


def expected_rejected_record_filename(source_file_sha256: str) -> str:
    """Derive the exact Phase 2A rejected-record filename."""
    return f"rejected__{source_file_sha256[:16]}__{source_file_sha256}.json"


# --- canonical identity and envelope helpers --------------------------------
def compute_archive_record_content_sha256(envelope: Mapping[str, Any]) -> str | None:
    """Hash an archive envelope excluding only its embedded self-hash field."""
    without_self = {
        key: value for key, value in envelope.items() if key != "archive_record_content_sha256"
    }
    return p1a.canonical_sha256(without_self)


def clean_payload_git_commit(payload: Mapping[str, Any]) -> str | None:
    """Return the payload clean commit, or ``None`` for every other state."""
    code = payload.get("code_identity")
    if isinstance(code, Mapping) and code.get("git_state") == "clean":
        candidate = code.get("git_commit")
        if p1a.is_git_commit(candidate):
            return candidate
    return None


def is_valid_archive_tool_version(value: Any) -> bool:
    """Require the current code-owned Phase 2A archive tool version."""
    return value == c.ARCHIVE_TOOL_VERSION


def is_valid_archive_tool_commit(value: Any) -> bool:
    """Validate the emitted archive-tool commit representation."""
    return value == "unavailable" or p1a.is_git_commit(value)


# --- canonical Phase 2A ingestion-reason domains ----------------------------
_REJECT_REASON_TOKENS = frozenset(
    {
        c.REASON_SOURCE_NOT_VALID_JSON,
        c.REASON_SOURCE_NOT_JSON_OBJECT,
        c.REASON_SCHEMA_VERSION_UNRECOGNIZED,
        c.REASON_TOP_LEVEL_KEYS_INVALID,
        c.REASON_AUTHORITY_ENVELOPE_VIOLATION,
        c.REASON_READINESS_CLAIM_PRESENT,
        c.REASON_RAW_CONTENT_UNSAFE,
        c.REASON_COMPOSITE_FINGERPRINT_MISMATCH,
        c.REASON_COVERAGE_KEY_MISMATCH,
        c.REASON_OBSERVATION_ID_MISMATCH,
        c.REASON_OBSERVATION_ID_CONTENT_CONFLICT,
        c.REASON_ARCHIVE_FILENAME_COLLISION,
        c.REASON_OBSERVATION_COMPLETENESS_INCONSISTENT,
    }
)
_QUARANTINE_REASON_TOKENS = frozenset(
    {
        c.REASON_OBSERVATION_INCOMPLETE,
        c.REASON_CODE_IDENTITY_DIRTY,
        c.REASON_CODE_IDENTITY_UNAVAILABLE,
        c.REASON_CODE_VERSION_NOT_USABLE,
        c.REASON_BUILDER_INTERNAL_ERROR_OBSERVATION,
    }
)

_DIAGNOSTIC_FIELDS = (
    "missing_observation_fields",
    "malformed_observation_fields",
    "compatibility_blockers",
    "permission_context_inconsistencies",
)
_WRITER_SLOTS = ("evidence_packet", "embedded_selection")
_WRITER_FIELDS = (
    "final_writer_source",
    "fallback_used",
    "canonical_error_token",
    "unknown_error_present",
    "error_summary_sha256",
    "invocation_count",
    "first_final_status_divergence",
    "final_disk_write_invocation",
)
_GUARD_FIELDS = (
    "match_observed",
    "unknown_timestamp_observed",
    "unexpected_normalized_path_observed",
    "differences_count",
)

_FIELD_DOMAIN_SUFFIXES = frozenset(
    {
        "observation_completeness",
        "observation_identity.observation_id",
        "observation_identity.generated_at",
        "coverage_identity.coverage_key",
        "coverage_identity.composite_config_fingerprint",
        "classification_contract_version",
        "identity_semantics.observation_id",
        "identity_semantics.coverage_key",
        "code_identity.git_state",
        "code_identity.git_commit",
        "code_identity.code_version_usable_for_evidence",
        "fallback_error_tokens",
        "shadow_and_observatory_observation.comparison_status",
        "shadow_and_observatory_observation.parity_passed",
        "shadow_and_observatory_observation.comparison_complete",
        "shadow_and_observatory_observation.skipped_artifact_keys",
        "shadow_and_observatory_observation.mismatch_artifact_keys",
        "shadow_and_observatory_observation.observatory_integration_result",
        "grounding_observation.evidence_packet_final_artifact_present",
        "grounding_observation.evidence_packet_final_artifact_parseable",
        "grounding_observation.compiled_support_signals_present",
        "grounding_observation.compiled_support_signals_parseable",
        "grounding_observation.grounded_memo_support_present",
        "grounding_observation.accepted_support_signal_count",
        "grounding_observation.evidence_packet_mapping_available",
        "permission_context_observation.research_state",
        "permission_context_observation.research_availability_state",
        "permission_context_observation.allowed_actions",
        "permission_context_observation.new_buy_allowed",
        "permission_context_observation.order_compilation_allowed",
        "permission_context_observation.permission_context_consistent",
        *_DIAGNOSTIC_FIELDS,
        *(f"contract_versions.{name}" for name in p1a.EXPECTED_SCHEMA_VERSIONS),
        *(f"configuration_hashes.{name}" for name in (
            "strategy_settings_hash",
            "research_anchors_sha256",
            "research_anchor_approvals_sha256",
            "research_anchor_revocations_sha256",
        )),
        *(f"input_state_observations.{name}" for name in (
            "as_of_input_class",
            "approvals_state",
            "revocations_state",
            "selected_source_class",
            "snapshot_state",
            "last_good_availability",
            "production_observatory_mapping_available",
        )),
        *(f"writer_outcomes.{slot}.{name}" for slot in _WRITER_SLOTS for name in _WRITER_FIELDS),
        *(f"guard_summaries.{slot}.{name}" for slot in _WRITER_SLOTS for name in _GUARD_FIELDS),
    }
)
_NESTED_STRUCTURE_SUFFIXES = frozenset(
    {
        "observation",
        "observation_identity",
        "coverage_identity",
        "identity_semantics",
        "code_identity",
        "contract_versions",
        "configuration_hashes",
        "input_state_observations",
        "writer_outcomes",
        "fallback_error_tokens",
        "guard_summaries",
        "shadow_and_observatory_observation",
        "grounding_observation",
        "permission_context_observation",
        *_DIAGNOSTIC_FIELDS,
        *(f"writer_outcomes.{slot}" for slot in _WRITER_SLOTS),
        *(f"guard_summaries.{slot}" for slot in _WRITER_SLOTS),
    }
)


def is_canonical_rejection_reason_token(value: Any) -> bool:
    """True iff one token can be emitted in a Phase 2A rejected record."""
    if not isinstance(value, str):
        return False
    if value in _REJECT_REASON_TOKENS:
        return True
    field_prefix = f"{c.REASON_PREFIX_FIELD_DOMAIN_INVALID}:"
    if value.startswith(field_prefix):
        return value[len(field_prefix):] in _FIELD_DOMAIN_SUFFIXES
    structure_prefix = f"{c.REASON_PREFIX_NESTED_STRUCTURE_INVALID}:"
    if value.startswith(structure_prefix):
        return value[len(structure_prefix):] in _NESTED_STRUCTURE_SUFFIXES
    return False


def is_canonical_quarantine_reason_token(value: Any) -> bool:
    """True iff one token can be emitted for a quarantined observation."""
    return isinstance(value, str) and value in _QUARANTINE_REASON_TOKENS


def is_canonical_reason_token_list(
    values: Any,
    *,
    decision: str,
) -> bool:
    """Validate sorted, duplicate-free Phase 2A reason tokens for one decision."""
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return False
    if not all(isinstance(value, str) for value in values):
        return False
    tokens = list(values)
    if tokens != sorted(set(tokens)):
        return False
    if decision == c.DECISION_ACCEPTED:
        return tokens == []
    if decision == c.DECISION_QUARANTINED:
        return bool(tokens) and all(is_canonical_quarantine_reason_token(token) for token in tokens)
    if decision == c.DECISION_REJECTED:
        return bool(tokens) and all(is_canonical_rejection_reason_token(token) for token in tokens)
    return False
