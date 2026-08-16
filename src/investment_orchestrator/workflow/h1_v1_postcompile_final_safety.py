"""Pure invocation-local V1 postcompile final-safety evaluation.

This module owns no state, permission, persistence, publication, order, or
execution behavior.  It obtains one current P1 generation, admits only the
exact P2/P3/P5 state contract, treats P4 as an untrusted candidate producer,
and independently reconstructs the candidate's deterministic arithmetic.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Final

from investment_orchestrator.market import us_equity_session_calendar as _calendar
from investment_orchestrator.mmi.contracts import AUTHORITY_EFFECT_NONE
from investment_orchestrator.state import research_availability as _availability
from investment_orchestrator.workflow import (
    h1_v1_buy_compiler_dry_run as _compiler,
    step3_h1_v1_proposal as _proposal,
)


POSTCOMPILE_CANDIDATE_VALID: Final = "POSTCOMPILE_CANDIDATE_VALID"
POSTCOMPILE_HOLD: Final = "HOLD"
POSTCOMPILE_NO_TRADE: Final = "NO_TRADE"

_EXPECTED_ALLOWED_ACTIONS: Final = (
    "HOLD",
    "NO_TRADE",
    "NEW_BUY",
    "ORDER_COMPILATION",
)
_EXPECTED_BLOCKED_ACTIONS: Final = (
    "SELL",
    "ROTATION",
    "REBALANCE",
    "EXTENDED_ETF_ADMISSION",
)


class V1PostcompileFinalSafetyError(RuntimeError):
    """A P6 admission or candidate-contract invariant failed closed."""

    def __init__(self, code: str, *, failure_class: str) -> None:
        super().__init__(code)
        self.code = code
        self.failure_class = failure_class


@dataclass(frozen=True, slots=True)
class H1V1PostcompileFinalSafetyResult:
    """One non-authorizing in-memory postcompile evaluation result."""

    terminal_outcome: str
    reason_code: str
    state: str | None
    selected_ticker: str | None
    deterministic_role: str | None
    candidate_legs: tuple[_compiler.H1V1BuyDryRunLeg, ...]
    target_increment: str | None
    total_new_candidate_notional: str | None
    postcompile_total_unfilled_buy_commitment: str | None
    postcompile_alpha_exposure: str | None
    postcompile_core_exposure: str | None
    ticker_exposures: tuple[_compiler.H1V1BuyDryRunTickerExposure, ...]
    source_bindings: tuple[tuple[str, object], ...]
    authority_effect: str = AUTHORITY_EFFECT_NONE
    not_authorization: bool = True


@dataclass(frozen=True, slots=True)
class _ReconstructedCandidate:
    legs: tuple[_compiler.H1V1BuyDryRunLeg, ...]
    ticker_exposures: tuple[_compiler.H1V1BuyDryRunTickerExposure, ...]
    target: Decimal
    new_notional: Decimal
    final_y: Decimal
    final_a: Decimal
    final_z: Decimal


def _fail(code: str, *, failure_class: str) -> None:
    raise V1PostcompileFinalSafetyError(code, failure_class=failure_class)


def _decimal(
    value: object,
    *,
    code: str,
    strictly_positive: bool = False,
) -> Decimal:
    try:
        return _compiler._decimal(
            value,
            code=code,
            strictly_positive=strictly_positive,
        )
    except _compiler.V1BuyCompilerDryRunError:
        _fail(code, failure_class="candidate/final-safety-contract")


def _text(value: Decimal) -> str:
    try:
        return _compiler._text(value)
    except _compiler.V1BuyCompilerDryRunError:
        _fail(
            "V1_POSTCOMPILE_ARITHMETIC_INVALID",
            failure_class="candidate/final-safety-contract",
        )


def _exact_sum(values: list[Decimal]) -> Decimal:
    try:
        return _compiler._exact_sum(values)
    except _compiler.V1BuyCompilerDryRunError:
        _fail(
            "V1_POSTCOMPILE_ARITHMETIC_INVALID",
            failure_class="candidate/final-safety-contract",
        )


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    try:
        return _compiler._exact_product(left, right)
    except _compiler.V1BuyCompilerDryRunError:
        _fail(
            "V1_POSTCOMPILE_ARITHMETIC_INVALID",
            failure_class="candidate/final-safety-contract",
        )


def _round_half_up_to_cents(value: Decimal) -> Decimal:
    try:
        return _compiler._round_half_up_to_cents(value)
    except _compiler.V1BuyCompilerDryRunError:
        _fail(
            "V1_POSTCOMPILE_LIMIT_PRICE_INVALID",
            failure_class="candidate/final-safety-contract",
        )


def _floor_nonnegative_ratio(numerator: Decimal, denominator: Decimal) -> int:
    try:
        return _compiler._floor_nonnegative_ratio(numerator, denominator)
    except _compiler.V1BuyCompilerDryRunError:
        _fail(
            "V1_POSTCOMPILE_QUANTITY_INPUT_INVALID",
            failure_class="candidate/final-safety-contract",
        )


def _source_bindings(proposal: Mapping[str, object]) -> Mapping[str, object]:
    value = proposal.get("source_bindings")
    if type(value) is not dict:
        _fail(
            "V1_POSTCOMPILE_PROPOSAL_BINDINGS_INVALID",
            failure_class="proposal/currentness/provenance",
        )
    return value


def _frozen_source_bindings(
    proposal: Mapping[str, object],
) -> tuple[tuple[str, object], ...]:
    frozen: list[tuple[str, object]] = []
    for key, value in sorted(_source_bindings(proposal).items()):
        if type(key) is not str:
            _fail(
                "V1_POSTCOMPILE_PROPOSAL_BINDINGS_INVALID",
                failure_class="proposal/currentness/provenance",
            )
        if type(value) is list:
            if any(type(item) is not str for item in value):
                _fail(
                    "V1_POSTCOMPILE_PROPOSAL_BINDINGS_INVALID",
                    failure_class="proposal/currentness/provenance",
                )
            value = tuple(value)
        elif value is not None and type(value) is not str:
            _fail(
                "V1_POSTCOMPILE_PROPOSAL_BINDINGS_INVALID",
                failure_class="proposal/currentness/provenance",
            )
        frozen.append((key, value))
    return tuple(frozen)


def _result(
    proposal: Mapping[str, object],
    *,
    terminal_outcome: str,
    reason_code: str,
    state: str | None = None,
    selected_ticker: str | None = None,
    deterministic_role: str | None = None,
    candidate_legs: tuple[_compiler.H1V1BuyDryRunLeg, ...] = (),
    target_increment: str | None = None,
    total_new_candidate_notional: str | None = None,
    postcompile_total_unfilled_buy_commitment: str | None = None,
    postcompile_alpha_exposure: str | None = None,
    postcompile_core_exposure: str | None = None,
    ticker_exposures: tuple[
        _compiler.H1V1BuyDryRunTickerExposure, ...
    ] = (),
) -> H1V1PostcompileFinalSafetyResult:
    return H1V1PostcompileFinalSafetyResult(
        terminal_outcome=terminal_outcome,
        reason_code=reason_code,
        state=state,
        selected_ticker=selected_ticker,
        deterministic_role=deterministic_role,
        candidate_legs=candidate_legs,
        target_increment=target_increment,
        total_new_candidate_notional=total_new_candidate_notional,
        postcompile_total_unfilled_buy_commitment=(
            postcompile_total_unfilled_buy_commitment
        ),
        postcompile_alpha_exposure=postcompile_alpha_exposure,
        postcompile_core_exposure=postcompile_core_exposure,
        ticker_exposures=ticker_exposures,
        source_bindings=_frozen_source_bindings(proposal),
    )


def _recognize_and_admit(
    proposal: Mapping[str, object],
) -> _proposal.H1V1ProposalStateRecognition | None:
    try:
        state = _proposal._recognize_h1_v1_proposal_state(proposal)
    except _proposal.V1ProposalStateRecognitionError:
        _fail(
            "V1_POSTCOMPILE_PROPOSAL_CONTRACT_INVALID",
            failure_class="proposal/currentness/provenance",
        )
    terminal = proposal.get("terminal_result")
    if terminal in {_proposal.TERMINAL_HOLD, _proposal.TERMINAL_NO_TRADE}:
        if state is not None:
            _fail(
                "V1_POSTCOMPILE_STATE_RECOGNITION_INVALID",
                failure_class="proposal/currentness/provenance",
            )
        return None
    if state is None:
        _fail(
            "V1_POSTCOMPILE_PERMISSION_SUBJECT_NOT_READY",
            failure_class="availability/permission",
        )
    try:
        allowed = _availability.canonical_allowed_actions_for_state(state.state)
        blocked = _availability.canonical_blocked_actions_for_state(state.state)
    except KeyError:
        _fail(
            "V1_POSTCOMPILE_PERMISSION_SUBJECT_NOT_READY",
            failure_class="availability/permission",
        )
    if not (
        state.state == _proposal.H1_V1_DETERMINISTIC_PROPOSAL_READY
        and allowed == _EXPECTED_ALLOWED_ACTIONS
        and blocked == _EXPECTED_BLOCKED_ACTIONS
        and state.allowed_actions == allowed
        and state.blocked_actions == blocked
        and len(allowed) + len(blocked) == len(_availability.ACTIONS)
        and set(allowed).isdisjoint(blocked)
        and set(allowed) | set(blocked) == set(_availability.ACTIONS)
        and state.new_buy_permission
        and state.order_compilation_allowed
        and not state.step3_allowed
        and not state.step4_allowed
    ):
        _fail(
            "V1_POSTCOMPILE_PERMISSION_CONTRACT_INVALID",
            failure_class="availability/permission",
        )
    return state


def _complete_generation(
    evaluation: _proposal.H1V1ProposalEvaluation,
) -> tuple[
    object,
    Mapping[str, str],
    object,
    object,
    object,
]:
    if (
        evaluation.strategy_source is None
        or evaluation.role_by_ticker is None
        or evaluation.budget is None
        or evaluation.exposure is None
        or evaluation.increment is None
    ):
        _fail(
            "V1_POSTCOMPILE_PROPOSAL_GENERATION_INCOMPLETE",
            failure_class="proposal/currentness/provenance",
        )
    return (
        evaluation.strategy_source,
        evaluation.role_by_ticker,
        evaluation.budget,
        evaluation.exposure,
        evaluation.increment,
    )


def _generation_provenance_failure(
    evaluation: _proposal.H1V1ProposalEvaluation,
    bindings: Mapping[str, object],
) -> str | None:
    strategy, roles, budget, exposure, increment = _complete_generation(evaluation)
    strategy_record = strategy.source_record
    expected = {
        "strategy_source_sha256": strategy_record.get("observed_sha256"),
        "strategy_source_record_identity_sha256": strategy_record.get(
            "source_record_identity_sha256"
        ),
        "portfolio_source_sha256": exposure.portfolio_source_sha256,
        "portfolio_source_record_identity_sha256": (
            exposure.portfolio_source_record_identity_sha256
        ),
        "holdings_policy_projection_identity_sha256": (
            exposure.policy_projection_identity_sha256
        ),
        "portfolio_scope_id": exposure.portfolio_scope_id,
        "holdings_observation_date": exposure.holdings_observation_date,
        "valuation_capture_sha256": exposure.capture_artifact_sha256,
        "valuation_source_kind": exposure.capture_source_kind,
        "valuation_provider_id": exposure.capture_provider_id,
        "valuation_session_date": exposure.capture_session_date,
        "valuation_trusted_evaluation_timestamp_utc": (
            exposure.capture_trusted_evaluation_timestamp_utc
        ),
        "calendar_id": exposure.calendar_id,
        "calendar_schedule_sha256": exposure.calendar_schedule_sha256,
        "latest_completed_session_date": (
            exposure.latest_completed_session_date
        ),
        "valuation_freshness_status": exposure.freshness_status,
        "x_source_sha256": budget.budget_ceiling_source.observed_sha256,
        "r_source_sha256": increment.increment_fraction_source.observed_sha256,
    }
    if any(bindings.get(key) != value for key, value in expected.items()):
        return "V1_POSTCOMPILE_INPUT_GENERATION_MISMATCH"
    if not (
        budget.portfolio_source_sha256
        == exposure.portfolio_source_sha256
        == increment.portfolio_source_sha256
        and budget.portfolio_source_record_identity_sha256
        == exposure.portfolio_source_record_identity_sha256
        == increment.portfolio_source_record_identity_sha256
        and budget.portfolio_source_date == exposure.holdings_observation_date
        and increment.portfolio_scope_id == exposure.portfolio_scope_id
        and increment.holdings_observation_date
        == exposure.holdings_observation_date
        and increment.capture_artifact_sha256
        == exposure.capture_artifact_sha256
        and increment.capture_session_date == exposure.capture_session_date
        and increment.calendar_id == exposure.calendar_id
        and increment.calendar_schedule_sha256
        == exposure.calendar_schedule_sha256
        and increment.latest_completed_session_date
        == exposure.latest_completed_session_date
        and increment.freshness_status == exposure.freshness_status
        and increment.policy_projection_identity_sha256
        == exposure.policy_projection_identity_sha256
        and increment.total_holdings_exposure == exposure.total_market_value
    ):
        return "V1_POSTCOMPILE_INPUT_GENERATION_MISMATCH"
    for position in exposure.positions:
        if roles.get(position.ticker) != position.classification:
            return "V1_POSTCOMPILE_INPUT_GENERATION_MISMATCH"
    if any(commitment.ticker not in roles for commitment in budget.current_open_buy_commitments):
        return "V1_POSTCOMPILE_INPUT_GENERATION_MISMATCH"
    return None


def _parse_trusted_evaluation_time(value: object) -> datetime | None:
    if type(value) is not str or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed


def _valuation_freshness_failure(
    evaluation: _proposal.H1V1ProposalEvaluation,
) -> str | None:
    _strategy, _roles, _budget, exposure, _increment = _complete_generation(
        evaluation
    )
    evaluation_time = _parse_trusted_evaluation_time(
        exposure.trusted_evaluation_timestamp_utc
    )
    try:
        mark_date = date.fromisoformat(exposure.mark_as_of_date)
    except (TypeError, ValueError):
        mark_date = None
    if (
        evaluation_time is None
        or mark_date is None
        or mark_date.isoformat() != exposure.mark_as_of_date
    ):
        return "V1_POSTCOMPILE_VALUATION_GENERATION_MISMATCH"
    freshness = _calendar.assess_manual_mark_freshness(
        mark_as_of_date=mark_date,
        evaluation_time_utc=evaluation_time,
    )
    if freshness.status is not _calendar.MarkFreshnessStatus.FRESH:
        return (
            freshness.reason_codes[0]
            if freshness.reason_codes
            else "V1_POSTCOMPILE_VALUATION_NOT_CURRENT"
        )
    completed = freshness.completed_session
    if completed is None:
        return "V1_POSTCOMPILE_VALUATION_GENERATION_MISMATCH"
    if not (
        freshness.mark_as_of_date == mark_date
        and completed.calendar_id == exposure.calendar_id
        and completed.calendar_schedule_sha256
        == exposure.calendar_schedule_sha256
        and completed.coverage_start_date == exposure.calendar_coverage_start_date
        and completed.coverage_end_date == exposure.calendar_coverage_end_date
        and completed.trusted_evaluation_timestamp_utc
        == exposure.trusted_evaluation_timestamp_utc
        and completed.session_date == exposure.latest_completed_session_date
        and completed.session_date == exposure.capture_session_date
        and completed.session_date == exposure.mark_as_of_date
        and completed.official_close_timestamp_et
        == exposure.latest_completed_session_close_timestamp_et
        and exposure.freshness_status
        == _calendar.MarkFreshnessStatus.FRESH.value
    ):
        return "V1_POSTCOMPILE_VALUATION_GENERATION_MISMATCH"
    return None


def _selected_proposal_facts(
    evaluation: _proposal.H1V1ProposalEvaluation,
) -> tuple[str, str, Decimal, Mapping[str, object]]:
    proposal = evaluation.proposal
    selected_ticker = proposal.get("selected_ticker")
    candidates = proposal.get("candidates")
    if type(selected_ticker) is not str or type(candidates) is not list:
        _fail(
            "V1_POSTCOMPILE_PROPOSAL_CONTRACT_INVALID",
            failure_class="proposal/currentness/provenance",
        )
    selected = [
        row
        for row in candidates
        if type(row) is dict and row.get("ticker") == selected_ticker
    ]
    if len(selected) != 1:
        _fail(
            "V1_POSTCOMPILE_PROPOSAL_CONTRACT_INVALID",
            failure_class="proposal/currentness/provenance",
        )
    role = selected[0].get("role")
    if role not in {_proposal.ROLE_CORE, _proposal.ROLE_SATELLITE}:
        _fail(
            "V1_POSTCOMPILE_PROPOSAL_CONTRACT_INVALID",
            failure_class="proposal/currentness/provenance",
        )
    target = _decimal(
        proposal.get("target_increment"),
        code="V1_POSTCOMPILE_TARGET_INVALID",
        strictly_positive=True,
    )
    return selected_ticker, role, target, selected[0]


def _strategy_ladder(
    evaluation: _proposal.H1V1ProposalEvaluation,
    *,
    selected_ticker: str,
    deterministic_role: str,
) -> tuple[str, tuple[_compiler._LadderStep, ...]]:
    strategy, _roles, _budget, _exposure, _increment = _complete_generation(
        evaluation
    )
    try:
        settings = _compiler._strategy_payload(strategy.raw_bytes)
        pricing_role = _compiler._pricing_role(
            settings,
            ticker=selected_ticker,
            deterministic_role=deterministic_role,
        )
        steps = _compiler._ladder(settings, pricing_role=pricing_role)
    except _compiler.V1BuyCompilerDryRunError as exc:
        _fail(exc.code, failure_class="candidate/final-safety-contract")
    return pricing_role, steps


def _selected_mark(
    evaluation: _proposal.H1V1ProposalEvaluation,
    *,
    selected_ticker: str,
    deterministic_role: str,
) -> Decimal:
    _strategy, roles, _budget, exposure, _increment = _complete_generation(
        evaluation
    )
    selected = [
        position
        for position in exposure.positions
        if position.ticker == selected_ticker
    ]
    if (
        len(selected) != 1
        or selected_ticker not in exposure.mark_ticker_domain
        or selected[0].classification != deterministic_role
        or roles.get(selected_ticker) != deterministic_role
    ):
        _fail(
            "V1_POSTCOMPILE_SELECTED_TICKER_MISMATCH",
            failure_class="candidate/final-safety-contract",
        )
    return _decimal(
        selected[0].mark,
        code="V1_POSTCOMPILE_SELECTED_MARK_INVALID",
        strictly_positive=True,
    )


def _candidate_posture_is_exact(
    candidate: _compiler.H1V1BuyCompilerDryRunResult,
) -> bool:
    return (
        candidate.dry_run_only is True
        and candidate.authority_effect == AUTHORITY_EFFECT_NONE
        and candidate.not_authorization is True
        and candidate.order_compilation_allowed is False
    )


def _candidate_identity_failure(
    evaluation: _proposal.H1V1ProposalEvaluation,
    candidate: _compiler.H1V1BuyCompilerDryRunResult,
    *,
    mark: Decimal,
) -> str | None:
    _strategy, _roles, _budget, exposure, _increment = _complete_generation(
        evaluation
    )
    if not (
        candidate.valuation_capture_sha256 == exposure.capture_artifact_sha256
        and candidate.valuation_provider_id == exposure.capture_provider_id
        and candidate.valuation_session_date == exposure.capture_session_date
        and candidate.calendar_id == exposure.calendar_id
        and candidate.calendar_schedule_sha256
        == exposure.calendar_schedule_sha256
        and candidate.validated_mark == _text(mark)
    ):
        return "V1_POSTCOMPILE_VALUATION_GENERATION_MISMATCH"
    return None


def _expected_legs(
    *,
    target: Decimal,
    mark: Decimal,
    steps: tuple[_compiler._LadderStep, ...],
) -> tuple[_compiler.H1V1BuyDryRunLeg, ...]:
    expected: list[_compiler.H1V1BuyDryRunLeg] = []
    for step in steps:
        raw_price = _exact_product(
            mark,
            _exact_sum([Decimal(1), step.offset]),
        )
        rounded_price = _round_half_up_to_cents(raw_price)
        leg_budget = _exact_product(target, step.weight)
        quantity = _floor_nonnegative_ratio(leg_budget, rounded_price)
        if quantity == 0:
            continue
        notional = _exact_product(rounded_price, Decimal(quantity))
        expected.append(
            _compiler.H1V1BuyDryRunLeg(
                step_name=step.name,
                allocation_weight=_text(step.weight),
                limit_offset_from_mark=_text(step.offset),
                rounded_limit_price=_text(rounded_price),
                whole_share_quantity=quantity,
                candidate_notional=_text(notional),
            )
        )
    return tuple(expected)


def _require_exact_leg_contract(
    candidate: _compiler.H1V1BuyCompilerDryRunResult,
    expected: tuple[_compiler.H1V1BuyDryRunLeg, ...],
) -> None:
    if type(candidate.candidate_legs) is not tuple or any(
        type(leg) is not _compiler.H1V1BuyDryRunLeg
        for leg in candidate.candidate_legs
    ):
        _fail(
            "V1_POSTCOMPILE_CANDIDATE_LEG_CONTRACT_INVALID",
            failure_class="candidate/final-safety-contract",
        )
    actual_names = tuple(leg.step_name for leg in candidate.candidate_legs)
    expected_names = tuple(leg.step_name for leg in expected)
    if actual_names != expected_names or len(set(actual_names)) != len(actual_names):
        _fail(
            "V1_POSTCOMPILE_CANDIDATE_LEG_SET_MISMATCH",
            failure_class="candidate/final-safety-contract",
        )
    for actual, required in zip(candidate.candidate_legs, expected, strict=True):
        if (
            actual.allocation_weight != required.allocation_weight
            or actual.limit_offset_from_mark != required.limit_offset_from_mark
        ):
            _fail(
                "V1_POSTCOMPILE_CANDIDATE_LADDER_MISMATCH",
                failure_class="candidate/final-safety-contract",
            )
        if actual.rounded_limit_price != required.rounded_limit_price:
            _fail(
                "V1_POSTCOMPILE_CANDIDATE_PRICE_MISMATCH",
                failure_class="candidate/final-safety-contract",
            )
        if (
            type(actual.whole_share_quantity) is not int
            or actual.whole_share_quantity <= 0
            or actual.whole_share_quantity != required.whole_share_quantity
        ):
            _fail(
                "V1_POSTCOMPILE_CANDIDATE_QUANTITY_MISMATCH",
                failure_class="candidate/final-safety-contract",
            )
        if actual.candidate_notional != required.candidate_notional:
            _fail(
                "V1_POSTCOMPILE_CANDIDATE_NOTIONAL_MISMATCH",
                failure_class="candidate/final-safety-contract",
            )


def _reconstruct_exposures(
    evaluation: _proposal.H1V1ProposalEvaluation,
    *,
    selected_ticker: str,
    selected_notional: Decimal,
) -> tuple[
    tuple[_compiler.H1V1BuyDryRunTickerExposure, ...],
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
]:
    _strategy, roles, budget, exposure, increment = _complete_generation(evaluation)
    holdings: dict[str, Decimal] = {}
    for position in exposure.positions:
        if position.ticker in holdings:
            _fail(
                "V1_POSTCOMPILE_HOLDING_DUPLICATE",
                failure_class="proposal/currentness/provenance",
            )
        holdings[position.ticker] = _decimal(
            position.market_value,
            code="V1_POSTCOMPILE_HOLDING_INVALID",
        )
    commitments: dict[str, Decimal] = {}
    for commitment in budget.current_open_buy_commitments:
        if commitment.ticker in commitments:
            _fail(
                "V1_POSTCOMPILE_COMMITMENT_DUPLICATE",
                failure_class="proposal/currentness/provenance",
            )
        commitments[commitment.ticker] = _decimal(
            commitment.commitment,
            code="V1_POSTCOMPILE_COMMITMENT_INVALID",
        )
    tickers = sorted(set(holdings) | set(commitments))
    if selected_ticker not in tickers:
        _fail(
            "V1_POSTCOMPILE_SELECTED_TICKER_MISMATCH",
            failure_class="candidate/final-safety-contract",
        )
    rows: list[_compiler.H1V1BuyDryRunTickerExposure] = []
    y_values: list[Decimal] = []
    initial_a_values: list[Decimal] = []
    initial_z_values: list[Decimal] = []
    final_a_values: list[Decimal] = []
    final_z_values: list[Decimal] = []
    for ticker in tickers:
        role = roles.get(ticker)
        if role not in {
            _proposal.ROLE_CORE,
            _proposal.ROLE_SATELLITE,
            _proposal.ROLE_APPROVED_EXTENDED,
        }:
            _fail(
                "V1_POSTCOMPILE_EXPOSURE_ROLE_UNRESOLVED",
                failure_class="proposal/currentness/provenance",
            )
        h_value = holdings.get(ticker, Decimal(0))
        e_value = commitments.get(ticker, Decimal(0))
        n_value = selected_notional if ticker == selected_ticker else Decimal(0)
        y_value = _exact_sum([e_value, n_value])
        initial_d = _exact_sum([h_value, e_value])
        final_d = _exact_sum([h_value, y_value])
        y_values.append(y_value)
        if role == _proposal.ROLE_CORE:
            initial_z_values.append(initial_d)
            final_z_values.append(final_d)
        else:
            initial_a_values.append(initial_d)
            final_a_values.append(final_d)
        rows.append(
            _compiler.H1V1BuyDryRunTickerExposure(
                ticker=ticker,
                role=role,
                holdings_exposure=_text(h_value),
                retained_buy_commitment=_text(e_value),
                new_candidate_notional=_text(n_value),
                final_unfilled_buy_commitment=_text(y_value),
                final_projected_exposure=_text(final_d),
            )
        )
    return (
        tuple(rows),
        _exact_sum(y_values),
        _exact_sum(final_a_values),
        _exact_sum(final_z_values),
        _exact_sum(initial_a_values),
        _exact_sum(initial_z_values),
        _decimal(
            budget.budget_ceiling_source.maximum_total_unfilled_buy_commitment,
            code="V1_POSTCOMPILE_X_INVALID",
        ),
        _decimal(
            increment.increment_cap_basis,
            code="V1_POSTCOMPILE_R_INVALID",
        ),
    )


def _candidate_result(
    evaluation: _proposal.H1V1ProposalEvaluation,
    *,
    state: _proposal.H1V1ProposalStateRecognition,
    candidate: _compiler.H1V1BuyCompilerDryRunResult,
    reconstructed: _ReconstructedCandidate,
    terminal_outcome: str,
    reason_code: str,
) -> H1V1PostcompileFinalSafetyResult:
    return _result(
        evaluation.proposal,
        terminal_outcome=terminal_outcome,
        reason_code=reason_code,
        state=state.state,
        selected_ticker=candidate.selected_ticker,
        deterministic_role=candidate.deterministic_role,
        candidate_legs=reconstructed.legs,
        target_increment=_text(reconstructed.target),
        total_new_candidate_notional=_text(reconstructed.new_notional),
        postcompile_total_unfilled_buy_commitment=_text(reconstructed.final_y),
        postcompile_alpha_exposure=_text(reconstructed.final_a),
        postcompile_core_exposure=_text(reconstructed.final_z),
        ticker_exposures=reconstructed.ticker_exposures,
    )


def _verify_h1_v1_postcompile_candidate(
    evaluation: _proposal.H1V1ProposalEvaluation,
    candidate: _compiler.H1V1BuyCompilerDryRunResult,
    *,
    _admitted_state: _proposal.H1V1ProposalStateRecognition | None = None,
) -> H1V1PostcompileFinalSafetyResult:
    """Independently verify one P4 candidate against its exact P1 generation."""
    state = _admitted_state or _recognize_and_admit(evaluation.proposal)
    if state is None:
        _fail(
            "V1_POSTCOMPILE_PERMISSION_SUBJECT_NOT_READY",
            failure_class="availability/permission",
        )
    if type(candidate) is not _compiler.H1V1BuyCompilerDryRunResult:
        _fail(
            "V1_POSTCOMPILE_CANDIDATE_CONTRACT_INVALID",
            failure_class="candidate/final-safety-contract",
        )
    if not _candidate_posture_is_exact(candidate):
        _fail(
            "V1_POSTCOMPILE_CANDIDATE_AUTHORITY_POSTURE_INVALID",
            failure_class="candidate/final-safety-contract",
        )
    bindings = _source_bindings(evaluation.proposal)
    provenance_failure = _generation_provenance_failure(evaluation, bindings)
    if provenance_failure is not None:
        return _result(
            evaluation.proposal,
            terminal_outcome=POSTCOMPILE_NO_TRADE,
            reason_code=provenance_failure,
            state=state.state,
        )
    freshness_failure = _valuation_freshness_failure(evaluation)
    if freshness_failure is not None:
        return _result(
            evaluation.proposal,
            terminal_outcome=POSTCOMPILE_NO_TRADE,
            reason_code=freshness_failure,
            state=state.state,
        )

    selected_ticker, deterministic_role, target, _selected = (
        _selected_proposal_facts(evaluation)
    )
    if (
        candidate.selected_ticker != selected_ticker
        or candidate.deterministic_role != deterministic_role
        or candidate.target_increment != _text(target)
    ):
        _fail(
            "V1_POSTCOMPILE_SELECTED_TICKER_OR_TARGET_MISMATCH",
            failure_class="candidate/final-safety-contract",
        )
    pricing_role, steps = _strategy_ladder(
        evaluation,
        selected_ticker=selected_ticker,
        deterministic_role=deterministic_role,
    )
    if candidate.pricing_role != pricing_role:
        _fail(
            "V1_POSTCOMPILE_CANDIDATE_LADDER_MISMATCH",
            failure_class="candidate/final-safety-contract",
        )
    mark = _selected_mark(
        evaluation,
        selected_ticker=selected_ticker,
        deterministic_role=deterministic_role,
    )
    identity_failure = _candidate_identity_failure(
        evaluation,
        candidate,
        mark=mark,
    )
    if identity_failure is not None:
        return _result(
            evaluation.proposal,
            terminal_outcome=POSTCOMPILE_NO_TRADE,
            reason_code=identity_failure,
            state=state.state,
        )
    expected_legs = _expected_legs(target=target, mark=mark, steps=steps)
    _require_exact_leg_contract(candidate, expected_legs)
    expected_notionals = [
        _decimal(
            leg.candidate_notional,
            code="V1_POSTCOMPILE_CANDIDATE_NOTIONAL_INVALID",
            strictly_positive=True,
        )
        for leg in expected_legs
    ]
    new_notional = _exact_sum(expected_notionals)
    if candidate.total_new_candidate_notional != _text(new_notional):
        _fail(
            "V1_POSTCOMPILE_CANDIDATE_AGGREGATE_MISMATCH",
            failure_class="candidate/final-safety-contract",
        )

    (
        expected_exposures,
        final_y,
        final_a,
        final_z,
        initial_a,
        initial_z,
        x_value,
        r_value,
    ) = _reconstruct_exposures(
        evaluation,
        selected_ticker=selected_ticker,
        selected_notional=new_notional,
    )
    capacity = evaluation.proposal.get("capacity")
    if type(capacity) is not dict:
        _fail(
            "V1_POSTCOMPILE_PROPOSAL_CONTRACT_INVALID",
            failure_class="proposal/currentness/provenance",
        )
    _strategy, _roles, budget, exposure, increment = _complete_generation(evaluation)
    h_value = _exact_sum(
        [
            _decimal(
                position.market_value,
                code="V1_POSTCOMPILE_HOLDING_INVALID",
            )
            for position in exposure.positions
        ]
    )
    e_value = _exact_sum(
        [
            _decimal(
                commitment.commitment,
                code="V1_POSTCOMPILE_COMMITMENT_INVALID",
            )
            for commitment in budget.current_open_buy_commitments
        ]
    )
    if not (
        capacity.get("H") == _text(h_value) == exposure.total_market_value
        and capacity.get("E")
        == _text(e_value)
        == budget.total_current_unfilled_buy_commitment
        and capacity.get("X") == _text(x_value)
        and capacity.get("R") == _text(r_value) == increment.increment_cap_basis
        and capacity.get("A_initial") == _text(initial_a)
        and capacity.get("Z_initial") == _text(initial_z)
    ):
        return _result(
            evaluation.proposal,
            terminal_outcome=POSTCOMPILE_NO_TRADE,
            reason_code="V1_POSTCOMPILE_INPUT_GENERATION_MISMATCH",
            state=state.state,
        )
    if type(candidate.ticker_exposures) is not tuple or (
        candidate.ticker_exposures != expected_exposures
    ):
        _fail(
            "V1_POSTCOMPILE_CANDIDATE_EXPOSURE_MISMATCH",
            failure_class="candidate/final-safety-contract",
        )
    if not (
        candidate.postcompile_total_unfilled_buy_commitment == _text(final_y)
        and candidate.postcompile_alpha_exposure == _text(final_a)
        and candidate.postcompile_core_exposure == _text(final_z)
    ):
        _fail(
            "V1_POSTCOMPILE_CANDIDATE_AGGREGATE_MISMATCH",
            failure_class="candidate/final-safety-contract",
        )

    reconstructed = _ReconstructedCandidate(
        legs=expected_legs,
        ticker_exposures=expected_exposures,
        target=target,
        new_notional=new_notional,
        final_y=final_y,
        final_a=final_a,
        final_z=final_z,
    )

    if expected_legs:
        if not (
            candidate.terminal_outcome == _compiler.DRY_RUN_POSITIVE
            and candidate.reason_code == "POSITIVE_WHOLE_SHARE_CANDIDATE"
        ):
            _fail(
                "V1_POSTCOMPILE_CANDIDATE_TERMINAL_MISMATCH",
                failure_class="candidate/final-safety-contract",
            )
        terminal = POSTCOMPILE_CANDIDATE_VALID
        reason = POSTCOMPILE_CANDIDATE_VALID
    else:
        if not (
            candidate.terminal_outcome == _compiler.DRY_RUN_HOLD
            and candidate.reason_code == "NO_WHOLE_SHARE_FEASIBILITY"
            and new_notional == 0
        ):
            _fail(
                "V1_POSTCOMPILE_CANDIDATE_TERMINAL_MISMATCH",
                failure_class="candidate/final-safety-contract",
            )
        terminal = POSTCOMPILE_HOLD
        reason = "NO_WHOLE_SHARE_FEASIBILITY"
    return _candidate_result(
        evaluation,
        state=state,
        candidate=candidate,
        reconstructed=reconstructed,
        terminal_outcome=terminal,
        reason_code=reason,
    )


def _evaluate_h1_v1_postcompile_final_safety_from_generation(
    evaluation: _proposal.H1V1ProposalEvaluation,
) -> H1V1PostcompileFinalSafetyResult:
    """Evaluate one already-bound generation without rereading any source."""
    state = _recognize_and_admit(evaluation.proposal)
    terminal = evaluation.proposal.get("terminal_result")
    reason = evaluation.proposal.get("reason_code")
    if type(reason) is not str:
        _fail(
            "V1_POSTCOMPILE_PROPOSAL_CONTRACT_INVALID",
            failure_class="proposal/currentness/provenance",
        )
    if terminal == _proposal.TERMINAL_HOLD:
        return _result(
            evaluation.proposal,
            terminal_outcome=POSTCOMPILE_HOLD,
            reason_code=reason,
        )
    if terminal == _proposal.TERMINAL_NO_TRADE:
        upstream_reason = _compiler._upstream_provenance_or_currentness_failure(
            evaluation.proposal
        )
        return _result(
            evaluation.proposal,
            terminal_outcome=POSTCOMPILE_NO_TRADE,
            reason_code=upstream_reason or reason,
        )
    if state is None:
        _fail(
            "V1_POSTCOMPILE_PERMISSION_SUBJECT_NOT_READY",
            failure_class="availability/permission",
        )
    try:
        candidate = _compiler._evaluate_h1_v1_buy_compiler_dry_run_from_generation(
            evaluation
        )
    except _compiler.V1BuyCompilerDryRunError as exc:
        if exc.failure_class == "provenance/currentness":
            return _result(
                evaluation.proposal,
                terminal_outcome=POSTCOMPILE_NO_TRADE,
                reason_code=exc.code,
                state=state.state,
            )
        _fail(exc.code, failure_class="candidate/final-safety-contract")
    return _verify_h1_v1_postcompile_candidate(
        evaluation,
        candidate,
        _admitted_state=state,
    )


def evaluate_h1_v1_postcompile_final_safety(
) -> H1V1PostcompileFinalSafetyResult:
    """Evaluate one current V1 postcompile candidate, purely and in memory."""
    generation = _proposal.evaluate_h1_v1_proposal_generation()
    return _evaluate_h1_v1_postcompile_final_safety_from_generation(generation)
