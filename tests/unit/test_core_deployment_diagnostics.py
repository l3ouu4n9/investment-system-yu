from __future__ import annotations

from pathlib import Path

import pytest

from investment_orchestrator.common.io import write_text
from investment_orchestrator.validators.validate_audited_decision_packet import (
    validate_audited_decision_packet,
)
from investment_orchestrator.validators.validate_decision_packet import validate_decision_packet
from investment_orchestrator.validators.validate_orders_output import validate_orders_output
from investment_orchestrator.validators.strategy_settings import ETF_ROLE_NAMES, parse_strategy_settings_text


def market_snapshot() -> dict:
    return {
        "schema_version": "1.0",
        "snapshot_type": "MARKET_DATA_SNAPSHOT",
        "run_timestamp_et": "2026-04-22 20:00 ET",
        "execution_date_et": "2026-04-22",
        "market_data_target_close_date_et": "2026-04-22",
        "close_time_zone": "America/New_York",
        "display_time_zone": "America/Los_Angeles",
        "primary_source": "Barchart",
        "fallback_source_for_last_close_and_price_asof_only": "Stooq",
        "holiday_aware_close_resolution": True,
        "tickers": [
            {
                "ticker": "QQQ",
                "last_close": 420.0,
                "price_asof": "2026-04-22",
                "atr_20_30d_pct": 2.0,
                "ma50": 410.0,
                "ma200": 390.0,
                "avg_volume_3m": 50000000,
                "last_close_source": "Barchart",
                "price_asof_source": "Barchart",
                "technicals_source": "Barchart",
                "retrieved_at_utc": None,
                "same_day_close_required": False,
                "freshness_ok": True,
                "data_gap": False,
                "data_gap_reason": None,
                "notes": [],
            }
        ],
    }


def diagnostic(*, why: str = "QQQ underdeployment reviewed; hold kept due explicit cap timing.") -> dict:
    return {
        "ticker": "QQQ",
        "role_layer": "benchmark_carrier_core",
        "current_holding_shares": 10,
        "existing_open_order_budget": 1000,
        "compiled_open_order_notional": 800,
        "residual_cash_not_allocated": 200,
        "highest_live_limit": 407.0,
        "current_last_close": 420.0,
        "distance_to_highest_live_limit_pct": -3.1,
        "anchor_drift_pct": 3.9,
        "near_edge_flags": {"near_highest_live_limit_edge": True},
        "deployment_status": "underdeployment_review_required",
        "weekly_allowed_responses": [
            "keep_existing",
            "same_budget_reanchor",
            "new_budget_if_hard_cap_and_strategy_allow",
            "hold_no_new_budget_with_explicit_reason",
        ],
        "why": why,
    }


def decision_packet(*, diag: dict | None = None) -> dict:
    payload = {
        "effective_allowed_buy_universe": ["QQQ"],
        "MARKET_DATA_SNAPSHOT": market_snapshot(),
        "active_shortlist": [],
        "buy_side_delta_table": [],
        "rotation_decision_layer_8_15": [],
        "sell_side_delta_table_8_2": [],
        "execution_plan_drafts_8_5": [
            {"ticker": "QQQ", "action_draft": "KEEP_EXISTING", "why": "explicit weekly keep reason"}
        ],
        "sell_execution_plan_drafts_8_6": [],
        "assumptions_and_data_gaps": [],
        "decision_builder_ready_for_audit": True,
    }
    if diag is not None:
        payload["core_deployment_diagnostics"] = [diag]
    return payload


def audited_packet(*, diag: dict | None = None, final_action: str = "KEEP_EXISTING", ticker: str = "QQQ") -> dict:
    payload = {
        "audit_passed": True,
        "order_compiler_ready": True,
        "final_buy_side_delta_table": [],
        "final_sell_side_delta_table": [],
        "final_execution_plans": [
            {"ticker": ticker, "compile_ready": True, "final_action": final_action, "why": "explicit audit keep reason"}
        ],
        "final_sell_execution_plans": [],
    }
    if diag is not None:
        payload["core_deployment_diagnostics"] = [diag]
    return payload


def test_strategy_a_accepts_core_deployment_diagnostics() -> None:
    parsed = validate_decision_packet(decision_packet(diag=diagnostic()))

    assert parsed["core_deployment_diagnostics"][0]["ticker"] == "QQQ"
    assert parsed["execution_plan_drafts_8_5"][0]["action_draft"] == "KEEP_EXISTING"


def test_strategy_a_underdeployment_keep_requires_explicit_why() -> None:
    payload = decision_packet(diag=diagnostic(why=""))
    payload["execution_plan_drafts_8_5"][0]["why"] = ""

    with pytest.raises(ValueError, match="explicit why"):
        validate_decision_packet(payload)


def test_strategy_b_accepts_and_passes_through_core_deployment_diagnostics() -> None:
    parsed = validate_audited_decision_packet(audited_packet(diag=diagnostic()))

    assert parsed["core_deployment_diagnostics"][0]["deployment_status"] == "underdeployment_review_required"
    assert parsed["final_execution_plans"][0]["ticker"] == "QQQ"


def test_strategy_b_underdeployment_keep_without_why_fails() -> None:
    payload = audited_packet(diag=diagnostic(why=""))
    payload["final_execution_plans"][0]["why"] = ""

    with pytest.raises(ValueError, match="explicit why"):
        validate_audited_decision_packet(payload)


