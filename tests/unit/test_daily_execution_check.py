from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from investment_orchestrator.common.io import read_json, write_json, write_text
from investment_orchestrator.common.schema_validation import ArtifactSchemaError, validate_artifact_schema
from investment_orchestrator.parsers.extract_daily_execution_check import (
    DailyExecutionCheckExtractionError,
    extract_daily_execution_check,
    parse_daily_execution_actions_text,
)
from investment_orchestrator.validators.validate_daily_execution_actions import (
    ACTION_REASON_CODE_VALUES,
    REASON_CODE_VALUES,
    validate_daily_execution_actions,
)
from investment_orchestrator.workflow.daily_execution_check import (
    build_daily_execution_precomputed_diagnostics,
    render_daily_execution_check_prompt,
)


PORTFOLIO_SNAPSHOT_BUY_OPEN_ORDERS_PATCH_HEADER = (
    "TICKER | budget | compiled_open_order_notional(optional) | residual_cash_not_allocated(optional) | "
    "template_id | anchor_baseline_last_close | anchor_price_asof | last_refresh_date_et(optional) | "
    "highest_live_limit(optional) | lowest_live_limit(optional) | live_step_count(optional) | "
    "live_order_steps_summary(optional) | live_order_qtys_summary(optional)"
)


def audited_packet(*, ticker: str = "QQQ") -> dict:
    return {
        "audit_passed": True,
        "order_compiler_ready": True,
        "final_buy_side_delta_table": [],
        "final_sell_side_delta_table": [],
        "final_execution_plans": [
            {
                "ticker": ticker,
                "compile_ready": True,
            }
        ],
        "final_sell_execution_plans": [],
    }


def valid_action_payload(
    *,
    action: str = "KEEP",
    ticker: str = "QQQ",
    replace_packet=None,
    reason_code: str = "execution_drift_within_tolerance",
) -> dict:
    return {
        "as_of": "2026-04-22",
        "workflow": "daily_execution_check",
        "scope": "execution_only",
        "weekly_sources": {
            "audited_decision_packet_path": "artifacts/current/step3_audit_engine/audited_decision_packet.json",
            "template4_orders_path": "artifacts/current/step4_order_compiler/template4_orders.txt",
            "order_state_export_path": "artifacts/current/step4_order_compiler/order_state_export.txt",
        },
        "actions": [
            {
                "ticker": ticker,
                "side": "BUY",
                "action": action,
                "reason_code": reason_code,
                "execution_only": True,
                "weekly_intent_preserved": action not in {"DATA_GAP", "HOLD_FOR_WEEKLY_REVIEW"},
                "no_new_thesis": True,
                "no_ranking_change": True,
                "no_budget_increase": True,
                "source_evidence": (
                    "weekly compile-ready QQQ row and current live state; "
                    "distance_to_lowest_live_limit_pct is required evidence only and is not an action threshold "
                    "under the supplied policy"
                ),
                "replace_packet": replace_packet,
            }
        ],
        "blocked_items": [],
        "portfolio_snapshot_existing_buy_open_orders_patch": [],
        "operator_notes": [],
    }


def valid_replace_packet(*, ticker: str = "QQQ", budget_delta=0) -> dict:
    return {
        "ticker": ticker,
        "same_ticker": True,
        "remaining_budget_before": 1000,
        "remaining_budget_after": 1000,
        "budget_delta": budget_delta,
        "preserve_weekly_role": True,
        "preserve_event_binding": True,
        "replacement_reason": "execution drift only",
        "proposed_anchor_source": "daily_market_data_snapshot.last_close",
        "notes_for_replace_packet_synthesis": "same ticker, same budget, same weekly binding",
    }


