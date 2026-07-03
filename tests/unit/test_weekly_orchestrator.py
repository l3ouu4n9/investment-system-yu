"""Tests for the weekly-level controlled orchestration (PR H1).

Covers the two routes from the Step 1 degraded-mode decision:

- degraded / blocked / missing / malformed permission -> deterministic
  controlled NO_TRADE terminal outcome (weekly_outcome.json + run_summary.json,
  exit 0), without entering the Step 2 LLM path or fabricating any downstream
  decision / audit / order artifact.
- STRICT_FRESH actionable permission -> proceed to the existing Step 2 render
  path; no terminal NO_TRADE is written.

It also asserts the step-level Step 2 gate behavior is unchanged (still fails
closed) and the weekly outcome is a deterministic, non-LLM operational artifact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.common.io import write_json, write_text
from investment_orchestrator.state.research_degraded_mode_gate import (
    ResearchDegradedModeGateError,
)
from investment_orchestrator.workflow import (
    step1_research,
    step2_decision_builder,
    step3_audit_engine,
    step4_order_compiler,
    weekly_orchestrator,
)


BAD_RESEARCH_SENTINEL = "BAD_RESEARCH_SENTINEL_SHOULD_NOT_ENTER_PROMPT"
DECISION_DISPLAY = "artifacts/current/step1_research/research_degraded_mode_decision.json"


def strict_fresh_permission() -> dict[str, Any]:
    return {
        "state": "STRICT_FRESH",
        "research_availability": "strict_fresh",
        "fresh_research_available": True,
        "handoff_valid": True,
        "handoff_stale": False,
        "settings_hash_match": True,
        "universe_match": True,
        "allowed_actions": [
            "HOLD",
            "NO_TRADE",
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "blocked_actions": [],
        "manual_review_required": False,
        "blocker_reasons": [],
        "non_blocker_reasons": [],
        "report_only": True,
    }


def degraded_permission(state: str, *, manual_review_required: bool = False) -> dict[str, Any]:
    return {
        "state": state,
        "research_availability": state.lower(),
        "fresh_research_available": False,
        "handoff_valid": False,
        "handoff_stale": state == "STRICT_STALE",
        "settings_hash_match": False,
        "universe_match": True,
        "allowed_actions": ["HOLD", "NO_TRADE"],
        "blocked_actions": [
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "manual_review_required": manual_review_required,
        "blocker_reasons": [f"research state {state} is not STRICT_FRESH."],
        "non_blocker_reasons": [],
        "report_only": True,
    }


def patch_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every module's repo_root() helper at the tmp repo."""
    for module in (
        weekly_orchestrator,
        step1_research,
        step2_decision_builder,
        step3_audit_engine,
        step4_order_compiler,
    ):
        monkeypatch.setattr(module, "repo_root", lambda: tmp_path)


