"""Closed, report-only H1-to-Legacy-Step-1 role mapping.

This offline adapter classifies the complete legacy strict-handoff role
inventory against source-bound H1 artifacts.  It is not a Legacy handoff
compiler, a production reader, an availability signal, or an authorization
surface.  In particular, H1 qualitative text never supplies mapping values.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Final, NoReturn

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MAX_MMI_H1_LEGACY_STEP1_MAPPING_REPORT_V1_CANONICAL_BYTES,
    MmiCanonicalizationError,
    _MMI_H1_LEGACY_STEP1_MAPPING_REPORT_V1_IDENTITY_DOMAIN,
    canonical_json_bytes,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import AUTHORITY_EFFECT_NONE
from investment_orchestrator.mmi.legacy_step1_compatibility_candidate_v1 import (
    MmiLegacyStep1CompatibilityCandidateV1Error,
    validate_mmi_legacy_step1_compatibility_candidate_v1,
)
from investment_orchestrator.validators.validate_research_handoff import (
    BASE_ROLE_KEYS,
    LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION,
)


__all__ = (
    "MmiH1LegacyStep1MappingReportV1Error",
    "build_mmi_h1_legacy_step1_mapping_report_v1",
    "validate_mmi_h1_legacy_step1_mapping_report_v1",
)

_SCHEMA_VERSION: Final = "mmi_h1_legacy_step1_mapping_report_v1"
_ARTIFACT_KIND: Final = "MMI_H1_LEGACY_STEP1_MAPPING_REPORT"
_ROLE_MAP_VERSION: Final = "h1_legacy_step1_role_map_v1"
_SCHEMA_NAME: Final = "mmi_h1_legacy_step1_mapping_report_v1.schema.json"
_IDENTITY_FIELD: Final = "mapping_report_identity_sha256"
_ZERO_SHA256: Final = "0" * 64
_IDENTIFIER_RE: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$", re.ASCII)
_ASCII_TRIM: Final = " \t\r\n"

_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "role_map_version",
        "target_legacy_validator_contract_version",
        "report_only",
        "authority_effect",
        "not_authorization",
        "full_legacy_compatibility",
        "upstream_identity_chain",
        "deterministic_mappings",
        "ordered_role_results",
        _IDENTITY_FIELD,
    }
)

_EXACT_DETERMINISTIC_EQUIVALENT: Final = (
    "EXACT_DETERMINISTIC_EQUIVALENT"
)
_EXACT_CODE_SUPPLIED_EQUIVALENT: Final = (
    "EXACT_CODE_SUPPLIED_EQUIVALENT"
)
_QUALITATIVE_NON_AUTHORITY_ONLY: Final = "QUALITATIVE_NON_AUTHORITY_ONLY"
_EXPLICITLY_UNAVAILABLE: Final = "EXPLICITLY_UNAVAILABLE"
_NOT_MAPPABLE: Final = "NOT_MAPPABLE"
_SEPARATE_CONTRACT_CHANGE: Final = (
    "LEGACY_ROLE_PROPOSED_FOR_REMOVAL_REQUIRES_SEPARATE_CONTRACT_CHANGE"
)

# This ordered inventory is the closed role-map contract.  It contains every
# strict Legacy handoff role plus the H1 evidence-only categories whose mapping
# status must be explicit to prevent a future consumer from inferring them.
_ROLE_STATUS_PAIRS: Final = (
    ("TRADE_UNIVERSE_ALLOWED_BUY_TICKERS", _EXACT_CODE_SUPPLIED_EQUIVALENT),
    ("BUY_UNIVERSE_SCORECARD_TICKER", _EXACT_CODE_SUPPLIED_EQUIVALENT),
    (
        "BUY_UNIVERSE_SCORECARD_ROLE_LAYER",
        _EXACT_CODE_SUPPLIED_EQUIVALENT,
    ),
    (
        "BUY_UNIVERSE_SCORECARD_EXECUTION_PRIORITY_THIS_RUN",
        _NOT_MAPPABLE,
    ),
    ("BUY_UNIVERSE_SCORECARD_ACTIONABILITY_STATUS", _NOT_MAPPABLE),
    (
        "BUY_UNIVERSE_SCORECARD_ENTRY_DRIVER",
        _QUALITATIVE_NON_AUTHORITY_ONLY,
    ),
    ("BUY_UNIVERSE_SCORECARD_PRIMARY_ANCHOR_TYPE", _EXPLICITLY_UNAVAILABLE),
    (
        "BUY_UNIVERSE_SCORECARD_PRIMARY_ANCHOR_EVENT_ID",
        _EXPLICITLY_UNAVAILABLE,
    ),
    (
        "BUY_UNIVERSE_SCORECARD_PRIMARY_ANCHOR_DATE_ET",
        _EXPLICITLY_UNAVAILABLE,
    ),
    (
        "BUY_UNIVERSE_SCORECARD_PREFERRED_SCHEDULED_THEME_EVENT_ID",
        _EXPLICITLY_UNAVAILABLE,
    ),
    (
        "BUY_UNIVERSE_SCORECARD_THESIS_12M_PLUS_SUPPORTED",
        _QUALITATIVE_NON_AUTHORITY_ONLY,
    ),
    (
        "BUY_UNIVERSE_SCORECARD_THESIS_12M_PLUS_SUMMARY",
        _QUALITATIVE_NON_AUTHORITY_ONLY,
    ),
    ("BUY_UNIVERSE_SCORECARD_THESIS_LINKAGE_QUALITY", _NOT_MAPPABLE),
    ("BUY_UNIVERSE_SCORECARD_COMPILE_BLOCKER_IF_ANY", _NOT_MAPPABLE),
    ("BUY_UNIVERSE_SCORECARD_EVENT_ID_REFS", _EXPLICITLY_UNAVAILABLE),
    (
        "BUY_UNIVERSE_SCORECARD_STRUCTURAL_THEME_REFS",
        _NOT_MAPPABLE,
    ),
    ("SCHEDULED_EVENTS", _EXPLICITLY_UNAVAILABLE),
    ("STRUCTURAL_THEMES_6_18M", _QUALITATIVE_NON_AUTHORITY_ONLY),
    ("REGIME_INPUTS", _EXPLICITLY_UNAVAILABLE),
    ("POLICY_ITEMS", _SEPARATE_CONTRACT_CHANGE),
    ("TOP5_NEXT_WEEK", _NOT_MAPPABLE),
    (
        "USER_APPROVED_EXTENDED_ETF_STATIC_LIST",
        _EXACT_CODE_SUPPLIED_EQUIVALENT,
    ),
    ("PROPOSED_EXTENDED_ETF_CANDIDATES", _EXPLICITLY_UNAVAILABLE),
    ("EXTENDED_ETF_CANDIDATE_UNIVERSE", _EXPLICITLY_UNAVAILABLE),
    ("EXTENDED_ETF_PREDECISION_SCORECARD", _EXPLICITLY_UNAVAILABLE),
    ("APPROVED_STATIC_LIST_SCREENING_LOG", _EXPLICITLY_UNAVAILABLE),
    ("OPTIONAL_EXTENDED_ETF_SLEEVE", _EXPLICITLY_UNAVAILABLE),
    ("EXTENDED_ETF_SCORECARD", _EXPLICITLY_UNAVAILABLE),
    ("STRATEGY_A_HANDOFF_VERSION", _SEPARATE_CONTRACT_CHANGE),
    ("STRATEGY_A_HANDOFF_SCOPE", _SEPARATE_CONTRACT_CHANGE),
    ("STRATEGY_A_NOT_ORDER_INSTRUCTION", _EXACT_CODE_SUPPLIED_EQUIVALENT),
    ("STRATEGY_A_MUST_STILL_APPLY", _SEPARATE_CONTRACT_CHANGE),
    ("BASE_SHORTLIST_ELIGIBLE_BY_ROLE", _NOT_MAPPABLE),
    ("BASE_WATCH_ONLY_BY_ROLE", _NOT_MAPPABLE),
    ("POSITIVE_DELTA_RESEARCH_SUPPORTED", _NOT_MAPPABLE),
    ("POSITIVE_DELTA_NOT_IMPLIED_FOR", _NOT_MAPPABLE),
    ("REPLACEMENT_RANKING_BY_ROLE", _NOT_MAPPABLE),
    ("ROTATION_HANDOFF", _SEPARATE_CONTRACT_CHANGE),
    ("BUY_SIDE_NO_ACTION_HINTS", _QUALITATIVE_NON_AUTHORITY_ONLY),
    ("EXTENDED_LANE_DOWNSTREAM_GATE", _EXPLICITLY_UNAVAILABLE),
    ("SELL_SIDE_RESEARCH_BOUNDARY", _NOT_MAPPABLE),
    ("TARGET_WEIGHTS", _EXPLICITLY_UNAVAILABLE),
    ("PROVENANCE_IDENTITY_CHAIN", _EXACT_DETERMINISTIC_EQUIVALENT),
    (
        "BUDGETS_CAPS_EXTERNAL_DETERMINISTIC_OWNER",
        _EXACT_CODE_SUPPLIED_EQUIVALENT,
    ),
    ("H1_EVIDENCE_OBSERVATIONS", _QUALITATIVE_NON_AUTHORITY_ONLY),
    ("H1_RISKS", _QUALITATIVE_NON_AUTHORITY_ONLY),
    ("H1_UNCERTAINTIES", _QUALITATIVE_NON_AUTHORITY_ONLY),
    ("H1_CONTRADICTIONS", _QUALITATIVE_NON_AUTHORITY_ONLY),
    ("H1_RESEARCH_QUESTIONS", _QUALITATIVE_NON_AUTHORITY_ONLY),
    ("H1_SUMMARY", _QUALITATIVE_NON_AUTHORITY_ONLY),
    ("FRESHNESS_INPUTS", _EXACT_CODE_SUPPLIED_EQUIVALENT),
    ("QUANTITIES_PERMISSIONS_GATES_ORDERS", _EXPLICITLY_UNAVAILABLE),
)
_LEGACY_ROLE_INVENTORY: Final = tuple(
    role for role, _ in _ROLE_STATUS_PAIRS
)
_ROLE_STATUS_BY_ROLE: Final = dict(_ROLE_STATUS_PAIRS)

_H1_QUALITATIVE_ARRAY_FIELDS: Final = (
    "evidence_observations",
    "risks",
    "uncertainties",
    "contradictions",
    "research_questions",
)
_AUTHORITY_BEARING_PROSE_RE: Final = re.compile(
    r"\b(?:hold|no[_ -]?trade|sell|new[_ -]?buy|order[_ -]?compilation)\b"
    r"|\b(?:buy|sell)\s+(?:\$?\d|[A-Z][A-Z0-9.-]{0,15}\b)"
    r"|\b(?:target[_ ]?weights?|budgets?|caps?|quantities?|permissions?|"
    r"freshness|availability|actionability|gates?|publication|pointers?|"
    r"role assignment|universe inclusion|instrument membership)\s*"
    r"(?:=|:|\$?\d)",
    re.IGNORECASE,
)


class MmiH1LegacyStep1MappingReportV1Error(ValueError):
    """Raised when no complete report-only H1-to-Legacy mapping exists."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise MmiH1LegacyStep1MappingReportV1Error(code)


