"""Frozen WEEKLY-SHADOW-01 static contract and identity foundation (WS01a/WS01a2).

This module owns only the static, code-owned vocabulary, resource-bound,
negative-authority, and identity primitives for WEEKLY-SHADOW-01.  It defines
pure, deterministic canonicalization and domain-separated identity helpers and
freezes the immutable prompt-template bytes.  It does not implement an R2F
adapter, source verifier, package builder, response importer, validator,
publisher, or CLI; it does not read or write any runtime artifact; it has no
production consumer; and it grants no authority, permission, approval,
portfolio, order, or execution effect.
"""

from __future__ import annotations

# ``from __future__ import annotations`` leaves this implementation marker in
# the module namespace on supported interpreters.  The frozen public surface is
# deliberately closed, so remove the marker after the compiler has applied it.
del annotations

import hashlib as _hashlib
import json as _json
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from typing import Final, Mapping, Sequence


class CanonicalizationError(ValueError):
    """Raised when a value cannot satisfy the frozen canonical JSON profile."""


class IdentityDefinitionError(ValueError):
    """Raised when an identity payload or frozen identity binding is invalid."""


# --- pure canonicalization -----------------------------------------------

_MAX_NESTING_DEPTH: Final = 16
_MAX_OBJECT_MEMBERS: Final = 1_024
_MAX_ARRAY_ITEMS: Final = 1_024


