from __future__ import annotations

import ast
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator
import pytest
from referencing import Registry, Resource

import investment_orchestrator.validators.validate_step2_decision_packet_v2 as packet_contract
from investment_orchestrator.validators.validate_step2_decision_packet_v2 import (
    CANDIDATE_VALIDITY_EVALUATED,
    DECISION_PACKET_MODES,
    DECISION_PACKET_SCHEMA_FILENAME,
    DECISION_PACKET_SCHEMA_VERSION,
    DECISION_PACKET_VALIDATION_RESULT_VERSION,
    FRESHNESS_EVALUATION_PERFORMED,
    IDENTITY_ONLY,
    MAX_CANONICAL_BYTES,
    MAX_EXECUTION_STEPS,
    MAX_IDENTIFIER_LENGTH,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODE_COUNT,
    MAX_RATIONALE_LENGTH,
    MAX_REFERENCE_COUNT,
    MAX_SAFE_INTEGER,
    MAX_TICKER_ARRAY,
    NOT_AUTHORIZATION,
    NO_TRADE_REASON_CODES,
    NO_TRADE_REASONS_REQUIRING_MARKET_OBSERVATIONS,
    PERMISSION_EFFECT_NONE,
    SEMANTIC_VALIDATION_PERFORMED,
    UNIVERSE_RESOLUTION_PERFORMED,
    VALIDATION_BOOLEAN_COERCION_ERROR,
    Step2DecisionPacketDiagnostic,
    Step2DecisionPacketValidationResult,
    validate_step2_decision_packet_v2,
)
from investment_orchestrator.validators.validate_step2_market_observations import (
    MARKET_OBSERVATIONS_SCHEMA_FILENAME,
    MARKET_OBSERVATIONS_SCHEMA_VERSION,
)


TOP_LEVEL_FIELDS = (
    "schema_version",
    "mode",
    "market_observations",
    "proposed_buy_universe",
    "active_shortlist",
    "exposure_overlap_diagnostics",
    "buy_side_delta_table",
    "rotation_decision_layer_8_15",
    "sell_side_delta_table_8_2",
    "execution_plan_drafts_8_5",
    "sell_execution_plan_drafts_8_6",
    "cold_regime_review_proposal",
    "post_cancel_redeployment_proposal",
    "reported_assumptions_and_data_gaps",
)

DECISION_ARRAYS = (
    "buy_side_delta_table",
    "rotation_decision_layer_8_15",
    "sell_side_delta_table_8_2",
    "execution_plan_drafts_8_5",
    "sell_execution_plan_drafts_8_6",
)

NO_TRADE_EMPTY_ARRAYS = (
    "proposed_buy_universe",
    "active_shortlist",
    "exposure_overlap_diagnostics",
    *DECISION_ARRAYS,
)

AUTHORITY_FIELDS = (
    "effective_allowed_buy_universe",
    "decision_builder_ready_for_audit",
    "freshness_ok",
    "data_gap",
    "same_day_close_required",
    "holiday_resolution_ok",
    "market_data_usable",
    "candidate_valid",
    "ready",
    "permission",
    "allowed_actions",
    "blocked_actions",
    "gate_result",
    "publication_eligible",
    "order_compilation_allowed",
    "final_safety_passed",
    "compile_ready",
    "final_action",
    "final_limit_price",
    "quantity",
    "broker",
    "account",
    "venue",
    "route",
    "submit",
    "transmit",
    "order_id",
    "client_order_id",
    "lot_id",
    "tax_lot_id",
)


def _valid_market_row(*, ticker: str = "QQQ") -> dict[str, Any]:
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
        "reported_last_close_source": "reported-primary-source",
        "reported_price_source": "reported-primary-source",
        "reported_technicals_source": "reported-primary-source",
        "reported_retrieved_at_utc": "2026-07-14T01:02:03.123456Z",
        "source_evidence_refs": ["observation:QQQ:close"],
        "reported_issue_codes": [],
        "observation_notes": ["Reported observation only."],
    }


def _valid_market_observations() -> dict[str, Any]:
    return {
        "schema_version": MARKET_OBSERVATIONS_SCHEMA_VERSION,
        "observations": [_valid_market_row()],
    }


def _active_shortlist_row() -> dict[str, Any]:
    return {
        "ticker": "QQQ",
        "rank": 1,
        "role_claim": "broad growth exposure",
        "proposal_status": "SELECTED",
        "rationale": "Reported shortlist proposal.",
        "reference_ids": ["shortlist:QQQ"],
        "reported_risk_notes": ["Concentration is a reported risk claim."],
    }


def _overlap_row() -> dict[str, Any]:
    return {
        "ticker": "QQQ",
        "overlaps_with": ["VGT"],
        "overlap_assessment": "MODERATE",
        "rationale": "Reported overlap proposal.",
        "reference_ids": ["overlap:QQQ:VGT"],
    }


def _buy_row(
    *,
    ticker: str = "QQQ",
    action: str = "HOLD_NO_NEW_BUDGET",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "proposed_action": action,
        "proposed_budget_cents": None,
        "rationale": "Proposal only; no authority is implied.",
        "reference_ids": [f"buy:{ticker}"],
    }


def _rotation_row() -> dict[str, Any]:
    return {
        "from_ticker": "QQQ",
        "to_ticker": "VGT",
        "proposal_type": "SAME_ROLE_ROTATION",
        "proposed_budget_cents": 50_000,
        "rationale": "Reported same-role rotation proposal.",
        "reference_ids": ["rotation:QQQ:VGT"],
    }


def _sell_row(*, action: str = "HOLD_NO_SELL") -> dict[str, Any]:
    return {
        "ticker": "QQQ",
        "proposed_action": action,
        "proposed_share_quantity": None,
        "replacement_ticker": None,
        "rationale": "Reported sell-side proposal.",
        "reference_ids": ["sell:QQQ"],
    }


def _execution_step() -> dict[str, Any]:
    return {
        "step_label": "first proposed rung",
        "proposed_offset_bps": -50,
        "proposed_weight_bps": 5000,
    }


def _execution_row(
    *,
    time_in_force: str = "DAY",
    expiry: str | None = None,
) -> dict[str, Any]:
    return {
        "ticker": "QQQ",
        "proposal_action": "KEEP_EXISTING",
        "plan_kind": "KEEP_EXISTING_LADDER",
        "proposed_time_in_force": time_in_force,
        "proposed_expiry_date": expiry,
        "proposed_steps": [_execution_step()],
        "rationale": "Non-executable planning proposal.",
        "reference_ids": ["execution:QQQ"],
    }


def _sell_execution_row(
    *,
    time_in_force: str = "DAY",
    expiry: str | None = None,
) -> dict[str, Any]:
    return {
        "ticker": "QQQ",
        "proposal_action": "SELL",
        "plan_kind": "SINGLE_LIMIT_SELL_PROPOSAL",
        "proposed_share_quantity": None,
        "proposed_limit_rule": "Reported limit construction rule only.",
        "proposed_lot_policy": "LTCG_ELIGIBLE_ONLY",
        "proposed_time_in_force": time_in_force,
        "proposed_expiry_date": expiry,
        "rationale": "Non-executable sell planning proposal.",
        "reference_ids": ["sell-execution:QQQ"],
    }


def _cold_review() -> dict[str, Any]:
    return {
        "reported_triggered": False,
        "candidate_tickers": ["QQQ"],
        "conclusion_claim": "NOT_TRIGGERED",
        "rationale": "Reported cold-regime conclusion only.",
        "reference_ids": ["cold-review"],
    }


def _redeployment_review() -> dict[str, Any]:
    return {
        "source_tickers": ["QQQ"],
        "destination_tickers": ["VGT"],
        "proposal": "PRESERVE_HEADROOM",
        "proposed_budget_cents": None,
        "rationale": "Reported redeployment proposal only.",
        "reference_ids": ["redeployment-review"],
    }


def _assumption_row() -> dict[str, Any]:
    return {
        "category": "ASSUMPTION",
        "code": "REPORTED_INPUT_ASSUMPTION",
        "detail": "Descriptive assumption; no deterministic authority.",
        "related_tickers": ["QQQ"],
        "reference_ids": ["assumption:1"],
    }


