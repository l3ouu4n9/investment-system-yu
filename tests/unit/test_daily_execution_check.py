from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from investment_orchestrator.common.io import read_json, write_json, write_text
from investment_orchestrator.parsers.extract_daily_execution_check import (
    DailyExecutionCheckExtractionError,
    extract_daily_execution_check,
    parse_daily_execution_actions_text,
)
from investment_orchestrator.validators.validate_daily_execution_actions import (
    validate_daily_execution_actions,
)
from investment_orchestrator.workflow.daily_execution_check import render_daily_execution_check_prompt


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


def valid_action_payload(*, action: str = "KEEP", ticker: str = "QQQ", replace_packet=None) -> dict:
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
                "reason_code": "execution_drift_within_tolerance",
                "execution_only": True,
                "weekly_intent_preserved": action not in {"DATA_GAP", "HOLD_FOR_WEEKLY_REVIEW"},
                "no_new_thesis": True,
                "no_ranking_change": True,
                "no_budget_increase": True,
                "source_evidence": "weekly compile-ready QQQ row and current live state",
                "replace_packet": replace_packet,
            }
        ],
        "blocked_items": [],
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


def wrap(payload: dict) -> str:
    return (
        "DAILY_EXECUTION_ACTIONS_START\n"
        + json.dumps(payload, indent=2)
        + "\nDAILY_EXECUTION_ACTIONS_END\n"
    )


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


def test_replace_without_replace_packet_fails() -> None:
    payload = valid_action_payload(action="REPLACE", replace_packet=None)
    with pytest.raises(ValueError):
        validate_daily_execution_actions(payload, audited_decision_packet=audited_packet())


def test_non_replace_with_replace_packet_fails() -> None:
    payload = valid_action_payload(action="KEEP", replace_packet=valid_replace_packet())
    with pytest.raises(ValueError):
        validate_daily_execution_actions(payload, audited_decision_packet=audited_packet())


def test_replace_with_budget_delta_above_zero_fails() -> None:
    payload = valid_action_payload(action="REPLACE", replace_packet=valid_replace_packet(budget_delta=1))
    with pytest.raises(ValueError):
        validate_daily_execution_actions(payload, audited_decision_packet=audited_packet())


def test_replace_with_ticker_not_in_weekly_allowlist_fails() -> None:
    payload = valid_action_payload(
        action="REPLACE",
        ticker="SMH",
        replace_packet=valid_replace_packet(ticker="SMH"),
    )
    with pytest.raises(ValueError, match="not in weekly-approved"):
        validate_daily_execution_actions(payload, audited_decision_packet=audited_packet(ticker="QQQ"))


def test_data_gap_allowed_without_replacement() -> None:
    payload = valid_action_payload(action="DATA_GAP", replace_packet=None)

    parsed = validate_daily_execution_actions(payload, audited_decision_packet=audited_packet())

    assert parsed["actions"][0]["action"] == "DATA_GAP"
    assert parsed["actions"][0]["replace_packet"] is None


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