def valid_portfolio_snapshot_buy_open_orders_patch_item(
    *,
    ticker: str = "QQQ",
    patch_type: str = "REPLACE_EXISTING_BUY_ORDER",
) -> dict:
    return {
        "ticker": ticker,
        "side": "BUY",
        "patch_type": patch_type,
        "paste_target_section": "PORTFOLIO_SNAPSHOT.existing_buy_open_orders_summary",
        "paste_instruction": (
            "Paste this row after the operator submits the order and confirms remaining quantities."
        ),
        "header": PORTFOLIO_SNAPSHOT_BUY_OPEN_ORDERS_PATCH_HEADER,
        "row_text": (
            f"{ticker} | 30320.92 | 29166.15 | 1154.77 | T4-E | 681.61 | 2026-05-05 |  | "
            "671.39 | 558.92 | 5 | starter@671.39;L1@650.94 | starter:15;L1:11"
        ),
        "operator_confirmation_required": True,
        "not_live_broker_truth_until_submitted": True,
        "source_evidence": "Generated from Daily Execution Check action after operator confirmation.",
    }


def wrap(payload: dict) -> str:
    return (
        "DAILY_EXECUTION_ACTIONS_START\n"
        + json.dumps(payload, indent=2)
        + "\nDAILY_EXECUTION_ACTIONS_END\n"
    )


def test_reason_code_schema_and_validator_stay_in_sync() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "daily_execution_actions.schema.json"
    schema = read_json(schema_path)
    schema_reason_codes = set(
        schema["$defs"]["action_item"]["properties"]["reason_code"]["enum"]
    )
    mapped_reason_codes = set().union(*ACTION_REASON_CODE_VALUES.values())

    assert schema_reason_codes == REASON_CODE_VALUES
    assert mapped_reason_codes == REASON_CODE_VALUES


def test_daily_actions_schema_accepts_empty_portfolio_snapshot_patch() -> None:
    validate_artifact_schema(valid_action_payload(), schema_name="daily_execution_actions.schema.json")


@pytest.mark.parametrize("patch_type", ["REPLACE_EXISTING_BUY_ORDER", "NEW_BUY_ORDER"])
def test_daily_actions_schema_accepts_portfolio_snapshot_patch_item(patch_type: str) -> None:
    payload = valid_action_payload()
    payload["portfolio_snapshot_existing_buy_open_orders_patch"] = [
        valid_portfolio_snapshot_buy_open_orders_patch_item(patch_type=patch_type)
    ]

    validate_artifact_schema(payload, schema_name="daily_execution_actions.schema.json")


def assert_daily_actions_schema_rejects(payload: dict, match: str) -> None:
    with pytest.raises(ArtifactSchemaError, match=match):
        validate_artifact_schema(payload, schema_name="daily_execution_actions.schema.json")


def test_daily_actions_schema_rejects_missing_portfolio_snapshot_patch() -> None:
    payload = valid_action_payload()
    del payload["portfolio_snapshot_existing_buy_open_orders_patch"]

    assert_daily_actions_schema_rejects(payload, "portfolio_snapshot_existing_buy_open_orders_patch")


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "expected_match"),
    [
        ("side", "SELL", "SELL"),
        ("patch_type", "MODIFY_BUY_ORDER", "MODIFY_BUY_ORDER"),
        ("paste_target_section", "PORTFOLIO_SNAPSHOT.other_section", "PORTFOLIO_SNAPSHOT.other_section"),
        ("header", "TICKER | budget", "TICKER"),
        ("operator_confirmation_required", False, "True was expected"),
        ("not_live_broker_truth_until_submitted", False, "True was expected"),
        ("source_evidence", "", "should be non-empty"),
    ],
)
def test_daily_actions_schema_rejects_invalid_portfolio_snapshot_patch_fields(
    field_name: str,
    invalid_value: object,
    expected_match: str,
) -> None:
    payload = valid_action_payload()
    patch_item = valid_portfolio_snapshot_buy_open_orders_patch_item()
    patch_item[field_name] = invalid_value
    payload["portfolio_snapshot_existing_buy_open_orders_patch"] = [patch_item]

    assert_daily_actions_schema_rejects(payload, expected_match)


def test_daily_actions_schema_rejects_unexpected_portfolio_snapshot_patch_property() -> None:
    payload = valid_action_payload()
    patch_item = valid_portfolio_snapshot_buy_open_orders_patch_item()
    patch_item["unexpected"] = "nope"
    payload["portfolio_snapshot_existing_buy_open_orders_patch"] = [patch_item]

    assert_daily_actions_schema_rejects(payload, "Additional properties are not allowed")