def _valid_draft() -> dict[str, Any]:
    return {
        "schema_version": DECISION_PACKET_SCHEMA_VERSION,
        "mode": "DECISION_DRAFT",
        "market_observations": _valid_market_observations(),
        "proposed_buy_universe": ["QQQ"],
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


def _comprehensive_draft() -> dict[str, Any]:
    value = _valid_draft()
    value.update(
        {
            "active_shortlist": [_active_shortlist_row()],
            "exposure_overlap_diagnostics": [_overlap_row()],
            "rotation_decision_layer_8_15": [_rotation_row()],
            "sell_side_delta_table_8_2": [_sell_row()],
            "execution_plan_drafts_8_5": [_execution_row()],
            "sell_execution_plan_drafts_8_6": [_sell_execution_row()],
            "cold_regime_review_proposal": _cold_review(),
            "post_cancel_redeployment_proposal": _redeployment_review(),
            "reported_assumptions_and_data_gaps": [_assumption_row()],
        }
    )
    return value


def _valid_no_trade(
    reason_code: str = "MISSING_REQUIRED_DATA",
    *,
    market_observations: dict[str, Any] | None | object = ...,
) -> dict[str, Any]:
    if market_observations is ...:
        market_value = (
            _valid_market_observations()
            if reason_code in NO_TRADE_REASONS_REQUIRING_MARKET_OBSERVATIONS
            else None
        )
    else:
        market_value = market_observations
    return {
        "schema_version": DECISION_PACKET_SCHEMA_VERSION,
        "mode": "NO_TRADE",
        "no_trade_reason": {
            "reason_code": reason_code,
            "reason_detail": "Reported no-trade reason; not a permission decision.",
            "reference_ids": ["no-trade:reason"],
        },
        "market_observations": market_value,
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
        "reported_assumptions_and_data_gaps": [_assumption_row()],
    }


def _canonical_oracle(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _schema_from_file(filename: str) -> dict[str, Any]:
    return json.loads(
        (_repo_root() / "schemas" / filename).read_text(encoding="utf-8")
    )


def _external_schema_validator() -> Any:
    market_schema = _schema_from_file(MARKET_OBSERVATIONS_SCHEMA_FILENAME)
    registry = Registry().with_resource(
        market_schema["$id"],
        Resource.from_contents(market_schema),
    )
    return packet_contract._ExactDraft202012Validator(
        _schema_from_file(DECISION_PACKET_SCHEMA_FILENAME),
        format_checker=packet_contract._FORMAT_CHECKER,
        registry=registry,
    )


def _external_schema_valid(value: Any) -> bool:
    return _external_schema_validator().is_valid(value)


def _assert_valid(value: Any) -> Step2DecisionPacketValidationResult:
    result = validate_step2_decision_packet_v2(value)
    assert result.structure_valid is True
    assert result.schema_valid is True
    assert result.diagnostics == ()
    assert result.canonical_identity_sha256 == hashlib.sha256(
        _canonical_oracle(value)
    ).hexdigest()
    assert result.canonical_size_bytes == len(_canonical_oracle(value))
    assert _external_schema_valid(value) is True
    return result


def _assert_diagnostic(
    value: Any,
    diagnostic: Step2DecisionPacketDiagnostic,
    *,
    structure_valid: bool,
) -> Step2DecisionPacketValidationResult:
    result = validate_step2_decision_packet_v2(value)
    assert result.structure_valid is structure_valid
    assert result.schema_valid is False
    assert result.diagnostics == (diagnostic,)
    return result


def _mapping_chain(depth: int) -> dict[str, Any]:
    root: dict[str, Any] = {}
    current = root
    for _ in range(depth):
        child: dict[str, Any] = {}
        current["child"] = child
        current = child
    return root


def _list_chain(depth: int) -> list[Any]:
    root: list[Any] = []
    current = root
    for _ in range(depth):
        child: list[Any] = []
        current.append(child)
        current = child
    return root


def test_schema_file_is_exact_validator_schema_and_draft_is_meta_valid() -> None:
    schema = _schema_from_file(DECISION_PACKET_SCHEMA_FILENAME)
    assert schema == packet_contract._STEP2_DECISION_PACKET_SCHEMA
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert (
        schema["properties"]["market_observations"]["anyOf"][0]["$ref"]
        == _schema_from_file(MARKET_OBSERVATIONS_SCHEMA_FILENAME)["$id"]
    )
    Draft202012Validator.check_schema(schema)


def test_schema_and_result_versions_are_closed() -> None:
    result = _assert_valid(_valid_draft())
    assert result.schema_version == "step2_decision_packet_v2"
    assert result.result_version == "step2_decision_packet_validation_result_v1"


def test_minimal_draft_with_hold_row_is_valid_and_non_authorizing() -> None:
    result = _assert_valid(_valid_draft())
    assert result.packet_mode == "DECISION_DRAFT"
    assert result.market_observations_structure_valid is True
    assert result.identity_only is True
    assert result.not_authorization is True
    assert result.permission_effect == "none"


def test_hold_only_draft_allows_empty_proposed_buy_universe_without_authority() -> None:
    value = _valid_draft()
    value["proposed_buy_universe"] = []
    value["buy_side_delta_table"] = [_buy_row(action="HOLD_NO_NEW_BUDGET")]

    result = _assert_valid(value)

    assert result.packet_mode == "DECISION_DRAFT"
    assert result.semantic_validation_performed is False
    assert result.freshness_evaluation_performed is False
    assert result.universe_resolution_performed is False
    assert result.candidate_validity_evaluated is False
    assert not hasattr(result, "allowed_actions")


@pytest.mark.parametrize(
    ("section", "row"),
    [
        ("buy_side_delta_table", _buy_row(action="NEW_ORDER")),
        ("sell_side_delta_table_8_2", _sell_row(action="SELL")),
        ("rotation_decision_layer_8_15", _rotation_row()),
        ("execution_plan_drafts_8_5", _execution_row()),
        ("sell_execution_plan_drafts_8_6", _sell_execution_row()),
    ],
)
def test_each_decision_bearing_draft_form_is_structurally_valid(
    section: str,
    row: dict[str, Any],
) -> None:
    value = _valid_draft()
    for field_name in DECISION_ARRAYS:
        value[field_name] = []
    value[section] = [row]
    if section.startswith("sell"):
        value["proposed_buy_universe"] = []
    _assert_valid(value)


@pytest.mark.parametrize("reason_code", NO_TRADE_REASON_CODES)
def test_every_no_trade_reason_code_has_a_valid_branch(reason_code: str) -> None:
    result = _assert_valid(_valid_no_trade(reason_code))
    assert result.packet_mode == "NO_TRADE"
    expected_market_validity = (
        True
        if reason_code in NO_TRADE_REASONS_REQUIRING_MARKET_OBSERVATIONS
        else None
    )
    assert result.market_observations_structure_valid is expected_market_validity


@pytest.mark.parametrize(
    "reason_code",
    ["MISSING_REQUIRED_DATA", "POLICY_BLOCKED", "MANUAL_REVIEW_REQUIRED"],
)
def test_optional_no_trade_market_observations_accept_null_or_valid_object(
    reason_code: str,
) -> None:
    _assert_valid(_valid_no_trade(reason_code, market_observations=None))
    _assert_valid(
        _valid_no_trade(
            reason_code,
            market_observations=_valid_market_observations(),
        )
    )


def test_draft_forbids_no_trade_reason() -> None:
    value = _valid_draft()
    value["no_trade_reason"] = _valid_no_trade()["no_trade_reason"]
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_draft_requires_at_least_one_decision_bearing_row() -> None:
    value = _valid_draft()
    for field_name in DECISION_ARRAYS:
        value[field_name] = []
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize(
    ("field_name", "nonempty_value"),
    [
        ("proposed_buy_universe", ["QQQ"]),
        ("active_shortlist", [_active_shortlist_row()]),
        ("exposure_overlap_diagnostics", [_overlap_row()]),
        ("buy_side_delta_table", [_buy_row()]),
        ("rotation_decision_layer_8_15", [_rotation_row()]),
        ("sell_side_delta_table_8_2", [_sell_row()]),
        ("execution_plan_drafts_8_5", [_execution_row()]),
        ("sell_execution_plan_drafts_8_6", [_sell_execution_row()]),
    ],
)
def test_no_trade_requires_every_proposal_array_empty(
    field_name: str,
    nonempty_value: list[Any],
) -> None:
    draft = _valid_draft()
    draft[field_name] = deepcopy(nonempty_value)
    _assert_valid(draft)

    value = _valid_no_trade()
    value[field_name] = deepcopy(nonempty_value)
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize(
    ("field_name", "value_factory"),
    [
        ("cold_regime_review_proposal", _cold_review),
        ("post_cancel_redeployment_proposal", _redeployment_review),
    ],
)
def test_no_trade_requires_review_proposals_null(
    field_name: str,
    value_factory: Any,
) -> None:
    value = _valid_no_trade()
    value[field_name] = value_factory()
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_no_trade_requires_at_least_one_reported_assumption_or_gap() -> None:
    value = _valid_no_trade()
    value["reported_assumptions_and_data_gaps"] = []
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize(
    "reason_code", sorted(NO_TRADE_REASONS_REQUIRING_MARKET_OBSERVATIONS)
)
def test_no_trade_reasons_requiring_market_reject_null(reason_code: str) -> None:
    value = _valid_no_trade(reason_code, market_observations=None)
    result = _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MARKET_OBSERVATIONS_INVALID,
        structure_valid=True,
    )
    assert result.market_observations_structure_valid is False


def test_no_trade_unknown_reason_is_public_schema_failure() -> None:
    value = _valid_no_trade("UNKNOWN_NO_TRADE_REASON")
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize(
    "mode",
    ["ORDER_READY", "AUDIT_READY", "PUBLISHABLE", "EXECUTABLE", "COMPILE_READY"],
)
def test_authority_looking_modes_receive_closed_mode_diagnostic(mode: str) -> None:
    value = _valid_draft()
    value["mode"] = mode
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MODE_INVALID,
        structure_valid=True,
    )


