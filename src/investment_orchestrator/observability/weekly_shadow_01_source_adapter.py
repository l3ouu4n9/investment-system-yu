"""Read-only R2F v2 verifier and source adapter for WEEKLY-SHADOW-01.

The public operation verifies one explicitly selected immutable R2F v2
generation and returns frozen in-memory values.  It never reads the editable
analyst memo, writes an artifact, chooses a generation, invokes a model, or
creates a permission, gate, portfolio, order, or execution result.
"""

from __future__ import annotations

del annotations

import ast as _ast
from dataclasses import dataclass as _dataclass
from datetime import date as _date
import hashlib as _hashlib
import json as _json
import math as _math
import os as _os
from pathlib import Path as _Path
import re as _re
import stat as _stat
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from collections.abc import Mapping
    from os import PathLike
    from typing import Any


# A fixed callable binding keeps the descriptor scanner explicit and prevents
# callers from replacing directory-selection semantics through a parameter.
_list_directory_entries = _os.listdir


_ADAPTER_VERSION = "legacy_r2f_adapter_v1"
_R2F_ROOT_PARTS = (
    "artifacts",
    "current",
    "step1_research",
    "r2f_report_" + "only",
)
_GENERATIONS_DIRECTORY = "generations"
_GENERATION_ID = _re.compile(r"[0-9a-f]{64}\Z")

_MANIFEST_FILENAME = "replacement_input_manifest.json"
_EVIDENCE_FILENAME = "evidence_packet.json"
_PROMPT_FILENAME = "analyst_memo_prompt.txt"
_RAW_MEMO_FILENAME = "analyst_memo_raw_output.txt"
_RENDER_BINDING_FILENAME = "render_generation_binding.json"
_IN_PROGRESS_FILENAME = ".render_in_progress"
_COMPLETED_FILENAMES = frozenset(
    {
        _MANIFEST_FILENAME,
        _EVIDENCE_FILENAME,
        _PROMPT_FILENAME,
        _RAW_MEMO_FILENAME,
        _RENDER_BINDING_FILENAME,
    }
)

_MANIFEST_SCHEMA_VERSION = "step1_replacement_input_manifest_v2"
_V1_MANIFEST_SCHEMA_VERSION = "step1_replacement_input_manifest_v1"
_RENDER_BINDING_SCHEMA_VERSION = "step1_replacement_render_generation_binding_v2"
_GENERATION_IDENTITY_SCHEMA_VERSION = "step1_replacement_generation_identity_v2"
_COMPATIBILITY_PROFILE = "step1_replacement_render_observation_v2"
_CAPTURE_PROFILE = "retained_repo_and_common_parent_v1"
_EVIDENCE_SCHEMA_VERSION = "evidence_packet_v1"
_BASELINE_REGISTRY_SCHEMA_VERSION = "active_research_anchor_registry_v1"
_APPROVALS_REGISTRY_SCHEMA_VERSION = "active_research_anchor_registry_with_approvals_v1"

_PROMPT_CONTRACT_SCHEMA_VERSION = "step1_replacement_prompt_contract_v2"
_PROMPT_TEMPLATE_ID = "r2f_analyst_memo_content_v2"
_PROMPT_RENDERER_PROFILE = "r2f_prompt_renderer_v2"
_RAW_MEMO_SCHEMA_VERSION = "r2f_analyst_memo_content_v2"
_PROMPT_PROJECTION_SCHEMA_VERSION = "r2f_bounded_research_prompt_projection_v2"
_UNIVERSE_PROJECTION_PROFILE = "allowed_buy_then_extended_base_precedence_v1"
_ACTIVE_ANCHOR_PROJECTION_PROFILE = "valid_active_registry_anchor_ids_sorted_v1"
_TEXT_ENCODING_PROFILE = "utf8_lf_no_bom_terminal_newline_v1"
_R2F_PROMPT_TEMPLATE_FILENAME = "r2f_analyst_memo_content_v2.txt"
_R2F_PROMPT_TEMPLATE_PLACEHOLDER = "{{ prompt_projection_json }}"
_EXPECTED_R2F_PROMPT_TEMPLATE_SHA256 = (
    "d378984b13abe19c15995225ba24803fd8c37a62d711a2ee85d3e0dbe8359f49"
)

_INPUT_PATHS = _MappingProxyType(
    {
        "strategy_settings": "inputs/current/strategy_settings.yaml",
        "portfolio_snapshot": "inputs/current/portfolio_snapshot.txt",
        "research_anchors": "inputs/current/research_anchors.yaml",
        "research_anchor_approvals": "inputs/current/research_anchor_approvals.yaml",
    }
)
_SOURCE_VERSIONS = _MappingProxyType(
    {
        "strategy_settings": "strategy_settings_repository_contract",
        "portfolio_snapshot": "portfolio_snapshot_repository_contract",
        "research_anchors": "research_anchors_v1",
        "research_anchor_approvals": "research_anchor_approvals_v1",
    }
)
_AUTHORITY_MARKERS = _MappingProxyType(
    {
        "report_only": True,
        "runtime_consumed": False,
        "permission_effect": "none",
        "not_authorization": True,
        "order_authorization": False,
        "broker_authorization": False,
    }
)

_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "compatibility_profile",
        "as_of",
        "generated_at",
        "capture_profile",
        "inputs",
        "parsed_decision_relevant_settings_sha256",
        "supported_source_versions",
        "evidence_packet",
        "active_registry",
        "domain_validation",
        "prompt_contract",
        *_AUTHORITY_MARKERS,
    }
)
_INPUT_RECORD_KEYS = frozenset(
    {"path", "file_sha256", "production_text_sha256", "source_version"}
)
_EVIDENCE_BINDING_KEYS = frozenset(
    {"schema_version", "file_sha256", "canonical_content_sha256"}
)
_REGISTRY_BINDING_KEYS = frozenset(
    {"schema_version", "canonical_content_sha256", "selected_source"}
)
_DOMAIN_VALIDATION_KEYS = frozenset({"status", "diagnostics"})
_ALLOWED_DOMAIN_DIAGNOSTICS = frozenset(
    {
        "APPROVAL_HASH_MISMATCH",
        "EMPTY_ACTIVE_REGISTRY",
        "EXPIRED_OR_INACTIVE_APPROVAL",
        "MARKET_METRICS_UNAVAILABLE",
        "NO_APPROVALS",
        "PORTFOLIO_COVERAGE_INCOMPLETE",
        "REVOCATION_PRESENT",
        "SCHEDULED_EVENTS_UNAVAILABLE",
    }
)

_PROMPT_CONTRACT_PROJECTION_KEYS = frozenset(
    {
        "schema_version",
        "template_id",
        "template_file_sha256",
        "renderer_profile",
        "raw_memo_schema_version",
        "evidence_projection_profile",
        "universe_projection_profile",
        "active_anchor_projection_profile",
        "text_encoding_profile",
    }
)
_PROMPT_CONTRACT_KEYS = frozenset(
    {
        "projection",
        "canonical_content_sha256",
        "prompt_projection_schema_version",
        "prompt_projection_canonical_sha256",
        "analyst_memo_prompt_file_sha256",
        "raw_memo_schema_version",
    }
)
_RENDER_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "compatibility_profile",
        "generation_id",
        "scope",
        "render_complete",
        "immutable_render_artifacts",
        "operator_editable_inputs",
        "generation_identity",
        *_AUTHORITY_MARKERS,
    }
)
_GENERATION_IDENTITY_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "prompt_contract_canonical_sha256",
        "analyst_memo_prompt_file_sha256",
        "raw_memo_schema_version",
    }
)

_EVIDENCE_REQUIRED_FIELDS = (
    "schema_version",
    "is_llm_generated",
    "generated_at",
    "source",
    "strategy_settings_hash",
    "strategy_settings_summary",
    "universe",
    "budget_settings",
    "portfolio_snapshot_summary",
    "last_good_research_summary",
    "market_metrics",
    "scheduled_events_deterministic",
    "research_anchors",
    "data_gaps",
    "source_artifacts",
)
_LLM_MEMO_FIELDS = (
    "regime_view",
    "key_risks",
    "opportunity_summary",
    "ticker_relative_view",
    "preferred_exposures",
    "avoid_or_deprioritize",
    "scheduled_event_interpretation",
    "confidence",
    "source_notes",
)
_EVIDENCE_TOP_LEVEL_KEYS = frozenset(
    {*_EVIDENCE_REQUIRED_FIELDS, "active_anchor_registry", *_AUTHORITY_MARKERS}
)
_UNIVERSE_KEYS = frozenset(
    {
        "core_universe",
        "satellite_universe",
        "approved_extended_etf",
        "allowed_buy_tickers",
        "role_source_by_ticker",
    }
)