def _snapshot_value(
    value: object,
    *,
    active_container_ids: set[int],
) -> object:
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_container_ids:
            _fail("MMI_H1_LEGACY_MAPPING_INPUT_INVALID")
        active_container_ids.add(container_id)
        try:
            snapshot: dict[str, object] = {}
            try:
                keys = tuple(value.keys())
                if (
                    any(type(key) is not str for key in keys)
                    or len(keys) != len(set(keys))
                ):
                    _fail("MMI_H1_LEGACY_MAPPING_INPUT_INVALID")
                for key in keys:
                    snapshot[key] = _snapshot_value(
                        value[key], active_container_ids=active_container_ids
                    )
            except MmiH1LegacyStep1MappingReportV1Error:
                raise
            except Exception:
                _fail("MMI_H1_LEGACY_MAPPING_INPUT_INVALID")
            return snapshot
        finally:
            active_container_ids.remove(container_id)
    if type(value) is list:
        container_id = id(value)
        if container_id in active_container_ids:
            _fail("MMI_H1_LEGACY_MAPPING_INPUT_INVALID")
        active_container_ids.add(container_id)
        try:
            return [
                _snapshot_value(item, active_container_ids=active_container_ids)
                for item in value
            ]
        finally:
            active_container_ids.remove(container_id)
    _fail("MMI_H1_LEGACY_MAPPING_INPUT_INVALID")