def test_missing_value_has_highest_priority() -> None:
    result = _assert_diagnostic(
        None,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MISSING,
        structure_valid=False,
    )
    assert result.canonical_identity_sha256 is None
    assert result.canonical_size_bytes is None


@pytest.mark.parametrize("field_name", TOP_LEVEL_FIELDS)
def test_every_required_top_level_field_is_permanently_covered(
    field_name: str,
) -> None:
    value = _valid_draft()
    del value[field_name]
    expected = Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID
    if field_name == "schema_version":
        expected = Step2DecisionPacketDiagnostic.DECISION_PACKET_VERSION_INVALID
    elif field_name == "mode":
        expected = Step2DecisionPacketDiagnostic.DECISION_PACKET_MODE_INVALID
    _assert_diagnostic(value, expected, structure_valid=True)


def test_no_trade_reason_is_required_only_in_no_trade_mode() -> None:
    value = _valid_no_trade()
    del value["no_trade_reason"]
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


NESTED_ROWS_AND_FIELDS = (
    ("active_shortlist", _active_shortlist_row),
    ("exposure_overlap_diagnostics", _overlap_row),
    ("buy_side_delta_table", _buy_row),
    ("rotation_decision_layer_8_15", _rotation_row),
    ("sell_side_delta_table_8_2", _sell_row),
    ("execution_plan_drafts_8_5", _execution_row),
    ("sell_execution_plan_drafts_8_6", _sell_execution_row),
    ("reported_assumptions_and_data_gaps", _assumption_row),
)


@pytest.mark.parametrize(("section", "row_factory"), NESTED_ROWS_AND_FIELDS)
def test_every_nested_row_required_field_is_permanently_covered(
    section: str,
    row_factory: Any,
) -> None:
    row = row_factory()
    for field_name in tuple(row):
        value = _comprehensive_draft()
        del value[section][0][field_name]
        _assert_diagnostic(
            value,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )


@pytest.mark.parametrize(
    ("section", "row_factory"),
    NESTED_ROWS_AND_FIELDS,
)
def test_unknown_field_rejected_at_every_nested_row_level(
    section: str,
    row_factory: Any,
) -> None:
    value = _comprehensive_draft()
    value[section][0]["unexpected"] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize(
    ("field_name", "factory"),
    [
        ("cold_regime_review_proposal", _cold_review),
        ("post_cancel_redeployment_proposal", _redeployment_review),
    ],
)
def test_review_object_required_and_unknown_fields_are_closed(
    field_name: str,
    factory: Any,
) -> None:
    for nested_field in factory():
        value = _comprehensive_draft()
        del value[field_name][nested_field]
        _assert_diagnostic(
            value,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )
    value = _comprehensive_draft()
    value[field_name]["unexpected"] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_no_trade_reason_required_and_unknown_fields_are_closed() -> None:
    for field_name in ("reason_code", "reason_detail", "reference_ids"):
        value = _valid_no_trade()
        del value["no_trade_reason"][field_name]
        _assert_diagnostic(
            value,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )
    value = _valid_no_trade()
    value["no_trade_reason"]["unexpected"] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_execution_step_required_and_unknown_fields_are_closed() -> None:
    for field_name in _execution_step():
        value = _comprehensive_draft()
        del value["execution_plan_drafts_8_5"][0]["proposed_steps"][0][field_name]
        _assert_diagnostic(
            value,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )
    value = _comprehensive_draft()
    value["execution_plan_drafts_8_5"][0]["proposed_steps"][0]["unexpected"] = 1
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_unknown_top_level_field_is_rejected() -> None:
    value = _valid_draft()
    value["unexpected"] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


class _DictSubclass(dict[str, Any]):
    pass


class _ListSubclass(list[Any]):
    pass


class _HostileMapping(Mapping[str, Any]):
    touched = False

    def __getitem__(self, key: str) -> Any:
        type(self).touched = True
        raise AssertionError("hostile mapping accessed")

    def __iter__(self) -> Iterator[str]:
        type(self).touched = True
        raise AssertionError("hostile mapping iterated")

    def __len__(self) -> int:
        type(self).touched = True
        raise AssertionError("hostile mapping sized")


class _CustomObject:
    pass


