"""Frozen WEEKLY-SHADOW-01 static contract and identity foundation (WS01a).

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
        "weekly_shadow_01_analyst_input_v1": "schemas/weekly_shadow_01_analyst_input.schema.json",
        "weekly_shadow_01_analyst_response_v1": "schemas/weekly_shadow_01_analyst_response.schema.json",
        "weekly_shadow_01_response_capture_v1": "schemas/weekly_shadow_01_response_capture.schema.json",
        "weekly_shadow_01_response_validation_v1": "schemas/weekly_shadow_01_response_validation.schema.json",
        "weekly_shadow_01_analyst_report_v1": "schemas/weekly_shadow_01_analyst_report.schema.json",
        "weekly_shadow_01_run_summary_v1": "schemas/weekly_shadow_01_run_summary.schema.json",
    }
)

# These constants are frozen against the exact repository schema bytes after
# strict decoding. They are literals so importing this module performs no I/O.
SCHEMA_IDENTITY_SHA256_BY_VERSION: Final = _MappingProxyType(
    {
        "weekly_shadow_01_analyst_input_v1": "809c61a4569e3bd408ad32bd509377768ede4b1325146fee0b9d8a1cb1d51af5",
        "weekly_shadow_01_analyst_response_v1": "2fad9bf5f216fb000c3c17e742a839cff3878f7acbbcacbdc0bdef6cd15426d7",
        "weekly_shadow_01_response_capture_v1": "529127017ce3fb541d2ca41959b252d1b9992d2fadaf94b1e6c056ee9d927bab",
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
) -> "Mapping[str, object]":
    return {
        "contract_version": f"{schema_key}_contract_v1",
        "contract_id": f"{schema_key}_semantic_contract",
        "schema_identity_sha256": SCHEMA_IDENTITY_SHA256_BY_VERSION[schema_key],
        "owner": owner,
        "ordered_relevant_blocking_reason_codes": list(relevant_blocking_reason_codes),
        "ordered_relevant_analyst_limitation_codes": list(relevant_analyst_limitation_codes),
        "required_profile_identities_sha256": list(required_profile_identities_sha256),
        "authority_effect": "none",
    }


_SEMANTIC_CONTRACT_RECORDS: Final = _MappingProxyType(
    {
        "weekly_shadow_01_analyst_input_v1": _semantic_contract_record(
            "weekly_shadow_01_analyst_input_v1",
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
        ),
        "weekly_shadow_01_analyst_response_v1": _semantic_contract_record(
            "weekly_shadow_01_analyst_response_v1",
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
        ),
        "weekly_shadow_01_response_capture_v1": _semantic_contract_record(
            "weekly_shadow_01_response_capture_v1",
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


# --- overall WS01a contract-catalog identity ---------------------------------

_CONTRACT_CATALOG_PAYLOAD: Final = {
    "catalog_version": "weekly_shadow_01_contract_catalog_v1",
    "domain_separators_hex": {name: value.hex() for name, value in DOMAIN_SEPARATORS.items()},
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
