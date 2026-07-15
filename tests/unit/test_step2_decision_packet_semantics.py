from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from investment_orchestrator.validators import (
    validate_step2_decision_packet_semantics as semantic_contract,
)
from investment_orchestrator.validators.validate_step2_decision_packet_semantics import (
    CANDIDATE_VALIDITY_EVALUATED,
    FRESHNESS_EVALUATION_PERFORMED,
    MAX_SEMANTIC_DIAGNOSTICS,
    NOT_AUTHORIZATION,
    PERMISSION_EFFECT_NONE,
    PORTFOLIO_BUDGET_VALIDATION_PERFORMED,
    SEMANTIC_VALIDATION_RESULT_VERSION,
    SOURCE_EVIDENCE_EVALUATION_PERFORMED,
    UNIVERSE_RESOLUTION_PERFORMED,
    VALIDATION_BOOLEAN_COERCION_ERROR,
    Step2DecisionPacketSemanticDiagnostic,
    Step2DecisionPacketSemanticValidationResult,
    validate_step2_decision_packet_semantics,
)
from investment_orchestrator.validators.validate_step2_decision_packet_v2 import (
    DECISION_PACKET_SCHEMA_VERSION,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODE_COUNT,
    validate_step2_decision_packet_v2,
)
from investment_orchestrator.validators.validate_step2_market_observations import (
    MARKET_OBSERVATIONS_SCHEMA_VERSION,
    REPORTED_ISSUE_CODES,
)


Diagnostic = Step2DecisionPacketSemanticDiagnostic
_MISSING = object()


def _market_row(ticker: str = "QQQ") -> dict[str, Any]:
    return {
        "ticker": ticker,
        "last_close": 500.25,
        "reported_price_asof": "2026-07-13",
        "atr_20_abs": 7.5,
        "atr_20_30d_pct": 1.5,
        "ma50": 490.0,
        "ma200": 450.0,
        "avg_volume_3m": 12_345_678,
        "week_52_low": 400.0,
        "week_52_high": 550.0,
        "reported_last_close_source": "reported-source",
        "reported_price_source": "reported-source",
        "reported_technicals_source": "reported-source",
        "reported_retrieved_at_utc": "2026-07-14T01:02:03Z",
        "source_evidence_refs": [f"observation:{ticker}"],
        "reported_issue_codes": [],
        "observation_notes": [],
    }


def _buy_row(
    ticker: str = "QQQ",
    action: str = "HOLD_NO_NEW_BUDGET",
    budget: int | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "proposed_action": action,
        "proposed_budget_cents": budget,
        "rationale": "Non-authorizing buy proposal.",
        "reference_ids": [],
    }


def _step(
    label: str = "rung-1",
    offset: int = -50,
    weight: int = 10_000,
) -> dict[str, Any]:
    return {
        "step_label": label,
        "proposed_offset_bps": offset,
        "proposed_weight_bps": weight,
    }


def _buy_execution(
    ticker: str = "QQQ",
    action: str = "NEW_ORDER",
    *,
    kind: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    tif: str = "DAY",
    expiry: str | None = None,
) -> dict[str, Any]:
    kinds = {
        "KEEP_EXISTING": "KEEP_EXISTING_LADDER",
        "NEW_ORDER": "NEW_LIMIT_LADDER",
        "REPLACE_EXISTING": "REPLACE_EXISTING_LADDER",
        "CANCEL_EXISTING": "CANCEL_EXISTING_ORDER",
    }
    if steps is None:
        steps = [] if action == "CANCEL_EXISTING" else [_step()]
    return {
        "ticker": ticker,
        "proposal_action": action,
        "plan_kind": kind or kinds[action],
        "proposed_time_in_force": tif,
        "proposed_expiry_date": expiry,
        "proposed_steps": steps,
        "rationale": "Non-executable buy plan.",
        "reference_ids": [],
    }


def _sell_row(
    ticker: str = "QQQ",
    action: str = "HOLD_NO_SELL",
    quantity: int | None = None,
    replacement: str | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "proposed_action": action,
        "proposed_share_quantity": quantity,
        "replacement_ticker": replacement,
        "rationale": "Non-authorizing sell proposal.",
        "reference_ids": [],
    }


def _sell_execution(
    ticker: str = "QQQ",
    quantity: int | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "proposal_action": "SELL",
        "plan_kind": "SINGLE_LIMIT_SELL_PROPOSAL",
        "proposed_share_quantity": quantity,
        "proposed_limit_rule": "Reported proposal only.",
        "proposed_lot_policy": "LTCG_ELIGIBLE_ONLY",
        "proposed_time_in_force": "DAY",
        "proposed_expiry_date": None,
        "rationale": "Non-executable sell plan.",
        "reference_ids": [],
    }


def _shortlist_row(
    ticker: str,
    rank: int,
    status: str = "SELECTED",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "rank": rank,
        "role_claim": "reported-role",
        "proposal_status": status,
        "rationale": "Reported shortlist proposal.",
        "reference_ids": [],
        "reported_risk_notes": [],
    }


def _rotation(
    source: str,
    destination: str,
    budget: int | None = None,
) -> dict[str, Any]:
    return {
        "from_ticker": source,
        "to_ticker": destination,
        "proposal_type": "SAME_ROLE_ROTATION",
        "proposed_budget_cents": budget,
        "rationale": "Reported rotation proposal.",
        "reference_ids": [],
    }


def _packet() -> dict[str, Any]:
    return {
        "schema_version": DECISION_PACKET_SCHEMA_VERSION,
        "mode": "DECISION_DRAFT",
        "market_observations": {
            "schema_version": MARKET_OBSERVATIONS_SCHEMA_VERSION,
            "observations": [_market_row()],
        },
        "proposed_buy_universe": ["QQQ", "VGT", "SPY"],
        "active_shortlist": [],
        "exposure_overlap_diagnostics": [],
        "buy_side_delta_table": [_buy_row()],
        "rotation_decision_layer_8_15": [],
        "sell_side_delta_table_8_2": [],
        "execution_plan_drafts_8_5": [],
        "sell_execution_plan_drafts_8_6": [],
        "cold_regime_review_proposal": None,
        "post_cancel_redeployment_proposal": None,
        "reported_assumptions_and_data_gaps": [],
    }


def _no_trade_packet() -> dict[str, Any]:
    return {
        "schema_version": DECISION_PACKET_SCHEMA_VERSION,
        "mode": "NO_TRADE",
        "no_trade_reason": {
            "reason_code": "MISSING_REQUIRED_DATA",
            "reason_detail": "Reported reason only.",
            "reference_ids": [],
        },
        "market_observations": None,
        "proposed_buy_universe": [],
        "active_shortlist": [],
        "exposure_overlap_diagnostics": [],
        "buy_side_delta_table": [],
        "rotation_decision_layer_8_15": [],
        "sell_side_delta_table_8_2": [],
        "execution_plan_drafts_8_5": [],
        "sell_execution_plan_drafts_8_6": [],
        "cold_regime_review_proposal": None,
        "post_cancel_redeployment_proposal": None,
        "reported_assumptions_and_data_gaps": [
            {
                "category": "DATA_GAP_CLAIM",
                "code": "REPORTED_GAP",
                "detail": "Reported missing data.",
                "related_tickers": [],
                "reference_ids": [],
            }
        ],
    }


def _validate(value: object) -> Step2DecisionPacketSemanticValidationResult:
    return validate_step2_decision_packet_semantics(value)


def _diagnostics(value: object) -> tuple[Diagnostic, ...]:
    return _validate(value).diagnostics


def _assert_valid(value: object) -> Step2DecisionPacketSemanticValidationResult:
    result = _validate(value)
    assert result.structural_validation_passed is True
    assert result.semantic_validation_performed is True
    assert result.semantic_valid is True
    assert result.diagnostics == ()
    return result


def _assert_has(value: object, diagnostic: Diagnostic) -> None:
    result = _validate(value)
    assert result.structural_validation_passed is True
    assert result.semantic_validation_performed is True
    assert result.semantic_valid is False
    assert diagnostic in result.diagnostics


