from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.common import paths as path_helpers
from investment_orchestrator.common.io import write_json, write_text
from investment_orchestrator.state.final_execution_safety_gate import (
    FinalExecutionSafetyGateError,
    evaluate_final_execution_safety,
    final_execution_safety_blocked_artifact_payload,
)
from investment_orchestrator.workflow import (
    step1_research,
    step2_decision_builder,
    step3_audit_engine,
    step4_order_compiler,
)


# --- fixtures (deterministic dict builders) ----------------------------------


def step1_permission(
    *,
    state: str = "STRICT_FRESH",
    manual_review_required: bool = False,
    allowed_actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "research_availability": state.lower(),
        "allowed_actions": allowed_actions
        if allowed_actions is not None
        else [
            "HOLD",
            "NO_TRADE",
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "manual_review_required": manual_review_required,
        "report_only": True,
    }


def step2_decision_packet(**overrides: Any) -> dict[str, Any]:
    packet = {
        "effective_allowed_buy_universe": ["QQQ"],
        "MARKET_DATA_SNAPSHOT": {"snapshot_type": "MARKET_DATA_SNAPSHOT"},
        "active_shortlist": [],
        "buy_side_delta_table": [],
        "rotation_decision_layer_8_15": [],
        "sell_side_delta_table_8_2": [],
        "execution_plan_drafts_8_5": [],
        "sell_execution_plan_drafts_8_6": [],
        "assumptions_and_data_gaps": [],
        "decision_builder_ready_for_audit": True,
    }
    packet.update(overrides)
    return packet


def step3_audited_packet(**overrides: Any) -> dict[str, Any]:
    packet = {
        "audit_passed": True,
        "order_compiler_ready": True,
        "final_buy_side_delta_table": [],
        "final_sell_side_delta_table": [],
        "final_execution_plans": [],
        "final_sell_execution_plans": [],
    }
    packet.update(overrides)
    return packet


def allow() -> dict[str, Any]:
    return dict(
        step1_permission=step1_permission(),
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=step3_audited_packet(),
    )


# --- gate unit tests: the allowed case --------------------------------------


def test_valid_strict_fresh_structured_no_blockers_allows() -> None:
    result = evaluate_final_execution_safety(**allow())

    assert result.ready_for_order_compilation is True
    assert result.blocked is False
    assert result.reason is None
    assert result.fail_reasons == []
    assert result.recommended_result is None
    assert result.is_deterministic is True
    assert all(result.checked_conditions.values())


# --- gate unit tests: block cases -------------------------------------------


def test_missing_step1_permission_blocks() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=None,
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=step3_audited_packet(),
    )
    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["step1_permission_present"] is False


def test_step1_not_strict_fresh_blocks() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=step1_permission(state="STRICT_STALE"),
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=step3_audited_packet(),
    )
    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["step1_state_strict_fresh"] is False


def test_pending_gates_state_still_blocks_final_execution_safety_gate() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=step1_permission(
            state="STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES",
            allowed_actions=["HOLD", "NO_TRADE"],
        ),
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=step3_audited_packet(),
    )

    assert result.ready_for_order_compilation is False
    assert result.recommended_result == "NO_TRADE"
    assert result.checked_conditions["step1_state_strict_fresh"] is False
    assert result.checked_conditions["order_compilation_allowed"] is False


def test_order_compilation_not_allowed_blocks() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=step1_permission(allowed_actions=["HOLD", "NO_TRADE"]),
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=step3_audited_packet(),
    )
    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["order_compilation_allowed"] is False


@pytest.mark.parametrize("block_key", ["step2_block", "step3_block", "step4_block"])
def test_upstream_block_artifact_blocks(block_key: str) -> None:
    result = evaluate_final_execution_safety(
        **allow(),
        **{block_key: {"blocked": True}},
    )
    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["no_upstream_block"] is False


def test_missing_step2_decision_packet_blocks() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=step1_permission(),
        step2_decision_packet=None,
        step3_audited_packet=step3_audited_packet(),
    )
    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["step2_decision_packet_structured"] is False


