from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.state.blocked_run_summary import (
    BlockedRunSummaryResult,
    blocked_run_summary_result_to_dict,
    build_blocked_run_summary,
    summarize_current_run,
    terminal_observation_from_research_gate,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    evaluate_step2_research_gate,
)


STEP1_DECISION_DISPLAY = "artifacts/current/step1_research/research_degraded_mode_decision.json"


def step1_decision(
    *,
    state: str = "NO_OUTPUT",
    manual_review_required: bool = False,
    allowed_actions: list[str] | None = None,
    blocked_actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "research_availability": state.lower(),
        "allowed_actions": allowed_actions or ["HOLD", "NO_TRADE"],
        "blocked_actions": blocked_actions
        if blocked_actions is not None
        else ["SELL", "NEW_BUY", "ROTATION", "REBALANCE", "EXTENDED_ETF_ADMISSION", "ORDER_COMPILATION"],
        "manual_review_required": manual_review_required,
        "blocker_reasons": ["no research output and no last-known-good available."],
        "non_blocker_reasons": [],
        "report_only": True,
    }


def step2_block(*, state: str = "NO_OUTPUT", manual_review_required: bool = False) -> dict[str, Any]:
    return {
        "blocked": True,
        "reason": "research_degraded_mode_gate",
        "state": state,
        "allowed_actions": ["HOLD", "NO_TRADE"],
        "blocked_actions": ["NEW_BUY", "ORDER_COMPILATION"],
        "manual_review_required": manual_review_required,
        "blocker_reasons": [f"research state {state} is not STRICT_FRESH."],
        "source_artifact": STEP1_DECISION_DISPLAY,
        "recommended_result": "NO_TRADE",
        "report_only": False,
    }


def upstream_block(
    *,
    reason: str = "upstream_research_gate_blocked",
    state: str = "NO_OUTPUT",
    manual_review_required: bool = False,
    with_permission: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "blocked": True,
        "reason": reason,
        "blocked_by_artifact": "artifacts/current/step2_decision_builder/step2_blocked_by_research_gate.json",
        "missing_required_artifacts": [],
        "stale_or_inconsistent_artifacts": ["upstream_gate_already_blocked:..."],
        "recommended_result": "NO_TRADE",
        "manual_review_required": manual_review_required,
        "report_only": False,
        "upstream_permission_read_errors": [],
    }
    payload["upstream_permission"] = (
        {
            "state": state,
            "research_availability": state.lower(),
            "allowed_actions": ["HOLD", "NO_TRADE"],
            "blocked_actions": ["NEW_BUY", "ORDER_COMPILATION"],
            "manual_review_required": manual_review_required,
            "blocker_reasons": [f"research state {state} is not STRICT_FRESH."],
            "non_blocker_reasons": [],
            "recommended_result": "NO_TRADE",
            "source_artifact": STEP1_DECISION_DISPLAY,
        }
        if with_permission
        else None
    )
    return payload


def final_safety_block(
    *,
    manual_review_required: bool = False,
    fail_reasons: list[str] | None = None,
) -> dict[str, Any]:
    reasons = fail_reasons or ["step3_audited_packet.blocker_reasons is non-empty."]
    return {
        "blocked": True,
        "reason": "final_execution_safety_gate",
        "ready_for_order_compilation": False,
        "recommended_result": "NO_TRADE",
        "manual_review_required": manual_review_required,
        "fail_reasons": list(reasons),
        "blocker_reasons": list(reasons),
        "non_blocker_reasons": [],
        "checked_conditions": {"no_explicit_blockers": False},
        "is_deterministic": True,
        "report_only": False,
    }


# --- build_blocked_run_summary ----------------------------------------------