@pytest.mark.parametrize(
    "value",
    [
        _DictSubclass(),
        _ListSubclass(),
        (1, 2),
        {1, 2},
        frozenset({1, 2}),
        (item for item in (1, 2)),
        b"bytes",
        bytearray(b"bytes"),
        _CustomObject(),
        {1: "non-string-key"},
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_unsupported_root_types_fail_before_schema_processing(value: Any) -> None:
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_custom_mapping_is_rejected_without_invoking_hostile_methods() -> None:
    _HostileMapping.touched = False
    _assert_diagnostic(
        _HostileMapping(),
        Step2DecisionPacketDiagnostic.DECISION_PACKET_STRUCTURE_INVALID,
        structure_valid=False,
    )
    assert _HostileMapping.touched is False


@pytest.mark.parametrize(
    "nested_value",
    [
        _DictSubclass(),
        _ListSubclass(),
        (1, 2),
        {1, 2},
        b"bytes",
        bytearray(b"bytes"),
        _CustomObject(),
    ],
)
def test_unsupported_nested_values_are_not_coerced(nested_value: Any) -> None:
    value = _valid_draft()
    value["buy_side_delta_table"][0]["rationale"] = nested_value
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_STRUCTURE_INVALID,
        structure_valid=False,
    )


@pytest.mark.parametrize(
    ("path", "boundary", "invalid"),
    [
        (("buy_side_delta_table", 0, "proposed_budget_cents"), MAX_SAFE_INTEGER, MAX_SAFE_INTEGER + 1),
        (("sell_side_delta_table_8_2", 0, "proposed_share_quantity"), MAX_SAFE_INTEGER, MAX_SAFE_INTEGER + 1),
        (("post_cancel_redeployment_proposal", "proposed_budget_cents"), MAX_SAFE_INTEGER, MAX_SAFE_INTEGER + 1),
        (("execution_plan_drafts_8_5", 0, "proposed_steps", 0, "proposed_offset_bps"), -MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER - 1),
    ],
)
def test_safe_integer_boundaries(path: tuple[Any, ...], boundary: int, invalid: int) -> None:
    def assign(value: dict[str, Any], assigned: Any) -> None:
        target: Any = value
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = assigned

    valid = _comprehensive_draft()
    assign(valid, boundary)
    _assert_valid(valid)

    rejected = _comprehensive_draft()
    assign(rejected, invalid)
    _assert_diagnostic(
        rejected,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize(
    ("proposed_offset_bps", "valid"),
    [
        (MAX_SAFE_INTEGER, True),
        (MAX_SAFE_INTEGER + 1, False),
        (-MAX_SAFE_INTEGER, True),
        (-MAX_SAFE_INTEGER - 1, False),
        (0, True),
        (True, False),
        (1.0, False),
    ],
)
def test_proposed_offset_bps_exact_bounds_and_integer_type(
    proposed_offset_bps: Any,
    valid: bool,
) -> None:
    value = _comprehensive_draft()
    value["execution_plan_drafts_8_5"][0]["proposed_steps"][0][
        "proposed_offset_bps"
    ] = proposed_offset_bps

    if valid:
        _assert_valid(value)
    else:
        _assert_diagnostic(
            value,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )


def test_nonnegative_integer_nullability_and_rank_boundaries() -> None:
    for assigned in (0, None, MAX_SAFE_INTEGER):
        value = _comprehensive_draft()
        value["buy_side_delta_table"][0]["proposed_budget_cents"] = assigned
        value["sell_side_delta_table_8_2"][0]["proposed_share_quantity"] = assigned
        _assert_valid(value)

    for assigned in (-1, 1.0, True, "1"):
        value = _comprehensive_draft()
        value["buy_side_delta_table"][0]["proposed_budget_cents"] = assigned
        _assert_diagnostic(
            value,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )

    for rank in (1, 128):
        value = _comprehensive_draft()
        value["active_shortlist"][0]["rank"] = rank
        _assert_valid(value)
    for rank in (0, 129, True, 1.0):
        value = _comprehensive_draft()
        value["active_shortlist"][0]["rank"] = rank
        _assert_diagnostic(
            value,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )


@pytest.mark.parametrize(
    ("section", "field_name"),
    [
        ("buy_side_delta_table", "proposed_budget_cents"),
        ("sell_side_delta_table_8_2", "proposed_share_quantity"),
        ("execution_plan_drafts_8_5", "proposed_time_in_force"),
    ],
)
def test_boolean_is_not_integer_or_string_enum(
    section: str,
    field_name: str,
) -> None:
    value = _comprehensive_draft()
    value[section][0][field_name] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize(
    ("time_in_force", "expiry", "valid"),
    [
        ("DAY", None, True),
        ("DAY", "2026-07-15", False),
        ("GTD", "2026-07-15", True),
        ("GTD", "2024-02-29", True),
        ("GTD", None, False),
        ("GTD", "2026-02-29", False),
        ("GTD", "2026-02-30", False),
        ("GTD", "2026-07-15T00:00:00Z", False),
        ("GTD", "2026-07-15T00:00:00+00:00", False),
        ("GTD", "2026/07/15", False),
        ("GTD", "2026-7-15", False),
        ("GTD", " 2026-07-15", False),
        ("GTD", "2026-07-15 ", False),
        ("GTD", "July 15, 2026", False),
    ],
)
@pytest.mark.parametrize(
    ("section", "factory"),
    [
        ("execution_plan_drafts_8_5", _execution_row),
        ("sell_execution_plan_drafts_8_6", _sell_execution_row),
    ],
)
def test_time_in_force_and_semantic_expiry_date_parity(
    time_in_force: str,
    expiry: str | None,
    valid: bool,
    section: str,
    factory: Any,
) -> None:
    value = _comprehensive_draft()
    value[section][0] = factory(time_in_force=time_in_force, expiry=expiry)
    python_valid = validate_step2_decision_packet_v2(value).schema_valid
    schema_valid = _external_schema_valid(value)
    assert python_valid is schema_valid
    assert python_valid is valid


@pytest.mark.parametrize(
    "ticker",
    ["A", "BRK.B", "ABC-1", "ABCDEFGHIJ"],
)
def test_valid_ticker_boundaries(ticker: str) -> None:
    value = _valid_draft()
    value["proposed_buy_universe"] = [ticker]
    _assert_valid(value)


@pytest.mark.parametrize(
    "ticker",
    ["", "qqq", "1QQ", " QQQ", "QQQ ", "ABCDEFGHIJK", "BRK/B", "A_B", "Å"],
)
def test_invalid_tickers_are_not_normalized(ticker: str) -> None:
    value = _valid_draft()
    value["proposed_buy_universe"] = [ticker]
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_ticker_reference_and_prose_boundaries() -> None:
    value = _comprehensive_draft()
    value["proposed_buy_universe"] = [f"A{i}" for i in range(MAX_TICKER_ARRAY)]
    value["buy_side_delta_table"][0]["reference_ids"] = [
        f"r{i}" for i in range(MAX_REFERENCE_COUNT)
    ]
    value["buy_side_delta_table"][0]["reference_ids"][0] = "r" * MAX_IDENTIFIER_LENGTH
    value["buy_side_delta_table"][0]["rationale"] = "r" * MAX_RATIONALE_LENGTH
    _assert_valid(value)

    invalid_values = []
    too_many_tickers = _comprehensive_draft()
    too_many_tickers["proposed_buy_universe"] = [
        f"A{i}" for i in range(MAX_TICKER_ARRAY + 1)
    ]
    invalid_values.append(too_many_tickers)
    duplicate_ticker = _comprehensive_draft()
    duplicate_ticker["proposed_buy_universe"] = ["QQQ", "QQQ"]
    invalid_values.append(duplicate_ticker)
    too_many_refs = _comprehensive_draft()
    too_many_refs["buy_side_delta_table"][0]["reference_ids"] = [
        f"r{i}" for i in range(MAX_REFERENCE_COUNT + 1)
    ]
    invalid_values.append(too_many_refs)
    duplicate_ref = _comprehensive_draft()
    duplicate_ref["buy_side_delta_table"][0]["reference_ids"] = ["r", "r"]
    invalid_values.append(duplicate_ref)
    long_identifier = _comprehensive_draft()
    long_identifier["buy_side_delta_table"][0]["reference_ids"] = [
        "r" * (MAX_IDENTIFIER_LENGTH + 1)
    ]
    invalid_values.append(long_identifier)
    long_rationale = _comprehensive_draft()
    long_rationale["buy_side_delta_table"][0]["rationale"] = "r" * (
        MAX_RATIONALE_LENGTH + 1
    )
    invalid_values.append(long_rationale)
    empty_rationale = _comprehensive_draft()
    empty_rationale["buy_side_delta_table"][0]["rationale"] = ""
    invalid_values.append(empty_rationale)

    for invalid in invalid_values:
        _assert_diagnostic(
            invalid,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )


def test_execution_step_count_and_weight_boundaries() -> None:
    value = _comprehensive_draft()
    value["execution_plan_drafts_8_5"][0]["proposed_steps"] = [
        _execution_step() for _ in range(MAX_EXECUTION_STEPS)
    ]
    value["execution_plan_drafts_8_5"][0]["proposed_steps"][0][
        "proposed_weight_bps"
    ] = 10_000
    _assert_valid(value)

    too_many = deepcopy(value)
    too_many["execution_plan_drafts_8_5"][0]["proposed_steps"].append(
        _execution_step()
    )
    _assert_diagnostic(
        too_many,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )

    for invalid_weight in (-1, 10_001, True):
        invalid = _comprehensive_draft()
        invalid["execution_plan_drafts_8_5"][0]["proposed_steps"][0][
            "proposed_weight_bps"
        ] = invalid_weight
        _assert_diagnostic(
            invalid,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )


ENUM_FIELD_CASES = (
    (("active_shortlist", 0, "proposal_status"), ("SELECTED", "WATCH_ONLY")),
    (("exposure_overlap_diagnostics", 0, "overlap_assessment"), ("LOW", "MODERATE", "HIGH", "UNKNOWN")),
    (("buy_side_delta_table", 0, "proposed_action"), ("KEEP_EXISTING", "HOLD_NO_NEW_BUDGET", "WATCHLIST_NO_TRADE", "NEW_ORDER", "REPLACE_EXISTING", "CANCEL_EXISTING")),
    (("rotation_decision_layer_8_15", 0, "proposal_type"), ("SAME_ROLE_ROTATION",)),
    (("sell_side_delta_table_8_2", 0, "proposed_action"), ("HOLD_NO_SELL", "SELL")),
    (("execution_plan_drafts_8_5", 0, "proposal_action"), ("KEEP_EXISTING", "NEW_ORDER", "REPLACE_EXISTING", "CANCEL_EXISTING")),
    (("execution_plan_drafts_8_5", 0, "plan_kind"), ("KEEP_EXISTING_LADDER", "NEW_LIMIT_LADDER", "REPLACE_EXISTING_LADDER", "CANCEL_EXISTING_ORDER")),
    (("sell_execution_plan_drafts_8_6", 0, "proposal_action"), ("SELL",)),
    (("sell_execution_plan_drafts_8_6", 0, "plan_kind"), ("SINGLE_LIMIT_SELL_PROPOSAL",)),
    (("sell_execution_plan_drafts_8_6", 0, "proposed_lot_policy"), ("LTCG_ELIGIBLE_ONLY",)),
    (("cold_regime_review_proposal", "conclusion_claim"), ("NOT_TRIGGERED", "PRESERVE_HEADROOM", "PROPOSE_DEPLOYMENT", "INSUFFICIENT_EVIDENCE")),
    (("post_cancel_redeployment_proposal", "proposal"), ("NO_REDEPLOYMENT", "REDEPLOY", "PRESERVE_HEADROOM")),
    (("reported_assumptions_and_data_gaps", 0, "category"), ("ASSUMPTION", "DATA_GAP_CLAIM", "SOURCE_CLAIM", "POLICY_CONCERN", "MANUAL_REVIEW_CLAIM")),
)


def _assign_path(value: dict[str, Any], path: tuple[Any, ...], assigned: Any) -> None:
    target: Any = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = assigned


@pytest.mark.parametrize(("path", "allowed_values"), ENUM_FIELD_CASES)
def test_every_nested_enum_value_and_closed_case_policy(
    path: tuple[Any, ...],
    allowed_values: tuple[str, ...],
) -> None:
    for allowed in allowed_values:
        value = _comprehensive_draft()
        _assign_path(value, path, allowed)
        _assert_valid(value)

    for invalid in (allowed_values[0].lower(), "UNRECOGNIZED_ENUM_VALUE"):
        value = _comprehensive_draft()
        _assign_path(value, path, invalid)
        _assert_diagnostic(
            value,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )


@pytest.mark.parametrize(
    ("section", "row_factory", "maximum"),
    [
        ("active_shortlist", _active_shortlist_row, 32),
        ("exposure_overlap_diagnostics", _overlap_row, 128),
        ("buy_side_delta_table", _buy_row, 128),
        ("rotation_decision_layer_8_15", _rotation_row, 128),
        ("sell_side_delta_table_8_2", _sell_row, 128),
        ("execution_plan_drafts_8_5", _execution_row, 128),
        ("sell_execution_plan_drafts_8_6", _sell_execution_row, 128),
        ("reported_assumptions_and_data_gaps", _assumption_row, 128),
    ],
)
def test_each_top_level_row_array_maximum_and_plus_one(
    section: str,
    row_factory: Any,
    maximum: int,
) -> None:
    value = _comprehensive_draft()
    value[section] = [row_factory() for _ in range(maximum)]
    _assert_valid(value)

    value[section].append(row_factory())
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_risk_note_and_identifier_string_boundaries() -> None:
    value = _comprehensive_draft()
    value["active_shortlist"][0]["role_claim"] = "r" * MAX_IDENTIFIER_LENGTH
    value["active_shortlist"][0]["reported_risk_notes"] = [
        "n" * MAX_RATIONALE_LENGTH for _ in range(MAX_REFERENCE_COUNT)
    ]
    value["execution_plan_drafts_8_5"][0]["proposed_steps"][0][
        "step_label"
    ] = "s" * MAX_IDENTIFIER_LENGTH
    value["reported_assumptions_and_data_gaps"][0]["code"] = (
        "c" * MAX_IDENTIFIER_LENGTH
    )
    _assert_valid(value)

    mutations = (
        ("active_shortlist", 0, "role_claim"),
        ("execution_plan_drafts_8_5", 0, "proposed_steps", 0, "step_label"),
        ("reported_assumptions_and_data_gaps", 0, "code"),
    )
    for path in mutations:
        invalid = _comprehensive_draft()
        _assign_path(invalid, path, "x" * (MAX_IDENTIFIER_LENGTH + 1))
        _assert_diagnostic(
            invalid,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )

    too_many_notes = _comprehensive_draft()
    too_many_notes["active_shortlist"][0]["reported_risk_notes"] = [
        "n" for _ in range(MAX_REFERENCE_COUNT + 1)
    ]
    _assert_diagnostic(
        too_many_notes,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_wrong_root_exact_json_scalars_fail_with_closed_schema_diagnostic() -> None:
    for value in ([], "packet", 1, True, 1.5):
        result = _assert_diagnostic(
            value,
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
        )
        assert result.canonical_identity_sha256 == hashlib.sha256(
            _canonical_oracle(value)
        ).hexdigest()


def test_supported_scalar_canonical_bytes_match_compact_json_oracle() -> None:
    corpus = (
        {},
        [],
        {"unicode": "東京 — café", "escaping": "quote=\" slash=\\\n"},
        {"values": [None, True, False, -0.0, 1.25, -1, 0, MAX_SAFE_INTEGER]},
        {"z": [3, {"b": 2, "a": 1}], "a": "first"},
    )
    for value in corpus:
        result = validate_step2_decision_packet_v2(value)
        oracle = _canonical_oracle(value)
        assert result.canonical_size_bytes == len(oracle)
        assert result.canonical_identity_sha256 == hashlib.sha256(oracle).hexdigest()


def test_schema_python_parity_for_supported_invalid_packet_corpus() -> None:
    corpus: list[dict[str, Any]] = []

    unknown = _valid_draft()
    unknown["unknown"] = 1
    corpus.append(unknown)

    wrong_version = _valid_draft()
    wrong_version["schema_version"] = "wrong"
    corpus.append(wrong_version)

    wrong_mode = _valid_draft()
    wrong_mode["mode"] = "ORDER_READY"
    corpus.append(wrong_mode)

    empty_draft = _valid_draft()
    for section in DECISION_ARRAYS:
        empty_draft[section] = []
    corpus.append(empty_draft)

    bad_market = _valid_draft()
    bad_market["market_observations"]["observations"][0]["ticker"] = "qqq"
    corpus.append(bad_market)

    bad_date = _comprehensive_draft()
    bad_date["execution_plan_drafts_8_5"][0].update(
        proposed_time_in_force="GTD",
        proposed_expiry_date="2026-02-30",
    )
    corpus.append(bad_date)

    bad_number = _comprehensive_draft()
    bad_number["buy_side_delta_table"][0]["proposed_budget_cents"] = 1.0
    corpus.append(bad_number)

    bad_no_trade = _valid_no_trade()
    bad_no_trade["buy_side_delta_table"] = [_buy_row()]
    corpus.append(bad_no_trade)

    for value in corpus:
        assert validate_step2_decision_packet_v2(value).schema_valid is False
        assert _external_schema_valid(value) is False


def test_market_observations_public_contract_is_composed(monkeypatch: pytest.MonkeyPatch) -> None:
    real_validator = packet_contract.validate_step2_market_observations
    calls: list[object] = []

    def recording_validator(value: object) -> Any:
        calls.append(value)
        return real_validator(value)

    monkeypatch.setattr(
        packet_contract,
        "validate_step2_market_observations",
        recording_validator,
    )
    _assert_valid(_valid_draft())
    assert calls
    assert all(call == _valid_market_observations() for call in calls)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda market: market.update(schema_version="wrong"),
        lambda market: market["observations"][0].update(last_close=-1),
        lambda market: market["observations"][0].update(freshness_ok=True),
    ],
)
def test_invalid_market_observations_map_to_closed_composition_diagnostic(
    mutator: Any,
) -> None:
    value = _valid_draft()
    mutator(value["market_observations"])
    result = _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MARKET_OBSERVATIONS_INVALID,
        structure_valid=True,
    )
    assert result.market_observations_structure_valid is False


