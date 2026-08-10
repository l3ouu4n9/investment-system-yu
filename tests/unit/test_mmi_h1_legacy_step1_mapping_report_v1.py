from __future__ import annotations

import ast
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.canonical import (
    MAX_MMI_H1_LEGACY_STEP1_MAPPING_REPORT_V1_CANONICAL_BYTES,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    MmiProjectionRunContext,
    _begin_mmi_projection_run_with_clock,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
)
from investment_orchestrator.mmi.grounded_prompt_v2 import (
    build_mmi_grounded_prompt_v2,
)
from investment_orchestrator.mmi.legacy_step1_compatibility_candidate_v1 import (
    build_mmi_legacy_step1_compatibility_candidate_v1,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.mmi.raw_response_envelope_v2 import (
    build_mmi_raw_response_envelope_v2,
)
from investment_orchestrator.mmi.validated_grounded_analysis_response_v2 import (
    build_mmi_validated_grounded_analysis_response_v2,
)
from investment_orchestrator.offline import (
    mmi_h1_legacy_step1_mapping_report_v1 as owner,
)
from investment_orchestrator.offline.mmi_h1_legacy_step1_mapping_report_v1 import (
    MmiH1LegacyStep1MappingReportV1Error,
    build_mmi_h1_legacy_step1_mapping_report_v1,
    validate_mmi_h1_legacy_step1_mapping_report_v1,
)
from investment_orchestrator.validators.validate_research_handoff import (
    LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION,
)

import _mmi_hermetic_source_checkout as hermetic


EVALUATION_TIME = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
SCHEMA_PATH = (
    repo_root() / "schemas/mmi_h1_legacy_step1_mapping_report_v1.schema.json"
)
IDENTITY_FIELD = "mapping_report_identity_sha256"
IDENTITY_DOMAIN = b"mmi_h1_legacy_step1_mapping_report_v1\0"

# Closed-design regression oracle.  This intentionally does not import the
# production role table: production output must equal this independently owned
# public contract in its exact serialized order.
APPROVED_ROLE_STATUS_PAIRS = (
    ("TRADE_UNIVERSE_ALLOWED_BUY_TICKERS", "EXACT_CODE_SUPPLIED_EQUIVALENT"),
    ("BUY_UNIVERSE_SCORECARD_TICKER", "EXACT_CODE_SUPPLIED_EQUIVALENT"),
    ("BUY_UNIVERSE_SCORECARD_ROLE_LAYER", "EXACT_CODE_SUPPLIED_EQUIVALENT"),
    ("BUY_UNIVERSE_SCORECARD_EXECUTION_PRIORITY_THIS_RUN", "NOT_MAPPABLE"),
    ("BUY_UNIVERSE_SCORECARD_ACTIONABILITY_STATUS", "NOT_MAPPABLE"),
    ("BUY_UNIVERSE_SCORECARD_ENTRY_DRIVER", "QUALITATIVE_NON_AUTHORITY_ONLY"),
    ("BUY_UNIVERSE_SCORECARD_PRIMARY_ANCHOR_TYPE", "EXPLICITLY_UNAVAILABLE"),
    ("BUY_UNIVERSE_SCORECARD_PRIMARY_ANCHOR_EVENT_ID", "EXPLICITLY_UNAVAILABLE"),
    ("BUY_UNIVERSE_SCORECARD_PRIMARY_ANCHOR_DATE_ET", "EXPLICITLY_UNAVAILABLE"),
    (
        "BUY_UNIVERSE_SCORECARD_PREFERRED_SCHEDULED_THEME_EVENT_ID",
        "EXPLICITLY_UNAVAILABLE",
    ),
    (
        "BUY_UNIVERSE_SCORECARD_THESIS_12M_PLUS_SUPPORTED",
        "QUALITATIVE_NON_AUTHORITY_ONLY",
    ),
    (
        "BUY_UNIVERSE_SCORECARD_THESIS_12M_PLUS_SUMMARY",
        "QUALITATIVE_NON_AUTHORITY_ONLY",
    ),
    ("BUY_UNIVERSE_SCORECARD_THESIS_LINKAGE_QUALITY", "NOT_MAPPABLE"),
    ("BUY_UNIVERSE_SCORECARD_COMPILE_BLOCKER_IF_ANY", "NOT_MAPPABLE"),
    ("BUY_UNIVERSE_SCORECARD_EVENT_ID_REFS", "EXPLICITLY_UNAVAILABLE"),
    ("BUY_UNIVERSE_SCORECARD_STRUCTURAL_THEME_REFS", "NOT_MAPPABLE"),
    ("SCHEDULED_EVENTS", "EXPLICITLY_UNAVAILABLE"),
    ("STRUCTURAL_THEMES_6_18M", "QUALITATIVE_NON_AUTHORITY_ONLY"),
    ("REGIME_INPUTS", "EXPLICITLY_UNAVAILABLE"),
    (
        "POLICY_ITEMS",
        "LEGACY_ROLE_PROPOSED_FOR_REMOVAL_REQUIRES_SEPARATE_CONTRACT_CHANGE",
    ),
    ("TOP5_NEXT_WEEK", "NOT_MAPPABLE"),
    (
        "USER_APPROVED_EXTENDED_ETF_STATIC_LIST",
        "EXACT_CODE_SUPPLIED_EQUIVALENT",
    ),
    ("PROPOSED_EXTENDED_ETF_CANDIDATES", "EXPLICITLY_UNAVAILABLE"),
    ("EXTENDED_ETF_CANDIDATE_UNIVERSE", "EXPLICITLY_UNAVAILABLE"),
    ("EXTENDED_ETF_PREDECISION_SCORECARD", "EXPLICITLY_UNAVAILABLE"),
    ("APPROVED_STATIC_LIST_SCREENING_LOG", "EXPLICITLY_UNAVAILABLE"),
    ("OPTIONAL_EXTENDED_ETF_SLEEVE", "EXPLICITLY_UNAVAILABLE"),
    ("EXTENDED_ETF_SCORECARD", "EXPLICITLY_UNAVAILABLE"),
    (
        "STRATEGY_A_HANDOFF_VERSION",
        "LEGACY_ROLE_PROPOSED_FOR_REMOVAL_REQUIRES_SEPARATE_CONTRACT_CHANGE",
    ),
    (
        "STRATEGY_A_HANDOFF_SCOPE",
        "LEGACY_ROLE_PROPOSED_FOR_REMOVAL_REQUIRES_SEPARATE_CONTRACT_CHANGE",
    ),
    ("STRATEGY_A_NOT_ORDER_INSTRUCTION", "EXACT_CODE_SUPPLIED_EQUIVALENT"),
    (
        "STRATEGY_A_MUST_STILL_APPLY",
        "LEGACY_ROLE_PROPOSED_FOR_REMOVAL_REQUIRES_SEPARATE_CONTRACT_CHANGE",
    ),
    ("BASE_SHORTLIST_ELIGIBLE_BY_ROLE", "NOT_MAPPABLE"),
    ("BASE_WATCH_ONLY_BY_ROLE", "NOT_MAPPABLE"),
    ("POSITIVE_DELTA_RESEARCH_SUPPORTED", "NOT_MAPPABLE"),
    ("POSITIVE_DELTA_NOT_IMPLIED_FOR", "NOT_MAPPABLE"),
    ("REPLACEMENT_RANKING_BY_ROLE", "NOT_MAPPABLE"),
    (
        "ROTATION_HANDOFF",
        "LEGACY_ROLE_PROPOSED_FOR_REMOVAL_REQUIRES_SEPARATE_CONTRACT_CHANGE",
    ),
    ("BUY_SIDE_NO_ACTION_HINTS", "QUALITATIVE_NON_AUTHORITY_ONLY"),
    ("EXTENDED_LANE_DOWNSTREAM_GATE", "EXPLICITLY_UNAVAILABLE"),
    ("SELL_SIDE_RESEARCH_BOUNDARY", "NOT_MAPPABLE"),
    ("TARGET_WEIGHTS", "EXPLICITLY_UNAVAILABLE"),
    ("PROVENANCE_IDENTITY_CHAIN", "EXACT_DETERMINISTIC_EQUIVALENT"),
    (
        "BUDGETS_CAPS_EXTERNAL_DETERMINISTIC_OWNER",
        "EXACT_CODE_SUPPLIED_EQUIVALENT",
    ),
    ("H1_EVIDENCE_OBSERVATIONS", "QUALITATIVE_NON_AUTHORITY_ONLY"),
    ("H1_RISKS", "QUALITATIVE_NON_AUTHORITY_ONLY"),
    ("H1_UNCERTAINTIES", "QUALITATIVE_NON_AUTHORITY_ONLY"),
    ("H1_CONTRADICTIONS", "QUALITATIVE_NON_AUTHORITY_ONLY"),
    ("H1_RESEARCH_QUESTIONS", "QUALITATIVE_NON_AUTHORITY_ONLY"),
    ("H1_SUMMARY", "QUALITATIVE_NON_AUTHORITY_ONLY"),
    ("FRESHNESS_INPUTS", "EXACT_CODE_SUPPLIED_EQUIVALENT"),
    ("QUANTITIES_PERMISSIONS_GATES_ORDERS", "EXPLICITLY_UNAVAILABLE"),
)
APPROVED_STATUS_AGGREGATE = {
    "EXACT_DETERMINISTIC_EQUIVALENT": 1,
    "EXACT_CODE_SUPPLIED_EQUIVALENT": 7,
    "QUALITATIVE_NON_AUTHORITY_ONLY": 11,
    "EXPLICITLY_UNAVAILABLE": 16,
    "NOT_MAPPABLE": 12,
    "LEGACY_ROLE_PROPOSED_FOR_REMOVAL_REQUIRES_SEPARATE_CONTRACT_CHANGE": 5,
}


class _FixedClock:
    def now_utc(self) -> datetime:
        return EVALUATION_TIME


@dataclass
class _Inputs:
    candidate: dict[str, object]
    response: dict[str, object]
    envelope: dict[str, object]
    evidence: dict[str, object]
    policy: dict[str, object]
    policy_source: object
    portfolio: dict[str, object]
    portfolio_source: object
    run_context: MmiProjectionRunContext


def _payload(*, view: dict[str, object], prompt: dict[str, object], rationale: str) -> dict[str, object]:
    policy_view = view["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    rows: list[dict[str, object]] = []
    for index, item in enumerate(instruments, start=1):
        assert type(item) is dict
        rows.append(
            {
                "ticker": item["ticker"],
                "evidence_status": "EVIDENCE_SUPPORTED",
                "rationale_12m_plus": rationale,
                "references": [f"POLICY.INSTRUMENT.{index:04d}"],
            }
        )
    context = prompt["prompt_context_binding_sha256"]
    assert type(context) is str
    return {
        "response_schema_version": "mmi_grounded_analysis_response_v2",
        "prompt_context_binding_sha256": context,
        "analysis_status": "QUALITATIVE_ANALYSIS_PROVIDED",
        "instrument_views": rows,
        "anchor_associations_status": "UNAVAILABLE",
        "scheduled_events_status": "UNAVAILABLE",
        "regime_observation_status": "UNAVAILABLE",
        "evidence_observations": [
            {
                "text": "Evidence observation remains report-only.",
                "references": ["VIEW.EVALUATION_TIMESTAMP"],
                "hypothesis": False,
            }
        ],
        "risks": [],
        "uncertainties": [],
        "contradictions": [],
        "research_questions": [],
        "summary": {
            "text": "Research-only summary.",
            "references": ["VIEW.EVALUATION_TIMESTAMP"],
            "hypothesis": False,
        },
    }


def _inputs(
    tmp_path_factory: pytest.TempPathFactory,
    *,
    rationale: str = "Evidence-linked qualitative rationale.",
) -> _Inputs:
    checkout = hermetic.build_checkout(tmp_path_factory, "h1-legacy-mapping")
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    policy_result = build_mmi_policy_projection(
        checkout.policy_source, run_context=run_context
    )
    assert policy_result.valid, policy_result.reason_codes
    assert policy_result.projection is not None
    policy = dict(policy_result.projection)

    portfolio_result = build_mmi_portfolio_snapshot_projection(
        checkout.portfolio_source,
        policy_projection=deepcopy(policy),
        policy_source=checkout.policy_source,
        run_context=run_context,
    )
    assert portfolio_result.valid, portfolio_result.reason_codes
    assert portfolio_result.projection is not None
    portfolio = dict(portfolio_result.projection)

    evidence_result = build_mmi_authenticated_evidence_bundle(
        policy_projection=deepcopy(policy),
        policy_source=checkout.policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    assert evidence_result.valid, evidence_result.reason_codes
    assert evidence_result.projection is not None
    evidence = dict(evidence_result.projection)

    view_result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=checkout.policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    assert view_result.valid, view_result.reason_codes
    assert view_result.projection is not None
    view = dict(view_result.projection)
    prompt = build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=deepcopy(view),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=checkout.policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    raw_response = json.dumps(
        _payload(view=view, prompt=prompt, rationale=rationale),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    envelope = build_mmi_raw_response_envelope_v2(
        grounded_prompt=deepcopy(prompt),
        raw_response_bytes=raw_response,
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=checkout.policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    response = build_mmi_validated_grounded_analysis_response_v2(
        raw_response_envelope=deepcopy(envelope),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=checkout.policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    candidate = build_mmi_legacy_step1_compatibility_candidate_v1(
        validated_grounded_analysis_response=deepcopy(response),
        raw_response_envelope=deepcopy(envelope),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=checkout.policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    return _Inputs(
        candidate=candidate,
        response=response,
        envelope=envelope,
        evidence=evidence,
        policy=policy,
        policy_source=checkout.policy_source,
        portfolio=portfolio,
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )


def _kwargs(inputs: _Inputs) -> dict[str, object]:
    return {
        "legacy_step1_compatibility_candidate": deepcopy(inputs.candidate),
        "validated_grounded_analysis_response": deepcopy(inputs.response),
        "raw_response_envelope": deepcopy(inputs.envelope),
        "evidence_bundle": deepcopy(inputs.evidence),
        "policy_projection": deepcopy(inputs.policy),
        "policy_source": inputs.policy_source,
        "portfolio_projection": deepcopy(inputs.portfolio),
        "portfolio_source": inputs.portfolio_source,
        "run_context": inputs.run_context,
    }


def _build(inputs: _Inputs) -> dict[str, object]:
    return build_mmi_h1_legacy_step1_mapping_report_v1(**_kwargs(inputs))


def _reidentity(report: dict[str, object]) -> None:
    report[IDENTITY_FIELD] = record_identity_sha256(
        report,
        identity_field=IDENTITY_FIELD,
        domain=IDENTITY_DOMAIN,
        maximum_bytes=MAX_MMI_H1_LEGACY_STEP1_MAPPING_REPORT_V1_CANONICAL_BYTES,
    )


def _role_statuses(report: dict[str, object]) -> dict[str, str]:
    rows = report["ordered_role_results"]
    assert type(rows) is list
    return {
        row["legacy_role"]: row["mapping_status"]
        for row in rows
        if type(row) is dict
        and type(row.get("legacy_role")) is str
        and type(row.get("mapping_status")) is str
    }


def test_closed_schema_and_complete_ordered_role_inventory() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
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
        IDENTITY_FIELD,
    }
    results = schema["properties"]["ordered_role_results"]
    assert results["minItems"] == results["maxItems"] == 52
    assert len(results["prefixItems"]) == 52
    assert results["items"] is False


def test_schema_closes_the_ordered_role_status_pair_contract(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    report = _build(_inputs(tmp_path_factory))
    assert not list(validator.iter_errors(report))

    swapped = deepcopy(report)
    swapped_rows = swapped["ordered_role_results"]
    assert type(swapped_rows) is list
    first_status = swapped_rows[0]["mapping_status"]
    swapped_rows[0]["mapping_status"] = swapped_rows[3]["mapping_status"]
    swapped_rows[3]["mapping_status"] = first_status
    assert list(validator.iter_errors(swapped))

    missing = deepcopy(report)
    missing_rows = missing["ordered_role_results"]
    assert type(missing_rows) is list
    missing_rows.pop()
    assert list(validator.iter_errors(missing))

    duplicate = deepcopy(report)
    duplicate_rows = duplicate["ordered_role_results"]
    assert type(duplicate_rows) is list
    duplicate_rows[1] = deepcopy(duplicate_rows[0])
    assert list(validator.iter_errors(duplicate))

    unknown_role = deepcopy(report)
    unknown_role_rows = unknown_role["ordered_role_results"]
    assert type(unknown_role_rows) is list
    unknown_role_rows[0]["legacy_role"] = "UNKNOWN_LEGACY_ROLE"
    assert list(validator.iter_errors(unknown_role))

    unknown_status = deepcopy(report)
    unknown_status_rows = unknown_status["ordered_role_results"]
    assert type(unknown_status_rows) is list
    unknown_status_rows[0]["mapping_status"] = "PARTIAL_EQUIVALENT"
    assert list(validator.iter_errors(unknown_status))


def test_builds_complete_non_authorizing_mapping_and_revalidates(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    inputs = _inputs(tmp_path_factory)
    report = _build(inputs)

    assert report["schema_version"] == "mmi_h1_legacy_step1_mapping_report_v1"
    assert report["artifact_kind"] == "MMI_H1_LEGACY_STEP1_MAPPING_REPORT"
    assert report["role_map_version"] == "h1_legacy_step1_role_map_v1"
    assert report["target_legacy_validator_contract_version"] == (
        LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION
    )
    assert report["report_only"] is True
    assert report["authority_effect"] == "NONE"
    assert report["not_authorization"] is True
    assert report["full_legacy_compatibility"] is False
    assert validate_mmi_h1_legacy_step1_mapping_report_v1(
        value=deepcopy(report), **_kwargs(inputs)
    ) == report

    results = report["ordered_role_results"]
    assert type(results) is list
    actual_pairs = tuple(
        (row["legacy_role"], row["mapping_status"])
        for row in results
        if type(row) is dict
    )
    assert len(results) == len(APPROVED_ROLE_STATUS_PAIRS) == 52
    assert actual_pairs == APPROVED_ROLE_STATUS_PAIRS
    assert len({role for role, _ in actual_pairs}) == 52
    assert Counter(status for _, status in actual_pairs) == APPROVED_STATUS_AGGREGATE


def test_explicit_gaps_qualitative_boundaries_and_external_owners_are_pinned(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    report = _build(_inputs(tmp_path_factory))
    statuses = _role_statuses(report)

    assert statuses["FRESHNESS_INPUTS"] == "EXACT_CODE_SUPPLIED_EQUIVALENT"
    assert statuses["QUANTITIES_PERMISSIONS_GATES_ORDERS"] == (
        "EXPLICITLY_UNAVAILABLE"
    )
    assert statuses["STRATEGY_A_NOT_ORDER_INSTRUCTION"] == (
        "EXACT_CODE_SUPPLIED_EQUIVALENT"
    )
    assert statuses["BUY_UNIVERSE_SCORECARD_THESIS_12M_PLUS_SUPPORTED"] == (
        "QUALITATIVE_NON_AUTHORITY_ONLY"
    )
    assert statuses["STRUCTURAL_THEMES_6_18M"] == (
        "QUALITATIVE_NON_AUTHORITY_ONLY"
    )
    assert statuses["BUY_SIDE_NO_ACTION_HINTS"] == (
        "QUALITATIVE_NON_AUTHORITY_ONLY"
    )
    assert "target_weights" not in report["deterministic_mappings"]


@pytest.mark.parametrize(
    "mutation", ("unknown", "unknown-status", "missing", "duplicate")
)
def test_role_inventory_rejects_unknown_missing_and_duplicate_results(
    tmp_path_factory: pytest.TempPathFactory,
    mutation: str,
) -> None:
    inputs = _inputs(tmp_path_factory)
    report = _build(inputs)
    rows = report["ordered_role_results"]
    assert type(rows) is list
    if mutation == "unknown":
        rows[0]["legacy_role"] = "UNKNOWN_LEGACY_ROLE"
    elif mutation == "unknown-status":
        rows[0]["mapping_status"] = "PARTIAL_EQUIVALENT"
    elif mutation == "missing":
        rows.pop()
    else:
        rows[1]["legacy_role"] = rows[0]["legacy_role"]

    with pytest.raises(MmiH1LegacyStep1MappingReportV1Error) as exc_info:
        validate_mmi_h1_legacy_step1_mapping_report_v1(
            value=report, **_kwargs(inputs)
        )
    assert exc_info.value.code == "MMI_H1_LEGACY_MAPPING_INVALID_ROLE_MAP"


def test_target_validator_contract_pin_and_report_identity_fail_closed(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    inputs = _inputs(tmp_path_factory)
    report = _build(inputs)
    report["target_legacy_validator_contract_version"] = "unknown_v9"

    with pytest.raises(MmiH1LegacyStep1MappingReportV1Error) as exc_info:
        validate_mmi_h1_legacy_step1_mapping_report_v1(
            value=report, **_kwargs(inputs)
        )
    assert exc_info.value.code == (
        "MMI_H1_LEGACY_MAPPING_TARGET_VALIDATOR_VERSION_MISMATCH"
    )

    report = _build(inputs)
    report["not_authorization"] = False
    with pytest.raises(MmiH1LegacyStep1MappingReportV1Error) as exc_info:
        validate_mmi_h1_legacy_step1_mapping_report_v1(
            value=report, **_kwargs(inputs)
        )
    assert exc_info.value.code == "MMI_H1_LEGACY_MAPPING_SCHEMA_INVALID"


def test_normalization_is_ascii_only_collision_safe_and_sequence_preserving() -> None:
    assert owner._normalize_identifier_sequence([" qqq\t", "smh\r\n"]) == [
        "QQQ",
        "SMH",
    ]
    with pytest.raises(MmiH1LegacyStep1MappingReportV1Error) as exc_info:
        owner._normalize_identifier_sequence([" qqq", "QQQ"])
    assert exc_info.value.code == "MMI_H1_LEGACY_MAPPING_IDENTIFIER_COLLISION"
    with pytest.raises(MmiH1LegacyStep1MappingReportV1Error) as exc_info:
        owner._normalize_identifier_sequence(["QQQ", "QQQ"])
    assert exc_info.value.code == "MMI_H1_LEGACY_MAPPING_DUPLICATE_IDENTIFIER"
    with pytest.raises(MmiH1LegacyStep1MappingReportV1Error) as exc_info:
        owner._normalize_identifier_sequence(["BRK/B"])
    assert exc_info.value.code == "MMI_H1_LEGACY_MAPPING_IDENTIFIER_NORMALIZATION_FAILED"


def test_membership_order_and_identity_chain_are_exactly_bound(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    inputs = _inputs(tmp_path_factory)
    report = _build(inputs)
    mappings = report["deterministic_mappings"]
    assert type(mappings) is dict
    assert mappings["base_trade_universe_ordered"] == [
        "QQQ",
        "VOO",
        "VTI",
        "VT",
        "SMH",
        "IGV",
    ]
    assert mappings["legacy_role_membership"] == [
        {"legacy_role": "benchmark_carrier_core", "tickers": ["QQQ"]},
        {
            "legacy_role": "diversified_core_buffer",
            "tickers": ["VOO", "VTI", "VT"],
        },
        {"legacy_role": "sector_alpha_tilt", "tickers": ["SMH", "IGV"]},
    ]

    changed_mapping = deepcopy(report)
    changed = changed_mapping["deterministic_mappings"]
    assert type(changed) is dict
    base = changed["base_trade_universe_ordered"]
    assert type(base) is list
    base.reverse()
    _reidentity(changed_mapping)
    with pytest.raises(MmiH1LegacyStep1MappingReportV1Error) as exc_info:
        validate_mmi_h1_legacy_step1_mapping_report_v1(
            value=changed_mapping, **_kwargs(inputs)
        )
    assert exc_info.value.code == "MMI_H1_LEGACY_MAPPING_SEQUENCE_MEMBERSHIP_MISMATCH"

    changed_identity = deepcopy(report)
    chain = changed_identity["upstream_identity_chain"]
    assert type(chain) is dict
    chain["portfolio_projection_identity_sha256"] = "0" * 64
    _reidentity(changed_identity)
    with pytest.raises(MmiH1LegacyStep1MappingReportV1Error) as exc_info:
        validate_mmi_h1_legacy_step1_mapping_report_v1(
            value=changed_identity, **_kwargs(inputs)
        )
    assert exc_info.value.code == "MMI_H1_LEGACY_MAPPING_IDENTITY_MISMATCH"


def test_invalid_upstream_and_authority_bearing_prose_fail_closed(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    inputs = _inputs(tmp_path_factory)
    bad_kwargs = _kwargs(inputs)
    candidate = bad_kwargs["legacy_step1_compatibility_candidate"]
    assert type(candidate) is dict
    candidate["legacy_step1_compatibility_candidate_identity_sha256"] = "0" * 64
    with pytest.raises(MmiH1LegacyStep1MappingReportV1Error) as exc_info:
        build_mmi_h1_legacy_step1_mapping_report_v1(**bad_kwargs)
    assert exc_info.value.code == "MMI_H1_LEGACY_MAPPING_UPSTREAM_ARTIFACT_INVALID"

    missing_portfolio = _kwargs(inputs)
    missing_portfolio["portfolio_projection"] = None
    with pytest.raises(MmiH1LegacyStep1MappingReportV1Error) as exc_info:
        build_mmi_h1_legacy_step1_mapping_report_v1(**missing_portfolio)
    assert exc_info.value.code == "MMI_H1_LEGACY_MAPPING_UPSTREAM_ARTIFACT_INVALID"

    authority_inputs = _inputs(
        tmp_path_factory, rationale="target_weights=50 is proposed by prose"
    )
    with pytest.raises(MmiH1LegacyStep1MappingReportV1Error) as exc_info:
        _build(authority_inputs)
    assert exc_info.value.code == (
        "MMI_H1_LEGACY_MAPPING_AUTHORITY_BEARING_QUALITATIVE_INPUT"
    )


def test_no_production_module_imports_or_consumes_the_report_only_adapter() -> None:
    package_root = repo_root() / "src/investment_orchestrator"
    offline_root = package_root / "offline"
    target = "investment_orchestrator.offline.mmi_h1_legacy_step1_mapping_report_v1"
    target_leaf = "mmi_h1_legacy_step1_mapping_report_v1"
    for path in package_root.rglob("*.py"):
        if offline_root in path.parents:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert not any(
            (
                isinstance(node, ast.Import)
                and any(alias.name == target for alias in node.names)
            )
            or (
                isinstance(node, ast.ImportFrom)
                and node.module == target
            )
            or (
                isinstance(node, ast.ImportFrom)
                and node.module == "investment_orchestrator.offline"
                and any(alias.name == target_leaf for alias in node.names)
            )
            for node in ast.walk(tree)
        ), path