def _assert_prerequisite_failure(
    result: Step2DecisionPacketSemanticValidationResult,
    diagnostic: Diagnostic,
) -> None:
    assert result.packet_schema_version is None
    assert result.packet_mode is None
    assert result.source_identity_sha256 is None
    assert result.structural_validation_passed is False
    assert result.semantic_validation_performed is False
    assert result.semantic_valid is None
    assert result.diagnostics == (diagnostic,)
    assert result.not_authorization is True
    assert result.permission_effect == "none"
    assert result.freshness_evaluation_performed is False
    assert result.source_evidence_evaluation_performed is False
    assert result.universe_resolution_performed is False
    assert result.portfolio_budget_validation_performed is False
    assert result.candidate_validity_evaluated is False
    assert not hasattr(result, "identity_only")
    assert not hasattr(result, "__dict__")
    with pytest.raises(TypeError) as exc_info:
        bool(result)
    assert str(exc_info.value) == VALIDATION_BOOLEAN_COERCION_ERROR
    with pytest.raises((FrozenInstanceError, AttributeError)):
        result.semantic_valid = False  # type: ignore[misc]


def _packet_for_buy_action(
    action: str,
    *,
    ticker: str = "QQQ",
    budget: int | None = None,
) -> dict[str, Any]:
    value = _packet()
    value["buy_side_delta_table"] = [_buy_row(ticker, action, budget)]
    if action in {"NEW_ORDER", "REPLACE_EXISTING", "CANCEL_EXISTING"}:
        value["execution_plan_drafts_8_5"] = [
            _buy_execution(ticker, action)
        ]
    return value


def _packet_for_buy_sell(
    buy_action: str,
    sell_action: str,
) -> dict[str, Any]:
    value = _packet_for_buy_action(
        buy_action,
        budget=10_000 if buy_action in {"NEW_ORDER", "REPLACE_EXISTING"} else None,
    )
    value["sell_side_delta_table_8_2"] = [_sell_row(action=sell_action)]
    if sell_action == "SELL":
        value["sell_execution_plan_drafts_8_6"] = [_sell_execution()]
    return value


def test_public_success_result_contract_and_identity() -> None:
    value = _packet()
    structural = validate_step2_decision_packet_v2(value)
    result = _assert_valid(value)
    assert [item.name for item in fields(result)] == [
        "result_version",
        "packet_schema_version",
        "packet_mode",
        "source_identity_sha256",
        "structural_validation_passed",
        "semantic_validation_performed",
        "semantic_valid",
        "diagnostics",
        "not_authorization",
        "permission_effect",
        "freshness_evaluation_performed",
        "source_evidence_evaluation_performed",
        "universe_resolution_performed",
        "portfolio_budget_validation_performed",
        "candidate_validity_evaluated",
    ]
    assert result.result_version == SEMANTIC_VALIDATION_RESULT_VERSION
    assert result.packet_schema_version == DECISION_PACKET_SCHEMA_VERSION
    assert result.packet_mode == "DECISION_DRAFT"
    assert result.source_identity_sha256 == structural.canonical_identity_sha256
    assert result.not_authorization is NOT_AUTHORIZATION is True
    assert result.permission_effect == PERMISSION_EFFECT_NONE == "none"
    assert (
        result.freshness_evaluation_performed
        is FRESHNESS_EVALUATION_PERFORMED
        is False
    )
    assert (
        result.source_evidence_evaluation_performed
        is SOURCE_EVIDENCE_EVALUATION_PERFORMED
        is False
    )
    assert (
        result.universe_resolution_performed
        is UNIVERSE_RESOLUTION_PERFORMED
        is False
    )
    assert (
        result.portfolio_budget_validation_performed
        is PORTFOLIO_BUDGET_VALIDATION_PERFORMED
        is False
    )
    assert (
        result.candidate_validity_evaluated
        is CANDIDATE_VALIDITY_EVALUATED
        is False
    )
    assert not hasattr(result, "identity_only")


def test_result_is_frozen_slotted_and_rejects_boolean_coercion() -> None:
    valid = _assert_valid(_packet())
    invalid_value = _packet()
    invalid_value["buy_side_delta_table"][0]["proposed_action"] = "NEW_ORDER"
    invalid_value["buy_side_delta_table"][0]["proposed_budget_cents"] = 0
    invalid_value["execution_plan_drafts_8_5"] = [_buy_execution()]
    invalid = _validate(invalid_value)
    unevaluated = _validate(None)
    for result in (valid, invalid, unevaluated):
        with pytest.raises(TypeError, match="no truth value") as exc_info:
            bool(result)
        assert str(exc_info.value) == VALIDATION_BOOLEAN_COERCION_ERROR
        with pytest.raises((FrozenInstanceError, AttributeError)):
            result.semantic_valid = True  # type: ignore[misc]
        assert not hasattr(result, "__dict__")


@pytest.mark.parametrize("value", [None, {}, {"schema_version": "wrong"}])
def test_first_b2_failure_branch(value: object) -> None:
    result = _validate(value)
    _assert_prerequisite_failure(
        result,
        Diagnostic.STRUCTURAL_PREREQUISITE_FAILED,
    )


def test_public_boundary_snapshot_capture_failure_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _packet()
    real = validate_step2_decision_packet_v2
    calls = 0

    def validate_then_introduce_cycle(candidate: object) -> Any:
        nonlocal calls
        calls += 1
        result = real(candidate)
        if calls == 1:
            cycle: list[Any] = []
            cycle.append(cycle)
            value["reported_assumptions_and_data_gaps"] = cycle
        return result

    monkeypatch.setattr(
        semantic_contract,
        "validate_step2_decision_packet_v2",
        validate_then_introduce_cycle,
    )
    result = _validate(value)
    assert calls == 1
    _assert_prerequisite_failure(result, Diagnostic.SNAPSHOT_CAPTURE_FAILED)


def test_second_b2_failure_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    real = validate_step2_decision_packet_v2
    calls = 0

    def fake(value: object) -> Any:
        nonlocal calls
        calls += 1
        return real(value) if calls == 1 else real(None)

    monkeypatch.setattr(semantic_contract, "validate_step2_decision_packet_v2", fake)
    result = _validate(_packet())
    assert calls == 2
    _assert_prerequisite_failure(
        result,
        Diagnostic.SNAPSHOT_REVALIDATION_FAILED,
    )


@pytest.mark.parametrize(
    "field_name",
    ["canonical_identity_sha256", "packet_mode", "schema_version"],
)
def test_snapshot_identity_mode_and_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
) -> None:
    real_result = validate_step2_decision_packet_v2(_packet())
    first = SimpleNamespace(**{
        name: getattr(real_result, name)
        for name in (
            "structure_valid",
            "schema_valid",
            "schema_version",
            "packet_mode",
            "canonical_identity_sha256",
        )
    })
    second_values = vars(first).copy()
    second_values[field_name] = {
        "canonical_identity_sha256": "0" * 64,
        "packet_mode": "NO_TRADE",
        "schema_version": "wrong",
    }[field_name]
    second = SimpleNamespace(**second_values)
    results = iter((first, second))
    monkeypatch.setattr(
        semantic_contract,
        "validate_step2_decision_packet_v2",
        lambda value: next(results),
    )
    result = _validate(_packet())
    _assert_prerequisite_failure(
        result,
        Diagnostic.SNAPSHOT_IDENTITY_MISMATCH,
    )


@pytest.mark.parametrize(
    "exception",
    [
        AssertionError(),
        AttributeError(),
        TypeError(),
        ValueError(),
        RecursionError(),
        MemoryError(),
        KeyboardInterrupt(),
        SystemExit(),
    ],
)
def test_unexpected_public_b2_exceptions_propagate(
    monkeypatch: pytest.MonkeyPatch,
    exception: BaseException,
) -> None:
    def fail(value: object) -> Any:
        raise exception

    monkeypatch.setattr(semantic_contract, "validate_step2_decision_packet_v2", fail)
    with pytest.raises(type(exception)):
        _validate(_packet())


def test_unexpected_second_public_b2_exception_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = validate_step2_decision_packet_v2
    calls = 0
    expected = ValueError("second public b2 dependency failure")

    def fail_on_second(value: object) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise expected
        return real(value)

    monkeypatch.setattr(
        semantic_contract,
        "validate_step2_decision_packet_v2",
        fail_on_second,
    )
    with pytest.raises(ValueError) as exc_info:
        _validate(_packet())
    assert calls == 2
    assert exc_info.value is expected


def test_coherent_between_validation_mutation_fails_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _packet()
    real = validate_step2_decision_packet_v2
    calls = 0

    def validate_then_mutate_caller(candidate: object) -> Any:
        nonlocal calls
        calls += 1
        result = real(candidate)
        if calls == 1:
            value["market_observations"]["observations"][0][
                "week_52_low"
            ] = 600.0
        return result

    monkeypatch.setattr(
        semantic_contract,
        "validate_step2_decision_packet_v2",
        validate_then_mutate_caller,
    )
    result = _validate(value)
    assert calls == 2
    _assert_prerequisite_failure(
        result,
        Diagnostic.SNAPSHOT_IDENTITY_MISMATCH,
    )


