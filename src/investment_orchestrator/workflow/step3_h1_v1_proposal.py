"""Report-only deterministic H1 V1 BUY proposal.

This workflow implements only the proposal calculations closed by
``docs/v1_buy_only_policy_v1.md``.  It creates no state, permission, gate,
publication, order, or execution authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Final

from investment_orchestrator.common.io import atomic_write_text
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi.canonical import (
    MmiCanonicalizationError,
    normalize_decimal_string,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MmiCapturedSource,
    MmiProjectionResultCategory,
    MmiSourceRole,
    begin_mmi_projection_run,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.source_capture import (
    MmiStableSourceDigestError,
    capture_current_mmi_source,
    capture_current_mmi_stable_source_digest,
)
from investment_orchestrator.observability import (
    report_only_budget_capacity as _budget_capacity,
    report_only_holdings_exposure as _holdings_exposure,
    report_only_increment_capacity as _increment_capacity,
)
from investment_orchestrator.workflow import step2_h1_currentness as _h1_currentness


V1_PROPOSAL_SCHEMA_VERSION: Final = "step3_h1_v1_buy_only_proposal_v1"
V1_POLICY_CONTRACT_VERSION: Final = "v1_buy_only_policy_v1"
V1_PROPOSAL_ARTIFACT_RELATIVE_PATH: Final = (
    "artifacts/current/step3_h1_v1_proposal/"
    "h1_v1_buy_only_proposal.json"
)

TERMINAL_HOLD: Final = "HOLD"
TERMINAL_NO_TRADE: Final = "NO_TRADE"
TERMINAL_POSITIVE_CANDIDATE: Final = "POSITIVE_INCREMENT_CANDIDATE"

DISPOSITION_EXCLUDE: Final = "EXCLUDE"
DISPOSITION_UNRESOLVED: Final = "UNRESOLVED"
DISPOSITION_MAINTAIN_ONLY: Final = "MAINTAIN_ONLY"
DISPOSITION_INCREMENT_ELIGIBLE: Final = "INCREMENT_ELIGIBLE"

ROLE_CORE: Final = "CORE"
ROLE_SATELLITE: Final = "SATELLITE"
ROLE_APPROVED_EXTENDED: Final = "APPROVED_EXTENDED"

V1_CORE_TICKERS: Final = ("QQQ", "VOO", "VTI", "VT")
V1_SATELLITE_TICKERS: Final = ("SMH", "IGV")
V1_PRIORITY_ORDER: Final = V1_CORE_TICKERS + V1_SATELLITE_TICKERS
_V1_BASE_TICKERS: Final = frozenset(V1_PRIORITY_ORDER)
_RECOGNIZED_EXPOSURE_ROLES: Final = frozenset(
    {ROLE_CORE, ROLE_SATELLITE, ROLE_APPROVED_EXTENDED}
)
class V1ProposalInputError(RuntimeError):
    """Controlled failure to obtain one validated deterministic input owner."""

    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        closed_reasons = reason_codes or ("V1_PROPOSAL_INPUT_INVALID",)
        super().__init__("; ".join(closed_reasons))
        self.reason_codes = closed_reasons


@dataclass(frozen=True, slots=True)
class _CurrentSourceSnapshot:
    strategy_source: MmiCapturedSource
    portfolio_source: MmiCapturedSource
    strategy_source_sha256: str
    strategy_source_record_identity_sha256: str
    portfolio_source_sha256: str
    portfolio_source_record_identity_sha256: str
    universe_projection_identity_sha256: str
    role_by_ticker: Mapping[str, str]


def h1_v1_proposal_path() -> Path:
    """Return the one fixed report-only proposal path."""
    return repo_root() / V1_PROPOSAL_ARTIFACT_RELATIVE_PATH


def _source_record_text(source: MmiCapturedSource, field: str) -> str:
    value = source.source_record.get(field)
    if type(value) is not str:
        raise V1ProposalInputError(("V1_PROPOSAL_SOURCE_RECORD_INVALID",))
    return value


def _capture_current_source(role: MmiSourceRole) -> tuple[MmiCapturedSource, str]:
    try:
        digest = capture_current_mmi_stable_source_digest(role)
    except MmiStableSourceDigestError as exc:
        raise V1ProposalInputError((exc.code,)) from None
    capture = capture_current_mmi_source(
        role,
        expected_source_sha256=digest.observed_sha256,
    )
    if (
        not capture.valid
        or capture.authority_effect != AUTHORITY_EFFECT_NONE
        or capture.source is None
        or capture.source.role is not role
    ):
        raise V1ProposalInputError(
            capture.reason_codes or ("V1_PROPOSAL_SOURCE_CAPTURE_INVALID",)
        )
    observed_sha256 = _source_record_text(capture.source, "observed_sha256")
    if observed_sha256 != digest.observed_sha256:
        raise V1ProposalInputError(("V1_PROPOSAL_SOURCE_GENERATION_MISMATCH",))
    return capture.source, observed_sha256


def _load_current_source_snapshot() -> _CurrentSourceSnapshot:
    strategy_source, strategy_sha256 = _capture_current_source(
        MmiSourceRole.STRATEGY_SETTINGS
    )
    portfolio_source, portfolio_sha256 = _capture_current_source(
        MmiSourceRole.PORTFOLIO_SNAPSHOT
    )
    policy_result = build_mmi_policy_projection(
        strategy_source,
        run_context=begin_mmi_projection_run(),
    )
    if (
        not policy_result.valid
        or policy_result.authority_effect != AUTHORITY_EFFECT_NONE
        or not isinstance(policy_result.projection, Mapping)
    ):
        raise V1ProposalInputError(
            policy_result.reason_codes
            or ("V1_PROPOSAL_POLICY_PROJECTION_INVALID",)
        )
    policy_projection = policy_result.projection
    universe = policy_projection.get("universe_projection")
    if not isinstance(universe, Mapping):
        raise V1ProposalInputError(("V1_PROPOSAL_POLICY_PROJECTION_INVALID",))
    universe_identity = universe.get("universe_projection_identity_sha256")
    if type(universe_identity) is not str:
        raise V1ProposalInputError(("V1_PROPOSAL_POLICY_PROJECTION_INVALID",))
    raw_roles = universe.get("role_by_ticker")
    if not isinstance(raw_roles, Mapping):
        raise V1ProposalInputError(("V1_PROPOSAL_ROLE_MAP_INVALID",))
    role_by_ticker: dict[str, str] = {}
    for ticker, role in raw_roles.items():
        if type(ticker) is not str or role not in _RECOGNIZED_EXPOSURE_ROLES:
            raise V1ProposalInputError(("V1_PROPOSAL_ROLE_MAP_INVALID",))
        role_by_ticker[ticker] = role
    expected_v1_roles = {
        **{ticker: ROLE_CORE for ticker in V1_CORE_TICKERS},
        **{ticker: ROLE_SATELLITE for ticker in V1_SATELLITE_TICKERS},
    }
    if any(role_by_ticker.get(ticker) != role for ticker, role in expected_v1_roles.items()):
        raise V1ProposalInputError(("V1_PROPOSAL_V1_BASE_ROLE_MISMATCH",))
    return _CurrentSourceSnapshot(
        strategy_source=strategy_source,
        portfolio_source=portfolio_source,
        strategy_source_sha256=strategy_sha256,
        strategy_source_record_identity_sha256=_source_record_text(
            strategy_source,
            "source_record_identity_sha256",
        ),
        portfolio_source_sha256=portfolio_sha256,
        portfolio_source_record_identity_sha256=_source_record_text(
            portfolio_source,
            "source_record_identity_sha256",
        ),
        universe_projection_identity_sha256=universe_identity,
        role_by_ticker=role_by_ticker,
    )


def _decimal(value: object, *, field: str) -> Decimal:
    if type(value) is not str:
        raise V1ProposalInputError((f"V1_PROPOSAL_{field}_INVALID",))
    try:
        parsed = Decimal(value)
        normalized = normalize_decimal_string(parsed)
    except (InvalidOperation, MmiCanonicalizationError):
        raise V1ProposalInputError((f"V1_PROPOSAL_{field}_INVALID",)) from None
    if not parsed.is_finite() or parsed < 0 or normalized != value:
        raise V1ProposalInputError((f"V1_PROPOSAL_{field}_INVALID",))
    return parsed


def _decimal_text(value: Decimal) -> str:
    try:
        return normalize_decimal_string(value)
    except MmiCanonicalizationError:
        raise V1ProposalInputError(("V1_PROPOSAL_ARITHMETIC_INVALID",)) from None


def _sum(values: list[Decimal]) -> Decimal:
    """Return the exact finite-decimal sum without using Decimal context."""
    if not values:
        return Decimal(0)
    components: list[tuple[int, int]] = []
    for value in values:
        sign, digits, exponent = value.as_tuple()
        if not isinstance(exponent, int):
            raise V1ProposalInputError(("V1_PROPOSAL_ARITHMETIC_INVALID",))
        coefficient = 0
        for digit in digits:
            coefficient = coefficient * 10 + digit
        components.append((-coefficient if sign else coefficient, exponent))
    common_exponent = min(exponent for _coefficient, exponent in components)
    total_coefficient = sum(
        coefficient * (10 ** (exponent - common_exponent))
        for coefficient, exponent in components
    )
    sign = int(total_coefficient < 0)
    result_digits = tuple(int(digit) for digit in str(abs(total_coefficient)))
    return Decimal((sign, result_digits, common_exponent))


def _base_source_bindings(
    h1_evaluation: _h1_currentness.H1CurrentContextEvaluation,
) -> dict[str, object]:
    context = h1_evaluation.context
    return {
        "h1_rendered_prompt_sha256": h1_evaluation.rendered_prompt_sha256,
        "h1_raw_response_sha256": h1_evaluation.raw_response_sha256,
        "h1_evidence_entry_identities_sha256": (
            list(context.evidence_entry_identities_sha256)
            if context is not None
            else []
        ),
        "h1_report_evidence_references": (
            list(context.evidence_references) if context is not None else []
        ),
        "strategy_source_sha256": None,
        "strategy_source_record_identity_sha256": None,
        "portfolio_source_sha256": None,
        "portfolio_source_record_identity_sha256": None,
        "role_universe_projection_identity_sha256": None,
        "holdings_policy_projection_identity_sha256": None,
        "portfolio_scope_id": None,
        "holdings_observation_date": None,
        "valuation_capture_sha256": None,
        "valuation_source_kind": None,
        "valuation_provider_id": None,
        "valuation_session_date": None,
        "valuation_trusted_evaluation_timestamp_utc": None,
        "calendar_id": None,
        "calendar_schedule_sha256": None,
        "latest_completed_session_date": None,
        "valuation_freshness_status": None,
        "x_source_sha256": None,
        "r_source_sha256": None,
    }


def _empty_capacity() -> dict[str, str | None]:
    return {
        "X": None,
        "H": None,
        "E": None,
        "R": None,
        "C": None,
        "A_initial": None,
        "Z_initial": None,
    }


def _proposal(
    *,
    h1_evaluation: _h1_currentness.H1CurrentContextEvaluation,
    terminal_result: str,
    reason_code: str,
    diagnostic_reason_codes: tuple[str, ...],
    source_bindings: Mapping[str, object],
    capacity: Mapping[str, str | None],
    candidates: list[dict[str, object]],
    selected_ticker: str | None,
    target_increment: str | None,
) -> dict[str, object]:
    return {
        "schema_version": V1_PROPOSAL_SCHEMA_VERSION,
        "policy_contract_version": V1_POLICY_CONTRACT_VERSION,
        "observed_on": h1_evaluation.observed_on,
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "not_authorization": True,
        "new_buy_permission": False,
        "order_compilation_allowed": False,
        "terminal_result": terminal_result,
        "reason_code": reason_code,
        "diagnostic_reason_codes": list(diagnostic_reason_codes),
        "source_bindings": dict(source_bindings),
        "capacity": dict(capacity),
        "candidates": candidates,
        "selected_ticker": selected_ticker,
        "target_increment": target_increment,
    }


def _persist_proposal(proposal: Mapping[str, object]) -> Path:
    path = h1_v1_proposal_path()
    atomic_write_text(
        path,
        json.dumps(dict(proposal), ensure_ascii=False, indent=2) + "\n",
    )
    return path


def _partial_candidates(
    exposure_projection: _holdings_exposure.ExposureProjection | None,
) -> list[dict[str, object]]:
    if exposure_projection is None:
        return []
    rows: list[dict[str, object]] = []
    for position in exposure_projection.positions:
        role = position.classification
        unresolved = role not in _RECOGNIZED_EXPOSURE_ROLES
        rows.append(
            {
                "ticker": position.ticker,
                "role": role if not unresolved else "UNRESOLVED",
                "disposition": (
                    DISPOSITION_UNRESOLVED
                    if unresolved
                    else (
                        DISPOSITION_MAINTAIN_ONLY
                        if position.ticker in _V1_BASE_TICKERS
                        else DISPOSITION_EXCLUDE
                    )
                ),
                "evidence_coverage_identities": [],
                "priority": None,
            }
        )
    priority_index = {ticker: index for index, ticker in enumerate(V1_PRIORITY_ORDER)}
    return sorted(
        rows,
        key=lambda row: (
            priority_index.get(str(row["ticker"]), len(priority_index)),
            str(row["ticker"]),
        ),
    )


def _owner_diagnostics(*results: object) -> tuple[str, ...]:
    reasons: set[str] = set()
    for result in results:
        values = getattr(result, "reason_codes", ())
        if isinstance(values, tuple):
            reasons.update(value for value in values if type(value) is str)
    return tuple(sorted(reasons))


def _validated_projection_inputs(
    *,
    snapshot: _CurrentSourceSnapshot,
    budget_result: _budget_capacity.BudgetCapacityObservationResult,
    exposure_result: _holdings_exposure.ExposureObservationResult,
    increment_result: _increment_capacity.IncrementCapacityObservationResult,
) -> tuple[
    _budget_capacity.BudgetCapacityProjection,
    _holdings_exposure.ExposureProjection,
    _increment_capacity.IncrementCapacityProjection,
]:
    if (
        budget_result.status
        is not _budget_capacity.BudgetCapacityObservationStatus.VALID_REPORT_ONLY
        or budget_result.projection is None
        or exposure_result.status
        is not _holdings_exposure.ExposureObservationStatus.VALID_REPORT_ONLY
        or exposure_result.projection is None
        or increment_result.status
        is not _increment_capacity.IncrementCapacityObservationStatus.VALID_REPORT_ONLY
        or increment_result.projection is None
    ):
        raise V1ProposalInputError(("V1_PROPOSAL_INPUT_OWNER_NOT_VALID",))
    budget = budget_result.projection
    exposure = exposure_result.projection
    increment = increment_result.projection
    if not (
        budget.portfolio_source_sha256
        == exposure.portfolio_source_sha256
        == increment.portfolio_source_sha256
        == snapshot.portfolio_source_sha256
        and budget.portfolio_source_record_identity_sha256
        == exposure.portfolio_source_record_identity_sha256
        == increment.portfolio_source_record_identity_sha256
        == snapshot.portfolio_source_record_identity_sha256
        and exposure.capture_artifact_sha256 == increment.capture_artifact_sha256
        and exposure.capture_session_date == increment.capture_session_date
        and exposure.calendar_id == increment.calendar_id
        and exposure.calendar_schedule_sha256
        == increment.calendar_schedule_sha256
        and exposure.policy_projection_identity_sha256
        == increment.policy_projection_identity_sha256
        and exposure.total_market_value == increment.total_holdings_exposure
    ):
        raise V1ProposalInputError(("V1_PROPOSAL_INPUT_GENERATION_MISMATCH",))
    return budget, exposure, increment


def _complete_proposal(
    *,
    h1_evaluation: _h1_currentness.H1CurrentContextEvaluation,
    snapshot: _CurrentSourceSnapshot,
    budget: _budget_capacity.BudgetCapacityProjection,
    exposure: _holdings_exposure.ExposureProjection,
    increment: _increment_capacity.IncrementCapacityProjection,
) -> dict[str, object]:
    bindings = _base_source_bindings(h1_evaluation)
    bindings.update(
        {
            "strategy_source_sha256": snapshot.strategy_source_sha256,
            "strategy_source_record_identity_sha256": (
                snapshot.strategy_source_record_identity_sha256
            ),
            "portfolio_source_sha256": snapshot.portfolio_source_sha256,
            "portfolio_source_record_identity_sha256": (
                snapshot.portfolio_source_record_identity_sha256
            ),
            "role_universe_projection_identity_sha256": (
                snapshot.universe_projection_identity_sha256
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
            "r_source_sha256": (
                increment.increment_fraction_source.observed_sha256
            ),
        }
    )

    x_value = _decimal(
        budget.budget_ceiling_source.maximum_total_unfilled_buy_commitment,
        field="X",
    )
    h_value = _decimal(exposure.total_market_value, field="H")
    e_value = _decimal(budget.total_current_unfilled_buy_commitment, field="E")
    r_cap = _decimal(increment.increment_cap_basis, field="R")

    holdings_by_ticker: dict[str, Decimal] = {}
    for position in exposure.positions:
        if position.ticker in holdings_by_ticker:
            raise V1ProposalInputError(("V1_PROPOSAL_HOLDING_DUPLICATE",))
        holdings_by_ticker[position.ticker] = _decimal(
            position.market_value,
            field="H_I",
        )
        if snapshot.role_by_ticker.get(position.ticker) != position.classification:
            raise V1ProposalInputError(("V1_PROPOSAL_HOLDING_ROLE_MISMATCH",))
    if _sum(list(holdings_by_ticker.values())) != h_value:
        raise V1ProposalInputError(("V1_PROPOSAL_H_TOTAL_MISMATCH",))

    commitments_by_ticker: dict[str, Decimal] = {}
    for commitment in budget.current_open_buy_commitments:
        if commitment.ticker in commitments_by_ticker:
            raise V1ProposalInputError(("V1_PROPOSAL_COMMITMENT_DUPLICATE",))
        commitments_by_ticker[commitment.ticker] = _decimal(
            commitment.commitment,
            field="E_I",
        )
    if _sum(list(commitments_by_ticker.values())) != e_value:
        raise V1ProposalInputError(("V1_PROPOSAL_E_TOTAL_MISMATCH",))

    exposure_tickers = set(holdings_by_ticker) | set(commitments_by_ticker)
    unresolved_tickers = sorted(
        ticker
        for ticker in exposure_tickers
        if snapshot.role_by_ticker.get(ticker) not in _RECOGNIZED_EXPOSURE_ROLES
    )
    a_initial = Decimal(0)
    z_initial = Decimal(0)
    if not unresolved_tickers:
        core_projected: list[Decimal] = []
        alpha_projected: list[Decimal] = []
        for ticker in sorted(exposure_tickers):
            projected = _sum(
                [
                    holdings_by_ticker.get(ticker, Decimal(0)),
                    commitments_by_ticker.get(ticker, Decimal(0)),
                ]
            )
            if snapshot.role_by_ticker[ticker] == ROLE_CORE:
                core_projected.append(projected)
            else:
                alpha_projected.append(projected)
        z_initial = _sum(core_projected)
        a_initial = _sum(alpha_projected)

    capacity = {
        "X": _decimal_text(x_value),
        "H": _decimal_text(h_value),
        "E": _decimal_text(e_value),
        "R": _decimal_text(r_cap),
        "C": None,
        "A_initial": (
            None if unresolved_tickers else _decimal_text(a_initial)
        ),
        "Z_initial": (
            None if unresolved_tickers else _decimal_text(z_initial)
        ),
    }

    context = h1_evaluation.context
    cited_entries_by_ticker: dict[str, tuple[str, ...]] = {}
    if context is not None:
        cited = frozenset(context.evidence_references)
        for ticker in holdings_by_ticker:
            cited_entries_by_ticker[ticker] = tuple(
                sorted(
                    entry.source_entry_identity_sha256
                    for entry in context.current_lh2_payload.sources
                    if entry.source_entry_identity_sha256 in cited
                    and ticker in entry.tickers
                )
            )

    global_increase_blocked = (
        context is None
        or bool(unresolved_tickers)
        or e_value > x_value
        or e_value > r_cap
        or (not unresolved_tickers and a_initial > z_initial)
    )
    capacity_value: Decimal | None = None
    az_headroom: Decimal | None = None
    if not global_increase_blocked:
        capacity_value = _sum(
            [min(x_value, r_cap), e_value.copy_negate()]
        )
        az_headroom = _sum([z_initial, a_initial.copy_negate()])
        capacity["C"] = _decimal_text(capacity_value)

    candidates: list[dict[str, object]] = []
    for ticker in holdings_by_ticker:
        role = snapshot.role_by_ticker.get(ticker)
        evidence_ids = cited_entries_by_ticker.get(ticker, ())
        if role not in _RECOGNIZED_EXPOSURE_ROLES:
            disposition = DISPOSITION_UNRESOLVED
        elif ticker not in _V1_BASE_TICKERS:
            disposition = DISPOSITION_EXCLUDE
        elif (
            global_increase_blocked
            or not evidence_ids
            or capacity_value is None
            or capacity_value <= 0
            or (
                role == ROLE_SATELLITE
                and (az_headroom is None or az_headroom <= 0)
            )
        ):
            disposition = DISPOSITION_MAINTAIN_ONLY
        else:
            disposition = DISPOSITION_INCREMENT_ELIGIBLE
        priority = None
        if disposition == DISPOSITION_INCREMENT_ELIGIBLE:
            priority = "PREFERRED" if role == ROLE_CORE else "STANDARD"
        candidates.append(
            {
                "ticker": ticker,
                "role": role if role is not None else "UNRESOLVED",
                "disposition": disposition,
                "evidence_coverage_identities": list(evidence_ids),
                "priority": priority,
            }
        )
    priority_index = {ticker: index for index, ticker in enumerate(V1_PRIORITY_ORDER)}
    candidates.sort(
        key=lambda row: (
            priority_index.get(str(row["ticker"]), len(priority_index)),
            str(row["ticker"]),
        )
    )

    if context is None:
        return _proposal(
            h1_evaluation=h1_evaluation,
            terminal_result=TERMINAL_NO_TRADE,
            reason_code="H1_CONTEXT_NOT_CURRENT",
            diagnostic_reason_codes=(
                h1_evaluation.reason_code or "H1_CONTEXT_NOT_CURRENT",
            ),
            source_bindings=bindings,
            capacity=capacity,
            candidates=candidates,
            selected_ticker=None,
            target_increment=None,
        )
    if unresolved_tickers:
        return _proposal(
            h1_evaluation=h1_evaluation,
            terminal_result=TERMINAL_NO_TRADE,
            reason_code="REQUIRED_EXPOSURE_ROLE_UNRESOLVED",
            diagnostic_reason_codes=tuple(
                f"UNRESOLVED_EXPOSURE:{ticker}" for ticker in unresolved_tickers
            ),
            source_bindings=bindings,
            capacity=capacity,
            candidates=candidates,
            selected_ticker=None,
            target_increment=None,
        )
    if e_value > x_value:
        terminal_reason = "EXISTING_COMMITMENT_EXCEEDS_X"
    elif e_value > r_cap:
        terminal_reason = "EXISTING_COMMITMENT_EXCEEDS_R"
    elif a_initial > z_initial:
        terminal_reason = "INITIAL_ALPHA_EXCEEDS_CORE"
    else:
        terminal_reason = ""
    if terminal_reason:
        return _proposal(
            h1_evaluation=h1_evaluation,
            terminal_result=TERMINAL_NO_TRADE,
            reason_code=terminal_reason,
            diagnostic_reason_codes=(),
            source_bindings=bindings,
            capacity=capacity,
            candidates=candidates,
            selected_ticker=None,
            target_increment=None,
        )
    if capacity_value is None:
        raise V1ProposalInputError(("V1_PROPOSAL_CAPACITY_INVARIANT_FAILED",))
    if capacity_value == 0:
        return _proposal(
            h1_evaluation=h1_evaluation,
            terminal_result=TERMINAL_HOLD,
            reason_code="NO_SHARED_CAPACITY",
            diagnostic_reason_codes=(),
            source_bindings=bindings,
            capacity=capacity,
            candidates=candidates,
            selected_ticker=None,
            target_increment=None,
        )
    eligible = [
        row
        for row in candidates
        if row["disposition"] == DISPOSITION_INCREMENT_ELIGIBLE
    ]
    if not eligible:
        return _proposal(
            h1_evaluation=h1_evaluation,
            terminal_result=TERMINAL_HOLD,
            reason_code="NO_INCREMENT_ELIGIBLE_TICKER",
            diagnostic_reason_codes=(),
            source_bindings=bindings,
            capacity=capacity,
            candidates=candidates,
            selected_ticker=None,
            target_increment=None,
        )
    selected = eligible[0]
    target = capacity_value
    if selected["role"] == ROLE_SATELLITE:
        if az_headroom is None:
            raise V1ProposalInputError(
                ("V1_PROPOSAL_AZ_HEADROOM_INVARIANT_FAILED",)
            )
        target = min(capacity_value, az_headroom)
    if target <= 0:
        return _proposal(
            h1_evaluation=h1_evaluation,
            terminal_result=TERMINAL_HOLD,
            reason_code="SELECTED_TARGET_NOT_POSITIVE",
            diagnostic_reason_codes=(),
            source_bindings=bindings,
            capacity=capacity,
            candidates=candidates,
            selected_ticker=None,
            target_increment=None,
        )
    return _proposal(
        h1_evaluation=h1_evaluation,
        terminal_result=TERMINAL_POSITIVE_CANDIDATE,
        reason_code="POSITIVE_INCREMENT_CANDIDATE",
        diagnostic_reason_codes=(),
        source_bindings=bindings,
        capacity=capacity,
        candidates=candidates,
        selected_ticker=str(selected["ticker"]),
        target_increment=_decimal_text(target),
    )


def build_h1_v1_proposal_workflow() -> Path:
    """Build and atomically persist one non-authoritative V1 proposal."""
    h1_evaluation = _h1_currentness.evaluate_current_h1_context()
    bindings = _base_source_bindings(h1_evaluation)
    try:
        snapshot = _load_current_source_snapshot()
    except V1ProposalInputError as exc:
        return _persist_proposal(
            _proposal(
                h1_evaluation=h1_evaluation,
                terminal_result=TERMINAL_NO_TRADE,
                reason_code="INPUT_SOURCE_CONTRACT_NOT_VALID",
                diagnostic_reason_codes=exc.reason_codes,
                source_bindings=bindings,
                capacity=_empty_capacity(),
                candidates=[],
                selected_ticker=None,
                target_increment=None,
            )
        )

    bindings.update(
        {
            "strategy_source_sha256": snapshot.strategy_source_sha256,
            "strategy_source_record_identity_sha256": (
                snapshot.strategy_source_record_identity_sha256
            ),
            "portfolio_source_sha256": snapshot.portfolio_source_sha256,
            "portfolio_source_record_identity_sha256": (
                snapshot.portfolio_source_record_identity_sha256
            ),
            "role_universe_projection_identity_sha256": (
                snapshot.universe_projection_identity_sha256
            ),
        }
    )
    budget_result = _budget_capacity.observe_current_report_only_budget_capacity(
        portfolio_snapshot_expected_sha256=snapshot.portfolio_source_sha256,
    )
    exposure_result = _holdings_exposure.observe_current_report_only_holdings_exposure(
        strategy_settings_expected_sha256=snapshot.strategy_source_sha256,
        portfolio_snapshot_expected_sha256=snapshot.portfolio_source_sha256,
    )
    increment_result = (
        _increment_capacity.observe_report_only_increment_capacity_from_exposure(
            exposure_result=exposure_result,
        )
    )
    try:
        budget, exposure, increment = _validated_projection_inputs(
            snapshot=snapshot,
            budget_result=budget_result,
            exposure_result=exposure_result,
            increment_result=increment_result,
        )
    except V1ProposalInputError as exc:
        reasons = tuple(sorted(set(exc.reason_codes + _owner_diagnostics(
            budget_result,
            exposure_result,
            increment_result,
        ))))
        return _persist_proposal(
            _proposal(
                h1_evaluation=h1_evaluation,
                terminal_result=TERMINAL_NO_TRADE,
                reason_code=(
                    "INPUT_GENERATION_MISMATCH"
                    if "V1_PROPOSAL_INPUT_GENERATION_MISMATCH" in exc.reason_codes
                    else "INPUT_OWNER_NOT_VALID"
                ),
                diagnostic_reason_codes=reasons,
                source_bindings=bindings,
                capacity=_empty_capacity(),
                candidates=_partial_candidates(exposure_result.projection),
                selected_ticker=None,
                target_increment=None,
            )
        )

    proposal = _complete_proposal(
        h1_evaluation=h1_evaluation,
        snapshot=snapshot,
        budget=budget,
        exposure=exposure,
        increment=increment,
    )
    return _persist_proposal(proposal)
