"""Authority-bearing tests for the pure V1 BUY compiler dry-run."""

from __future__ import annotations

from dataclasses import fields, replace
from decimal import Decimal
from pathlib import Path

import pytest

import _mmi_hermetic_source_checkout as hermetic
from investment_orchestrator.observability import (
    report_only_increment_capacity as increment_capacity,
)
from investment_orchestrator.state import research_availability
from investment_orchestrator.workflow import (
    h1_v1_buy_compiler_dry_run as dry_run,
    step3_h1_v1_proposal as proposal,
)
from test_report_only_holdings_exposure import (
    _observe as _observe_holdings_exposure,
    _session as _completed_session,
)
from test_step3_h1_v1_proposal import (
    _Case,
    _build_case,
    _install_case_inputs,
)


def _strategy_settings() -> dict[str, object]:
    settings = hermetic.strategy_settings_mapping()
    settings["buy_order_template_map"] = {
        "benchmark_carrier_core": {
            "ordered_step_names": ["starter", "L1", "L2", "L3", "L4"],
            "step_offsets_vs_anchor": {
                "starter": "-0.010",
                "L1": "-0.030",
                "L2": "-0.060",
                "L3": "-0.095",
                "L4": "-0.130",
            },
            "step_budget_weights": {
                "starter": "0.40",
                "L1": "0.25",
                "L2": "0.18",
                "L3": "0.12",
                "L4": "0.05",
            },
            "default_plan_type": "new_limit_ladder",
            "default_time_in_force": "DAY",
        },
        "diversified_core_buffer": {
            "ordered_step_names": ["L1", "L2", "L3", "L4"],
            "step_offsets_vs_anchor": {
                "L1": "-0.025",
                "L2": "-0.055",
                "L3": "-0.085",
                "L4": "-0.120",
            },
            "step_budget_weights": {
                "L1": "0.35",
                "L2": "0.30",
                "L3": "0.22",
                "L4": "0.13",
            },
            "default_plan_type": "new_limit_ladder",
            "default_time_in_force": "DAY",
        },
        "sector_alpha_tilt": {
            "ordered_step_names": ["L1", "L2", "L3", "L4"],
            "step_offsets_vs_anchor": {
                "L1": "-0.040",
                "L2": "-0.085",
                "L3": "-0.135",
                "L4": "-0.180",
            },
            "step_budget_weights": {
                "L1": "0.28",
                "L2": "0.27",
                "L3": "0.25",
                "L4": "0.20",
            },
            "default_plan_type": "new_limit_ladder",
            "default_time_in_force": "DAY",
        },
    }
    return settings


def _current_capture_case(case: _Case) -> _Case:
    exposure = case.exposure_result.projection
    increment = case.increment_result.projection
    assert exposure is not None
    assert increment is not None
    current_exposure = replace(
        exposure,
        capture_source_kind="EXTERNAL_MARKET_DATA_CAPTURE",
        capture_provider_id="YAHOO_FINANCE",
        calendar_id="US_EQUITY_REGULAR",
        freshness_status="FRESH",
    )
    current_increment = replace(
        increment,
        calendar_id=current_exposure.calendar_id,
        freshness_status=current_exposure.freshness_status,
    )
    return replace(
        case,
        exposure_result=replace(case.exposure_result, projection=current_exposure),
        increment_result=replace(
            case.increment_result,
            projection=current_increment,
        ),
    )


def _case(
    tmp_path: Path,
    *,
    positions: tuple[tuple[str, str], ...],
    commitments: tuple[tuple[str, str], ...] = (),
    x: str = "1000",
    r_cap: str = "1000",
    evidence_groups: tuple[tuple[str, ...], ...] = (("QQQ",),),
) -> _Case:
    return _current_capture_case(
        _build_case(
            tmp_path,
            positions=positions,
            commitments=commitments,
            x=x,
            r_cap=r_cap,
            evidence_groups=evidence_groups,
            strategy_settings=_strategy_settings(),
        )
    )


def _evaluate(
    case: _Case,
    monkeypatch: pytest.MonkeyPatch,
) -> dry_run.H1V1BuyCompilerDryRunResult:
    h1_calls = _install_case_inputs(case, monkeypatch)
    result = dry_run.evaluate_h1_v1_buy_compiler_dry_run()
    assert h1_calls == [None]
    return result