def test_no_output_chain_summarizes_to_no_trade() -> None:
    result = build_blocked_run_summary(
        step1_decision=step1_decision(state="NO_OUTPUT"),
        step2_block=step2_block(state="NO_OUTPUT"),
        step3_block=upstream_block(state="NO_OUTPUT"),
        step4_block=upstream_block(state="NO_OUTPUT"),
    )

    assert result.run_blocked is True
    assert result.recommended_result == "NO_TRADE"
    assert result.research_state == "NO_OUTPUT"
    assert result.highest_severity_state == "NO_OUTPUT"
    assert result.research_availability == "no_output"
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in result.blocked_actions
    assert "ORDER_COMPILATION" in result.blocked_actions
    assert result.blocked_stages == ["step2", "step3", "step4"]
    assert result.manual_review_required is False


def test_strict_fresh_evidence_only_summarizes_to_no_trade() -> None:
    # R2E.1: the non-actionable compiled evidence-first state is a controlled
    # NO_TRADE run (HOLD/NO_TRADE only); the summary must reflect that.
    result = build_blocked_run_summary(
        step1_decision=step1_decision(state="STRICT_FRESH_EVIDENCE_ONLY"),
        step2_block=step2_block(state="STRICT_FRESH_EVIDENCE_ONLY"),
        step3_block=None,
        step4_block=None,
    )

    assert result.run_blocked is True  # not STRICT_FRESH -> degraded/blocked
    assert result.recommended_result == "NO_TRADE"
    assert result.research_state == "STRICT_FRESH_EVIDENCE_ONLY"
    assert result.highest_severity_state == "STRICT_FRESH_EVIDENCE_ONLY"
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in result.blocked_actions
    assert "ORDER_COMPILATION" in result.blocked_actions
    assert result.manual_review_required is False


def test_grounded_memo_state_summarizes_to_no_trade() -> None:
    # R2E.4: the grounded-memo state is also a controlled NO_TRADE run.
    state = "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
    result = build_blocked_run_summary(
        step1_decision=step1_decision(state=state),
        step2_block=step2_block(state=state),
        step3_block=None,
        step4_block=None,
    )

    assert result.run_blocked is True
    assert result.recommended_result == "NO_TRADE"
    assert result.research_state == state
    assert result.highest_severity_state == state  # known benign state (not -1/unknown)
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in result.blocked_actions
    assert "ORDER_COMPILATION" in result.blocked_actions
    assert result.manual_review_required is False


def test_pending_gates_state_summarizes_to_no_trade() -> None:
    # R2E.5b-5b: promoted actionable handoff exists, but gates are still closed.
    state = "STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES"
    result = build_blocked_run_summary(
        step1_decision=step1_decision(state=state),
        step2_block=step2_block(state=state),
        step3_block=None,
        step4_block=None,
    )

    assert result.run_blocked is True
    assert result.recommended_result == "NO_TRADE"
    assert result.research_state == state
    assert result.highest_severity_state == state
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in result.blocked_actions
    assert "ORDER_COMPILATION" in result.blocked_actions
    assert result.manual_review_required is False


def test_step1_manual_review_propagates_to_summary() -> None:
    result = build_blocked_run_summary(
        step1_decision=step1_decision(state="MANUAL_REVIEW_REQUIRED", manual_review_required=True),
        step2_block=None,
        step3_block=None,
        step4_block=None,
    )

    assert result.run_blocked is True  # step1 degraded
    assert result.manual_review_required is True
    assert result.research_state == "MANUAL_REVIEW_REQUIRED"
    assert result.highest_severity_state == "MANUAL_REVIEW_REQUIRED"


def test_upstream_block_manual_review_propagates_to_summary() -> None:
    result = build_blocked_run_summary(
        step1_decision=None,
        step2_block=None,
        step3_block=upstream_block(state="MANUAL_REVIEW_REQUIRED", manual_review_required=True),
        step4_block=None,
    )

    assert result.manual_review_required is True
    assert result.blocked_stages == ["step3"]


def test_step2_blocked_only_lists_step2_stage() -> None:
    result = build_blocked_run_summary(
        step1_decision=step1_decision(state="NO_OUTPUT"),
        step2_block=step2_block(state="NO_OUTPUT"),
        step3_block=None,
        step4_block=None,
    )

    assert result.blocked_stages == ["step2"]
    assert result.run_blocked is True