def test_snapshot_accepts_exact_builtins_and_copies_aliases_independently() -> None:
    shared = [None, True, 1, 1.5, "value"]
    source = {"first": shared, "second": shared}
    outcome = semantic_contract._capture_snapshot(source)
    assert outcome.failure is None
    assert outcome.snapshot == source
    assert outcome.snapshot is not source
    assert outcome.snapshot["first"] is not shared
    assert outcome.snapshot["second"] is not shared
    assert outcome.snapshot["first"] is not outcome.snapshot["second"]


@pytest.mark.parametrize(
    ("value", "failure"),
    [
        ({"bad": object()}, "UNSUPPORTED_EXACT_TYPE"),
        ({"bad": float("nan")}, "UNSUPPORTED_EXACT_TYPE"),
        ({1: "bad"}, "NON_STRING_MAPPING_KEY"),
    ],
)
def test_snapshot_explicit_type_and_key_failures(value: Any, failure: str) -> None:
    outcome = semantic_contract._capture_snapshot(value)
    assert outcome.snapshot is None
    assert outcome.failure is semantic_contract._SnapshotCaptureFailure[failure]


def test_snapshot_rejects_exact_builtin_subclasses() -> None:
    class DictSubclass(dict[str, Any]):
        pass

    outcome = semantic_contract._capture_snapshot(DictSubclass())
    assert (
        outcome.failure
        is semantic_contract._SnapshotCaptureFailure.UNSUPPORTED_EXACT_TYPE
    )


def _nested_list(depth: int) -> list[Any]:
    root: list[Any] = []
    current = root
    for _ in range(depth):
        child: list[Any] = []
        current.append(child)
        current = child
    return root


def test_snapshot_depth_boundary() -> None:
    assert semantic_contract._capture_snapshot(
        _nested_list(MAX_JSON_NESTING_DEPTH)
    ).failure is None
    assert semantic_contract._capture_snapshot(
        _nested_list(MAX_JSON_NESTING_DEPTH + 1)
    ).failure is semantic_contract._SnapshotCaptureFailure.DEPTH_LIMIT_EXCEEDED


def test_snapshot_node_boundary() -> None:
    allowed = [None] * (MAX_JSON_NODE_COUNT - 1)
    excessive = [None] * MAX_JSON_NODE_COUNT
    assert semantic_contract._capture_snapshot(allowed).failure is None
    assert semantic_contract._capture_snapshot(
        excessive
    ).failure is semantic_contract._SnapshotCaptureFailure.NODE_LIMIT_EXCEEDED


def test_snapshot_cycle_failure() -> None:
    value: list[Any] = []
    value.append(value)
    outcome = semantic_contract._capture_snapshot(value)
    assert outcome.failure is semantic_contract._SnapshotCaptureFailure.CYCLE_DETECTED


def test_public_boundary_post_capture_caller_mutation_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _packet()
    expected = validate_step2_decision_packet_v2(value)
    real = validate_step2_decision_packet_v2
    calls = 0

    def validate_then_mutate_original(candidate: object) -> Any:
        nonlocal calls
        calls += 1
        result = real(candidate)
        if calls == 2:
            value["market_observations"]["observations"][0][
                "week_52_low"
            ] = 600.0
        return result

    monkeypatch.setattr(
        semantic_contract,
        "validate_step2_decision_packet_v2",
        validate_then_mutate_original,
    )
    result = _validate(value)
    assert calls == 2
    assert (
        value["market_observations"]["observations"][0]["week_52_low"]
        == 600.0
    )
    assert result.structural_validation_passed is True
    assert result.semantic_validation_performed is True
    assert result.semantic_valid is True
    assert result.diagnostics == ()
    assert result.source_identity_sha256 == expected.canonical_identity_sha256

    captured_result_fields = (
        result.source_identity_sha256,
        result.semantic_valid,
        result.diagnostics,
    )
    value["proposed_buy_universe"].append("LATE")
    assert (
        result.source_identity_sha256,
        result.semantic_valid,
        result.diagnostics,
    ) == captured_result_fields


def test_semantic_failure_retains_identity_and_marks_performed() -> None:
    value = _packet()
    value["market_observations"]["observations"][0]["week_52_low"] = 600.0
    result = _validate(value)
    structural = validate_step2_decision_packet_v2(value)
    assert result.structural_validation_passed is True
    assert result.semantic_validation_performed is True
    assert result.semantic_valid is False
    assert result.source_identity_sha256 == structural.canonical_identity_sha256
    assert result.diagnostics == (Diagnostic.WEEK_52_RANGE_INVALID,)


def test_market_duplicate_ticker_and_range_rules() -> None:
    value = _packet()
    value["market_observations"]["observations"].append(_market_row())
    value["market_observations"]["observations"][0]["week_52_low"] = 600.0
    value["market_observations"]["observations"][0]["last_close"] = 700.0
    diagnostics = _diagnostics(value)
    assert Diagnostic.DUPLICATE_MARKET_OBSERVATION_TICKER in diagnostics
    assert Diagnostic.WEEK_52_RANGE_INVALID in diagnostics
    # A close outside a coherent range is deliberately not checked.
    value["market_observations"]["observations"] = [_market_row()]
    value["market_observations"]["observations"][0]["last_close"] = 700.0
    _assert_valid(value)


@pytest.mark.parametrize(
    "missing_field",
    [
        "reported_price_asof",
        "reported_last_close_source",
        "reported_price_source",
    ],
)
def test_close_claim_completeness(missing_field: str) -> None:
    value = _packet()
    value["market_observations"]["observations"][0][missing_field] = None
    _assert_has(value, Diagnostic.MARKET_CLOSE_CLAIM_INCOMPLETE)


def test_partial_close_claims_without_close_are_allowed() -> None:
    value = _packet()
    row = value["market_observations"]["observations"][0]
    row["last_close"] = None
    for field in _TECHNICAL_FIELDS:
        row[field] = None
    row["reported_technicals_source"] = None
    row["reported_retrieved_at_utc"] = None
    row["reported_last_close_source"] = None
    _assert_valid(value)


_TECHNICAL_FIELDS = (
    "atr_20_abs",
    "atr_20_30d_pct",
    "ma50",
    "ma200",
    "avg_volume_3m",
    "week_52_low",
    "week_52_high",
)


def test_close_only_numeric_claim_requires_retrieval_timestamp() -> None:
    value = _packet()
    row = value["market_observations"]["observations"][0]
    for field in _TECHNICAL_FIELDS:
        row[field] = None
    row["reported_technicals_source"] = None
    row["reported_retrieved_at_utc"] = None

    result = _validate(value)
    assert result.semantic_valid is False
    assert result.diagnostics == (
        Diagnostic.MARKET_NUMERIC_CLAIM_RETRIEVAL_TIMESTAMP_MISSING,
    )
    assert Diagnostic.MARKET_CLOSE_CLAIM_INCOMPLETE not in result.diagnostics
    assert Diagnostic.TECHNICAL_METRIC_SOURCE_MISSING not in result.diagnostics

    row["reported_retrieved_at_utc"] = "2026-07-14T01:02:03Z"
    _assert_valid(value)


@pytest.mark.parametrize("metric", _TECHNICAL_FIELDS)
def test_each_numeric_metric_requires_timestamp_and_technical_source(
    metric: str,
) -> None:
    value = _packet()
    row = value["market_observations"]["observations"][0]
    row["last_close"] = None
    for field in _TECHNICAL_FIELDS:
        row[field] = None
    row[metric] = 1 if metric == "avg_volume_3m" else 1.0
    row["reported_retrieved_at_utc"] = None
    row["reported_technicals_source"] = None
    diagnostics = _diagnostics(value)
    assert Diagnostic.MARKET_NUMERIC_CLAIM_RETRIEVAL_TIMESTAMP_MISSING in diagnostics
    assert Diagnostic.TECHNICAL_METRIC_SOURCE_MISSING in diagnostics


def test_source_and_evidence_without_metrics_need_no_timestamp() -> None:
    value = _packet()
    row = value["market_observations"]["observations"][0]
    row["last_close"] = None
    for field in _TECHNICAL_FIELDS:
        row[field] = None
    row["reported_retrieved_at_utc"] = None
    row["source_evidence_refs"] = ["reported-reference"]
    _assert_valid(value)


