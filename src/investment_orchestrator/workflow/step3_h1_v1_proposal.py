"""Report-only deterministic H1 V1 BUY proposal.

This workflow implements only the proposal calculations closed by
``docs/v1_buy_only_policy_v1.md``.  Its persisted P1 proposal remains
report-only and non-authorizing.  The separately owned state permission may
grant NEW_BUY after complete proposal recognition, but this module creates no
gate, publication, order, or execution authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
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
from investment_orchestrator.state.research_availability import (
    ACTIONS,
    H1_V1_DETERMINISTIC_PROPOSAL_READY,
    canonical_allowed_actions_for_state,
    canonical_blocked_actions_for_state,
)
from investment_orchestrator.workflow import step2_h1_currentness as _h1_currentness
from investment_orchestrator.workflow.step2_h1_provenance import (
    require_sha256_representation,
)


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

_PROPOSAL_KEYS: Final = frozenset(
    {
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
)
_SOURCE_BINDING_KEYS: Final = frozenset(
    {
        "h1_rendered_prompt_sha256",
        "h1_raw_response_sha256",
        "h1_evidence_entry_identities_sha256",
        "h1_report_evidence_references",
        "strategy_source_sha256",
        "strategy_source_record_identity_sha256",
        "portfolio_source_sha256",
        "portfolio_source_record_identity_sha256",
        "role_universe_projection_identity_sha256",
        "holdings_policy_projection_identity_sha256",
        "portfolio_scope_id",
        "holdings_observation_date",
        "valuation_capture_sha256",
        "valuation_source_kind",
        "valuation_provider_id",
        "valuation_session_date",
        "valuation_trusted_evaluation_timestamp_utc",
        "calendar_id",
        "calendar_schedule_sha256",
        "latest_completed_session_date",
        "valuation_freshness_status",
        "x_source_sha256",
        "r_source_sha256",
    }
)
_SOURCE_BINDING_SHA_KEYS: Final = frozenset(
    {
        "h1_rendered_prompt_sha256",
        "h1_raw_response_sha256",
        "strategy_source_sha256",
        "strategy_source_record_identity_sha256",
        "portfolio_source_sha256",
        "portfolio_source_record_identity_sha256",
        "role_universe_projection_identity_sha256",
        "holdings_policy_projection_identity_sha256",
        "valuation_capture_sha256",
        "calendar_schedule_sha256",
        "x_source_sha256",
        "r_source_sha256",
    }
)
_SNAPSHOT_SOURCE_BINDING_KEYS: Final = frozenset(
    {
        "strategy_source_sha256",
        "strategy_source_record_identity_sha256",
        "portfolio_source_sha256",
        "portfolio_source_record_identity_sha256",
        "role_universe_projection_identity_sha256",
    }
)
_PROJECTION_SOURCE_BINDING_KEYS: Final = (
    _SOURCE_BINDING_KEYS
    - _SNAPSHOT_SOURCE_BINDING_KEYS
    - {
        "h1_rendered_prompt_sha256",
        "h1_raw_response_sha256",
        "h1_evidence_entry_identities_sha256",
        "h1_report_evidence_references",
    }
)
_SOURCE_BINDING_DATE_KEYS: Final = frozenset(
    {
        "holdings_observation_date",
        "valuation_session_date",
        "latest_completed_session_date",
    }
)
_CAPACITY_KEYS: Final = frozenset(
    {"X", "H", "E", "R", "C", "A_initial", "Z_initial"}
)
_CANDIDATE_KEYS: Final = frozenset(
    {
        "ticker",
        "role",
        "disposition",
        "evidence_coverage_identities",
        "priority",
    }
)
_HOLD_REASON_CODES: Final = frozenset(
    {
        "NO_SHARED_CAPACITY",
        "NO_INCREMENT_ELIGIBLE_TICKER",
        "SELECTED_TARGET_NOT_POSITIVE",
    }
)
_NO_TRADE_REASON_CODES: Final = frozenset(
    {
        "INPUT_SOURCE_CONTRACT_NOT_VALID",
        "INPUT_GENERATION_MISMATCH",
        "INPUT_OWNER_NOT_VALID",
        "H1_CONTEXT_NOT_CURRENT",
        "REQUIRED_EXPOSURE_ROLE_UNRESOLVED",
        "EXISTING_COMMITMENT_EXCEEDS_X",
        "EXISTING_COMMITMENT_EXCEEDS_R",
        "INITIAL_ALPHA_EXCEEDS_CORE",
    }
)


class V1ProposalInputError(RuntimeError):
    """Controlled failure to obtain one validated deterministic input owner."""

    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        closed_reasons = reason_codes or ("V1_PROPOSAL_INPUT_INVALID",)
        super().__init__("; ".join(closed_reasons))
        self.reason_codes = closed_reasons


class V1ProposalStateRecognitionError(RuntimeError):
    """An impossible proposal result tried to cross the state boundary."""


@dataclass(frozen=True, slots=True)
class H1V1ProposalStateRecognition:
    """Permission state recognized from one fresh pure P1 evaluation.

    ``report_only`` / ``authority_effect`` / ``not_authorization`` describe the
    underlying P1 proposal.  The canonical state action row and
    ``new_buy_permission`` are the state permission contract.
    """

    state: str
    allowed_actions: tuple[str, ...]
    blocked_actions: tuple[str, ...]
    manual_review_required: bool
    report_only: bool
    authority_effect: str
    not_authorization: bool
    new_buy_permission: bool
    order_compilation_allowed: bool
    step3_allowed: bool
    step4_allowed: bool


@dataclass(frozen=True, slots=True)
class H1V1ProposalEvaluation:
    """One coherent pure P1 generation and its validated deterministic inputs.

    The proposal remains the closed report-only P1 result.  The optional typed
    projections are retained only so a downstream pure derivative can consume
    the exact generation that produced that result without rereading sources.
    They are absent whenever P1 could not establish a complete projection
    generation.
    """

    proposal: dict[str, object]
    strategy_source: MmiCapturedSource | None
    role_by_ticker: Mapping[str, str] | None
    budget: _budget_capacity.BudgetCapacityProjection | None
    exposure: _holdings_exposure.ExposureProjection | None
    increment: _increment_capacity.IncrementCapacityProjection | None


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


def _raise_result_contract_invalid() -> None:
    raise V1ProposalInputError(("V1_PROPOSAL_RESULT_CONTRACT_INVALID",))


def _is_canonical_iso_date(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value


def _is_sha256(value: object) -> bool:
    try:
        require_sha256_representation(value, "V1 proposal identity")
    except ValueError:
        return False
    return True


def _validated_sha_list(value: object) -> tuple[str, ...]:
    if type(value) is not list or any(not _is_sha256(item) for item in value):
        _raise_result_contract_invalid()
    identities = tuple(value)
    if len(set(identities)) != len(identities):
        _raise_result_contract_invalid()
    return identities


def _validated_result_capacity(
    value: object,
) -> dict[str, Decimal | None]:
    if type(value) is not dict or set(value) != _CAPACITY_KEYS:
        _raise_result_contract_invalid()
    parsed: dict[str, Decimal | None] = {}
    for key in _CAPACITY_KEYS:
        raw = value[key]
        if raw is None:
            parsed[key] = None
            continue
        try:
            parsed[key] = _decimal(raw, field=f"CAPACITY_{key.upper()}")
        except V1ProposalInputError:
            _raise_result_contract_invalid()

    base_values = tuple(parsed[key] for key in ("X", "H", "E", "R"))
    if any(item is None for item in base_values) != all(
        item is None for item in base_values
    ):
        _raise_result_contract_invalid()
    if all(item is None for item in base_values):
        if any(
            parsed[key] is not None
            for key in ("C", "A_initial", "Z_initial")
        ):
            _raise_result_contract_invalid()
        return parsed

    x_value = parsed["X"]
    h_value = parsed["H"]
    e_value = parsed["E"]
    r_value = parsed["R"]
    if (
        x_value is None
        or h_value is None
        or e_value is None
        or r_value is None
        or r_value > h_value
    ):
        _raise_result_contract_invalid()

    a_value = parsed["A_initial"]
    z_value = parsed["Z_initial"]
    if (a_value is None) != (z_value is None):
        _raise_result_contract_invalid()
    if a_value is not None and z_value is not None:
        if _sum([a_value, z_value]) != _sum([h_value, e_value]):
            _raise_result_contract_invalid()

    c_value = parsed["C"]
    if c_value is not None:
        if (
            a_value is None
            or z_value is None
            or e_value > x_value
            or e_value > r_value
            or c_value
            != _sum([min(x_value, r_value), e_value.copy_negate()])
        ):
            _raise_result_contract_invalid()
    return parsed


def _validated_result_candidates(
    value: object,
    *,
    current_evidence: tuple[str, ...],
    cited_evidence: tuple[str, ...],
) -> list[dict[str, object]]:
    if type(value) is not list:
        _raise_result_contract_invalid()
    rows: list[dict[str, object]] = []
    tickers: list[str] = []
    current_set = set(current_evidence)
    cited_set = set(cited_evidence)
    for raw_row in value:
        if type(raw_row) is not dict or set(raw_row) != _CANDIDATE_KEYS:
            _raise_result_contract_invalid()
        ticker = raw_row["ticker"]
        role = raw_row["role"]
        disposition = raw_row["disposition"]
        priority = raw_row["priority"]
        if type(ticker) is not str or not ticker:
            _raise_result_contract_invalid()
        evidence_ids = _validated_sha_list(
            raw_row["evidence_coverage_identities"]
        )
        if (
            list(evidence_ids) != sorted(evidence_ids)
            or not set(evidence_ids).issubset(current_set)
        ):
            _raise_result_contract_invalid()

        expected_role = (
            ROLE_CORE
            if ticker in V1_CORE_TICKERS
            else ROLE_SATELLITE if ticker in V1_SATELLITE_TICKERS else None
        )
        if expected_role is not None and role != expected_role:
            _raise_result_contract_invalid()
        if disposition == DISPOSITION_UNRESOLVED:
            valid_row = role == "UNRESOLVED" and priority is None
        elif disposition == DISPOSITION_EXCLUDE:
            valid_row = (
                ticker not in _V1_BASE_TICKERS
                and role in _RECOGNIZED_EXPOSURE_ROLES
                and priority is None
            )
        elif disposition == DISPOSITION_MAINTAIN_ONLY:
            valid_row = (
                ticker in _V1_BASE_TICKERS
                and role == expected_role
                and priority is None
            )
        elif disposition == DISPOSITION_INCREMENT_ELIGIBLE:
            valid_row = (
                ticker in _V1_BASE_TICKERS
                and role == expected_role
                and bool(evidence_ids)
                and priority
                == ("PREFERRED" if role == ROLE_CORE else "STANDARD")
            )
        else:
            valid_row = False
        if not valid_row:
            _raise_result_contract_invalid()
        rows.append(raw_row)
        tickers.append(ticker)

    if len(set(tickers)) != len(tickers):
        _raise_result_contract_invalid()
    priority_index = {ticker: index for index, ticker in enumerate(V1_PRIORITY_ORDER)}
    if tickers != sorted(
        tickers,
        key=lambda ticker: (priority_index.get(ticker, len(priority_index)), ticker),
    ):
        _raise_result_contract_invalid()
    return rows


def _validate_h1_v1_proposal_result(proposal: Mapping[str, object]) -> None:
    """Validate the complete closed P1 in-memory result contract."""
    if type(proposal) is not dict or set(proposal) != _PROPOSAL_KEYS:
        _raise_result_contract_invalid()
    if not (
        proposal["schema_version"] == V1_PROPOSAL_SCHEMA_VERSION
        and proposal["policy_contract_version"] == V1_POLICY_CONTRACT_VERSION
        and _is_canonical_iso_date(proposal["observed_on"])
        and proposal["report_only"] is True
        and proposal["authority_effect"] == AUTHORITY_EFFECT_NONE
        and proposal["not_authorization"] is True
        and proposal["new_buy_permission"] is False
        and proposal["order_compilation_allowed"] is False
    ):
        _raise_result_contract_invalid()

    diagnostic_reasons = proposal["diagnostic_reason_codes"]
    if type(diagnostic_reasons) is not list or any(
        type(reason) is not str or not reason for reason in diagnostic_reasons
    ):
        _raise_result_contract_invalid()

    terminal_result = proposal["terminal_result"]
    reason_code = proposal["reason_code"]
    if terminal_result == TERMINAL_POSITIVE_CANDIDATE:
        if reason_code != TERMINAL_POSITIVE_CANDIDATE or diagnostic_reasons != []:
            _raise_result_contract_invalid()
    elif terminal_result == TERMINAL_HOLD:
        if reason_code not in _HOLD_REASON_CODES or diagnostic_reasons != []:
            _raise_result_contract_invalid()
    elif terminal_result == TERMINAL_NO_TRADE:
        if reason_code not in _NO_TRADE_REASON_CODES:
            _raise_result_contract_invalid()
    else:
        _raise_result_contract_invalid()

    source_bindings = proposal["source_bindings"]
    if type(source_bindings) is not dict or set(source_bindings) != _SOURCE_BINDING_KEYS:
        _raise_result_contract_invalid()
    if not (
        _is_sha256(source_bindings["h1_rendered_prompt_sha256"])
        and _is_sha256(source_bindings["h1_raw_response_sha256"])
    ):
        _raise_result_contract_invalid()
    current_evidence = _validated_sha_list(
        source_bindings["h1_evidence_entry_identities_sha256"]
    )
    cited_evidence = _validated_sha_list(
        source_bindings["h1_report_evidence_references"]
    )
    if not set(cited_evidence).issubset(current_evidence):
        _raise_result_contract_invalid()
    if (
        terminal_result in (TERMINAL_HOLD, TERMINAL_POSITIVE_CANDIDATE)
        or reason_code
        in {
            "REQUIRED_EXPOSURE_ROLE_UNRESOLVED",
            "EXISTING_COMMITMENT_EXCEEDS_X",
            "EXISTING_COMMITMENT_EXCEEDS_R",
            "INITIAL_ALPHA_EXCEEDS_CORE",
        }
    ) and (not current_evidence or not cited_evidence):
        _raise_result_contract_invalid()
    if reason_code == "H1_CONTEXT_NOT_CURRENT" and (
        current_evidence or cited_evidence
    ):
        _raise_result_contract_invalid()
    for key in _SOURCE_BINDING_SHA_KEYS - {
        "h1_rendered_prompt_sha256",
        "h1_raw_response_sha256",
    }:
        value = source_bindings[key]
        if value is not None and not _is_sha256(value):
            _raise_result_contract_invalid()
    for key in _SOURCE_BINDING_DATE_KEYS:
        value = source_bindings[key]
        if value is not None and not _is_canonical_iso_date(value):
            _raise_result_contract_invalid()
    typed_string_keys = (
        _SOURCE_BINDING_KEYS
        - _SOURCE_BINDING_SHA_KEYS
        - _SOURCE_BINDING_DATE_KEYS
        - {
            "h1_evidence_entry_identities_sha256",
            "h1_report_evidence_references",
        }
    )
    for key in typed_string_keys:
        value = source_bindings[key]
        if value is not None and (type(value) is not str or not value.strip()):
            _raise_result_contract_invalid()

    if reason_code == "INPUT_SOURCE_CONTRACT_NOT_VALID":
        if any(
            source_bindings[key] is not None
            for key in _SNAPSHOT_SOURCE_BINDING_KEYS
            | _PROJECTION_SOURCE_BINDING_KEYS
        ):
            _raise_result_contract_invalid()
    elif reason_code in {"INPUT_GENERATION_MISMATCH", "INPUT_OWNER_NOT_VALID"}:
        if any(
            source_bindings[key] is None for key in _SNAPSHOT_SOURCE_BINDING_KEYS
        ) or any(
            source_bindings[key] is not None
            for key in _PROJECTION_SOURCE_BINDING_KEYS
        ):
            _raise_result_contract_invalid()
    elif any(
        source_bindings[key] is None
        for key in _SNAPSHOT_SOURCE_BINDING_KEYS | _PROJECTION_SOURCE_BINDING_KEYS
    ):
        _raise_result_contract_invalid()

    capacity = _validated_result_capacity(proposal["capacity"])
    candidates = _validated_result_candidates(
        proposal["candidates"],
        current_evidence=current_evidence,
        cited_evidence=cited_evidence,
    )
    selected_ticker = proposal["selected_ticker"]
    target_text = proposal["target_increment"]

    if terminal_result in (TERMINAL_HOLD, TERMINAL_NO_TRADE):
        if selected_ticker is not None or target_text is not None:
            _raise_result_contract_invalid()
        if terminal_result == TERMINAL_HOLD and any(
            capacity[key] is None for key in _CAPACITY_KEYS
        ):
            _raise_result_contract_invalid()
        if terminal_result == TERMINAL_NO_TRADE:
            if capacity["C"] is not None or any(
                row["disposition"] == DISPOSITION_INCREMENT_ELIGIBLE
                for row in candidates
            ):
                _raise_result_contract_invalid()
        return

    if (
        type(selected_ticker) is not str
        or selected_ticker not in V1_PRIORITY_ORDER
        or any(capacity[key] is None for key in _CAPACITY_KEYS)
        or not current_evidence
        or not cited_evidence
        or any(value is None for value in source_bindings.values())
    ):
        _raise_result_contract_invalid()
    try:
        target = _decimal(target_text, field="TARGET_INCREMENT")
    except V1ProposalInputError:
        _raise_result_contract_invalid()
    if target <= 0:
        _raise_result_contract_invalid()

    eligible = [
        row
        for row in candidates
        if row["disposition"] == DISPOSITION_INCREMENT_ELIGIBLE
    ]
    selected_rows = [row for row in eligible if row["ticker"] == selected_ticker]
    eligible_tickers = {str(row["ticker"]) for row in eligible}
    expected_selected = next(
        (ticker for ticker in V1_PRIORITY_ORDER if ticker in eligible_tickers),
        None,
    )
    if len(selected_rows) != 1 or selected_ticker != expected_selected:
        _raise_result_contract_invalid()

    c_value = capacity["C"]
    a_value = capacity["A_initial"]
    z_value = capacity["Z_initial"]
    if c_value is None or a_value is None or z_value is None or c_value <= 0:
        _raise_result_contract_invalid()
    selected = selected_rows[0]
    if selected["role"] == ROLE_CORE:
        expected_target = c_value
    elif selected["role"] == ROLE_SATELLITE:
        headroom = _sum([z_value, a_value.copy_negate()])
        expected_target = min(c_value, headroom)
    else:
        _raise_result_contract_invalid()
    if a_value > z_value or target != expected_target or expected_target <= 0:
        _raise_result_contract_invalid()


def _persist_proposal(proposal: Mapping[str, object]) -> Path:
    _validate_h1_v1_proposal_result(proposal)
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
    bound_entries_by_ticker: dict[str, tuple[str, ...]] = {}
    if context is not None:
        for ticker in holdings_by_ticker:
            bound_entries_by_ticker[ticker] = tuple(
                sorted(
                    entry.source_entry_identity_sha256
                    for entry in context.current_lh2_payload.sources
                    if ticker in entry.tickers
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
        evidence_ids = bound_entries_by_ticker.get(ticker, ())
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


def evaluate_h1_v1_proposal_generation() -> H1V1ProposalEvaluation:
    """Evaluate one complete current P1 generation without persistence."""
    h1_evaluation = _h1_currentness.evaluate_current_h1_context()
    bindings = _base_source_bindings(h1_evaluation)
    try:
        snapshot = _load_current_source_snapshot()
    except V1ProposalInputError as exc:
        return H1V1ProposalEvaluation(
            proposal=_proposal(
                h1_evaluation=h1_evaluation,
                terminal_result=TERMINAL_NO_TRADE,
                reason_code="INPUT_SOURCE_CONTRACT_NOT_VALID",
                diagnostic_reason_codes=exc.reason_codes,
                source_bindings=bindings,
                capacity=_empty_capacity(),
                candidates=[],
                selected_ticker=None,
                target_increment=None,
            ),
            strategy_source=None,
            role_by_ticker=None,
            budget=None,
            exposure=None,
            increment=None,
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
        return H1V1ProposalEvaluation(
            proposal=_proposal(
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
            ),
            strategy_source=snapshot.strategy_source,
            role_by_ticker=snapshot.role_by_ticker,
            budget=None,
            exposure=None,
            increment=None,
        )

    return H1V1ProposalEvaluation(
        proposal=_complete_proposal(
            h1_evaluation=h1_evaluation,
            snapshot=snapshot,
            budget=budget,
            exposure=exposure,
            increment=increment,
        ),
        strategy_source=snapshot.strategy_source,
        role_by_ticker=snapshot.role_by_ticker,
        budget=budget,
        exposure=exposure,
        increment=increment,
    )


def evaluate_h1_v1_proposal() -> dict[str, object]:
    """Evaluate one complete current V1 proposal without persistence."""
    return evaluate_h1_v1_proposal_generation().proposal


def _recognize_h1_v1_proposal_state(
    proposal: Mapping[str, object],
) -> H1V1ProposalStateRecognition | None:
    """Recognize the narrowly permissioned V1 state from one P1 result.

    HOLD and NO_TRADE are complete P1 outcomes but are not the positive-ready
    state.  This performs complete result-contract validation only; it neither
    reloads proposal inputs nor writes any artifact.
    """
    try:
        _validate_h1_v1_proposal_result(proposal)
    except V1ProposalInputError:
        raise V1ProposalStateRecognitionError(
            "V1_PROPOSAL_STATE_RECOGNITION_INVARIANT_FAILED"
        ) from None
    terminal_result = proposal["terminal_result"]
    if terminal_result in (TERMINAL_HOLD, TERMINAL_NO_TRADE):
        return None

    allowed_actions = canonical_allowed_actions_for_state(
        H1_V1_DETERMINISTIC_PROPOSAL_READY
    )
    blocked_actions = canonical_blocked_actions_for_state(
        H1_V1_DETERMINISTIC_PROPOSAL_READY
    )
    if not (
        allowed_actions == ("HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION")
        and blocked_actions
        == tuple(action for action in ACTIONS if action not in allowed_actions)
        and len(allowed_actions) + len(blocked_actions) == len(ACTIONS)
        and set(allowed_actions).isdisjoint(blocked_actions)
        and set(allowed_actions) | set(blocked_actions) == set(ACTIONS)
    ):
        raise V1ProposalStateRecognitionError(
            "V1_PROPOSAL_STATE_ACTION_CONTRACT_INVALID"
        )
    return H1V1ProposalStateRecognition(
        state=H1_V1_DETERMINISTIC_PROPOSAL_READY,
        allowed_actions=allowed_actions,
        blocked_actions=blocked_actions,
        manual_review_required=False,
        report_only=True,
        authority_effect=AUTHORITY_EFFECT_NONE,
        not_authorization=True,
        new_buy_permission="NEW_BUY" in allowed_actions,
        order_compilation_allowed="ORDER_COMPILATION" in allowed_actions,
        step3_allowed=False,
        step4_allowed=False,
    )


def evaluate_h1_v1_proposal_state(
) -> H1V1ProposalStateRecognition | None:
    """Recognize the state from one fresh pure P1 evaluation with no writes."""
    return _recognize_h1_v1_proposal_state(evaluate_h1_v1_proposal())


def build_h1_v1_proposal_workflow() -> Path:
    """Evaluate and atomically persist one non-authoritative V1 proposal."""
    return _persist_proposal(evaluate_h1_v1_proposal())
