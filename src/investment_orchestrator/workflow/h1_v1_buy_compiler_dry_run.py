"""Pure deterministic V1 BUY compiler dry-run.

This module projects one complete current P1 target into candidate prices,
whole-share quantities, and notionals.  It writes nothing, creates no order
artifact, and grants no ORDER_COMPILATION, Step 4, publication, final-safety,
broker, or execution authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

import yaml

from investment_orchestrator.market.us_equity_session_calendar import (
    MarkFreshnessStatus,
)
from investment_orchestrator.mmi.canonical import (
    MmiCanonicalizationError,
    normalize_decimal_string,
)
from investment_orchestrator.mmi.contracts import AUTHORITY_EFFECT_NONE
from investment_orchestrator.workflow import step3_h1_v1_proposal as _proposal


DRY_RUN_POSITIVE: Final = "POSITIVE_BUY_DRY_RUN"
DRY_RUN_HOLD: Final = "HOLD"

_PRICE_QUANTUM_EXPONENT: Final = -2
_PRICING_ROLE_BENCHMARK: Final = "benchmark_carrier_core"
_PRICING_ROLE_DIVERSIFIED: Final = "diversified_core_buffer"
_PRICING_ROLE_SATELLITE: Final = "sector_alpha_tilt"


class V1BuyCompilerDryRunError(RuntimeError):
    """A required dry-run input or compiler invariant failed closed."""

    def __init__(self, code: str, *, failure_class: str) -> None:
        super().__init__(code)
        self.code = code
        self.failure_class = failure_class


@dataclass(frozen=True, slots=True)
class H1V1BuyDryRunLeg:
    """One non-authoritative whole-share candidate ladder leg."""

    step_name: str
    allocation_weight: str
    limit_offset_from_mark: str
    rounded_limit_price: str
    whole_share_quantity: int
    candidate_notional: str


@dataclass(frozen=True, slots=True)
class H1V1BuyDryRunTickerExposure:
    """Exact postcompile exposure facts for one current exposure ticker."""

    ticker: str
    role: str
    holdings_exposure: str
    retained_buy_commitment: str
    new_candidate_notional: str
    final_unfilled_buy_commitment: str
    final_projected_exposure: str


@dataclass(frozen=True, slots=True)
class H1V1BuyCompilerDryRunResult:
    """One in-memory dry-run derivative with no order authority."""

    terminal_outcome: str
    reason_code: str
    selected_ticker: str
    deterministic_role: str
    pricing_role: str
    valuation_capture_sha256: str
    valuation_provider_id: str
    valuation_session_date: str
    calendar_id: str
    calendar_schedule_sha256: str
    validated_mark: str
    target_increment: str
    candidate_legs: tuple[H1V1BuyDryRunLeg, ...]
    total_new_candidate_notional: str
    postcompile_total_unfilled_buy_commitment: str
    postcompile_alpha_exposure: str
    postcompile_core_exposure: str
    ticker_exposures: tuple[H1V1BuyDryRunTickerExposure, ...]
    dry_run_only: bool = True
    authority_effect: str = AUTHORITY_EFFECT_NONE
    not_authorization: bool = True
    order_compilation_allowed: bool = False


@dataclass(frozen=True, slots=True)
class _LadderStep:
    name: str
    offset: Decimal
    weight: Decimal


def _fail(code: str, *, failure_class: str) -> None:
    raise V1BuyCompilerDryRunError(code, failure_class=failure_class)


def _decimal(value: object, *, code: str, strictly_positive: bool = False) -> Decimal:
    if type(value) is not str:
        _fail(code, failure_class="compiler/contract")
    try:
        parsed = Decimal(value)
        normalize_decimal_string(parsed)
    except (InvalidOperation, MmiCanonicalizationError):
        _fail(code, failure_class="compiler/contract")
    if not parsed.is_finite() or parsed < 0 or (strictly_positive and parsed <= 0):
        _fail(code, failure_class="compiler/contract")
    return parsed


def _signed_decimal(value: object, *, code: str) -> Decimal:
    if type(value) is not str:
        _fail(code, failure_class="compiler/contract")
    try:
        parsed = Decimal(value)
        normalize_decimal_string(parsed)
    except (InvalidOperation, MmiCanonicalizationError):
        _fail(code, failure_class="compiler/contract")
    if not parsed.is_finite():
        _fail(code, failure_class="compiler/contract")
    return parsed


def _text(value: Decimal) -> str:
    try:
        return normalize_decimal_string(value)
    except MmiCanonicalizationError:
        _fail(
            "V1_BUY_DRY_RUN_ARITHMETIC_INVALID",
            failure_class="compiler/contract",
        )


def _coefficient_and_exponent(value: Decimal) -> tuple[int, int]:
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        _fail(
            "V1_BUY_DRY_RUN_ARITHMETIC_INVALID",
            failure_class="compiler/contract",
        )
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    return (-coefficient if sign else coefficient), exponent


def _decimal_from_units(coefficient: int, exponent: int) -> Decimal:
    sign = int(coefficient < 0)
    digits = tuple(int(digit) for digit in str(abs(coefficient)))
    return Decimal((sign, digits, exponent))


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    left_coefficient, left_exponent = _coefficient_and_exponent(left)
    right_coefficient, right_exponent = _coefficient_and_exponent(right)
    return _decimal_from_units(
        left_coefficient * right_coefficient,
        left_exponent + right_exponent,
    )


def _round_half_up_to_cents(value: Decimal) -> Decimal:
    coefficient, exponent = _coefficient_and_exponent(value)
    if coefficient <= 0:
        _fail(
            "V1_BUY_DRY_RUN_LIMIT_PRICE_INVALID",
            failure_class="compiler/contract",
        )
    if exponent >= _PRICE_QUANTUM_EXPONENT:
        return value
    divisor = 10 ** (_PRICE_QUANTUM_EXPONENT - exponent)
    rounded, remainder = divmod(coefficient, divisor)
    if remainder * 2 >= divisor:
        rounded += 1
    result = _decimal_from_units(rounded, _PRICE_QUANTUM_EXPONENT)
    if result <= 0:
        _fail(
            "V1_BUY_DRY_RUN_LIMIT_PRICE_INVALID",
            failure_class="compiler/contract",
        )
    return result


def _floor_nonnegative_ratio(numerator: Decimal, denominator: Decimal) -> int:
    numerator_coefficient, numerator_exponent = _coefficient_and_exponent(
        numerator
    )
    denominator_coefficient, denominator_exponent = _coefficient_and_exponent(
        denominator
    )
    if numerator_coefficient < 0 or denominator_coefficient <= 0:
        _fail(
            "V1_BUY_DRY_RUN_QUANTITY_INPUT_INVALID",
            failure_class="compiler/contract",
        )
    exponent_delta = numerator_exponent - denominator_exponent
    if exponent_delta >= 0:
        numerator_units = numerator_coefficient * (10 ** exponent_delta)
        denominator_units = denominator_coefficient
    else:
        numerator_units = numerator_coefficient
        denominator_units = denominator_coefficient * (10 ** -exponent_delta)
    return numerator_units // denominator_units


def _exact_sum(values: list[Decimal]) -> Decimal:
    return _proposal._sum(values)


def _strategy_payload(raw_bytes: bytes) -> Mapping[str, object]:
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
        payload = yaml.load(text, Loader=yaml.BaseLoader)
    except (UnicodeDecodeError, yaml.YAMLError):
        _fail(
            "V1_BUY_DRY_RUN_STRATEGY_SETTINGS_INVALID",
            failure_class="compiler/contract",
        )
    if type(payload) is not dict:
        _fail(
            "V1_BUY_DRY_RUN_STRATEGY_SETTINGS_INVALID",
            failure_class="compiler/contract",
        )
    return payload


def _pricing_role(
    payload: Mapping[str, object],
    *,
    ticker: str,
    deterministic_role: str,
) -> str:
    benchmark = payload.get("benchmark")
    core = payload.get("core_universe")
    satellite = payload.get("satellite_universe")
    if (
        type(benchmark) is not str
        or type(core) is not list
        or type(satellite) is not list
        or any(type(item) is not str for item in core + satellite)
        or benchmark != _proposal.V1_CORE_TICKERS[0]
        or len(core) != len(set(core))
        or len(satellite) != len(set(satellite))
        or set(core) != set(_proposal.V1_CORE_TICKERS)
        or set(satellite) != set(_proposal.V1_SATELLITE_TICKERS)
    ):
        _fail(
            "V1_BUY_DRY_RUN_ROLE_POLICY_INVALID",
            failure_class="compiler/contract",
        )
    if deterministic_role == _proposal.ROLE_CORE and ticker in core:
        return (
            _PRICING_ROLE_BENCHMARK
            if ticker == benchmark
            else _PRICING_ROLE_DIVERSIFIED
        )
    if deterministic_role == _proposal.ROLE_SATELLITE and ticker in satellite:
        return _PRICING_ROLE_SATELLITE
    _fail(
        "V1_BUY_DRY_RUN_ROLE_POLICY_MISMATCH",
        failure_class="compiler/contract",
    )


def _ladder(
    payload: Mapping[str, object],
    *,
    pricing_role: str,
) -> tuple[_LadderStep, ...]:
    template_map = payload.get("buy_order_template_map")
    if type(template_map) is not dict:
        _fail(
            "V1_BUY_DRY_RUN_LADDER_INVALID",
            failure_class="compiler/contract",
        )
    config = template_map.get(pricing_role)
    if type(config) is not dict:
        _fail(
            "V1_BUY_DRY_RUN_LADDER_INVALID",
            failure_class="compiler/contract",
        )
    ordered_steps = config.get("ordered_step_names")
    offsets = config.get("step_offsets_vs_anchor")
    weights = config.get("step_budget_weights")
    if (
        type(ordered_steps) is not list
        or not ordered_steps
        or any(type(step) is not str or not step for step in ordered_steps)
        or len(set(ordered_steps)) != len(ordered_steps)
        or type(offsets) is not dict
        or type(weights) is not dict
        or set(offsets) != set(ordered_steps)
        or set(weights) != set(ordered_steps)
        or config.get("default_plan_type") != "new_limit_ladder"
        or config.get("default_time_in_force") != "DAY"
    ):
        _fail(
            "V1_BUY_DRY_RUN_LADDER_INVALID",
            failure_class="compiler/contract",
        )
    steps: list[_LadderStep] = []
    for step_name in ordered_steps:
        offset = _signed_decimal(
            offsets[step_name],
            code="V1_BUY_DRY_RUN_LADDER_OFFSET_INVALID",
        )
        weight = _decimal(
            weights[step_name],
            code="V1_BUY_DRY_RUN_LADDER_WEIGHT_INVALID",
            strictly_positive=True,
        )
        if not Decimal(-1) < offset < Decimal(0):
            _fail(
                "V1_BUY_DRY_RUN_LADDER_OFFSET_INVALID",
                failure_class="compiler/contract",
            )
        steps.append(_LadderStep(name=step_name, offset=offset, weight=weight))
    if _exact_sum([step.weight for step in steps]) != Decimal(1):
        _fail(
            "V1_BUY_DRY_RUN_LADDER_WEIGHT_INVALID",
            failure_class="compiler/contract",
        )
    return tuple(steps)


def _required_mapping(value: object, *, code: str) -> Mapping[str, object]:
    if type(value) is not dict:
        _fail(code, failure_class="provenance/currentness")
    return value


def _upstream_provenance_or_currentness_failure(
    proposal: Mapping[str, object],
) -> str | None:
    """Preserve closed P1 valuation/provenance failure classifications."""
    if proposal.get("terminal_result") != _proposal.TERMINAL_NO_TRADE:
        return None
    if proposal.get("reason_code") == "INPUT_GENERATION_MISMATCH":
        return "INPUT_GENERATION_MISMATCH"
    diagnostic_codes = proposal.get("diagnostic_reason_codes")
    if (
        type(diagnostic_codes) is list
        and "US_EQUITY_SESSION_MARK_DATE_STALE" in diagnostic_codes
    ):
        return "US_EQUITY_SESSION_MARK_DATE_STALE"
    return None


def _validate_generation(
    evaluation: _proposal.H1V1ProposalEvaluation,
) -> tuple[
    dict[str, object],
    _proposal.H1V1ProposalStateRecognition,
    Mapping[str, object],
]:
    proposal = evaluation.proposal
    state = _proposal._recognize_h1_v1_proposal_state(proposal)
    if state is None:
        upstream_failure = _upstream_provenance_or_currentness_failure(proposal)
        if upstream_failure is not None:
            _fail(
                upstream_failure,
                failure_class="provenance/currentness",
            )
        _fail(
            "V1_BUY_DRY_RUN_PERMISSION_SUBJECT_NOT_READY",
            failure_class="availability/permission",
        )
    if (
        state.state != _proposal.H1_V1_DETERMINISTIC_PROPOSAL_READY
        or state.allowed_actions != ("HOLD", "NO_TRADE", "NEW_BUY")
        or not state.new_buy_permission
        or state.order_compilation_allowed
        or state.step3_allowed
        or state.step4_allowed
    ):
        _fail(
            "V1_BUY_DRY_RUN_PERMISSION_SUBJECT_NOT_READY",
            failure_class="availability/permission",
        )
    if (
        evaluation.strategy_source is None
        or evaluation.role_by_ticker is None
        or evaluation.budget is None
        or evaluation.exposure is None
        or evaluation.increment is None
    ):
        _fail(
            "V1_BUY_DRY_RUN_PROPOSAL_GENERATION_INCOMPLETE",
            failure_class="provenance/currentness",
        )
    bindings = _required_mapping(
        proposal.get("source_bindings"),
        code="V1_BUY_DRY_RUN_PROPOSAL_BINDINGS_INVALID",
    )
    source_bindings = {str(key): value for key, value in bindings.items()}
    return proposal, state, source_bindings


def _bound_selected_mark(
    evaluation: _proposal.H1V1ProposalEvaluation,
    *,
    ticker: str,
    deterministic_role: str,
    source_bindings: Mapping[str, object],
) -> Decimal:
    exposure = evaluation.exposure
    if exposure is None:
        _fail(
            "V1_BUY_DRY_RUN_PROPOSAL_GENERATION_INCOMPLETE",
            failure_class="provenance/currentness",
        )
    expected_bindings = {
        "valuation_capture_sha256": exposure.capture_artifact_sha256,
        "valuation_source_kind": exposure.capture_source_kind,
        "valuation_provider_id": exposure.capture_provider_id,
        "valuation_session_date": exposure.capture_session_date,
        "calendar_id": exposure.calendar_id,
        "calendar_schedule_sha256": exposure.calendar_schedule_sha256,
        "latest_completed_session_date": exposure.latest_completed_session_date,
        "valuation_freshness_status": exposure.freshness_status,
    }
    if any(source_bindings.get(key) != value for key, value in expected_bindings.items()):
        _fail(
            "V1_BUY_DRY_RUN_VALUATION_GENERATION_MISMATCH",
            failure_class="provenance/currentness",
        )
    if (
        exposure.freshness_status != MarkFreshnessStatus.FRESH.value
        or exposure.capture_session_date != exposure.latest_completed_session_date
        or exposure.mark_as_of_date != exposure.capture_session_date
        or exposure.calendar_id is None
        or exposure.calendar_schedule_sha256 is None
    ):
        _fail(
            "V1_BUY_DRY_RUN_VALUATION_NOT_CURRENT",
            failure_class="provenance/currentness",
        )
    selected = [position for position in exposure.positions if position.ticker == ticker]
    if (
        len(selected) != 1
        or ticker not in exposure.mark_ticker_domain
        or selected[0].classification != deterministic_role
    ):
        _fail(
            "V1_BUY_DRY_RUN_SELECTED_MARK_UNAVAILABLE",
            failure_class="provenance/currentness",
        )
    return _decimal(
        selected[0].mark,
        code="V1_BUY_DRY_RUN_SELECTED_MARK_INVALID",
        strictly_positive=True,
    )


def _ticker_exposures(
    evaluation: _proposal.H1V1ProposalEvaluation,
    *,
    selected_ticker: str,
    selected_notional: Decimal,
) -> tuple[
    tuple[H1V1BuyDryRunTickerExposure, ...],
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
]:
    budget = evaluation.budget
    exposure = evaluation.exposure
    roles = evaluation.role_by_ticker
    if budget is None or exposure is None or roles is None:
        _fail(
            "V1_BUY_DRY_RUN_PROPOSAL_GENERATION_INCOMPLETE",
            failure_class="provenance/currentness",
        )
    holdings: dict[str, Decimal] = {}
    for position in exposure.positions:
        if position.ticker in holdings:
            _fail(
                "V1_BUY_DRY_RUN_HOLDING_DUPLICATE",
                failure_class="compiler/contract",
            )
        holdings[position.ticker] = _decimal(
            position.market_value,
            code="V1_BUY_DRY_RUN_HOLDING_INVALID",
        )
    commitments: dict[str, Decimal] = {}
    for commitment in budget.current_open_buy_commitments:
        if commitment.ticker in commitments:
            _fail(
                "V1_BUY_DRY_RUN_COMMITMENT_DUPLICATE",
                failure_class="compiler/contract",
            )
        commitments[commitment.ticker] = _decimal(
            commitment.commitment,
            code="V1_BUY_DRY_RUN_COMMITMENT_INVALID",
        )
    tickers = sorted(set(holdings) | set(commitments))
    rows: list[H1V1BuyDryRunTickerExposure] = []
    y_values: list[Decimal] = []
    initial_alpha_values: list[Decimal] = []
    initial_core_values: list[Decimal] = []
    alpha_values: list[Decimal] = []
    core_values: list[Decimal] = []
    for ticker in tickers:
        role = roles.get(ticker)
        if role not in {
            _proposal.ROLE_CORE,
            _proposal.ROLE_SATELLITE,
            _proposal.ROLE_APPROVED_EXTENDED,
        }:
            _fail(
                "V1_BUY_DRY_RUN_EXPOSURE_ROLE_UNRESOLVED",
                failure_class="compiler/contract",
            )
        h_value = holdings.get(ticker, Decimal(0))
        e_value = commitments.get(ticker, Decimal(0))
        n_value = selected_notional if ticker == selected_ticker else Decimal(0)
        y_value = _exact_sum([e_value, n_value])
        initial_d_value = _exact_sum([h_value, e_value])
        d_value = _exact_sum([h_value, y_value])
        y_values.append(y_value)
        if role == _proposal.ROLE_CORE:
            initial_core_values.append(initial_d_value)
            core_values.append(d_value)
        else:
            initial_alpha_values.append(initial_d_value)
            alpha_values.append(d_value)
        rows.append(
            H1V1BuyDryRunTickerExposure(
                ticker=ticker,
                role=role,
                holdings_exposure=_text(h_value),
                retained_buy_commitment=_text(e_value),
                new_candidate_notional=_text(n_value),
                final_unfilled_buy_commitment=_text(y_value),
                final_projected_exposure=_text(d_value),
            )
        )
    return (
        tuple(rows),
        _exact_sum(y_values),
        _exact_sum(alpha_values),
        _exact_sum(core_values),
        _exact_sum(initial_alpha_values),
        _exact_sum(initial_core_values),
    )


def evaluate_h1_v1_buy_compiler_dry_run() -> H1V1BuyCompilerDryRunResult:
    """Evaluate one current, permission-checked, side-effect-free BUY dry-run."""
    evaluation = _proposal.evaluate_h1_v1_proposal_generation()
    proposal, _state, source_bindings = _validate_generation(evaluation)
    selected_ticker = proposal["selected_ticker"]
    target_text = proposal["target_increment"]
    candidates = proposal["candidates"]
    capacity = proposal["capacity"]
    if (
        type(selected_ticker) is not str
        or type(candidates) is not list
        or type(capacity) is not dict
    ):
        _fail(
            "V1_BUY_DRY_RUN_PROPOSAL_CONTRACT_INVALID",
            failure_class="compiler/contract",
        )
    target = _decimal(
        target_text,
        code="V1_BUY_DRY_RUN_TARGET_INVALID",
        strictly_positive=True,
    )
    selected_rows = [
        row
        for row in candidates
        if type(row) is dict and row.get("ticker") == selected_ticker
    ]
    if len(selected_rows) != 1:
        _fail(
            "V1_BUY_DRY_RUN_SELECTED_CANDIDATE_INVALID",
            failure_class="compiler/contract",
        )
    deterministic_role = selected_rows[0].get("role")
    if deterministic_role not in {_proposal.ROLE_CORE, _proposal.ROLE_SATELLITE}:
        _fail(
            "V1_BUY_DRY_RUN_SELECTED_CANDIDATE_INVALID",
            failure_class="compiler/contract",
        )
    if evaluation.strategy_source is None:
        _fail(
            "V1_BUY_DRY_RUN_PROPOSAL_GENERATION_INCOMPLETE",
            failure_class="provenance/currentness",
        )
    strategy_sha = evaluation.strategy_source.source_record.get("observed_sha256")
    strategy_record_identity = evaluation.strategy_source.source_record.get(
        "source_record_identity_sha256"
    )
    if (
        strategy_sha != source_bindings.get("strategy_source_sha256")
        or strategy_record_identity
        != source_bindings.get("strategy_source_record_identity_sha256")
    ):
        _fail(
            "V1_BUY_DRY_RUN_STRATEGY_GENERATION_MISMATCH",
            failure_class="provenance/currentness",
        )
    settings = _strategy_payload(evaluation.strategy_source.raw_bytes)
    pricing_role = _pricing_role(
        settings,
        ticker=selected_ticker,
        deterministic_role=deterministic_role,
    )
    steps = _ladder(settings, pricing_role=pricing_role)
    mark = _bound_selected_mark(
        evaluation,
        ticker=selected_ticker,
        deterministic_role=deterministic_role,
        source_bindings=source_bindings,
    )

    legs: list[H1V1BuyDryRunLeg] = []
    notionals: list[Decimal] = []
    for step in steps:
        factor = _exact_sum([Decimal(1), step.offset])
        raw_price = _exact_product(mark, factor)
        rounded_price = _round_half_up_to_cents(raw_price)
        leg_budget = _exact_product(target, step.weight)
        quantity = _floor_nonnegative_ratio(leg_budget, rounded_price)
        if quantity == 0:
            continue
        notional = _exact_product(rounded_price, Decimal(quantity))
        if notional <= 0 or notional > leg_budget:
            _fail(
                "V1_BUY_DRY_RUN_LEG_INVARIANT_FAILED",
                failure_class="compiler/contract",
            )
        notionals.append(notional)
        legs.append(
            H1V1BuyDryRunLeg(
                step_name=step.name,
                allocation_weight=_text(step.weight),
                limit_offset_from_mark=_text(step.offset),
                rounded_limit_price=_text(rounded_price),
                whole_share_quantity=quantity,
                candidate_notional=_text(notional),
            )
        )
    total_new_notional = _exact_sum(notionals)
    if total_new_notional < 0 or total_new_notional > target:
        _fail(
            "V1_BUY_DRY_RUN_TARGET_BOUND_FAILED",
            failure_class="compiler/contract",
        )

    (
        ticker_exposures,
        final_y,
        final_a,
        final_z,
        initial_a,
        initial_z,
    ) = _ticker_exposures(
        evaluation,
        selected_ticker=selected_ticker,
        selected_notional=total_new_notional,
    )
    x_value = _decimal(
        capacity.get("X"),
        code="V1_BUY_DRY_RUN_X_INVALID",
    )
    r_value = _decimal(
        capacity.get("R"),
        code="V1_BUY_DRY_RUN_R_INVALID",
    )
    h_value = _decimal(
        capacity.get("H"),
        code="V1_BUY_DRY_RUN_H_INVALID",
    )
    e_value = _decimal(
        capacity.get("E"),
        code="V1_BUY_DRY_RUN_E_INVALID",
    )
    a_initial_value = _decimal(
        capacity.get("A_initial"),
        code="V1_BUY_DRY_RUN_A_INITIAL_INVALID",
    )
    z_initial_value = _decimal(
        capacity.get("Z_initial"),
        code="V1_BUY_DRY_RUN_Z_INITIAL_INVALID",
    )
    if evaluation.exposure is None or evaluation.budget is None:
        _fail(
            "V1_BUY_DRY_RUN_PROPOSAL_GENERATION_INCOMPLETE",
            failure_class="provenance/currentness",
        )
    if (
        _exact_sum(
            [
                _decimal(
                    position.market_value,
                    code="V1_BUY_DRY_RUN_HOLDING_INVALID",
                )
                for position in evaluation.exposure.positions
            ]
        )
        != h_value
        or _exact_sum(
            [
                _decimal(
                    commitment.commitment,
                    code="V1_BUY_DRY_RUN_COMMITMENT_INVALID",
                )
                for commitment in evaluation.budget.current_open_buy_commitments
            ]
        )
        != e_value
        or initial_a != a_initial_value
        or initial_z != z_initial_value
        or _decimal(
            evaluation.budget.budget_ceiling_source.maximum_total_unfilled_buy_commitment,
            code="V1_BUY_DRY_RUN_X_SOURCE_INVALID",
        )
        != x_value
        or _decimal(
            evaluation.increment.increment_cap_basis,
            code="V1_BUY_DRY_RUN_R_SOURCE_INVALID",
        )
        != r_value
        or evaluation.budget.budget_ceiling_source.observed_sha256
        != source_bindings.get("x_source_sha256")
        or evaluation.increment.increment_fraction_source.observed_sha256
        != source_bindings.get("r_source_sha256")
        or evaluation.exposure.portfolio_source_sha256
        != source_bindings.get("portfolio_source_sha256")
        or evaluation.exposure.portfolio_source_record_identity_sha256
        != source_bindings.get("portfolio_source_record_identity_sha256")
        or evaluation.exposure.policy_projection_identity_sha256
        != source_bindings.get("holdings_policy_projection_identity_sha256")
    ):
        _fail(
            "V1_BUY_DRY_RUN_PROPOSAL_GENERATION_MISMATCH",
            failure_class="provenance/currentness",
        )
    if (
        final_y != _exact_sum([e_value, total_new_notional])
        or final_y > x_value
        or final_y > r_value
    ):
        _fail(
            "V1_BUY_DRY_RUN_POSTCOMPILE_CAP_FAILED",
            failure_class="compiler/contract",
        )
    if final_a > final_z:
        _fail(
            "V1_BUY_DRY_RUN_POSTCOMPILE_AZ_FAILED",
            failure_class="compiler/contract",
        )

    exposure = evaluation.exposure
    terminal = DRY_RUN_POSITIVE if legs else DRY_RUN_HOLD
    reason_code = "POSITIVE_WHOLE_SHARE_CANDIDATE" if legs else "NO_WHOLE_SHARE_FEASIBILITY"
    return H1V1BuyCompilerDryRunResult(
        terminal_outcome=terminal,
        reason_code=reason_code,
        selected_ticker=selected_ticker,
        deterministic_role=deterministic_role,
        pricing_role=pricing_role,
        valuation_capture_sha256=exposure.capture_artifact_sha256,
        valuation_provider_id=exposure.capture_provider_id,
        valuation_session_date=exposure.capture_session_date,
        calendar_id=exposure.calendar_id or "",
        calendar_schedule_sha256=exposure.calendar_schedule_sha256 or "",
        validated_mark=_text(mark),
        target_increment=_text(target),
        candidate_legs=tuple(legs),
        total_new_candidate_notional=_text(total_new_notional),
        postcompile_total_unfilled_buy_commitment=_text(final_y),
        postcompile_alpha_exposure=_text(final_a),
        postcompile_core_exposure=_text(final_z),
        ticker_exposures=ticker_exposures,
    )
