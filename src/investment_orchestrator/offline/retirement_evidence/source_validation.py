"""Strict, full-contract validator for ``step1a_retirement_observation_v1``.

This is the safety core of Phase 2A.  It does NOT accept an observation on the
basis of schema version + authority markers alone; it validates the complete
committed v1 contract - exact top-level and nested key allowlists, field types,
enums/tokens, the authority envelope, raw-content-safe field domains, and it
*recomputes* the composite configuration fingerprint, coverage key, and
observation id and compares them to the stored values.

It reuses the committed Phase 1A domains, canonicalization, and identity
recomputation (imported from
:mod:`investment_orchestrator.research.step1a_retirement_observation`) rather
than re-implementing a weaker parallel copy that could drift from the writer.

The classifier is pure and read-only: it copies no raw content, mutates no
input, never raises on hostile input, and reaches no retirement/sufficiency
conclusion.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from investment_orchestrator.research import step1a_retirement_observation as p1a

from investment_orchestrator.offline.retirement_evidence import archive_contract as c


# --- exact key allowlists (committed v1 shapes) ------------------------------
_FULL_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "is_llm_generated",
        "report_only",
        "not_authorization",
        "not_execution_authorization",
        "permission_effect",
        "consumed_by_gates",
        "consumed_by_order_path",
        "consumed_by_downstream",
        "safe_to_ignore",
        "assessment_state",
        "identity_semantics",
        "classification_contract_version",
        "observation_identity",
        "coverage_identity",
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
        "observation_completeness",
        "missing_observation_fields",
        "malformed_observation_fields",
        "compatibility_blockers",
        "permission_context_inconsistencies",
    }
)
_MINIMAL_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "is_llm_generated",
        "report_only",
        "not_authorization",
        "not_execution_authorization",
        "permission_effect",
        "consumed_by_gates",
        "consumed_by_order_path",
        "consumed_by_downstream",
        "safe_to_ignore",
        "assessment_state",
        "observation_identity",
        "coverage_identity",
        "observation_completeness",
        "missing_observation_fields",
        "malformed_observation_fields",
        "compatibility_blockers",
        "permission_context_inconsistencies",
    }
)

# Exact authority envelope required in BOTH shapes.
_AUTHORITY_ENVELOPE = {
    "schema_version": p1a.SCHEMA_VERSION,
    "is_llm_generated": False,
    "report_only": True,
    "not_authorization": True,
    "not_execution_authorization": True,
    "permission_effect": "none",
    "consumed_by_gates": False,
    "consumed_by_order_path": False,
    "consumed_by_downstream": False,
    "safe_to_ignore": True,
    "assessment_state": "observation_only",
}

_OBSERVATION_IDENTITY_KEYS = frozenset({"observation_id", "generated_at"})
_COVERAGE_IDENTITY_KEYS = frozenset({"coverage_key", "composite_config_fingerprint"})
_IDENTITY_SEMANTICS_KEYS = frozenset({"observation_id", "coverage_key"})
_CODE_IDENTITY_KEYS = frozenset(
    {"git_commit", "git_state", "code_version_usable_for_evidence"}
)
_CONFIGURATION_HASHES_KEYS = frozenset(
    {
        "strategy_settings_hash",
        "research_anchors_sha256",
        "research_anchor_approvals_sha256",
        "research_anchor_revocations_sha256",
    }
)
_INPUT_STATE_KEYS = frozenset(
    {
        "as_of_input_class",
        "approvals_state",
        "revocations_state",
        "selected_source_class",
        "snapshot_state",
        "last_good_availability",
        "production_observatory_mapping_available",
    }
)
_WRITER_OUTCOMES_KEYS = frozenset({"evidence_packet", "embedded_selection"})
_WRITER_OUTCOME_KEYS = frozenset(
    {
        "final_writer_source",
        "fallback_used",
        "canonical_error_token",
        "unknown_error_present",
        "error_summary_sha256",
        "invocation_count",
        "first_final_status_divergence",
        "final_disk_write_invocation",
    }
)
_GUARD_SUMMARIES_KEYS = frozenset({"evidence_packet", "embedded_selection"})
_GUARD_SUMMARY_KEYS = frozenset(
    {
        "match_observed",
        "unknown_timestamp_observed",
        "unexpected_normalized_path_observed",
        "differences_count",
    }
)
_SHADOW_KEYS = frozenset(
    {
        "comparison_status",
        "parity_passed",
        "comparison_complete",
        "skipped_artifact_keys",
        "mismatch_artifact_keys",
        "observatory_integration_result",
    }
)
_GROUNDING_KEYS = frozenset(
    {
        "evidence_packet_final_artifact_present",
        "evidence_packet_final_artifact_parseable",
        "compiled_support_signals_present",
        "compiled_support_signals_parseable",
        "accepted_support_signal_count",
        "grounded_memo_support_present",
        "evidence_packet_mapping_available",
    }
)
_PERMISSION_KEYS = frozenset(
    {
        "research_state",
        "research_availability_state",
        "allowed_actions",
        "new_buy_allowed",
        "order_compilation_allowed",
        "permission_context_consistent",
    }
)

_DIAGNOSTIC_COLLECTIONS = (
    "missing_observation_fields",
    "malformed_observation_fields",
    "compatibility_blockers",
    "permission_context_inconsistencies",
)

# Safe diagnostic entry: lowercase dotted / colon tokens only (no path
# separators, whitespace, or raw content can match).
_DIAGNOSTIC_ENTRY_RE = re.compile(r"[a-z][a-z0-9_]*([.:][a-z0-9_]+)*\Z")
# Conservative prose domain for the two fixed identity_semantics descriptions:
# ASCII letters/spaces/basic punctuation only - no path separators, brackets,
# braces, or control characters that could carry injected raw content.
_PROSE_RE = re.compile(r"[A-Za-z0-9 ,.;:'\"()\-]{1,400}\Z")
_NON_EMPTY_ERROR_TOKENS = frozenset(
    t for t in p1a.KNOWN_CANONICAL_ERROR_TOKENS if t != ""
)


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of classifying one parsed observation payload."""

    decision: str  # accepted / quarantined / rejected
    reason_tokens: tuple[str, ...]  # sorted, deduped, canonical, non-raw
    recomputed_identity: Mapping[str, Any]  # composite / coverage_key / observation_id