def test_draft_rejects_null_market_observations_with_composition_diagnostic() -> None:
    value = _valid_draft()
    value["market_observations"] = None
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MARKET_OBSERVATIONS_INVALID,
        structure_valid=True,
    )


def _assert_registry_composition_failure() -> Step2DecisionPacketValidationResult:
    result = _assert_diagnostic(
        _valid_draft(),
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MARKET_OBSERVATIONS_INVALID,
        structure_valid=True,
    )
    assert result.market_observations_structure_valid is False
    assert result.canonical_identity_sha256 is not None
    assert result.canonical_size_bytes is not None
    with pytest.raises(TypeError, match="^" + VALIDATION_BOOLEAN_COERCION_ERROR + "$"):
        bool(result)
    return result


def test_approved_market_observations_registry_succeeds() -> None:
    assert packet_contract._has_approved_market_observations_reference_resource()
    _assert_valid(_valid_draft())


def test_empty_registry_fails_closed_as_market_observations_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(packet_contract, "_SCHEMA_REGISTRY", Registry())
    _assert_registry_composition_failure()


def test_registry_missing_market_observations_resource_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unrelated_resource = Resource.from_contents(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:step2-unrelated-resource",
        }
    )
    monkeypatch.setattr(
        packet_contract,
        "_SCHEMA_REGISTRY",
        Registry().with_resource("urn:step2-unrelated-resource", unrelated_resource),
    )
    _assert_registry_composition_failure()