def test_missing_step3_audited_packet_blocks() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=step1_permission(),
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=None,
    )
    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["step3_audited_packet_structured"] is False


def test_malformed_step3_execution_plan_field_blocks() -> None:
    # LLM bools true, but the execution-plan field is not a list.
    result = evaluate_final_execution_safety(
        step1_permission=step1_permission(),
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=step3_audited_packet(final_execution_plans="not-a-list"),
    )
    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["step3_audited_packet_structured"] is False
    # audit_passed / order_compiler_ready being true did NOT make it ready.
    assert result.checked_conditions["step3_audit_passed"] is True
    assert result.checked_conditions["step3_order_compiler_ready"] is True


def test_audit_passed_false_blocks() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=step1_permission(),
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=step3_audited_packet(audit_passed=False),
    )
    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["step3_audit_passed"] is False


def test_order_compiler_ready_false_blocks() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=step1_permission(),
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=step3_audited_packet(order_compiler_ready=False),
    )
    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["step3_order_compiler_ready"] is False


def test_blocker_reasons_non_empty_blocks() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=step1_permission(),
        step2_decision_packet=step2_decision_packet(blocker_reasons=["something failed"]),
        step3_audited_packet=step3_audited_packet(),
    )
    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["no_explicit_blockers"] is False


def test_manual_review_required_blocks_and_propagates() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=step1_permission(manual_review_required=True),
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=step3_audited_packet(),
    )
    assert result.ready_for_order_compilation is False
    assert result.manual_review_required is True
    assert result.checked_conditions["step1_no_manual_review"] is False


def test_buy_intent_without_new_buy_permission_blocks() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=step1_permission(allowed_actions=["HOLD", "NO_TRADE", "ORDER_COMPILATION"]),
        step2_decision_packet=step2_decision_packet(buy_side_delta_table=[{"ticker": "QQQ"}]),
        step3_audited_packet=step3_audited_packet(),
    )
    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["new_buy_allowed_if_needed"] is False


def test_llm_self_report_true_is_not_sufficient_alone() -> None:
    # audit_passed=true + order_compiler_ready=true, but upstream is blocked and
    # there is no STRICT_FRESH permission: the gate must still refuse.
    result = evaluate_final_execution_safety(
        step1_permission=None,
        step2_decision_packet=None,
        step3_audited_packet=step3_audited_packet(audit_passed=True, order_compiler_ready=True),
        step2_block={"blocked": True},
    )
    assert result.ready_for_order_compilation is False


def test_blocked_artifact_payload_schema() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=None,
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=step3_audited_packet(),
    )
    payload = final_execution_safety_blocked_artifact_payload(
        result, source_artifacts={"step1_permission": "artifacts/.../decision.json"}
    )
    assert payload["blocked"] is True
    assert payload["reason"] == "final_execution_safety_gate"
    assert payload["ready_for_order_compilation"] is False
    assert payload["recommended_result"] == "NO_TRADE"
    assert payload["is_deterministic"] is True
    assert payload["report_only"] is False
    assert "checked_conditions" in payload
    assert "source_artifacts" in payload
    json.dumps(payload, ensure_ascii=False)


# --- Step 4 integration ------------------------------------------------------


def market_snapshot() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "snapshot_type": "MARKET_DATA_SNAPSHOT",
        "run_timestamp_et": "2026-06-22 16:33 ET",
        "execution_date_et": "2026-06-22",
        "market_data_target_close_date_et": "2026-06-22",
        "close_time_zone": "America/New_York",
        "display_time_zone": "America/Los_Angeles",
        "primary_source": "fixture",
        "fallback_source_for_last_close_and_price_asof_only": "fixture",
        "holiday_aware_close_resolution": True,
        "tickers": [],
    }