def write_step4_files(tmp_path: Path, *, orders: str, summary: str) -> tuple[Path, Path, Path]:
    template4_path = tmp_path / "template4_orders.txt"
    state_path = tmp_path / "order_state_export.txt"
    summary_path = tmp_path / "exec_summary.txt"
    write_text(template4_path, orders)
    write_text(state_path, "ORDER_STATE_EXPORT\n(2a) existing_buy_open_orders_summary\nNONE\n")
    write_text(summary_path, summary)
    return template4_path, state_path, summary_path


def diagnostic_summary_text() -> str:
    return (
        "TEMPLATE5_EXEC_SUMMARY\n"
        "QQQ benchmark_carrier_core underdeployment_review_required highest_live_limit 407.0 "
        "current_last_close 420.0 distance_to_highest_live_limit_pct -3.1 why "
        "QQQ underdeployment reviewed; hold kept due explicit cap timing.\n"
        "WEEKLY_REVIEW_NEEDED: core ETF deployment adequacy requires weekly decision; "
        "Strategy C did not change orders.\n"
    )


def test_strategy_c_summary_accepts_diagnostics_without_buy_orders(tmp_path: Path) -> None:
    template4_path, state_path, summary_path = write_step4_files(
        tmp_path,
        orders="TEMPLATE4_ORDERS\nBUY_ORDERS\nNONE\n",
        summary=diagnostic_summary_text(),
    )

    parsed = validate_orders_output(
        template4_orders_path=template4_path,
        order_state_export_path=state_path,
        exec_summary_path=summary_path,
        audited_decision_packet=audited_packet(diag=diagnostic()),
    )

    assert "WEEKLY_REVIEW_NEEDED" in parsed["exec_summary"]


def test_strategy_c_diagnostics_do_not_generate_buy_orders(tmp_path: Path) -> None:
    template4_path, state_path, summary_path = write_step4_files(
        tmp_path,
        orders="TEMPLATE4_ORDERS\nBUY_ORDERS\nticker=QQQ | step_name=L1\n",
        summary=diagnostic_summary_text(),
    )

    with pytest.raises(ValueError, match="not compile-ready buy-side"):
        validate_orders_output(
            template4_orders_path=template4_path,
            order_state_export_path=state_path,
            exec_summary_path=summary_path,
            audited_decision_packet=audited_packet(diag=diagnostic(), final_action="KEEP_EXISTING"),
        )


def test_strategy_c_buy_orders_only_from_executable_final_plans(tmp_path: Path) -> None:
    template4_path, state_path, summary_path = write_step4_files(
        tmp_path,
        orders="TEMPLATE4_ORDERS\nBUY_ORDERS\nticker=QQQ | step_name=L1\n",
        summary=diagnostic_summary_text(),
    )

    parsed = validate_orders_output(
        template4_orders_path=template4_path,
        order_state_export_path=state_path,
        exec_summary_path=summary_path,
        audited_decision_packet=audited_packet(diag=diagnostic(), final_action="NEW_ORDER"),
    )

    assert "ticker=QQQ" in parsed["template4_orders"]


def test_strategy_c_cancel_existing_buy_order_rows_are_valid_buy_side_actions(tmp_path: Path) -> None:
    template4_path, state_path, summary_path = write_step4_files(
        tmp_path,
        orders=(
            "TEMPLATE4_ORDERS\n"
            "BUY_ORDERS\n"
            "ticker=GRID | step_name=L1 | order_intent=CANCEL_EXISTING\n"
        ),
        summary=diagnostic_summary_text(),
    )

    parsed = validate_orders_output(
        template4_orders_path=template4_path,
        order_state_export_path=state_path,
        exec_summary_path=summary_path,
        audited_decision_packet=audited_packet(diag=diagnostic(), final_action="CANCEL_EXISTING", ticker="GRID"),
    )

    assert "order_intent=CANCEL_EXISTING" in parsed["template4_orders"]


def test_strategy_c_buy_order_row_intent_must_match_final_action(tmp_path: Path) -> None:
    template4_path, state_path, summary_path = write_step4_files(
        tmp_path,
        orders=(
            "TEMPLATE4_ORDERS\n"
            "BUY_ORDERS\n"
            "ticker=GRID | step_name=L1 | order_intent=REPLACE_EXISTING_SUBMIT_LEG\n"
        ),
        summary=diagnostic_summary_text(),
    )

    with pytest.raises(ValueError, match="order_intent values"):
        validate_orders_output(
            template4_orders_path=template4_path,
            order_state_export_path=state_path,
            exec_summary_path=summary_path,
            audited_decision_packet=audited_packet(diag=diagnostic(), final_action="CANCEL_EXISTING", ticker="GRID"),
        )


def test_current_strategy_settings_new_policy_surface_and_keep_roles() -> None:
    settings_text = Path("inputs/current/strategy_settings.yaml").read_text(encoding="utf-8")
    settings = parse_strategy_settings_text(settings_text)

    assert "etf_layer_execution_policy" in settings
    drift_policy = settings["daily_execution_drift_policy"]
    assert "near_edge_monitor_band" in drift_policy
    assert "lowest_live_limit_policy" in drift_policy
    assert "repeated_near_edge_policy" in drift_policy
    for tolerance_key in (
        "anchor_drift_abs_pct_static_cap",
        "anchor_drift_atr_multiple_cap",
        "max_negative_distance_to_highest_live_limit_pct",
    ):
        assert set(ETF_ROLE_NAMES).issubset(drift_policy["keep_tolerance"][tolerance_key])