def test_incompatible_registered_market_observations_resource_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incompatible_contents = dict(
        packet_contract._MARKET_OBSERVATIONS_REFERENCE_ADAPTER
    )
    incompatible_contents["format"] = "incompatible-market-observations-format"
    registry = Registry().with_resource(
        packet_contract._MARKET_OBSERVATIONS_SCHEMA_URI,
        Resource.from_contents(incompatible_contents),
    )
    monkeypatch.setattr(packet_contract, "_SCHEMA_REGISTRY", registry)
    _assert_registry_composition_failure()


def test_malformed_registered_market_observations_resource_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed_resource = Resource.from_contents(
        {"$schema": "https://json-schema.org/draft/2020-12/schema"}
    )
    registry = Registry().with_resource(
        packet_contract._MARKET_OBSERVATIONS_SCHEMA_URI,
        malformed_resource,
    )
    monkeypatch.setattr(packet_contract, "_SCHEMA_REGISTRY", registry)
    _assert_registry_composition_failure()


def test_wrong_market_observations_resource_uri_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_uri_contents = dict(
        packet_contract._MARKET_OBSERVATIONS_REFERENCE_ADAPTER
    )
    wrong_uri_contents["$id"] = "urn:wrong-market-observations-resource-uri"
    registry = Registry().with_resource(
        "urn:wrong-market-observations-resource-uri",
        Resource.from_contents(wrong_uri_contents),
    )
    monkeypatch.setattr(packet_contract, "_SCHEMA_REGISTRY", registry)
    _assert_registry_composition_failure()


def test_schema_registry_resolution_exception_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unresolvable_validator = packet_contract._ExactDraft202012Validator(
        packet_contract._STEP2_DECISION_PACKET_SCHEMA,
        format_checker=packet_contract._FORMAT_CHECKER,
        registry=Registry(),
    )
    monkeypatch.setattr(packet_contract, "_SCHEMA_VALIDATOR", unresolvable_validator)
    _assert_registry_composition_failure()


def _assert_registry_priority_result_contract(
    result: Step2DecisionPacketValidationResult,
) -> None:
    assert result.identity_only is True
    assert result.not_authorization is True
    assert result.permission_effect == "none"
    assert result.semantic_validation_performed is False
    assert result.freshness_evaluation_performed is False
    assert result.universe_resolution_performed is False
    assert result.candidate_validity_evaluated is False


def test_packet_version_precedes_registry_failure_and_reaches_registry_after_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(packet_contract, "_SCHEMA_REGISTRY", Registry())
    value = _valid_draft()
    value["schema_version"] = "wrong-version"

    version_result = validate_step2_decision_packet_v2(value)
    assert version_result.diagnostics == (
        Step2DecisionPacketDiagnostic.DECISION_PACKET_VERSION_INVALID,
    )
    assert version_result.structure_valid is True
    assert version_result.schema_valid is False
    assert version_result.identity_only is True
    assert version_result.not_authorization is True
    _assert_registry_priority_result_contract(version_result)
    with pytest.raises(FrozenInstanceError):
        version_result.schema_valid = True  # type: ignore[misc]
    with pytest.raises(TypeError, match="^" + VALIDATION_BOOLEAN_COERCION_ERROR + "$"):
        bool(version_result)

    value["schema_version"] = DECISION_PACKET_SCHEMA_VERSION
    registry_result = validate_step2_decision_packet_v2(value)
    assert registry_result.diagnostics == (
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MARKET_OBSERVATIONS_INVALID,
    )
    assert registry_result.structure_valid is True
    assert registry_result.schema_valid is False
    assert registry_result.market_observations_structure_valid is False
    assert registry_result.identity_only is True
    assert registry_result.not_authorization is True
    _assert_registry_priority_result_contract(registry_result)
    with pytest.raises(FrozenInstanceError):
        registry_result.schema_valid = True  # type: ignore[misc]
    with pytest.raises(TypeError, match="^" + VALIDATION_BOOLEAN_COERCION_ERROR + "$"):
        bool(registry_result)


def test_packet_mode_precedes_registry_failure_and_reaches_registry_after_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(packet_contract, "_SCHEMA_REGISTRY", Registry())
    value = _valid_draft()
    value["mode"] = "ORDER_READY"

    mode_result = validate_step2_decision_packet_v2(value)
    assert mode_result.diagnostics == (
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MODE_INVALID,
    )
    assert mode_result.structure_valid is True
    assert mode_result.schema_valid is False
    assert mode_result.identity_only is True
    assert mode_result.not_authorization is True
    _assert_registry_priority_result_contract(mode_result)
    with pytest.raises(FrozenInstanceError):
        mode_result.schema_valid = True  # type: ignore[misc]
    with pytest.raises(TypeError, match="^" + VALIDATION_BOOLEAN_COERCION_ERROR + "$"):
        bool(mode_result)

    value["mode"] = "DECISION_DRAFT"
    registry_result = validate_step2_decision_packet_v2(value)
    assert registry_result.diagnostics == (
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MARKET_OBSERVATIONS_INVALID,
    )
    assert registry_result.structure_valid is True
    assert registry_result.schema_valid is False
    assert registry_result.market_observations_structure_valid is False
    assert registry_result.identity_only is True
    assert registry_result.not_authorization is True
    _assert_registry_priority_result_contract(registry_result)
    with pytest.raises(FrozenInstanceError):
        registry_result.schema_valid = True  # type: ignore[misc]
    with pytest.raises(TypeError, match="^" + VALIDATION_BOOLEAN_COERCION_ERROR + "$"):
        bool(registry_result)


def test_registry_failure_precedes_later_packet_schema_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved_registry = packet_contract._SCHEMA_REGISTRY
    monkeypatch.setattr(packet_contract, "_SCHEMA_REGISTRY", Registry())
    value = _valid_draft()
    value["buy_side_delta_table"][0]["unexpected"] = "later schema defect"

    registry_result = validate_step2_decision_packet_v2(value)
    assert registry_result.diagnostics == (
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MARKET_OBSERVATIONS_INVALID,
    )
    assert registry_result.structure_valid is True
    assert registry_result.schema_valid is False
    assert registry_result.market_observations_structure_valid is False
    assert registry_result.identity_only is True
    assert registry_result.not_authorization is True
    _assert_registry_priority_result_contract(registry_result)
    with pytest.raises(FrozenInstanceError):
        registry_result.schema_valid = True  # type: ignore[misc]
    with pytest.raises(TypeError, match="^" + VALIDATION_BOOLEAN_COERCION_ERROR + "$"):
        bool(registry_result)

    monkeypatch.setattr(packet_contract, "_SCHEMA_REGISTRY", approved_registry)
    schema_result = validate_step2_decision_packet_v2(value)
    assert schema_result.diagnostics == (
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
    )
    assert schema_result.structure_valid is True
    assert schema_result.schema_valid is False
    assert schema_result.market_observations_structure_valid is True
    assert schema_result.identity_only is True
    assert schema_result.not_authorization is True
    _assert_registry_priority_result_contract(schema_result)
    with pytest.raises(FrozenInstanceError):
        schema_result.schema_valid = True  # type: ignore[misc]
    with pytest.raises(TypeError, match="^" + VALIDATION_BOOLEAN_COERCION_ERROR + "$"):
        bool(schema_result)


def test_unrelated_schema_validator_assertion_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnexpectedAssertionValidator:
        def is_valid(self, value: object) -> bool:
            raise AssertionError("unexpected schema validator failure")

    monkeypatch.setattr(
        packet_contract,
        "_SCHEMA_VALIDATOR",
        _UnexpectedAssertionValidator(),
    )
    with pytest.raises(
        AssertionError,
        match="^unexpected schema validator failure$",
    ):
        validate_step2_decision_packet_v2(_valid_draft())