def test_core_dry_run_is_exact_one_generation_non_authoritative_and_write_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(
        tmp_path,
        positions=(("VOO", "600"), ("QQQ", "100")),
        commitments=(("QQQ", "100"),),
        x="1000",
        r_cap="700",
        evidence_groups=(("VOO",), ("QQQ",)),
    )
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    result = _evaluate(case, monkeypatch)

    assert result.terminal_outcome == "POSITIVE_BUY_DRY_RUN"
    assert result.selected_ticker == "QQQ"
    assert result.deterministic_role == "CORE"
    assert result.pricing_role == "benchmark_carrier_core"
    assert result.validated_mark == "100"
    assert result.target_increment == "600"
    assert [leg.step_name for leg in result.candidate_legs] == [
        "starter",
        "L1",
        "L2",
    ]
    assert [leg.rounded_limit_price for leg in result.candidate_legs] == [
        "99",
        "97",
        "94",
    ]
    assert [leg.whole_share_quantity for leg in result.candidate_legs] == [
        2,
        1,
        1,
    ]
    assert all(
        type(leg.whole_share_quantity) is int
        for leg in result.candidate_legs
    )
    assert [leg.candidate_notional for leg in result.candidate_legs] == [
        "198",
        "97",
        "94",
    ]
    assert result.total_new_candidate_notional == "389"
    assert result.postcompile_total_unfilled_buy_commitment == "489"
    assert Decimal(result.postcompile_total_unfilled_buy_commitment) <= Decimal(
        "700"
    )
    assert result.postcompile_alpha_exposure == "0"
    assert result.postcompile_core_exposure == "1189"
    assert result.dry_run_only is True
    assert result.authority_effect == "NONE"
    assert result.not_authorization is True
    assert result.order_compilation_allowed is False
    assert {
        "order_id",
        "broker_order_id",
        "execution_id",
        "order_ready",
        "compiler_ready",
        "publish_ready",
        "execution_ready",
    }.isdisjoint(field.name for field in fields(result))
    assert {row.ticker for row in result.ticker_exposures} == {"QQQ", "VOO"}
    assert all(
        row.new_candidate_notional == "0"
        for row in result.ticker_exposures
        if row.ticker != "QQQ"
    )
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before
    assert not any("order" in path.name.casefold() for path in tmp_path.rglob("*"))


def test_round_half_up_precedes_share_flooring_and_zero_legs_are_not_redistributed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(
        tmp_path,
        positions=(("QQQ", "100.5"), ("VOO", "899.5")),
        x="497.4875",
        r_cap="497.4875",
        evidence_groups=(("QQQ",),),
    )

    result = _evaluate(case, monkeypatch)

    starter = result.candidate_legs[0]
    assert starter.step_name == "starter"
    assert starter.rounded_limit_price == "99.5"
    assert starter.whole_share_quantity == 1
    assert starter.candidate_notional == "99.5"
    assert [leg.step_name for leg in result.candidate_legs] == ["starter", "L1"]
    assert result.total_new_candidate_notional == "196.99"
    assert Decimal(result.total_new_candidate_notional) < Decimal(
        result.target_increment
    )