class _Acc:
    """Accumulates reject-class and quarantine-class reason tokens."""

    def __init__(self) -> None:
        self.reject: set[str] = set()
        self.quarantine: set[str] = set()

    def bad_field(self, field: str) -> None:
        self.reject.add(c.field_domain_invalid(field))

    def bad_struct(self, section: str) -> None:
        self.reject.add(c.nested_structure_invalid(section))


def classify_observation(payload: Any) -> ValidationResult:
    """Validate a parsed observation and classify it, never raising."""
    try:
        return _classify(payload)
    except Exception:  # noqa: BLE001 - hostile input must never crash the tool
        return ValidationResult(
            decision=c.DECISION_REJECTED,
            reason_tokens=(c.nested_structure_invalid("observation"),),
            recomputed_identity=_empty_identity(),
        )


def _classify(payload: Any) -> ValidationResult:
    acc = _Acc()

    if not isinstance(payload, Mapping):
        return _reject({c.REASON_SOURCE_NOT_JSON_OBJECT})

    # Schema version must be exactly the recognized source contract.
    if payload.get("schema_version") != c.SOURCE_SCHEMA_VERSION:
        return _reject({c.REASON_SCHEMA_VERSION_UNRECOGNIZED})

    keys = frozenset(payload.keys())
    if keys == _FULL_TOP_LEVEL_KEYS:
        shape = "full"
    elif keys == _MINIMAL_TOP_LEVEL_KEYS:
        shape = "minimal"
    else:
        # Unknown shape: unexpected or missing top-level keys.
        return _reject({c.REASON_TOP_LEVEL_KEYS_INVALID})

    _check_authority_envelope(payload, acc)
    _check_readiness_denylist(payload, acc)

    if shape == "full":
        _check_full(payload, acc)
    else:
        _check_minimal(payload, acc)

    recomputed = p1a.recompute_observation_identity(payload)
    _check_recomputed_identity(payload, recomputed, acc)

    if acc.reject:
        return ValidationResult(
            decision=c.DECISION_REJECTED,
            reason_tokens=_sorted(acc.reject),
            recomputed_identity=recomputed,
        )
    if acc.quarantine:
        return ValidationResult(
            decision=c.DECISION_QUARANTINED,
            reason_tokens=_sorted(acc.quarantine),
            recomputed_identity=recomputed,
        )
    return ValidationResult(
        decision=c.DECISION_ACCEPTED,
        reason_tokens=(),
        recomputed_identity=recomputed,
    )