_ISSUE_MAPPING_CASES = (
    ("MISSING_LAST_CLOSE_CLAIM", ("last_close",)),
    ("MISSING_PRICE_DATE_CLAIM", ("reported_price_asof",)),
    (
        "MISSING_CLOSE_SOURCE_CLAIM",
        ("reported_last_close_source", "reported_price_source"),
    ),
    ("MISSING_TECHNICALS_CLAIM", _TECHNICAL_FIELDS),
    ("MISSING_TECHNICAL_SOURCE_CLAIM", ("reported_technicals_source",)),
    (
        "MISSING_RETRIEVAL_TIMESTAMP_CLAIM",
        ("reported_retrieved_at_utc",),
    ),
)


@pytest.mark.parametrize(("code", "governed_fields"), _ISSUE_MAPPING_CASES)
def test_populated_missing_claim_code_is_inconsistent(
    code: str,
    governed_fields: tuple[str, ...],
) -> None:
    value = _packet()
    value["market_observations"]["observations"][0]["reported_issue_codes"] = [code]
    _assert_has(value, Diagnostic.REPORTED_ISSUE_CLAIM_INCONSISTENT)


@pytest.mark.parametrize(("code", "governed_fields"), _ISSUE_MAPPING_CASES)
def test_missing_or_partial_claim_is_consistent_with_code(
    code: str,
    governed_fields: tuple[str, ...],
) -> None:
    value = _packet()
    row = value["market_observations"]["observations"][0]
    row["reported_issue_codes"] = [code]
    row[governed_fields[0]] = None
    diagnostics = _diagnostics(value)
    assert Diagnostic.REPORTED_ISSUE_CLAIM_INCONSISTENT not in diagnostics


@pytest.mark.parametrize(
    "code",
    [
        "STALE_DATA_CLAIM",
        "FUTURE_DATED_DATA_CLAIM",
        "SOURCE_CONFLICT_CLAIM",
        "OTHER_REPORTED_ISSUE",
    ],
)
def test_deferred_issue_codes_are_not_evaluated(code: str) -> None:
    value = _packet()
    value["market_observations"]["observations"][0]["reported_issue_codes"] = [code]
    _assert_valid(value)


def test_exact_issue_code_enum_is_covered() -> None:
    tested = {case[0] for case in _ISSUE_MAPPING_CASES} | {
        "STALE_DATA_CLAIM",
        "FUTURE_DATED_DATA_CLAIM",
        "SOURCE_CONFLICT_CLAIM",
        "OTHER_REPORTED_ISSUE",
    }
    assert tuple(REPORTED_ISSUE_CODES) == (
        "MISSING_LAST_CLOSE_CLAIM",
        "MISSING_PRICE_DATE_CLAIM",
        "MISSING_CLOSE_SOURCE_CLAIM",
        "MISSING_TECHNICALS_CLAIM",
        "MISSING_TECHNICAL_SOURCE_CLAIM",
        "MISSING_RETRIEVAL_TIMESTAMP_CLAIM",
        "STALE_DATA_CLAIM",
        "FUTURE_DATED_DATA_CLAIM",
        "SOURCE_CONFLICT_CLAIM",
        "OTHER_REPORTED_ISSUE",
    )
    assert tested == set(REPORTED_ISSUE_CODES)


def test_duplicate_observation_does_not_suppress_row_issue_check() -> None:
    value = _packet()
    duplicate = _market_row()
    duplicate["reported_issue_codes"] = ["MISSING_LAST_CLOSE_CLAIM"]
    value["market_observations"]["observations"].append(duplicate)
    diagnostics = _diagnostics(value)
    assert Diagnostic.DUPLICATE_MARKET_OBSERVATION_TICKER in diagnostics
    assert Diagnostic.REPORTED_ISSUE_CLAIM_INCONSISTENT in diagnostics


def test_shortlist_unique_noncontiguous_out_of_order_ranks_are_valid() -> None:
    value = _packet()
    value["active_shortlist"] = [
        _shortlist_row("QQQ", 100),
        _shortlist_row("VGT", 3),
    ]
    _assert_valid(value)


def test_shortlist_duplicate_ticker_and_rank() -> None:
    value = _packet()
    value["active_shortlist"] = [
        _shortlist_row("QQQ", 7),
        _shortlist_row("QQQ", 7),
    ]
    diagnostics = _diagnostics(value)
    assert Diagnostic.DUPLICATE_SHORTLIST_TICKER in diagnostics
    assert Diagnostic.DUPLICATE_SHORTLIST_RANK in diagnostics


def test_selected_shortlist_requires_universe_but_watch_only_does_not() -> None:
    selected = _packet()
    selected["active_shortlist"] = [_shortlist_row("OUT", 1, "SELECTED")]
    _assert_has(
        selected,
        Diagnostic.SHORTLIST_TICKER_NOT_IN_PROPOSED_BUY_UNIVERSE,
    )
    watch = _packet()
    watch["active_shortlist"] = [_shortlist_row("OUT", 1, "WATCH_ONLY")]
    _assert_valid(watch)


def test_exposure_duplicate_and_self_reference() -> None:
    value = _packet()
    row = {
        "ticker": "QQQ",
        "overlaps_with": ["QQQ"],
        "overlap_assessment": "HIGH",
        "rationale": "Reported overlap.",
        "reference_ids": [],
    }
    value["exposure_overlap_diagnostics"] = [row, deepcopy(row)]
    diagnostics = _diagnostics(value)
    assert Diagnostic.DUPLICATE_EXPOSURE_OVERLAP_TICKER in diagnostics
    assert Diagnostic.EXPOSURE_OVERLAP_SELF_REFERENCE in diagnostics


@pytest.mark.parametrize(
    ("action", "membership_required"),
    [
        ("KEEP_EXISTING", False),
        ("HOLD_NO_NEW_BUDGET", False),
        ("WATCHLIST_NO_TRADE", False),
        ("NEW_ORDER", True),
        ("REPLACE_EXISTING", True),
        ("CANCEL_EXISTING", False),
    ],
)
def test_exact_buy_delta_universe_membership_classes(
    action: str,
    membership_required: bool,
) -> None:
    value = _packet_for_buy_action(action, ticker="OUT", budget=None)
    diagnostics = _diagnostics(value)
    assert (
        Diagnostic.BUY_TICKER_NOT_IN_PROPOSED_BUY_UNIVERSE in diagnostics
    ) is membership_required


@pytest.mark.parametrize(
    ("action", "budget", "valid"),
    [
        (
            action,
            budget,
            not (
                action in {"NEW_ORDER", "REPLACE_EXISTING"}
                and budget == 0
            ),
        )
        for action, budget in product(
            (
                "KEEP_EXISTING",
                "HOLD_NO_NEW_BUDGET",
                "WATCHLIST_NO_TRADE",
                "NEW_ORDER",
                "REPLACE_EXISTING",
                "CANCEL_EXISTING",
            ),
            (None, 0, 10_000),
        )
    ],
)
def test_complete_buy_action_budget_matrix(
    action: str,
    budget: int | None,
    valid: bool,
) -> None:
    value = _packet_for_buy_action(action, budget=budget)
    diagnostics = _diagnostics(value)
    assert (Diagnostic.BUY_ACTION_BUDGET_INCONSISTENT not in diagnostics) is valid


def test_duplicate_buy_does_not_suppress_row_budget_check() -> None:
    value = _packet_for_buy_action("NEW_ORDER", budget=0)
    value["buy_side_delta_table"].append(_buy_row("QQQ", "NEW_ORDER", 0))
    diagnostics = _diagnostics(value)
    assert Diagnostic.DUPLICATE_BUY_TICKER in diagnostics
    assert Diagnostic.BUY_ACTION_BUDGET_INCONSISTENT in diagnostics