def prepare_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    permission: dict[str, Any] | None = None,
    with_step2_render_inputs: bool = False,
) -> None:
    patch_repo_root(tmp_path, monkeypatch)
    if permission is not None:
        write_json(step1_research.step1_research_degraded_mode_decision_path(), permission)
    if with_step2_render_inputs:
        write_text(
            tmp_path / "prompts" / "strategy_a_decision_builder.txt",
            "RESEARCH\n{{ research_json }}\nPORTFOLIO\n{{ portfolio_snapshot }}\n"
            "SETTINGS\n{{ strategy_settings }}\n",
        )
        write_text(
            tmp_path / "inputs" / "current" / "strategy_settings.yaml", "as_of: '2026-06-22'\n"
        )
        write_text(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", "QQQ | 1 | 100\n")
        write_json(
            step1_research.step1_research_output_path(),
            {"schema_version": "1.0", "as_of": "2026-06-22", "sentinel": BAD_RESEARCH_SENTINEL},
        )


def read_weekly_outcome(tmp_path: Path) -> dict[str, Any]:
    payload = json.loads(
        weekly_orchestrator.weekly_outcome_path(tmp_path).read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload


def assert_no_downstream_artifacts_created(tmp_path: Path) -> None:
    """No Step 2/3/4 decision / audit / order artifacts may be fabricated."""
    base = tmp_path / "artifacts" / "current"
    forbidden = [
        base / "step2_decision_builder" / "prompt.txt",
        base / "step2_decision_builder" / "decision_packet.json",
        base / "step2_decision_builder" / "template2_output.txt",
        base / "step3_audit_engine" / "step3_promoted_audit_only.json",
        base / "step3_audit_engine" / "step3_promoted_audit_only_downstream_block.json",
        base / "step3_audit_engine" / "audited_decision_packet.json",
        base / "step4_order_compiler" / "template4_orders.txt",
        base / "step4_order_compiler" / "order_state_export.txt",
        base / "step4_order_compiler" / "exec_summary.txt",
    ]
    for path in forbidden:
        assert not path.exists(), f"weekly NO_TRADE path must not create {path}"


# --- terminal NO_TRADE routes ------------------------------------------------


@pytest.mark.parametrize(
    ("state", "manual_review_required"),
    [
        ("DEGRADED_WITH_LAST_GOOD", False),
        ("STRICT_STALE", False),
        ("DEGRADED_NO_RESEARCH", False),
        ("INVALID_CONTRACT", False),
        ("NO_OUTPUT", False),
        # R2E.1: non-actionable compiled evidence-first state -> controlled NO_TRADE.
        ("STRICT_FRESH_EVIDENCE_ONLY", False),
        # R2E.4: grounded-memo state is likewise non-actionable -> controlled NO_TRADE.
        ("STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE", False),
        # R2E.5b-5b: promoted pointer is recognized, but gates remain closed.
        ("STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES", False),
        ("MANUAL_REVIEW_REQUIRED", True),
    ],
)
def test_degraded_states_produce_controlled_no_trade_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    manual_review_required: bool,
) -> None:
    prepare_repo(
        tmp_path,
        monkeypatch,
        permission=degraded_permission(state, manual_review_required=manual_review_required),
    )

    result = weekly_orchestrator.run_weekly()

    assert result.actionable is False
    assert result.weekly_completed is True
    assert result.terminal_result == "NO_TRADE"
    assert result.research_state == state
    assert result.manual_review_required is manual_review_required
    assert result.exit_code == 0  # controlled completion, not an error

    outcome = read_weekly_outcome(tmp_path)
    assert outcome["terminal_result"] == "NO_TRADE"
    assert outcome["reason"] == "research_degraded_mode"
    assert outcome["research_state"] == state
    assert outcome["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in outcome["blocked_actions"]
    assert "ORDER_COMPILATION" in outcome["blocked_actions"]
    assert outcome["manual_review_required"] is manual_review_required
    assert outcome["is_llm_generated"] is False
    assert outcome["weekly_completed"] is True
    # Source artifacts reference the deterministic decision + run summary only.
    assert outcome["source_artifacts"]["research_degraded_mode_decision"] == DECISION_DISPLAY
    assert outcome["source_artifacts"]["run_summary"] == "artifacts/current/run_summary.json"

    # run_summary.json is written and agrees on NO_TRADE.
    run_summary_path = tmp_path / "artifacts" / "current" / "run_summary.json"
    assert run_summary_path.is_file()
    run_summary = json.loads(run_summary_path.read_text(encoding="utf-8"))
    assert run_summary["recommended_result"] == "NO_TRADE"
    assert run_summary["is_llm_generated"] is False

    assert_no_downstream_artifacts_created(tmp_path)
    # Step 2 LLM prompt is never rendered on the terminal path.
    assert not step2_decision_builder.step2_prompt_path().exists()


def test_manual_review_required_terminal_flags_manual_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_repo(
        tmp_path,
        monkeypatch,
        permission=degraded_permission("MANUAL_REVIEW_REQUIRED", manual_review_required=True),
    )

    result = weekly_orchestrator.run_weekly()

    assert result.terminal_result == "NO_TRADE"
    assert result.manual_review_required is True
    outcome = read_weekly_outcome(tmp_path)
    assert outcome["manual_review_required"] is True
    run_summary = json.loads(
        (tmp_path / "artifacts" / "current" / "run_summary.json").read_text(encoding="utf-8")
    )
    assert run_summary["manual_review_required"] is True


def test_missing_decision_artifact_fails_closed_to_no_trade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No Step 1 decision artifact at all -> fail closed to a controlled NO_TRADE.
    prepare_repo(tmp_path, monkeypatch, permission=None)

    result = weekly_orchestrator.run_weekly()

    assert result.actionable is False
    assert result.terminal_result == "NO_TRADE"
    assert result.research_state == "MISSING_RESEARCH_PERMISSION"
    assert result.exit_code == 0
    assert_no_downstream_artifacts_created(tmp_path)


def test_malformed_decision_artifact_fails_closed_to_no_trade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_repo_root(tmp_path, monkeypatch)
    write_text(step1_research.step1_research_degraded_mode_decision_path(), "{not valid json")

    result = weekly_orchestrator.run_weekly()

    assert result.terminal_result == "NO_TRADE"
    assert result.research_state == "MALFORMED_RESEARCH_PERMISSION"
    assert_no_downstream_artifacts_created(tmp_path)


def test_weekly_outcome_is_deterministic_and_not_llm_generated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_repo(tmp_path, monkeypatch, permission=degraded_permission("NO_OUTPUT"))

    weekly_orchestrator.run_weekly()
    first = read_weekly_outcome(tmp_path)
    weekly_orchestrator.run_weekly()
    second = read_weekly_outcome(tmp_path)

    assert first == second
    assert first["is_llm_generated"] is False
    assert first["report_only"] is False
    # No fabricated decision/audit/order payloads embedded in the artifact.
    for forbidden in ("decision_packet", "audited_decision_packet", "orders", "template4_orders"):
        assert forbidden not in first


# --- actionable STRICT_FRESH route -------------------------------------------


def test_strict_fresh_proceeds_to_step2_render_and_does_not_terminal_no_trade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_repo(
        tmp_path,
        monkeypatch,
        permission=strict_fresh_permission(),
        with_step2_render_inputs=True,
    )

    result = weekly_orchestrator.run_weekly()

    assert result.actionable is True
    assert result.weekly_completed is False
    assert result.terminal_result is None  # no terminal NO_TRADE before Step 2
    assert result.research_state == "STRICT_FRESH"

    # The existing Step 2 render path was exercised (prompt produced).
    assert step2_decision_builder.step2_prompt_path().exists()
    assert not step2_decision_builder.step2_blocked_by_research_gate_path().exists()

    outcome = read_weekly_outcome(tmp_path)
    assert outcome["actionable"] is True
    assert outcome["terminal_result"] is None
    assert outcome["reason"] == "research_strict_fresh_actionable"
    assert outcome["is_llm_generated"] is False

    # No run_summary NO_TRADE is fabricated on the actionable path.
    assert not (tmp_path / "artifacts" / "current" / "run_summary.json").exists()


def test_strict_fresh_can_proceed_without_rendering_step2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_repo(tmp_path, monkeypatch, permission=strict_fresh_permission())

    result = weekly_orchestrator.run_weekly(render_step2_when_actionable=False)

    assert result.actionable is True
    assert result.terminal_result is None
    # Did not render Step 2 in this mode.
    assert not step2_decision_builder.step2_prompt_path().exists()
    outcome = read_weekly_outcome(tmp_path)
    assert outcome["actionable"] is True
    assert "step2_prompt" not in outcome["source_artifacts"]


def test_strict_fresh_missing_required_action_does_not_proceed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # STRICT_FRESH but ORDER_COMPILATION not permitted -> not actionable -> NO_TRADE.
    permission = strict_fresh_permission()
    permission["allowed_actions"] = ["HOLD", "NO_TRADE", "NEW_BUY"]
    prepare_repo(tmp_path, monkeypatch, permission=permission)

    result = weekly_orchestrator.run_weekly()

    assert result.actionable is False
    assert result.terminal_result == "NO_TRADE"
    assert result.research_state == "STRICT_FRESH"
    assert not step2_decision_builder.step2_prompt_path().exists()


# --- step-level gate behavior is unchanged -----------------------------------


def test_step_level_step2_render_still_fails_closed_when_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The weekly terminal path does not change the step-level fail-closed gate:
    # run_step2 render still raises for a degraded permission.
    prepare_repo(
        tmp_path,
        monkeypatch,
        permission=degraded_permission("DEGRADED_WITH_LAST_GOOD"),
        with_step2_render_inputs=True,
    )

    # weekly resolves to a controlled NO_TRADE...
    result = weekly_orchestrator.run_weekly()
    assert result.terminal_result == "NO_TRADE"

    # ...but the step-level command remains fail-closed (exit 1 / raises).
    with pytest.raises(ResearchDegradedModeGateError, match="Step 2 blocked"):
        step2_decision_builder.render_step2_prompt()


# --- CLI exit behavior -------------------------------------------------------


def test_cli_exit_zero_on_controlled_no_trade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from investment_orchestrator.cli import run_weekly as run_weekly_cli

    prepare_repo(tmp_path, monkeypatch, permission=degraded_permission("DEGRADED_WITH_LAST_GOOD"))
    monkeypatch.setattr(run_weekly_cli.sys, "argv", ["run_weekly"])

    exit_code = run_weekly_cli.main()

    assert exit_code == 0  # weekly-level controlled NO_TRADE is exit 0
    out = capsys.readouterr().out
    assert "weekly_actionable=false" in out
    assert "terminal_result=NO_TRADE" in out


def test_cli_exit_zero_on_actionable_proceed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from investment_orchestrator.cli import run_weekly as run_weekly_cli

    prepare_repo(tmp_path, monkeypatch, permission=strict_fresh_permission())
    monkeypatch.setattr(run_weekly_cli.sys, "argv", ["run_weekly", "--no-render-step2"])

    exit_code = run_weekly_cli.main()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "weekly_actionable=true" in out


# --- R2E.5b-6c promoted Step 2 decision-only weekly terminal -------------------


def promoted_decision_only_permission() -> dict[str, Any]:
    return {
        "state": "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY",
        "research_availability": "strict_fresh_compiled_actionable_step2_decision_only",
        "fresh_research_available": False,
        "handoff_valid": False,
        "handoff_stale": False,
        "settings_hash_match": None,
        "universe_match": None,
        "allowed_actions": ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"],
        "blocked_actions": [
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "manual_review_required": False,
        "blocker_reasons": [
            "promoted_step2_decision_only_enabled",
            "order_compilation_requires_future_gate_pr",
            "final_execution_requires_future_gate_pr",
        ],
        "non_blocker_reasons": [],
        "source": "promoted_compiled_actionable_handoff",
        "promoted_step2_decision_only": True,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "report_only": True,
    }


def promoted_step3_audit_only_permission() -> dict[str, Any]:
    payload = promoted_decision_only_permission()
    payload.update(
        {
            "state": "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY",
            "research_availability": "strict_fresh_compiled_actionable_step3_audit_only",
            "allowed_actions": [
                "HOLD",
                "NO_TRADE",
                "PROMOTED_RESEARCH_DECISION",
                "PROMOTED_RESEARCH_AUDIT",
            ],
            "promoted_step3_audit_only": True,
            "step3_audit_only_allowed": True,
            "permission_effect": "promoted_step3_audit_only",
        }
    )
    return payload


def test_promoted_decision_only_terminates_no_trade_pending_final_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_repo(
        tmp_path,
        monkeypatch,
        permission=promoted_decision_only_permission(),
        with_step2_render_inputs=True,  # even with render inputs available...
    )

    result = weekly_orchestrator.run_weekly()

    # ...weekly never auto-runs the Step 2 LLM path for the decision-only state.
    assert result.actionable is False
    assert result.weekly_completed is True
    assert result.terminal_result == "NO_TRADE_PENDING_FINAL_GATES"
    assert result.research_state == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    assert result.exit_code == 0
    assert result.step2_prompt_path is None
    assert not step2_decision_builder.step2_prompt_path().exists()

    outcome = read_weekly_outcome(tmp_path)
    assert outcome["terminal_result"] == "NO_TRADE_PENDING_FINAL_GATES"
    assert outcome["reason"] == "promoted_step2_decision_only_pending_final_gates"
    assert outcome["actionable"] is False
    assert outcome["weekly_completed"] is True
    assert outcome["mode"] == "promoted_step2_decision_only"
    assert outcome["allowed_actions"] == ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]
    assert "NEW_BUY" in outcome["blocked_actions"]
    assert "ORDER_COMPILATION" in outcome["blocked_actions"]
    assert outcome["order_compilation_allowed"] is False
    assert outcome["new_buy_permission"] is False
    assert outcome["is_llm_generated"] is False
    assert "run_step2" in outcome["next_step"]  # manual decision-only flow is enabled

    # run_summary agrees: blocked run, NO_TRADE recommendation, decision-only state.
    run_summary_path = tmp_path / "artifacts" / "current" / "run_summary.json"
    assert run_summary_path.is_file()
    run_summary = json.loads(run_summary_path.read_text(encoding="utf-8"))
    assert run_summary["run_blocked"] is True
    assert run_summary["recommended_result"] == "NO_TRADE"
    assert run_summary["research_state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"

    # No Step 2/3/4 decision / audit / order artifacts are fabricated.
    assert_no_downstream_artifacts_created(tmp_path)


def test_weekly_promoted_step3_audit_only_remains_no_order_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_repo(
        tmp_path,
        monkeypatch,
        permission=promoted_step3_audit_only_permission(),
        with_step2_render_inputs=True,
    )

    result = weekly_orchestrator.run_weekly()

    assert result.actionable is False
    assert result.weekly_completed is True
    assert result.terminal_result == "NO_TRADE"
    assert result.research_state == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY"
    assert result.step2_prompt_path is None
    assert not step2_decision_builder.step2_prompt_path().exists()
    assert not step3_audit_engine.step3_prompt_path().exists()
    assert not step4_order_compiler.step4_prompt_path().exists()

    outcome = read_weekly_outcome(tmp_path)
    assert outcome["terminal_result"] == "NO_TRADE"
    assert outcome["reason"] == "research_degraded_mode"
    assert outcome["allowed_actions"] == [
        "HOLD",
        "NO_TRADE",
        "PROMOTED_RESEARCH_DECISION",
        "PROMOTED_RESEARCH_AUDIT",
    ]
    assert "NEW_BUY" in outcome["blocked_actions"]
    assert "ORDER_COMPILATION" in outcome["blocked_actions"]
    assert outcome["is_llm_generated"] is False
    assert_no_downstream_artifacts_created(tmp_path)
