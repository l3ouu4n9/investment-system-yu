from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiProjectionRunContext,
    MmiSourceRole,
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
from investment_orchestrator.mmi.raw_response_envelope_v2 import (
    build_mmi_raw_response_envelope_v2,
)
from investment_orchestrator.mmi.source_capture import (
    _capture_mmi_source_at_root,
)
from investment_orchestrator.mmi.validated_grounded_analysis_response_v2 import (
    build_mmi_validated_grounded_analysis_response_v2,
)
from investment_orchestrator.offline.mmi_legacy_step1_comparison_report_v1 import (
    COVERAGE_CATEGORIES,
    MAX_LEGACY_RESEARCH_RAW_BYTES,
    MAX_LEGACY_STRATEGY_SETTINGS_CANONICAL_BYTES,
    MmiLegacyStep1ComparisonReportV1Error,
    build_mmi_legacy_step1_comparison_report_v1,
    validate_mmi_legacy_step1_comparison_report_v1,
)
import _mmi_hermetic_source_checkout as hermetic


SCHEMA_NAME = "mmi_legacy_step1_comparison_report_v1.schema.json"
IDENTITY_FIELD = "comparison_report_identity_sha256"
FIXTURES = repo_root() / "tests/fixtures/step1_contract_failures"
VALID_LEGACY = FIXTURES / "minimal_valid_research_handoff.json"
STRUCTURED_LEGACY = FIXTURES / "legacy_structured_missing_handoff.json"
NARRATIVE_LEGACY = FIXTURES / "current_research_output_minimal.json"
WRAPPED_LEGACY = FIXTURES / "wrapped_research_json_minimal.txt"
EVALUATION_TIME = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
# Test-owned source dates, fixed before ``EVALUATION_TIME`` so that an
# operational ``inputs/current`` refresh cannot reach this module.
SOURCE_AS_OF = "2026-07-30"
SOURCE_RUN_TIMESTAMP_ET = "2026-07-30 10:00 ET"

UNAVAILABLE_LEGACY_FIELDS = (
    "legacy_instrument_count",
    "shared_instrument_count",
    "membership_equal",
    "shared_sequence_equal",
    "h1_only_tickers",
    "legacy_only_tickers",
    "legacy_duplicate_tickers",
    "legacy_role_layers_present",
)


class _FixedClock:
    def now_utc(self) -> datetime:
        return EVALUATION_TIME


class _Inputs:
    def __init__(
        self,
        *,
        candidate: dict[str, object],
        response: dict[str, object],
        envelope: dict[str, object],
        evidence: dict[str, object],
        policy: dict[str, object],
        policy_source: MmiCapturedSource,
        run_context: MmiProjectionRunContext,
        settings: dict[str, object],
    ) -> None:
        self.candidate = candidate
        self.response = response
        self.envelope = envelope
        self.evidence = evidence
        self.policy = policy
        self.policy_source = policy_source
        self.run_context = run_context
        self.settings = settings


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _payload(
    *,
    view: dict[str, object],
    prompt: dict[str, object],
) -> dict[str, object]:
    policy_view = view["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    rows = []
    for index, item in enumerate(instruments, start=1):
        assert type(item) is dict
        rows.append(
            {
                "ticker": item["ticker"],
                "evidence_status": "EVIDENCE_SUPPORTED",
                "rationale_12m_plus": "R" * 40,
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
                "text": "Deterministic evidence observation.",
                "references": ["VIEW.EVALUATION_TIMESTAMP"],
                "hypothesis": False,
            }
        ],
        "risks": [],
        "uncertainties": [],
        "contradictions": [],
        "research_questions": [],
        "summary": {
            "text": "Qualitative evidence remains report-only.",
            "references": ["VIEW.EVALUATION_TIMESTAMP"],
            "hypothesis": False,
        },
    }


