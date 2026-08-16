"""Focused contracts for the report-only H1 V1 BUY proposal."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest
import yaml

import _mmi_hermetic_source_checkout as hermetic
import investment_orchestrator.common.io as io_mod
from investment_orchestrator.cli import run_step2
from investment_orchestrator.mmi.canonical import normalize_decimal_string
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MmiCapturedSource,
    MmiSourceRole,
    begin_mmi_projection_run,
)
from investment_orchestrator.mmi.long_horizon_research_payload_v2 import (
    MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_VERSION,
    validate_mmi_long_horizon_research_payload_v2,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.observability import (
    report_only_budget_capacity as budget_capacity,
    report_only_holdings_exposure as holdings_exposure,
    report_only_increment_capacity as increment_capacity,
)
from investment_orchestrator.state import research_availability
from investment_orchestrator.workflow import (
    step2_h1_currentness,
    step3_h1_v1_proposal as proposal,
)


@dataclass(frozen=True)
class _Case:
    root: Path
    strategy_source: MmiCapturedSource
    portfolio_source: MmiCapturedSource
    h1_evaluation: step2_h1_currentness.H1CurrentContextEvaluation
    budget_result: budget_capacity.BudgetCapacityObservationResult
    exposure_result: holdings_exposure.ExposureObservationResult
    increment_result: increment_capacity.IncrementCapacityObservationResult
    policy_identity: str
    portfolio_sha256: str
    portfolio_record_identity: str


def _decimal_text(value: Decimal | str | int) -> str:
    return normalize_decimal_string(Decimal(value))


def _evidence_payload(
    *ticker_groups: tuple[str, ...],
):
    return validate_mmi_long_horizon_research_payload_v2(
        {
            "schema_version": (
                MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_VERSION
            ),
            "sources": [
                {
                    "publisher": f"Publisher {index}",
                    "published_at": "2026-08-01",
                    "source_locator": f"operator/evidence-{index}.txt",
                    "tickers": list(tickers),
                    "excerpt_text": f"Current evidence {index}.",
                }
                for index, tickers in enumerate(ticker_groups, start=1)
            ],
        }
    )


def _current_h1(
    payload,
    *,
    cited_indexes: tuple[int, ...] | None = None,
    prose_suffix: str = "A",
) -> step2_h1_currentness.H1CurrentContextEvaluation:
    identities = tuple(
        entry.source_entry_identity_sha256 for entry in payload.sources
    )
    references = (
        identities
        if cited_indexes is None
        else tuple(identities[index] for index in cited_indexes)
    )
    context = step2_h1_currentness.ValidatedCurrentH1Context(
        observed_on="2026-08-15",
        rendered_prompt_sha256="a" * 64,
        raw_response_sha256="b" * 64,
        evidence_entry_identities_sha256=identities,
        evidence_references=references,
        long_horizon_opportunity=f"Long horizon {prose_suffix}",
        valuation_context=f"Valuation {prose_suffix}",
        portfolio_contribution=f"Contribution {prose_suffix}",
        evidence_integrity=f"Integrity {prose_suffix}",
        prior_thesis_change=f"Thesis {prose_suffix}",
        current_lh2_payload=payload,
    )
    return step2_h1_currentness.H1CurrentContextEvaluation(
        observed_on=context.observed_on,
        is_current=True,
        rendered_prompt_sha256=context.rendered_prompt_sha256,
        raw_response_sha256=context.raw_response_sha256,
        reason_code=None,
        context=context,
    )


def _not_current_h1() -> step2_h1_currentness.H1CurrentContextEvaluation:
    return step2_h1_currentness.H1CurrentContextEvaluation(
        observed_on="2026-08-15",
        is_current=False,
        rendered_prompt_sha256="a" * 64,
        raw_response_sha256="b" * 64,
        reason_code="CURRENT_LH2_STALE",
        context=None,
    )


def _build_case(
    tmp_path: Path,
    *,
    positions: tuple[tuple[str, str], ...],
    commitments: tuple[tuple[str, str], ...] = (),
    x: str = "1000",
    r_cap: str = "1000",
    evidence_groups: tuple[tuple[str, ...], ...] = (("QQQ",),),
    cited_indexes: tuple[int, ...] | None = None,
    h1_evaluation: step2_h1_currentness.H1CurrentContextEvaluation | None = None,
    prose_suffix: str = "A",
    strategy_settings: dict[str, object] | None = None,
) -> _Case:
    strategy_raw = (
        hermetic.strategy_settings_bytes()
        if strategy_settings is None
        else yaml.safe_dump(
            strategy_settings,
            sort_keys=False,
            allow_unicode=True,
        ).encode("utf-8")
    )
    portfolio_raw = hermetic.portfolio_snapshot_bytes()
    strategy_source = hermetic.capture_source(
        tmp_path,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=strategy_raw,
    )
    portfolio_source = hermetic.capture_source(
        tmp_path,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        raw=portfolio_raw,
    )
    policy_result = build_mmi_policy_projection(
        strategy_source,
        run_context=begin_mmi_projection_run(),
    )
    assert policy_result.valid
    assert policy_result.projection is not None
    policy_identity = policy_result.projection[
        "policy_projection_identity_sha256"
    ]
    assert isinstance(policy_identity, str)
    universe_projection = policy_result.projection["universe_projection"]
    assert isinstance(universe_projection, dict)
    role_by_ticker = universe_projection["role_by_ticker"]
    assert isinstance(role_by_ticker, dict)

    portfolio_sha256 = portfolio_source.source_record["observed_sha256"]
    portfolio_record_identity = portfolio_source.source_record[
        "source_record_identity_sha256"
    ]
    assert isinstance(portfolio_sha256, str)
    assert isinstance(portfolio_record_identity, str)

    payload = _evidence_payload(*evidence_groups)
    current_h1 = h1_evaluation or _current_h1(
        payload,
        cited_indexes=cited_indexes,
        prose_suffix=prose_suffix,
    )
    total_h = _decimal_text(
        sum((Decimal(value) for _, value in positions), Decimal(0))
    )
    total_e = _decimal_text(
        sum((Decimal(value) for _, value in commitments), Decimal(0))
    )
    increment_fraction = _decimal_text(Decimal(r_cap) / Decimal(total_h))
    assert Decimal(0) <= Decimal(increment_fraction) <= Decimal(1)
    exposure_projection = holdings_exposure.ExposureProjection(
        schema_version="report_only_holdings_exposure_projection_v1",
        authority_effect=AUTHORITY_EFFECT_NONE,
        portfolio_source_sha256=portfolio_sha256,
        portfolio_source_record_identity_sha256=portfolio_record_identity,
        portfolio_scope_id="strict_positive_etf_positions",
        holdings_observation_date="2026-07-26",
        capture_artifact_sha256="c" * 64,
        capture_source_kind="LOCAL_NORMALIZED_MANUAL_CAPTURE",
        capture_provider_id="operator_fixture",
        capture_session_date="2026-08-14",
        capture_trusted_evaluation_timestamp_utc="2026-08-15T01:00:00Z",
        mark_ticker_domain=tuple(ticker for ticker, _ in positions),
        mark_as_of_date="2026-08-14",
        calendar_id="XNYS",
        calendar_schedule_sha256="d" * 64,
        calendar_coverage_start_date="2026-08-01",
        calendar_coverage_end_date="2026-08-31",
        trusted_evaluation_timestamp_utc="2026-08-15T01:00:00Z",
        latest_completed_session_date="2026-08-14",
        latest_completed_session_close_timestamp_et=(
            "2026-08-14T16:00:00-04:00"
        ),
        freshness_status="LATEST_COMPLETED_REVIEWED_SESSION",
        policy_projection_identity_sha256=policy_identity,
        currency="USD",
        positions=tuple(
            holdings_exposure.ExposurePosition(
                ticker=ticker,
                shares="1",
                mark=value,
                market_value=value,
                classification=role_by_ticker[ticker],
            )
            for ticker, value in positions
        ),
        total_market_value=total_h,
    )
    exposure_result = holdings_exposure.ExposureObservationResult(
        authority_effect=AUTHORITY_EFFECT_NONE,
        status=(
            holdings_exposure.ExposureObservationStatus.VALID_REPORT_ONLY
        ),
        reason_codes=(),
        projection=exposure_projection,
    )
    budget_projection = budget_capacity.BudgetCapacityProjection(
        schema_version="report_only_budget_capacity_projection_v1",
        authority_effect=AUTHORITY_EFFECT_NONE,
        budget_ceiling_source=budget_capacity.BudgetCeilingSource(
            repository_relative_locator="inputs/current/budget_ceiling.txt",
            observed_sha256="e" * 64,
            observed_size_bytes=64,
            currency="USD",
            maximum_total_unfilled_buy_commitment=_decimal_text(x),
        ),
        portfolio_source_sha256=portfolio_sha256,
        portfolio_source_record_identity_sha256=portfolio_record_identity,
        portfolio_source_date="2026-07-26",
        currency="USD",
        current_open_buy_commitments=tuple(
            budget_capacity.CurrentOpenBuyCommitment(
                ticker=ticker,
                commitment=_decimal_text(value),
                commitment_source="existing_buy_open_orders_summary",
            )
            for ticker, value in commitments
        ),
        total_current_unfilled_buy_commitment=total_e,
        remaining_ceiling=_decimal_text(max(Decimal(x) - Decimal(total_e), 0)),
        over_ceiling_amount=_decimal_text(max(Decimal(total_e) - Decimal(x), 0)),
    )
    budget_result = budget_capacity.BudgetCapacityObservationResult(
        authority_effect=AUTHORITY_EFFECT_NONE,
        status=budget_capacity.BudgetCapacityObservationStatus.VALID_REPORT_ONLY,
        reason_codes=(),
        projection=budget_projection,
    )
    increment_projection = increment_capacity.IncrementCapacityProjection(
        schema_version="report_only_increment_capacity_projection_v1",
        authority_effect=AUTHORITY_EFFECT_NONE,
        increment_fraction_source=increment_capacity.IncrementFractionSource(
            repository_relative_locator="inputs/current/increment_fraction.txt",
            observed_sha256="f" * 64,
            observed_size_bytes=64,
            basis="total_holdings_exposure",
            increment_fraction=increment_fraction,
        ),
        currency="USD",
        portfolio_source_sha256=portfolio_sha256,
        portfolio_source_record_identity_sha256=portfolio_record_identity,
        portfolio_scope_id=exposure_projection.portfolio_scope_id,
        holdings_observation_date=exposure_projection.holdings_observation_date,
        capture_artifact_sha256=exposure_projection.capture_artifact_sha256,
        capture_session_date=exposure_projection.capture_session_date,
        calendar_id=exposure_projection.calendar_id,
        calendar_schedule_sha256=exposure_projection.calendar_schedule_sha256,
        latest_completed_session_date=(
            exposure_projection.latest_completed_session_date
        ),
        freshness_status=exposure_projection.freshness_status,
        policy_projection_identity_sha256=policy_identity,
        total_holdings_exposure=total_h,
        increment_fraction=increment_fraction,
        increment_cap_basis=_decimal_text(r_cap),
    )
    increment_result = increment_capacity.IncrementCapacityObservationResult(
        authority_effect=AUTHORITY_EFFECT_NONE,
        status=(
            increment_capacity.IncrementCapacityObservationStatus.VALID_REPORT_ONLY
        ),
        reason_codes=(),
        projection=increment_projection,
    )
    return _Case(
        root=tmp_path,
        strategy_source=strategy_source,
        portfolio_source=portfolio_source,
        h1_evaluation=current_h1,
        budget_result=budget_result,
        exposure_result=exposure_result,
        increment_result=increment_result,
        policy_identity=policy_identity,
        portfolio_sha256=portfolio_sha256,
        portfolio_record_identity=portfolio_record_identity,
    )


def _install_case_inputs(
    case: _Case,
    monkeypatch: pytest.MonkeyPatch,
    *,
    h1_evaluation: step2_h1_currentness.H1CurrentContextEvaluation | None = None,
) -> list[None]:
    sources = {
        MmiSourceRole.STRATEGY_SETTINGS: case.strategy_source,
        MmiSourceRole.PORTFOLIO_SNAPSHOT: case.portfolio_source,
    }

    def capture(role: MmiSourceRole):
        source = sources[role]
        observed = source.source_record["observed_sha256"]
        assert isinstance(observed, str)
        return source, observed

    h1_calls: list[None] = []

    def evaluate_h1():
        h1_calls.append(None)
        return h1_evaluation or case.h1_evaluation

    monkeypatch.setattr(proposal, "repo_root", lambda: case.root)
    monkeypatch.setattr(proposal, "_capture_current_source", capture)
    monkeypatch.setattr(
        proposal._h1_currentness,
        "evaluate_current_h1_context",
        evaluate_h1,
    )
    monkeypatch.setattr(
        proposal._budget_capacity,
        "observe_current_report_only_budget_capacity",
        lambda **_: case.budget_result,
    )
    monkeypatch.setattr(
        proposal._holdings_exposure,
        "observe_current_report_only_holdings_exposure",
        lambda **_: case.exposure_result,
    )
    monkeypatch.setattr(
        proposal._increment_capacity,
        "observe_report_only_increment_capacity_from_exposure",
        lambda **_: case.increment_result,
    )
    return h1_calls


def _run_case(
    case: _Case,
    monkeypatch: pytest.MonkeyPatch,
    *,
    h1_evaluation: step2_h1_currentness.H1CurrentContextEvaluation | None = None,
) -> dict[str, object]:
    h1_calls = _install_case_inputs(
        case,
        monkeypatch,
        h1_evaluation=h1_evaluation,
    )
    path = proposal.build_h1_v1_proposal_workflow()
    assert h1_calls == [None]
    return json.loads(path.read_text(encoding="utf-8"))


def _recognize_case(
    case: _Case,
    monkeypatch: pytest.MonkeyPatch,
    *,
    h1_evaluation: step2_h1_currentness.H1CurrentContextEvaluation | None = None,
) -> proposal.H1V1ProposalStateRecognition | None:
    h1_calls = _install_case_inputs(
        case,
        monkeypatch,
        h1_evaluation=h1_evaluation,
    )
    result = proposal.evaluate_h1_v1_proposal_state()
    assert h1_calls == [None]
    return result


def _candidate(result: dict[str, object], ticker: str) -> dict[str, object]:
    candidates = result["candidates"]
    assert isinstance(candidates, list)
    return next(row for row in candidates if row["ticker"] == ticker)


def test_positive_core_candidate_uses_fixed_priority_and_is_report_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(
        tmp_path,
        positions=(("VOO", "600"), ("QQQ", "1400")),
        commitments=(("QQQ", "100"),),
        x="1000",
        r_cap="1000",
        evidence_groups=(("VOO",), ("QQQ",)),
    )
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    result = _run_case(case, monkeypatch)

    assert result["terminal_result"] == "POSITIVE_INCREMENT_CANDIDATE"
    assert result["selected_ticker"] == "QQQ"
    assert result["target_increment"] == "900"
    assert _candidate(result, "QQQ")["priority"] == "PREFERRED"
    assert _candidate(result, "VOO")["disposition"] == "INCREMENT_ELIGIBLE"
    assert result["report_only"] is True
    assert result["authority_effect"] == "NONE"
    assert result["not_authorization"] is True
    assert result["new_buy_permission"] is False
    assert result["order_compilation_allowed"] is False
    assert set(result) == {
        "schema_version",
        "policy_contract_version",
        "observed_on",
        "report_only",
        "authority_effect",
        "not_authorization",
        "new_buy_permission",
        "order_compilation_allowed",
        "terminal_result",
        "reason_code",
        "diagnostic_reason_codes",
        "source_bindings",
        "capacity",
        "candidates",
        "selected_ticker",
        "target_increment",
    }
    assert result["source_bindings"]["portfolio_source_sha256"] == (
        case.portfolio_sha256
    )
    forbidden_fields = {
        "quantity",
        "limit_price",
        "orders",
        "sell",
        "execution",
    }
    assert forbidden_fields.isdisjoint(result)
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert set(after) - set(before) == {
        proposal.V1_PROPOSAL_ARTIFACT_RELATIVE_PATH
    }


def test_positive_current_proposal_recognizes_exact_new_buy_permission_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(
        tmp_path,
        positions=(("VOO", "600"), ("QQQ", "1400")),
        commitments=(("QQQ", "100"),),
        x="1000",
        r_cap="1000",
        evidence_groups=(("VOO",), ("QQQ",)),
    )
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    state = _recognize_case(case, monkeypatch)

    assert state is not None
    assert state.state == "H1_V1_DETERMINISTIC_PROPOSAL_READY"
    assert state.allowed_actions == ("HOLD", "NO_TRADE", "NEW_BUY")
    expected_blocked = tuple(
        action
        for action in research_availability.ACTIONS
        if action not in state.allowed_actions
    )
    assert state.blocked_actions == expected_blocked
    assert set(state.allowed_actions).isdisjoint(state.blocked_actions)
    assert set(state.allowed_actions) | set(state.blocked_actions) == set(
        research_availability.ACTIONS
    )
    assert state.manual_review_required is False
    assert state.report_only is True
    assert state.authority_effect == "NONE"
    assert state.not_authorization is True
    assert state.new_buy_permission is True
    assert state.order_compilation_allowed is False
    assert state.step3_allowed is False
    assert state.step4_allowed is False
    assert "SELL" in state.blocked_actions
    assert "NEW_BUY" not in state.blocked_actions
    assert "ORDER_COMPILATION" in state.blocked_actions
    assert not (tmp_path / proposal.V1_PROPOSAL_ARTIFACT_RELATIVE_PATH).exists()
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_hold_and_noncurrent_no_trade_do_not_recognize_proposal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hold_case = _build_case(
        tmp_path / "hold",
        positions=(("QQQ", "1000"),),
        evidence_groups=(("VOO",),),
    )
    assert _recognize_case(hold_case, monkeypatch) is None
    assert not (
        hold_case.root / proposal.V1_PROPOSAL_ARTIFACT_RELATIVE_PATH
    ).exists()

    no_trade_case = _build_case(
        tmp_path / "no_trade",
        positions=(("QQQ", "1000"),),
        h1_evaluation=_not_current_h1(),
    )
    forged_path = (
        no_trade_case.root / proposal.V1_PROPOSAL_ARTIFACT_RELATIVE_PATH
    )
    forged_path.parent.mkdir(parents=True, exist_ok=True)
    forged_bytes = b'{"terminal_result":"POSITIVE_INCREMENT_CANDIDATE"}\n'
    forged_path.write_bytes(forged_bytes)

    assert _recognize_case(no_trade_case, monkeypatch) is None
    assert forged_path.read_bytes() == forged_bytes


def test_near_valid_positive_missing_capacity_fails_state_recognition_invariant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(
        tmp_path,
        positions=(("QQQ", "2000"),),
        commitments=(("QQQ", "100"),),
        x="1000",
        r_cap="1000",
        evidence_groups=(("QQQ",),),
    )
    h1_calls = _install_case_inputs(case, monkeypatch)
    complete = proposal.evaluate_h1_v1_proposal()
    assert h1_calls == [None]
    assert complete["terminal_result"] == "POSITIVE_INCREMENT_CANDIDATE"
    incomplete = dict(complete)
    del incomplete["capacity"]

    monkeypatch.setattr(
        proposal,
        "evaluate_h1_v1_proposal",
        lambda: incomplete,
    )
    with pytest.raises(
        proposal.V1ProposalStateRecognitionError,
        match="V1_PROPOSAL_STATE_RECOGNITION_INVARIANT_FAILED",
    ):
        proposal.evaluate_h1_v1_proposal_state()


def test_near_valid_positive_target_mismatch_fails_state_recognition_invariant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(
        tmp_path,
        positions=(("QQQ", "2000"),),
        commitments=(("QQQ", "100"),),
        x="1000",
        r_cap="1000",
        evidence_groups=(("QQQ",),),
    )
    h1_calls = _install_case_inputs(case, monkeypatch)
    complete = proposal.evaluate_h1_v1_proposal()
    assert h1_calls == [None]
    assert complete["target_increment"] == "900"
    inconsistent = dict(complete)
    inconsistent["target_increment"] = "1"

    monkeypatch.setattr(
        proposal,
        "evaluate_h1_v1_proposal",
        lambda: inconsistent,
    )
    with pytest.raises(
        proposal.V1ProposalStateRecognitionError,
        match="V1_PROPOSAL_STATE_RECOGNITION_INVARIANT_FAILED",
    ):
        proposal.evaluate_h1_v1_proposal_state()


def test_large_mixed_scale_aggregates_remain_mathematically_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extended_tickers = tuple(f"X{index:03d}" for index in range(103))
    strategy_settings = hermetic.strategy_settings_mapping()
    strategy_settings["user_approved_extended_etf_static_list"] = list(
        extended_tickers
    )
    strategy_settings["user_approved_extended_etf_theme_map"] = {}
    case = _build_case(
        tmp_path,
        positions=(
            *((ticker, "98") for ticker in extended_tickers),
            ("QQQ", "0.125"),
        ),
        commitments=tuple((ticker, "98") for ticker in extended_tickers),
        x="30000",
        r_cap="10094.125",
        evidence_groups=(("QQQ",),),
        strategy_settings=strategy_settings,
    )
    result = _run_case(case, monkeypatch)

    assert result["terminal_result"] == "NO_TRADE"
    assert result["reason_code"] == "INITIAL_ALPHA_EXCEEDS_CORE"
    assert result["capacity"] == {
        "X": "30000",
        "H": "10094.125",
        "E": "10094",
        "R": "10094.125",
        "C": None,
        "A_initial": "20188",
        "Z_initial": "0.125",
    }


def test_satellite_target_is_capped_and_extended_exposure_counts_in_alpha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(
        tmp_path,
        positions=(("QQQ", "500"), ("SMH", "200"), ("QUAL", "250")),
        x="1000",
        r_cap="950",
        evidence_groups=(("SMH",),),
    )
    result = _run_case(case, monkeypatch)

    assert result["capacity"]["Z_initial"] == "500"
    assert result["capacity"]["A_initial"] == "450"
    assert result["selected_ticker"] == "SMH"
    assert result["target_increment"] == "50"
    assert _candidate(result, "QUAL")["disposition"] == "EXCLUDE"


def test_uncited_ticker_evidence_is_maintain_only_not_exclude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(
        tmp_path,
        positions=(("QQQ", "1000"),),
        evidence_groups=(("QQQ",), ("VOO",)),
        cited_indexes=(1,),
    )
    result = _run_case(case, monkeypatch)

    assert result["terminal_result"] == "HOLD"
    assert result["reason_code"] == "NO_INCREMENT_ELIGIBLE_TICKER"
    qqq = _candidate(result, "QQQ")
    assert qqq["disposition"] == "MAINTAIN_ONLY"
    assert qqq["evidence_coverage_identities"] == []


def test_qualitative_prose_has_no_disposition_or_allocation_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(
        tmp_path,
        positions=(("QQQ", "1000"),),
        evidence_groups=(("QQQ",),),
    )
    first = _run_case(case, monkeypatch)
    payload = case.h1_evaluation.context.current_lh2_payload
    second_h1 = _current_h1(payload, prose_suffix="opposite prose")
    second = _run_case(case, monkeypatch, h1_evaluation=second_h1)

    decision_fields = (
        "terminal_result",
        "reason_code",
        "capacity",
        "candidates",
        "selected_ticker",
        "target_increment",
    )
    assert {key: first[key] for key in decision_fields} == {
        key: second[key] for key in decision_fields
    }


@pytest.mark.parametrize(
    ("positions", "commitments", "x", "r_cap", "reason"),
    (
        ((("QQQ", "1000"),), (("QQQ", "101"),), "100", "1000", "EXISTING_COMMITMENT_EXCEEDS_X"),
        ((("QQQ", "1000"),), (("QQQ", "101"),), "1000", "100", "EXISTING_COMMITMENT_EXCEEDS_R"),
        ((("QQQ", "100"), ("SMH", "101")), (), "1000", "201", "INITIAL_ALPHA_EXCEEDS_CORE"),
    ),
)
def test_closed_global_failures_are_no_trade_without_sell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    positions: tuple[tuple[str, str], ...],
    commitments: tuple[tuple[str, str], ...],
    x: str,
    r_cap: str,
    reason: str,
) -> None:
    case = _build_case(
        tmp_path,
        positions=positions,
        commitments=commitments,
        x=x,
        r_cap=r_cap,
        evidence_groups=(("QQQ", "SMH"),),
    )
    result = _run_case(case, monkeypatch)

    assert result["terminal_result"] == "NO_TRADE"
    assert result["reason_code"] == reason
    assert result["selected_ticker"] is None
    assert result["target_increment"] is None
    assert "SELL" not in json.dumps(result)


@pytest.mark.parametrize(
    ("positions", "commitments", "x", "r_cap", "evidence_groups", "reason"),
    (
        ((("QQQ", "1000"),), (("QQQ", "100"),), "100", "100", (("QQQ",),), "NO_SHARED_CAPACITY"),
        ((("QQQ", "1000"),), (), "1000", "1000", (("VOO",),), "NO_INCREMENT_ELIGIBLE_TICKER"),
    ),
)
def test_valid_zero_capacity_or_no_eligible_ticker_is_hold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    positions: tuple[tuple[str, str], ...],
    commitments: tuple[tuple[str, str], ...],
    x: str,
    r_cap: str,
    evidence_groups: tuple[tuple[str, ...], ...],
    reason: str,
) -> None:
    case = _build_case(
        tmp_path,
        positions=positions,
        commitments=commitments,
        x=x,
        r_cap=r_cap,
        evidence_groups=evidence_groups,
    )
    result = _run_case(case, monkeypatch)
    assert result["terminal_result"] == "HOLD"
    assert result["reason_code"] == reason
    assert result["selected_ticker"] is None


def test_unresolved_required_exposure_role_is_no_trade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(
        tmp_path,
        positions=(("QQQ", "1000"),),
    )
    exposure = case.exposure_result.projection
    assert exposure is not None
    unresolved_projection = replace(
        exposure,
        positions=(
            holdings_exposure.ExposurePosition(
                ticker="UNKNOWN",
                shares="1",
                mark="1000",
                market_value="1000",
                classification="UNRESOLVED",
            ),
        ),
    )
    unresolved_case = _Case(
        root=case.root,
        strategy_source=case.strategy_source,
        portfolio_source=case.portfolio_source,
        h1_evaluation=case.h1_evaluation,
        budget_result=case.budget_result,
        exposure_result=holdings_exposure.ExposureObservationResult(
            authority_effect=AUTHORITY_EFFECT_NONE,
            status=holdings_exposure.ExposureObservationStatus.MANUAL_REVIEW,
            reason_codes=("REPORT_ONLY_EXPOSURE_ROLE_UNRESOLVED",),
            projection=unresolved_projection,
        ),
        increment_result=increment_capacity.IncrementCapacityObservationResult(
            authority_effect=AUTHORITY_EFFECT_NONE,
            status=increment_capacity.IncrementCapacityObservationStatus.MANUAL_REVIEW,
            reason_codes=("REPORT_ONLY_EXPOSURE_ROLE_UNRESOLVED",),
            projection=None,
        ),
        policy_identity=case.policy_identity,
        portfolio_sha256=case.portfolio_sha256,
        portfolio_record_identity=case.portfolio_record_identity,
    )
    result = _run_case(unresolved_case, monkeypatch)
    assert result["terminal_result"] == "NO_TRADE"
    assert result["reason_code"] == "INPUT_OWNER_NOT_VALID"
    assert _candidate(result, "UNKNOWN")["disposition"] == "UNRESOLVED"


def test_noncurrent_h1_is_no_trade_and_observation_is_not_a_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(
        tmp_path,
        positions=(("QQQ", "1000"),),
        h1_evaluation=_not_current_h1(),
    )
    observation_path = (
        tmp_path
        / "artifacts/current/step2_decision_builder"
        / "h1_qualitative_currentness_observation.json"
    )
    observation_path.parent.mkdir(parents=True, exist_ok=True)
    observation_path.write_text(
        json.dumps({"is_current": True}) + "\n",
        encoding="utf-8",
    )
    result = _run_case(case, monkeypatch)

    assert result["terminal_result"] == "NO_TRADE"
    assert result["reason_code"] == "H1_CONTEXT_NOT_CURRENT"
    assert result["diagnostic_reason_codes"] == ["CURRENT_LH2_STALE"]
    assert result["selected_ticker"] is None


def test_atomic_write_failure_preserves_previous_proposal_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(tmp_path, positions=(("QQQ", "1000"),))
    output = tmp_path / proposal.V1_PROPOSAL_ARTIFACT_RELATIVE_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    previous = b'{"previous":"proposal"}\n'
    output.write_bytes(previous)

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr(io_mod.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        _run_case(case, monkeypatch)
    assert output.read_bytes() == previous


def test_increment_owner_reuses_exact_exposure_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(tmp_path, positions=(("QQQ", "1000"),))
    monkeypatch.setattr(
        increment_capacity,
        "_read_current_increment_fraction",
        lambda: (
            increment_capacity.IncrementFractionSource(
                repository_relative_locator=(
                    "inputs/current/increment_fraction.txt"
                ),
                observed_sha256="f" * 64,
                observed_size_bytes=64,
                basis="total_holdings_exposure",
                increment_fraction="0.25",
            ),
            (),
            False,
        ),
    )
    monkeypatch.setattr(
        increment_capacity._holdings_exposure,
        "observe_current_report_only_holdings_exposure",
        lambda **_: pytest.fail("exposure generation was reread"),
    )
    result = (
        increment_capacity.observe_report_only_increment_capacity_from_exposure(
            exposure_result=case.exposure_result,
        )
    )
    assert result.status is (
        increment_capacity.IncrementCapacityObservationStatus.VALID_REPORT_ONLY
    )
    assert result.projection is not None
    assert result.projection.total_holdings_exposure == "1000"
    assert result.projection.increment_cap_basis == "250"


def test_h1_contract_failure_preserves_previous_proposal_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / proposal.V1_PROPOSAL_ARTIFACT_RELATIVE_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    previous = b'{"previous":"proposal"}\n'
    output.write_bytes(previous)
    monkeypatch.setattr(proposal, "repo_root", lambda: tmp_path)

    def fail_context():
        raise ValueError("H1 contract failure")

    monkeypatch.setattr(
        proposal._h1_currentness,
        "evaluate_current_h1_context",
        fail_context,
    )
    with pytest.raises(ValueError, match="H1 contract failure"):
        proposal.build_h1_v1_proposal_workflow()
    assert output.read_bytes() == previous


def test_cli_exposes_only_argument_free_report_only_dispatch() -> None:
    args = run_step2.build_parser().parse_args(["build-v1-proposal"])
    assert vars(args) == {"command": "build-v1-proposal"}
    with pytest.raises(SystemExit):
        run_step2.build_parser().parse_args(
            ["build-v1-proposal", "--date", "2026-08-15"]
        )