def test_step3_4_blocks_with_upstream_permission_expose_state_and_actions() -> None:
    result = build_blocked_run_summary(
        step1_decision=None,
        step2_block=None,
        step3_block=upstream_block(state="DEGRADED_NO_RESEARCH"),
        step4_block=upstream_block(state="DEGRADED_NO_RESEARCH"),
    )

    assert result.research_state == "DEGRADED_NO_RESEARCH"
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in result.blocked_actions
    assert result.blocked_stages == ["step3", "step4"]


def test_strict_fresh_with_no_blocks_is_not_blocked() -> None:
    result = build_blocked_run_summary(
        step1_decision=step1_decision(
            state="STRICT_FRESH",
            allowed_actions=[
                "HOLD",
                "NO_TRADE",
                "SELL",
                "NEW_BUY",
                "ROTATION",
                "REBALANCE",
                "EXTENDED_ETF_ADMISSION",
                "ORDER_COMPILATION",
            ],
            blocked_actions=[],
        ),
        step2_block=None,
        step3_block=None,
        step4_block=None,
    )

    assert result.run_blocked is False
    assert result.recommended_result is None  # do not fabricate NO_TRADE
    assert result.research_state == "STRICT_FRESH"
    assert result.blocked_stages == []
    assert result.blocked_actions == []
    assert "NEW_BUY" in result.allowed_actions


def test_no_artifacts_at_all_is_not_blocked() -> None:
    result = build_blocked_run_summary(
        step1_decision=None, step2_block=None, step3_block=None, step4_block=None
    )

    assert result.run_blocked is False
    assert result.recommended_result is None
    assert result.research_state is None
    assert result.highest_severity_state is None
    assert result.blocked_stages == []


def test_summary_is_deterministic_and_not_llm_generated() -> None:
    inputs = dict(
        step1_decision=step1_decision(),
        step2_block=step2_block(),
        step3_block=upstream_block(),
        step4_block=upstream_block(),
    )
    first = blocked_run_summary_result_to_dict(build_blocked_run_summary(**inputs))
    second = blocked_run_summary_result_to_dict(build_blocked_run_summary(**inputs))

    assert first == second
    assert first["is_llm_generated"] is False
    assert first["report_only"] is False
    json.dumps(first, ensure_ascii=False)


def test_summary_does_not_fabricate_decision_or_order_outputs() -> None:
    payload = blocked_run_summary_result_to_dict(
        build_blocked_run_summary(
            step1_decision=step1_decision(),
            step2_block=step2_block(),
            step3_block=upstream_block(),
            step4_block=upstream_block(),
        )
    )

    for forbidden in (
        "decision_packet",
        "audited_decision_packet",
        "orders",
        "final_buy_side_delta_table",
        "final_execution_plans",
        "template4_orders",
    ):
        assert forbidden not in payload


def test_primary_blocker_reasons_deduped_in_order() -> None:
    result = build_blocked_run_summary(
        step1_decision=step1_decision(state="NO_OUTPUT"),
        step2_block=step2_block(state="NO_OUTPUT"),
        step3_block=None,
        step4_block=None,
    )

    assert result.primary_blocker_reasons[0] == "research state NO_OUTPUT is not STRICT_FRESH."
    assert len(result.primary_blocker_reasons) == len(set(result.primary_blocker_reasons))


# --- UX3: final execution safety gate as a blocked source --------------------


def test_final_gate_only_block_is_run_blocked_no_trade_step4() -> None:
    # Upstream all green (STRICT_FRESH, no upstream blocks), but the final
    # execution safety gate blocked Step 4.
    result = build_blocked_run_summary(
        step1_decision=step1_decision(
            state="STRICT_FRESH",
            allowed_actions=["HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION"],
            blocked_actions=[],
        ),
        step2_block=None,
        step3_block=None,
        step4_block=None,
        step4_final_safety_block=final_safety_block(),
    )

    assert result.run_blocked is True
    assert result.recommended_result == "NO_TRADE"
    assert result.blocked_stages == ["step4"]