# --- shared checks -----------------------------------------------------------
def _check_authority_envelope(payload: Mapping[str, Any], acc: _Acc) -> None:
    for key, expected in _AUTHORITY_ENVELOPE.items():
        value = payload.get(key)
        # Exact identity match, and bool must be a real bool (not 0/1).
        if isinstance(expected, bool):
            if not (isinstance(value, bool) and value is expected):
                acc.reject.add(c.REASON_AUTHORITY_ENVELOPE_VIOLATION)
        elif value != expected:
            acc.reject.add(c.REASON_AUTHORITY_ENVELOPE_VIOLATION)


def _check_readiness_denylist(payload: Mapping[str, Any], acc: _Acc) -> None:
    try:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        acc.reject.add(c.REASON_RAW_CONTENT_UNSAFE)
        return
    for token in c.READINESS_DENYLIST:
        if token in serialized:
            acc.reject.add(c.REASON_READINESS_CLAIM_PRESENT)
            return


def _check_diagnostics(payload: Mapping[str, Any], acc: _Acc) -> None:
    for name in _DIAGNOSTIC_COLLECTIONS:
        value = payload.get(name)
        if not _is_safe_token_list(value):
            acc.bad_struct(name)


def _check_completeness_consistency(payload: Mapping[str, Any], acc: _Acc) -> None:
    """Reject a completeness value that contradicts the four diagnostics.

    Phase 1A derives this field; it is not an independent claim.  Keep this
    check shared by both the full observation and the recognized minimal
    builder-error shape so every contradictory payload receives the same
    code-owned rejection token.
    """
    completeness = payload.get("observation_completeness")
    if completeness not in ("complete", "incomplete"):
        acc.bad_field("observation_completeness")
        return
    # F1: the committed Phase 1A writer derives completeness from the four
    # diagnostic collections (complete iff all four are empty).  A payload whose
    # completeness field contradicts its own collections is fabricated and must
    # be rejected - never accepted, quarantined, or silently repaired.  The
    # check runs only when all four collections are lists; malformed collections
    # are already rejected structurally by _check_diagnostics.
    collections = [payload.get(name) for name in _DIAGNOSTIC_COLLECTIONS]
    if all(isinstance(value, list) for value in collections):
        any_diagnostics = any(len(value) > 0 for value in collections)
        if completeness == "complete" and any_diagnostics:
            acc.reject.add(c.REASON_OBSERVATION_COMPLETENESS_INCONSISTENT)
        elif completeness == "incomplete" and not any_diagnostics:
            acc.reject.add(c.REASON_OBSERVATION_COMPLETENESS_INCONSISTENT)


def _quarantine_incomplete_observation(payload: Mapping[str, Any], acc: _Acc) -> None:
    """Mark a structurally valid full incomplete observation non-countable."""
    completeness = payload.get("observation_completeness")
    if completeness == "incomplete":
        acc.quarantine.add(c.REASON_OBSERVATION_INCOMPLETE)