def test_mapping_and_list_depth_boundaries() -> None:
    mapping_at_limit = validate_step2_decision_packet_v2(
        _mapping_chain(MAX_JSON_NESTING_DEPTH)
    )
    assert mapping_at_limit.structure_valid is True
    assert mapping_at_limit.diagnostics == (
        Step2DecisionPacketDiagnostic.DECISION_PACKET_VERSION_INVALID,
    )
    _assert_diagnostic(
        _mapping_chain(MAX_JSON_NESTING_DEPTH + 1),
        Step2DecisionPacketDiagnostic.DECISION_PACKET_STRUCTURE_INVALID,
        structure_valid=False,
    )

    list_at_limit = validate_step2_decision_packet_v2(
        _list_chain(MAX_JSON_NESTING_DEPTH)
    )
    assert list_at_limit.structure_valid is True
    assert list_at_limit.diagnostics == (
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
    )
    _assert_diagnostic(
        _list_chain(MAX_JSON_NESTING_DEPTH + 1),
        Step2DecisionPacketDiagnostic.DECISION_PACKET_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_node_count_4096_accepted_and_4097_rejected_structurally() -> None:
    at_limit = {"items": [None] * (MAX_JSON_NODE_COUNT - 2)}
    result = validate_step2_decision_packet_v2(at_limit)
    assert result.structure_valid is True
    assert result.diagnostics == (
        Step2DecisionPacketDiagnostic.DECISION_PACKET_VERSION_INVALID,
    )

    over_limit = {"items": [None] * (MAX_JSON_NODE_COUNT - 1)}
    _assert_diagnostic(
        over_limit,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_STRUCTURE_INVALID,
        structure_valid=False,
    )


def _self_dict_cycle() -> dict[str, Any]:
    value: dict[str, Any] = {}
    value["cycle"] = value
    return value


def _self_list_cycle() -> list[Any]:
    value: list[Any] = []
    value.append(value)
    return value


def _mixed_cycle() -> dict[str, Any]:
    value: dict[str, Any] = {}
    child: list[Any] = [value]
    value["child"] = child
    return value


@pytest.mark.parametrize("value", [_self_dict_cycle(), _self_list_cycle(), _mixed_cycle()])
def test_cycles_fail_closed_without_raw_recursion(value: Any) -> None:
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_shared_acyclic_dict_and_list_aliases_are_copied_by_value() -> None:
    shared_refs = ["shared-ref"]
    shared_row = _assumption_row()
    value = _valid_draft()
    value["buy_side_delta_table"][0]["reference_ids"] = shared_refs
    value["reported_assumptions_and_data_gaps"] = [shared_row, shared_row]
    result = _assert_valid(value)
    captured_identity = result.canonical_identity_sha256

    shared_refs.append("later")
    shared_row["detail"] = "later mutation"
    assert result.canonical_identity_sha256 == captured_identity
    assert (
        validate_step2_decision_packet_v2(value).canonical_identity_sha256
        != captured_identity
    )


def test_canonical_size_exact_boundary_and_plus_one() -> None:
    at_limit = _valid_draft()
    at_limit["padding"] = ""
    base_size = len(_canonical_oracle(at_limit))
    at_limit["padding"] = "x" * (MAX_CANONICAL_BYTES - base_size)
    assert len(_canonical_oracle(at_limit)) == MAX_CANONICAL_BYTES

    boundary_result = validate_step2_decision_packet_v2(at_limit)
    assert boundary_result.structure_valid is True
    assert boundary_result.canonical_size_bytes == MAX_CANONICAL_BYTES
    assert boundary_result.diagnostics == (
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
    )

    over_limit = deepcopy(at_limit)
    over_limit["padding"] += "x"
    over_result = _assert_diagnostic(
        over_limit,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SIZE_EXCEEDED,
        structure_valid=True,
    )
    assert over_result.canonical_identity_sha256 is None
    assert over_result.canonical_size_bytes == MAX_CANONICAL_BYTES + 1


def test_canonical_identity_determinism_order_and_unicode() -> None:
    first = _comprehensive_draft()
    first["reported_assumptions_and_data_gaps"][0]["detail"] = (
        "東京 — café — quote=\" slash=\\ control=\n"
    )
    second = {key: first[key] for key in reversed(list(first))}
    first_result = _assert_valid(first)
    second_result = _assert_valid(second)
    assert first_result.canonical_identity_sha256 == second_result.canonical_identity_sha256
    assert first_result.canonical_size_bytes == second_result.canonical_size_bytes
    assert _assert_valid(first) == first_result

    reordered = deepcopy(first)
    reordered["proposed_buy_universe"] = ["QQQ", "VGT"]
    ordered_identity = _assert_valid(reordered).canonical_identity_sha256
    reordered["proposed_buy_universe"] = ["VGT", "QQQ"]
    assert _assert_valid(reordered).canonical_identity_sha256 != ordered_identity


@pytest.mark.parametrize("authority_field", AUTHORITY_FIELDS)
def test_every_authority_or_execution_field_rejected_at_top_level(
    authority_field: str,
) -> None:
    value = _valid_draft()
    value[authority_field] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


NESTED_OBJECT_LOCATORS = (
    ("no_trade_reason",),
    ("active_shortlist", 0),
    ("exposure_overlap_diagnostics", 0),
    ("buy_side_delta_table", 0),
    ("rotation_decision_layer_8_15", 0),
    ("sell_side_delta_table_8_2", 0),
    ("execution_plan_drafts_8_5", 0),
    ("execution_plan_drafts_8_5", 0, "proposed_steps", 0),
    ("sell_execution_plan_drafts_8_6", 0),
    ("cold_regime_review_proposal",),
    ("post_cancel_redeployment_proposal",),
    ("reported_assumptions_and_data_gaps", 0),
)


@pytest.mark.parametrize("locator", NESTED_OBJECT_LOCATORS)
def test_every_nested_object_level_rejects_authority_fields(
    locator: tuple[Any, ...],
) -> None:
    value = _valid_no_trade() if locator == ("no_trade_reason",) else _comprehensive_draft()
    target: Any = value
    for part in locator:
        target = target[part]
    target["permission"] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize("authority_field", AUTHORITY_FIELDS)
def test_nested_authority_vocabulary_is_not_silently_accepted(
    authority_field: str,
) -> None:
    value = _comprehensive_draft()
    value["buy_side_delta_table"][0][authority_field] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_validation_result_fields_are_frozen_and_non_authorizing() -> None:
    result = _assert_valid(_valid_draft())
    assert result.identity_only is IDENTITY_ONLY is True
    assert result.not_authorization is NOT_AUTHORIZATION is True
    assert result.permission_effect == PERMISSION_EFFECT_NONE == "none"
    assert result.semantic_validation_performed is SEMANTIC_VALIDATION_PERFORMED is False
    assert result.freshness_evaluation_performed is FRESHNESS_EVALUATION_PERFORMED is False
    assert result.universe_resolution_performed is UNIVERSE_RESOLUTION_PERFORMED is False
    assert result.candidate_validity_evaluated is CANDIDATE_VALIDITY_EVALUATED is False
    assert not hasattr(result, "candidate_valid")
    assert not hasattr(result, "market_usable")
    assert not hasattr(result, "allowed_actions")
    assert not hasattr(result, "publication_eligible")
    assert not hasattr(result, "order_compilation_allowed")
    with pytest.raises(FrozenInstanceError):
        result.schema_valid = False  # type: ignore[misc]


def test_boolean_coercion_rejected_for_valid_and_invalid_results() -> None:
    valid = validate_step2_decision_packet_v2(_valid_draft())
    invalid = validate_step2_decision_packet_v2(None)
    for result in (valid, invalid):
        with pytest.raises(TypeError, match="^" + VALIDATION_BOOLEAN_COERCION_ERROR + "$" ):
            bool(result)
        assert result.structure_valid in {True, False}


def test_diagnostic_enum_is_exact_and_priority_is_deterministic() -> None:
    assert {diagnostic.value for diagnostic in Step2DecisionPacketDiagnostic} == {
        "decision_packet_missing",
        "decision_packet_structure_invalid",
        "decision_packet_size_exceeded",
        "decision_packet_version_invalid",
        "decision_packet_mode_invalid",
        "decision_packet_market_observations_invalid",
        "decision_packet_schema_invalid",
    }
    value = _valid_draft()
    value["schema_version"] = "wrong"
    value["mode"] = "ORDER_READY"
    value["market_observations"] = {"bad": True}
    value["unexpected"] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_VERSION_INVALID,
        structure_valid=True,
    )


def test_diagnostic_priority_structure_precedes_size_version_mode_and_schema() -> None:
    value: dict[str, Any] = {
        "schema_version": "wrong-version",
        "mode": "ORDER_READY",
        "market_observations": {"bad": True},
        "unexpected": True,
        "padding": "x" * MAX_CANONICAL_BYTES,
        "unsupported": ("tuple",),
    }
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_diagnostic_priority_size_precedes_version_mode_and_schema() -> None:
    value = _valid_draft()
    value["schema_version"] = "wrong-version"
    value["mode"] = "ORDER_READY"
    value["market_observations"] = {"bad": True}
    value["unexpected"] = True
    value["padding"] = "x" * MAX_CANONICAL_BYTES
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SIZE_EXCEEDED,
        structure_valid=True,
    )