def test_final_gate_fail_reasons_appear_in_primary_blocker_reasons() -> None:
    result = build_blocked_run_summary(
        step1_decision=step1_decision(state="STRICT_FRESH", blocked_actions=[]),
        step2_block=None,
        step3_block=None,
        step4_block=None,
        step4_final_safety_block=final_safety_block(
            fail_reasons=["Step 3 final_execution_plans must be a list."]
        ),
    )

    assert "Step 3 final_execution_plans must be a list." in result.primary_blocker_reasons


def test_final_gate_manual_review_propagates_to_summary() -> None:
    result = build_blocked_run_summary(
        step1_decision=step1_decision(state="STRICT_FRESH", blocked_actions=[]),
        step2_block=None,
        step3_block=None,
        step4_block=None,
        step4_final_safety_block=final_safety_block(manual_review_required=True),
    )

    assert result.manual_review_required is True


def test_step4_upstream_and_final_gate_blocks_do_not_duplicate_step4() -> None:
    result = build_blocked_run_summary(
        step1_decision=step1_decision(state="NO_OUTPUT"),
        step2_block=None,
        step3_block=None,
        step4_block=upstream_block(),
        step4_final_safety_block=final_safety_block(),
    )

    assert result.blocked_stages.count("step4") == 1


def test_no_final_gate_block_preserves_existing_behavior() -> None:
    # Backward-compatible: omitting step4_final_safety_block matches prior result.
    result = build_blocked_run_summary(
        step1_decision=step1_decision(state="NO_OUTPUT"),
        step2_block=step2_block(state="NO_OUTPUT"),
        step3_block=upstream_block(),
        step4_block=upstream_block(),
    )

    assert result.run_blocked is True
    assert result.blocked_stages == ["step2", "step3", "step4"]


# --- summarize_current_run (filesystem, no monkeypatch needed) ---------------


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def summary_paths(tmp_path: Path) -> dict[str, Path]:
    base = tmp_path / "artifacts" / "current"
    return {
        "step1_decision_path": base / "step1_research" / "research_degraded_mode_decision.json",
        "step2_block_path": base / "step2_decision_builder" / "step2_blocked_by_research_gate.json",
        "step3_block_path": base / "step3_audit_engine" / "step3_blocked_by_upstream_gate.json",
        "step4_block_path": base / "step4_order_compiler" / "step4_blocked_by_upstream_gate.json",
        "step4_final_safety_block_path": base
        / "step4_order_compiler"
        / "step4_blocked_by_final_execution_safety_gate.json",
        "output_path": base / "run_summary.json",
    }


def test_summarize_current_run_writes_run_summary(tmp_path: Path) -> None:
    paths = summary_paths(tmp_path)
    write_json_file(paths["step1_decision_path"], step1_decision(state="NO_OUTPUT"))
    write_json_file(paths["step2_block_path"], step2_block(state="NO_OUTPUT"))
    write_json_file(paths["step3_block_path"], upstream_block(state="NO_OUTPUT"))
    write_json_file(paths["step4_block_path"], upstream_block(state="NO_OUTPUT"))

    result = summarize_current_run(repo_root_path=tmp_path, **paths)

    assert result.run_blocked is True
    assert paths["output_path"].is_file()
    written = json.loads(paths["output_path"].read_text(encoding="utf-8"))
    assert written["recommended_result"] == "NO_TRADE"
    assert written["research_state"] == "NO_OUTPUT"
    assert written["read_errors"] == []
    assert set(written["source_artifacts"]) == {
        "step1_degraded_decision",
        "step2_blocked_by_research_gate",
        "step3_blocked_by_upstream_gate",
        "step4_blocked_by_upstream_gate",
    }