# --- minimal shape -----------------------------------------------------------
def _check_minimal(payload: Mapping[str, Any], acc: _Acc) -> None:
    identity = payload.get("observation_identity")
    if _is_exact_mapping(identity, _OBSERVATION_IDENTITY_KEYS):
        if identity.get("observation_id") is not None:
            acc.bad_field("observation_identity.observation_id")
        if not _is_generated_at_or_none(identity.get("generated_at")):
            acc.bad_field("observation_identity.generated_at")
    else:
        acc.bad_struct("observation_identity")

    coverage = payload.get("coverage_identity")
    if _is_exact_mapping(coverage, _COVERAGE_IDENTITY_KEYS):
        if coverage.get("coverage_key") is not None:
            acc.bad_field("coverage_identity.coverage_key")
        if coverage.get("composite_config_fingerprint") is not None:
            acc.bad_field("coverage_identity.composite_config_fingerprint")
    else:
        acc.bad_struct("coverage_identity")

    _check_diagnostics(payload, acc)
    _check_completeness_consistency(payload, acc)
    # The minimal writer emits exactly this fixed marker; anything else is not a
    # genuine builder-internal-error observation.
    if payload.get("missing_observation_fields") != ["builder_internal_error"]:
        acc.bad_field("missing_observation_fields")
    for empty in (
        "malformed_observation_fields",
        "compatibility_blockers",
        "permission_context_inconsistencies",
    ):
        if payload.get(empty) != []:
            acc.bad_field(empty)
    if payload.get("observation_completeness") != "incomplete":
        acc.bad_field("observation_completeness")

    if not acc.reject:
        acc.quarantine.add(c.REASON_BUILDER_INTERNAL_ERROR_OBSERVATION)


# --- full shape --------------------------------------------------------------
def _check_full(payload: Mapping[str, Any], acc: _Acc) -> None:
    _check_identity_sections(payload, acc)
    _check_identity_semantics(payload, acc)
    if payload.get("classification_contract_version") != p1a.CLASSIFICATION_CONTRACT_VERSION:
        acc.bad_field("classification_contract_version")
    _check_code_identity(payload, acc)
    _check_contract_versions(payload, acc)
    _check_configuration_hashes(payload, acc)
    _check_input_state(payload, acc)
    _check_writer_outcomes(payload, acc)
    _check_fallback_error_tokens(payload, acc)
    _check_guard_summaries(payload, acc)
    _check_shadow(payload, acc)
    _check_grounding(payload, acc)
    _check_permission(payload, acc)
    _check_diagnostics(payload, acc)
    _check_completeness_consistency(payload, acc)
    _quarantine_incomplete_observation(payload, acc)


def _check_identity_sections(payload: Mapping[str, Any], acc: _Acc) -> None:
    identity = payload.get("observation_identity")
    if _is_exact_mapping(identity, _OBSERVATION_IDENTITY_KEYS):
        if not _is_sha256_or_none(identity.get("observation_id")):
            acc.bad_field("observation_identity.observation_id")
        if not _is_generated_at_or_none(identity.get("generated_at")):
            acc.bad_field("observation_identity.generated_at")
    else:
        acc.bad_struct("observation_identity")

    coverage = payload.get("coverage_identity")
    if _is_exact_mapping(coverage, _COVERAGE_IDENTITY_KEYS):
        if not _is_sha256_or_none(coverage.get("coverage_key")):
            acc.bad_field("coverage_identity.coverage_key")
        if not _is_sha256_or_none(coverage.get("composite_config_fingerprint")):
            acc.bad_field("coverage_identity.composite_config_fingerprint")
    else:
        acc.bad_struct("coverage_identity")


def _check_identity_semantics(payload: Mapping[str, Any], acc: _Acc) -> None:
    semantics = payload.get("identity_semantics")
    if not _is_exact_mapping(semantics, _IDENTITY_SEMANTICS_KEYS):
        acc.bad_struct("identity_semantics")
        return
    for key in ("observation_id", "coverage_key"):
        value = semantics.get(key)
        if not (isinstance(value, str) and _PROSE_RE.fullmatch(value)):
            acc.bad_field(f"identity_semantics.{key}")