def test_diagnostic_priority_version_precedes_mode_market_and_schema() -> None:
    value = _valid_draft()
    value["schema_version"] = "wrong-version"
    value["mode"] = "ORDER_READY"
    value["market_observations"] = {"bad": True}
    value["unexpected"] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_VERSION_INVALID,
        structure_valid=True,
    )


def test_diagnostic_priority_mode_precedes_market_and_schema() -> None:
    value = _valid_draft()
    value["mode"] = "ORDER_READY"
    value["market_observations"] = {"bad": True}
    value["unexpected"] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MODE_INVALID,
        structure_valid=True,
    )


def test_diagnostic_priority_market_observations_precedes_packet_schema() -> None:
    value = _valid_draft()
    value["market_observations"] = {"bad": True}
    value["unexpected"] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_MARKET_OBSERVATIONS_INVALID,
        structure_valid=True,
    )


def test_diagnostic_priority_remaining_packet_schema_after_earlier_layers() -> None:
    value = _valid_draft()
    value["unexpected"] = True
    _assert_diagnostic(
        value,
        Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_deferred_semantics_remain_structurally_valid_without_claims() -> None:
    value = _comprehensive_draft()
    value["proposed_buy_universe"] = ["QQQ"]
    value["active_shortlist"].append(deepcopy(value["active_shortlist"][0]))
    value["rotation_decision_layer_8_15"][0]["to_ticker"] = "QQQ"
    value["sell_side_delta_table_8_2"][0]["ticker"] = "SPY"
    value["execution_plan_drafts_8_5"][0]["proposal_action"] = "CANCEL_EXISTING"
    value["execution_plan_drafts_8_5"][0]["plan_kind"] = "NEW_LIMIT_LADDER"
    value["execution_plan_drafts_8_5"][0]["proposed_steps"] = [
        {"step_label": "a", "proposed_offset_bps": -10, "proposed_weight_bps": 1},
        {"step_label": "a", "proposed_offset_bps": -20, "proposed_weight_bps": 2},
    ]
    value["market_observations"]["observations"][0]["week_52_low"] = 600.0
    value["market_observations"]["observations"][0]["week_52_high"] = 500.0
    result = _assert_valid(value)
    assert result.semantic_validation_performed is False
    assert result.freshness_evaluation_performed is False
    assert result.universe_resolution_performed is False
    assert result.candidate_validity_evaluated is False


_CONTRACT_MODULE = packet_contract.__name__
_CONTRACT_BASENAME = _CONTRACT_MODULE.rsplit(".", 1)[-1]
_CONTRACT_RELATIVE_PATH = Path(
    "src/investment_orchestrator/validators"
) / f"{_CONTRACT_BASENAME}.py"
_B1_RELATIVE_PATH = Path(
    "src/investment_orchestrator/validators/validate_step2_market_observations.py"
)
_CONTRACT_SYMBOLS = frozenset(
    {
        validate_step2_decision_packet_v2.__name__,
        Step2DecisionPacketValidationResult.__name__,
        Step2DecisionPacketDiagnostic.__name__,
        "DECISION_PACKET_SCHEMA_FILENAME",
        "DECISION_PACKET_SCHEMA_VERSION",
        "DECISION_PACKET_VALIDATION_RESULT_VERSION",
    }
)
_CONTRACT_TEXT_MARKERS = (
    _CONTRACT_MODULE,
    *sorted(_CONTRACT_SYMBOLS),
    DECISION_PACKET_SCHEMA_VERSION,
    DECISION_PACKET_VALIDATION_RESULT_VERSION,
    DECISION_PACKET_SCHEMA_FILENAME,
)


def _contract_reference_findings(relative_path: str, source_text: str) -> list[str]:
    findings: list[str] = []
    try:
        tree = ast.parse(source_text, filename=relative_path)
    except SyntaxError:
        return [f"{relative_path}: AST syntax error"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _CONTRACT_MODULE or alias.name.startswith(
                    f"{_CONTRACT_MODULE}."
                ):
                    findings.append(f"{relative_path}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            imported_module = node.module or ""
            if imported_module == _CONTRACT_MODULE or imported_module.endswith(
                f".{_CONTRACT_BASENAME}"
            ):
                findings.append(f"{relative_path}: from-import {imported_module}")
            for alias in node.names:
                if alias.name in _CONTRACT_SYMBOLS:
                    findings.append(f"{relative_path}: symbol {alias.name}")
        elif isinstance(node, ast.Name) and node.id in _CONTRACT_SYMBOLS:
            findings.append(f"{relative_path}: symbol {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _CONTRACT_SYMBOLS:
            findings.append(f"{relative_path}: symbol {node.attr}")

    for marker in _CONTRACT_TEXT_MARKERS:
        if marker in source_text:
            findings.append(f"{relative_path}: text {marker}")
    return sorted(set(findings))


def test_b2_reference_detector_recognizes_import_symbol_and_literal_forms() -> None:
    cases = {
        "direct": f"import {_CONTRACT_MODULE}\n",
        "from": f"from {_CONTRACT_MODULE} import {validate_step2_decision_packet_v2.__name__}\n",
        "alias": f"import {_CONTRACT_MODULE} as contract\ncontract.{validate_step2_decision_packet_v2.__name__}({{}})\n",
        "symbol": f"handler = {Step2DecisionPacketValidationResult.__name__}\n",
        "schema-version": f"VERSION = {DECISION_PACKET_SCHEMA_VERSION!r}\n",
        "result-version": f"VERSION = {DECISION_PACKET_VALIDATION_RESULT_VERSION!r}\n",
        "schema-file": f"SCHEMA = {DECISION_PACKET_SCHEMA_FILENAME!r}\n",
        "constant-imports": (
            f"from {_CONTRACT_MODULE} import "
            "DECISION_PACKET_SCHEMA_FILENAME, "
            "DECISION_PACKET_SCHEMA_VERSION, "
            "DECISION_PACKET_VALIDATION_RESULT_VERSION\n"
        ),
    }
    for name, source in cases.items():
        assert _contract_reference_findings(f"synthetic/{name}.py", source), name


def test_b2_reference_detector_rejects_reverse_b1_to_b2_reference() -> None:
    source = (
        f"from {_CONTRACT_MODULE} import "
        f"{validate_step2_decision_packet_v2.__name__}\n"
    )
    findings = _contract_reference_findings(
        _B1_RELATIVE_PATH.as_posix(),
        source,
    )
    assert findings == [
        f"{_B1_RELATIVE_PATH.as_posix()}: "
        f"from-import {_CONTRACT_MODULE}",
        f"{_B1_RELATIVE_PATH.as_posix()}: "
        f"symbol {validate_step2_decision_packet_v2.__name__}",
        f"{_B1_RELATIVE_PATH.as_posix()}: text {_CONTRACT_MODULE}",
        f"{_B1_RELATIVE_PATH.as_posix()}: text "
        f"{DECISION_PACKET_SCHEMA_VERSION}",
        f"{_B1_RELATIVE_PATH.as_posix()}: text "
        f"{validate_step2_decision_packet_v2.__name__}",
    ]


def test_no_production_consumer_references_decision_packet_v2_contract() -> None:
    repo_root = _repo_root()
    production_root = repo_root / "src" / "investment_orchestrator"
    excluded_paths = {
        repo_root / _CONTRACT_RELATIVE_PATH,
    }
    findings: list[str] = []
    for path in sorted(production_root.rglob("*.py")):
        if path in excluded_paths:
            continue
        relative_path = path.relative_to(repo_root).as_posix()
        findings.extend(
            _contract_reference_findings(
                relative_path,
                path.read_text(encoding="utf-8"),
            )
        )
    assert sorted(set(findings)) == [], "\n".join(sorted(set(findings)))


def test_validator_module_has_no_writer_path_clock_environment_or_authority_capability() -> None:
    source = (
        _repo_root() / _CONTRACT_RELATIVE_PATH
    ).read_text(encoding="utf-8")
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
            "settings",
            "portfolio",
            "permissions",
        }
    )
    forbidden_text = (
        "artifacts/current",
        "Path(",
        "open(",
        ".write_text(",
        ".write_bytes(",
        ".rename(",
        ".replace(",
        "subprocess.",
        "allowed_actions",
        "publication_eligible",
        "order_compilation_allowed",
    )
    assert all(marker not in source for marker in forbidden_text)