def _validate_exact_json_value(value: object, *, depth: int = 1) -> None:
    """Reject anything outside the frozen exact-type canonical JSON profile."""
    if depth > _MAX_NESTING_DEPTH:
        raise CanonicalizationError("JSON_DEPTH_BOUND_EXCEEDED")
    value_type = type(value)
    if value_type is dict:
        if len(value) > _MAX_OBJECT_MEMBERS:
            raise CanonicalizationError("JSON_OBJECT_MEMBER_BOUND_EXCEEDED")
        for key, member in value.items():
            if type(key) is not str:
                raise CanonicalizationError("OBJECT_KEY_TYPE_INVALID")
            if not key.isascii():
                raise CanonicalizationError("OBJECT_KEY_NON_ASCII")
            _validate_exact_json_value(member, depth=depth + 1)
    elif value_type is list:
        if len(value) > _MAX_ARRAY_ITEMS:
            raise CanonicalizationError("JSON_ARRAY_ITEM_BOUND_EXCEEDED")
        for member in value:
            _validate_exact_json_value(member, depth=depth + 1)
    elif value_type is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise CanonicalizationError("SURROGATE_NOT_ALLOWED")
    elif value_type in (int, bool, type(None)):
        pass
    elif value_type is float:
        raise CanonicalizationError("FLOAT_NOT_ALLOWED")
    else:
        raise CanonicalizationError(f"JSON_EXACT_TYPE_INVALID:{value_type.__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Return the exact canonical JSON serialization for an accepted value."""
    _validate_exact_json_value(value)
    return _json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def domain_separated_sha256(domain_separator: bytes, payload: object) -> str:
    """Hash a canonical payload under an unambiguous NUL-terminated domain."""
    if type(domain_separator) is not bytes:
        raise IdentityDefinitionError("DOMAIN_SEPARATOR_TYPE_INVALID")
    if not domain_separator or not domain_separator.endswith(b"\0") or b"\0" in domain_separator[:-1]:
        raise IdentityDefinitionError("DOMAIN_SEPARATOR_FORMAT_INVALID")
    return _hashlib.sha256(domain_separator + canonical_json_bytes(payload)).hexdigest()


def compute_identity(
    domain_name: str,
    payload: "Mapping[str, object]",
    *,
    exclude_fields: "Sequence[str]" = (),
) -> str:
    """Compute a domain-separated identity, excluding self-identity fields.

    ``payload`` is never mutated; a detached copy without ``exclude_fields`` is
    hashed instead.
    """
    if type(domain_name) is not str or domain_name not in DOMAIN_SEPARATORS:
        raise IdentityDefinitionError("DOMAIN_NAME_UNKNOWN")
    if type(payload) is not dict:
        raise IdentityDefinitionError("IDENTITY_PAYLOAD_TYPE_INVALID")
    detached = {key: member for key, member in payload.items() if key not in set(exclude_fields)}
    return domain_separated_sha256(DOMAIN_SEPARATORS[domain_name], detached)


# --- identity domains ------------------------------------------------------

DOMAIN_SEPARATORS: Final = _MappingProxyType(
    {
        "source_artifact": b"weekly_shadow_01_source_artifact_v1\0",
        "evidence_record": b"weekly_shadow_01_evidence_record_v1\0",
        "run": b"weekly_shadow_01_run_v1\0",
        "input_package": b"weekly_shadow_01_input_package_v1\0",
        "prompt_template": b"weekly_shadow_01_prompt_template_v1\0",
        "prompt_render": b"weekly_shadow_01_prompt_render_v1\0",
        "response_capture": b"weekly_shadow_01_response_capture_v1\0",
        "validation": b"weekly_shadow_01_validation_v1\0",
        "report": b"weekly_shadow_01_report_v1\0",
        "run_summary": b"weekly_shadow_01_run_summary_v1\0",
        "schema_identity": b"weekly_shadow_01_schema_identity_v1\0",
        "semantic_contract_identity": b"weekly_shadow_01_semantic_contract_identity_v1\0",
        "resource_bound_profile": b"weekly_shadow_01_resource_bound_profile_v1\0",
        "negative_authority_profile": b"weekly_shadow_01_negative_authority_profile_v1\0",
        "vocabulary_profile": b"weekly_shadow_01_vocabulary_profile_v1\0",
        "contract_catalog": b"weekly_shadow_01_contract_catalog_v1\0",
    }
)


def _validate_domain_separators() -> None:
    values = tuple(DOMAIN_SEPARATORS.values())
    if len(values) != len(set(values)):
        raise IdentityDefinitionError("DOMAIN_SEPARATOR_DUPLICATE")
    for name, value in DOMAIN_SEPARATORS.items():
        if type(name) is not str or not name.isascii() or type(value) is not bytes:
            raise IdentityDefinitionError("DOMAIN_SEPARATOR_TYPE_INVALID")
        if not value or not value.endswith(b"\0") or b"\0" in value[:-1]:
            raise IdentityDefinitionError("DOMAIN_SEPARATOR_FORMAT_INVALID")


_validate_domain_separators()


# --- code-owned run-status vocabulary --------------------------------------

RUN_STATUS_VALUES: Final = ("ANALYSIS_COMPLETE", "BLOCKED")

# --- analyst-owned qualitative vocabularies ---------------------------------

ANALYST_CONCLUSION_VALUES: Final = (
    "OBSERVATIONS_AVAILABLE",
    "NO_CHANGE_JUSTIFIED",
    "INSUFFICIENT_EVIDENCE_FOR_CONCLUSION",
)

ANALYST_CONCLUSION_SEMANTICS: Final = _MappingProxyType(
    {
        "OBSERVATIONS_AVAILABLE": _MappingProxyType(
            {
                "meaning": (
                    "evidence_bound_observations_risks_uncertainties_or_"
                    "thesis_implications_are_available"
                ),
                "creates_action": False,
                "creates_state": False,
            }
        ),
        "NO_CHANGE_JUSTIFIED": _MappingProxyType(
            {
                "meaning": (
                    "the_analyst_did_not_identify_supplied_evidence_that_"
                    "supports_changing_the_long_term_thesis"
                ),
                "is_not": (
                    "NO_TRADE",
                    "HOLD",
                    "portfolio_unchanged",
                    "rebalance_not_required",
                    "permission_denied",
                    "gate_blocked",
                ),
                "creates_action": False,
                "creates_state": False,
            }
        ),
        "INSUFFICIENT_EVIDENCE_FOR_CONCLUSION": _MappingProxyType(
            {
                "meaning": (
                    "the_analyst_qualitatively_considers_the_supplied_evidence_"
                    "insufficient_for_a_substantive_analytical_conclusion"
                ),
                "compatible_run_status": "ANALYSIS_COMPLETE",
                "creates_action": False,
                "creates_state": False,
            }
        ),
    }
)

ANALYST_CONFIDENCE_VALUES: Final = ("LOW", "MEDIUM", "HIGH")

PROHIBITED_ANALYST_CONCLUSION_VALUES: Final = (
    "HOLD",
    "NO_TRADE",
    "BUY",
    "SELL",
    "NEW_BUY",
    "BLOCKED",
    "APPROVED",
    "REJECTED",
    "ELIGIBLE",
    "INELIGIBLE",
)

# --- code-owned validation / publication vocabularies -----------------------

VALIDATION_STATUS_VALUES: Final = ("VALID", "INVALID")
PUBLICATION_STATUS_VALUES: Final = ("NOT_ATTEMPTED", "PUBLISHED", "FAILED")

# --- code-owned blocking-reason vocabulary (WS01_BR_*) ----------------------

BLOCKING_REASON_CODES: Final = (
    "WS01_BR_SOURCE_GENERATION_INVALID",
    "WS01_BR_SOURCE_ARTIFACT_SET_MISMATCH",
    "WS01_BR_SOURCE_VERSION_UNSUPPORTED",
    "WS01_BR_SOURCE_READ_UNSTABLE",
    "WS01_BR_SOURCE_BINDING_MISMATCH",
    "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
    "WS01_BR_RESOURCE_BOUND_EXCEEDED",
    "WS01_BR_RESPONSE_MISSING",
    "WS01_BR_RESPONSE_UNREADABLE",
    "WS01_BR_RESPONSE_OVERSIZED",
    "WS01_BR_RESPONSE_PARSE_FAILED",
    "WS01_BR_RESPONSE_DUPLICATE_KEY",
    "WS01_BR_RESPONSE_SCHEMA_INVALID",
    "WS01_BR_RUN_BINDING_MISMATCH",
    "WS01_BR_PACKAGE_BINDING_MISMATCH",
    "WS01_BR_PROMPT_TEMPLATE_BINDING_MISMATCH",
    "WS01_BR_SOURCE_GENERATION_BINDING_MISMATCH",
    "WS01_BR_ARTIFACT_ECHO_INCOMPLETE",
    "WS01_BR_ARTIFACT_ECHO_UNEXPECTED",
    "WS01_BR_EVIDENCE_ECHO_INCOMPLETE",
    "WS01_BR_EVIDENCE_ECHO_UNEXPECTED",
    "WS01_BR_EVIDENCE_REFERENCE_INVALID",
    "WS01_BR_PROHIBITED_KEY",
    "WS01_BR_PROHIBITED_INTENT",
    "WS01_BR_CROSS_FIELD_INVALID",
    "WS01_BR_REPORT_CONSTRUCTION_FAILED",
    "WS01_BR_REPORT_IDENTITY_FAILURE",
    "WS01_BR_PUBLICATION_FAILED",
    "WS01_BR_PUBLICATION_CONFLICT",
    "WS01_BR_PUBLICATION_AMBIGUOUS",
    "WS01_BR_IMMUTABLE_VERIFICATION_FAILED",
    "WS01_BR_INTERNAL_INVARIANT_FAILURE",
)

# --- analyst-owned qualitative limitation vocabulary (WS01_AL_*) ------------

ANALYST_LIMITATION_CODES: Final = (
    "WS01_AL_EVIDENCE_SPARSE",
    "WS01_AL_EVIDENCE_PERCEIVED_STALE",
    "WS01_AL_SCOPE_NARROW",
    "WS01_AL_SOURCE_COVERAGE_GAP",
    "WS01_AL_AMBIGUOUS_SIGNAL",
    "WS01_AL_FOLLOWUP_REQUIRED",
)


def _validate_vocabularies() -> None:
    for name, values in (
        ("RUN_STATUS_VALUES", RUN_STATUS_VALUES),
        ("ANALYST_CONCLUSION_VALUES", ANALYST_CONCLUSION_VALUES),
        ("ANALYST_CONFIDENCE_VALUES", ANALYST_CONFIDENCE_VALUES),
        ("PROHIBITED_ANALYST_CONCLUSION_VALUES", PROHIBITED_ANALYST_CONCLUSION_VALUES),
        ("VALIDATION_STATUS_VALUES", VALIDATION_STATUS_VALUES),
        ("PUBLICATION_STATUS_VALUES", PUBLICATION_STATUS_VALUES),
        ("BLOCKING_REASON_CODES", BLOCKING_REASON_CODES),
        ("ANALYST_LIMITATION_CODES", ANALYST_LIMITATION_CODES),
    ):
        if len(values) != len(set(values)):
            raise IdentityDefinitionError(f"VOCABULARY_DUPLICATE:{name}")
    if not all(code.startswith("WS01_BR_") for code in BLOCKING_REASON_CODES):
        raise IdentityDefinitionError("BLOCKING_REASON_PREFIX_INVALID")
    if not all(code.startswith("WS01_AL_") for code in ANALYST_LIMITATION_CODES):
        raise IdentityDefinitionError("ANALYST_LIMITATION_PREFIX_INVALID")
    if set(BLOCKING_REASON_CODES) & set(ANALYST_LIMITATION_CODES):
        raise IdentityDefinitionError("BLOCKING_REASON_AND_LIMITATION_NOT_DISJOINT")
    if set(ANALYST_CONCLUSION_VALUES) & set(PROHIBITED_ANALYST_CONCLUSION_VALUES):
        raise IdentityDefinitionError("ANALYST_CONCLUSION_PROHIBITED_OVERLAP")
    if set(RUN_STATUS_VALUES) & set(ANALYST_CONCLUSION_VALUES):
        raise IdentityDefinitionError("RUN_STATUS_ANALYST_CONCLUSION_OVERLAP")


_validate_vocabularies()


# --- negative-authority profile ---------------------------------------------

NEGATIVE_AUTHORITY_PROFILE: Final = _MappingProxyType(
    {
        "authority_effect": "none",
        "permission_effect": "none",
        "approval_eligible": False,
        "precompile_eligible": False,
        "order_eligible": False,
        "portfolio_effect": "none",
        "order_path_effect": "none",
        "execution_authority": False,
    }
)

NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256: Final = compute_identity(
    "negative_authority_profile",
    {"profile_version": "weekly_shadow_01_negative_authority_profile_v1", **NEGATIVE_AUTHORITY_PROFILE},
)


# --- WS01a2 grounding-input contract metadata -------------------------------

WEEKLY_SHADOW_STAGE_VERSION: Final = "weekly_shadow_01_stage_a_v1"
LEGACY_R2F_ADAPTER_ID: Final = "legacy_r2f_v2_to_weekly_shadow_v1"
R2F_SOURCE_GENERATION_ID_PATTERN: Final = "^[0-9a-f]{64}$"
R2F_SOURCE_GENERATION_VERSION: Final = "step1_replacement_render_observation_v2"

CONSUMED_SOURCE_ARTIFACT_ROLES: Final = (
    "replacement_input_manifest.json",
    "evidence_packet.json",
    "analyst_memo_prompt.txt",
    "render_generation_binding.json",
)
PERMANENTLY_UNCONSUMED_SOURCE_ARTIFACT_ROLE: Final = "analyst_memo_raw_output.txt"
INCOMPLETE_SOURCE_GENERATION_MARKER: Final = ".render_in_progress"

EVIDENCE_VALUE_VARIANTS: Final = (
    "active_anchor_v1",
    "availability_status_v1",
    "diagnostic_code_v1",
)
ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS: Final = (
    "applicable_tickers",
    "anchor_date_et",
    "valid_from",
    "valid_until",
    "confidence_floor",
    "summary",
    "validation.stale",
)
AVAILABILITY_STATUS_NORMALIZED_VALUE_FIELDS: Final = (
    "available",
    "data_gap",
)
DIAGNOSTIC_CODE_VALUES: Final = ("EMPTY_ACTIVE_REGISTRY",)

SOURCE_LOCATOR_TYPES: Final = (
    "active_anchor_by_id",
    "availability_status",
    "manifest_diagnostic",
)
AVAILABILITY_SUBJECTS: Final = (
    "market_metrics",
    "scheduled_events_deterministic",
)
ACTIVE_ANCHOR_SOURCE_LOCATOR_CONTRACT: Final = _MappingProxyType(
    {
        "locator_type": "active_anchor_by_id",
        "source_artifact_role": "evidence_packet.json",
        "required_fields": ("locator_type", "source_artifact_role", "anchor_id"),
        "anchor_id_contract": "bounded_nonempty_source_owned_text_max_2048_code_points",
    }
)
AVAILABILITY_SOURCE_LOCATOR_CONTRACT: Final = _MappingProxyType(
    {
        "locator_type": "availability_status",
        "source_artifact_role": "evidence_packet.json",
        "required_fields": (
            "locator_type",
            "source_artifact_role",
            "availability_subject",
        ),
        "availability_subjects": AVAILABILITY_SUBJECTS,
    }
)
DIAGNOSTIC_SOURCE_LOCATOR_CONTRACT: Final = _MappingProxyType(
    {
        "locator_type": "manifest_diagnostic",
        "source_artifact_role": "replacement_input_manifest.json",
        "required_fields": (
            "locator_type",
            "source_artifact_role",
            "diagnostic_code",
        ),
        "diagnostic_code": "EMPTY_ACTIVE_REGISTRY",
        "normalized_value_present": False,
    }
)
PACKAGE_OWNED_SOURCE_CONTEXT: Final = _MappingProxyType(
    {
        "lineage_type": "verified_r2f_v2_generation",
        "package_owned_fields": (
            "source_generation_id",
            "source_generation_version",
            "source_artifact_bindings",
        ),
        "source_generation_version": R2F_SOURCE_GENERATION_VERSION,
        "record_generation_lineage": "inherited_from_package",
        "artifact_identity_resolution": (
            "locator.source_artifact_role_to_unique_package_source_artifact_binding"
        ),
    }
)
OBSOLETE_EVIDENCE_RECORD_FIELDS: Final = (
    "source_artifact_identity_sha256",
    "source_field_bindings",
    "source_lineage",
)
OBSOLETE_ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS: Final = ("anchor_id",)
SOURCE_LOCATOR_SEMANTICS: Final = (
    "each_evidence_variant_has_one_closed_source_locator",
    "locator_role_is_a_closed_consumed_source_artifact_role",
    "record_artifact_identity_is_resolved_from_the_unique_package_binding",
    "record_generation_lineage_is_inherited_from_package_context",
    "active_anchor_id_selects_one_unique_verified_source_anchor",
    "active_anchor_normalized_value_excludes_locator_anchor_id",
    "applicable_tickers_is_one_complete_ordered_source_array",
    "availability_subject_selects_one_closed_verified_source_object",
    "manifest_diagnostic_code_is_both_locator_and_sole_source_value",
    "no_arbitrary_source_paths_or_dynamic_source_indices_are_representable",
    "obsolete_duplicate_record_fields_are_rejected_by_closed_record_schemas",
)
LOGICAL_LOCATOR_DEFINITION: Final = _MappingProxyType(
    {
        "ordered_components": (
            "value_type",
            "source_locator",
            "package_source_generation_context",
            "resolved_source_artifact_binding",
        ),
        "package_source_generation_context_fields": (
            "source_generation_id",
            "source_generation_version",
        ),
        "resolved_source_artifact_binding_fields": (
            "source_id",
            "source_artifact_identity_sha256",
        ),
    }
)
LOGICAL_LOCATOR_UNIQUENESS_RULES: Final = (
    "reject_duplicate_complete_logical_locator",
    "reject_duplicate_evidence_record_id",
    "reject_one_logical_locator_with_multiple_normalized_values",
    "reject_duplicate_active_anchor_id",
    "reject_duplicate_availability_subject",
    "reject_duplicate_manifest_diagnostic",
    "reject_duplicates_before_input_package_identity_acceptance",
    "json_schema_unique_items_is_not_the_enforcement_mechanism",
    "no_first_write_last_write_merge_deduplication_or_silent_normalization",
)
EVIDENCE_VARIANT_RANKS: Final = _MappingProxyType(
    {
        "active_anchor_v1": 0,
        "availability_status_v1": 1,
        "diagnostic_code_v1": 2,
    }
)
AVAILABILITY_SUBJECT_RANKS: Final = _MappingProxyType(
    {
        "market_metrics": 0,
        "scheduled_events_deterministic": 1,
    }
)
EVIDENCE_RECORD_CANONICAL_ORDERING: Final = _MappingProxyType(
    {
        "ordering_key": (
            "variant_rank",
            "canonical_source_locator_bytes",
            "evidence_record_id",
        ),
        "direction": "ascending",
        "canonical_source_locator_encoding": "canonical_json_bytes",
        "canonical_source_locator_byte_comparison": "unsigned_lexicographic",
        "active_anchor_within_variant_order": "locator_anchor_id",
        "availability_subject_order": AVAILABILITY_SUBJECTS,
        "manifest_diagnostic_position": "single_fixed_position_under_variant_rank",
        "final_sequence_requirement": "strictly_increasing",
    }
)
CANONICAL_EVIDENCE_ORDERING_RULES: Final = (
    "construct_evidence_records_in_frozen_canonical_order",
    "reject_duplicate_canonical_ordering_keys",
    "verify_final_evidence_record_sequence_is_strictly_increasing",
    "never_rely_on_source_traversal_or_caller_mapping_order",
    "reject_caller_supplied_noncanonical_evidence_record_sequence",
    "never_silently_reorder_or_accept_a_noncanonical_input_package",
    "ws01c_never_repairs_analyst_input_evidence_record_order",
)
CANONICAL_ORDER_INDEPENDENCE_INPUTS: Final = (
    "source_json_insertion_order",
    "filesystem_enumeration_order",
    "caller_dictionary_order",
    "hash_seed",
    "locale",
    "timezone",
    "process_identity",
    "repository_path",
)
ANALYST_INPUT_SCHEMA_ENFORCED_CONSTRAINTS: Final = (
    "source_generation_id_lowercase_sha256_shape",
    "closed_source_artifact_role_membership_and_exact_four_position_binding_array",
    "closed_evidence_record_source_locator_and_normalized_value_shapes",
    "authority_effect_none",
    "evidence_records_max_items_256",
    "active_anchor_summary_max_code_points_2048",
    "active_anchor_applicable_tickers_max_items_1017",
    "availability_diagnostic_record_ids_max_items_256",
    "freshness_diagnostic_record_ids_max_items_256",
    "individual_text_array_and_object_shape_bounds",
    "closed_schema_shape_guarantees_nesting_below_max_nesting_depth",
)
WS01B_RUNTIME_DEFERRED_RESOURCE_BOUND_RESPONSIBILITIES: Final = (
    "each_source_artifact_byte_length_le_source_artifact_max_bytes",
    "combined_source_artifact_byte_length_le_source_artifacts_total_max_bytes",
    "canonical_analyst_input_package_byte_length_le_analyst_input_max_bytes",
    "rendered_analyst_prompt_byte_length_le_rendered_prompt_max_bytes",
    "combined_diagnostic_reference_union_count_le_max_diagnostics",
    "diagnostic_reference_ids_unique_across_both_arrays",
    "logical_locator_count_and_uniqueness_le_max_evidence_records",
    "aggregate_analyst_text_code_points_le_max_aggregate_analyst_text_code_points",
)
WS01B_RUNTIME_BOUND_FAILURE_CODE: Final = "WS01_BR_RESOURCE_BOUND_EXCEEDED"
STATIC_RUNTIME_RESPONSIBILITY_TABLE: Final = _MappingProxyType(
    {
        "schema_enforced": (
            "source_generation_id_shape",
            "closed_source_locator_variant",
            "source_artifact_role_membership",
            "closed_normalized_value_shape",
            "authority_effect_none",
            "individual_evidence_text_and_array_bounds",
            "individual_diagnostic_array_bounds",
            "closed_object_shapes_and_nesting",
        ),
        "ws01b_enforced": (
            "verified_source_generation_selection",
            "role_to_unique_package_artifact_resolution",
            "unique_active_anchor_lookup",
            "exact_normalized_value_equality_with_verified_source",
            "logical_locator_uniqueness",
            "evidence_record_id_uniqueness",
            "canonical_evidence_record_ordering",
            "diagnostic_reference_membership_category_cross_array_uniqueness_and_union_bound",
            "canonical_analyst_input_package_byte_bound",
            "rendered_analyst_prompt_byte_bound",
            "aggregate_analyst_input_resource_bounds",
            "record_and_package_identity_computation_before_acceptance",
        ),
        "ws01c_enforced": (
            "untrusted_analyst_response_schema_and_prohibited_content_validation",
            "analyst_response_artifact_and_evidence_identity_echo_validation",
            "analyst_response_reference_membership_validation",
            "analyst_response_negative_authority_validation",
        ),
        "not_required": (
            "record_level_source_generation_equality_reconciliation",
            "record_level_source_artifact_identity_equality_reconciliation",
            "dynamic_source_field_path_reconciliation",
            "input_package_duplicate_merge_or_deduplication",
            "ws01c_input_package_repair_reconciliation_or_reordering",
        ),
    }
)
WS01B_SOURCE_CORRELATION_RESPONSIBILITIES: Final = (
    "select_the_explicit_verified_r2f_v2_generation",
    "resolve_locator_role_to_the_unique_package_artifact_binding",
    "lookup_active_anchor_by_unique_verified_source_anchor_id",
    "copy_the_complete_permitted_normalized_value_exactly",
    "preserve_complete_ticker_array_order_and_values",
    "perform_no_coercion_defaulting_truncation_or_summarization",
    "reject_duplicate_logical_locators_and_evidence_record_ids_before_package_identity_acceptance",
    "reject_one_logical_locator_with_multiple_normalized_values",
    "reject_duplicate_active_anchor_availability_or_manifest_diagnostic_locators",
    "never_merge_deduplicate_or_choose_first_or_last_duplicate_record",
    "construct_and_verify_strictly_increasing_canonical_evidence_record_order",
    "reject_duplicate_ordering_keys_and_noncanonical_caller_sequences",
    "enforce_diagnostic_referential_category_cross_array_and_union_invariants",
    "enforce_runtime_deferred_analyst_input_resource_bounds_fail_closed",
    "compute_record_locator_record_identity_and_package_identity_from_frozen_recipes",
)
WS01C_RESPONSE_VALIDATION_RESPONSIBILITIES: Final = (
    "validate_untrusted_analyst_response_content",
    "validate_package_artifact_and_evidence_identity_echoes",
    "validate_response_evidence_references_and_negative_authority",
    "never_reconcile_or_repair_input_package_source_correlation",
)

DIAGNOSTIC_REFERENCE_INVARIANTS: Final = (
    "every_diagnostic_id_references_one_evidence_record",
    "availability_ids_reference_only_availability_or_empty_active_registry_records",
    "freshness_ids_reference_only_active_anchor_records",
    "diagnostic_id_union_count_does_not_exceed_max_diagnostics",
    "no_duplicate_id_across_availability_and_freshness_arrays",
    "referential_and_union_invariants_are_enforced_by_future_ws01b",
)

PROJECTION_EXCLUSIONS: Final = (
    "analyst_memo_prompt_prose",
    "analyst_memo_raw_output",
    "existing_analyst_recommendation_or_conclusion",
    "universe_membership",
    "allowed_buy_lists",
    "budget_or_cap_configuration",
    "allocation_configuration",
    "portfolio_positions_or_targets",
    "existing_or_proposed_orders",
    "inactive_anchor_approval_revocation_readiness_or_audit_fields",
    "permission_approval_gate_or_compiler_eligibility",
    "broker_or_execution_data",
)

EVIDENCE_RECORD_ID_RECIPE: Final = _MappingProxyType(
    {
        "domain_name": "evidence_record",
        "payload_kind": "weekly_shadow_01_evidence_record_locator_v2",
        "record_contract_version": "weekly_shadow_01_evidence_record_v2",
        "package_source_context_fields": (
            "source_generation_id",
            "source_generation_version",
        ),
        "resolved_artifact_binding_fields": (
            "source_id",
            "source_artifact_identity_sha256",
        ),
        "ordered_payload_fields": (
            "record_contract_version",
            "source_generation_id",
            "source_generation_version",
            "resolved_source_artifact_binding",
            "value_type",
            "source_locator",
        ),
        "normalized_value_included": False,
        "identifier_encoding": "ws01ev-<64-lowercase-hex-digest>",
    }
)
EVIDENCE_RECORD_IDENTITY_RECIPE: Final = _MappingProxyType(
    {
        "domain_name": "evidence_record",
        "payload_kind": "weekly_shadow_01_evidence_record_identity_v2",
        "payload_shape": (
            "payload_kind_package_source_context_resolved_artifact_binding_and_evidence_record"
        ),
        "package_source_context_fields": (
            "source_generation_id",
            "source_generation_version",
        ),
        "resolved_artifact_binding_fields": (
            "source_id",
            "source_artifact_identity_sha256",
        ),
        "ordered_payload_fields": (
            "source_generation_id",
            "source_generation_version",
            "resolved_source_artifact_binding",
            "evidence_record",
        ),
        "payload": "complete_evidence_record",
        "excluded_fields": ("evidence_record_identity_sha256",),
        "identity_encoding": "64-lowercase-hex-digest",
    }
)
INPUT_PACKAGE_IDENTITY_RECIPE: Final = _MappingProxyType(
    {
        "domain_name": "input_package",
        "payload": "complete_analyst_input_package",
        "excluded_fields": ("input_package_identity_sha256",),
        "identity_encoding": "64-lowercase-hex-digest",
    }
)


# --- resource-bound profile --------------------------------------------------

RESOURCE_BOUND_PROFILE: Final = _MappingProxyType(
    {
        "source_artifact_count": 4,
        "source_artifact_max_bytes": 1_048_576,
        "source_artifacts_total_max_bytes": 4_194_304,
        "analyst_input_max_bytes": 524_288,
        "rendered_prompt_max_bytes": 786_432,
        "raw_response_max_bytes": 131_072,
        "response_capture_max_bytes": 196_608,
        "response_validation_max_bytes": 131_072,
        "analyst_report_max_bytes": 262_144,
        "run_summary_max_bytes": 65_536,
        "max_nesting_depth": _MAX_NESTING_DEPTH,
        "max_object_members": _MAX_OBJECT_MEMBERS,
        "max_array_items": _MAX_ARRAY_ITEMS,
        "max_evidence_records": 256,
        "max_entries_per_analytical_section": 32,
        "max_total_analytical_entries": 128,
        "max_references_per_entry": 16,
        "max_diagnostics": 256,
        "max_text_code_points": 2_048,
        "max_aggregate_analyst_text_code_points": 32_768,
    }
)

# Bounds enforceable directly by a JSON Schema keyword (minItems/maxItems on a
# single named field) or by this module's pure canonicalization helper.  The
# remainder are byte-size or cross-field aggregate bounds that require reading
# real artifact bytes or summing across several fields; WS01a defines them
# without implementing their runtime enforcement (WS01b/WS01c own that).
RESOURCE_BOUND_SCHEMA_OR_HELPER_ENFORCED_FIELDS: Final = (
    "source_artifact_count",
    "raw_response_max_bytes",
    "max_nesting_depth",
    "max_object_members",
    "max_array_items",
    "max_evidence_records",
    "max_entries_per_analytical_section",
    "max_references_per_entry",
    "max_diagnostics",
    "max_text_code_points",
)

RESOURCE_BOUND_RUNTIME_DEFERRED_FIELDS: Final = (
    "source_artifact_max_bytes",
    "source_artifacts_total_max_bytes",
    "analyst_input_max_bytes",
    "rendered_prompt_max_bytes",
    "response_capture_max_bytes",
    "response_validation_max_bytes",
    "analyst_report_max_bytes",
    "run_summary_max_bytes",
    "max_total_analytical_entries",
    "max_aggregate_analyst_text_code_points",
)


def _validate_resource_bound_profile() -> None:
    if not all(type(value) is int and value > 0 for value in RESOURCE_BOUND_PROFILE.values()):
        raise IdentityDefinitionError("RESOURCE_BOUND_NOT_POSITIVE_INT")
    partition = set(RESOURCE_BOUND_SCHEMA_OR_HELPER_ENFORCED_FIELDS) | set(RESOURCE_BOUND_RUNTIME_DEFERRED_FIELDS)
    if partition != set(RESOURCE_BOUND_PROFILE):
        raise IdentityDefinitionError("RESOURCE_BOUND_PARTITION_INCOMPLETE")
    if set(RESOURCE_BOUND_SCHEMA_OR_HELPER_ENFORCED_FIELDS) & set(RESOURCE_BOUND_RUNTIME_DEFERRED_FIELDS):
        raise IdentityDefinitionError("RESOURCE_BOUND_PARTITION_NOT_DISJOINT")


_validate_resource_bound_profile()

RESOURCE_BOUND_PROFILE_IDENTITY_SHA256: Final = compute_identity(
    "resource_bound_profile",
    {"profile_version": "weekly_shadow_01_resource_bound_profile_v1", **RESOURCE_BOUND_PROFILE},
)


# --- prohibited response vocabulary profiles ---------------------------------

PROHIBITED_KEY_TERMS: Final = (
    "buy",
    "sell",
    "new_buy",
    "trade",
    "order",
    "side",
    "quantity",
    "shares",
    "weight",
    "allocation",
    "budget",
    "cap",
    "rebalance",
    "exposure",
    "approve",
    "permission",
    "eligible",
    "compile",
    "submit",
    "execute",
    "broker",
    "hold",
    "no_trade",
    "blocked",
)

PROHIBITED_INTENT_TERMS: Final = (
    "increase",
    "decrease",
    "add",
    "trim",
    "overweight",
    "underweight",
    "buy",
    "sell",
    "rebalance",
    "submit",
    "execute",
)

_PROHIBITED_PROFILE_NORMALIZATION_STEPS: Final = (
    "unicode_nfc_normalize",
    "casefold",
    "collapse_punctuation_and_separator_runs_to_single_space",
    "no_semantic_synonym_inference_beyond_the_frozen_vocabulary",
    "no_llm_driven_additions",
    "deterministic_bounded_output",
)

PROHIBITED_KEY_PROFILE_IDENTITY_SHA256: Final = compute_identity(
    "vocabulary_profile",
    {
        "profile_version": "weekly_shadow_01_prohibited_key_profile_v1",
        "terms": list(PROHIBITED_KEY_TERMS),
        "normalization_steps": list(_PROHIBITED_PROFILE_NORMALIZATION_STEPS),
        "applies_to": ["response_scanner_property_names"],
        "implemented_by": "WS01c",
    },
)

PROHIBITED_INTENT_PROFILE_IDENTITY_SHA256: Final = compute_identity(
    "vocabulary_profile",
    {
        "profile_version": "weekly_shadow_01_prohibited_intent_profile_v1",
        "terms": list(PROHIBITED_INTENT_TERMS),
        "normalization_steps": list(_PROHIBITED_PROFILE_NORMALIZATION_STEPS),
        "applies_to": ["response_scanner_free_text_content"],
        "implemented_by": "WS01c",
    },
)


# --- prompt template ---------------------------------------------------------

PROMPT_TEMPLATE_PLACEHOLDER: Final = "{{WEEKLY_SHADOW_01_INPUT_PACKAGE_JSON}}"

_PROMPT_TEMPLATE_LINES: Final = (
    "You are the WEEKLY-SHADOW-01 shadow research analyst.",
    "",
    "You supply qualitative research analysis only. You do not decide whether "
    "this run succeeds or is blocked; deterministic code alone makes that "
    "decision after you respond.",
    "",
    "You must not output a run status of any kind. You must never output any "
    "of the following labels, in any field, in any form: NO_TRADE, BLOCKED, "
    "HOLD, BUY, SELL, NEW_BUY, APPROVED, REJECTED, ELIGIBLE, INELIGIBLE, or "
    "any order, permission, or gate label.",
    "",
    "The only permitted value for \"analyst_conclusion\" is exactly one of: "
    "OBSERVATIONS_AVAILABLE, NO_CHANGE_JUSTIFIED, "
    "INSUFFICIENT_EVIDENCE_FOR_CONCLUSION.",
    "",
    "\"NO_CHANGE_JUSTIFIED\" means only that the supplied evidence did not "
    "support changing the long-term thesis. It is not a trade decision, not a "
    "HOLD decision, and not a portfolio decision of any kind.",
    "",
    "\"INSUFFICIENT_EVIDENCE_FOR_CONCLUSION\" means only that you consider the "
    "supplied evidence insufficient for a substantive analytical conclusion. "
    "It is your opinion, not a validator result; it does not block the run.",
    "",
    "The only permitted value for \"analyst_confidence\" is exactly one of: "
    "LOW, MEDIUM, HIGH. Confidence is qualitative only. It is never a number, "
    "a percentage, a probability, or a score, and it creates no threshold and "
    "no decision.",
    "",
    "Any \"analyst_limitation_codes\" you supply describe qualitative "
    "limitations you perceive in the supplied evidence only. They do not "
    "control, satisfy, or override any deterministic diagnostic, and they are "
    "never interpreted as an availability, freshness, permission, or gate "
    "outcome.",
    "",
    "Use only the evidence supplied to you below. Do not invent facts, "
    "sources, prices, dates, or events that are not present in the supplied "
    "evidence.",
    "",
    "Your entire output must be exactly one JSON object conforming to the "
    "WEEKLY-SHADOW-01 analyst-response schema, and nothing else: no Markdown, "
    "no code fences, no commentary before or after the JSON object.",
    "",
    "The supplied input package for this run is the following JSON object:",
    PROMPT_TEMPLATE_PLACEHOLDER,
)

PROMPT_TEMPLATE_TEXT: Final = "\n".join(_PROMPT_TEMPLATE_LINES) + "\n"
PROMPT_TEMPLATE_BYTES: Final = PROMPT_TEMPLATE_TEXT.encode("utf-8")


def _validate_prompt_template() -> None:
    if b"\r" in PROMPT_TEMPLATE_BYTES:
        raise IdentityDefinitionError("PROMPT_TEMPLATE_NOT_LF_ONLY")
    if PROMPT_TEMPLATE_BYTES.startswith(b"\xef\xbb\xbf"):
        raise IdentityDefinitionError("PROMPT_TEMPLATE_HAS_BOM")
    if not PROMPT_TEMPLATE_BYTES.endswith(b"\n"):
        raise IdentityDefinitionError("PROMPT_TEMPLATE_MISSING_FINAL_NEWLINE")
    placeholder_bytes = PROMPT_TEMPLATE_PLACEHOLDER.encode("utf-8")
    if PROMPT_TEMPLATE_BYTES.count(placeholder_bytes) != 1:
        raise IdentityDefinitionError("PROMPT_TEMPLATE_PLACEHOLDER_NOT_EXACTLY_ONE")
    try:
        PROMPT_TEMPLATE_BYTES.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise IdentityDefinitionError("PROMPT_TEMPLATE_NOT_UTF8") from exc


_validate_prompt_template()

PROMPT_TEMPLATE_IDENTITY_SHA256: Final = compute_identity(
    "prompt_template",
    {
        "profile_version": "weekly_shadow_01_prompt_template_v1",
        "encoding": "utf-8",
        "newline_convention": "lf_only",
        "byte_size": len(PROMPT_TEMPLATE_BYTES),
        "placeholder": PROMPT_TEMPLATE_PLACEHOLDER,
        "placeholder_occurrences": 1,
        "sha256": _hashlib.sha256(PROMPT_TEMPLATE_BYTES).hexdigest(),
    },
)


# --- vocabulary profile identities -------------------------------------------

RUN_STATUS_VOCABULARY_IDENTITY_SHA256: Final = compute_identity(
    "vocabulary_profile",
    {
        "profile_version": "weekly_shadow_01_run_status_vocabulary_v1",
        "values": list(RUN_STATUS_VALUES),
        "owner": "deterministic_code",
        "authority_effect": "none",
    },
)

ANALYST_CONCLUSION_VOCABULARY_IDENTITY_SHA256: Final = compute_identity(
    "vocabulary_profile",
    {
        "profile_version": "weekly_shadow_01_analyst_conclusion_vocabulary_v1",
        "values": list(ANALYST_CONCLUSION_VALUES),
        "prohibited_values": list(PROHIBITED_ANALYST_CONCLUSION_VALUES),
        "owner": "llm_content_validated_by_code",
        "authority_effect": "none",
    },
)

ANALYST_CONFIDENCE_VOCABULARY_IDENTITY_SHA256: Final = compute_identity(
    "vocabulary_profile",
    {
        "profile_version": "weekly_shadow_01_analyst_confidence_vocabulary_v1",
        "values": list(ANALYST_CONFIDENCE_VALUES),
        "numeric_forms_prohibited": True,
        "owner": "llm_content_validated_by_code",
        "authority_effect": "none",
    },
)

BLOCKING_REASON_VOCABULARY_IDENTITY_SHA256: Final = compute_identity(
    "vocabulary_profile",
    {
        "profile_version": "weekly_shadow_01_blocking_reason_vocabulary_v1",
        "values": list(BLOCKING_REASON_CODES),
        "owner": "deterministic_code",
        "authority_effect": "none",
    },
)

ANALYST_LIMITATION_VOCABULARY_IDENTITY_SHA256: Final = compute_identity(
    "vocabulary_profile",
    {
        "profile_version": "weekly_shadow_01_analyst_limitation_vocabulary_v1",
        "values": list(ANALYST_LIMITATION_CODES),
        "owner": "llm_content_validated_by_code",
        "authority_effect": "none",
    },
)


# --- six frozen schemas -------------------------------------------------------

SCHEMA_FILENAME_BY_VERSION: Final = _MappingProxyType(
    {
        "weekly_shadow_01_analyst_input_v2": "schemas/weekly_shadow_01_analyst_input.schema.json",
        "weekly_shadow_01_analyst_response_v2": "schemas/weekly_shadow_01_analyst_response.schema.json",
        "weekly_shadow_01_response_capture_v2": "schemas/weekly_shadow_01_response_capture.schema.json",
        "weekly_shadow_01_response_validation_v1": "schemas/weekly_shadow_01_response_validation.schema.json",
        "weekly_shadow_01_analyst_report_v1": "schemas/weekly_shadow_01_analyst_report.schema.json",
        "weekly_shadow_01_run_summary_v1": "schemas/weekly_shadow_01_run_summary.schema.json",
    }
)

# These constants are frozen against the exact repository schema bytes after
# strict decoding. They are literals so importing this module performs no I/O.
SCHEMA_IDENTITY_SHA256_BY_VERSION: Final = _MappingProxyType(
    {
        "weekly_shadow_01_analyst_input_v2": "41c6258b3d27b97554a785628ab3e990e0f1f89bbaad7d70a787dd230853f5f0",
        "weekly_shadow_01_analyst_response_v2": "3625d86dd84ae1243ccb4992e339d0935dff646c87e74f7792ecd635956ca160",
        "weekly_shadow_01_response_capture_v2": "a2f727e89e29f2a3ab9791d8274236f8481b2c30175eb07bc4d4bf458d429a95",
        "weekly_shadow_01_response_validation_v1": "2990ad8fc4f22de8b21691f54b3a967aed66e733078bd75ea74bc1330ee02f02",
        "weekly_shadow_01_analyst_report_v1": "7b415fa8eb7cb4ecce92ddf06eb394574f7d1435dd840657396dd2eeb0f4feb8",
        "weekly_shadow_01_run_summary_v1": "114e92f0d151bba7266a651172cd7dac01f9652a4c6fe47557582b10dcf706a7",
    }
)


# --- six semantic contracts ---------------------------------------------------

def _semantic_contract_record(
    schema_key: str,
    *,
    owner: str,
    relevant_blocking_reason_codes: "Sequence[str]",
    relevant_analyst_limitation_codes: "Sequence[str]",
    required_profile_identities_sha256: "Sequence[str]",
    semantic_metadata: object | None = None,
) -> "Mapping[str, object]":
    record = {
        "contract_version": f"{schema_key}_contract_v1",
        "contract_id": f"{schema_key}_semantic_contract",
        "schema_identity_sha256": SCHEMA_IDENTITY_SHA256_BY_VERSION[schema_key],
        "owner": owner,
        "ordered_relevant_blocking_reason_codes": list(relevant_blocking_reason_codes),
        "ordered_relevant_analyst_limitation_codes": list(relevant_analyst_limitation_codes),
        "required_profile_identities_sha256": list(required_profile_identities_sha256),
        "authority_effect": "none",
    }
    if semantic_metadata is not None:
        record["semantic_metadata"] = semantic_metadata
    return record


def _recipe_payload(recipe: "Mapping[str, object]") -> dict[str, object]:
    return {
        key: list(value) if type(value) is tuple else value
        for key, value in recipe.items()
    }


_ANALYST_INPUT_V2_SEMANTIC_METADATA: Final = {
    "source_generation_contract": {
        "identifier_type": "string",
        "identifier_pattern": R2F_SOURCE_GENERATION_ID_PATTERN,
        "source_generation_version": R2F_SOURCE_GENERATION_VERSION,
        "selection_owner": "future_ws01b_explicit_caller_input",
        "current_latest_active_fallback": False,
    },
    "adapter_id": LEGACY_R2F_ADAPTER_ID,
    "ordered_consumed_source_artifact_roles": list(CONSUMED_SOURCE_ARTIFACT_ROLES),
    "binding_only_source_artifact_roles": ["analyst_memo_prompt.txt"],
    "permanently_unconsumed_source_artifact_role": PERMANENTLY_UNCONSUMED_SOURCE_ARTIFACT_ROLE,
    "incomplete_source_generation_marker": INCOMPLETE_SOURCE_GENERATION_MARKER,
    "evidence_value_variants": list(EVIDENCE_VALUE_VARIANTS),
    "active_anchor_normalized_value_fields": list(ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS),
    "availability_status_normalized_value_fields": list(
        AVAILABILITY_STATUS_NORMALIZED_VALUE_FIELDS
    ),
    "diagnostic_code_values": list(DIAGNOSTIC_CODE_VALUES),
    "source_locator_contracts": {
        "active_anchor_v1": _recipe_payload(ACTIVE_ANCHOR_SOURCE_LOCATOR_CONTRACT),
        "availability_status_v1": _recipe_payload(AVAILABILITY_SOURCE_LOCATOR_CONTRACT),
        "diagnostic_code_v1": _recipe_payload(DIAGNOSTIC_SOURCE_LOCATOR_CONTRACT),
    },
    "package_owned_source_context": _recipe_payload(PACKAGE_OWNED_SOURCE_CONTEXT),
    "source_locator_semantics": list(SOURCE_LOCATOR_SEMANTICS),
    "logical_locator_definition": _recipe_payload(LOGICAL_LOCATOR_DEFINITION),
    "logical_locator_uniqueness_rules": list(LOGICAL_LOCATOR_UNIQUENESS_RULES),
    "evidence_variant_ranks": dict(EVIDENCE_VARIANT_RANKS),
    "availability_subject_ranks": dict(AVAILABILITY_SUBJECT_RANKS),
    "canonical_evidence_record_ordering": _recipe_payload(
        EVIDENCE_RECORD_CANONICAL_ORDERING
    ),
    "canonical_evidence_ordering_rules": list(CANONICAL_EVIDENCE_ORDERING_RULES),
    "canonical_order_independence_inputs": list(CANONICAL_ORDER_INDEPENDENCE_INPUTS),
    "analyst_input_schema_enforced_constraints": list(
        ANALYST_INPUT_SCHEMA_ENFORCED_CONSTRAINTS
    ),
    "future_ws01b_runtime_deferred_resource_bound_responsibilities": list(
        WS01B_RUNTIME_DEFERRED_RESOURCE_BOUND_RESPONSIBILITIES
    ),
    "future_ws01b_runtime_bound_failure_code": WS01B_RUNTIME_BOUND_FAILURE_CODE,
    "static_runtime_responsibility_table": _recipe_payload(
        STATIC_RUNTIME_RESPONSIBILITY_TABLE
    ),
    "future_ws01b_source_correlation_responsibilities": list(
        WS01B_SOURCE_CORRELATION_RESPONSIBILITIES
    ),
    "future_ws01c_response_validation_responsibilities": list(
        WS01C_RESPONSE_VALIDATION_RESPONSIBILITIES
    ),
    "obsolete_evidence_record_fields": list(OBSOLETE_EVIDENCE_RECORD_FIELDS),
    "obsolete_active_anchor_normalized_value_fields": list(
        OBSOLETE_ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS
    ),
    "diagnostic_reference_arrays": [
        "availability_diagnostic_record_ids",
        "freshness_diagnostic_record_ids",
    ],
    "diagnostic_reference_invariants": list(DIAGNOSTIC_REFERENCE_INVARIANTS),
    "diagnostic_relational_enforcement_owner": "future_ws01b",
    "projection_exclusions": list(PROJECTION_EXCLUSIONS),
    "evidence_record_id_recipe": _recipe_payload(EVIDENCE_RECORD_ID_RECIPE),
    "evidence_record_identity_recipe": _recipe_payload(EVIDENCE_RECORD_IDENTITY_RECIPE),
    "input_package_identity_recipe": _recipe_payload(INPUT_PACKAGE_IDENTITY_RECIPE),
    "stage_version_field_present": False,
    "raw_or_canonical_artifact_hash_package_fields_present": False,
    "record_level_source_lineage_present": False,
    "record_level_source_artifact_identity_present": False,
    "dynamic_source_field_bindings_present": False,
    "negative_authority_profile_hash_field_present": False,
    "authority_effect": "none",
}

_ANALYST_RESPONSE_V2_SEMANTIC_METADATA: Final = {
    "stage_version": WEEKLY_SHADOW_STAGE_VERSION,
    "source_generation_id_pattern": R2F_SOURCE_GENERATION_ID_PATTERN,
    "ordered_source_artifact_echoes": "source_id_and_identity_only",
    "ordered_evidence_record_echoes": "record_id_and_identity_only",
    "evidence_values_or_lineage_echoed": False,
    "content_trust": "untrusted_llm_content_validated_by_future_ws01c",
    "authority_effect": "none",
}

_RESPONSE_CAPTURE_V2_SEMANTIC_METADATA: Final = {
    "source_generation_id_pattern": R2F_SOURCE_GENERATION_ID_PATTERN,
    "capture_content": "exact_untrusted_response_bytes_only",
    "authority_effect": "none",
}


_SEMANTIC_CONTRACT_RECORDS: Final = _MappingProxyType(
    {
        "weekly_shadow_01_analyst_input_v2": _semantic_contract_record(
            "weekly_shadow_01_analyst_input_v2",
            owner="deterministic_code",
            relevant_blocking_reason_codes=(
                "WS01_BR_SOURCE_GENERATION_INVALID",
                "WS01_BR_SOURCE_ARTIFACT_SET_MISMATCH",
                "WS01_BR_SOURCE_VERSION_UNSUPPORTED",
                "WS01_BR_SOURCE_READ_UNSTABLE",
                "WS01_BR_SOURCE_BINDING_MISMATCH",
                "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
                "WS01_BR_RESOURCE_BOUND_EXCEEDED",
            ),
            relevant_analyst_limitation_codes=(),
            required_profile_identities_sha256=(
                RESOURCE_BOUND_PROFILE_IDENTITY_SHA256,
                PROMPT_TEMPLATE_IDENTITY_SHA256,
                NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256,
            ),
            semantic_metadata=_ANALYST_INPUT_V2_SEMANTIC_METADATA,
        ),
        "weekly_shadow_01_analyst_response_v2": _semantic_contract_record(
            "weekly_shadow_01_analyst_response_v2",
            owner="llm_content_validated_by_code",
            relevant_blocking_reason_codes=(
                "WS01_BR_RESPONSE_SCHEMA_INVALID",
                "WS01_BR_PROHIBITED_KEY",
                "WS01_BR_PROHIBITED_INTENT",
                "WS01_BR_EVIDENCE_REFERENCE_INVALID",
                "WS01_BR_ARTIFACT_ECHO_INCOMPLETE",
                "WS01_BR_ARTIFACT_ECHO_UNEXPECTED",
                "WS01_BR_EVIDENCE_ECHO_INCOMPLETE",
                "WS01_BR_EVIDENCE_ECHO_UNEXPECTED",
                "WS01_BR_CROSS_FIELD_INVALID",
                "WS01_BR_RUN_BINDING_MISMATCH",
                "WS01_BR_PACKAGE_BINDING_MISMATCH",
                "WS01_BR_PROMPT_TEMPLATE_BINDING_MISMATCH",
                "WS01_BR_SOURCE_GENERATION_BINDING_MISMATCH",
            ),
            relevant_analyst_limitation_codes=ANALYST_LIMITATION_CODES,
            required_profile_identities_sha256=(
                ANALYST_CONCLUSION_VOCABULARY_IDENTITY_SHA256,
                ANALYST_CONFIDENCE_VOCABULARY_IDENTITY_SHA256,
                ANALYST_LIMITATION_VOCABULARY_IDENTITY_SHA256,
                NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256,
            ),
            semantic_metadata=_ANALYST_RESPONSE_V2_SEMANTIC_METADATA,
        ),
        "weekly_shadow_01_response_capture_v2": _semantic_contract_record(
            "weekly_shadow_01_response_capture_v2",
            owner="deterministic_code",
            relevant_blocking_reason_codes=(
                "WS01_BR_RESPONSE_MISSING",
                "WS01_BR_RESPONSE_UNREADABLE",
                "WS01_BR_RESPONSE_OVERSIZED",
            ),
            relevant_analyst_limitation_codes=(),
            required_profile_identities_sha256=(
                RESOURCE_BOUND_PROFILE_IDENTITY_SHA256,
                NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256,
            ),
            semantic_metadata=_RESPONSE_CAPTURE_V2_SEMANTIC_METADATA,
        ),
        "weekly_shadow_01_response_validation_v1": _semantic_contract_record(
            "weekly_shadow_01_response_validation_v1",
            owner="deterministic_code",
            relevant_blocking_reason_codes=BLOCKING_REASON_CODES,
            relevant_analyst_limitation_codes=(),
            required_profile_identities_sha256=(
                BLOCKING_REASON_VOCABULARY_IDENTITY_SHA256,
                NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256,
            ),
        ),
        "weekly_shadow_01_analyst_report_v1": _semantic_contract_record(
            "weekly_shadow_01_analyst_report_v1",
            owner="deterministic_code_and_validated_llm_content",
            relevant_blocking_reason_codes=(),
            relevant_analyst_limitation_codes=ANALYST_LIMITATION_CODES,
            required_profile_identities_sha256=(
                RUN_STATUS_VOCABULARY_IDENTITY_SHA256,
                ANALYST_CONCLUSION_VOCABULARY_IDENTITY_SHA256,
                ANALYST_CONFIDENCE_VOCABULARY_IDENTITY_SHA256,
                ANALYST_LIMITATION_VOCABULARY_IDENTITY_SHA256,
                NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256,
            ),
        ),
        "weekly_shadow_01_run_summary_v1": _semantic_contract_record(
            "weekly_shadow_01_run_summary_v1",
            owner="deterministic_code",
            relevant_blocking_reason_codes=BLOCKING_REASON_CODES,
            relevant_analyst_limitation_codes=(),
            required_profile_identities_sha256=(
                RUN_STATUS_VOCABULARY_IDENTITY_SHA256,
                BLOCKING_REASON_VOCABULARY_IDENTITY_SHA256,
                NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256,
            ),
        ),
    }
)

SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION: Final = _MappingProxyType(
    {
        schema_key: compute_identity("semantic_contract_identity", record)
        for schema_key, record in _SEMANTIC_CONTRACT_RECORDS.items()
    }
)


# --- overall WS01a2 contract-catalog identity --------------------------------

_CONTRACT_CATALOG_PAYLOAD: Final = {
    "catalog_version": "weekly_shadow_01_contract_catalog_v2",
    "domain_separators_hex": {name: value.hex() for name, value in DOMAIN_SEPARATORS.items()},
    "weekly_shadow_stage_version": WEEKLY_SHADOW_STAGE_VERSION,
    "legacy_r2f_adapter_id": LEGACY_R2F_ADAPTER_ID,
    "r2f_source_generation_id_pattern": R2F_SOURCE_GENERATION_ID_PATTERN,
    "r2f_source_generation_version": R2F_SOURCE_GENERATION_VERSION,
    "ordered_consumed_source_artifact_roles": list(CONSUMED_SOURCE_ARTIFACT_ROLES),
    "permanently_unconsumed_source_artifact_role": PERMANENTLY_UNCONSUMED_SOURCE_ARTIFACT_ROLE,
    "incomplete_source_generation_marker": INCOMPLETE_SOURCE_GENERATION_MARKER,
    "evidence_value_variants": list(EVIDENCE_VALUE_VARIANTS),
    "active_anchor_normalized_value_fields": list(ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS),
    "availability_status_normalized_value_fields": list(
        AVAILABILITY_STATUS_NORMALIZED_VALUE_FIELDS
    ),
    "diagnostic_code_values": list(DIAGNOSTIC_CODE_VALUES),
    "source_locator_contracts": {
        "active_anchor_v1": _recipe_payload(ACTIVE_ANCHOR_SOURCE_LOCATOR_CONTRACT),
        "availability_status_v1": _recipe_payload(AVAILABILITY_SOURCE_LOCATOR_CONTRACT),
        "diagnostic_code_v1": _recipe_payload(DIAGNOSTIC_SOURCE_LOCATOR_CONTRACT),
    },
    "package_owned_source_context": _recipe_payload(PACKAGE_OWNED_SOURCE_CONTEXT),
    "source_locator_semantics": list(SOURCE_LOCATOR_SEMANTICS),
    "logical_locator_definition": _recipe_payload(LOGICAL_LOCATOR_DEFINITION),
    "logical_locator_uniqueness_rules": list(LOGICAL_LOCATOR_UNIQUENESS_RULES),
    "evidence_variant_ranks": dict(EVIDENCE_VARIANT_RANKS),
    "availability_subject_ranks": dict(AVAILABILITY_SUBJECT_RANKS),
    "canonical_evidence_record_ordering": _recipe_payload(
        EVIDENCE_RECORD_CANONICAL_ORDERING
    ),
    "canonical_evidence_ordering_rules": list(CANONICAL_EVIDENCE_ORDERING_RULES),
    "canonical_order_independence_inputs": list(CANONICAL_ORDER_INDEPENDENCE_INPUTS),
    "analyst_input_schema_enforced_constraints": list(
        ANALYST_INPUT_SCHEMA_ENFORCED_CONSTRAINTS
    ),
    "future_ws01b_runtime_deferred_resource_bound_responsibilities": list(
        WS01B_RUNTIME_DEFERRED_RESOURCE_BOUND_RESPONSIBILITIES
    ),
    "future_ws01b_runtime_bound_failure_code": WS01B_RUNTIME_BOUND_FAILURE_CODE,
    "static_runtime_responsibility_table": _recipe_payload(
        STATIC_RUNTIME_RESPONSIBILITY_TABLE
    ),
    "future_ws01b_source_correlation_responsibilities": list(
        WS01B_SOURCE_CORRELATION_RESPONSIBILITIES
    ),
    "future_ws01c_response_validation_responsibilities": list(
        WS01C_RESPONSE_VALIDATION_RESPONSIBILITIES
    ),
    "obsolete_evidence_record_fields": list(OBSOLETE_EVIDENCE_RECORD_FIELDS),
    "obsolete_active_anchor_normalized_value_fields": list(
        OBSOLETE_ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS
    ),
    "diagnostic_reference_invariants": list(DIAGNOSTIC_REFERENCE_INVARIANTS),
    "projection_exclusions": list(PROJECTION_EXCLUSIONS),
    "evidence_record_id_recipe": _recipe_payload(EVIDENCE_RECORD_ID_RECIPE),
    "evidence_record_identity_recipe": _recipe_payload(EVIDENCE_RECORD_IDENTITY_RECIPE),
    "input_package_identity_recipe": _recipe_payload(INPUT_PACKAGE_IDENTITY_RECIPE),
    "run_status_values": list(RUN_STATUS_VALUES),
    "analyst_conclusion_values": list(ANALYST_CONCLUSION_VALUES),
    "analyst_confidence_values": list(ANALYST_CONFIDENCE_VALUES),
    "validation_status_values": list(VALIDATION_STATUS_VALUES),
    "publication_status_values": list(PUBLICATION_STATUS_VALUES),
    "blocking_reason_codes": list(BLOCKING_REASON_CODES),
    "analyst_limitation_codes": list(ANALYST_LIMITATION_CODES),
    "prohibited_analyst_conclusion_values": list(PROHIBITED_ANALYST_CONCLUSION_VALUES),
    "prohibited_key_terms": list(PROHIBITED_KEY_TERMS),
    "prohibited_intent_terms": list(PROHIBITED_INTENT_TERMS),
    "resource_bound_profile": dict(RESOURCE_BOUND_PROFILE),
    "negative_authority_profile": dict(NEGATIVE_AUTHORITY_PROFILE),
    "prompt_template_sha256": _hashlib.sha256(PROMPT_TEMPLATE_BYTES).hexdigest(),
    "prompt_template_identity_sha256": PROMPT_TEMPLATE_IDENTITY_SHA256,
    "resource_bound_profile_identity_sha256": RESOURCE_BOUND_PROFILE_IDENTITY_SHA256,
    "negative_authority_profile_identity_sha256": NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256,
    "run_status_vocabulary_identity_sha256": RUN_STATUS_VOCABULARY_IDENTITY_SHA256,
    "analyst_conclusion_vocabulary_identity_sha256": ANALYST_CONCLUSION_VOCABULARY_IDENTITY_SHA256,
    "analyst_confidence_vocabulary_identity_sha256": ANALYST_CONFIDENCE_VOCABULARY_IDENTITY_SHA256,
    "blocking_reason_vocabulary_identity_sha256": BLOCKING_REASON_VOCABULARY_IDENTITY_SHA256,
    "analyst_limitation_vocabulary_identity_sha256": ANALYST_LIMITATION_VOCABULARY_IDENTITY_SHA256,
    "prohibited_key_profile_identity_sha256": PROHIBITED_KEY_PROFILE_IDENTITY_SHA256,
    "prohibited_intent_profile_identity_sha256": PROHIBITED_INTENT_PROFILE_IDENTITY_SHA256,
    "schema_filename_by_version": dict(SCHEMA_FILENAME_BY_VERSION),
    "schema_identity_sha256_by_version": dict(SCHEMA_IDENTITY_SHA256_BY_VERSION),
    "semantic_contract_identity_sha256_by_version": dict(SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION),
    "authority_effect": "none",
}

CONTRACT_CATALOG_IDENTITY_SHA256: Final = compute_identity("contract_catalog", _CONTRACT_CATALOG_PAYLOAD)


__all__ = [
    "CanonicalizationError",
    "IdentityDefinitionError",
    "canonical_json_bytes",
    "domain_separated_sha256",
    "compute_identity",
    "DOMAIN_SEPARATORS",
    "RUN_STATUS_VALUES",
    "ANALYST_CONCLUSION_VALUES",
    "ANALYST_CONCLUSION_SEMANTICS",
    "ANALYST_CONFIDENCE_VALUES",
    "PROHIBITED_ANALYST_CONCLUSION_VALUES",
    "VALIDATION_STATUS_VALUES",
    "PUBLICATION_STATUS_VALUES",
    "BLOCKING_REASON_CODES",
    "ANALYST_LIMITATION_CODES",
    "NEGATIVE_AUTHORITY_PROFILE",
    "NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256",
    "WEEKLY_SHADOW_STAGE_VERSION",
    "LEGACY_R2F_ADAPTER_ID",
    "R2F_SOURCE_GENERATION_ID_PATTERN",
    "R2F_SOURCE_GENERATION_VERSION",
    "CONSUMED_SOURCE_ARTIFACT_ROLES",
    "PERMANENTLY_UNCONSUMED_SOURCE_ARTIFACT_ROLE",
    "INCOMPLETE_SOURCE_GENERATION_MARKER",
    "EVIDENCE_VALUE_VARIANTS",
    "ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS",
    "AVAILABILITY_STATUS_NORMALIZED_VALUE_FIELDS",
    "DIAGNOSTIC_CODE_VALUES",
    "SOURCE_LOCATOR_TYPES",
    "AVAILABILITY_SUBJECTS",
    "ACTIVE_ANCHOR_SOURCE_LOCATOR_CONTRACT",
    "AVAILABILITY_SOURCE_LOCATOR_CONTRACT",
    "DIAGNOSTIC_SOURCE_LOCATOR_CONTRACT",
    "PACKAGE_OWNED_SOURCE_CONTEXT",
    "OBSOLETE_EVIDENCE_RECORD_FIELDS",
    "OBSOLETE_ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS",
    "SOURCE_LOCATOR_SEMANTICS",
    "LOGICAL_LOCATOR_DEFINITION",
    "LOGICAL_LOCATOR_UNIQUENESS_RULES",
    "EVIDENCE_VARIANT_RANKS",
    "AVAILABILITY_SUBJECT_RANKS",
    "EVIDENCE_RECORD_CANONICAL_ORDERING",
    "CANONICAL_EVIDENCE_ORDERING_RULES",
    "CANONICAL_ORDER_INDEPENDENCE_INPUTS",
    "ANALYST_INPUT_SCHEMA_ENFORCED_CONSTRAINTS",
    "WS01B_RUNTIME_DEFERRED_RESOURCE_BOUND_RESPONSIBILITIES",
    "WS01B_RUNTIME_BOUND_FAILURE_CODE",
    "STATIC_RUNTIME_RESPONSIBILITY_TABLE",
    "WS01B_SOURCE_CORRELATION_RESPONSIBILITIES",
    "WS01C_RESPONSE_VALIDATION_RESPONSIBILITIES",
    "DIAGNOSTIC_REFERENCE_INVARIANTS",
    "PROJECTION_EXCLUSIONS",
    "EVIDENCE_RECORD_ID_RECIPE",
    "EVIDENCE_RECORD_IDENTITY_RECIPE",
    "INPUT_PACKAGE_IDENTITY_RECIPE",
    "RESOURCE_BOUND_PROFILE",
    "RESOURCE_BOUND_PROFILE_IDENTITY_SHA256",
    "RESOURCE_BOUND_SCHEMA_OR_HELPER_ENFORCED_FIELDS",
    "RESOURCE_BOUND_RUNTIME_DEFERRED_FIELDS",
    "PROHIBITED_KEY_TERMS",
    "PROHIBITED_INTENT_TERMS",
    "PROHIBITED_KEY_PROFILE_IDENTITY_SHA256",
    "PROHIBITED_INTENT_PROFILE_IDENTITY_SHA256",
    "PROMPT_TEMPLATE_PLACEHOLDER",
    "PROMPT_TEMPLATE_TEXT",
    "PROMPT_TEMPLATE_BYTES",
    "PROMPT_TEMPLATE_IDENTITY_SHA256",
    "RUN_STATUS_VOCABULARY_IDENTITY_SHA256",
    "ANALYST_CONCLUSION_VOCABULARY_IDENTITY_SHA256",
    "ANALYST_CONFIDENCE_VOCABULARY_IDENTITY_SHA256",
    "BLOCKING_REASON_VOCABULARY_IDENTITY_SHA256",
    "ANALYST_LIMITATION_VOCABULARY_IDENTITY_SHA256",
    "SCHEMA_FILENAME_BY_VERSION",
    "SCHEMA_IDENTITY_SHA256_BY_VERSION",
    "SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION",
    "CONTRACT_CATALOG_IDENTITY_SHA256",
]