def _check_code_identity(payload: Mapping[str, Any], acc: _Acc) -> None:
    code = payload.get("code_identity")
    if not _is_exact_mapping(code, _CODE_IDENTITY_KEYS):
        acc.bad_struct("code_identity")
        return
    state = code.get("git_state")
    commit = code.get("git_commit")
    usable = code.get("code_version_usable_for_evidence")
    if state not in p1a.VALID_CODE_IDENTITY_STATES:
        acc.bad_field("code_identity.git_state")
    if not (commit is None or p1a.is_git_commit(commit)):
        acc.bad_field("code_identity.git_commit")
    if not isinstance(usable, bool):
        acc.bad_field("code_identity.code_version_usable_for_evidence")
    else:
        # Consistency with the committed derivation.
        expected_usable = state == "clean" and p1a.is_git_commit(commit)
        if usable is not expected_usable:
            acc.bad_field("code_identity.code_version_usable_for_evidence")
    # Quarantine (non-countable) signals - only meaningful once structure is ok.
    if not acc.reject:
        if state == "dirty":
            acc.quarantine.add(c.REASON_CODE_IDENTITY_DIRTY)
        elif state == "unavailable":
            acc.quarantine.add(c.REASON_CODE_IDENTITY_UNAVAILABLE)
        if usable is False:
            acc.quarantine.add(c.REASON_CODE_VERSION_NOT_USABLE)


def _check_contract_versions(payload: Mapping[str, Any], acc: _Acc) -> None:
    versions = payload.get("contract_versions")
    expected_keys = frozenset(p1a.EXPECTED_SCHEMA_VERSIONS.keys())
    if not _is_exact_mapping(versions, expected_keys):
        acc.bad_struct("contract_versions")
        return
    for name in expected_keys:
        value = versions.get(name)
        if not (value is None or p1a.is_safe_diagnostic_token(value)):
            acc.bad_field(f"contract_versions.{name}")


def _check_configuration_hashes(payload: Mapping[str, Any], acc: _Acc) -> None:
    hashes = payload.get("configuration_hashes")
    if not _is_exact_mapping(hashes, _CONFIGURATION_HASHES_KEYS):
        acc.bad_struct("configuration_hashes")
        return
    for name in _CONFIGURATION_HASHES_KEYS:
        if not _is_sha256_or_none(hashes.get(name)):
            acc.bad_field(f"configuration_hashes.{name}")


def _check_input_state(payload: Mapping[str, Any], acc: _Acc) -> None:
    state = payload.get("input_state_observations")
    if not _is_exact_mapping(state, _INPUT_STATE_KEYS):
        acc.bad_struct("input_state_observations")
        return
    checks = {
        "as_of_input_class": lambda v: v in p1a.VALID_AS_OF_INPUT_CLASSES,
        "approvals_state": lambda v: v in p1a.VALID_MANIFEST_STATES,
        "revocations_state": lambda v: v in p1a.VALID_MANIFEST_STATES,
        "selected_source_class": lambda v: v is None or v in p1a.VALID_SELECTED_SOURCES,
        "snapshot_state": lambda v: v in p1a.VALID_BOOL_STATES,
        "last_good_availability": lambda v: v in p1a.VALID_BOOL_STATES,
        "production_observatory_mapping_available": lambda v: isinstance(v, bool),
    }
    for name, ok in checks.items():
        if not ok(state.get(name)):
            acc.bad_field(f"input_state_observations.{name}")


def _check_writer_outcomes(payload: Mapping[str, Any], acc: _Acc) -> None:
    outcomes = payload.get("writer_outcomes")
    if not _is_exact_mapping(outcomes, _WRITER_OUTCOMES_KEYS):
        acc.bad_struct("writer_outcomes")
        return
    for slot in _WRITER_OUTCOMES_KEYS:
        _check_writer_outcome(outcomes.get(slot), f"writer_outcomes.{slot}", acc)