def _snapshot_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail("MMI_H1_LEGACY_MAPPING_INPUT_INVALID")
    snapshot = _snapshot_value(value, active_container_ids=set())
    if type(snapshot) is not dict:
        _fail("MMI_H1_LEGACY_MAPPING_INPUT_INVALID")
    return snapshot


def _require_dict(value: object, *, code: str) -> dict[str, object]:
    if type(value) is not dict:
        _fail(code)
    return value


def _require_list(value: object, *, code: str) -> list[object]:
    if type(value) is not list:
        _fail(code)
    return value


def _require_sha256(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or not set(value) <= set("0123456789abcdef")
    ):
        _fail(code)
    return value


def _normalize_identifier(value: object) -> str:
    if type(value) is not str:
        _fail("MMI_H1_LEGACY_MAPPING_IDENTIFIER_NORMALIZATION_FAILED")
    normalized = value.strip(_ASCII_TRIM)
    if not normalized or not normalized.isascii():
        _fail("MMI_H1_LEGACY_MAPPING_IDENTIFIER_NORMALIZATION_FAILED")
    normalized = normalized.upper()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        _fail("MMI_H1_LEGACY_MAPPING_IDENTIFIER_NORMALIZATION_FAILED")
    return normalized


def _normalize_identifier_sequence(value: object) -> list[str]:
    raw_values = _require_list(
        value, code="MMI_H1_LEGACY_MAPPING_IDENTIFIER_NORMALIZATION_FAILED"
    )
    result: list[str] = []
    raw_by_canonical: dict[str, str] = {}
    for raw in raw_values:
        normalized = _normalize_identifier(raw)
        if normalized in raw_by_canonical:
            if raw == raw_by_canonical[normalized]:
                _fail("MMI_H1_LEGACY_MAPPING_DUPLICATE_IDENTIFIER")
            _fail("MMI_H1_LEGACY_MAPPING_IDENTIFIER_COLLISION")
        assert type(raw) is str
        raw_by_canonical[normalized] = raw
        result.append(normalized)
    return result


