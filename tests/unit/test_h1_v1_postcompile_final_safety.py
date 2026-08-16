"""Authority-bearing tests for invocation-local V1 postcompile safety."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from investment_orchestrator.market import us_equity_session_calendar as calendar
from investment_orchestrator.observability import (
    report_only_increment_capacity as increment_capacity,
)
from investment_orchestrator.state import research_availability
from investment_orchestrator.workflow import (
    h1_v1_buy_compiler_dry_run as dry_run,
    h1_v1_postcompile_final_safety as final_safety,
    step3_h1_v1_proposal as proposal,
)
from test_h1_v1_buy_compiler_dry_run import _strategy_settings
from test_report_only_holdings_exposure import (
    _observe as _observe_holdings_exposure,
    _session as _completed_session,
)
from test_step3_h1_v1_proposal import (
    _Case,
    _build_case,
    _install_case_inputs,
)


def _fresh_case(
    tmp_path: Path,
    *,
    positions: tuple[tuple[str, str], ...],
    commitments: tuple[tuple[str, str], ...] = (),
    x: str = "1000",
    r_cap: str = "1000",
    evidence_groups: tuple[tuple[str, ...], ...] = (("QQQ",),),
) -> _Case:
    case = _build_case(
        tmp_path,
        positions=positions,
        commitments=commitments,
        x=x,
        r_cap=r_cap,
        evidence_groups=evidence_groups,
        strategy_settings=_strategy_settings(),
    )
    freshness = calendar.assess_manual_mark_freshness(
        mark_as_of_date=date(2026, 8, 14),
        evaluation_time_utc=datetime(2026, 8, 15, 1, tzinfo=UTC),
    )
    completed = freshness.completed_session
    assert freshness.status is calendar.MarkFreshnessStatus.FRESH
    assert completed is not None
    exposure = case.exposure_result.projection
    increment = case.increment_result.projection
    assert exposure is not None
    assert increment is not None
    current_exposure = replace(
        exposure,
        capture_source_kind="EXTERNAL_MARKET_DATA_CAPTURE",
        capture_provider_id="YAHOO_FINANCE",
        capture_session_date=completed.session_date,
        capture_trusted_evaluation_timestamp_utc=(
            completed.trusted_evaluation_timestamp_utc
        ),
        mark_as_of_date=completed.session_date,
        calendar_id=completed.calendar_id,
        calendar_schedule_sha256=completed.calendar_schedule_sha256,
        calendar_coverage_start_date=completed.coverage_start_date,
        calendar_coverage_end_date=completed.coverage_end_date,
        trusted_evaluation_timestamp_utc=(
            completed.trusted_evaluation_timestamp_utc
        ),
        latest_completed_session_date=completed.session_date,
        latest_completed_session_close_timestamp_et=(
            completed.official_close_timestamp_et
        ),
        freshness_status=calendar.MarkFreshnessStatus.FRESH.value,
    )
    current_increment = replace(
        increment,
        capture_session_date=current_exposure.capture_session_date,
        calendar_id=current_exposure.calendar_id,
        calendar_schedule_sha256=current_exposure.calendar_schedule_sha256,
        latest_completed_session_date=(
            current_exposure.latest_completed_session_date
        ),
        freshness_status=current_exposure.freshness_status,
    )
    return replace(
        case,
        exposure_result=replace(
            case.exposure_result,
            projection=current_exposure,
        ),
        increment_result=replace(
            case.increment_result,
            projection=current_increment,
        ),
    )


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _generation_and_candidate(
    case: _Case,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    proposal.H1V1ProposalEvaluation,
    dry_run.H1V1BuyCompilerDryRunResult,
]:
    h1_calls = _install_case_inputs(case, monkeypatch)
    generation = proposal.evaluate_h1_v1_proposal_generation()
    assert h1_calls == [None]
    candidate = dry_run._evaluate_h1_v1_buy_compiler_dry_run_from_generation(
        generation
    )
    return generation, candidate


def test_core_positive_reconstructs_every_fact_from_one_generation_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("VOO", "600"), ("QQQ", "100")),
        commitments=(("QQQ", "100"),),
        x="1000",
        r_cap="700",
        evidence_groups=(("VOO",), ("QQQ",)),
    )
    h1_calls = _install_case_inputs(case, monkeypatch)
    before = _files(tmp_path)
    compiler_candidates: list[dry_run.H1V1BuyCompilerDryRunResult] = []
    freshness_calls: list[tuple[date, datetime]] = []
    compile_from_generation = (
        dry_run._evaluate_h1_v1_buy_compiler_dry_run_from_generation
    )
    assess_freshness = calendar.assess_manual_mark_freshness

    def capture_candidate(
        generation: proposal.H1V1ProposalEvaluation,
    ) -> dry_run.H1V1BuyCompilerDryRunResult:
        candidate = compile_from_generation(generation)
        compiler_candidates.append(candidate)
        return candidate

    def capture_freshness(
        *,
        mark_as_of_date: date,
        evaluation_time_utc: datetime,
    ) -> calendar.MarkFreshnessResult:
        freshness_calls.append((mark_as_of_date, evaluation_time_utc))
        return assess_freshness(
            mark_as_of_date=mark_as_of_date,
            evaluation_time_utc=evaluation_time_utc,
        )

    monkeypatch.setattr(
        dry_run,
        "_evaluate_h1_v1_buy_compiler_dry_run_from_generation",
        capture_candidate,
    )
    monkeypatch.setattr(
        final_safety._calendar,
        "assess_manual_mark_freshness",
        capture_freshness,
    )
    monkeypatch.setattr(
        dry_run,
        "evaluate_h1_v1_buy_compiler_dry_run",
        lambda: pytest.fail("P6 must not call the public P4 evaluator"),
    )

    result = final_safety.evaluate_h1_v1_postcompile_final_safety()

    assert h1_calls == [None]
    assert len(compiler_candidates) == 1
    assert len(freshness_calls) == 1
    p4_candidate = compiler_candidates[0]
    assert p4_candidate.dry_run_only is True
    assert p4_candidate.authority_effect == "NONE"
    assert p4_candidate.not_authorization is True
    assert p4_candidate.order_compilation_allowed is False
    assert result.terminal_outcome == "POSTCOMPILE_CANDIDATE_VALID"
    assert result.reason_code == "POSTCOMPILE_CANDIDATE_VALID"
    assert result.state == "H1_V1_DETERMINISTIC_PROPOSAL_READY"
    assert result.selected_ticker == "QQQ"
    assert result.deterministic_role == "CORE"
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
    assert [leg.candidate_notional for leg in result.candidate_legs] == [
        "198",
        "97",
        "94",
    ]
    assert result.target_increment == "600"
    assert result.total_new_candidate_notional == "389"
    assert result.postcompile_total_unfilled_buy_commitment == "489"
    assert result.postcompile_alpha_exposure == "0"
    assert result.postcompile_core_exposure == "1189"
    assert result.authority_effect == "NONE"
    assert result.not_authorization is True
    assert dict(result.source_bindings)["valuation_capture_sha256"] == "c" * 64
    assert {
        "final_safety_passed",
        "safe_to_execute",
        "execution_ready",
        "order_ready",
        "publication_ready",
        "broker_ready",
        "order_id",
        "client_order_id",
    }.isdisjoint(field.name for field in fields(result))
    assert _files(tmp_path) == before
    assert not any("order" in path.name.casefold() for path in tmp_path.rglob("*"))


def test_satellite_positive_includes_approved_extended_in_independent_az(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("QQQ", "1050"), ("SMH", "100"), ("QUAL", "100")),
        x="1000",
        r_cap="1000",
        evidence_groups=(("SMH",),),
    )
    h1_calls = _install_case_inputs(case, monkeypatch)

    result = final_safety.evaluate_h1_v1_postcompile_final_safety()

    assert h1_calls == [None]
    assert result.terminal_outcome == "POSTCOMPILE_CANDIDATE_VALID"
    assert result.selected_ticker == "SMH"
    assert result.deterministic_role == "SATELLITE"
    assert result.target_increment == "850"
    assert result.total_new_candidate_notional == "712"
    assert result.postcompile_total_unfilled_buy_commitment == "712"
    assert result.postcompile_alpha_exposure == "912"
    assert result.postcompile_core_exposure == "1050"
    rows = {row.ticker: row for row in result.ticker_exposures}
    assert rows["QUAL"].role == "APPROVED_EXTENDED"
    assert rows["QUAL"].final_projected_exposure == "100"
    assert Decimal(result.postcompile_alpha_exposure) <= Decimal(
        result.postcompile_core_exposure
    )


def test_p1_hold_bypasses_p4_and_returns_no_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("QQQ", "1000"),),
        evidence_groups=(("VOO",),),
    )
    h1_calls = _install_case_inputs(case, monkeypatch)
    monkeypatch.setattr(
        dry_run,
        "_evaluate_h1_v1_buy_compiler_dry_run_from_generation",
        lambda _generation: pytest.fail("P1 HOLD must not compile"),
    )

    result = final_safety.evaluate_h1_v1_postcompile_final_safety()

    assert h1_calls == [None]
    assert result.terminal_outcome == "HOLD"
    assert result.reason_code == "NO_INCREMENT_ELIGIBLE_TICKER"
    assert result.state is None
    assert result.candidate_legs == ()
    assert result.total_new_candidate_notional is None


def test_real_stale_owner_path_is_no_trade_and_never_compiles(
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
    assert stale_exposure.projection is None
    assert stale_exposure.reason_codes == (
        "US_EQUITY_SESSION_MARK_DATE_STALE",
    )
    case = _fresh_case(
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
    before = _files(tmp_path)
    monkeypatch.setattr(
        dry_run,
        "_evaluate_h1_v1_buy_compiler_dry_run_from_generation",
        lambda _generation: pytest.fail("stale valuation must not compile"),
    )

    result = final_safety.evaluate_h1_v1_postcompile_final_safety()

    assert h1_calls == [None]
    assert result.terminal_outcome == "NO_TRADE"
    assert result.reason_code == "US_EQUITY_SESSION_MARK_DATE_STALE"
    assert result.state is None
    assert result.candidate_legs == ()
    assert _files(tmp_path) == before


def test_pre_p1_generation_mismatch_is_no_trade_and_never_compiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("QQQ", "100"),),
        x="50",
        r_cap="50",
    )
    increment = case.increment_result.projection
    assert increment is not None
    mismatched_case = replace(
        case,
        increment_result=replace(
            case.increment_result,
            projection=replace(
                increment,
                capture_artifact_sha256="9" * 64,
            ),
        ),
    )
    h1_calls = _install_case_inputs(mismatched_case, monkeypatch)
    monkeypatch.setattr(
        dry_run,
        "_evaluate_h1_v1_buy_compiler_dry_run_from_generation",
        lambda _generation: pytest.fail("mismatched generation must not compile"),
    )

    result = final_safety.evaluate_h1_v1_postcompile_final_safety()

    assert h1_calls == [None]
    assert result.terminal_outcome == "NO_TRADE"
    assert result.reason_code == "INPUT_GENERATION_MISMATCH"
    assert result.state is None


def test_exact_permission_contract_is_required_before_p4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("QQQ", "100"),),
        x="50",
        r_cap="50",
    )
    h1_calls = _install_case_inputs(case, monkeypatch)
    generation = proposal.evaluate_h1_v1_proposal_generation()
    assert h1_calls == [None]
    recognized = proposal._recognize_h1_v1_proposal_state(generation.proposal)
    assert recognized is not None
    monkeypatch.setattr(
        final_safety._proposal,
        "_recognize_h1_v1_proposal_state",
        lambda _proposal: replace(
            recognized,
            allowed_actions=("HOLD", "NO_TRADE", "NEW_BUY"),
        ),
    )
    monkeypatch.setattr(
        dry_run,
        "_evaluate_h1_v1_buy_compiler_dry_run_from_generation",
        lambda _generation: pytest.fail("permission mismatch must not compile"),
    )

    with pytest.raises(final_safety.V1PostcompileFinalSafetyError) as exc_info:
        final_safety._evaluate_h1_v1_postcompile_final_safety_from_generation(
            generation
        )

    assert exc_info.value.code == "V1_POSTCOMPILE_PERMISSION_CONTRACT_INVALID"
    assert exc_info.value.failure_class == "availability/permission"


def test_selected_ticker_and_second_ticker_mutations_fail_candidate_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("VOO", "600"), ("QQQ", "100")),
        commitments=(("QQQ", "100"),),
        x="1000",
        r_cap="700",
        evidence_groups=(("VOO",), ("QQQ",)),
    )
    generation, candidate = _generation_and_candidate(case, monkeypatch)

    with pytest.raises(final_safety.V1PostcompileFinalSafetyError) as exc_info:
        final_safety._verify_h1_v1_postcompile_candidate(
            generation,
            replace(candidate, selected_ticker="VOO"),
        )
    assert exc_info.value.code == (
        "V1_POSTCOMPILE_SELECTED_TICKER_OR_TARGET_MISMATCH"
    )

    rows = list(candidate.ticker_exposures)
    voo_index = next(index for index, row in enumerate(rows) if row.ticker == "VOO")
    rows[voo_index] = replace(rows[voo_index], new_candidate_notional="1")
    with pytest.raises(final_safety.V1PostcompileFinalSafetyError) as exc_info:
        final_safety._verify_h1_v1_postcompile_candidate(
            generation,
            replace(candidate, ticker_exposures=tuple(rows)),
        )
    assert exc_info.value.code == "V1_POSTCOMPILE_CANDIDATE_EXPOSURE_MISMATCH"


def test_round_half_up_price_mismatch_fails_hard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("QQQ", "100.5"), ("VOO", "899.5")),
        x="497.4875",
        r_cap="497.4875",
    )
    generation, candidate = _generation_and_candidate(case, monkeypatch)
    starter = candidate.candidate_legs[0]
    assert starter.rounded_limit_price == "99.5"
    changed = replace(
        candidate,
        candidate_legs=(
            replace(starter, rounded_limit_price="99.49"),
            *candidate.candidate_legs[1:],
        ),
    )

    with pytest.raises(final_safety.V1PostcompileFinalSafetyError) as exc_info:
        final_safety._verify_h1_v1_postcompile_candidate(generation, changed)

    assert exc_info.value.code == "V1_POSTCOMPILE_CANDIDATE_PRICE_MISMATCH"


def test_quantity_and_notional_mismatches_fail_hard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("QQQ", "100"), ("VOO", "900")),
        x="500",
        r_cap="500",
    )
    generation, candidate = _generation_and_candidate(case, monkeypatch)
    first = candidate.candidate_legs[0]
    fractional = replace(
        candidate,
        candidate_legs=(
            replace(first, whole_share_quantity=Decimal("1.5")),
            *candidate.candidate_legs[1:],
        ),
    )
    with pytest.raises(final_safety.V1PostcompileFinalSafetyError) as exc_info:
        final_safety._verify_h1_v1_postcompile_candidate(generation, fractional)
    assert exc_info.value.code == "V1_POSTCOMPILE_CANDIDATE_QUANTITY_MISMATCH"

    wrong_notional = replace(
        candidate,
        candidate_legs=(
            replace(first, candidate_notional="1"),
            *candidate.candidate_legs[1:],
        ),
    )
    with pytest.raises(final_safety.V1PostcompileFinalSafetyError) as exc_info:
        final_safety._verify_h1_v1_postcompile_candidate(
            generation,
            wrong_notional,
        )
    assert exc_info.value.code == "V1_POSTCOMPILE_CANDIDATE_NOTIONAL_MISMATCH"


def test_claimed_n_and_complete_leg_set_mismatches_fail_hard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("QQQ", "100"), ("VOO", "900")),
        x="500",
        r_cap="500",
    )
    generation, candidate = _generation_and_candidate(case, monkeypatch)

    with pytest.raises(final_safety.V1PostcompileFinalSafetyError) as exc_info:
        final_safety._verify_h1_v1_postcompile_candidate(
            generation,
            replace(candidate, total_new_candidate_notional="1"),
        )
    assert exc_info.value.code == "V1_POSTCOMPILE_CANDIDATE_AGGREGATE_MISMATCH"

    with pytest.raises(final_safety.V1PostcompileFinalSafetyError) as exc_info:
        final_safety._verify_h1_v1_postcompile_candidate(
            generation,
            replace(candidate, candidate_legs=candidate.candidate_legs[1:]),
        )
    assert exc_info.value.code == "V1_POSTCOMPILE_CANDIDATE_LEG_SET_MISMATCH"

    with pytest.raises(final_safety.V1PostcompileFinalSafetyError) as exc_info:
        final_safety._verify_h1_v1_postcompile_candidate(
            generation,
            replace(
                candidate,
                candidate_legs=(
                    *candidate.candidate_legs,
                    candidate.candidate_legs[0],
                ),
            ),
        )
    assert exc_info.value.code == "V1_POSTCOMPILE_CANDIDATE_LEG_SET_MISMATCH"


def test_valuation_identity_rebinding_is_no_trade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("QQQ", "100"),),
        x="50",
        r_cap="50",
    )
    generation, candidate = _generation_and_candidate(case, monkeypatch)

    result = final_safety._verify_h1_v1_postcompile_candidate(
        generation,
        replace(candidate, valuation_capture_sha256="9" * 64),
    )

    assert result.terminal_outcome == "NO_TRADE"
    assert result.reason_code == "V1_POSTCOMPILE_VALUATION_GENERATION_MISMATCH"
    assert result.candidate_legs == ()


def test_p4_authority_posture_is_required_without_becoming_state_permission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("QQQ", "100"),),
        x="50",
        r_cap="50",
    )
    generation, candidate = _generation_and_candidate(case, monkeypatch)
    state = proposal._recognize_h1_v1_proposal_state(generation.proposal)
    assert state is not None
    assert state.order_compilation_allowed is True
    assert candidate.order_compilation_allowed is False

    with pytest.raises(final_safety.V1PostcompileFinalSafetyError) as exc_info:
        final_safety._verify_h1_v1_postcompile_candidate(
            generation,
            replace(candidate, order_compilation_allowed=True),
        )

    assert exc_info.value.code == (
        "V1_POSTCOMPILE_CANDIDATE_AUTHORITY_POSTURE_INVALID"
    )


def test_all_zero_compilation_is_hold_with_y_equal_existing_commitment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _fresh_case(
        tmp_path,
        positions=(("QQQ", "100"),),
        commitments=(("QQQ", "0.5"),),
        x="1",
        r_cap="1",
    )
    h1_calls = _install_case_inputs(case, monkeypatch)
    before = _files(tmp_path)

    result = final_safety.evaluate_h1_v1_postcompile_final_safety()

    assert h1_calls == [None]
    assert result.terminal_outcome == "HOLD"
    assert result.reason_code == "NO_WHOLE_SHARE_FEASIBILITY"
    assert result.state == "H1_V1_DETERMINISTIC_PROPOSAL_READY"
    assert result.candidate_legs == ()
    assert result.total_new_candidate_notional == "0"
    assert result.postcompile_total_unfilled_buy_commitment == "0.5"
    assert result.postcompile_alpha_exposure == "0"
    assert result.postcompile_core_exposure == "100.5"
    assert _files(tmp_path) == before


def test_canonical_state_rows_and_legacy_sell_are_unchanged() -> None:
    assert research_availability.canonical_allowed_actions_for_state(
        research_availability.H1_MAPPED_FRESH_NON_ACTIONABLE
    ) == ("HOLD", "NO_TRADE")
    assert research_availability.canonical_allowed_actions_for_state(
        research_availability.H1_V1_DETERMINISTIC_PROPOSAL_READY
    ) == ("HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION")
    assert research_availability.canonical_blocked_actions_for_state(
        research_availability.H1_V1_DETERMINISTIC_PROPOSAL_READY
    ) == ("SELL", "ROTATION", "REBALANCE", "EXTENDED_ETF_ADMISSION")
    assert research_availability.canonical_allowed_actions_for_state(
        research_availability.STRICT_STALE
    ) == ("HOLD", "NO_TRADE", "SELL")