def _check_writer_outcome(outcome: Any, field: str, acc: _Acc) -> None:
    if not _is_exact_mapping(outcome, _WRITER_OUTCOME_KEYS):
        acc.bad_struct(field)
        return
    src = outcome.get("final_writer_source")
    if not (src is None or src in p1a.VALID_WRITER_SOURCES):
        acc.bad_field(f"{field}.final_writer_source")
    if not _is_bool_or_none(outcome.get("fallback_used")):
        acc.bad_field(f"{field}.fallback_used")
    token = outcome.get("canonical_error_token")
    if not (token is None or token in p1a.KNOWN_CANONICAL_ERROR_TOKENS):
        acc.bad_field(f"{field}.canonical_error_token")
    if not _is_bool_or_none(outcome.get("unknown_error_present")):
        acc.bad_field(f"{field}.unknown_error_present")
    if not _is_sha256_or_none(outcome.get("error_summary_sha256")):
        acc.bad_field(f"{field}.error_summary_sha256")
    if not _is_int_or_none(outcome.get("invocation_count")):
        acc.bad_field(f"{field}.invocation_count")
    if not _is_bool_or_none(outcome.get("first_final_status_divergence")):
        acc.bad_field(f"{field}.first_final_status_divergence")
    if not _is_int_or_none(outcome.get("final_disk_write_invocation")):
        acc.bad_field(f"{field}.final_disk_write_invocation")


def _check_fallback_error_tokens(payload: Mapping[str, Any], acc: _Acc) -> None:
    tokens = payload.get("fallback_error_tokens")
    if not isinstance(tokens, list):
        acc.bad_struct("fallback_error_tokens")
        return
    if not all(isinstance(t, str) and t in _NON_EMPTY_ERROR_TOKENS for t in tokens):
        acc.bad_field("fallback_error_tokens")
    elif list(tokens) != sorted(set(tokens)):
        acc.bad_struct("fallback_error_tokens")


def _check_guard_summaries(payload: Mapping[str, Any], acc: _Acc) -> None:
    summaries = payload.get("guard_summaries")
    if not _is_exact_mapping(summaries, _GUARD_SUMMARIES_KEYS):
        acc.bad_struct("guard_summaries")
        return
    for slot in _GUARD_SUMMARIES_KEYS:
        _check_guard_summary(summaries.get(slot), f"guard_summaries.{slot}", acc)


def _check_guard_summary(summary: Any, field: str, acc: _Acc) -> None:
    if not _is_exact_mapping(summary, _GUARD_SUMMARY_KEYS):
        acc.bad_struct(field)
        return
    for name in ("match_observed", "unknown_timestamp_observed", "unexpected_normalized_path_observed"):
        if not _is_bool_or_none(summary.get(name)):
            acc.bad_field(f"{field}.{name}")
    if not _is_int_or_none(summary.get("differences_count")):
        acc.bad_field(f"{field}.differences_count")


def _check_shadow(payload: Mapping[str, Any], acc: _Acc) -> None:
    shadow = payload.get("shadow_and_observatory_observation")
    if not _is_exact_mapping(shadow, _SHADOW_KEYS):
        acc.bad_struct("shadow_and_observatory_observation")
        return
    status = shadow.get("comparison_status")
    if not (status is None or status in p1a.VALID_SHADOW_STATUSES):
        acc.bad_field("shadow_and_observatory_observation.comparison_status")
    if not _is_bool_or_none(shadow.get("parity_passed")):
        acc.bad_field("shadow_and_observatory_observation.parity_passed")
    if not _is_bool_or_none(shadow.get("comparison_complete")):
        acc.bad_field("shadow_and_observatory_observation.comparison_complete")
    for name in ("skipped_artifact_keys", "mismatch_artifact_keys"):
        value = shadow.get(name)
        if value is None:
            continue
        if not _is_safe_token_list(value):
            acc.bad_field(f"shadow_and_observatory_observation.{name}")
    integration = shadow.get("observatory_integration_result")
    if not (integration is None or integration in p1a.VALID_OBSERVATORY_INTEGRATION_RESULTS):
        acc.bad_field("shadow_and_observatory_observation.observatory_integration_result")