def write_weekly_sources(tmp_path: Path, *, ticker: str = "QQQ") -> tuple[Path, Path, Path]:
    audited_path = tmp_path / "audited_decision_packet.json"
    template4_path = tmp_path / "template4_orders.txt"
    state_path = tmp_path / "order_state_export.txt"
    write_json(audited_path, audited_packet(ticker=ticker))
    write_text(template4_path, f"TEMPLATE4_ORDERS\nBUY_ORDERS\nticker={ticker} | step_name=L1\n")
    write_text(
        state_path,
        "ORDER_STATE_EXPORT\n(2a) existing_buy_open_orders_summary\n"
        "TICKER | budget | template_id\n"
        f"{ticker} | 1000 | T4-B\n",
    )
    return audited_path, template4_path, state_path


def test_valid_daily_actions_parse_passes(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw_output.txt"
    output_path = tmp_path / "daily_execution_actions.json"
    text_path = tmp_path / "daily_execution_check.txt"
    audited_path, template4_path, state_path = write_weekly_sources(tmp_path)
    write_text(raw_path, wrap(valid_action_payload()))

    payload = extract_daily_execution_check(
        raw_output_path=raw_path,
        daily_execution_actions_path=output_path,
        daily_execution_check_text_path=text_path,
        audited_decision_packet_path=audited_path,
        template4_orders_path=template4_path,
        order_state_export_path=state_path,
    )

    assert payload["actions"][0]["ticker"] == "QQQ"
    assert read_json(output_path)["workflow"] == "daily_execution_check"
    assert text_path.read_text(encoding="utf-8").strip().startswith("{")


def test_missing_daily_execution_actions_block_fails() -> None:
    with pytest.raises(DailyExecutionCheckExtractionError, match="Missing required marker"):
        parse_daily_execution_actions_text("{}")


def test_malformed_json_fails() -> None:
    with pytest.raises(DailyExecutionCheckExtractionError, match="not valid JSON"):
        parse_daily_execution_actions_text(
            "DAILY_EXECUTION_ACTIONS_START\n{bad json\nDAILY_EXECUTION_ACTIONS_END"
        )


def test_unknown_action_fails() -> None:
    payload = valid_action_payload(action="DO_SOMETHING")
    with pytest.raises(ValueError):
        validate_daily_execution_actions(payload, audited_decision_packet=audited_packet())


def test_unknown_reason_code_fails() -> None:
    payload = valid_action_payload(reason_code="weekly_replace_already_reflected_and_execution_drift_within_tolerance")
    with pytest.raises(ValueError, match="reason_code"):
        validate_daily_execution_actions(payload, audited_decision_packet=audited_packet())


def test_reason_code_must_match_action() -> None:
    payload = valid_action_payload(action="CANCEL", reason_code="execution_drift_within_tolerance")
    with pytest.raises(ValueError, match="not valid for action"):
        validate_daily_execution_actions(payload, audited_decision_packet=audited_packet())


def test_replace_without_replace_packet_fails() -> None:
    payload = valid_action_payload(
        action="REPLACE",
        replace_packet=None,
        reason_code="execution_reanchor_required",
    )
    with pytest.raises(ValueError):
        validate_daily_execution_actions(payload, audited_decision_packet=audited_packet())


def test_non_replace_with_replace_packet_fails() -> None:
    payload = valid_action_payload(action="KEEP", replace_packet=valid_replace_packet())
    with pytest.raises(ValueError):
        validate_daily_execution_actions(payload, audited_decision_packet=audited_packet())


def test_replace_with_budget_delta_above_zero_fails() -> None:
    payload = valid_action_payload(
        action="REPLACE",
        replace_packet=valid_replace_packet(budget_delta=1),
        reason_code="execution_reanchor_required",
    )
    with pytest.raises(ValueError):
        validate_daily_execution_actions(payload, audited_decision_packet=audited_packet())


def test_replace_with_ticker_not_in_weekly_allowlist_fails() -> None:
    payload = valid_action_payload(
        action="REPLACE",
        ticker="SMH",
        replace_packet=valid_replace_packet(ticker="SMH"),
        reason_code="execution_reanchor_required",
    )
    with pytest.raises(ValueError, match="not in weekly-approved"):
        validate_daily_execution_actions(payload, audited_decision_packet=audited_packet(ticker="QQQ"))


def test_data_gap_allowed_without_replacement() -> None:
    payload = valid_action_payload(
        action="DATA_GAP",
        replace_packet=None,
        reason_code="live_order_state_data_gap",
    )

    parsed = validate_daily_execution_actions(payload, audited_decision_packet=audited_packet())

    assert parsed["actions"][0]["action"] == "DATA_GAP"
    assert parsed["actions"][0]["replace_packet"] is None


def near_edge_settings() -> dict:
    return {
        "daily_execution_drift_policy": {
            "near_edge_monitor_band": {"enabled": True},
            "lowest_live_limit_policy": {
                "distance_to_lowest_live_limit_pct": {"action_threshold_role": "none"}
            },
        }
    }


def near_edge_diagnostics() -> dict:
    return {
        "diagnostics": [
            {
                "ticker": "QQQ",
                "near_anchor_edge": True,
                "near_highest_live_limit_edge": True,
            }
        ]
    }


def test_keep_near_edge_remains_keep_with_canonical_reason_code() -> None:
    payload = valid_action_payload()
    payload["operator_notes"] = [
        {"ticker": "QQQ", "note_type": "near_anchor_drift_edge", "message": "near edge"},
        {"ticker": "QQQ", "note_type": "near_highest_live_limit_edge", "message": "near edge"},
    ]

    parsed = validate_daily_execution_actions(
        payload,
        audited_decision_packet=audited_packet(),
        strategy_settings=near_edge_settings(),
        precomputed_diagnostics=near_edge_diagnostics(),
    )

    assert parsed["actions"][0]["action"] == "KEEP"
    assert parsed["actions"][0]["reason_code"] == "execution_drift_within_tolerance"


def test_near_edge_requires_operator_note() -> None:
    payload = valid_action_payload()

    with pytest.raises(ValueError, match="operator_notes"):
        validate_daily_execution_actions(
            payload,
            audited_decision_packet=audited_packet(),
            strategy_settings=near_edge_settings(),
            precomputed_diagnostics=near_edge_diagnostics(),
        )


def test_lowest_limit_evidence_only_statement_required_when_policy_none() -> None:
    payload = valid_action_payload()
    payload["actions"][0]["source_evidence"] = "distance_to_lowest_live_limit_pct=-12.0"

    with pytest.raises(ValueError, match="evidence-only"):
        validate_daily_execution_actions(
            payload,
            audited_decision_packet=audited_packet(),
            strategy_settings=near_edge_settings(),
        )


def test_anchor_drift_tolerance_must_not_be_used_for_lowest_limit() -> None:
    payload = valid_action_payload()
    payload["actions"][0]["source_evidence"] = (
        "anchor_drift_tolerance threshold compared to distance_to_lowest_live_limit_pct; "
        "distance_to_lowest_live_limit_pct is required evidence only and is not an action threshold "
        "under the supplied policy"
    )

    with pytest.raises(ValueError, match="anchor_drift_tolerance"):
        validate_daily_execution_actions(
            payload,
            audited_decision_packet=audited_packet(),
            strategy_settings=near_edge_settings(),
        )


def test_highest_live_limit_cap_must_not_be_used_for_lowest_limit() -> None:
    payload = valid_action_payload()
    payload["actions"][0]["source_evidence"] = (
        "max_negative_distance_to_highest_live_limit_pct threshold compared to "
        "distance_to_lowest_live_limit_pct; distance_to_lowest_live_limit_pct is required evidence only "
        "and is not an action threshold under the supplied policy"
    )

    with pytest.raises(ValueError, match="max_negative_distance_to_highest_live_limit"):
        validate_daily_execution_actions(
            payload,
            audited_decision_packet=audited_packet(),
            strategy_settings=near_edge_settings(),
        )


def test_old_settings_without_near_edge_keeps_feature_disabled() -> None:
    payload = valid_action_payload()

    parsed = validate_daily_execution_actions(
        payload,
        audited_decision_packet=audited_packet(),
        strategy_settings={"hard_cap_open_orders_budget": 1000},
        precomputed_diagnostics=near_edge_diagnostics(),
    )

    assert parsed["actions"][0]["action"] == "KEEP"


def test_precomputed_diagnostics_do_not_infer_role_from_ambiguous_template_id() -> None:
    portfolio_snapshot = dedent(
        """
        (2a) existing_buy_open_orders_summary
        TICKER | budget | compiled_open_order_notional(optional) | residual_cash_not_allocated(optional) | template_id | anchor_baseline_last_close | anchor_price_asof | last_refresh_date_et(optional) | highest_live_limit(optional) | lowest_live_limit(optional) | live_step_count(optional) | live_order_steps_summary(optional) | live_order_qtys_summary(optional)
        CIBR | 1000 | 900 | 100 | T4-B | 75.32 | 2026-05-08 | 2026-05-11 | 70.80 | 57.24 | 4 | L1@70.80;L2@67.03;L3@62.52;L4@57.24 | L1:8;L2:11;L3:19;L4:24
        (2b) sell_open_orders
        """
    )
    settings = {
        "buy_order_template_map": {
            "diversified_core_buffer": {"template_id": "T4-B"},
            "extended_etf_minority_sleeve": {"template_id": "T4-B"},
        },
        "daily_execution_drift_policy": {
            "ticker_role_fallback": {},
            "keep_tolerance": {
                "anchor_drift_abs_pct_static_cap": {"diversified_core_buffer": 4.5},
                "anchor_drift_atr_multiple_cap": {"diversified_core_buffer": 2.5},
                "max_negative_distance_to_highest_live_limit_pct": {"diversified_core_buffer": 6.0},
            },
            "near_edge_monitor_band": {
                "anchor_drift_pct_band": {"diversified_core_buffer": 1.0},
                "highest_live_limit_distance_pct_band": {"diversified_core_buffer": 1.0},
            },
        },
    }

    diagnostics = build_daily_execution_precomputed_diagnostics(
        portfolio_snapshot=portfolio_snapshot,
        daily_market_data_snapshot={
            "tickers": [
                {
                    "ticker": "CIBR",
                    "last_close": 75.43,
                    "price_asof": "2026-05-11",
                    "atr_20_30d_pct": 2.4,
                }
            ]
        },
        audited_packet=audited_packet(ticker="CIBR"),
        settings=settings,
    )

    row = diagnostics["diagnostics"][0]
    assert row["ticker"] == "CIBR"
    assert row["role_used"] is None
    assert "role_used" in row["data_gap_fields"]


def test_workflow_render_writes_to_daily_artifacts_not_current(tmp_path: Path) -> None:
    weekly_dir = tmp_path / "artifacts" / "current"
    step3_dir = weekly_dir / "step3_audit_engine"
    step4_dir = weekly_dir / "step4_order_compiler"
    inputs_dir = tmp_path / "inputs" / "current"
    step3_dir.mkdir(parents=True)
    step4_dir.mkdir(parents=True)
    inputs_dir.mkdir(parents=True)
    write_json(step3_dir / "audited_decision_packet.json", audited_packet())
    write_text(step4_dir / "template4_orders.txt", "TEMPLATE4_ORDERS\nBUY_ORDERS\nticker=QQQ | step_name=L1\n")
    write_text(
        step4_dir / "order_state_export.txt",
        "ORDER_STATE_EXPORT\n(2a) existing_buy_open_orders_summary\nQQQ | 1000 | T4-B\n",
    )
    write_text(inputs_dir / "portfolio_snapshot.txt", "portfolio snapshot\n")
    write_text(inputs_dir / "strategy_settings.yaml", "hard_cap_open_orders_budget: 1000\n")

    result = render_daily_execution_check_prompt(as_of_date="2026-04-22", root=tmp_path)

    prompt_path = Path(result["prompt_path"])
    raw_path = Path(result["raw_output_path"])
    assert prompt_path == tmp_path / "artifacts" / "daily" / "2026-04-22" / "daily_execution_check" / "prompt.txt"
    assert raw_path.exists()
    assert not (tmp_path / "artifacts" / "current" / "daily_execution_check").exists()
    assert "DATA_UNAVAILABLE: no daily market data snapshot found" in prompt_path.read_text(encoding="utf-8")