def test_summarize_current_run_records_malformed_without_fabricating(tmp_path: Path) -> None:
    paths = summary_paths(tmp_path)
    # Malformed Step 1 decision; only a Step 2 block is valid.
    paths["step1_decision_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["step1_decision_path"].write_text("{not json", encoding="utf-8")
    write_json_file(paths["step2_block_path"], step2_block(state="NO_OUTPUT"))

    result = summarize_current_run(repo_root_path=tmp_path, **paths)

    assert result.read_errors  # malformed file recorded
    assert any("research_degraded_mode_decision.json" in err for err in result.read_errors)
    # Permission falls back to the valid Step 2 block; nothing fabricated.
    assert result.run_blocked is True
    assert result.research_state == "NO_OUTPUT"
    assert result.blocked_stages == ["step2"]


def test_summarize_current_run_no_artifacts_is_not_blocked(tmp_path: Path) -> None:
    paths = summary_paths(tmp_path)

    result = summarize_current_run(repo_root_path=tmp_path, **paths)

    assert result.run_blocked is False
    assert result.read_errors == []  # absent files are not errors
    assert paths["output_path"].is_file()


def test_standalone_run_status_with_no_observed_artifacts_remains_no_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from investment_orchestrator.cli import run_status

    base = tmp_path / "artifacts" / "current"
    monkeypatch.setattr(run_status, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        run_status,
        "step1_research_degraded_mode_decision_path",
        lambda: base / "step1_research" / "research_degraded_mode_decision.json",
    )
    monkeypatch.setattr(
        run_status,
        "step2_blocked_by_research_gate_path",
        lambda: base / "step2_decision_builder" / "step2_blocked_by_research_gate.json",
    )
    monkeypatch.setattr(
        run_status,
        "step3_blocked_by_upstream_gate_path",
        lambda: base / "step3_audit_engine" / "step3_blocked_by_upstream_gate.json",
    )
    monkeypatch.setattr(
        run_status,
        "step4_blocked_by_upstream_gate_path",
        lambda: base / "step4_order_compiler" / "step4_blocked_by_upstream_gate.json",
    )
    monkeypatch.setattr(
        run_status,
        "step4_blocked_by_final_execution_safety_gate_path",
        lambda: base
        / "step4_order_compiler"
        / "step4_blocked_by_final_execution_safety_gate.json",
    )
    monkeypatch.setattr(run_status.sys, "argv", ["run_status"])

    assert run_status.main() == 0

    written = json.loads((base / "run_summary.json").read_text(encoding="utf-8"))
    assert written["run_blocked"] is False
    assert written["terminal_stage"] is None
    assert written["terminal_reason_codes"] == []


def test_summarize_current_run_includes_final_gate_only_block(tmp_path: Path) -> None:
    paths = summary_paths(tmp_path)
    # Upstream all green (STRICT_FRESH permission, no upstream blocks), only the
    # final execution safety gate blocked Step 4.
    write_json_file(
        paths["step1_decision_path"],
        step1_decision(
            state="STRICT_FRESH",
            allowed_actions=["HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION"],
            blocked_actions=[],
        ),
    )
    write_json_file(paths["step4_final_safety_block_path"], final_safety_block())

    result = summarize_current_run(repo_root_path=tmp_path, **paths)

    written = json.loads(paths["output_path"].read_text(encoding="utf-8"))
    assert written["run_blocked"] is True
    assert written["recommended_result"] == "NO_TRADE"
    assert "step4" in written["blocked_stages"]
    assert written["is_llm_generated"] is False
    assert "step4_final_execution_safety_gate" in written["source_artifacts"]
    assert (
        written["source_artifacts"]["step4_final_execution_safety_gate"]
        == "artifacts/current/step4_order_compiler/step4_blocked_by_final_execution_safety_gate.json"
    )
    assert "step3_audited_packet.blocker_reasons is non-empty." in written["primary_blocker_reasons"]


def test_summarize_current_run_malformed_final_gate_records_read_error(tmp_path: Path) -> None:
    paths = summary_paths(tmp_path)
    write_json_file(
        paths["step1_decision_path"],
        step1_decision(state="STRICT_FRESH", blocked_actions=[]),
    )
    paths["step4_final_safety_block_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["step4_final_safety_block_path"].write_text("{not json", encoding="utf-8")

    result = summarize_current_run(repo_root_path=tmp_path, **paths)

    assert result.read_errors
    assert any(
        "step4_blocked_by_final_execution_safety_gate.json" in err for err in result.read_errors
    )


def test_step2_decision_only_state_summarizes_to_blocked_no_trade() -> None:
    # R2E.5b-6c: Step 2 decision-only is permitted, but the run still summarizes
    # as blocked / NO_TRADE because the order path stays closed.
    state = "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    result = build_blocked_run_summary(
        step1_decision=step1_decision(
            state=state,
            allowed_actions=["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"],
        ),
        step2_block=None,
        step3_block=None,
        step4_block=None,
    )

    assert result.run_blocked is True
    assert result.recommended_result == "NO_TRADE"
    assert result.research_state == state
    assert result.highest_severity_state == state
    assert result.allowed_actions == ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]
    assert "NEW_BUY" in result.blocked_actions
    assert "ORDER_COMPILATION" in result.blocked_actions


# --- terminal observability contract ----------------------------------------


def test_gate_terminal_observation_reports_current_degraded_parse_details() -> None:
    decision = step1_decision(
        state="DEGRADED_WITH_LAST_GOOD",
        allowed_actions=["HOLD", "NO_TRADE"],
    )
    decision["blocker_reasons"] = []
    decision["diagnostic_reason"] = "step1 parse failed before research_output.json was produced."
    decision["parse_error"] = (
        "Could not find RESEARCH_JSON_START/END or any balanced JSON object in Step 1 raw output."
    )
    observation = terminal_observation_from_research_gate(
        evaluate_step2_research_gate(decision)
    )

    result = build_blocked_run_summary(
        step1_decision=decision,
        step2_block=None,
        step3_block=None,
        step4_block=None,
        terminal_observation=observation,
    )

    assert result.run_blocked is True
    assert result.recommended_result == "NO_TRADE"
    assert result.terminal_stage == "step2_research_gate"
    assert result.stopped_before_stage == "step2_decision_builder"
    assert result.terminal_reason_codes == ["research_degraded_mode"]
    assert result.blocked_stages == []
    assert result.primary_blocker_reasons == [
        "research state DEGRADED_WITH_LAST_GOOD is not STRICT_FRESH.",
        "research permission does not allow required actions: NEW_BUY, ORDER_COMPILATION",
    ]
    assert result.terminal_diagnostics == [
        "step1 parse failed before research_output.json was produced.",
        "Could not find RESEARCH_JSON_START/END or any balanced JSON object in Step 1 raw output.",
    ]


def test_gate_reason_order_is_preserved_and_deduplicated() -> None:
    decision = step1_decision(state="NO_OUTPUT")
    decision["blocker_reasons"] = ["first deterministic reason", "first deterministic reason"]
    observation = terminal_observation_from_research_gate(
        evaluate_step2_research_gate(decision)
    )

    result = build_blocked_run_summary(
        step1_decision=decision,
        step2_block=None,
        step3_block=None,
        step4_block=None,
        terminal_observation=observation,
    )

    assert result.primary_blocker_reasons == [
        "first deterministic reason",
        "research state NO_OUTPUT is not STRICT_FRESH.",
        "research permission does not allow required actions: NEW_BUY, ORDER_COMPILATION",
    ]


def test_terminal_observation_is_additive_to_unchanged_summary_actions() -> None:
    decision = step1_decision(
        state="DEGRADED_WITH_LAST_GOOD",
        allowed_actions=["HOLD", "NO_TRADE"],
    )
    decision["blocker_reasons"] = []
    baseline = build_blocked_run_summary(
        step1_decision=decision,
        step2_block=None,
        step3_block=None,
        step4_block=None,
    )
    observed = build_blocked_run_summary(
        step1_decision=decision,
        step2_block=None,
        step3_block=None,
        step4_block=None,
        terminal_observation=terminal_observation_from_research_gate(
            evaluate_step2_research_gate(decision)
        ),
    )

    for field_name in (
        "run_blocked",
        "recommended_result",
        "manual_review_required",
        "highest_severity_state",
        "research_state",
        "research_availability",
        "allowed_actions",
        "blocked_actions",
        "blocked_stages",
    ):
        assert getattr(observed, field_name) == getattr(baseline, field_name)
    assert baseline.primary_blocker_reasons == []
    assert observed.primary_blocker_reasons


def test_missing_optional_step1_diagnostics_preserve_gate_reason() -> None:
    decision = step1_decision(state="NO_OUTPUT")
    observation = terminal_observation_from_research_gate(
        evaluate_step2_research_gate(decision)
    )

    result = build_blocked_run_summary(
        step1_decision=decision,
        step2_block=None,
        step3_block=None,
        step4_block=None,
        terminal_observation=observation,
    )

    assert result.terminal_reason_codes == ["research_degraded_mode"]
    assert result.primary_blocker_reasons
    assert result.terminal_diagnostics == []


def test_malformed_step1_diagnostic_is_not_copied_or_stringified() -> None:
    class HostileDiagnostic(str):
        def __str__(self) -> str:
            raise AssertionError("must not stringify malformed diagnostic")

    decision = step1_decision(state="NO_OUTPUT")
    decision["diagnostic_reason"] = HostileDiagnostic("untrusted")
    observation = terminal_observation_from_research_gate(
        evaluate_step2_research_gate(decision)
    )

    result = build_blocked_run_summary(
        step1_decision=decision,
        step2_block=None,
        step3_block=None,
        step4_block=None,
        terminal_observation=observation,
    )

    assert "terminal_diagnostic_invalid" in result.terminal_reason_codes
    assert result.terminal_diagnostics == [
        "step1 diagnostic_reason is not a non-empty string."
    ]


def test_conflicting_terminal_observation_and_explicit_block_is_reported() -> None:
    decision = step1_decision(state="NO_OUTPUT")
    observation = terminal_observation_from_research_gate(
        evaluate_step2_research_gate(decision)
    )

    result = build_blocked_run_summary(
        step1_decision=decision,
        step2_block=step2_block(state="NO_OUTPUT"),
        step3_block=None,
        step4_block=None,
        terminal_observation=observation,
    )

    assert result.terminal_reason_codes == [
        "research_degraded_mode",
        "terminal_source_conflict",
    ]
    assert result.terminal_diagnostics == [
        "weekly terminal observation conflicts with explicit downstream block artifacts."
    ]
    assert result.blocked_stages == ["step2"]


def test_explicit_final_safety_block_has_its_own_terminal_location() -> None:
    result = build_blocked_run_summary(
        step1_decision=step1_decision(
            state="STRICT_FRESH",
            allowed_actions=["HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION"],
            blocked_actions=[],
        ),
        step2_block=None,
        step3_block=None,
        step4_block=None,
        step4_final_safety_block=final_safety_block(),
    )

    assert result.terminal_stage == "step4_final_execution_safety_gate"
    assert result.stopped_before_stage == "order_compilation"
    assert result.terminal_reason_codes == ["final_execution_safety_gate_blocked"]
    assert result.blocked_stages == ["step4"]


def test_malformed_explicit_block_is_not_reported_as_a_valid_blocked_stage(
    tmp_path: Path,
) -> None:
    paths = summary_paths(tmp_path)
    write_json_file(
        paths["step1_decision_path"],
        step1_decision(
            state="STRICT_FRESH",
            allowed_actions=["HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION"],
            blocked_actions=[],
        ),
    )
    write_json_file(paths["step2_block_path"], {"blocked": True, "reason": 7})

    result = summarize_current_run(repo_root_path=tmp_path, **paths)

    assert result.blocked_stages == []
    assert result.run_blocked is True
    assert result.terminal_stage is None
    assert result.terminal_reason_codes == ["summary_source_invalid"]
    assert result.terminal_diagnostics == ["step2 explicit block artifact is invalid."]