def _check_grounding(payload: Mapping[str, Any], acc: _Acc) -> None:
    grounding = payload.get("grounding_observation")
    if not _is_exact_mapping(grounding, _GROUNDING_KEYS):
        acc.bad_struct("grounding_observation")
        return
    for name in (
        "evidence_packet_final_artifact_present",
        "evidence_packet_final_artifact_parseable",
        "compiled_support_signals_present",
        "compiled_support_signals_parseable",
        "grounded_memo_support_present",
    ):
        if not _is_bool_or_none(grounding.get(name)):
            acc.bad_field(f"grounding_observation.{name}")
    if not _is_int_or_none(grounding.get("accepted_support_signal_count")):
        acc.bad_field("grounding_observation.accepted_support_signal_count")
    if not isinstance(grounding.get("evidence_packet_mapping_available"), bool):
        acc.bad_field("grounding_observation.evidence_packet_mapping_available")


def _check_permission(payload: Mapping[str, Any], acc: _Acc) -> None:
    permission = payload.get("permission_context_observation")
    if not _is_exact_mapping(permission, _PERMISSION_KEYS):
        acc.bad_struct("permission_context_observation")
        return
    state = permission.get("research_state")
    if not (state is None or state in p1a.VALID_RESEARCH_STATES):
        acc.bad_field("permission_context_observation.research_state")
    avail = permission.get("research_availability_state")
    if not (avail is None or p1a.is_safe_diagnostic_token(avail)):
        acc.bad_field("permission_context_observation.research_availability_state")
    actions = permission.get("allowed_actions")
    if actions is not None:
        if not (
            isinstance(actions, list)
            and all(isinstance(a, str) and a in p1a.VALID_ALLOWED_ACTIONS for a in actions)
            and len(actions) == len(set(actions))
        ):
            acc.bad_field("permission_context_observation.allowed_actions")
    for name in ("new_buy_allowed", "order_compilation_allowed", "permission_context_consistent"):
        if not _is_bool_or_none(permission.get(name)):
            acc.bad_field(f"permission_context_observation.{name}")


def _check_recomputed_identity(
    payload: Mapping[str, Any],
    recomputed: Mapping[str, Any],
    acc: _Acc,
) -> None:
    """Compare recomputed derived hashes against stored values."""
    coverage = payload.get("coverage_identity")
    identity = payload.get("observation_identity")
    if not (isinstance(coverage, Mapping) and isinstance(identity, Mapping)):
        # Structure already flagged; nothing safe to compare against.
        return
    if recomputed.get("composite_config_fingerprint") != coverage.get(
        "composite_config_fingerprint"
    ):
        acc.reject.add(c.REASON_COMPOSITE_FINGERPRINT_MISMATCH)
    if recomputed.get("coverage_key") != coverage.get("coverage_key"):
        acc.reject.add(c.REASON_COVERAGE_KEY_MISMATCH)
    if recomputed.get("observation_id") != identity.get("observation_id"):
        acc.reject.add(c.REASON_OBSERVATION_ID_MISMATCH)


# --- small domain predicates -------------------------------------------------
def _is_exact_mapping(value: Any, keys: frozenset[str]) -> bool:
    return isinstance(value, Mapping) and frozenset(value.keys()) == keys


def _is_sha256_or_none(value: Any) -> bool:
    return value is None or p1a.is_canonical_sha256(value)


def _is_bool_or_none(value: Any) -> bool:
    return value is None or isinstance(value, bool)


def _is_int_or_none(value: Any) -> bool:
    return value is None or p1a.is_non_negative_int(value)


def _is_generated_at_or_none(value: Any) -> bool:
    return value is None or p1a.is_valid_generated_at(value)


def _is_safe_token_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    if not all(isinstance(x, str) and _DIAGNOSTIC_ENTRY_RE.fullmatch(x) for x in value):
        return False
    return value == sorted(set(value))


def _empty_identity() -> dict[str, Any]:
    return {
        "composite_config_fingerprint": None,
        "coverage_key": None,
        "observation_id": None,
    }


def _reject(tokens: set[str]) -> ValidationResult:
    return ValidationResult(
        decision=c.DECISION_REJECTED,
        reason_tokens=_sorted(tokens),
        recomputed_identity=_empty_identity(),
    )


def _sorted(tokens: set[str]) -> tuple[str, ...]:
    return tuple(sorted(tokens))