def _inputs_from_source(
    *,
    source: MmiCapturedSource,
    settings: dict[str, object],
) -> _Inputs:
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    policy_result = build_mmi_policy_projection(source, run_context=run_context)
    assert policy_result.valid, policy_result.reason_codes
    assert policy_result.projection is not None
    policy = dict(policy_result.projection)

    evidence_result = build_mmi_authenticated_evidence_bundle(
        policy_projection=deepcopy(policy),
        policy_source=source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    assert evidence_result.valid, evidence_result.reason_codes
    assert evidence_result.projection is not None
    evidence = dict(evidence_result.projection)

    view_result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    assert view_result.valid, view_result.reason_codes
    assert view_result.projection is not None
    view = dict(view_result.projection)

    prompt = build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=deepcopy(view),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    raw_response = json.dumps(
        _payload(view=view, prompt=prompt),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    envelope = build_mmi_raw_response_envelope_v2(
        grounded_prompt=deepcopy(prompt),
        raw_response_bytes=raw_response,
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    response = build_mmi_validated_grounded_analysis_response_v2(
        raw_response_envelope=deepcopy(envelope),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    candidate = build_mmi_legacy_step1_compatibility_candidate_v1(
        validated_grounded_analysis_response=deepcopy(response),
        raw_response_envelope=deepcopy(envelope),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    return _Inputs(
        candidate=candidate,
        response=response,
        envelope=envelope,
        evidence=evidence,
        policy=policy,
        policy_source=source,
        run_context=run_context,
        settings=settings,
    )


@pytest.fixture(scope="module", autouse=True)
def _no_live_operational_inputs():
    with hermetic.live_operational_input_access_forbidden():
        yield


@pytest.fixture(scope="module")
def checkout(
    tmp_path_factory: pytest.TempPathFactory,
) -> hermetic.HermeticSourceCheckout:
    return hermetic.build_checkout(
        tmp_path_factory,
        "h2-hermetic-checkout",
        as_of=SOURCE_AS_OF,
        run_timestamp_et=SOURCE_RUN_TIMESTAMP_ET,
        updated=SOURCE_AS_OF,
    )


@pytest.fixture(scope="module")
def inputs(checkout: hermetic.HermeticSourceCheckout) -> _Inputs:
    raw = checkout.strategy_settings_raw
    settings = yaml.safe_load(raw.decode("utf-8"))
    assert type(settings) is dict
    return _inputs_from_source(
        source=checkout.policy_source,
        settings=settings,
    )


def _kwargs(inputs: _Inputs) -> dict[str, object]:
    return {
        "legacy_step1_compatibility_candidate": deepcopy(inputs.candidate),
        "validated_grounded_analysis_response": deepcopy(inputs.response),
        "raw_response_envelope": deepcopy(inputs.envelope),
        "evidence_bundle": deepcopy(inputs.evidence),
        "policy_projection": deepcopy(inputs.policy),
        "policy_source": inputs.policy_source,
        "portfolio_projection": None,
        "portfolio_source": None,
        "run_context": inputs.run_context,
        "legacy_strategy_settings": deepcopy(inputs.settings),
    }


def _report(inputs: _Inputs, legacy_bytes: bytes) -> dict[str, object]:
    return build_mmi_legacy_step1_comparison_report_v1(
        legacy_research_raw_bytes=legacy_bytes,
        **_kwargs(inputs),
    )


def _mutated_valid_legacy(mutate) -> bytes:
    """Deterministically mutate the committed valid legacy fixture."""
    payload = json.loads(VALID_LEGACY.read_text(encoding="utf-8"))
    mutate(payload)
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _row(report: dict[str, object], category: str) -> dict[str, object]:
    rows = report["coverage_comparison"]
    assert type(rows) is list
    for row in rows:
        assert type(row) is dict
        if row["category"] == category:
            return row
    raise AssertionError(category)


def test_valid_strict_legacy_fixture_builds_validates_and_repeats_exactly(
    inputs: _Inputs,
) -> None:
    legacy_bytes = VALID_LEGACY.read_bytes()
    first = _report(inputs, legacy_bytes)
    second = _report(inputs, legacy_bytes)
    assert first == second
    assert _canonical(first) == _canonical(second)
    validate_artifact_schema(first, schema_name=SCHEMA_NAME)
    assert first["report_only"] is True
    assert first["authority_effect"] == "NONE"
    assert first["legacy_contract_status"] == {
        "raw_parse_status": "PARSED",
        "strict_handoff_status": "STRICT_HANDOFF_VALID",
        "strict_handoff_blocker_count": 0,
        "legacy_source_shape": "strict",
        "legacy_self_reported_validation_passed": True,
    }
    assert validate_mmi_legacy_step1_comparison_report_v1(
        value=first,
        legacy_research_raw_bytes=legacy_bytes,
        **_kwargs(inputs),
    ) == first


def test_self_reported_pass_never_makes_a_strict_invalid_artifact_valid(
    inputs: _Inputs,
) -> None:
    legacy_bytes = STRUCTURED_LEGACY.read_bytes()
    payload = json.loads(STRUCTURED_LEGACY.read_text(encoding="utf-8"))
    assert payload["validation_summary"]["passed"] is True
    report = _report(inputs, legacy_bytes)
    status = report["legacy_contract_status"]
    assert type(status) is dict
    assert status["legacy_self_reported_validation_passed"] is True
    assert status["strict_handoff_status"] == "LEGACY_HANDOFF_CONTRACT_FAILURE"
    blocker_count = status["strict_handoff_blocker_count"]
    assert type(blocker_count) is int and blocker_count > 0
    assert status["legacy_source_shape"] == "legacy_structured"
    comparison = report["instrument_comparison"]
    assert type(comparison) is dict
    assert comparison["comparison_basis"] == (
        "STRICT_INVALID_BUT_STRUCTURALLY_READABLE"
    )
    assert comparison["legacy_instrument_count"] == 2


@pytest.mark.parametrize(
    ("legacy_bytes", "parse_status", "basis"),
    [
        (
            b"no legacy research object here",
            "LEGACY_PARSE_FAILURE",
            "UNAVAILABLE_PARSE_FAILURE",
        ),
        (
            b'{"schema_version": "1.0"}',
            "LEGACY_SCHEMA_FAILURE",
            "UNAVAILABLE_SCHEMA_FAILURE",
        ),
    ],
)
def test_legacy_parse_and_schema_failures_are_classified_not_raised(
    inputs: _Inputs,
    legacy_bytes: bytes,
    parse_status: str,
    basis: str,
) -> None:
    report = _report(inputs, legacy_bytes)
    validate_artifact_schema(report, schema_name=SCHEMA_NAME)
    assert report["legacy_contract_status"] == {
        "raw_parse_status": parse_status,
        "strict_handoff_status": "NOT_EVALUATED",
        "strict_handoff_blocker_count": None,
        "legacy_source_shape": None,
        "legacy_self_reported_validation_passed": None,
    }
    comparison = report["instrument_comparison"]
    assert type(comparison) is dict
    assert comparison["comparison_basis"] == basis
    provenance = report["provenance"]
    assert type(provenance) is dict
    assert provenance["legacy_parsed_payload_canonical_sha256"] is None
    assert provenance["legacy_normalized_candidate_canonical_sha256"] is None


@pytest.mark.parametrize(
    "legacy_bytes",
    [
        b"no legacy research object here",
        b'{"schema_version": "1.0"}',
        None,
    ],
)
def test_unavailable_bases_never_report_empty_or_false_legacy_facts(
    inputs: _Inputs,
    legacy_bytes: bytes | None,
) -> None:
    payload = (
        NARRATIVE_LEGACY.read_bytes() if legacy_bytes is None else legacy_bytes
    )
    report = _report(inputs, payload)
    comparison = report["instrument_comparison"]
    assert type(comparison) is dict
    assert comparison["comparison_basis"].startswith("UNAVAILABLE_")
    for field in UNAVAILABLE_LEGACY_FIELDS:
        value = comparison[field]
        assert value is None, field
        assert value is not False and value != 0 and value != []
    assert type(comparison["h1_instrument_count"]) is int
    assert comparison["h1_policy_roles_present"] == [
        "APPROVED_EXTENDED",
        "CORE",
        "SATELLITE",
    ]


def test_instrument_membership_order_and_duplicate_differences_are_exact(
    inputs: _Inputs,
) -> None:
    baseline = _report(inputs, VALID_LEGACY.read_bytes())
    base = baseline["instrument_comparison"]
    assert type(base) is dict
    assert base["comparison_basis"] == "STRICT_VALID"
    assert base["legacy_instrument_count"] == 6
    assert base["shared_instrument_count"] == 6
    assert base["membership_equal"] is False
    assert base["shared_sequence_equal"] is True
    assert base["legacy_only_tickers"] == []
    assert base["legacy_duplicate_tickers"] == []
    # ``h1_only_tickers`` is the H1 analysis universe minus the legacy
    # scorecard, sorted.  The legacy fixture covers exactly the synthetic
    # core and satellite members, so the remainder is the synthetic
    # extended-ETF sleeve in sorted order.  This expectation is owned by the
    # test sources; it is deliberately not a snapshot of ``inputs/current``.
    assert base["h1_only_tickers"] == ["CIBR", "QUAL"]
    assert base["legacy_role_layers_present"] == [
        "benchmark_carrier_core",
        "diversified_core_buffer",
        "sector_alpha_tilt",
    ]

    def _swap(payload: dict[str, object]) -> None:
        rows = payload["buy_universe_scorecard"]
        assert type(rows) is list
        rows[0], rows[1] = rows[1], rows[0]

    reordered = _report(inputs, _mutated_valid_legacy(_swap))[
        "instrument_comparison"
    ]
    assert type(reordered) is dict
    # Reordering rows changes exactly the sequence fact: membership is a set
    # comparison and is unaffected, and every other structural fact is stable.
    assert reordered["shared_sequence_equal"] is False
    assert reordered["membership_equal"] is False
    assert {
        key: value
        for key, value in reordered.items()
        if key != "shared_sequence_equal"
    } == {
        key: value
        for key, value in base.items()
        if key != "shared_sequence_equal"
    }

    def _foreign(payload: dict[str, object]) -> None:
        rows = payload["buy_universe_scorecard"]
        assert type(rows) is list
        assert type(rows[-1]) is dict
        rows[-1]["ticker"] = "ZZZFOREIGN"

    foreign = _report(inputs, _mutated_valid_legacy(_foreign))[
        "instrument_comparison"
    ]
    assert type(foreign) is dict
    assert foreign["legacy_only_tickers"] == ["ZZZFOREIGN"]
    assert "IGV" in foreign["h1_only_tickers"]
    assert foreign["shared_instrument_count"] == 5

    def _duplicate(payload: dict[str, object]) -> None:
        rows = payload["buy_universe_scorecard"]
        assert type(rows) is list
        assert type(rows[-1]) is dict and type(rows[0]) is dict
        rows[-1]["ticker"] = rows[0]["ticker"]

    duplicated = _report(inputs, _mutated_valid_legacy(_duplicate))[
        "instrument_comparison"
    ]
    assert type(duplicated) is dict
    assert duplicated["legacy_duplicate_tickers"] == ["QQQ"]
    assert duplicated["legacy_instrument_count"] == 6
    # A duplicate must never read as a fully equivalent legacy sequence.
    assert duplicated["shared_sequence_equal"] is False
    assert duplicated["membership_equal"] is False


def test_unreadable_scorecard_shape_is_distinct_from_a_parse_failure(
    inputs: _Inputs,
) -> None:
    def _shape(payload: dict[str, object]) -> None:
        payload["buy_universe_scorecard"] = {"QQQ": {"role_layer": "x"}}

    report = _report(inputs, _mutated_valid_legacy(_shape))
    status = report["legacy_contract_status"]
    comparison = report["instrument_comparison"]
    assert type(status) is dict and type(comparison) is dict
    assert status["raw_parse_status"] == "PARSED"
    assert comparison["comparison_basis"] == "UNAVAILABLE_SCORECARD_SHAPE"
    assert comparison["legacy_instrument_count"] is None
    for category in (
        "INSTRUMENT_RATIONALE",
        "INSTRUMENT_REFERENCES",
        "ANCHOR_ASSOCIATIONS",
    ):
        row = _row(report, category)
        assert row["legacy_status"] == "UNAVAILABLE_DUE_TO_LEGACY_CONTRACT"
        assert row["legacy_count"] is None
        assert row["comparison_class"] == "NOT_COMPARABLE"


def test_coverage_rows_are_the_fixed_fifteen_with_orthogonal_dimensions(
    inputs: _Inputs,
) -> None:
    report = _report(inputs, VALID_LEGACY.read_bytes())
    rows = report["coverage_comparison"]
    assert type(rows) is list
    assert tuple(row["category"] for row in rows) == COVERAGE_CATEGORIES
    assert len(rows) == 15
    assert _row(report, "SCHEDULED_EVENTS")["h1_status"] == (
        "EXPLICITLY_UNAVAILABLE_TIER_A"
    )
    assert _row(report, "TARGET_WEIGHTS")["h1_status"] == (
        "POLICY_METHOD_ABSENT"
    )
    assert _row(report, "STRUCTURAL_THEMES")["h1_status"] == "NOT_REPRESENTED"
    assert _row(report, "RISKS")["h1_status"] == "ABSENT"
    assert _row(report, "RISKS")["h1_count"] == 0
    assert _row(report, "EVIDENCE_OBSERVATIONS")["h1_status"] == "PRESENT"
    assert _row(report, "SUMMARY")["legacy_status"] == "NOT_REPRESENTED"
    assert _row(report, "SUMMARY")["legacy_count"] is None
    assert _row(report, "INSTRUMENT_RATIONALE")["comparison_class"] == (
        "AVAILABLE_IN_BOTH"
    )
    assert _row(report, "STRUCTURAL_THEMES")["comparison_class"] == (
        "AVAILABLE_ONLY_IN_LEGACY"
    )
    assert _row(report, "EVIDENCE_OBSERVATIONS")["comparison_class"] == (
        "AVAILABLE_ONLY_IN_H1"
    )
    assert _row(report, "TARGET_WEIGHTS")["comparison_class"] == (
        "AVAILABLE_IN_NEITHER"
    )
    assert _row(report, "INSTRUMENT_REFERENCES")["legacy_consumer_class"] == (
        "DETERMINISTIC_CONSUMER_PRESENT"
    )
    # Legacy deterministic validation/promotion logic inspects
    # thesis_12m_plus_summary for blocking data-gap markers.
    assert _row(report, "INSTRUMENT_RATIONALE")["legacy_consumer_class"] == (
        "DETERMINISTIC_CONSUMER_PRESENT"
    )
    assert _row(report, "STRUCTURAL_THEMES")["legacy_consumer_class"] == (
        "PROMPT_ONLY"
    )
    assert _row(report, "RISKS")["legacy_consumer_class"] == "NO_CONSUMER"


def test_tier_a_unavailable_h1_category_with_present_legacy_is_legacy_only(
    inputs: _Inputs,
) -> None:
    def _add_event(payload: dict[str, object]) -> None:
        payload["scheduled_events"] = [
            {"event_id": "evt_0001", "date_et": "2026-07-01"}
        ]

    report = _report(inputs, _mutated_valid_legacy(_add_event))
    row = _row(report, "SCHEDULED_EVENTS")
    assert row["h1_status"] == "EXPLICITLY_UNAVAILABLE_TIER_A"
    assert row["legacy_status"] == "PRESENT"
    assert row["legacy_count"] == 1
    assert row["h1_count"] is None
    assert row["comparison_class"] == "AVAILABLE_ONLY_IN_LEGACY"


def test_parse_failure_makes_artifact_derived_categories_not_comparable(
    inputs: _Inputs,
) -> None:
    report = _report(inputs, b"no legacy research object here")
    for category in (
        "INSTRUMENT_RATIONALE",
        "INSTRUMENT_REFERENCES",
        "ANCHOR_ASSOCIATIONS",
        "SCHEDULED_EVENTS",
        "REGIME_INPUTS",
        "STRUCTURAL_THEMES",
        "TOP_FIVE_NEXT_WEEK",
        "EXTENDED_ETF_SLEEVE_FIELDS",
    ):
        row = _row(report, category)
        assert row["legacy_status"] == "UNAVAILABLE_DUE_TO_LEGACY_CONTRACT"
        assert row["legacy_count"] is None
        assert row["comparison_class"] == "NOT_COMPARABLE"
    summary = report["comparison_summary"]
    assert type(summary) is dict
    assert summary["coverage_not_comparable_count"] == 8


def test_summary_counts_and_limitations_are_derived_from_report_content(
    inputs: _Inputs,
) -> None:
    for legacy_bytes in (
        VALID_LEGACY.read_bytes(),
        STRUCTURED_LEGACY.read_bytes(),
        NARRATIVE_LEGACY.read_bytes(),
        b"no legacy research object here",
    ):
        report = _report(inputs, legacy_bytes)
        rows = report["coverage_comparison"]
        limitations = report["limitations"]
        summary = report["comparison_summary"]
        assert type(rows) is list
        assert type(limitations) is list
        assert type(summary) is dict
        classes = [row["comparison_class"] for row in rows]
        assert summary == {
            "coverage_available_in_both_count": classes.count(
                "AVAILABLE_IN_BOTH"
            ),
            "coverage_only_in_one_count": (
                classes.count("AVAILABLE_ONLY_IN_H1")
                + classes.count("AVAILABLE_ONLY_IN_LEGACY")
            ),
            "coverage_not_comparable_count": classes.count("NOT_COMPARABLE"),
            "limitation_count": len(limitations),
        }
        assert limitations == sorted(set(limitations))


def test_limitations_are_conditional_on_the_actual_comparison_path(
    inputs: _Inputs,
) -> None:
    readable = _report(inputs, VALID_LEGACY.read_bytes())["limitations"]
    unavailable = _report(inputs, b"not parseable")["limitations"]
    assert type(readable) is list and type(unavailable) is list
    assert "LEGACY_INSTRUMENT_IDENTIFIERS_COMPARED_WITHOUT_NORMALIZATION" in (
        readable
    )
    assert "POLICY_ROLE_AND_LEGACY_ROLE_LAYER_MAPPING_UNDEFINED" in readable
    assert "LEGACY_STRICT_VALIDATOR_HAS_NO_DECLARED_CONTRACT_VERSION" in (
        readable
    )
    assert "H1_AND_LEGACY_REFERENCE_SYSTEMS_STRUCTURALLY_DISTINCT" in readable
    for absent in (
        "LEGACY_INSTRUMENT_IDENTIFIERS_COMPARED_WITHOUT_NORMALIZATION",
        "POLICY_ROLE_AND_LEGACY_ROLE_LAYER_MAPPING_UNDEFINED",
        "LEGACY_STRICT_VALIDATOR_HAS_NO_DECLARED_CONTRACT_VERSION",
        "H1_AND_LEGACY_REFERENCE_SYSTEMS_STRUCTURALLY_DISTINCT",
    ):
        assert absent not in unavailable
    for always in (
        "H1_SOURCE_CAPABILITY_GAPS_REQUIRE_TIER_B",
        "LEGACY_STRATEGY_SETTINGS_NOT_PROVEN_IDENTICAL_TO_H1_POLICY_SOURCE",
        "TARGET_WEIGHTS_NOT_DERIVABLE_FROM_CURRENT_POLICY_METHOD",
    ):
        assert always in readable and always in unavailable


def test_altered_h1_candidate_is_rejected_through_its_existing_owner(
    inputs: _Inputs,
) -> None:
    kwargs = _kwargs(inputs)
    candidate = kwargs["legacy_step1_compatibility_candidate"]
    assert type(candidate) is dict
    summary = candidate["summary"]
    assert type(summary) is dict
    summary["text"] = "Independently resealed qualitative substitution."
    candidate[
        "legacy_step1_compatibility_candidate_identity_sha256"
    ] = hashlib.sha256(b"resealed").hexdigest()
    with pytest.raises(MmiLegacyStep1ComparisonReportV1Error) as excinfo:
        build_mmi_legacy_step1_comparison_report_v1(
            legacy_research_raw_bytes=VALID_LEGACY.read_bytes(),
            **kwargs,
        )
    assert excinfo.value.code == (
        "MMI_LEGACY_STEP1_COMPARISON_H1_CANDIDATE_INVALID"
    )


def test_source_inconsistent_h1_upstream_is_rejected_through_its_owner(
    inputs: _Inputs,
) -> None:
    kwargs = _kwargs(inputs)
    envelope = kwargs["raw_response_envelope"]
    assert type(envelope) is dict
    envelope["raw_response_sha256"] = "f" * 64
    with pytest.raises(MmiLegacyStep1ComparisonReportV1Error) as excinfo:
        build_mmi_legacy_step1_comparison_report_v1(
            legacy_research_raw_bytes=VALID_LEGACY.read_bytes(),
            **kwargs,
        )
    assert excinfo.value.code == (
        "MMI_LEGACY_STEP1_COMPARISON_H1_CANDIDATE_INVALID"
    )


def test_exact_raw_byte_provenance_hash_is_over_the_supplied_bytes(
    inputs: _Inputs,
) -> None:
    legacy_bytes = WRAPPED_LEGACY.read_bytes()
    report = _report(inputs, legacy_bytes)
    provenance = report["provenance"]
    assert type(provenance) is dict
    assert provenance["legacy_raw_bytes_sha256"] == hashlib.sha256(
        legacy_bytes
    ).hexdigest()
    assert provenance["legacy_strategy_settings_canonical_sha256"] == (
        hashlib.sha256(_canonical(inputs.settings)).hexdigest()
    )
    assert provenance[
        "legacy_step1_compatibility_candidate_identity_sha256"
    ] == inputs.candidate[
        "legacy_step1_compatibility_candidate_identity_sha256"
    ]
    assert set(provenance) == {
        "legacy_step1_compatibility_candidate_identity_sha256",
        "legacy_raw_bytes_sha256",
        "legacy_parsed_payload_canonical_sha256",
        "legacy_normalized_candidate_canonical_sha256",
        "legacy_strategy_settings_canonical_sha256",
    }


@pytest.mark.parametrize(
    "legacy_bytes",
    [
        bytearray(b'{"schema_version": "1.0"}'),
        '{"schema_version": "1.0"}',
        b"\xff\xfe not utf-8",
        b"x" * (MAX_LEGACY_RESEARCH_RAW_BYTES + 1),
    ],
)
def test_invalid_raw_byte_input_is_an_h2_input_contract_failure(
    inputs: _Inputs,
    legacy_bytes: object,
) -> None:
    with pytest.raises(MmiLegacyStep1ComparisonReportV1Error) as excinfo:
        build_mmi_legacy_step1_comparison_report_v1(
            legacy_research_raw_bytes=legacy_bytes,
            **_kwargs(inputs),
        )
    assert excinfo.value.code == "MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID"


def test_raw_byte_ceiling_accepts_the_exact_boundary(inputs: _Inputs) -> None:
    padding = b" " * (
        MAX_LEGACY_RESEARCH_RAW_BYTES - len(b'{"schema_version": "1.0"}')
    )
    at_limit = b'{"schema_version": "1.0"}' + padding
    assert len(at_limit) == MAX_LEGACY_RESEARCH_RAW_BYTES
    report = _report(inputs, at_limit)
    status = report["legacy_contract_status"]
    assert type(status) is dict
    assert status["raw_parse_status"] == "LEGACY_SCHEMA_FAILURE"


def test_settings_canonical_ceiling_is_an_h2_input_contract_failure(
    inputs: _Inputs,
) -> None:
    kwargs = _kwargs(inputs)
    kwargs["legacy_strategy_settings"] = {
        "pad": "x" * (MAX_LEGACY_STRATEGY_SETTINGS_CANONICAL_BYTES + 1)
    }
    with pytest.raises(MmiLegacyStep1ComparisonReportV1Error) as excinfo:
        build_mmi_legacy_step1_comparison_report_v1(
            legacy_research_raw_bytes=VALID_LEGACY.read_bytes(),
            **kwargs,
        )
    assert excinfo.value.code == "MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID"


def test_decoding_matches_the_legacy_path_for_bom_and_mixed_newlines(
    inputs: _Inputs,
    tmp_path: Path,
) -> None:
    body = json.dumps(
        json.loads(VALID_LEGACY.read_text(encoding="utf-8")),
        ensure_ascii=False,
        indent=2,
    )
    lf_bytes = body.encode("utf-8")
    mixed = body.replace("\n", "\r\n", 1).replace("\n", "\r", 1)
    bom_mixed_bytes = b"\xef\xbb\xbf" + mixed.encode("utf-8")

    path = tmp_path / "legacy_raw_output.txt"
    path.write_bytes(bom_mixed_bytes)
    legacy_text = path.read_text(encoding="utf-8")
    assert legacy_text.startswith("﻿")
    assert "\r" not in legacy_text

    lf_report = _report(inputs, lf_bytes)
    bom_report = _report(inputs, bom_mixed_bytes)
    lf_provenance = lf_report["provenance"]
    bom_provenance = bom_report["provenance"]
    assert type(lf_provenance) is dict and type(bom_provenance) is dict
    assert (
        lf_provenance["legacy_raw_bytes_sha256"]
        != bom_provenance["legacy_raw_bytes_sha256"]
    )
    assert (
        lf_provenance["legacy_parsed_payload_canonical_sha256"]
        == bom_provenance["legacy_parsed_payload_canonical_sha256"]
    )
    assert lf_report["instrument_comparison"] == (
        bom_report["instrument_comparison"]
    )


def test_identity_authentication_and_non_expected_detection(
    inputs: _Inputs,
) -> None:
    legacy_bytes = VALID_LEGACY.read_bytes()
    report = _report(inputs, legacy_bytes)

    tampered = deepcopy(report)
    comparison = tampered["instrument_comparison"]
    assert type(comparison) is dict
    comparison["membership_equal"] = True
    with pytest.raises(MmiLegacyStep1ComparisonReportV1Error) as mismatch:
        validate_mmi_legacy_step1_comparison_report_v1(
            value=tampered,
            legacy_research_raw_bytes=legacy_bytes,
            **_kwargs(inputs),
        )
    assert mismatch.value.code == (
        "MMI_LEGACY_STEP1_COMPARISON_IDENTITY_MISMATCHED"
    )

    other_bytes = STRUCTURED_LEGACY.read_bytes()
    with pytest.raises(MmiLegacyStep1ComparisonReportV1Error) as non_expected:
        validate_mmi_legacy_step1_comparison_report_v1(
            value=report,
            legacy_research_raw_bytes=other_bytes,
            **_kwargs(inputs),
        )
    assert non_expected.value.code == "MMI_LEGACY_STEP1_COMPARISON_NON_EXPECTED"


def test_unsupported_contract_version_is_reported_before_schema_failure(
    inputs: _Inputs,
) -> None:
    legacy_bytes = VALID_LEGACY.read_bytes()
    report = deepcopy(_report(inputs, legacy_bytes))
    report["comparison_contract_version"] = "mmi_legacy_step1_comparison_v0"
    with pytest.raises(MmiLegacyStep1ComparisonReportV1Error) as excinfo:
        validate_mmi_legacy_step1_comparison_report_v1(
            value=report,
            legacy_research_raw_bytes=legacy_bytes,
            **_kwargs(inputs),
        )
    assert excinfo.value.code == (
        "MMI_LEGACY_STEP1_COMPARISON_CONTRACT_UNSUPPORTED"
    )


def test_unknown_report_field_is_rejected_by_the_closed_contract(
    inputs: _Inputs,
) -> None:
    legacy_bytes = VALID_LEGACY.read_bytes()
    report = deepcopy(_report(inputs, legacy_bytes))
    report["new_buy_permission"] = True
    with pytest.raises(MmiLegacyStep1ComparisonReportV1Error) as excinfo:
        validate_mmi_legacy_step1_comparison_report_v1(
            value=report,
            legacy_research_raw_bytes=legacy_bytes,
            **_kwargs(inputs),
        )
    assert excinfo.value.code == "MMI_LEGACY_STEP1_COMPARISON_SCHEMA_INVALID"


def _large_settings() -> dict[str, object]:
    core = ["C00000000000001"]
    satellite = ["S00000000000001"]
    extended = [f"E{index:014d}" for index in range(1, 255)]
    return {
        "as_of": "2026-07-30",
        "run_timestamp_et": "2026-07-30 10:00 ET",
        "benchmark": core[0],
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.00,
        "relative_rotation_enabled": True,
        "relative_rotation_guardrails": {
            "require_same_role_for_rotation": True,
            "min_score_gap_to_rotate": 2,
            "do_not_rotate_if_current_holding_still_role_valid": True,
            "no_rotation_on_one_rank_change_only": True,
        },
        "core_universe": core,
        "satellite_universe": satellite,
        "user_approved_extended_etf_static_list": extended,
        "user_approved_extended_etf_theme_map": {
            ticker: {"theme_bucket": f"theme_{index:04d}"}
            for index, ticker in enumerate(extended, start=1)
        },
        "active_shortlist_size_rule": {
            "benchmark_carrier": 1,
            "diversified_core_buffer_max": 1,
            "sector_alpha_tilt_max": 1,
            "extended_etf_minority_sleeve_max": 2,
        },
        "max_new_tickers_per_week": {
            "base_universe_new_tickers_per_week": 0,
            "extended_etf_sleeve_new_tickers_per_week": 2,
        },
        "extended_etf_constraints": {
            "sleeve_budget_cap_pct_of_total_open_orders": 0.35,
            "single_extended_etf_budget_cap_pct_of_total_open_orders": 0.20,
            "activation_minimum_effective_budget_pct_of_total_open_orders": (
                0.04
            ),
            "max_same_theme_extended_etf_count": 1,
            "max_same_theme_budget_pct_of_total_open_orders": 0.25,
            "require_distinct_theme_buckets_when_multiple_extended_etfs": (
                True
            ),
        },
    }


def _large_inputs(root: Path) -> _Inputs:
    settings = _large_settings()
    raw = yaml.safe_dump(
        settings,
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    path = root / "inputs/current/strategy_settings.yaml"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    capture = _capture_mmi_source_at_root(
        root,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert capture.valid, capture.reason_codes
    assert capture.source is not None
    return _inputs_from_source(source=capture.source, settings=settings)


def _large_legacy_bytes() -> bytes:
    tickers = [f"L{index:014d}" for index in range(1, 257)]
    payload = {
        "schema_version": "1.0",
        "as_of": "2026-07-30",
        "buy_universe_scorecard": [
            {"ticker": ticker, "role_layer": "diversified_core_buffer"}
            for ticker in tickers
        ],
        "validation_summary": {
            "passed": False,
            "fail_reasons": [],
            "auto_fixes_applied": [],
        },
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_large_valid_public_comparison_stays_within_the_report_ceiling(
    tmp_path: Path,
) -> None:
    inputs = _large_inputs(tmp_path)
    legacy_bytes = _large_legacy_bytes()
    report = build_mmi_legacy_step1_comparison_report_v1(
        legacy_research_raw_bytes=legacy_bytes,
        **_kwargs(inputs),
    )
    comparison = report["instrument_comparison"]
    assert type(comparison) is dict
    assert comparison["h1_instrument_count"] == 256
    assert comparison["legacy_instrument_count"] == 256
    assert comparison["shared_instrument_count"] == 0
    assert comparison["membership_equal"] is False
    assert len(comparison["h1_only_tickers"]) == 256
    assert len(comparison["legacy_only_tickers"]) == 256
    assert len(_canonical(report)) < 65_536
    validate_artifact_schema(report, schema_name=SCHEMA_NAME)
    assert validate_mmi_legacy_step1_comparison_report_v1(
        value=report,
        legacy_research_raw_bytes=legacy_bytes,
        **_kwargs(inputs),
    ) == report


def test_oversized_legacy_instrument_view_fails_without_truncation(
    inputs: _Inputs,
) -> None:
    def _oversize(payload: dict[str, object]) -> None:
        payload["buy_universe_scorecard"] = [
            {"ticker": f"L{index:06d}", "role_layer": "diversified_core_buffer"}
            for index in range(257)
        ]

    with pytest.raises(MmiLegacyStep1ComparisonReportV1Error) as excinfo:
        _report(inputs, _mutated_valid_legacy(_oversize))
    assert excinfo.value.code == (
        "MMI_LEGACY_STEP1_COMPARISON_RESOURCE_LIMIT_EXCEEDED"
    )


def test_oversized_legacy_role_vocabulary_fails_without_truncation(
    inputs: _Inputs,
) -> None:
    def _roles(payload: dict[str, object]) -> None:
        payload["buy_universe_scorecard"] = [
            {"ticker": f"L{index:06d}", "role_layer": f"role_{index:04d}"}
            for index in range(17)
        ]

    with pytest.raises(MmiLegacyStep1ComparisonReportV1Error) as excinfo:
        _report(inputs, _mutated_valid_legacy(_roles))
    assert excinfo.value.code == (
        "MMI_LEGACY_STEP1_COMPARISON_RESOURCE_LIMIT_EXCEEDED"
    )


def test_report_carries_no_prose_from_either_side(inputs: _Inputs) -> None:
    report = _report(inputs, VALID_LEGACY.read_bytes())
    serialized = _canonical(report).decode("utf-8")
    candidate_summary = inputs.candidate["summary"]
    assert type(candidate_summary) is dict
    for prose in (
        candidate_summary["text"],
        "Qualitative evidence remains report-only.",
        "Benchmark growth exposure remains useful as a long-term reference.",
        "Deterministic evidence observation.",
        "Template2 buy-side must restrict to allowed_buy_tickers only.",
    ):
        assert type(prose) is str
        assert prose not in serialized


def test_sources_are_test_owned_and_live_inputs_are_unreachable(
    checkout: hermetic.HermeticSourceCheckout,
    inputs: _Inputs,
) -> None:
    hermetic.assert_checkout_resolves_both_locators(checkout.root)
    hermetic.assert_test_owned_source(
        inputs.policy_source,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=checkout.strategy_settings_raw,
    )
    assert inputs.policy_source.source_record[
        "repository_relative_locator"
    ] == hermetic.STRATEGY_SETTINGS_LOCATOR
    assert inputs.settings == yaml.safe_load(
        checkout.strategy_settings_raw.decode("utf-8")
    )
    hermetic.assert_live_operational_inputs_are_unreachable()
