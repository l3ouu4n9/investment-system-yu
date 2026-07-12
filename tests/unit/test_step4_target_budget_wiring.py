"""G5: Step 4 primary parse path wires target_new_buy_budget_this_run.

Verifies the deterministic operator-provided per-run new-buy budget is read from
``strategy_settings.yaml`` and forwarded into the post-order validator via the
primary ``parse_step4_output`` path (with ``require_safety_context=True``). No
prompt / compiler / investment-semantics change is exercised here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import investment_orchestrator.workflow.step4_order_compiler as step4
import pytest
from investment_orchestrator.common.io import write_text
from investment_orchestrator.validators.strategy_settings import parse_strategy_settings_text


def test_real_strategy_settings_has_non_negative_target_budget() -> None:
    text = Path("inputs/current/strategy_settings.yaml").read_text(encoding="utf-8")
    settings = parse_strategy_settings_text(text)  # permissive: unknown keys accepted
    assert "target_new_buy_budget_this_run" in settings
    value = settings["target_new_buy_budget_this_run"]
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    assert value >= 0


def test_parse_step4_output_forwards_target_budget_from_settings(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    def fake_extract(**kwargs: Any) -> tuple[str, str, str]:
        captured.update(kwargs)
        return ("t4", "ose", "exec")

    # Stub the heavy upstream guards / loaders so only the wiring is exercised.
    monkeypatch.setattr(step4, "enforce_step4_upstream_guard", lambda: None)
    monkeypatch.setattr(step4, "enforce_step4_final_execution_safety_gate", lambda: None)
    monkeypatch.setattr(
        step4,
        "load_audited_decision_packet",
        lambda: {"audit_passed": True, "order_compiler_ready": True},
    )
    monkeypatch.setattr(
        step4,
        "load_strategy_settings",
        lambda: {
            "target_new_buy_budget_this_run": 9999.0,
            "hard_cap_open_orders_budget": 38211.29,
        },
    )
    monkeypatch.setattr(step4, "load_portfolio_snapshot_text", lambda: "snapshot")
    monkeypatch.setattr(step4, "parse_existing_buy_open_orders_summary", lambda text: None)
    monkeypatch.setattr(step4, "load_effective_allowed_buy_universe", lambda: None)
    monkeypatch.setattr(step4, "step4_raw_output_path", lambda: tmp_path / "raw.txt")
    monkeypatch.setattr(step4, "step4_template4_orders_path", lambda: tmp_path / "t4.txt")
    monkeypatch.setattr(step4, "step4_order_state_export_path", lambda: tmp_path / "ose.txt")
    monkeypatch.setattr(step4, "step4_exec_summary_path", lambda: tmp_path / "exec.txt")
    monkeypatch.setattr(step4, "extract_orders_and_summary", fake_extract)

    step4.parse_step4_output()

    assert captured["target_new_buy_budget_this_run"] == 9999.0
    assert captured["hard_cap_open_orders_budget"] == 38211.29
    assert captured["require_safety_context"] is True


def test_parse_step4_output_forwards_none_when_target_budget_absent(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    # If the operator omits the key, the value forwarded is None; the validator's
    # require_safety_context then fails closed only when net-new rows exist.
    captured: dict[str, Any] = {}

    monkeypatch.setattr(step4, "enforce_step4_upstream_guard", lambda: None)
    monkeypatch.setattr(step4, "enforce_step4_final_execution_safety_gate", lambda: None)
    monkeypatch.setattr(
        step4,
        "load_audited_decision_packet",
        lambda: {"audit_passed": True, "order_compiler_ready": True},
    )
    monkeypatch.setattr(step4, "load_strategy_settings", lambda: {"hard_cap_open_orders_budget": 1.0})
    monkeypatch.setattr(step4, "load_portfolio_snapshot_text", lambda: "snapshot")
    monkeypatch.setattr(step4, "parse_existing_buy_open_orders_summary", lambda text: None)
    monkeypatch.setattr(step4, "load_effective_allowed_buy_universe", lambda: None)
    monkeypatch.setattr(step4, "step4_raw_output_path", lambda: tmp_path / "raw.txt")
    monkeypatch.setattr(step4, "step4_template4_orders_path", lambda: tmp_path / "t4.txt")
    monkeypatch.setattr(step4, "step4_order_state_export_path", lambda: tmp_path / "ose.txt")
    monkeypatch.setattr(step4, "step4_exec_summary_path", lambda: tmp_path / "exec.txt")

    def fake_extract(**kwargs: Any) -> tuple[str, str, str]:
        captured.update(kwargs)
        return ("t4", "ose", "exec")

    monkeypatch.setattr(step4, "extract_orders_and_summary", fake_extract)

    step4.parse_step4_output()

    assert captured["target_new_buy_budget_this_run"] is None
    assert captured["require_safety_context"] is True


def test_primary_parse_rejects_blank_intent_after_upstream_guards_pass(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    raw = tmp_path / "raw.txt"
    template4 = tmp_path / "canonical" / "template4_orders.txt"
    state = tmp_path / "canonical" / "order_state_export.txt"
    summary = tmp_path / "canonical" / "exec_summary.txt"
    write_text(
        raw,
        "TEMPLATE4_ORDERS_START\n"
        "TEMPLATE4_ORDERS\nSELL_ORDERS\nNONE\nBUY_ORDERS\n"
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=\n"
        "TEMPLATE4_ORDERS_END\n"
        "ORDER_STATE_EXPORT_START\nORDER_STATE_EXPORT\nNONE\nORDER_STATE_EXPORT_END\n"
        "TEMPLATE5_EXEC_SUMMARY_START\nTEMPLATE5_EXEC_SUMMARY\nno diagnostics\n"
        "TEMPLATE5_EXEC_SUMMARY_END\n",
    )

    monkeypatch.setattr(step4, "enforce_step4_upstream_guard", lambda: None)
    monkeypatch.setattr(step4, "enforce_step4_final_execution_safety_gate", lambda: None)
    monkeypatch.setattr(
        step4,
        "load_audited_decision_packet",
        lambda: {
            "audit_passed": True,
            "order_compiler_ready": True,
            "final_execution_plans": [
                {
                    "ticker": "QQQ",
                    "final_action": "NEW_ORDER",
                    "compile_ready": True,
                    "compiled_open_order_notional": 10.0,
                    "target_open_order_budget": 10.0,
                }
            ],
        },
    )
    monkeypatch.setattr(
        step4,
        "load_strategy_settings",
        lambda: {
            "core_universe": ["QQQ"],
            "satellite_universe": [],
            "user_approved_extended_etf_static_list": [],
            "hard_cap_open_orders_budget": 1000.0,
            "target_new_buy_budget_this_run": 1000.0,
            "max_new_tickers_per_week": {
                "base_universe_new_tickers_per_week": 1,
                "extended_etf_sleeve_new_tickers_per_week": 0,
            },
        },
    )
    monkeypatch.setattr(step4, "load_portfolio_snapshot_text", lambda: "no existing orders")
    monkeypatch.setattr(step4, "load_effective_allowed_buy_universe", lambda: ["QQQ"])
    monkeypatch.setattr(step4, "step4_raw_output_path", lambda: raw)
    monkeypatch.setattr(step4, "step4_template4_orders_path", lambda: template4)
    monkeypatch.setattr(step4, "step4_order_state_export_path", lambda: state)
    monkeypatch.setattr(step4, "step4_exec_summary_path", lambda: summary)

    with pytest.raises(ValueError, match="required nonempty order_intent"):
        step4.parse_step4_output()

    assert not template4.exists()
    assert not state.exists()
    assert not summary.exists()
    assert (template4.parent / "quarantine" / template4.name).is_file()