_EXPECTED_SCHEMA_IDENTITIES = _MappingProxyType(
    {
        "weekly_shadow_01_analyst_input_v2": (
            "41c6258b3d27b97554a785628ab3e990e0f1f89bbaad7d70a787dd230853f5f0"
        ),
        "weekly_shadow_01_analyst_response_v2": (
            "3625d86dd84ae1243ccb4992e339d0935dff646c87e74f7792ecd635956ca160"
        ),
        "weekly_shadow_01_response_capture_v2": (
            "a2f727e89e29f2a3ab9791d8274236f8481b2c30175eb07bc4d4bf458d429a95"
        ),
        "weekly_shadow_01_response_validation_v1": (
            "2990ad8fc4f22de8b21691f54b3a967aed66e733078bd75ea74bc1330ee02f02"
        ),
        "weekly_shadow_01_analyst_report_v1": (
            "7b415fa8eb7cb4ecce92ddf06eb394574f7d1435dd840657396dd2eeb0f4feb8"
        ),
        "weekly_shadow_01_run_summary_v1": (
            "114e92f0d151bba7266a651172cd7dac01f9652a4c6fe47557582b10dcf706a7"
        ),
    }
)
_EXPECTED_SEMANTIC_IDENTITIES = _MappingProxyType(
    {
        "weekly_shadow_01_analyst_input_v2": (
            "b49a1fa7bdd3affbf2c25c4f9184bcbdf54c9e1201327bad565c35c1de066eb1"
        ),
        "weekly_shadow_01_analyst_response_v2": (
            "a3a14276ec697ad4e806f6c6d16250b95f279ba4c13aee573bbd8263039ea546"
        ),
        "weekly_shadow_01_response_capture_v2": (
            "2ff319f61fd445458b9cb897e9a2db83265deb5c3ea93313a73572f39efab19b"
        ),
        "weekly_shadow_01_response_validation_v1": (
            "3a41c1b6149aaa471d3dd94bd007b74cfcddbbdd97790bf00c7cdebd9b5000d5"
        ),
        "weekly_shadow_01_analyst_report_v1": (
            "195112bf9087b1f63f680c93a77d41487e4bceae4564a621c55c15b6cb684014"
        ),
        "weekly_shadow_01_run_summary_v1": (
            "88bc37d815c348fa0791c51fbdc660f2527c2d9975a01ab2bde2b9853c2a99b3"
        ),
    }
)
_EXPECTED_PROFILE_IDENTITIES = _MappingProxyType(
    {
        "negative_authority": "b20ea7218880c5799897d7d3fbd74515af88ad6fcc9e2f4c1d4cc83649e61ff1",
        "resource_bound": "acef986d2728660acce561f7c0d6a86fb0a942fa07ba8d3aea64bd061eee0e2e",
        "prohibited_key": "88247b4e04877b3925a988bce9185181e4d2c4214cf7e58a51b415907651dc9c",
        "prohibited_intent": "5376a4e55d8bb6f1d79808355f5056e35e25a004869ba62ee6ff225f55f3b0ba",
        "prompt_template": "e02839c54e4883af253158ab2295a61c2fe22ce483d296dae8af2e23bcd9dd37",
        "run_status": "be57d34943541c65839ec1774387d70008633606705b888b64709087f72d6f8a",
        "analyst_conclusion": "210dc5e43311a54ad09daf0f4a64405d6ce999b0818c40f1f7d0da1f644ba9cd",
        "analyst_confidence": "6cd369043ddf149b7eba2a9550955ea7518ce3e48a1263499df9ab76548fed82",
        "blocking_reason": "4c105e9074f5c8fa8ab4e13ee6065aa4e99752cde33116932a6bcc134f784c47",
        "analyst_limitation": "0e9ac34fcc09308269385b6b80c4e5b62dfd74fa80ea3c9bbe24d54d84f0fefc",
    }
)
_EXPECTED_PROMPT_RAW_SHA256 = "527b0b8fea23f9fd7265e6287bdd14da55280fb3340269eae49af062c5c5e25c"
_EXPECTED_CONTRACT_CATALOG_IDENTITY = (
    "36a0f850a089c3276c62dfe677ebfbce1ee9d1289e0487c3aad358db6cb556d4"
)
_EXPECTED_CONTRACT_MODULE_SHA256 = (
    "cc6659754275991a5d244aec8f26f725dc74d339be766cdf7694e97e6f19792a"
)
_EXPECTED_DOMAIN_SEPARATORS = _MappingProxyType(
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
_BLOCKING_REASON_CODES = frozenset(
    {
        "WS01_BR_SOURCE_GENERATION_INVALID",
        "WS01_BR_SOURCE_ARTIFACT_SET_MISMATCH",
        "WS01_BR_SOURCE_VERSION_UNSUPPORTED",
        "WS01_BR_SOURCE_READ_UNSTABLE",
        "WS01_BR_SOURCE_BINDING_MISMATCH",
        "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
        "WS01_BR_RESOURCE_BOUND_EXCEEDED",
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    }
)
_CONSUMED_SOURCE_ARTIFACT_ROLES = (
    _MANIFEST_FILENAME,
    _EVIDENCE_FILENAME,
    _PROMPT_FILENAME,
    _RENDER_BINDING_FILENAME,
)
_AVAILABILITY_SUBJECTS = (
    "market_metrics",
    "scheduled_events_deterministic",
)
_DIAGNOSTIC_CODE_VALUES = ("EMPTY_ACTIVE_REGISTRY",)
_EVIDENCE_VARIANT_RANKS = _MappingProxyType(
    {
        "active_anchor_v1": 0,
        "availability_status_v1": 1,
        "diagnostic_code_v1": 2,
    }
)
_AVAILABILITY_SUBJECT_RANKS = _MappingProxyType(
    {"market_metrics": 0, "scheduled_events_deterministic": 1}
)
_RESOURCE_BOUND_PROFILE = _MappingProxyType(
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
        "max_nesting_depth": 16,
        "max_object_members": 1_024,
        "max_array_items": 1_024,
        "max_evidence_records": 256,
        "max_entries_per_analytical_section": 32,
        "max_total_analytical_entries": 128,
        "max_references_per_entry": 16,
        "max_diagnostics": 256,
        "max_text_code_points": 2_048,
        "max_aggregate_analyst_text_code_points": 32_768,
    }
)
_NEGATIVE_AUTHORITY_PROFILE = _MappingProxyType(
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
_PROHIBITED_ANALYST_CONCLUSION_VALUES = (
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
_PROMPT_TEMPLATE_PLACEHOLDER = "{{WEEKLY_SHADOW_01_INPUT_PACKAGE_JSON}}"
_PROMPT_TEMPLATE_LINES = (
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
    _PROMPT_TEMPLATE_PLACEHOLDER,
)
_PROMPT_TEMPLATE_BYTES = ("\n".join(_PROMPT_TEMPLATE_LINES) + "\n").encode("utf-8")
_RESOURCE_BOUND_PROFILE_IDENTITY = _EXPECTED_PROFILE_IDENTITIES["resource_bound"]
_NEGATIVE_AUTHORITY_PROFILE_IDENTITY = _EXPECTED_PROFILE_IDENTITIES[
    "negative_authority"
]
_PROMPT_TEMPLATE_IDENTITY = _EXPECTED_PROFILE_IDENTITIES["prompt_template"]
_LEGACY_R2F_ADAPTER_ID = "legacy_r2f_v2_to_weekly_shadow_v1"
_LOGICAL_LOCATOR_UNIQUENESS_RULES = (
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
_CANONICAL_EVIDENCE_RECORD_ORDERING = _MappingProxyType(
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
        "availability_subject_order": _AVAILABILITY_SUBJECTS,
        "manifest_diagnostic_position": "single_fixed_position_under_variant_rank",
        "final_sequence_requirement": "strictly_increasing",
    }
)
_CANONICAL_EVIDENCE_ORDERING_RULES = (
    "construct_evidence_records_in_frozen_canonical_order",
    "reject_duplicate_canonical_ordering_keys",
    "verify_final_evidence_record_sequence_is_strictly_increasing",
    "never_rely_on_source_traversal_or_caller_mapping_order",
    "reject_caller_supplied_noncanonical_evidence_record_sequence",
    "never_silently_reorder_or_accept_a_noncanonical_input_package",
    "ws01c_never_repairs_analyst_input_evidence_record_order",
)
_WS01B_RUNTIME_BOUND_RESPONSIBILITIES = (
    "each_source_artifact_byte_length_le_source_artifact_max_bytes",
    "combined_source_artifact_byte_length_le_source_artifacts_total_max_bytes",
    "canonical_analyst_input_package_byte_length_le_analyst_input_max_bytes",
    "rendered_analyst_prompt_byte_length_le_rendered_prompt_max_bytes",
    "combined_diagnostic_reference_union_count_le_max_diagnostics",
    "diagnostic_reference_ids_unique_across_both_arrays",
    "logical_locator_count_and_uniqueness_le_max_evidence_records",
    "aggregate_analyst_text_code_points_le_max_aggregate_analyst_text_code_points",
)
_WS01B_SOURCE_CORRELATION_RESPONSIBILITIES = (
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
_STATIC_RUNTIME_RESPONSIBILITY_TABLE = _MappingProxyType(
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
_DIAGNOSTIC_REFERENCE_INVARIANTS = (
    "every_diagnostic_id_references_one_evidence_record",
    "availability_ids_reference_only_availability_or_empty_active_registry_records",
    "freshness_ids_reference_only_active_anchor_records",
    "diagnostic_id_union_count_does_not_exceed_max_diagnostics",
    "no_duplicate_id_across_availability_and_freshness_arrays",
    "referential_and_union_invariants_are_enforced_by_future_ws01b",
)


class _SourceAdapterFailure(RuntimeError):
    """Private reason-code carrier that never crosses a public boundary."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if code not in _BLOCKING_REASON_CODES:
            code = "WS01_BR_INTERNAL_INVARIANT_FAILURE"
        self.code = code
        super().__init__(code)


@_dataclass(frozen=True, slots=True, init=False)
class _WS01bResult:
    """Closed immutable success/failure envelope shared by both WS01b modules."""

    ok: bool
    value: object | None
    reason_code: str | None

    def __new__(cls, *_args: object, **_kwargs: object) -> "_WS01bResult":
        raise TypeError("WS01b results are created only by private factories")


def _result_failure(reason_code: object) -> _WS01bResult:
    code = (
        reason_code
        if type(reason_code) is str and reason_code in _BLOCKING_REASON_CODES
        else "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    )
    result = object.__new__(_WS01bResult)
    object.__setattr__(result, "ok", False)
    object.__setattr__(result, "value", None)
    object.__setattr__(result, "reason_code", code)
    return result


def _result_success(value: object) -> _WS01bResult:
    if type(value) not in (_VerifiedR2FGeneration, _VerifiedSourceSnapshot):
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    result = object.__new__(_WS01bResult)
    object.__setattr__(result, "ok", True)
    object.__setattr__(result, "value", value)
    object.__setattr__(result, "reason_code", None)
    return result


@_dataclass(frozen=True, slots=True)
class _AuthenticatedContractSurface:
    """One sealed, deeply immutable WS01a2 contract snapshot."""

    complete_surface: "Mapping[str, Any]"
    runtime_surface: "Mapping[str, Any]"
    catalog_identity_sha256: str
    seal_sha256: str


@_dataclass(frozen=True, slots=True, init=False)
class _SourceArtifactBinding:
    """Identity-only package binding for one verified consumed artifact."""

    source_id: str
    source_artifact_identity_sha256: str
    byte_size: int
    file_sha256: str
    canonical_content_sha256: str | None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("private WS01b source-artifact binding")

    def to_package_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "source_artifact_identity_sha256": self.source_artifact_identity_sha256,
        }


@_dataclass(frozen=True, slots=True, init=False)
class _VerifiedR2FGeneration:
    """Immutable complete verification result; it contains no raw analyst memo."""

    adapter_id: str
    adapter_version: str
    source_generation_id: str
    source_generation_version: str
    evaluation_timestamp_utc: str
    source_artifact_bindings: tuple[_SourceArtifactBinding, ...]
    manifest: "Mapping[str, Any]"
    evidence_packet: "Mapping[str, Any]"
    analyst_input_schema: "Mapping[str, Any]"
    contract_surface: "Mapping[str, Any]"
    authenticated_contract_surface: _AuthenticatedContractSurface
    contract_catalog_identity_sha256: str

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("private WS01b verified generation")


@_dataclass(frozen=True, slots=True, init=False)
class _VerifiedSourceSnapshot:
    """Frozen, projection-only source values accepted for package construction."""

    adapter_id: str
    adapter_version: str
    source_generation_id: str
    source_generation_version: str
    evaluation_timestamp_utc: str
    source_artifact_bindings: tuple[_SourceArtifactBinding, ...]
    active_anchors: tuple["Mapping[str, Any]", ...]
    availability_statuses: tuple["Mapping[str, Any]", ...]
    representation_diagnostics: tuple[str, ...]
    contract_catalog_identity_sha256: str
    analyst_input_schema: "Mapping[str, Any]"
    contract_surface: "Mapping[str, Any]"
    authenticated_contract_surface: _AuthenticatedContractSurface
    snapshot_identity_sha256: str

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("private WS01b source snapshot")


def _new_source_artifact_binding(
    *,
    source_id: str,
    source_artifact_identity_sha256: str,
    byte_size: int,
    file_sha256: str,
    canonical_content_sha256: str | None,
) -> _SourceArtifactBinding:
    binding = object.__new__(_SourceArtifactBinding)
    object.__setattr__(binding, "source_id", source_id)
    object.__setattr__(
        binding,
        "source_artifact_identity_sha256",
        source_artifact_identity_sha256,
    )
    object.__setattr__(binding, "byte_size", byte_size)
    object.__setattr__(binding, "file_sha256", file_sha256)
    object.__setattr__(
        binding,
        "canonical_content_sha256",
        canonical_content_sha256,
    )
    return binding


def _new_verified_r2f_generation(**fields: object) -> _VerifiedR2FGeneration:
    generation = object.__new__(_VerifiedR2FGeneration)
    for name in _VerifiedR2FGeneration.__slots__:
        object.__setattr__(generation, name, fields[name])
    return generation


def _new_verified_source_snapshot(**fields: object) -> _VerifiedSourceSnapshot:
    snapshot = object.__new__(_VerifiedSourceSnapshot)
    for name in _VerifiedSourceSnapshot.__slots__:
        object.__setattr__(snapshot, name, fields[name])
    return snapshot


@_dataclass(frozen=True, slots=True)
class _RegularFileState:
    device: int
    inode: int
    size: int
    mode: int
    mtime_ns: int
    ctime_ns: int
    link_count: int


@_dataclass(frozen=True, slots=True)
class _StableFileSnapshot:
    raw_bytes: bytes
    state: _RegularFileState


class _DescriptorOwner:
    __slots__ = ("_descriptors",)

    def __init__(self) -> None:
        self._descriptors: list[int] = []

    def register(self, descriptor: int) -> int:
        self._descriptors.append(descriptor)
        return descriptor

    def close_all(self) -> bool:
        failed = False
        descriptors, self._descriptors = self._descriptors, []
        for descriptor in reversed(descriptors):
            try:
                _os.close(descriptor)
            except Exception:
                failed = True
        return failed


class _DuplicateJsonKey(ValueError):
    pass


class _ContractSourceEvaluationError(ValueError):
    pass


def verify_r2f_v2_generation(
    generation_id: str,
    *,
    repository_root: "str | PathLike[str] | None" = None,
) -> _WS01bResult:
    """Return a closed outcome for one explicitly selected R2F v2 generation."""
    result: _VerifiedR2FGeneration | None = None
    reason_code: str | None = None
    try:
        result = _verify_r2f_v2_generation(
            generation_id,
            repository_root=repository_root,
        )
    except _SourceAdapterFailure as failure:
        reason_code = failure.code
    except Exception:
        reason_code = "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    if reason_code is not None:
        return _result_failure(reason_code)
    if result is None:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _result_success(result)


def _verify_r2f_v2_generation(
    generation_id: str,
    *,
    repository_root: "str | PathLike[str] | None" = None,
) -> _VerifiedR2FGeneration:
    """Verify one explicit R2F v2 generation without consuming its raw memo."""
    if type(generation_id) is not str or _GENERATION_ID.fullmatch(generation_id) is None:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    root = _repository_root(repository_root)
    _require_descriptor_primitives()
    owner = _DescriptorOwner()
    result: _VerifiedR2FGeneration | None = None
    failure_code: str | None = None
    try:
        repository_fd, chain = _open_absolute_directory_chain(root, owner=owner)
        input_schema, authenticated_contract_surface, r2f_prompt_template = (
            _verify_ws01a2_contract_surface(
                repository_fd=repository_fd,
                owner=owner,
                chain=chain,
            )
        )
        parent_fd = repository_fd
        for component in (*_R2F_ROOT_PARTS, _GENERATIONS_DIRECTORY, generation_id):
            child_fd = _open_directory_at(parent_fd, component, owner=owner)
            chain.append((parent_fd, component, child_fd))
            parent_fd = child_fd
        generation_fd = parent_fd
        generation_directory_identity = _directory_identity(_os.fstat(generation_fd))
        result = _verify_generation_at(
            generation_fd=generation_fd,
            generation_id=generation_id,
            analyst_input_schema=input_schema,
            authenticated_contract_surface=authenticated_contract_surface,
            r2f_prompt_template=r2f_prompt_template,
            owner=owner,
        )
        _verify_directory_chain(chain)
        if _directory_identity(_os.fstat(generation_fd)) != generation_directory_identity:
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
    except _SourceAdapterFailure as failure:
        failure_code = failure.code
    except Exception:
        failure_code = "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    finally:
        cleanup_failed = owner.close_all()
    if cleanup_failed:
        _raise("WS01_BR_SOURCE_READ_UNSTABLE")
    if failure_code is not None:
        _raise(failure_code)
    if result is None:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return result


def build_source_snapshot(
    generation_id: str,
    *,
    repository_root: "str | PathLike[str] | None" = None,
) -> _WS01bResult:
    """Verify and project one explicitly selected R2F v2 generation."""
    result: _VerifiedSourceSnapshot | None = None
    reason_code: str | None = None
    try:
        verified_generation = _verify_r2f_v2_generation(
            generation_id,
            repository_root=repository_root,
        )
        result = _build_source_snapshot(verified_generation)
    except _SourceAdapterFailure as failure:
        reason_code = failure.code
    except Exception:
        reason_code = "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    if reason_code is not None:
        return _result_failure(reason_code)
    if result is None:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _result_success(result)


def _build_source_snapshot(
    verified_generation: _VerifiedR2FGeneration,
) -> _VerifiedSourceSnapshot:
    """Project only frozen WS01a2 evidence values from a verified generation."""
    if type(verified_generation) is not _VerifiedR2FGeneration:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    try:
        _require_verified_generation_shape(verified_generation)
        evidence = verified_generation.evidence_packet
        registry = evidence["active_anchor_registry"]
        rows = registry["active_anchors"]
        active_anchors: list[dict[str, object]] = []
        seen_anchor_ids: set[str] = set()
        for raw_row in rows:
            row = _project_active_anchor(raw_row)
            anchor_id = row["anchor_id"]
            assert type(anchor_id) is str
            if anchor_id in seen_anchor_ids:
                _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
            seen_anchor_ids.add(anchor_id)
            active_anchors.append(row)
        active_anchors.sort(key=lambda row: _canonical_r2f_json_bytes(row["anchor_id"]))

        availability_statuses = [
            {
                "availability_subject": subject,
                "normalized_value": _project_availability(evidence[subject]),
            }
            for subject in _AVAILABILITY_SUBJECTS
        ]
        diagnostics = verified_generation.manifest["domain_validation"]["diagnostics"]
        representation_diagnostics = tuple(
            code for code in _DIAGNOSTIC_CODE_VALUES if code in diagnostics
        )
        empty_present = "EMPTY_ACTIVE_REGISTRY" in representation_diagnostics
        if empty_present != (len(active_anchors) == 0):
            _raise("WS01_BR_SOURCE_BINDING_MISMATCH")

        snapshot_payload = {
            "payload_kind": "weekly_shadow_01_verified_source_snapshot_v1",
            "adapter_id": verified_generation.adapter_id,
            "adapter_version": verified_generation.adapter_version,
            "source_generation_id": verified_generation.source_generation_id,
            "source_generation_version": verified_generation.source_generation_version,
            "evaluation_timestamp_utc": verified_generation.evaluation_timestamp_utc,
            "source_artifact_bindings": [
                binding.to_package_dict()
                for binding in verified_generation.source_artifact_bindings
            ],
            "active_anchors": active_anchors,
            "availability_statuses": availability_statuses,
            "representation_diagnostics": list(representation_diagnostics),
            ("contract_catalog_" + "identity_sha256"): (
                verified_generation.contract_catalog_identity_sha256
            ),
            "contract_surface": _deep_thaw(verified_generation.contract_surface),
        }
        snapshot_identity = _sha256(
            _EXPECTED_DOMAIN_SEPARATORS["source_artifact"]
            + _canonical_ws01_json_bytes(snapshot_payload)
        )
        snapshot = _new_verified_source_snapshot(
            adapter_id=verified_generation.adapter_id,
            adapter_version=verified_generation.adapter_version,
            source_generation_id=verified_generation.source_generation_id,
            source_generation_version=verified_generation.source_generation_version,
            evaluation_timestamp_utc=verified_generation.evaluation_timestamp_utc,
            source_artifact_bindings=verified_generation.source_artifact_bindings,
            active_anchors=tuple(_deep_freeze(value) for value in active_anchors),
            availability_statuses=tuple(
                _deep_freeze(value) for value in availability_statuses
            ),
            representation_diagnostics=representation_diagnostics,
            contract_catalog_identity_sha256=(
                verified_generation.contract_catalog_identity_sha256
            ),
            analyst_input_schema=verified_generation.analyst_input_schema,
            contract_surface=verified_generation.contract_surface,
            authenticated_contract_surface=(
                verified_generation.authenticated_contract_surface
            ),
            snapshot_identity_sha256=snapshot_identity,
        )
        _require_snapshot_identity(snapshot)
        return snapshot
    except _SourceAdapterFailure:
        raise
    except (AssertionError, KeyError, RecursionError, TypeError, ValueError):
        raise


def _repository_root(value: "str | PathLike[str] | None") -> _Path:
    if value is None:
        root = _Path(__file__).parents[3]
    else:
        try:
            root = _Path(value)
        except TypeError:
            _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if not root.is_absolute() or any(part in ("", ".", "..") for part in root.parts[1:]):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    return root


def _verify_ws01a2_contract_surface(
    *,
    repository_fd: int,
    owner: _DescriptorOwner,
    chain: list[tuple[int, str, int]],
) -> tuple["Mapping[str, Any]", _AuthenticatedContractSurface, bytes]:
    expected_schema_files = {
        "weekly_shadow_01_analyst_input_v2": "schemas/weekly_shadow_01_analyst_input.schema.json",
        "weekly_shadow_01_analyst_response_v2": "schemas/weekly_shadow_01_analyst_response.schema.json",
        "weekly_shadow_01_response_capture_v2": "schemas/weekly_shadow_01_response_capture.schema.json",
        "weekly_shadow_01_response_validation_v1": "schemas/weekly_shadow_01_response_validation.schema.json",
        "weekly_shadow_01_analyst_report_v1": "schemas/weekly_shadow_01_analyst_report.schema.json",
        "weekly_shadow_01_run_summary_v1": "schemas/weekly_shadow_01_run_summary.schema.json",
    }
    source_fd = _open_directory_at(repository_fd, "src", owner=owner)
    chain.append((repository_fd, "src", source_fd))
    package_fd = _open_directory_at(source_fd, "investment_orchestrator", owner=owner)
    chain.append((source_fd, "investment_orchestrator", package_fd))
    observability_fd = _open_directory_at(package_fd, "observability", owner=owner)
    chain.append((package_fd, "observability", observability_fd))
    contract_source = _read_stable_regular_file_at(
        observability_fd,
        "weekly_shadow_01_contracts.py",
        maximum_bytes=1_048_576,
        too_large_code="WS01_BR_INTERNAL_INVARIANT_FAILURE",
        owner=owner,
    )
    if contract_source.startswith(b"\xef\xbb\xbf") or b"\r" in contract_source:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    try:
        contract_source.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    schema_fd = _open_directory_at(repository_fd, "schemas", owner=owner)
    chain.append((repository_fd, "schemas", schema_fd))
    schema_directory_state = _directory_inventory_state(_os.fstat(schema_fd))
    expected_schema_names = frozenset(
        relative_path.rsplit("/", 1)[1]
        for relative_path in expected_schema_files.values()
    )
    actual_schema_names = frozenset(
        name
        for name in _list_directory_entries(schema_fd)
        if name.startswith("weekly_shadow_01_") and name.endswith(".schema.json")
    )
    if actual_schema_names != expected_schema_names:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    parsed_schemas: dict[str, dict[str, object]] = {}
    raw_schemas: dict[str, bytes] = {}
    for version, relative_path in expected_schema_files.items():
        filename = relative_path.rsplit("/", 1)[1]
        raw = _read_stable_regular_file_at(
            schema_fd,
            filename,
            maximum_bytes=1_048_576,
            too_large_code="WS01_BR_INTERNAL_INVARIANT_FAILURE",
            owner=owner,
        )
        schema = _parse_json_object(raw, "WS01_BR_INTERNAL_INVARIANT_FAILURE")
        raw_schemas[version] = raw
        parsed_schemas[version] = schema
    if _directory_inventory_state(_os.fstat(schema_fd)) != schema_directory_state:
        _raise("WS01_BR_SOURCE_READ_UNSTABLE")

    authenticated_surface = _authenticate_contract_surface(
        contract_source=contract_source,
        expected_schema_files=expected_schema_files,
        raw_schemas=raw_schemas,
        parsed_schemas=parsed_schemas,
    )
    prompts_fd = _open_directory_at(repository_fd, "prompts", owner=owner)
    chain.append((repository_fd, "prompts", prompts_fd))
    r2f_prompt_template = _read_stable_regular_file_at(
        prompts_fd,
        _R2F_PROMPT_TEMPLATE_FILENAME,
        maximum_bytes=1_048_576,
        too_large_code="WS01_BR_INTERNAL_INVARIANT_FAILURE",
        owner=owner,
    )
    if (
        _sha256(r2f_prompt_template) != _EXPECTED_R2F_PROMPT_TEMPLATE_SHA256
        or r2f_prompt_template.startswith(b"\xef\xbb\xbf")
        or b"\r" in r2f_prompt_template
        or not r2f_prompt_template.endswith(b"\n")
        or r2f_prompt_template.count(
            _R2F_PROMPT_TEMPLATE_PLACEHOLDER.encode("utf-8")
        )
        != 1
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return (
        _deep_freeze(parsed_schemas["weekly_shadow_01_analyst_input_v2"]),
        authenticated_surface,
        r2f_prompt_template,
    )


def _detach_contract_json(value: object) -> object:
    if isinstance(value, _MappingProxyType) or type(value) is dict:
        if any(type(key) is not str for key in value):
            _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        return {
            key: _detach_contract_json(item)
            for key, item in value.items()
        }
    if type(value) in (tuple, list):
        return [_detach_contract_json(item) for item in value]
    if type(value) in (str, int, bool, type(None)):
        return value
    _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")


def _contract_json_value(value: object) -> object:
    try:
        return _detach_contract_json(value)
    except (TypeError, ValueError):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")


def _identity_from_domains(
    domains: dict[str, bytes], domain_name: str, payload: dict[str, object]
) -> str:
    domain = domains.get(domain_name)
    if type(domain) is not bytes:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _sha256(domain + _canonical_ws01_json_bytes(payload))


def _compute_authenticated_surface_seal(payload: dict[str, object]) -> str:
    return _sha256(
        b"weekly_shadow_01_authenticated_contract_surface_v1\0"
        + _canonical_ws01_json_bytes(payload)
    )


def _bind_contract_comprehension_target(
    target: _ast.expr,
    value: object,
    local_values: dict[str, object],
) -> None:
    if isinstance(target, _ast.Name):
        local_values[target.id] = value
        return
    if isinstance(target, (_ast.Tuple, _ast.List)) and type(value) in (
        tuple,
        list,
    ):
        if len(target.elts) != len(value):
            raise _ContractSourceEvaluationError
        for child, item in zip(target.elts, value, strict=True):
            _bind_contract_comprehension_target(child, item, local_values)
        return
    raise _ContractSourceEvaluationError


def _contract_source_expression(
    node: _ast.expr,
    values: dict[str, object],
    local_values: dict[str, object] | None = None,
) -> object:
    local = {} if local_values is None else local_values
    if isinstance(node, _ast.Constant):
        if type(node.value) in (str, bytes, int, bool, type(None)):
            return node.value
        raise _ContractSourceEvaluationError
    if isinstance(node, _ast.Name):
        if node.id in local:
            return local[node.id]
        if node.id in values:
            return values[node.id]
        raise _ContractSourceEvaluationError
    if isinstance(node, _ast.Tuple):
        return tuple(
            _contract_source_expression(item, values, local) for item in node.elts
        )
    if isinstance(node, _ast.List):
        return [
            _contract_source_expression(item, values, local) for item in node.elts
        ]
    if isinstance(node, _ast.Set):
        return {
            _contract_source_expression(item, values, local) for item in node.elts
        }
    if isinstance(node, _ast.Dict):
        result: dict[object, object] = {}
        for key_node, value_node in zip(node.keys, node.values, strict=True):
            item = _contract_source_expression(value_node, values, local)
            if key_node is None:
                if type(item) is not dict:
                    raise _ContractSourceEvaluationError
                result.update(item)
                continue
            key = _contract_source_expression(key_node, values, local)
            if type(key) not in (str, int, bool, type(None)):
                raise _ContractSourceEvaluationError
            result[key] = item
        return result
    if isinstance(node, _ast.BinOp) and isinstance(node.op, _ast.Add):
        left = _contract_source_expression(node.left, values, local)
        right = _contract_source_expression(node.right, values, local)
        if type(left) is type(right) and type(left) in (str, bytes, tuple, list, int):
            return left + right
        raise _ContractSourceEvaluationError
    if isinstance(node, _ast.UnaryOp) and isinstance(
        node.op, (_ast.UAdd, _ast.USub)
    ):
        operand = _contract_source_expression(node.operand, values, local)
        if type(operand) is not int:
            raise _ContractSourceEvaluationError
        return operand if isinstance(node.op, _ast.UAdd) else -operand
    if isinstance(node, _ast.Subscript):
        container = _contract_source_expression(node.value, values, local)
        key = _contract_source_expression(node.slice, values, local)
        if type(container) not in (dict, tuple, list, str, bytes):
            raise _ContractSourceEvaluationError
        try:
            return container[key]
        except (IndexError, KeyError, TypeError) as exc:
            raise _ContractSourceEvaluationError from exc
    if isinstance(node, _ast.DictComp):
        if len(node.generators) != 1 or node.generators[0].ifs:
            raise _ContractSourceEvaluationError
        generator = node.generators[0]
        if generator.is_async:
            raise _ContractSourceEvaluationError
        iterable = _contract_source_expression(generator.iter, values, local)
        if type(iterable) not in (tuple, list):
            raise _ContractSourceEvaluationError
        result: dict[object, object] = {}
        for item in iterable:
            iteration_values = dict(local)
            _bind_contract_comprehension_target(
                generator.target, item, iteration_values
            )
            key = _contract_source_expression(node.key, values, iteration_values)
            member = _contract_source_expression(
                node.value, values, iteration_values
            )
            result[key] = member
        return result
    if not isinstance(node, _ast.Call) or any(
        keyword.arg is None for keyword in node.keywords
    ):
        raise _ContractSourceEvaluationError

    arguments = [
        _contract_source_expression(argument, values, local)
        for argument in node.args
    ]
    keywords = {
        keyword.arg: _contract_source_expression(keyword.value, values, local)
        for keyword in node.keywords
        if keyword.arg is not None
    }
    if isinstance(node.func, _ast.Name):
        name = node.func.id
        if name == "_MappingProxyType" and len(arguments) == 1 and not keywords:
            if type(arguments[0]) is not dict:
                raise _ContractSourceEvaluationError
            return arguments[0]
        if name in {"list", "tuple", "dict", "set", "frozenset"} and not keywords:
            if len(arguments) > 1:
                raise _ContractSourceEvaluationError
            argument = arguments[0] if arguments else ()
            constructors = {
                "list": list,
                "tuple": tuple,
                "dict": dict,
                "set": set,
                "frozenset": frozenset,
            }
            try:
                return constructors[name](argument)
            except (TypeError, ValueError) as exc:
                raise _ContractSourceEvaluationError from exc
        if name == "len" and len(arguments) == 1 and not keywords:
            try:
                return len(arguments[0])
            except TypeError as exc:
                raise _ContractSourceEvaluationError from exc
        if name == "_recipe_payload" and len(arguments) == 1 and not keywords:
            recipe = arguments[0]
            if type(recipe) is not dict:
                raise _ContractSourceEvaluationError
            return {
                key: list(member) if type(member) is tuple else member
                for key, member in recipe.items()
            }
        if name == "compute_identity" and len(arguments) == 2:
            domain_name, payload = arguments
            exclude_fields = keywords.get("exclude_fields", ())
            if (
                type(domain_name) is not str
                or type(payload) is not dict
                or type(exclude_fields) not in (tuple, list)
            ):
                raise _ContractSourceEvaluationError
            detached = {
                key: member
                for key, member in payload.items()
                if key not in set(exclude_fields)
            }
            domains = values.get("DOMAIN_SEPARATORS")
            if type(domains) is not dict:
                raise _ContractSourceEvaluationError
            return _identity_from_domains(domains, domain_name, detached)
        if name == "_semantic_contract_record" and len(arguments) == 1:
            schema_key = arguments[0]
            schemas = values.get("SCHEMA_IDENTITY_SHA256_BY_VERSION")
            if type(schema_key) is not str or type(schemas) is not dict:
                raise _ContractSourceEvaluationError
            required = {
                "owner",
                "relevant_blocking_reason_codes",
                "relevant_analyst_limitation_codes",
                "required_profile_identities_sha256",
            }
            if not required <= set(keywords) or set(keywords) - required != {
                "semantic_metadata"
            } and set(keywords) != required:
                raise _ContractSourceEvaluationError
            record = {
                "contract_version": f"{schema_key}_contract_v1",
                "contract_id": f"{schema_key}_semantic_contract",
                "schema_identity_sha256": schemas[schema_key],
                "owner": keywords["owner"],
                "ordered_relevant_blocking_reason_codes": list(
                    keywords["relevant_blocking_reason_codes"]
                ),
                "ordered_relevant_analyst_limitation_codes": list(
                    keywords["relevant_analyst_limitation_codes"]
                ),
                "required_profile_identities_sha256": list(
                    keywords["required_profile_identities_sha256"]
                ),
                "authority_effect": "none",
            }
            semantic_metadata = keywords.get("semantic_metadata")
            if semantic_metadata is not None:
                record["semantic_metadata"] = semantic_metadata
            return record
        raise _ContractSourceEvaluationError

    if isinstance(node.func, _ast.Attribute):
        if (
            node.func.attr == "hexdigest"
            and not arguments
            and not keywords
            and isinstance(node.func.value, _ast.Call)
            and isinstance(node.func.value.func, _ast.Attribute)
            and isinstance(node.func.value.func.value, _ast.Name)
            and node.func.value.func.value.id == "_hashlib"
            and node.func.value.func.attr == "sha256"
            and len(node.func.value.args) == 1
            and not node.func.value.keywords
        ):
            raw = _contract_source_expression(
                node.func.value.args[0], values, local
            )
            if type(raw) is not bytes:
                raise _ContractSourceEvaluationError
            return _sha256(raw)
        base = _contract_source_expression(node.func.value, values, local)
        if node.func.attr == "join" and type(base) is str and len(arguments) == 1:
            try:
                return base.join(arguments[0])
            except TypeError as exc:
                raise _ContractSourceEvaluationError from exc
        if node.func.attr == "encode" and type(base) is str:
            if keywords or len(arguments) > 1:
                raise _ContractSourceEvaluationError
            encoding = arguments[0] if arguments else "utf-8"
            if encoding != "utf-8":
                raise _ContractSourceEvaluationError
            return base.encode("utf-8")
        if node.func.attr == "hex" and type(base) is bytes and not arguments:
            return base.hex()
        if node.func.attr == "items" and type(base) is dict and not arguments:
            return tuple(base.items())
    raise _ContractSourceEvaluationError


def _contract_exports_from_source(contract_source: bytes) -> dict[str, object]:
    try:
        text = contract_source.decode("utf-8", errors="strict")
        tree = _ast.parse(text, filename="weekly_shadow_01_contracts.py")
        values: dict[str, object] = {}
        for statement in tree.body:
            target: _ast.expr | None = None
            expression: _ast.expr | None = None
            if isinstance(statement, _ast.AnnAssign):
                target = statement.target
                expression = statement.value
            elif (
                isinstance(statement, _ast.Assign)
                and len(statement.targets) == 1
            ):
                target = statement.targets[0]
                expression = statement.value
            if (
                not isinstance(target, _ast.Name)
                or expression is None
            ):
                continue
            values[target.id] = _contract_source_expression(expression, values)
        return values
    except (
        KeyError,
        SyntaxError,
        TypeError,
        UnicodeError,
        ValueError,
        _ContractSourceEvaluationError,
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")


def _verify_contract_profiles() -> None:
    negative_identity = _domain_identity(
        "negative_authority_profile",
        {
            "profile_version": "weekly_shadow_01_negative_authority_profile_v1",
            **dict(_NEGATIVE_AUTHORITY_PROFILE),
        },
    )
    resource_identity = _domain_identity(
        "resource_bound_profile",
        {
            "profile_version": "weekly_shadow_01_resource_bound_profile_v1",
            **dict(_RESOURCE_BOUND_PROFILE),
        },
    )
    prompt_raw_sha256 = _sha256(_PROMPT_TEMPLATE_BYTES)
    prompt_identity = _domain_identity(
        "prompt_template",
        {
            "profile_version": "weekly_shadow_01_prompt_template_v1",
            "encoding": "utf-8",
            "newline_convention": "lf_only",
            "byte_size": len(_PROMPT_TEMPLATE_BYTES),
            "placeholder": _PROMPT_TEMPLATE_PLACEHOLDER,
            "placeholder_occurrences": 1,
            "sha256": prompt_raw_sha256,
        },
    )
    if (
        negative_identity
        != "b20ea7218880c5799897d7d3fbd74515af88ad6fcc9e2f4c1d4cc83649e61ff1"
        or resource_identity
        != "acef986d2728660acce561f7c0d6a86fb0a942fa07ba8d3aea64bd061eee0e2e"
        or prompt_raw_sha256
        != "527b0b8fea23f9fd7265e6287bdd14da55280fb3340269eae49af062c5c5e25c"
        or prompt_identity
        != "e02839c54e4883af253158ab2295a61c2fe22ce483d296dae8af2e23bcd9dd37"
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")


def _authenticate_contract_surface(
    *,
    contract_source: bytes,
    expected_schema_files: dict[str, str],
    raw_schemas: dict[str, bytes],
    parsed_schemas: dict[str, dict[str, object]],
) -> _AuthenticatedContractSurface:
    expected_schema_rows = (
        ("weekly_shadow_01_analyst_input_v2", "41c6258b3d27b97554a785628ab3e990e0f1f89bbaad7d70a787dd230853f5f0"),
        ("weekly_shadow_01_analyst_response_v2", "3625d86dd84ae1243ccb4992e339d0935dff646c87e74f7792ecd635956ca160"),
        ("weekly_shadow_01_response_capture_v2", "a2f727e89e29f2a3ab9791d8274236f8481b2c30175eb07bc4d4bf458d429a95"),
        ("weekly_shadow_01_response_validation_v1", "2990ad8fc4f22de8b21691f54b3a967aed66e733078bd75ea74bc1330ee02f02"),
        ("weekly_shadow_01_analyst_report_v1", "7b415fa8eb7cb4ecce92ddf06eb394574f7d1435dd840657396dd2eeb0f4feb8"),
        ("weekly_shadow_01_run_summary_v1", "114e92f0d151bba7266a651172cd7dac01f9652a4c6fe47557582b10dcf706a7"),
    )
    expected_semantic_rows = (
        ("weekly_shadow_01_analyst_input_v2", "b49a1fa7bdd3affbf2c25c4f9184bcbdf54c9e1201327bad565c35c1de066eb1"),
        ("weekly_shadow_01_analyst_response_v2", "a3a14276ec697ad4e806f6c6d16250b95f279ba4c13aee573bbd8263039ea546"),
        ("weekly_shadow_01_response_capture_v2", "2ff319f61fd445458b9cb897e9a2db83265deb5c3ea93313a73572f39efab19b"),
        ("weekly_shadow_01_response_validation_v1", "3a41c1b6149aaa471d3dd94bd007b74cfcddbbdd97790bf00c7cdebd9b5000d5"),
        ("weekly_shadow_01_analyst_report_v1", "195112bf9087b1f63f680c93a77d41487e4bceae4564a621c55c15b6cb684014"),
        ("weekly_shadow_01_run_summary_v1", "88bc37d815c348fa0791c51fbdc660f2527c2d9975a01ab2bde2b9853c2a99b3"),
    )
    expected_profile_rows = (
        ("negative_authority", "b20ea7218880c5799897d7d3fbd74515af88ad6fcc9e2f4c1d4cc83649e61ff1"),
        ("resource_bound", "acef986d2728660acce561f7c0d6a86fb0a942fa07ba8d3aea64bd061eee0e2e"),
        ("prohibited_key", "88247b4e04877b3925a988bce9185181e4d2c4214cf7e58a51b415907651dc9c"),
        ("prohibited_intent", "5376a4e55d8bb6f1d79808355f5056e35e25a004869ba62ee6ff225f55f3b0ba"),
        ("prompt_template", "e02839c54e4883af253158ab2295a61c2fe22ce483d296dae8af2e23bcd9dd37"),
        ("run_status", "be57d34943541c65839ec1774387d70008633606705b888b64709087f72d6f8a"),
        ("analyst_conclusion", "210dc5e43311a54ad09daf0f4a64405d6ce999b0818c40f1f7d0da1f644ba9cd"),
        ("analyst_confidence", "6cd369043ddf149b7eba2a9550955ea7518ce3e48a1263499df9ab76548fed82"),
        ("blocking_reason", "4c105e9074f5c8fa8ab4e13ee6065aa4e99752cde33116932a6bcc134f784c47"),
        ("analyst_limitation", "0e9ac34fcc09308269385b6b80c4e5b62dfd74fa80ea3c9bbe24d54d84f0fefc"),
    )
    expected_domain_rows = (
        ("source_artifact", b"weekly_shadow_01_source_artifact_v1\0"),
        ("evidence_record", b"weekly_shadow_01_evidence_record_v1\0"),
        ("run", b"weekly_shadow_01_run_v1\0"),
        ("input_package", b"weekly_shadow_01_input_package_v1\0"),
        ("prompt_template", b"weekly_shadow_01_prompt_template_v1\0"),
        ("prompt_render", b"weekly_shadow_01_prompt_render_v1\0"),
        ("response_capture", b"weekly_shadow_01_response_capture_v1\0"),
        ("validation", b"weekly_shadow_01_validation_v1\0"),
        ("report", b"weekly_shadow_01_report_v1\0"),
        ("run_summary", b"weekly_shadow_01_run_summary_v1\0"),
        ("schema_identity", b"weekly_shadow_01_schema_identity_v1\0"),
        ("semantic_contract_identity", b"weekly_shadow_01_semantic_contract_identity_v1\0"),
        ("resource_bound_profile", b"weekly_shadow_01_resource_bound_profile_v1\0"),
        ("negative_authority_profile", b"weekly_shadow_01_negative_authority_profile_v1\0"),
        ("vocabulary_profile", b"weekly_shadow_01_vocabulary_profile_v1\0"),
        ("contract_catalog", b"weekly_shadow_01_contract_catalog_v1\0"),
    )
    expected_schema_identities = dict(expected_schema_rows)
    expected_semantic_identities = dict(expected_semantic_rows)
    expected_profile_identities = dict(expected_profile_rows)
    expected_domains = dict(expected_domain_rows)
    expected_contract_module_sha256 = "cc6659754275991a5d244aec8f26f725dc74d339be766cdf7694e97e6f19792a"
    expected_prompt_raw_sha256 = "527b0b8fea23f9fd7265e6287bdd14da55280fb3340269eae49af062c5c5e25c"
    expected_catalog_identity = "36a0f850a089c3276c62dfe677ebfbce1ee9d1289e0487c3aad358db6cb556d4"
    expected_runtime_surface_sha256 = "bb8baea1ec8c8418481fcc1bbbd0961ff427b11426219bb07bd3082dd502f0b5"
    expected_complete_surface_seal = (
        "f99f7a981fcbfa16524c5a9c505597f434dc1a64d9f50705a6a6cafb7ed88989"
    )

    if (
        _sha256(contract_source) != expected_contract_module_sha256
        or _EXPECTED_CONTRACT_MODULE_SHA256 != expected_contract_module_sha256
        or dict(_EXPECTED_DOMAIN_SEPARATORS) != expected_domains
        or dict(_EXPECTED_SCHEMA_IDENTITIES) != expected_schema_identities
        or dict(_EXPECTED_SEMANTIC_IDENTITIES) != expected_semantic_identities
        or dict(_EXPECTED_PROFILE_IDENTITIES) != expected_profile_identities
        or _EXPECTED_PROMPT_RAW_SHA256 != expected_prompt_raw_sha256
        or _EXPECTED_CONTRACT_CATALOG_IDENTITY != expected_catalog_identity
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    contract_values = _contract_exports_from_source(contract_source)
    actual_domain_mapping = contract_values.get("DOMAIN_SEPARATORS")
    if type(actual_domain_mapping) is not dict:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    actual_domains = dict(actual_domain_mapping)
    if actual_domains != expected_domains:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    _verify_contract_profiles()

    module_schema_files = _contract_json_value(
        contract_values["SCHEMA_FILENAME_BY_VERSION"]
    )
    module_schema_identities = _contract_json_value(
        contract_values["SCHEMA_IDENTITY_SHA256_BY_VERSION"]
    )
    if (
        module_schema_files != expected_schema_files
        or set(raw_schemas) != set(expected_schema_files)
        or set(parsed_schemas) != set(expected_schema_files)
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    recomputed_schema_identities: dict[str, str] = {}
    schema_raw_sha256: dict[str, str] = {}
    for version, relative_path in expected_schema_files.items():
        schema = parsed_schemas[version]
        recomputed_schema_identities[version] = _identity_from_domains(
            actual_domains,
            "schema_identity",
            {
                "schema_version": version,
                "schema_path": relative_path,
                "schema_id": schema.get("$id"),
                "schema": schema,
            },
        )
        schema_raw_sha256[version] = _sha256(raw_schemas[version])
    if (
        recomputed_schema_identities != expected_schema_identities
        or module_schema_identities != recomputed_schema_identities
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    semantic_records = _contract_json_value(
        contract_values["_SEMANTIC_CONTRACT_RECORDS"]
    )
    semantic_metadata = {
        "weekly_shadow_01_analyst_input_v2": _contract_json_value(
            contract_values["_ANALYST_INPUT_V2_SEMANTIC_METADATA"]
        ),
        "weekly_shadow_01_analyst_response_v2": _contract_json_value(
            contract_values["_ANALYST_RESPONSE_V2_SEMANTIC_METADATA"]
        ),
        "weekly_shadow_01_response_capture_v2": _contract_json_value(
            contract_values["_RESPONSE_CAPTURE_V2_SEMANTIC_METADATA"]
        ),
    }
    if type(semantic_records) is not dict or set(semantic_records) != set(
        expected_schema_files
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    for version, metadata in semantic_metadata.items():
        if semantic_records[version].get("semantic_metadata") != metadata:
            _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    recomputed_semantic_identities = {
        version: _identity_from_domains(
            actual_domains, "semantic_contract_identity", record
        )
        for version, record in semantic_records.items()
    }
    module_semantic_identities = _contract_json_value(
        contract_values["SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION"]
    )
    if (
        recomputed_semantic_identities != expected_semantic_identities
        or module_semantic_identities != recomputed_semantic_identities
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    prompt_bytes = contract_values["PROMPT_TEMPLATE_BYTES"]
    prompt_text = contract_values["PROMPT_TEMPLATE_TEXT"]
    prompt_placeholder = contract_values["PROMPT_TEMPLATE_PLACEHOLDER"]
    if (
        type(prompt_bytes) is not bytes
        or type(prompt_text) is not str
        or type(prompt_placeholder) is not str
        or prompt_bytes != prompt_text.encode("utf-8")
        or prompt_bytes != _PROMPT_TEMPLATE_BYTES
        or prompt_placeholder != _PROMPT_TEMPLATE_PLACEHOLDER
        or prompt_bytes.startswith(b"\xef\xbb\xbf")
        or b"\r" in prompt_bytes
        or not prompt_bytes.endswith(b"\n")
        or prompt_bytes.count(prompt_placeholder.encode("utf-8")) != 1
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    prompt_raw_sha256 = _sha256(prompt_bytes)

    normalization_steps = _contract_json_value(
        contract_values["_PROHIBITED_PROFILE_NORMALIZATION_STEPS"]
    )
    profile_payloads = {
        "negative_authority": {
            "profile_version": "weekly_shadow_01_negative_authority_profile_v1",
            **_contract_json_value(contract_values["NEGATIVE_AUTHORITY_PROFILE"]),
        },
        "resource_bound": {
            "profile_version": "weekly_shadow_01_resource_bound_profile_v1",
            **_contract_json_value(contract_values["RESOURCE_BOUND_PROFILE"]),
        },
        "prohibited_key": {
            "profile_version": "weekly_shadow_01_prohibited_key_profile_v1",
            "terms": _contract_json_value(contract_values["PROHIBITED_KEY_TERMS"]),
            "normalization_steps": normalization_steps,
            "applies_to": ["response_scanner_property_names"],
            "implemented_by": "WS01c",
        },
        "prohibited_intent": {
            "profile_version": "weekly_shadow_01_prohibited_intent_profile_v1",
            "terms": _contract_json_value(
                contract_values["PROHIBITED_INTENT_TERMS"]
            ),
            "normalization_steps": normalization_steps,
            "applies_to": ["response_scanner_free_text_content"],
            "implemented_by": "WS01c",
        },
        "prompt_template": {
            "profile_version": "weekly_shadow_01_prompt_template_v1",
            "encoding": "utf-8",
            "newline_convention": "lf_only",
            "byte_size": len(prompt_bytes),
            "placeholder": prompt_placeholder,
            "placeholder_occurrences": 1,
            "sha256": prompt_raw_sha256,
        },
        "run_status": {
            "profile_version": "weekly_shadow_01_run_status_vocabulary_v1",
            "values": _contract_json_value(contract_values["RUN_STATUS_VALUES"]),
            "owner": "deterministic_code",
            "authority_effect": "none",
        },
        "analyst_conclusion": {
            "profile_version": "weekly_shadow_01_analyst_conclusion_vocabulary_v1",
            "values": _contract_json_value(
                contract_values["ANALYST_CONCLUSION_VALUES"]
            ),
            "prohibited_values": _contract_json_value(
                contract_values["PROHIBITED_ANALYST_CONCLUSION_VALUES"]
            ),
            "owner": "llm_content_validated_by_code",
            "authority_effect": "none",
        },
        "analyst_confidence": {
            "profile_version": "weekly_shadow_01_analyst_confidence_vocabulary_v1",
            "values": _contract_json_value(
                contract_values["ANALYST_CONFIDENCE_VALUES"]
            ),
            "numeric_forms_prohibited": True,
            "owner": "llm_content_validated_by_code",
            "authority_effect": "none",
        },
        "blocking_reason": {
            "profile_version": "weekly_shadow_01_blocking_reason_vocabulary_v1",
            "values": _contract_json_value(
                contract_values["BLOCKING_REASON_CODES"]
            ),
            "owner": "deterministic_code",
            "authority_effect": "none",
        },
        "analyst_limitation": {
            "profile_version": "weekly_shadow_01_analyst_limitation_vocabulary_v1",
            "values": _contract_json_value(
                contract_values["ANALYST_LIMITATION_CODES"]
            ),
            "owner": "llm_content_validated_by_code",
            "authority_effect": "none",
        },
    }
    profile_domains = {
        "negative_authority": "negative_authority_profile",
        "resource_bound": "resource_bound_profile",
        "prohibited_key": "vocabulary_profile",
        "prohibited_intent": "vocabulary_profile",
        "prompt_template": "prompt_template",
        "run_status": "vocabulary_profile",
        "analyst_conclusion": "vocabulary_profile",
        "analyst_confidence": "vocabulary_profile",
        "blocking_reason": "vocabulary_profile",
        "analyst_limitation": "vocabulary_profile",
    }
    recomputed_profile_identities = {
        name: _identity_from_domains(actual_domains, profile_domains[name], payload)
        for name, payload in profile_payloads.items()
    }
    exported_profile_identities = {
        "negative_authority": contract_values[
            "NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256"
        ],
        "resource_bound": contract_values[
            "RESOURCE_BOUND_PROFILE_IDENTITY_SHA256"
        ],
        "prohibited_key": contract_values[
            "PROHIBITED_KEY_PROFILE_IDENTITY_SHA256"
        ],
        "prohibited_intent": contract_values[
            "PROHIBITED_INTENT_PROFILE_IDENTITY_SHA256"
        ],
        "prompt_template": contract_values["PROMPT_TEMPLATE_IDENTITY_SHA256"],
        "run_status": contract_values["RUN_STATUS_VOCABULARY_IDENTITY_SHA256"],
        "analyst_conclusion": contract_values[
            "ANALYST_CONCLUSION_VOCABULARY_IDENTITY_SHA256"
        ],
        "analyst_confidence": contract_values[
            "ANALYST_CONFIDENCE_VOCABULARY_IDENTITY_SHA256"
        ],
        "blocking_reason": contract_values[
            "BLOCKING_REASON_VOCABULARY_IDENTITY_SHA256"
        ],
        "analyst_limitation": contract_values[
            "ANALYST_LIMITATION_VOCABULARY_IDENTITY_SHA256"
        ],
    }
    if (
        prompt_raw_sha256 != expected_prompt_raw_sha256
        or recomputed_profile_identities != expected_profile_identities
        or exported_profile_identities != recomputed_profile_identities
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    catalog_payload = _contract_json_value(
        contract_values["_CONTRACT_CATALOG_PAYLOAD"]
    )
    if type(catalog_payload) is not dict:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    recomputed_catalog_identity = _identity_from_domains(
        actual_domains, "contract_catalog", catalog_payload
    )
    if (
        recomputed_catalog_identity != expected_catalog_identity
        or contract_values["CONTRACT_CATALOG_IDENTITY_SHA256"]
        != recomputed_catalog_identity
        or catalog_payload.get("schema_identity_sha256_by_version")
        != recomputed_schema_identities
        or catalog_payload.get("semantic_contract_identity_sha256_by_version")
        != recomputed_semantic_identities
        or catalog_payload.get("domain_separators_hex")
        != {name: value.hex() for name, value in actual_domains.items()}
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    grounding_metadata = {
        "WEEKLY_SHADOW_STAGE_VERSION": _contract_json_value(
            contract_values["WEEKLY_SHADOW_STAGE_VERSION"]
        ),
        "LEGACY_R2F_ADAPTER_ID": _contract_json_value(
            contract_values["LEGACY_R2F_ADAPTER_ID"]
        ),
        "R2F_SOURCE_GENERATION_ID_PATTERN": _contract_json_value(
            contract_values["R2F_SOURCE_GENERATION_ID_PATTERN"]
        ),
        "R2F_SOURCE_GENERATION_VERSION": _contract_json_value(
            contract_values["R2F_SOURCE_GENERATION_VERSION"]
        ),
        "CONSUMED_SOURCE_ARTIFACT_ROLES": _contract_json_value(
            contract_values["CONSUMED_SOURCE_ARTIFACT_ROLES"]
        ),
        "PERMANENTLY_UNCONSUMED_SOURCE_ARTIFACT_ROLE": _contract_json_value(
            contract_values["PERMANENTLY_UNCONSUMED_SOURCE_ARTIFACT_ROLE"]
        ),
        "INCOMPLETE_SOURCE_GENERATION_MARKER": _contract_json_value(
            contract_values["INCOMPLETE_SOURCE_GENERATION_MARKER"]
        ),
        "EVIDENCE_VALUE_VARIANTS": _contract_json_value(
            contract_values["EVIDENCE_VALUE_VARIANTS"]
        ),
        "ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS": _contract_json_value(
            contract_values["ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS"]
        ),
        "AVAILABILITY_STATUS_NORMALIZED_VALUE_FIELDS": _contract_json_value(
            contract_values["AVAILABILITY_STATUS_NORMALIZED_VALUE_FIELDS"]
        ),
        "DIAGNOSTIC_CODE_VALUES": _contract_json_value(
            contract_values["DIAGNOSTIC_CODE_VALUES"]
        ),
        "SOURCE_LOCATOR_TYPES": _contract_json_value(
            contract_values["SOURCE_LOCATOR_TYPES"]
        ),
        "AVAILABILITY_SUBJECTS": _contract_json_value(
            contract_values["AVAILABILITY_SUBJECTS"]
        ),
        "ACTIVE_ANCHOR_SOURCE_LOCATOR_CONTRACT": _contract_json_value(
            contract_values["ACTIVE_ANCHOR_SOURCE_LOCATOR_CONTRACT"]
        ),
        "AVAILABILITY_SOURCE_LOCATOR_CONTRACT": _contract_json_value(
            contract_values["AVAILABILITY_SOURCE_LOCATOR_CONTRACT"]
        ),
        "DIAGNOSTIC_SOURCE_LOCATOR_CONTRACT": _contract_json_value(
            contract_values["DIAGNOSTIC_SOURCE_LOCATOR_CONTRACT"]
        ),
        "PACKAGE_OWNED_SOURCE_CONTEXT": _contract_json_value(
            contract_values["PACKAGE_OWNED_SOURCE_CONTEXT"]
        ),
        "OBSOLETE_EVIDENCE_RECORD_FIELDS": _contract_json_value(
            contract_values["OBSOLETE_EVIDENCE_RECORD_FIELDS"]
        ),
        "OBSOLETE_ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS": _contract_json_value(
            contract_values["OBSOLETE_ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS"]
        ),
        "SOURCE_LOCATOR_SEMANTICS": _contract_json_value(
            contract_values["SOURCE_LOCATOR_SEMANTICS"]
        ),
        "LOGICAL_LOCATOR_DEFINITION": _contract_json_value(
            contract_values["LOGICAL_LOCATOR_DEFINITION"]
        ),
        "LOGICAL_LOCATOR_UNIQUENESS_RULES": _contract_json_value(
            contract_values["LOGICAL_LOCATOR_UNIQUENESS_RULES"]
        ),
        "EVIDENCE_VARIANT_RANKS": _contract_json_value(
            contract_values["EVIDENCE_VARIANT_RANKS"]
        ),
        "AVAILABILITY_SUBJECT_RANKS": _contract_json_value(
            contract_values["AVAILABILITY_SUBJECT_RANKS"]
        ),
        "EVIDENCE_RECORD_CANONICAL_ORDERING": _contract_json_value(
            contract_values["EVIDENCE_RECORD_CANONICAL_ORDERING"]
        ),
        "CANONICAL_EVIDENCE_ORDERING_RULES": _contract_json_value(
            contract_values["CANONICAL_EVIDENCE_ORDERING_RULES"]
        ),
        "CANONICAL_ORDER_INDEPENDENCE_INPUTS": _contract_json_value(
            contract_values["CANONICAL_ORDER_INDEPENDENCE_INPUTS"]
        ),
        "ANALYST_INPUT_SCHEMA_ENFORCED_CONSTRAINTS": _contract_json_value(
            contract_values["ANALYST_INPUT_SCHEMA_ENFORCED_CONSTRAINTS"]
        ),
        "WS01B_RUNTIME_DEFERRED_RESOURCE_BOUND_RESPONSIBILITIES": (
            _contract_json_value(
                contract_values[
                    "WS01B_RUNTIME_DEFERRED_RESOURCE_BOUND_RESPONSIBILITIES"
                ]
            )
        ),
        "WS01B_RUNTIME_BOUND_FAILURE_CODE": _contract_json_value(
            contract_values["WS01B_RUNTIME_BOUND_FAILURE_CODE"]
        ),
        "STATIC_RUNTIME_RESPONSIBILITY_TABLE": _contract_json_value(
            contract_values["STATIC_RUNTIME_RESPONSIBILITY_TABLE"]
        ),
        "WS01B_SOURCE_CORRELATION_RESPONSIBILITIES": _contract_json_value(
            contract_values["WS01B_SOURCE_CORRELATION_RESPONSIBILITIES"]
        ),
        "WS01C_RESPONSE_VALIDATION_RESPONSIBILITIES": _contract_json_value(
            contract_values["WS01C_RESPONSE_VALIDATION_RESPONSIBILITIES"]
        ),
        "DIAGNOSTIC_REFERENCE_INVARIANTS": _contract_json_value(
            contract_values["DIAGNOSTIC_REFERENCE_INVARIANTS"]
        ),
        "PROJECTION_EXCLUSIONS": _contract_json_value(
            contract_values["PROJECTION_EXCLUSIONS"]
        ),
        "EVIDENCE_RECORD_ID_RECIPE": _contract_json_value(
            contract_values["EVIDENCE_RECORD_ID_RECIPE"]
        ),
        "EVIDENCE_RECORD_IDENTITY_RECIPE": _contract_json_value(
            contract_values["EVIDENCE_RECORD_IDENTITY_RECIPE"]
        ),
        "INPUT_PACKAGE_IDENTITY_RECIPE": _contract_json_value(
            contract_values["INPUT_PACKAGE_IDENTITY_RECIPE"]
        ),
        "RESOURCE_BOUND_SCHEMA_OR_HELPER_ENFORCED_FIELDS": _contract_json_value(
            contract_values["RESOURCE_BOUND_SCHEMA_OR_HELPER_ENFORCED_FIELDS"]
        ),
        "RESOURCE_BOUND_RUNTIME_DEFERRED_FIELDS": _contract_json_value(
            contract_values["RESOURCE_BOUND_RUNTIME_DEFERRED_FIELDS"]
        ),
    }
    vocabulary_metadata = {
        "RUN_STATUS_VALUES": _contract_json_value(
            contract_values["RUN_STATUS_VALUES"]
        ),
        "ANALYST_CONCLUSION_VALUES": _contract_json_value(
            contract_values["ANALYST_CONCLUSION_VALUES"]
        ),
        "ANALYST_CONCLUSION_SEMANTICS": _contract_json_value(
            contract_values["ANALYST_CONCLUSION_SEMANTICS"]
        ),
        "ANALYST_CONFIDENCE_VALUES": _contract_json_value(
            contract_values["ANALYST_CONFIDENCE_VALUES"]
        ),
        "PROHIBITED_ANALYST_CONCLUSION_VALUES": _contract_json_value(
            contract_values["PROHIBITED_ANALYST_CONCLUSION_VALUES"]
        ),
        "VALIDATION_STATUS_VALUES": _contract_json_value(
            contract_values["VALIDATION_STATUS_VALUES"]
        ),
        "PUBLICATION_STATUS_VALUES": _contract_json_value(
            contract_values["PUBLICATION_STATUS_VALUES"]
        ),
        "BLOCKING_REASON_CODES": _contract_json_value(
            contract_values["BLOCKING_REASON_CODES"]
        ),
        "ANALYST_LIMITATION_CODES": _contract_json_value(
            contract_values["ANALYST_LIMITATION_CODES"]
        ),
        "PROHIBITED_KEY_TERMS": _contract_json_value(
            contract_values["PROHIBITED_KEY_TERMS"]
        ),
        "PROHIBITED_INTENT_TERMS": _contract_json_value(
            contract_values["PROHIBITED_INTENT_TERMS"]
        ),
    }

    runtime_surface = _runtime_contract_surface(
        contract_module_sha256=expected_contract_module_sha256,
        schema_identities=recomputed_schema_identities,
        semantic_identities=recomputed_semantic_identities,
        catalog_identity=recomputed_catalog_identity,
        domains=actual_domains,
        profile_payloads=profile_payloads,
        profile_identities=recomputed_profile_identities,
        prompt_bytes=prompt_bytes,
        prompt_placeholder=prompt_placeholder,
        grounding_metadata=grounding_metadata,
    )
    if _sha256(_canonical_ws01_json_bytes(runtime_surface)) != expected_runtime_surface_sha256:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    complete_surface = {
        "authenticated_surface_version": "weekly_shadow_01_authenticated_contract_surface_v1",
        "contract_module_sha256": expected_contract_module_sha256,
        "schema_filename_by_version": dict(expected_schema_files),
        "schema_raw_sha256_by_version": schema_raw_sha256,
        "schema_identity_sha256_by_version": recomputed_schema_identities,
        "semantic_metadata_by_version": semantic_metadata,
        "semantic_contract_records": semantic_records,
        "semantic_contract_identity_sha256_by_version": recomputed_semantic_identities,
        "contract_catalog_payload": catalog_payload,
        "contract_catalog_identity_sha256": recomputed_catalog_identity,
        "profile_identity_payloads": profile_payloads,
        "profile_identity_sha256": recomputed_profile_identities,
        "prompt_template": {
            "text": prompt_text,
            "raw_sha256": prompt_raw_sha256,
            "identity_sha256": recomputed_profile_identities["prompt_template"],
            "placeholder": prompt_placeholder,
        },
        "vocabulary_metadata": vocabulary_metadata,
        "domain_separators_hex": {
            name: value.hex() for name, value in actual_domains.items()
        },
        "grounding_metadata": grounding_metadata,
        "adapter": {
            "adapter_id": grounding_metadata["LEGACY_R2F_ADAPTER_ID"],
            "adapter_version": _ADAPTER_VERSION,
        },
        "source_generation_version": grounding_metadata[
            "R2F_SOURCE_GENERATION_VERSION"
        ],
        "runtime_surface_sha256": expected_runtime_surface_sha256,
    }
    seal = _compute_authenticated_surface_seal(complete_surface)
    if seal != expected_complete_surface_seal:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    frozen_runtime_surface = _deep_freeze(runtime_surface)
    authenticated = _AuthenticatedContractSurface(
        complete_surface=_deep_freeze(complete_surface),
        runtime_surface=frozen_runtime_surface,
        catalog_identity_sha256=recomputed_catalog_identity,
        seal_sha256=seal,
    )
    _require_authenticated_contract_surface(authenticated)
    return authenticated


def _runtime_contract_surface(
    *,
    contract_module_sha256: str,
    schema_identities: dict[str, str],
    semantic_identities: dict[str, str],
    catalog_identity: str,
    domains: dict[str, bytes],
    profile_payloads: dict[str, dict[str, object]],
    profile_identities: dict[str, str],
    prompt_bytes: bytes,
    prompt_placeholder: str,
    grounding_metadata: dict[str, object],
) -> dict[str, object]:
    record_id_recipe = grounding_metadata["EVIDENCE_RECORD_ID_RECIPE"]
    record_identity_recipe = grounding_metadata[
        "EVIDENCE_RECORD_IDENTITY_RECIPE"
    ]
    package_identity_recipe = grounding_metadata["INPUT_PACKAGE_IDENTITY_RECIPE"]
    return {
        "contract_surface_version": "weekly_shadow_01_runtime_contract_surface_v1",
        "contract_module_sha256": contract_module_sha256,
        "schema_identity_sha256_by_version": dict(schema_identities),
        "semantic_contract_identity_sha256_by_version": dict(semantic_identities),
        "contract_catalog_identity_sha256": catalog_identity,
        "domain_separators_hex": {
            name: value.hex() for name, value in domains.items()
        },
        "resource_bound_profile": {
            key: value
            for key, value in profile_payloads["resource_bound"].items()
            if key != "profile_version"
        },
        "resource_bound_profile_identity_sha256": profile_identities[
            "resource_bound"
        ],
        "negative_authority": {
            key: value
            for key, value in profile_payloads["negative_authority"].items()
            if key != "profile_version"
        },
        "negative_authority_profile_identity_sha256": profile_identities[
            "negative_authority"
        ],
        "prompt_template_text": prompt_bytes.decode("utf-8"),
        "prompt_template_raw_sha256": _sha256(prompt_bytes),
        "prompt_template_identity_sha256": profile_identities["prompt_template"],
        "prompt_template_placeholder": prompt_placeholder,
        "source_generation_version": grounding_metadata[
            "R2F_SOURCE_GENERATION_VERSION"
        ],
        "adapter_id": grounding_metadata["LEGACY_R2F_ADAPTER_ID"],
        "consumed_source_artifact_roles": grounding_metadata[
            "CONSUMED_SOURCE_ARTIFACT_ROLES"
        ],
        "permanently_unconsumed_source_artifact_role": grounding_metadata[
            "PERMANENTLY_UNCONSUMED_SOURCE_ARTIFACT_ROLE"
        ],
        "incomplete_source_generation_marker": grounding_metadata[
            "INCOMPLETE_SOURCE_GENERATION_MARKER"
        ],
        "availability_subjects": grounding_metadata["AVAILABILITY_SUBJECTS"],
        "diagnostic_code_values": grounding_metadata["DIAGNOSTIC_CODE_VALUES"],
        "evidence_variant_ranks": grounding_metadata["EVIDENCE_VARIANT_RANKS"],
        "availability_subject_ranks": grounding_metadata[
            "AVAILABILITY_SUBJECT_RANKS"
        ],
        "logical_locator_uniqueness_rules": grounding_metadata[
            "LOGICAL_LOCATOR_UNIQUENESS_RULES"
        ],
        "canonical_evidence_record_ordering": grounding_metadata[
            "EVIDENCE_RECORD_CANONICAL_ORDERING"
        ],
        "canonical_evidence_ordering_rules": grounding_metadata[
            "CANONICAL_EVIDENCE_ORDERING_RULES"
        ],
        "runtime_bound_responsibilities": grounding_metadata[
            "WS01B_RUNTIME_DEFERRED_RESOURCE_BOUND_RESPONSIBILITIES"
        ],
        "runtime_bound_failure_code": grounding_metadata[
            "WS01B_RUNTIME_BOUND_FAILURE_CODE"
        ],
        "static_runtime_responsibility_table": grounding_metadata[
            "STATIC_RUNTIME_RESPONSIBILITY_TABLE"
        ],
        "source_correlation_responsibilities": grounding_metadata[
            "WS01B_SOURCE_CORRELATION_RESPONSIBILITIES"
        ],
        "diagnostic_reference_invariants": grounding_metadata[
            "DIAGNOSTIC_REFERENCE_INVARIANTS"
        ],
        "prohibited_conclusion_ids": profile_payloads["analyst_conclusion"][
            "prohibited_values"
        ],
        "evidence_record_locator_recipe": {
            "domain_name": record_id_recipe["domain_name"],
            "payload_kind": record_id_recipe["payload_kind"],
            "record_contract_version": record_id_recipe[
                "record_contract_version"
            ],
            "normalized_value_included": record_id_recipe[
                "normalized_value_included"
            ],
        },
        "evidence_record_identity_recipe": {
            "domain_name": record_identity_recipe["domain_name"],
            "payload_kind": record_identity_recipe["payload_kind"],
            "excluded_fields": record_identity_recipe["excluded_fields"],
        },
        "input_package_identity_recipe": {
            "domain_name": package_identity_recipe["domain_name"],
            "excluded_fields": package_identity_recipe["excluded_fields"],
        },
    }


def _require_authenticated_contract_surface(
    value: _AuthenticatedContractSurface,
) -> None:
    if (
        type(value) is not _AuthenticatedContractSurface
        or not isinstance(value.complete_surface, _MappingProxyType)
        or not isinstance(value.runtime_surface, _MappingProxyType)
        or not _is_sha256(value.catalog_identity_sha256)
        or not _is_sha256(value.seal_sha256)
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    complete_surface = _deep_thaw(value.complete_surface)
    runtime_surface = _deep_thaw(value.runtime_surface)
    if (
        type(complete_surface) is not dict
        or type(runtime_surface) is not dict
        or _compute_authenticated_surface_seal(complete_surface)
        != value.seal_sha256
        or complete_surface.get("runtime_surface_sha256")
        != _sha256(_canonical_ws01_json_bytes(runtime_surface))
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")


def _verify_generation_at(
    *,
    generation_fd: int,
    generation_id: str,
    analyst_input_schema: "Mapping[str, Any]",
    authenticated_contract_surface: _AuthenticatedContractSurface,
    r2f_prompt_template: bytes,
    owner: _DescriptorOwner,
) -> _VerifiedR2FGeneration:
    generation_directory_state = _directory_inventory_state(
        _os.fstat(generation_fd)
    )
    names = _generation_entry_names(generation_fd)
    if _IN_PROGRESS_FILENAME in names:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if names != _COMPLETED_FILENAMES:
        _raise("WS01_BR_SOURCE_ARTIFACT_SET_MISMATCH")

    maximum = _RESOURCE_BOUND_PROFILE["source_artifact_max_bytes"]
    total_maximum = _RESOURCE_BOUND_PROFILE[
        "source_artifacts_total_max_bytes"
    ]
    first_snapshots: dict[str, _StableFileSnapshot] = {}
    raw_by_name: dict[str, bytes] = {}
    total = 0
    for filename in _CONSUMED_SOURCE_ARTIFACT_ROLES:
        snapshot = _read_stable_regular_file_snapshot_at(
            generation_fd,
            filename,
            maximum_bytes=maximum,
            too_large_code="WS01_BR_RESOURCE_BOUND_EXCEEDED",
            owner=owner,
        )
        raw = snapshot.raw_bytes
        total += len(raw)
        if total > total_maximum:
            _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
        first_snapshots[filename] = snapshot
        raw_by_name[filename] = raw

    if (
        _directory_inventory_state(_os.fstat(generation_fd))
        != generation_directory_state
        or _generation_entry_names(generation_fd) != _COMPLETED_FILENAMES
    ):
        _raise("WS01_BR_SOURCE_READ_UNSTABLE")
    for filename in _CONSUMED_SOURCE_ARTIFACT_ROLES:
        reopened = _read_stable_regular_file_snapshot_at(
            generation_fd,
            filename,
            maximum_bytes=maximum,
            too_large_code="WS01_BR_SOURCE_READ_UNSTABLE",
            owner=owner,
        )
        original = first_snapshots[filename]
        if reopened.state != original.state or reopened.raw_bytes != original.raw_bytes:
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
    if (
        _directory_inventory_state(_os.fstat(generation_fd))
        != generation_directory_state
        or _generation_entry_names(generation_fd) != _COMPLETED_FILENAMES
    ):
        _raise("WS01_BR_SOURCE_READ_UNSTABLE")

    manifest = _parse_json_object(
        raw_by_name[_MANIFEST_FILENAME], "WS01_BR_SOURCE_GENERATION_INVALID"
    )
    evidence = _parse_json_object(
        raw_by_name[_EVIDENCE_FILENAME], "WS01_BR_SOURCE_GENERATION_INVALID"
    )
    render_binding = _parse_json_object(
        raw_by_name[_RENDER_BINDING_FILENAME],
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )
    for parsed_value in (manifest, evidence, render_binding):
        _validate_source_json_resource_tree(parsed_value, depth=1)
    if (
        raw_by_name[_MANIFEST_FILENAME] != _r2f_json_file_bytes(manifest)
        or raw_by_name[_EVIDENCE_FILENAME] != _r2f_json_file_bytes(evidence)
        or raw_by_name[_RENDER_BINDING_FILENAME]
        != _r2f_json_file_bytes(render_binding)
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")

    _validate_manifest(manifest)
    _validate_evidence_packet(evidence, manifest=manifest)
    evaluation_timestamp = _source_bound_evaluation_timestamp_utc(
        manifest,
        evidence=evidence,
        render_binding=render_binding,
        generation_id=generation_id,
    )
    if manifest["evidence_packet"]["file_sha256"] != _sha256(
        raw_by_name[_EVIDENCE_FILENAME]
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    prompt_bytes = raw_by_name[_PROMPT_FILENAME]
    _validate_prompt_bytes(prompt_bytes)
    _validate_prompt_contract(
        manifest,
        evidence=evidence,
        prompt_bytes=prompt_bytes,
        template_bytes=r2f_prompt_template,
    )
    _validate_generation_identity(manifest, generation_id=generation_id)
    _validate_render_binding(
        render_binding,
        generation_id=generation_id,
        manifest=manifest,
        evidence=evidence,
        raw_by_name=raw_by_name,
    )

    bindings = tuple(
        _source_artifact_binding(
            source_id=source_id,
            generation_id=generation_id,
            raw=raw_by_name[source_id],
            parsed=(
                manifest
                if source_id == _MANIFEST_FILENAME
                else evidence
                if source_id == _EVIDENCE_FILENAME
                else render_binding
                if source_id == _RENDER_BINDING_FILENAME
                else None
            ),
        )
        for source_id in _CONSUMED_SOURCE_ARTIFACT_ROLES
    )
    if tuple(item.source_id for item in bindings) != tuple(
        _CONSUMED_SOURCE_ARTIFACT_ROLES
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    if (
        _directory_inventory_state(_os.fstat(generation_fd))
        != generation_directory_state
    ):
        _raise("WS01_BR_SOURCE_READ_UNSTABLE")
    return _new_verified_r2f_generation(
        adapter_id=_LEGACY_R2F_ADAPTER_ID,
        adapter_version=_ADAPTER_VERSION,
        source_generation_id=generation_id,
        source_generation_version=_COMPATIBILITY_PROFILE,
        evaluation_timestamp_utc=evaluation_timestamp,
        source_artifact_bindings=bindings,
        manifest=_deep_freeze(manifest),
        evidence_packet=_deep_freeze(evidence),
        analyst_input_schema=analyst_input_schema,
        contract_surface=authenticated_contract_surface.runtime_surface,
        authenticated_contract_surface=authenticated_contract_surface,
        contract_catalog_identity_sha256=(
            authenticated_contract_surface.catalog_identity_sha256
        ),
    )


def _validate_manifest(payload: dict[str, object]) -> None:
    if set(payload) != _MANIFEST_KEYS:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if payload.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
        _raise("WS01_BR_SOURCE_VERSION_UNSUPPORTED")
    if payload.get("compatibility_profile") != _COMPATIBILITY_PROFILE:
        _raise("WS01_BR_SOURCE_VERSION_UNSUPPORTED")
    _validate_authority(payload)
    as_of = _validated_as_of(payload.get("as_of"))
    if payload.get("generated_at") != f"{as_of}T00:00:00+00:00":
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    if payload.get("capture_profile") != _CAPTURE_PROFILE:
        _raise("WS01_BR_SOURCE_VERSION_UNSUPPORTED")

    inputs = payload.get("inputs")
    if type(inputs) is not dict or set(inputs) != set(_INPUT_PATHS):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    for name, expected_path in _INPUT_PATHS.items():
        record = inputs.get(name)
        if type(record) is not dict or set(record) != _INPUT_RECORD_KEYS:
            _raise("WS01_BR_SOURCE_GENERATION_INVALID")
        if record.get("path") != expected_path:
            _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
        if record.get("source_version") != _SOURCE_VERSIONS[name]:
            _raise("WS01_BR_SOURCE_VERSION_UNSUPPORTED")
        if not _is_sha256(record.get("file_sha256")) or not _is_sha256(
            record.get("production_text_sha256")
        ):
            _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    if payload.get("supported_source_versions") != dict(_SOURCE_VERSIONS):
        _raise("WS01_BR_SOURCE_VERSION_UNSUPPORTED")
    if not _is_sha256(payload.get("parsed_decision_relevant_settings_sha256")):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")

    evidence = payload.get("evidence_packet")
    if type(evidence) is not dict or set(evidence) != _EVIDENCE_BINDING_KEYS:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if evidence.get("schema_version") != _EVIDENCE_SCHEMA_VERSION:
        _raise("WS01_BR_SOURCE_VERSION_UNSUPPORTED")
    if not _is_sha256(evidence.get("file_sha256")) or not _is_sha256(
        evidence.get("canonical_content_sha256")
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")

    registry = payload.get("active_registry")
    if type(registry) is not dict or set(registry) != _REGISTRY_BINDING_KEYS:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    selected_source = registry.get("selected_source")
    schema_version = registry.get("schema_version")
    if selected_source == "approvals_inclusive":
        expected_registry_version = _APPROVALS_REGISTRY_SCHEMA_VERSION
    elif selected_source in {"baseline_fallback", "fail_closed_empty"}:
        expected_registry_version = _BASELINE_REGISTRY_SCHEMA_VERSION
    else:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if schema_version != expected_registry_version:
        _raise("WS01_BR_SOURCE_VERSION_UNSUPPORTED")
    if not _is_sha256(registry.get("canonical_content_sha256")):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")

    domain = payload.get("domain_validation")
    if type(domain) is not dict or set(domain) != _DOMAIN_VALIDATION_KEYS:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    diagnostics = domain.get("diagnostics")
    if (
        domain.get("status") != "DOMAIN_VALID_BUT_NONACTIVATING"
        or type(diagnostics) is not list
        or any(type(item) is not str for item in diagnostics)
        or diagnostics != sorted(set(diagnostics))
        or any(item not in _ALLOWED_DOMAIN_DIAGNOSTICS for item in diagnostics)
    ):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    _validate_prompt_contract_record(payload.get("prompt_contract"))


def _validate_prompt_contract_record(value: object) -> None:
    if type(value) is not dict or set(value) != _PROMPT_CONTRACT_KEYS:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    projection = value.get("projection")
    if type(projection) is not dict or set(projection) != _PROMPT_CONTRACT_PROJECTION_KEYS:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    expected = {
        "schema_version": _PROMPT_CONTRACT_SCHEMA_VERSION,
        "template_id": _PROMPT_TEMPLATE_ID,
        "renderer_profile": _PROMPT_RENDERER_PROFILE,
        "raw_memo_schema_version": _RAW_MEMO_SCHEMA_VERSION,
        "evidence_projection_profile": _PROMPT_PROJECTION_SCHEMA_VERSION,
        "universe_projection_profile": _UNIVERSE_PROJECTION_PROFILE,
        "active_anchor_projection_profile": _ACTIVE_ANCHOR_PROJECTION_PROFILE,
        "text_encoding_profile": _TEXT_ENCODING_PROFILE,
    }
    if any(projection.get(key) != item for key, item in expected.items()):
        _raise("WS01_BR_SOURCE_VERSION_UNSUPPORTED")
    if not _is_sha256(projection.get("template_file_sha256")):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    if value.get("canonical_content_sha256") != _canonical_r2f_sha256(projection):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    if (
        value.get("prompt_projection_schema_version")
        != _PROMPT_PROJECTION_SCHEMA_VERSION
        or not _is_sha256(value.get("prompt_projection_canonical_sha256"))
        or not _is_sha256(value.get("analyst_memo_prompt_file_sha256"))
        or value.get("raw_memo_schema_version") != _RAW_MEMO_SCHEMA_VERSION
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")


def _validate_evidence_packet(
    payload: dict[str, object], *, manifest: dict[str, object]
) -> None:
    if set(payload) != _EVIDENCE_TOP_LEVEL_KEYS:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    _validate_authority(payload)
    if (
        payload.get("schema_version") != _EVIDENCE_SCHEMA_VERSION
        or payload.get("is_llm_generated") is not False
        or payload.get("source") != "deterministic_inputs"
    ):
        _raise("WS01_BR_SOURCE_VERSION_UNSUPPORTED")
    if payload.get("generated_at") != f"{manifest['as_of']}T00:00:00+00:00":
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    if (
        payload.get("strategy_settings_hash")
        != manifest["parsed_decision_relevant_settings_sha256"]
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    if any(name in payload for name in _LLM_MEMO_FIELDS):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if type(payload.get("data_gaps")) is not list:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    source_artifacts = payload.get("source_artifacts")
    if source_artifacts != dict(_INPUT_PATHS):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    budget = payload.get("budget_settings")
    if type(budget) is not dict or not {
        "hard_cap_open_orders_budget",
        "target_new_buy_budget_this_run",
        "max_new_tickers_per_week",
    }.issubset(budget):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    universe = payload.get("universe")
    if type(universe) is not dict or set(universe) != _UNIVERSE_KEYS:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    for key in (
        "core_universe",
        "satellite_universe",
        "approved_extended_etf",
        "allowed_buy_tickers",
    ):
        values = universe.get(key)
        if (
            type(values) is not list
            or any(
                type(item) is not str
                or item == ""
                or item != item.strip().upper()
                for item in values
            )
            or len(values) != len(set(values))
        ):
            _raise("WS01_BR_SOURCE_GENERATION_INVALID")

    for subject in _AVAILABILITY_SUBJECTS:
        projected = _project_availability(payload.get(subject))
        diagnostics = manifest["domain_validation"]["diagnostics"]
        expected_code = (
            "MARKET_METRICS_UNAVAILABLE"
            if subject == "market_metrics"
            else "SCHEDULED_EVENTS_UNAVAILABLE"
        )
        if (not projected["available"]) != (expected_code in diagnostics):
            _raise("WS01_BR_SOURCE_BINDING_MISMATCH")

    registry = payload.get("active_anchor_registry")
    _validate_active_registry(registry)
    manifest_registry = manifest["active_registry"]
    if (
        manifest_registry["schema_version"] != registry["schema_version"]
        or manifest_registry["canonical_content_sha256"]
        != _canonical_r2f_sha256(registry)
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    manifest_evidence = manifest["evidence_packet"]
    if (
        manifest_evidence["schema_version"] != payload["schema_version"]
        or manifest_evidence["canonical_content_sha256"]
        != _canonical_r2f_sha256(payload)
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")


def _validate_active_registry(value: object) -> None:
    if type(value) is not dict:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if value.get("schema_version") not in {
        _BASELINE_REGISTRY_SCHEMA_VERSION,
        _APPROVALS_REGISTRY_SCHEMA_VERSION,
    }:
        _raise("WS01_BR_SOURCE_VERSION_UNSUPPORTED")
    if (
        value.get("is_llm_generated") is not False
        or value.get("report_only") is not True
        or value.get("permission_effect") != "none"
        or value.get("not_authorization") is not True
        or value.get("registry_valid") is not True
    ):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    rows = value.get("active_anchors")
    if type(rows) is not list:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    seen: set[str] = set()
    for row in rows:
        projected = _project_active_anchor(row)
        anchor_id = projected["anchor_id"]
        assert type(anchor_id) is str
        if anchor_id in seen:
            _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
        seen.add(anchor_id)


def _validate_prompt_contract(
    manifest: dict[str, object],
    *,
    evidence: dict[str, object],
    prompt_bytes: bytes,
    template_bytes: bytes,
) -> None:
    prompt_contract = manifest["prompt_contract"]
    projection = _bounded_r2f_prompt_projection(evidence, as_of=manifest["as_of"])
    projection_bytes = _json.dumps(
        projection,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    expected_prompt = template_bytes.replace(
        _R2F_PROMPT_TEMPLATE_PLACEHOLDER.encode("utf-8"),
        projection_bytes,
    )
    if (
        prompt_contract["projection"]["template_file_sha256"]
        != _sha256(template_bytes)
        or prompt_bytes != expected_prompt
        or prompt_contract["prompt_projection_canonical_sha256"]
        != _canonical_r2f_sha256(projection)
        or prompt_contract["analyst_memo_prompt_file_sha256"]
        != _sha256(prompt_bytes)
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")


def _validate_generation_identity(
    manifest: dict[str, object], *, generation_id: str
) -> None:
    identity = _semantic_generation_identity(manifest)
    if _canonical_r2f_sha256(identity) != generation_id:
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")


def _semantic_generation_identity(manifest: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": _GENERATION_IDENTITY_SCHEMA_VERSION,
        "manifest_schema_version": _MANIFEST_SCHEMA_VERSION,
        "evidence_schema_version": manifest["evidence_packet"]["schema_version"],
        "compatibility_profile": _COMPATIBILITY_PROFILE,
        "capture_profile": manifest["capture_profile"],
        "as_of": manifest["as_of"],
        "inputs": {
            name: {
                "path": manifest["inputs"][name]["path"],
                "source_version": manifest["inputs"][name]["source_version"],
                "file_sha256": manifest["inputs"][name]["file_sha256"],
                "production_text_sha256": manifest["inputs"][name][
                    "production_text_sha256"
                ],
            }
            for name in _INPUT_PATHS
        },
        "supported_source_versions": dict(manifest["supported_source_versions"]),
        "parsed_decision_relevant_settings_sha256": manifest[
            "parsed_decision_relevant_settings_sha256"
        ],
        "active_registry": dict(manifest["active_registry"]),
        "evidence_packet": dict(manifest["evidence_packet"]),
        "authority_markers": dict(_AUTHORITY_MARKERS),
        "prompt_contract": dict(manifest["prompt_contract"]),
    }


def _validate_render_binding(
    payload: dict[str, object],
    *,
    generation_id: str,
    manifest: dict[str, object],
    evidence: dict[str, object],
    raw_by_name: dict[str, bytes],
) -> None:
    if set(payload) != _RENDER_BINDING_KEYS:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    prompt_contract = manifest["prompt_contract"]
    expected = {
        "schema_version": _RENDER_BINDING_SCHEMA_VERSION,
        "compatibility_profile": _COMPATIBILITY_PROFILE,
        "generation_id": generation_id,
        "scope": "IMMUTABLE_RENDER_ARTIFACTS_AND_INITIAL_BLANK_MEMO_ONLY",
        "render_complete": True,
        "immutable_render_artifacts": {
            _MANIFEST_FILENAME: {
                "schema_version": _MANIFEST_SCHEMA_VERSION,
                "file_sha256": _sha256(raw_by_name[_MANIFEST_FILENAME]),
                "canonical_content_sha256": _canonical_r2f_sha256(manifest),
                "mutable_after_render": False,
            },
            _EVIDENCE_FILENAME: {
                "schema_version": _EVIDENCE_SCHEMA_VERSION,
                "file_sha256": _sha256(raw_by_name[_EVIDENCE_FILENAME]),
                "canonical_content_sha256": _canonical_r2f_sha256(evidence),
                "mutable_after_render": False,
            },
            _PROMPT_FILENAME: {
                "media_type": "text/plain; charset=utf-8",
                "file_sha256": _sha256(raw_by_name[_PROMPT_FILENAME]),
                "mutable_after_render": False,
            },
        },
        "operator_editable_inputs": {
            _RAW_MEMO_FILENAME: {
                "media_type": "text/plain; charset=utf-8",
                "initial_file_sha256": _sha256(b""),
                "initial_state": "BLANK",
                "operator_editable_after_render": True,
                "render_witness_attests_initial_bytes_only": True,
            }
        },
        "generation_identity": {
            "schema_version": _GENERATION_IDENTITY_SCHEMA_VERSION,
            "prompt_contract_canonical_sha256": prompt_contract[
                "canonical_content_sha256"
            ],
            "analyst_memo_prompt_file_sha256": prompt_contract[
                "analyst_memo_prompt_file_sha256"
            ],
            "raw_memo_schema_version": _RAW_MEMO_SCHEMA_VERSION,
        },
        **dict(_AUTHORITY_MARKERS),
    }
    if payload != expected:
        if (
            payload.get("schema_version") != _RENDER_BINDING_SCHEMA_VERSION
            or payload.get("compatibility_profile") != _COMPATIBILITY_PROFILE
        ):
            _raise("WS01_BR_SOURCE_VERSION_UNSUPPORTED")
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    generation_binding = payload.get("generation_identity")
    if type(generation_binding) is not dict or set(generation_binding) != (
        _GENERATION_IDENTITY_BINDING_KEYS
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")


def _source_artifact_binding(
    *,
    source_id: str,
    generation_id: str,
    raw: bytes,
    parsed: dict[str, object] | None,
) -> _SourceArtifactBinding:
    canonical = _canonical_r2f_sha256(parsed) if parsed is not None else None
    relative_path = "/".join(
        (*_R2F_ROOT_PARTS, _GENERATIONS_DIRECTORY, generation_id, source_id)
    )
    identity_payload = {
        "payload_kind": "weekly_shadow_01_verified_source_artifact_v1",
        "source_generation_id": generation_id,
        "source_generation_version": _COMPATIBILITY_PROFILE,
        "source_id": source_id,
        "repository_relative_path": relative_path,
        "media_type": (
            "application/json" if source_id.endswith(".json") else "text/plain; charset=utf-8"
        ),
        "byte_size": len(raw),
        "file_sha256": _sha256(raw),
        "canonical_content_sha256": canonical,
    }
    return _new_source_artifact_binding(
        source_id=source_id,
        source_artifact_identity_sha256=_domain_identity(
            "source_artifact", identity_payload
        ),
        byte_size=len(raw),
        file_sha256=_sha256(raw),
        canonical_content_sha256=canonical,
    )


def _project_active_anchor(value: object) -> dict[str, object]:
    value = _deep_thaw(value)
    if type(value) is not dict:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    required_projection_fields = {
        "anchor_id",
        "applicable_tickers",
        "anchor_date_et",
        "valid_from",
        "valid_until",
        "confidence_floor",
        "summary",
        "validation",
    }
    if not required_projection_fields.issubset(value):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    anchor_id = value.get("anchor_id")
    tickers = value.get("applicable_tickers")
    validation = value.get("validation")
    if type(anchor_id) is str and len(anchor_id) > 2_048:
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    if not _bounded_nonempty_text(anchor_id):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if (
        type(tickers) is not list
        or not tickers
        or any(type(item) is not str for item in tickers)
        or len(tickers) != len(set(tickers))
    ):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if len(tickers) > 1_017 or any(len(item) > 2_048 for item in tickers):
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    if any(not _bounded_nonempty_text(item) for item in tickers):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    for field in ("anchor_date_et", "valid_from", "valid_until"):
        item = value.get(field)
        if item is not None:
            _validated_iso_date(item)
    if value.get("confidence_floor") not in {"low", "medium", "high"}:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    summary = value.get("summary")
    if type(summary) is str and len(summary) > 2_048:
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    if summary is not None and type(summary) is not str:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if (
        type(validation) is not dict
        or "stale" not in validation
        or type(validation.get("stale")) is not bool
    ):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    return {
        "anchor_id": anchor_id,
        "normalized_value": {
            "applicable_tickers": list(tickers),
            "anchor_date_et": value.get("anchor_date_et"),
            "valid_from": value.get("valid_from"),
            "valid_until": value.get("valid_until"),
            "confidence_floor": value.get("confidence_floor"),
            "summary": summary,
            "validation": {"stale": validation["stale"]},
        },
    }


def _project_availability(value: object) -> dict[str, object]:
    value = _deep_thaw(value)
    if type(value) is not dict or set(value) != {"available", "data_gap"}:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    available = value.get("available")
    data_gap = value.get("data_gap")
    if type(available) is not bool:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if type(data_gap) is str and len(data_gap) > 2_048:
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    if data_gap is not None and type(data_gap) is not str:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    return {"available": available, "data_gap": data_gap}


def _bounded_r2f_prompt_projection(
    evidence: dict[str, object], *, as_of: object
) -> dict[str, object]:
    universe = evidence["universe"]
    eligible: list[dict[str, str]] = []
    seen: set[str] = set()
    for category, key in (
        ("BASE_EVIDENCE_UNIVERSE", "allowed_buy_tickers"),
        ("APPROVED_EXTENDED_OBSERVATION_ONLY", "approved_extended_etf"),
    ):
        for instrument_id in universe[key]:
            if instrument_id in seen:
                continue
            seen.add(instrument_id)
            eligible.append(
                {"instrument_id": instrument_id, "universe_category": category}
            )
    active_anchors = []
    for row in evidence["active_anchor_registry"]["active_anchors"]:
        projected = _project_active_anchor(row)
        normalized = projected["normalized_value"]
        active_anchors.append(
            {
                "anchor_id": projected["anchor_id"],
                "applicable_tickers": list(normalized["applicable_tickers"]),
                "anchor_date_et": normalized["anchor_date_et"],
                "valid_from": normalized["valid_from"],
                "valid_until": normalized["valid_until"],
                "confidence_floor": normalized["confidence_floor"],
                "summary": normalized["summary"],
            }
        )
    active_anchors.sort(key=lambda row: row["anchor_id"])
    return {
        "schema_version": _PROMPT_PROJECTION_SCHEMA_VERSION,
        "as_of": as_of,
        "eligible_instruments": eligible,
        "active_anchors": active_anchors,
        "research_context": {
            subject: _project_availability(evidence[subject])
            for subject in _AVAILABILITY_SUBJECTS
        },
    }


def _require_verified_generation_shape(value: _VerifiedR2FGeneration) -> None:
    _require_authenticated_contract_surface(value.authenticated_contract_surface)
    authenticated_runtime_surface = (
        value.authenticated_contract_surface.runtime_surface
    )
    if (
        value.adapter_id != _LEGACY_R2F_ADAPTER_ID
        or value.adapter_version != _ADAPTER_VERSION
        or _GENERATION_ID.fullmatch(value.source_generation_id) is None
        or value.source_generation_version != _COMPATIBILITY_PROFILE
        or value.contract_catalog_identity_sha256
        != authenticated_runtime_surface["contract_catalog_identity_sha256"]
        or tuple(item.source_id for item in value.source_artifact_bindings)
        != tuple(_CONSUMED_SOURCE_ARTIFACT_ROLES)
        or value.contract_surface is not authenticated_runtime_surface
    ):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    if any(
        type(item) is not _SourceArtifactBinding
        or not _is_sha256(item.source_artifact_identity_sha256)
        for item in value.source_artifact_bindings
    ):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")


def _require_snapshot_identity(snapshot: _VerifiedSourceSnapshot) -> None:
    payload = {
        "payload_kind": "weekly_shadow_01_verified_source_snapshot_v1",
        "adapter_id": snapshot.adapter_id,
        "adapter_version": snapshot.adapter_version,
        "source_generation_id": snapshot.source_generation_id,
        "source_generation_version": snapshot.source_generation_version,
        "evaluation_timestamp_utc": snapshot.evaluation_timestamp_utc,
        "source_artifact_bindings": [
            binding.to_package_dict() for binding in snapshot.source_artifact_bindings
        ],
        "active_anchors": _deep_thaw(snapshot.active_anchors),
        "availability_statuses": _deep_thaw(snapshot.availability_statuses),
        "representation_diagnostics": list(snapshot.representation_diagnostics),
        ("contract_catalog_" + "identity_sha256"): (
            snapshot.contract_catalog_identity_sha256
        ),
        "contract_surface": _deep_thaw(snapshot.contract_surface),
    }
    expected = _sha256(
        _EXPECTED_DOMAIN_SEPARATORS["source_artifact"]
        + _canonical_ws01_json_bytes(payload)
    )
    if expected != snapshot.snapshot_identity_sha256:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")


def _validate_prompt_bytes(value: bytes) -> None:
    if (
        value.startswith(b"\xef\xbb\xbf")
        or b"\r" in value
        or not value.endswith(b"\n")
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    try:
        value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")


def _validated_iso_date(value: object) -> str:
    if type(value) is not str:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    try:
        parsed = _date.fromisoformat(value)
    except ValueError:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    if parsed.isoformat() != value:
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    return value


def _validated_as_of(value: object) -> str:
    return _validated_iso_date(value)


def _source_bound_evaluation_timestamp_utc(
    manifest: dict[str, object],
    *,
    evidence: dict[str, object],
    render_binding: dict[str, object],
    generation_id: str,
) -> str:
    """Derive package time solely from the authenticated R2F source lineage."""
    as_of = _validated_as_of(manifest.get("as_of"))
    source_timestamp = f"{as_of}T00:00:00+00:00"
    strategy_summary = evidence.get("strategy_settings_summary")
    registry = evidence.get("active_anchor_registry")
    if (
        manifest.get("generated_at") != source_timestamp
        or evidence.get("generated_at") != source_timestamp
        or type(strategy_summary) is not dict
        or strategy_summary.get("as_of") != as_of
        or type(registry) is not dict
        or registry.get("generated_at") != source_timestamp
        or render_binding.get("generation_id") != generation_id
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    # R2F freezes +00:00; WS01a2 freezes the equivalent canonical Z spelling.
    return f"{as_of}T00:00:00Z"


def _bounded_nonempty_text(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 2_048
        and value == value.strip()
        and not any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    )


def _validate_authority(payload: dict[str, object]) -> None:
    if any(payload.get(key) != value for key, value in _AUTHORITY_MARKERS.items()):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")


def _generation_entry_names(generation_fd: int) -> frozenset[str]:
    try:
        names = _list_directory_entries(generation_fd)
    except OSError:
        _raise("WS01_BR_SOURCE_ARTIFACT_SET_MISMATCH")
    if any(type(name) is not str for name in names) or len(names) != len(set(names)):
        _raise("WS01_BR_SOURCE_ARTIFACT_SET_MISMATCH")
    return frozenset(names)


def _read_stable_regular_file_at(
    directory_fd: int,
    filename: str,
    *,
    maximum_bytes: int,
    too_large_code: str,
    owner: _DescriptorOwner,
) -> bytes:
    return _read_stable_regular_file_snapshot_at(
        directory_fd,
        filename,
        maximum_bytes=maximum_bytes,
        too_large_code=too_large_code,
        owner=owner,
    ).raw_bytes


def _read_stable_regular_file_snapshot_at(
    directory_fd: int,
    filename: str,
    *,
    maximum_bytes: int,
    too_large_code: str,
    owner: _DescriptorOwner,
) -> _StableFileSnapshot:
    try:
        entry_before = _os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        if not _stat.S_ISREG(entry_before.st_mode):
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
        if entry_before.st_size > maximum_bytes:
            _raise(too_large_code)
        expected = _regular_file_state(entry_before)
        descriptor = owner.register(
            _os.open(
                filename,
                _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC | _os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        )
        opened = _os.fstat(descriptor)
        if not _stat.S_ISREG(opened.st_mode) or _regular_file_state(opened) != expected:
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
        first = _read_complete_descriptor(
            descriptor,
            expected_size=expected.size,
            maximum_bytes=maximum_bytes,
        )
        descriptor_after_first = _os.fstat(descriptor)
        entry_after_first = _os.stat(
            filename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not _stat.S_ISREG(entry_after_first.st_mode)
            or _regular_file_state(descriptor_after_first) != expected
            or _regular_file_state(entry_after_first) != expected
        ):
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
        if _os.lseek(descriptor, 0, _os.SEEK_SET) != 0:
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
        second = _read_complete_descriptor(
            descriptor,
            expected_size=expected.size,
            maximum_bytes=maximum_bytes,
        )
        descriptor_after_second = _os.fstat(descriptor)
        entry_after_second = _os.stat(
            filename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not _stat.S_ISREG(entry_after_second.st_mode)
            or _regular_file_state(descriptor_after_second) != expected
            or _regular_file_state(entry_after_second) != expected
            or first != second
        ):
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
        return _StableFileSnapshot(raw_bytes=first, state=expected)
    except _SourceAdapterFailure:
        raise
    except OSError:
        _raise("WS01_BR_SOURCE_READ_UNSTABLE")


def _read_complete_descriptor(
    descriptor: int,
    *,
    expected_size: int,
    maximum_bytes: int,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = maximum_bytes + 1 - total
        if remaining <= 0:
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
        chunk = _os.read(descriptor, min(65_536, remaining))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum_bytes:
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
        chunks.append(chunk)
    if total != expected_size:
        _raise("WS01_BR_SOURCE_READ_UNSTABLE")
    return b"".join(chunks)


def _open_absolute_directory_chain(
    root: _Path, *, owner: _DescriptorOwner
) -> tuple[int, list[tuple[int, str, int]]]:
    parts = root.parts
    if not parts or parts[0] != "/":
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    base = owner.register(
        _os.open("/", _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC)
    )
    chain: list[tuple[int, str, int]] = []
    parent = base
    for component in parts[1:]:
        child = _open_directory_at(parent, component, owner=owner)
        chain.append((parent, component, child))
        parent = child
    if not chain:
        chain.append((-1, "", base))
    return parent, chain


def _open_directory_at(
    parent_fd: int, name: str, *, owner: _DescriptorOwner
) -> int:
    try:
        entry = _os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _stat.S_ISDIR(entry.st_mode):
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
        descriptor = owner.register(
            _os.open(
                name,
                _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        )
        opened = _os.fstat(descriptor)
        if (
            not _stat.S_ISDIR(opened.st_mode)
            or _directory_identity(entry) != _directory_identity(opened)
        ):
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
        return descriptor
    except _SourceAdapterFailure:
        raise
    except OSError:
        _raise("WS01_BR_SOURCE_READ_UNSTABLE")


def _verify_directory_chain(chain: list[tuple[int, str, int]]) -> None:
    for parent_fd, name, child_fd in chain:
        if parent_fd < 0:
            continue
        try:
            entry = _os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            opened = _os.fstat(child_fd)
        except OSError:
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")
        if (
            not _stat.S_ISDIR(entry.st_mode)
            or not _stat.S_ISDIR(opened.st_mode)
            or _directory_identity(entry) != _directory_identity(opened)
        ):
            _raise("WS01_BR_SOURCE_READ_UNSTABLE")


def _require_descriptor_primitives() -> None:
    if any(
        not hasattr(_os, flag)
        for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")
    ):
        _raise("WS01_BR_SOURCE_READ_UNSTABLE")
    if any(function not in _os.supports_dir_fd for function in (_os.open, _os.stat)):
        _raise("WS01_BR_SOURCE_READ_UNSTABLE")
    if _os.listdir not in _os.supports_fd:
        _raise("WS01_BR_SOURCE_READ_UNSTABLE")


def _regular_file_state(value: _os.stat_result) -> _RegularFileState:
    return _RegularFileState(
        device=value.st_dev,
        inode=value.st_ino,
        size=value.st_size,
        mode=value.st_mode,
        mtime_ns=value.st_mtime_ns,
        ctime_ns=value.st_ctime_ns,
        link_count=value.st_nlink,
    )


def _directory_identity(value: _os.stat_result) -> tuple[int, int]:
    return (value.st_dev, value.st_ino)


def _directory_inventory_state(value: _os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_mtime_ns, value.st_ctime_ns)


def _parse_json_object(value: bytes, code: str) -> dict[str, object]:
    try:
        parsed = _json.loads(
            value.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (
        _DuplicateJsonKey,
        UnicodeDecodeError,
        _json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        _raise(code)
    if type(parsed) is not dict:
        _raise(code)
    return parsed


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ValueError


def _r2f_json_file_bytes(value: dict[str, object]) -> bytes:
    return (
        _json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_r2f_json_bytes(value: object) -> bytes:
    return _json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_ws01_json_bytes(value: object) -> bytes:
    _validate_ws01_json_value(value, depth=1)
    return _json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _validate_ws01_json_value(value: object, *, depth: int) -> None:
    if depth > _RESOURCE_BOUND_PROFILE["max_nesting_depth"]:
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    if type(value) is dict:
        if len(value) > _RESOURCE_BOUND_PROFILE["max_object_members"]:
            _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
        for key, item in value.items():
            if type(key) is not str or not key.isascii():
                _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
            _validate_ws01_json_value(item, depth=depth + 1)
    elif type(value) is list:
        if len(value) > _RESOURCE_BOUND_PROFILE["max_array_items"]:
            _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
        for item in value:
            _validate_ws01_json_value(item, depth=depth + 1)
    elif type(value) is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    elif type(value) not in (int, bool, type(None)):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")


def _validate_source_json_resource_tree(value: object, *, depth: int) -> None:
    if depth > _RESOURCE_BOUND_PROFILE["max_nesting_depth"]:
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    if type(value) is dict:
        if len(value) > _RESOURCE_BOUND_PROFILE["max_object_members"]:
            _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
        for key, item in value.items():
            if type(key) is not str:
                _raise("WS01_BR_SOURCE_GENERATION_INVALID")
            _validate_source_json_resource_tree(item, depth=depth + 1)
    elif type(value) is list:
        if len(value) > _RESOURCE_BOUND_PROFILE["max_array_items"]:
            _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
        for item in value:
            _validate_source_json_resource_tree(item, depth=depth + 1)
    elif type(value) is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    elif type(value) is float:
        if not _math.isfinite(value):
            _raise("WS01_BR_SOURCE_GENERATION_INVALID")
    elif type(value) not in (int, bool, type(None)):
        _raise("WS01_BR_SOURCE_GENERATION_INVALID")


def _canonical_r2f_sha256(value: object) -> str:
    return _sha256(_canonical_r2f_json_bytes(value))


def _domain_identity(domain_name: str, payload: dict[str, object]) -> str:
    try:
        domain = _EXPECTED_DOMAIN_SEPARATORS[domain_name]
    except KeyError:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _sha256(domain + _canonical_ws01_json_bytes(payload))


def _sha256(value: bytes) -> str:
    return _hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return type(value) is str and _GENERATION_ID.fullmatch(value) is not None


def _deep_freeze(value: object) -> object:
    if type(value) is dict:
        return _MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: object) -> object:
    if isinstance(value, _MappingProxyType):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_deep_thaw(item) for item in value]
    return value


def _raise(code: str) -> None:
    raise _SourceAdapterFailure(code)


__all__ = (
    "verify_r2f_v2_generation",
    "build_source_snapshot",
)