def test_maximum_scale_mark_compiles_without_rendering_the_unrounded_price(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(
        tmp_path,
        positions=(
            ("QQQ", "100.000000000000000000000001"),
            ("VOO", "899.999999999999999999999999"),
        ),
        x="1000",
        r_cap="1000",
        evidence_groups=(("QQQ",),),
    )
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    result = _evaluate(case, monkeypatch)

    assert result.terminal_outcome == "POSITIVE_BUY_DRY_RUN"
    assert result.validated_mark == "100.000000000000000000000001"
    starter = result.candidate_legs[0]
    assert "raw_limit_price" not in {field.name for field in fields(starter)}
    assert starter.rounded_limit_price == "99"
    assert starter.whole_share_quantity == 4
    assert starter.candidate_notional == "396"
    assert result.total_new_candidate_notional == "774.5"
    assert Decimal(result.total_new_candidate_notional) <= Decimal(
        result.target_increment
    )
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before


def test_all_zero_quantities_are_hold_without_candidate_legs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(
        tmp_path,
        positions=(("QQQ", "100"),),
        x="1",
        r_cap="1",
    )

    result = _evaluate(case, monkeypatch)

    assert result.terminal_outcome == "HOLD"
    assert result.reason_code == "NO_WHOLE_SHARE_FEASIBILITY"
    assert result.candidate_legs == ()
    assert result.total_new_candidate_notional == "0"
    assert result.postcompile_total_unfilled_buy_commitment == "0"


def test_satellite_dry_run_respects_target_and_postcompile_alpha_core_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(
        tmp_path,
        positions=(("QQQ", "1050"), ("SMH", "100"), ("QUAL", "100")),
        x="1000",
        r_cap="1000",
        evidence_groups=(("SMH",),),
    )

    result = _evaluate(case, monkeypatch)

    assert result.selected_ticker == "SMH"
    assert result.pricing_role == "sector_alpha_tilt"
    assert result.target_increment == "850"
    assert result.total_new_candidate_notional == "712"
    assert result.postcompile_total_unfilled_buy_commitment == "712"
    assert result.postcompile_alpha_exposure == "912"
    assert result.postcompile_core_exposure == "1050"
    assert Decimal(result.total_new_candidate_notional) <= Decimal(
        result.target_increment
    )
    assert {row.ticker for row in result.ticker_exposures} == {
        "QQQ",
        "QUAL",
        "SMH",
    }


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("generation", "V1_BUY_DRY_RUN_VALUATION_GENERATION_MISMATCH"),
        ("calendar", "V1_BUY_DRY_RUN_VALUATION_GENERATION_MISMATCH"),
    ),
)
def test_valuation_generation_or_freshness_failure_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected_code: str,
) -> None:
    case = _case(tmp_path, positions=(("QQQ", "100"),), x="50", r_cap="50")
    h1_calls = _install_case_inputs(case, monkeypatch)
    evaluation = proposal.evaluate_h1_v1_proposal_generation()
    assert h1_calls == [None]
    assert evaluation.exposure is not None
    if mutation == "generation":
        changed_exposure = replace(
            evaluation.exposure,
            capture_artifact_sha256="9" * 64,
        )
        changed_proposal = evaluation.proposal
    else:
        changed_exposure = replace(
            evaluation.exposure,
            calendar_schedule_sha256="8" * 64,
        )
        changed_proposal = evaluation.proposal
    changed = replace(
        evaluation,
        proposal=changed_proposal,
        exposure=changed_exposure,
    )
    monkeypatch.setattr(
        dry_run._proposal,
        "evaluate_h1_v1_proposal_generation",
        lambda: changed,
    )

    with pytest.raises(dry_run.V1BuyCompilerDryRunError) as exc_info:
        dry_run.evaluate_h1_v1_buy_compiler_dry_run()
    assert exc_info.value.code == expected_code
    assert exc_info.value.failure_class == "provenance/currentness"


def test_real_stale_valuation_owner_path_preserves_currentness_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_exposure = _observe_holdings_exposure(
        tmp_path / "stale_owner",
        monkeypatch,
        completed_session=_completed_session(
            session_date="2026-08-11",
            official_close_timestamp_et="2026-08-11T16:00:00-04:00",
            trusted_evaluation_timestamp_utc="2026-08-11T20:00:00Z",
        ),
    )
    assert stale_exposure.reason_codes == (
        "US_EQUITY_SESSION_MARK_DATE_STALE",
    )
    assert stale_exposure.projection is None
    case = _case(
        tmp_path / "proposal_case",
        positions=(("QQQ", "100"),),
        x="50",
        r_cap="50",
    )
    increment = case.increment_result.projection
    assert increment is not None
    stale_increment = increment_capacity._project_increment_capacity_from_exposure(
        r_source=increment.increment_fraction_source,
        exposure_result=stale_exposure,
    )
    stale_case = replace(
        case,
        exposure_result=stale_exposure,
        increment_result=stale_increment,
    )
    h1_calls = _install_case_inputs(stale_case, monkeypatch)
    observed_terminal_results: list[str] = []
    recognize = proposal._recognize_h1_v1_proposal_state

    def capture_terminal_result(
        result: dict[str, object],
    ) -> proposal.H1V1ProposalStateRecognition | None:
        terminal_result = result["terminal_result"]
        assert isinstance(terminal_result, str)
        observed_terminal_results.append(terminal_result)
        return recognize(result)

    monkeypatch.setattr(
        dry_run._proposal,
        "_recognize_h1_v1_proposal_state",
        capture_terminal_result,
    )
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(dry_run.V1BuyCompilerDryRunError) as exc_info:
        dry_run.evaluate_h1_v1_buy_compiler_dry_run()

    assert h1_calls == [None]
    assert observed_terminal_results == [proposal.TERMINAL_NO_TRADE]
    assert exc_info.value.code == "US_EQUITY_SESSION_MARK_DATE_STALE"
    assert exc_info.value.failure_class == "provenance/currentness"
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before