@pytest.mark.parametrize(
    ("action", "execution_present", "valid"),
    [
        ("KEEP_EXISTING", False, True),
        ("KEEP_EXISTING", True, True),
        ("HOLD_NO_NEW_BUDGET", False, True),
        ("HOLD_NO_NEW_BUDGET", True, False),
        ("WATCHLIST_NO_TRADE", False, True),
        ("WATCHLIST_NO_TRADE", True, False),
        ("NEW_ORDER", False, False),
        ("NEW_ORDER", True, True),
        ("REPLACE_EXISTING", False, False),
        ("REPLACE_EXISTING", True, True),
        ("CANCEL_EXISTING", False, False),
        ("CANCEL_EXISTING", True, True),
    ],
)
def test_complete_buy_action_plan_presence_matrix(
    action: str,
    execution_present: bool,
    valid: bool,
) -> None:
    value = _packet_for_buy_action(action, budget=10_000)
    execution_action = (
        "KEEP_EXISTING"
        if action in {"HOLD_NO_NEW_BUDGET", "WATCHLIST_NO_TRADE"}
        else action
    )
    value["execution_plan_drafts_8_5"] = (
        [_buy_execution(action=execution_action)] if execution_present else []
    )
    diagnostics = _diagnostics(value)
    assert (Diagnostic.BUY_EXECUTION_CORRESPONDENCE_INVALID not in diagnostics) is valid


def test_orphan_and_mismatched_buy_execution_are_rejected() -> None:
    orphan = _packet()
    orphan["execution_plan_drafts_8_5"] = [_buy_execution("VGT", "NEW_ORDER")]
    _assert_has(orphan, Diagnostic.BUY_EXECUTION_CORRESPONDENCE_INVALID)
    mismatch = _packet_for_buy_action("NEW_ORDER", budget=1)
    mismatch["execution_plan_drafts_8_5"] = [
        _buy_execution(action="REPLACE_EXISTING")
    ]
    _assert_has(mismatch, Diagnostic.BUY_EXECUTION_CORRESPONDENCE_INVALID)