def _validated_h1_candidate(
    *,
    legacy_step1_compatibility_candidate: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: object,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: object,
    run_context: object,
) -> dict[str, object]:
    if portfolio_projection is None or portfolio_source is None:
        _fail("MMI_H1_LEGACY_MAPPING_UPSTREAM_ARTIFACT_INVALID")
    try:
        return validate_mmi_legacy_step1_compatibility_candidate_v1(
            value=legacy_step1_compatibility_candidate,
            validated_grounded_analysis_response=(
                validated_grounded_analysis_response
            ),
            raw_response_envelope=raw_response_envelope,
            evidence_bundle=evidence_bundle,
            policy_projection=policy_projection,
            policy_source=policy_source,
            portfolio_projection=portfolio_projection,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except MmiLegacyStep1CompatibilityCandidateV1Error:
        _fail("MMI_H1_LEGACY_MAPPING_UPSTREAM_ARTIFACT_INVALID")


def _upstream_identity_chain(
    *,
    candidate: dict[str, object],
    validated_grounded_analysis_response: dict[str, object],
    raw_response_envelope: dict[str, object],
    evidence_bundle: dict[str, object],
    policy_projection: dict[str, object],
    portfolio_projection: dict[str, object],
) -> dict[str, object]:
    upstream_invalid = "MMI_H1_LEGACY_MAPPING_UPSTREAM_ARTIFACT_INVALID"
    identity_mismatch = "MMI_H1_LEGACY_MAPPING_IDENTITY_MISMATCH"
    policy_id = _require_sha256(
        policy_projection.get("policy_projection_identity_sha256"),
        code=upstream_invalid,
    )
    strategy_id = _require_sha256(
        policy_projection.get("source_record_identity_sha256"),
        code=upstream_invalid,
    )
    universe = _require_dict(
        policy_projection.get("universe_projection"), code=upstream_invalid
    )
    universe_id = _require_sha256(
        policy_projection.get("universe_projection_identity_sha256"),
        code=upstream_invalid,
    )
    if universe.get("universe_projection_identity_sha256") != universe_id:
        _fail(identity_mismatch)
    if universe.get("source_record_identity_sha256") != strategy_id:
        _fail(identity_mismatch)

    portfolio_id = _require_sha256(
        portfolio_projection.get("portfolio_projection_identity_sha256"),
        code=upstream_invalid,
    )
    portfolio_source_id = _require_sha256(
        portfolio_projection.get("portfolio_source_record_identity_sha256"),
        code=upstream_invalid,
    )
    if (
        portfolio_projection.get("portfolio_source_status")
        != "SOURCE_PRESENT_CONTENT_BOUND"
        or portfolio_projection.get("policy_projection_identity_sha256")
        != policy_id
    ):
        _fail(identity_mismatch)

    evidence_id = _require_sha256(
        evidence_bundle.get("evidence_bundle_identity_sha256"), code=upstream_invalid
    )
    evidence_policy = _require_dict(
        evidence_bundle.get("policy_component"), code=upstream_invalid
    )
    evidence_portfolio = _require_dict(
        evidence_bundle.get("portfolio_component"), code=upstream_invalid
    )
    if (
        evidence_policy.get("presence_status")
        != "PRESENT_SOURCE_BOUND_VALIDATED"
        or evidence_policy.get("strategy_source_record_identity_sha256")
        != strategy_id
        or evidence_policy.get("universe_projection_identity_sha256")
        != universe_id
        or evidence_policy.get("policy_projection_identity_sha256") != policy_id
        or evidence_portfolio.get("presence_status")
        != "PRESENT_SOURCE_BOUND_VALIDATED"
        or evidence_portfolio.get("portfolio_projection_identity_sha256")
        != portfolio_id
        or evidence_portfolio.get("portfolio_source_record_identity_sha256")
        != portfolio_source_id
        or evidence_portfolio.get("policy_projection_identity_sha256")
        != policy_id
    ):
        _fail(identity_mismatch)

    candidate_id = _require_sha256(
        candidate.get("legacy_step1_compatibility_candidate_identity_sha256"),
        code=upstream_invalid,
    )
    candidate_provenance = _require_dict(
        candidate.get("provenance"), code=upstream_invalid
    )
    view_id = _require_sha256(
        candidate_provenance.get("analyst_visible_evidence_view_identity_sha256"),
        code=upstream_invalid,
    )
    response_id = _require_sha256(
        validated_grounded_analysis_response.get(
            "validated_grounded_analysis_response_identity_sha256"
        ),
        code=upstream_invalid,
    )
    if (
        candidate_provenance.get(
            "validated_grounded_analysis_response_identity_sha256"
        )
        != response_id
    ):
        _fail(identity_mismatch)
    envelope_id = _require_sha256(
        raw_response_envelope.get("raw_response_envelope_identity_sha256"),
        code=upstream_invalid,
    )
    prompt_id = _require_sha256(
        raw_response_envelope.get("grounded_prompt_artifact_identity_sha256"),
        code=upstream_invalid,
    )
    if (
        validated_grounded_analysis_response.get(
            "raw_response_envelope_identity_sha256"
        )
        != envelope_id
    ):
        _fail(identity_mismatch)
    response_payload = _require_dict(
        validated_grounded_analysis_response.get("response_payload"),
        code=upstream_invalid,
    )
    context_id = _require_sha256(
        response_payload.get("prompt_context_binding_sha256"), code=upstream_invalid
    )
    return {
        "strategy_settings_source_record_identity_sha256": strategy_id,
        "policy_projection_identity_sha256": policy_id,
        "universe_projection_identity_sha256": universe_id,
        "portfolio_source_record_identity_sha256": portfolio_source_id,
        "portfolio_projection_identity_sha256": portfolio_id,
        "evidence_bundle_identity_sha256": evidence_id,
        "analyst_visible_evidence_view_identity_sha256": view_id,
        "grounded_prompt_artifact_identity_sha256": prompt_id,
        "prompt_context_binding_sha256": context_id,
        "raw_response_envelope_identity_sha256": envelope_id,
        "validated_grounded_analysis_response_identity_sha256": response_id,
        "legacy_step1_compatibility_candidate_identity_sha256": candidate_id,
    }


def _deterministic_mappings(
    *,
    candidate: dict[str, object],
    policy_projection: dict[str, object],
) -> dict[str, object]:
    upstream_invalid = "MMI_H1_LEGACY_MAPPING_UPSTREAM_ARTIFACT_INVALID"
    mismatch = "MMI_H1_LEGACY_MAPPING_SEQUENCE_MEMBERSHIP_MISMATCH"
    universe = _require_dict(
        policy_projection.get("universe_projection"), code=upstream_invalid
    )
    core = _normalize_identifier_sequence(universe.get("core_universe"))
    satellite = _normalize_identifier_sequence(universe.get("satellite_universe"))
    approved_extended = _normalize_identifier_sequence(
        universe.get("approved_extended_universe")
    )
    benchmark = _normalize_identifier_sequence(
        universe.get("benchmark_reference_instruments")
    )
    analysis_scope = _normalize_identifier_sequence(
        universe.get("analysis_scope_instruments")
    )
    if (
        len(benchmark) != 1
        or benchmark[0] not in core
        or set(core) & set(satellite)
        or set(core) & set(approved_extended)
        or set(satellite) & set(approved_extended)
        or analysis_scope != [*core, *satellite, *approved_extended]
    ):
        _fail(mismatch)

    raw_roles = _require_dict(universe.get("role_by_ticker"), code=upstream_invalid)
    normalized_role_keys = _normalize_identifier_sequence(list(raw_roles))
    if set(normalized_role_keys) != set(analysis_scope):
        _fail(mismatch)
    expected_role_by_ticker = {
        **{ticker: "CORE" for ticker in core},
        **{ticker: "SATELLITE" for ticker in satellite},
        **{ticker: "APPROVED_EXTENDED" for ticker in approved_extended},
    }
    actual_role_by_ticker: dict[str, object] = {}
    for raw_ticker, normalized_ticker in zip(
        raw_roles, normalized_role_keys, strict=True
    ):
        actual_role_by_ticker[normalized_ticker] = raw_roles[raw_ticker]
    if actual_role_by_ticker != expected_role_by_ticker:
        _fail(mismatch)

    assessments = _require_list(
        candidate.get("ordered_instrument_assessments"), code=upstream_invalid
    )
    assessment_tickers: list[object] = []
    assessment_roles: list[object] = []
    for assessment in assessments:
        row = _require_dict(assessment, code=upstream_invalid)
        assessment_tickers.append(row.get("ticker"))
        assessment_roles.append(row.get("policy_role"))
    if (
        _normalize_identifier_sequence(assessment_tickers) != analysis_scope
        or assessment_roles
        != [expected_role_by_ticker[ticker] for ticker in analysis_scope]
    ):
        _fail(mismatch)

    benchmark_ticker = benchmark[0]
    return {
        "base_trade_universe_ordered": [*core, *satellite],
        "legacy_role_membership": [
            {"legacy_role": BASE_ROLE_KEYS[0], "tickers": [benchmark_ticker]},
            {
                "legacy_role": BASE_ROLE_KEYS[1],
                "tickers": [
                    ticker for ticker in core if ticker != benchmark_ticker
                ],
            },
            {"legacy_role": BASE_ROLE_KEYS[2], "tickers": satellite},
        ],
        "approved_extended_static_membership_ordered": approved_extended,
    }


def _qualitative_h1_strings(candidate: dict[str, object]) -> list[str]:
    upstream_invalid = "MMI_H1_LEGACY_MAPPING_UPSTREAM_ARTIFACT_INVALID"
    result: list[str] = []
    assessments = _require_list(
        candidate.get("ordered_instrument_assessments"), code=upstream_invalid
    )
    for assessment in assessments:
        rationale = _require_dict(assessment, code=upstream_invalid).get(
            "rationale_12m_plus"
        )
        if rationale is not None:
            if type(rationale) is not str:
                _fail(upstream_invalid)
            result.append(rationale)
    for field in _H1_QUALITATIVE_ARRAY_FIELDS:
        items = _require_list(candidate.get(field), code=upstream_invalid)
        for item in items:
            text = _require_dict(item, code=upstream_invalid).get("text")
            if type(text) is not str:
                _fail(upstream_invalid)
            result.append(text)
    summary = _require_dict(candidate.get("summary"), code=upstream_invalid)
    summary_text = summary.get("text")
    if type(summary_text) is not str:
        _fail(upstream_invalid)
    result.append(summary_text)
    return result


def _reject_authority_bearing_qualitative_content(
    candidate: dict[str, object],
) -> None:
    if any(
        _AUTHORITY_BEARING_PROSE_RE.search(value) is not None
        for value in _qualitative_h1_strings(candidate)
    ):
        _fail("MMI_H1_LEGACY_MAPPING_AUTHORITY_BEARING_QUALITATIVE_INPUT")


def _ordered_role_results() -> list[object]:
    return [
        {"legacy_role": role, "mapping_status": status}
        for role, status in _ROLE_STATUS_PAIRS
    ]


def _validate_role_results(value: object) -> None:
    results = _require_list(value, code="MMI_H1_LEGACY_MAPPING_INVALID_ROLE_MAP")
    observed_roles: list[str] = []
    for result in results:
        row = _require_dict(result, code="MMI_H1_LEGACY_MAPPING_INVALID_ROLE_MAP")
        role = row.get("legacy_role")
        status = row.get("mapping_status")
        if type(role) is not str or type(status) is not str:
            _fail("MMI_H1_LEGACY_MAPPING_INVALID_ROLE_MAP")
        observed_roles.append(role)
        if _ROLE_STATUS_BY_ROLE.get(role) != status:
            _fail("MMI_H1_LEGACY_MAPPING_INVALID_ROLE_MAP")
    if tuple(observed_roles) != _LEGACY_ROLE_INVENTORY:
        _fail("MMI_H1_LEGACY_MAPPING_INVALID_ROLE_MAP")


def _validate_report_contract(report: dict[str, object]) -> None:
    if set(report) != _TOP_LEVEL_FIELDS:
        _fail("MMI_H1_LEGACY_MAPPING_SCHEMA_INVALID")
    if report.get("target_legacy_validator_contract_version") != (
        LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION
    ):
        _fail("MMI_H1_LEGACY_MAPPING_TARGET_VALIDATOR_VERSION_MISMATCH")
    if (
        report.get("schema_version") != _SCHEMA_VERSION
        or report.get("artifact_kind") != _ARTIFACT_KIND
        or report.get("role_map_version") != _ROLE_MAP_VERSION
        or report.get("report_only") is not True
        or report.get("authority_effect") != AUTHORITY_EFFECT_NONE
        or report.get("not_authorization") is not True
        or report.get("full_legacy_compatibility") is not False
    ):
        _fail("MMI_H1_LEGACY_MAPPING_SCHEMA_INVALID")
    _validate_role_results(report.get("ordered_role_results"))
    try:
        validate_artifact_schema(report, schema_name=_SCHEMA_NAME)
    except Exception:
        _fail("MMI_H1_LEGACY_MAPPING_SCHEMA_INVALID")


def _validate_report_canonical_size(report: dict[str, object]) -> None:
    try:
        canonical_json_bytes(
            report,
            maximum_bytes=(
                MAX_MMI_H1_LEGACY_STEP1_MAPPING_REPORT_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail("MMI_H1_LEGACY_MAPPING_RESOURCE_LIMIT_EXCEEDED")


def _report_identity(report: dict[str, object]) -> str:
    if set(report) != _TOP_LEVEL_FIELDS:
        _fail("MMI_H1_LEGACY_MAPPING_SCHEMA_INVALID")
    try:
        return record_identity_sha256(
            report,
            identity_field=_IDENTITY_FIELD,
            domain=_MMI_H1_LEGACY_STEP1_MAPPING_REPORT_V1_IDENTITY_DOMAIN,
            maximum_bytes=(
                MAX_MMI_H1_LEGACY_STEP1_MAPPING_REPORT_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail("MMI_H1_LEGACY_MAPPING_IDENTITY_MISMATCH")


def _build_from_validated_h1(
    *,
    candidate: dict[str, object],
    validated_grounded_analysis_response: dict[str, object],
    raw_response_envelope: dict[str, object],
    evidence_bundle: dict[str, object],
    policy_projection: dict[str, object],
    portfolio_projection: dict[str, object],
) -> dict[str, object]:
    _reject_authority_bearing_qualitative_content(candidate)
    report: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_kind": _ARTIFACT_KIND,
        "role_map_version": _ROLE_MAP_VERSION,
        "target_legacy_validator_contract_version": (
            LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION
        ),
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "not_authorization": True,
        "full_legacy_compatibility": False,
        "upstream_identity_chain": _upstream_identity_chain(
            candidate=candidate,
            validated_grounded_analysis_response=(
                validated_grounded_analysis_response
            ),
            raw_response_envelope=raw_response_envelope,
            evidence_bundle=evidence_bundle,
            policy_projection=policy_projection,
            portfolio_projection=portfolio_projection,
        ),
        "deterministic_mappings": _deterministic_mappings(
            candidate=candidate,
            policy_projection=policy_projection,
        ),
        "ordered_role_results": _ordered_role_results(),
        _IDENTITY_FIELD: _ZERO_SHA256,
    }
    _validate_report_contract(report)
    _validate_report_canonical_size(report)
    report[_IDENTITY_FIELD] = _report_identity(report)
    _validate_report_contract(report)
    return report


def _validated_source_bound_h1_inputs(
    *,
    legacy_step1_compatibility_candidate: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: object,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: object,
    run_context: object,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    candidate = _validated_h1_candidate(
        legacy_step1_compatibility_candidate=_snapshot_mapping(
            legacy_step1_compatibility_candidate
        ),
        validated_grounded_analysis_response=_snapshot_mapping(
            validated_grounded_analysis_response
        ),
        raw_response_envelope=_snapshot_mapping(raw_response_envelope),
        evidence_bundle=_snapshot_mapping(evidence_bundle),
        policy_projection=_snapshot_mapping(policy_projection),
        policy_source=policy_source,
        portfolio_projection=(
            None
            if portfolio_projection is None
            else _snapshot_mapping(portfolio_projection)
        ),
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    if portfolio_projection is None:
        _fail("MMI_H1_LEGACY_MAPPING_UPSTREAM_ARTIFACT_INVALID")
    return (
        candidate,
        _snapshot_mapping(validated_grounded_analysis_response),
        _snapshot_mapping(raw_response_envelope),
        _snapshot_mapping(evidence_bundle),
        _snapshot_mapping(policy_projection),
        _snapshot_mapping(portfolio_projection),
    )


def build_mmi_h1_legacy_step1_mapping_report_v1(
    *,
    legacy_step1_compatibility_candidate: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: object,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: object,
    run_context: object,
) -> dict[str, object]:
    """Build one complete, source-bound, non-authorizing mapping report."""
    (
        candidate,
        response,
        envelope,
        evidence,
        policy,
        portfolio,
    ) = _validated_source_bound_h1_inputs(
        legacy_step1_compatibility_candidate=(
            legacy_step1_compatibility_candidate
        ),
        validated_grounded_analysis_response=(
            validated_grounded_analysis_response
        ),
        raw_response_envelope=raw_response_envelope,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    return _build_from_validated_h1(
        candidate=candidate,
        validated_grounded_analysis_response=response,
        raw_response_envelope=envelope,
        evidence_bundle=evidence,
        policy_projection=policy,
        portfolio_projection=portfolio,
    )


def validate_mmi_h1_legacy_step1_mapping_report_v1(
    *,
    value: Mapping[str, object],
    legacy_step1_compatibility_candidate: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: object,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: object,
    run_context: object,
) -> dict[str, object]:
    """Revalidate a report as the exact deterministic source-bound result."""
    report = _snapshot_mapping(value)
    _validate_report_contract(report)
    _validate_report_canonical_size(report)
    if report.get(_IDENTITY_FIELD) != _report_identity(report):
        _fail("MMI_H1_LEGACY_MAPPING_IDENTITY_MISMATCH")
    (
        candidate,
        response,
        envelope,
        evidence,
        policy,
        portfolio,
    ) = _validated_source_bound_h1_inputs(
        legacy_step1_compatibility_candidate=(
            legacy_step1_compatibility_candidate
        ),
        validated_grounded_analysis_response=(
            validated_grounded_analysis_response
        ),
        raw_response_envelope=raw_response_envelope,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    expected_identity_chain = _upstream_identity_chain(
        candidate=candidate,
        validated_grounded_analysis_response=response,
        raw_response_envelope=envelope,
        evidence_bundle=evidence,
        policy_projection=policy,
        portfolio_projection=portfolio,
    )
    if report.get("upstream_identity_chain") != expected_identity_chain:
        _fail("MMI_H1_LEGACY_MAPPING_IDENTITY_MISMATCH")
    expected_mappings = _deterministic_mappings(
        candidate=candidate,
        policy_projection=policy,
    )
    if report.get("deterministic_mappings") != expected_mappings:
        _fail("MMI_H1_LEGACY_MAPPING_SEQUENCE_MEMBERSHIP_MISMATCH")
    expected = _build_from_validated_h1(
        candidate=candidate,
        validated_grounded_analysis_response=response,
        raw_response_envelope=envelope,
        evidence_bundle=evidence,
        policy_projection=policy,
        portfolio_projection=portfolio,
    )
    if report != expected:
        _fail("MMI_H1_LEGACY_MAPPING_NON_EXPECTED")
    return report