def test_pre_p1_projection_generation_mismatch_preserves_provenance_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(
        tmp_path,
        positions=(("QQQ", "100"),),
        x="50",
        r_cap="50",
    )
    increment = case.increment_result.projection
    assert increment is not None
    mismatched_increment = replace(
        case.increment_result,
        projection=replace(
            increment,
            capture_artifact_sha256="9" * 64,
        ),
    )
    mismatched_case = replace(case, increment_result=mismatched_increment)
    h1_calls = _install_case_inputs(mismatched_case, monkeypatch)

    with pytest.raises(dry_run.V1BuyCompilerDryRunError) as exc_info:
        dry_run.evaluate_h1_v1_buy_compiler_dry_run()

    assert h1_calls == [None]
    assert exc_info.value.code == "INPUT_GENERATION_MISMATCH"
    assert exc_info.value.failure_class == "provenance/currentness"


def test_complete_p2_state_has_exact_order_compilation_permission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, positions=(("QQQ", "100"),), x="50", r_cap="50")
    h1_calls = _install_case_inputs(case, monkeypatch)
    evaluation = proposal.evaluate_h1_v1_proposal_generation()
    assert h1_calls == [None]
    state = proposal._recognize_h1_v1_proposal_state(evaluation.proposal)

    assert state is not None
    assert state.allowed_actions == (
        "HOLD",
        "NO_TRADE",
        "NEW_BUY",
        "ORDER_COMPILATION",
    )
    assert state.blocked_actions == tuple(
        action
        for action in research_availability.ACTIONS
        if action not in state.allowed_actions
    )
    assert "ORDER_COMPILATION" not in state.blocked_actions
    assert state.order_compilation_allowed is True
    assert state.step3_allowed is False
    assert state.step4_allowed is False
    assert evaluation.proposal["new_buy_permission"] is False
    assert evaluation.proposal["order_compilation_allowed"] is False


def test_malformed_static_ladder_fails_closed_without_a_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _strategy_settings()
    template_map = settings["buy_order_template_map"]
    assert isinstance(template_map, dict)
    benchmark_ladder = template_map["benchmark_carrier_core"]
    assert isinstance(benchmark_ladder, dict)
    weights = benchmark_ladder["step_budget_weights"]
    assert isinstance(weights, dict)
    weights["starter"] = "0.41"
    case = _current_capture_case(
        _build_case(
            tmp_path,
            positions=(("QQQ", "100"),),
            x="50",
            r_cap="50",
            evidence_groups=(("QQQ",),),
            strategy_settings=settings,
        )
    )
    _install_case_inputs(case, monkeypatch)

    with pytest.raises(dry_run.V1BuyCompilerDryRunError) as exc_info:
        dry_run.evaluate_h1_v1_buy_compiler_dry_run()
    assert exc_info.value.code == "V1_BUY_DRY_RUN_LADDER_WEIGHT_INVALID"
    assert exc_info.value.failure_class == "compiler/contract"


def test_forged_target_cannot_cross_complete_p2_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, positions=(("QQQ", "100"),), x="50", r_cap="50")
    h1_calls = _install_case_inputs(case, monkeypatch)
    evaluation = proposal.evaluate_h1_v1_proposal_generation()
    assert h1_calls == [None]
    forged_proposal = dict(evaluation.proposal)
    forged_proposal["target_increment"] = "1"
    monkeypatch.setattr(
        dry_run._proposal,
        "evaluate_h1_v1_proposal_generation",
        lambda: replace(evaluation, proposal=forged_proposal),
    )

    with pytest.raises(proposal.V1ProposalStateRecognitionError):
        dry_run.evaluate_h1_v1_buy_compiler_dry_run()