def prepare_tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for module in (path_helpers, step1_research, step2_decision_builder, step3_audit_engine, step4_order_compiler):
        monkeypatch.setattr(module, "repo_root", lambda: tmp_path)

    write_text(
        tmp_path / "prompts" / "strategy_c_order_compiler.txt",
        "RESEARCH\n{{ research_json }}\nPORTFOLIO\n{{ portfolio_snapshot }}\n"
        "SETTINGS\n{{ strategy_settings }}\nMARKET\n{{ market_data_snapshot }}\n"
        "AUDITED\n{{ audited_decision_packet }}\n",
    )
    write_text(tmp_path / "inputs" / "current" / "strategy_settings.yaml", "as_of: '2026-06-22'\n")
    write_text(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", "QQQ | 1 | 100\n")
    write_json(step1_research.step1_research_output_path(), {"schema_version": "1.0"})


def write_full_upstream(tmp_path: Path, *, strict_fresh_permission: bool = True) -> None:
    if strict_fresh_permission:
        write_json(
            step1_research.step1_research_degraded_mode_decision_path(),
            step1_permission(),
        )
    write_text(step2_decision_builder.step2_prompt_path(), "STEP2 PROMPT\n")
    write_text(step2_decision_builder.step2_raw_output_path(), "STEP2 RAW\n")
    write_text(step2_decision_builder.step2_template2_output_path(), "TEMPLATE2 OUTPUT\n")
    packet = step2_decision_packet()
    packet["MARKET_DATA_SNAPSHOT"] = market_snapshot()
    write_json(step2_decision_builder.step2_decision_packet_path(), packet)
    write_text(step3_audit_engine.step3_prompt_path(), "STEP3 PROMPT\n")
    write_text(step3_audit_engine.step3_raw_output_path(), "STEP3 RAW\n")
    write_text(step3_audit_engine.step3_template3_audit_path(), "TEMPLATE3 AUDIT\n")
    write_json(step3_audit_engine.step3_audited_decision_packet_path(), step3_audited_packet())


def test_step4_final_gate_blocks_before_prompt_when_permission_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    # All upstream artifacts present (upstream guard passes) but NO Step 1
    # STRICT_FRESH permission -> final gate must block.
    write_full_upstream(tmp_path, strict_fresh_permission=False)
    monkeypatch.setattr(
        step4_order_compiler,
        "build_step4_prompt_text",
        lambda: pytest.fail("Step 4 rendered a prompt despite final-gate block"),
    )

    with pytest.raises(FinalExecutionSafetyGateError):
        step4_order_compiler.render_step4_prompt()

    assert not step4_order_compiler.step4_prompt_path().exists()
    payload = json.loads(
        step4_order_compiler.step4_blocked_by_final_execution_safety_gate_path().read_text(
            encoding="utf-8"
        )
    )
    assert payload["blocked"] is True
    assert payload["reason"] == "final_execution_safety_gate"
    assert payload["recommended_result"] == "NO_TRADE"
    assert payload["is_deterministic"] is True
    assert payload["ready_for_order_compilation"] is False


def test_step4_final_gate_blocks_when_audit_passed_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_full_upstream(tmp_path)
    # LLM self-reported audit_passed=false -> still blocked (bool is necessary).
    write_json(
        step3_audit_engine.step3_audited_decision_packet_path(),
        step3_audited_packet(audit_passed=False),
    )

    with pytest.raises(FinalExecutionSafetyGateError):
        step4_order_compiler.render_step4_prompt()

    assert step4_order_compiler.step4_blocked_by_final_execution_safety_gate_path().exists()
    assert not step4_order_compiler.step4_prompt_path().exists()


def test_step4_final_gate_allows_strict_fresh_structured_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_full_upstream(tmp_path)

    result = step4_order_compiler.render_step4_prompt()

    assert step4_order_compiler.step4_prompt_path().exists()
    assert not step4_order_compiler.step4_blocked_by_final_execution_safety_gate_path().exists()
    assert result["prompt_path"] == str(step4_order_compiler.step4_prompt_path())


def test_step4_upstream_guard_runs_before_final_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    # Step 2 research gate blocked -> upstream guard must fire first; the final
    # gate's own blocked artifact must NOT be written.
    write_json(step2_decision_builder.step2_blocked_by_research_gate_path(), {"blocked": True})

    with pytest.raises(Exception):
        step4_order_compiler.render_step4_prompt()

    assert step4_order_compiler.step4_blocked_by_upstream_gate_path().exists()
    assert not step4_order_compiler.step4_blocked_by_final_execution_safety_gate_path().exists()


# --- PR G1: primary-path effective universe wiring ---------------------------


def test_load_effective_allowed_buy_universe_reads_stricter_per_run_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    packet = step2_decision_packet()
    packet["effective_allowed_buy_universe"] = ["QQQ", "SMH"]  # stricter per-run subset
    write_json(step2_decision_builder.step2_decision_packet_path(), packet)

    assert step4_order_compiler.load_effective_allowed_buy_universe() == ["QQQ", "SMH"]


def test_load_effective_allowed_buy_universe_none_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    # No decision packet written -> None, so the validator falls back to the
    # static strategy-settings universe floor (never weaker than settings).
    assert step4_order_compiler.load_effective_allowed_buy_universe() is None


def promoted_step3_audit_only_permission() -> dict[str, Any]:
    return {
        "state": "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY",
        "research_availability": "strict_fresh_compiled_actionable_step3_audit_only",
        "allowed_actions": [
            "HOLD",
            "NO_TRADE",
            "PROMOTED_RESEARCH_DECISION",
            "PROMOTED_RESEARCH_AUDIT",
        ],
        "blocked_actions": [
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "promoted_step2_decision_only": True,
        "promoted_step3_audit_only": True,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "step4_allowed": False,
        "final_execution_allowed": False,
        "broker_automation_allowed": False,
        "manual_review_required": False,
        "report_only": True,
    }


def test_promoted_step3_audit_only_on_disk_layout_blocks_final_execution_safety_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2E.5b-6f.1: on-disk regression guard (not just an in-memory call).

    Writes the real ``research_degraded_mode_decision.json`` artifact that a
    promoted Step 3 audit-only run produces, with no Step 2/3 downstream
    artifacts on disk (accurately reflecting that promoted audit-only never
    writes an ``audited_decision_packet.json``), and drives the final gate's
    own file-reading entrypoint directly -- independent of the upstream
    artifact guard, which has its own coverage. This proves the final
    execution safety gate itself, reading from disk, still rejects the
    promoted Step 3 audit-only state and authorizes no order/final-execution
    artifact.
    """
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_json(
        step1_research.step1_research_degraded_mode_decision_path(),
        promoted_step3_audit_only_permission(),
    )

    with pytest.raises(FinalExecutionSafetyGateError):
        step4_order_compiler.enforce_step4_final_execution_safety_gate()

    block_path = step4_order_compiler.step4_blocked_by_final_execution_safety_gate_path()
    assert block_path.exists()
    payload = json.loads(block_path.read_text(encoding="utf-8"))

    assert payload["blocked"] is True
    assert payload["ready_for_order_compilation"] is False
    assert payload["recommended_result"] == "NO_TRADE"
    assert payload["checked_conditions"]["step1_state_strict_fresh"] is False
    assert payload["checked_conditions"]["order_compilation_allowed"] is False
    assert not step4_order_compiler.step4_prompt_path().exists()
    assert not step4_order_compiler.step4_template4_orders_path().exists()
    assert not step4_order_compiler.step4_order_state_export_path().exists()
    assert not step4_order_compiler.step4_exec_summary_path().exists()


def test_promoted_step2_decision_only_state_still_blocks_final_execution_safety_gate() -> None:
    # R2E.5b-6c: the decision-only state permits Step 2 ONLY; the final gate is
    # unchanged and must reject it (state != STRICT_FRESH, ORDER_COMPILATION absent).
    result = evaluate_final_execution_safety(
        step2_decision_packet=step2_decision_packet(),
        step3_audited_packet=step3_audited_packet(),
        step1_permission=step1_permission(
            state="STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY",
            allowed_actions=["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"],
        ),
    )

    assert result.ready_for_order_compilation is False
    assert result.blocked is True
    assert result.checked_conditions["step1_state_strict_fresh"] is False
    assert result.checked_conditions["order_compilation_allowed"] is False
    assert result.recommended_result == "NO_TRADE"