def test_buy_execution_action_kind_mapping() -> None:
    value = _packet_for_buy_action("NEW_ORDER", budget=1)
    value["execution_plan_drafts_8_5"][0]["plan_kind"] = "KEEP_EXISTING_LADDER"
    _assert_has(value, Diagnostic.BUY_EXECUTION_ACTION_KIND_INCONSISTENT)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda row: row.update(proposed_steps=[]),
        lambda row: row["proposed_steps"].append(_step("rung-1", -100, 1)),
        lambda row: row["proposed_steps"].append(_step("rung-2", -50, 1)),
        lambda row: row["proposed_steps"][0].update(proposed_weight_bps=0),
        lambda row: row["proposed_steps"][0].update(proposed_weight_bps=9_999),
    ],
)
def test_buy_execution_step_invariants(
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    value = _packet_for_buy_action("NEW_ORDER", budget=1)
    mutator(value["execution_plan_drafts_8_5"][0])
    _assert_has(value, Diagnostic.BUY_EXECUTION_STEPS_INCONSISTENT)


def test_buy_execution_exactly_sixteen_steps_is_semantically_valid() -> None:
    value = _packet_for_buy_action("NEW_ORDER", budget=1)
    steps = [
        _step(
            label=f"rung-{index + 1}",
            offset=-(index + 1),
            weight=625,
        )
        for index in range(16)
    ]
    value["execution_plan_drafts_8_5"][0]["proposed_steps"] = steps

    structural = validate_step2_decision_packet_v2(value)
    assert structural.structure_valid is True
    assert structural.schema_valid is True
    result = _assert_valid(value)
    assert len(steps) == 16
    assert Diagnostic.BUY_EXECUTION_STEPS_INCONSISTENT not in result.diagnostics


def test_keep_zero_steps_and_cancel_day_null_expiry_are_valid() -> None:
    keep = _packet_for_buy_action("KEEP_EXISTING")
    keep["execution_plan_drafts_8_5"] = [
        _buy_execution(action="KEEP_EXISTING", steps=[])
    ]
    _assert_valid(keep)
    cancel = _packet_for_buy_action("CANCEL_EXISTING")
    _assert_valid(cancel)


def test_cancel_requires_no_steps_day_and_null_expiry() -> None:
    with_steps = _packet_for_buy_action("CANCEL_EXISTING")
    with_steps["execution_plan_drafts_8_5"][0]["proposed_steps"] = [_step()]
    _assert_has(with_steps, Diagnostic.BUY_EXECUTION_STEPS_INCONSISTENT)
    gtd = _packet_for_buy_action("CANCEL_EXISTING")
    gtd["execution_plan_drafts_8_5"][0].update(
        proposed_time_in_force="GTD",
        proposed_expiry_date="2026-07-20",
    )
    _assert_has(gtd, Diagnostic.BUY_EXECUTION_STEPS_INCONSISTENT)


def test_duplicate_buy_execution_suppresses_correspondence_only() -> None:
    value = _packet_for_buy_action("NEW_ORDER", budget=1)
    duplicate = deepcopy(value["execution_plan_drafts_8_5"][0])
    duplicate["plan_kind"] = "KEEP_EXISTING_LADDER"
    value["execution_plan_drafts_8_5"].append(duplicate)
    diagnostics = _diagnostics(value)
    assert Diagnostic.DUPLICATE_BUY_EXECUTION_TICKER in diagnostics
    assert Diagnostic.BUY_EXECUTION_CORRESPONDENCE_INVALID not in diagnostics
    assert Diagnostic.BUY_EXECUTION_ACTION_KIND_INCONSISTENT in diagnostics


@pytest.mark.parametrize(
    ("action", "quantity", "replacement", "fields_valid"),
    [
        ("HOLD_NO_SELL", None, None, True),
        ("HOLD_NO_SELL", 0, None, True),
        ("HOLD_NO_SELL", 1, None, False),
        ("HOLD_NO_SELL", None, "VGT", False),
        ("SELL", None, None, True),
        ("SELL", 0, None, False),
        ("SELL", 1, None, True),
    ],
)
def test_sell_field_matrix(
    action: str,
    quantity: int | None,
    replacement: str | None,
    fields_valid: bool,
) -> None:
    value = _packet()
    value["sell_side_delta_table_8_2"] = [
        _sell_row(action=action, quantity=quantity, replacement=replacement)
    ]
    if action == "SELL":
        value["sell_execution_plan_drafts_8_6"] = [
            _sell_execution(quantity=quantity)
        ]
    diagnostics = _diagnostics(value)
    assert (
        Diagnostic.SELL_ACTION_FIELDS_INCONSISTENT not in diagnostics
    ) is fields_valid


def test_sell_replacement_self_and_universe_rules() -> None:
    self_replace = _packet()
    self_replace["sell_side_delta_table_8_2"] = [
        _sell_row(action="SELL", replacement="QQQ")
    ]
    self_replace["sell_execution_plan_drafts_8_6"] = [_sell_execution()]
    _assert_has(self_replace, Diagnostic.SELL_REPLACEMENT_SELF_REFERENCE)
    outside = _packet()
    outside["sell_side_delta_table_8_2"] = [
        _sell_row(action="SELL", replacement="OUT")
    ]
    outside["sell_execution_plan_drafts_8_6"] = [_sell_execution()]
    _assert_has(
        outside,
        Diagnostic.SELL_REPLACEMENT_TICKER_NOT_IN_PROPOSED_BUY_UNIVERSE,
    )


@pytest.mark.parametrize(
    ("action", "execution_present", "valid"),
    [
        ("HOLD_NO_SELL", False, True),
        ("HOLD_NO_SELL", True, False),
        ("SELL", False, False),
        ("SELL", True, True),
    ],
)
def test_sell_action_plan_matrix(
    action: str,
    execution_present: bool,
    valid: bool,
) -> None:
    value = _packet()
    value["sell_side_delta_table_8_2"] = [_sell_row(action=action)]
    value["sell_execution_plan_drafts_8_6"] = (
        [_sell_execution()] if execution_present else []
    )
    diagnostics = _diagnostics(value)
    assert (
        Diagnostic.SELL_EXECUTION_CORRESPONDENCE_INVALID not in diagnostics
    ) is valid


@pytest.mark.parametrize(
    ("sell_quantity", "execution_quantity", "valid"),
    [
        (None, None, True),
        (1, 1, True),
        (None, 1, False),
        (1, None, False),
        (1, 2, False),
        (0, 0, False),
    ],
)
def test_sell_quantity_correspondence(
    sell_quantity: int | None,
    execution_quantity: int | None,
    valid: bool,
) -> None:
    value = _packet()
    value["sell_side_delta_table_8_2"] = [
        _sell_row(action="SELL", quantity=sell_quantity)
    ]
    value["sell_execution_plan_drafts_8_6"] = [
        _sell_execution(quantity=execution_quantity)
    ]
    diagnostics = _diagnostics(value)
    assert (Diagnostic.SELL_EXECUTION_QUANTITY_INCONSISTENT not in diagnostics) is valid


def test_duplicate_sell_and_execution_suppress_correspondence_and_quantity() -> None:
    value = _packet()
    value["sell_side_delta_table_8_2"] = [
        _sell_row(action="SELL", quantity=1),
        _sell_row(action="SELL", quantity=2),
    ]
    value["sell_execution_plan_drafts_8_6"] = [
        _sell_execution(quantity=3),
        _sell_execution(quantity=4),
    ]
    diagnostics = _diagnostics(value)
    assert Diagnostic.DUPLICATE_SELL_TICKER in diagnostics
    assert Diagnostic.DUPLICATE_SELL_EXECUTION_TICKER in diagnostics
    assert Diagnostic.SELL_EXECUTION_CORRESPONDENCE_INVALID not in diagnostics
    assert Diagnostic.SELL_EXECUTION_QUANTITY_INCONSISTENT not in diagnostics


@pytest.mark.parametrize(
    ("buy_action", "sell_action", "contradictory"),
    [
        (buy, sell, buy in {"NEW_ORDER", "REPLACE_EXISTING"} and sell == "SELL")
        for buy, sell in product(
            (
                "KEEP_EXISTING",
                "HOLD_NO_NEW_BUDGET",
                "WATCHLIST_NO_TRADE",
                "NEW_ORDER",
                "REPLACE_EXISTING",
                "CANCEL_EXISTING",
            ),
            ("HOLD_NO_SELL", "SELL"),
        )
    ],
)
def test_complete_same_ticker_buy_sell_matrix(
    buy_action: str,
    sell_action: str,
    contradictory: bool,
) -> None:
    diagnostics = _diagnostics(_packet_for_buy_sell(buy_action, sell_action))
    assert (
        Diagnostic.SAME_TICKER_BUY_SELL_CONTRADICTION in diagnostics
    ) is contradictory


def test_duplicate_buy_or_sell_suppresses_same_ticker_check() -> None:
    value = _packet_for_buy_sell("NEW_ORDER", "SELL")
    value["buy_side_delta_table"].append(_buy_row("QQQ", "NEW_ORDER", 1))
    assert Diagnostic.SAME_TICKER_BUY_SELL_CONTRADICTION not in _diagnostics(value)


@pytest.mark.parametrize(
    ("budget", "valid"),
    [(None, True), (0, False), (1, True)],
)
def test_rotation_budget_matrix(budget: int | None, valid: bool) -> None:
    value = _valid_single_rotation(budget)
    diagnostics = _diagnostics(value)
    assert (Diagnostic.ROTATION_BUDGET_INCONSISTENT not in diagnostics) is valid


def _valid_single_rotation(budget: int | None = None) -> dict[str, Any]:
    value = _packet()
    value["proposed_buy_universe"] = ["B"]
    value["buy_side_delta_table"] = [
        _buy_row("A", "CANCEL_EXISTING"),
        _buy_row("B", "NEW_ORDER", 1),
    ]
    value["execution_plan_drafts_8_5"] = [
        _buy_execution("A", "CANCEL_EXISTING"),
        _buy_execution("B", "NEW_ORDER"),
    ]
    value["rotation_decision_layer_8_15"] = [_rotation("A", "B", budget)]
    return value


def _rotation_source_resolution_packet(
    *,
    cancel_source: bool,
    sell_source: bool,
) -> dict[str, Any]:
    value = _packet()
    value["proposed_buy_universe"] = ["DST"]
    value["buy_side_delta_table"] = [_buy_row("DST", "NEW_ORDER", 1)]
    value["execution_plan_drafts_8_5"] = [
        _buy_execution("DST", "NEW_ORDER")
    ]
    value["sell_side_delta_table_8_2"] = []
    value["sell_execution_plan_drafts_8_6"] = []
    if cancel_source:
        value["buy_side_delta_table"].insert(
            0,
            _buy_row("SRC", "CANCEL_EXISTING"),
        )
        value["execution_plan_drafts_8_5"].insert(
            0,
            _buy_execution("SRC", "CANCEL_EXISTING"),
        )
    if sell_source:
        value["sell_side_delta_table_8_2"] = [
            _sell_row("SRC", "SELL")
        ]
        value["sell_execution_plan_drafts_8_6"] = [
            _sell_execution("SRC")
        ]
    value["rotation_decision_layer_8_15"] = [_rotation("SRC", "DST")]
    return value


@pytest.mark.parametrize(
    ("cancel_source", "sell_source", "valid"),
    [
        (False, False, False),
        (True, False, True),
        (False, True, True),
        (True, True, False),
    ],
)
def test_rotation_source_requires_exactly_one_qualifying_action(
    cancel_source: bool,
    sell_source: bool,
    valid: bool,
) -> None:
    result = _validate(
        _rotation_source_resolution_packet(
            cancel_source=cancel_source,
            sell_source=sell_source,
        )
    )
    assert result.structural_validation_passed is True
    assert result.semantic_validation_performed is True
    if valid:
        assert result.semantic_valid is True
        assert result.diagnostics == ()
    else:
        assert result.semantic_valid is False
        assert result.diagnostics == (
            Diagnostic.ROTATION_ENDPOINT_INCONSISTENT,
        )


def test_dual_rotation_source_with_cycle_is_ordered_and_deduplicated() -> None:
    value = _rotation_source_resolution_packet(
        cancel_source=True,
        sell_source=True,
    )
    value["rotation_decision_layer_8_15"].extend(
        (_rotation("X", "Y"), _rotation("Y", "X"))
    )
    reversed_value = deepcopy(value)
    reversed_value["rotation_decision_layer_8_15"].reverse()

    for candidate in (value, reversed_value):
        result = _validate(candidate)
        assert result.semantic_valid is False
        assert result.diagnostics == (
            Diagnostic.ROTATION_CYCLE,
            Diagnostic.ROTATION_ENDPOINT_INCONSISTENT,
        )
        assert len(result.diagnostics) == len(set(result.diagnostics))
        assert result.diagnostics.count(
            Diagnostic.ROTATION_ENDPOINT_INCONSISTENT
        ) == 1


def _rotation_graph_packet(edges: list[tuple[str, str]]) -> dict[str, Any]:
    value = _packet()
    value["rotation_decision_layer_8_15"] = [
        _rotation(source, destination) for source, destination in edges
    ]
    return value


@pytest.mark.parametrize(
    "edges",
    [
        [],
        [("A", "B")],
        [("A", "B"), ("B", "C")],
        [("A", "C"), ("B", "C")],
        [("A", "B"), ("A", "C")],
        [("A", "B"), ("C", "D")],
    ],
)
def test_rotation_acyclic_graph_shapes_do_not_emit_cycle(
    edges: list[tuple[str, str]],
) -> None:
    assert Diagnostic.ROTATION_CYCLE not in _diagnostics(
        _rotation_graph_packet(edges)
    )


@pytest.mark.parametrize(
    "edges",
    [
        [("A", "B"), ("B", "A")],
        [("A", "B"), ("B", "C"), ("C", "A")],
        [("A", "B"), ("B", "C"), ("C", "D"), ("D", "A")],
    ],
)
def test_rotation_cycles_of_every_required_shape(edges: list[tuple[str, str]]) -> None:
    assert Diagnostic.ROTATION_CYCLE in _diagnostics(_rotation_graph_packet(edges))


def test_rotation_duplicate_edge_in_cycle_is_deduplicated() -> None:
    value = _rotation_graph_packet([("A", "B"), ("A", "B"), ("B", "A")])
    diagnostics = _diagnostics(value)
    assert Diagnostic.DUPLICATE_ROTATION_PAIR in diagnostics
    assert Diagnostic.ROTATION_CYCLE in diagnostics


def test_rotation_self_edge_has_no_redundant_cycle_diagnostic() -> None:
    value = _rotation_graph_packet([("A", "A"), ("A", "A")])
    diagnostics = _diagnostics(value)
    assert Diagnostic.DUPLICATE_ROTATION_PAIR in diagnostics
    assert Diagnostic.ROTATION_SELF_REFERENCE in diagnostics
    assert Diagnostic.ROTATION_CYCLE not in diagnostics


def test_rotation_cycle_runs_despite_endpoint_mismatch_and_row_order() -> None:
    first = _rotation_graph_packet([("A", "B"), ("B", "C"), ("C", "A")])
    second = deepcopy(first)
    second["rotation_decision_layer_8_15"].reverse()
    for value in (first, second):
        diagnostics = _diagnostics(value)
        assert Diagnostic.ROTATION_CYCLE in diagnostics
        assert Diagnostic.ROTATION_ENDPOINT_INCONSISTENT in diagnostics


def test_rotation_endpoint_rules_and_duplicate_suppression() -> None:
    _assert_valid(_valid_single_rotation())
    invalid = _valid_single_rotation()
    invalid["buy_side_delta_table"][1]["proposed_action"] = "HOLD_NO_NEW_BUDGET"
    invalid["execution_plan_drafts_8_5"] = [
        invalid["execution_plan_drafts_8_5"][0]
    ]
    _assert_has(invalid, Diagnostic.ROTATION_ENDPOINT_INCONSISTENT)
    duplicate = _valid_single_rotation()
    duplicate["buy_side_delta_table"].append(_buy_row("B", "NEW_ORDER", 1))
    assert Diagnostic.ROTATION_ENDPOINT_INCONSISTENT not in _diagnostics(duplicate)


_COLD_CASES = [
    (triggered, conclusion, nonempty, valid)
    for triggered, conclusion, nonempty, valid in (
        (False, "NOT_TRIGGERED", False, True),
        (False, "NOT_TRIGGERED", True, False),
        (False, "PRESERVE_HEADROOM", False, False),
        (False, "PRESERVE_HEADROOM", True, False),
        (False, "PROPOSE_DEPLOYMENT", False, False),
        (False, "PROPOSE_DEPLOYMENT", True, False),
        (False, "INSUFFICIENT_EVIDENCE", False, False),
        (False, "INSUFFICIENT_EVIDENCE", True, False),
        (True, "NOT_TRIGGERED", False, False),
        (True, "NOT_TRIGGERED", True, False),
        (True, "PRESERVE_HEADROOM", False, True),
        (True, "PRESERVE_HEADROOM", True, True),
        (True, "PROPOSE_DEPLOYMENT", False, False),
        (True, "PROPOSE_DEPLOYMENT", True, True),
        (True, "INSUFFICIENT_EVIDENCE", False, True),
        (True, "INSUFFICIENT_EVIDENCE", True, True),
    )
]


@pytest.mark.parametrize(
    ("triggered", "conclusion", "nonempty", "valid"),
    _COLD_CASES,
)
def test_complete_cold_regime_matrix(
    triggered: bool,
    conclusion: str,
    nonempty: bool,
    valid: bool,
) -> None:
    value = _packet()
    value["cold_regime_review_proposal"] = {
        "reported_triggered": triggered,
        "candidate_tickers": ["QQQ"] if nonempty else [],
        "conclusion_claim": conclusion,
        "rationale": "Reported cold review.",
        "reference_ids": [],
    }
    diagnostics = _diagnostics(value)
    assert (Diagnostic.COLD_REGIME_PROPOSAL_INCONSISTENT not in diagnostics) is valid


def test_only_valid_cold_deployment_checks_universe() -> None:
    value = _packet()
    value["cold_regime_review_proposal"] = {
        "reported_triggered": True,
        "candidate_tickers": ["OUT"],
        "conclusion_claim": "PROPOSE_DEPLOYMENT",
        "rationale": "Reported cold review.",
        "reference_ids": [],
    }
    _assert_has(
        value,
        Diagnostic.COLD_REGIME_DEPLOYMENT_CANDIDATE_NOT_IN_PROPOSED_BUY_UNIVERSE,
    )
    value["cold_regime_review_proposal"]["reported_triggered"] = False
    diagnostics = _diagnostics(value)
    assert Diagnostic.COLD_REGIME_PROPOSAL_INCONSISTENT in diagnostics
    assert (
        Diagnostic.COLD_REGIME_DEPLOYMENT_CANDIDATE_NOT_IN_PROPOSED_BUY_UNIVERSE
        not in diagnostics
    )


def _redeployment(
    proposal: str,
    sources: list[str],
    destinations: list[str],
    budget: int | None,
) -> dict[str, Any]:
    return {
        "source_tickers": sources,
        "destination_tickers": destinations,
        "proposal": proposal,
        "proposed_budget_cents": budget,
        "rationale": "Reported redeployment proposal.",
        "reference_ids": [],
    }


_REDEPLOYMENT_MATRIX = [
    (proposal, source_state, destination_state, budget)
    for proposal, source_state, destination_state, budget in product(
        ("NO_REDEPLOYMENT", "REDEPLOY", "PRESERVE_HEADROOM"),
        (False, True),
        (False, True),
        (None, 0, 1),
    )
]


@pytest.mark.parametrize(
    ("proposal", "has_sources", "has_destinations", "budget"),
    _REDEPLOYMENT_MATRIX,
)
def test_complete_post_cancel_matrix(
    proposal: str,
    has_sources: bool,
    has_destinations: bool,
    budget: int | None,
) -> None:
    sources = ["QQQ"] if has_sources else []
    destinations = ["VGT"] if has_destinations else []
    expected_valid = (
        proposal in {"NO_REDEPLOYMENT", "PRESERVE_HEADROOM"}
        and not destinations
        and budget is None
    ) or (
        proposal == "REDEPLOY"
        and bool(sources)
        and bool(destinations)
        and budget == 1
    )
    value = _packet()
    value["post_cancel_redeployment_proposal"] = _redeployment(
        proposal, sources, destinations, budget
    )
    diagnostics = _diagnostics(value)
    assert (
        Diagnostic.POST_CANCEL_REDEPLOYMENT_PROPOSAL_INCONSISTENT
        not in diagnostics
    ) is expected_valid


def test_redeployment_overlap_and_endpoint_rules() -> None:
    overlap = _packet()
    overlap["post_cancel_redeployment_proposal"] = _redeployment(
        "REDEPLOY", ["QQQ"], ["QQQ"], 1
    )
    diagnostics = _diagnostics(overlap)
    assert Diagnostic.POST_CANCEL_REDEPLOYMENT_OVERLAP in diagnostics
    assert Diagnostic.POST_CANCEL_REDEPLOYMENT_PROPOSAL_INCONSISTENT in diagnostics

    valid = _packet()
    valid["proposed_buy_universe"] = ["DST"]
    valid["buy_side_delta_table"] = [
        _buy_row("SRC", "CANCEL_EXISTING"),
        _buy_row("DST", "NEW_ORDER", 1),
    ]
    valid["execution_plan_drafts_8_5"] = [
        _buy_execution("SRC", "CANCEL_EXISTING"),
        _buy_execution("DST", "NEW_ORDER"),
    ]
    valid["post_cancel_redeployment_proposal"] = _redeployment(
        "REDEPLOY", ["SRC"], ["DST"], 1
    )
    _assert_valid(valid)

    invalid = deepcopy(valid)
    invalid["post_cancel_redeployment_proposal"]["destination_tickers"] = ["OUT"]
    _assert_has(
        invalid,
        Diagnostic.POST_CANCEL_REDEPLOYMENT_ENDPOINT_INCONSISTENT,
    )


def test_invalid_redeployment_matrix_suppresses_endpoint_check() -> None:
    value = _packet()
    value["post_cancel_redeployment_proposal"] = _redeployment(
        "REDEPLOY", [], ["OUT"], 0
    )
    diagnostics = _diagnostics(value)
    assert Diagnostic.POST_CANCEL_REDEPLOYMENT_PROPOSAL_INCONSISTENT in diagnostics
    assert Diagnostic.POST_CANCEL_REDEPLOYMENT_ENDPOINT_INCONSISTENT not in diagnostics


def test_no_trade_without_observations_is_semantically_valid() -> None:
    result = _assert_valid(_no_trade_packet())
    assert result.packet_mode == "NO_TRADE"


def test_no_trade_supplied_observations_receive_ordinary_semantics() -> None:
    value = _no_trade_packet()
    value["no_trade_reason"]["reason_code"] = "SOURCE_CONFLICT"
    value["market_observations"] = {
        "schema_version": MARKET_OBSERVATIONS_SCHEMA_VERSION,
        "observations": [_market_row()],
    }
    value["market_observations"]["observations"][0]["week_52_low"] = 600.0
    _assert_has(value, Diagnostic.WEEK_52_RANGE_INVALID)


def test_extra_proposed_universe_ticker_needs_no_observation() -> None:
    value = _packet()
    value["proposed_buy_universe"].append("NOOBS")
    _assert_valid(value)


def test_diagnostic_enum_exact_order_count_and_removed_members() -> None:
    expected = (
        "structural_prerequisite_failed",
        "snapshot_capture_failed",
        "snapshot_revalidation_failed",
        "snapshot_identity_mismatch",
        "duplicate_market_observation_ticker",
        "market_close_claim_incomplete",
        "market_numeric_claim_retrieval_timestamp_missing",
        "technical_metric_source_missing",
        "week_52_range_invalid",
        "reported_issue_claim_inconsistent",
        "duplicate_shortlist_ticker",
        "duplicate_shortlist_rank",
        "shortlist_ticker_not_in_proposed_buy_universe",
        "duplicate_exposure_overlap_ticker",
        "exposure_overlap_self_reference",
        "duplicate_buy_ticker",
        "buy_ticker_not_in_proposed_buy_universe",
        "buy_action_budget_inconsistent",
        "duplicate_rotation_pair",
        "rotation_self_reference",
        "rotation_cycle",
        "rotation_budget_inconsistent",
        "duplicate_sell_ticker",
        "sell_action_fields_inconsistent",
        "sell_replacement_self_reference",
        "sell_replacement_ticker_not_in_proposed_buy_universe",
        "same_ticker_buy_sell_contradiction",
        "rotation_endpoint_inconsistent",
        "duplicate_buy_execution_ticker",
        "buy_execution_correspondence_invalid",
        "buy_execution_action_kind_inconsistent",
        "buy_execution_steps_inconsistent",
        "duplicate_sell_execution_ticker",
        "sell_execution_correspondence_invalid",
        "sell_execution_quantity_inconsistent",
        "cold_regime_proposal_inconsistent",
        "cold_regime_deployment_candidate_not_in_proposed_buy_universe",
        "post_cancel_redeployment_proposal_inconsistent",
        "post_cancel_redeployment_overlap",
        "post_cancel_redeployment_endpoint_inconsistent",
    )
    assert tuple(item.value for item in Diagnostic) == expected
    assert len(Diagnostic) == 40
    assert MAX_SEMANTIC_DIAGNOSTICS == 36
    assert not hasattr(Diagnostic, "SHORTLIST_RANK_SEQUENCE_INVALID")
    assert not hasattr(
        Diagnostic,
        "PROPOSED_BUY_UNIVERSE_MARKET_OBSERVATION_MISSING",
    )


def test_semantic_diagnostics_are_ordered_and_globally_deduplicated() -> None:
    value = _packet()
    rows = value["market_observations"]["observations"]
    rows.extend((deepcopy(rows[0]), deepcopy(rows[0])))
    for row in rows:
        row["week_52_low"] = 600.0
        row["reported_issue_codes"] = ["MISSING_LAST_CLOSE_CLAIM"]
    value["buy_side_delta_table"] = [
        _buy_row("OUT", "NEW_ORDER", 0),
        _buy_row("OUT", "NEW_ORDER", 0),
    ]
    diagnostics = _diagnostics(value)
    assert diagnostics == tuple(
        item for item in Diagnostic if item in set(diagnostics)
    )
    assert len(diagnostics) == len(set(diagnostics))
    assert len(diagnostics) <= MAX_SEMANTIC_DIAGNOSTICS


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


_SEMANTIC_MODULE = semantic_contract.__name__
_SEMANTIC_BASENAME = _SEMANTIC_MODULE.rsplit(".", 1)[-1]
_SEMANTIC_RELATIVE_PATH = Path(
    "src/investment_orchestrator/validators"
) / f"{_SEMANTIC_BASENAME}.py"
_SEMANTIC_SYMBOLS = frozenset(
    {
        "validate_step2_decision_packet_semantics",
        "Step2DecisionPacketSemanticDiagnostic",
        "Step2DecisionPacketSemanticValidationResult",
        "SEMANTIC_VALIDATION_RESULT_VERSION",
    }
)


def _semantic_reference_findings(relative_path: str, source: str) -> list[str]:
    findings: list[str] = []
    tree = ast.parse(source, filename=relative_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _SEMANTIC_MODULE or alias.name.startswith(
                    f"{_SEMANTIC_MODULE}."
                ):
                    findings.append(f"{relative_path}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == _SEMANTIC_MODULE or module.endswith(
                f".{_SEMANTIC_BASENAME}"
            ):
                findings.append(f"{relative_path}: from-import {module}")
            for alias in node.names:
                if alias.name in _SEMANTIC_SYMBOLS:
                    findings.append(f"{relative_path}: symbol {alias.name}")
        elif isinstance(node, ast.Name) and node.id in _SEMANTIC_SYMBOLS:
            findings.append(f"{relative_path}: symbol {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _SEMANTIC_SYMBOLS:
            findings.append(f"{relative_path}: symbol {node.attr}")
    for marker in (
        _SEMANTIC_MODULE,
        *_SEMANTIC_SYMBOLS,
        SEMANTIC_VALIDATION_RESULT_VERSION,
    ):
        if marker in source:
            findings.append(f"{relative_path}: text {marker}")
    return sorted(set(findings))


def test_semantic_reference_detector_handles_import_alias_symbol_and_literal() -> None:
    cases = (
        f"import {_SEMANTIC_MODULE}\n",
        f"from {_SEMANTIC_MODULE} import validate_step2_decision_packet_semantics\n",
        "handler = Step2DecisionPacketSemanticValidationResult\n",
        f"VERSION = {SEMANTIC_VALIDATION_RESULT_VERSION!r}\n",
    )
    assert all(
        _semantic_reference_findings(f"synthetic/{index}.py", source)
        for index, source in enumerate(cases)
    )


def test_no_production_consumer_references_semantic_contract() -> None:
    repo_root = _repo_root()
    production_root = repo_root / "src" / "investment_orchestrator"
    contract_path = repo_root / _SEMANTIC_RELATIVE_PATH
    findings: list[str] = []
    for path in sorted(production_root.rglob("*.py")):
        if path == contract_path:
            continue
        relative = path.relative_to(repo_root).as_posix()
        findings.extend(
            _semantic_reference_findings(
                relative,
                path.read_text(encoding="utf-8"),
            )
        )
    assert sorted(set(findings)) == [], "\n".join(sorted(set(findings)))


def test_semantic_module_uses_only_public_b2_symbols_and_no_broad_except() -> None:
    path = _repo_root() / _SEMANTIC_RELATIVE_PATH
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    b2_module = (
        "investment_orchestrator.validators.validate_step2_decision_packet_v2"
    )
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == b2_module
    ]
    assert len(imports) == 1
    assert {alias.name for alias in imports[0].names} == {
        "DECISION_PACKET_SCHEMA_VERSION",
        "MAX_JSON_NESTING_DEPTH",
        "MAX_JSON_NODE_COUNT",
        "validate_step2_decision_packet_v2",
    }
    assert all(not alias.name.startswith("_") for alias in imports[0].names)

    def contains_exception_handler(source_text: str) -> bool:
        parsed = ast.parse(source_text)
        return any(
            isinstance(node, (ast.Try, ast.TryStar))
            for node in ast.walk(parsed)
        )

    assert not any(
        isinstance(node, (ast.Try, ast.TryStar)) for node in ast.walk(tree)
    )
    assert contains_exception_handler(
        "try:\n    pass\nexcept Exception:\n    pass\n"
    )
    assert contains_exception_handler(
        "try:\n    pass\nexcept* Exception:\n    pass\n"
    )


def test_semantic_module_has_no_io_clock_authority_or_order_capability() -> None:
    source = (_repo_root() / _SEMANTIC_RELATIVE_PATH).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert imported_roots.isdisjoint(
        {
            "os",
            "pathlib",
            "subprocess",
            "shutil",
            "time",
            "datetime",
            "socket",
            "requests",
            "settings",
            "portfolio",
            "permissions",
            "orders",
            "broker",
        }
    )
    forbidden = (
        "Path(",
        "open(",
        "getenv",
        "allowed_actions",
        "publication_eligible",
        "order_compilation_allowed",
        "final_safety_passed",
        "compile_ready",
        "broker",
        "submit",
        "transmit",
    )
    assert all(marker not in source for marker in forbidden)


def test_deferred_authority_fields_and_evaluations_do_not_exist() -> None:
    result = _assert_valid(_packet())
    forbidden_fields = (
        "freshness_ok",
        "market_data_usable",
        "effective_allowed_buy_universe",
        "candidate_valid",
        "permission",
        "publication_eligible",
        "order_compilation_allowed",
        "final_safety_passed",
        "compile_ready",
        "broker",
        "order_id",
    )
    assert all(not hasattr(result, name) for name in forbidden_fields)
