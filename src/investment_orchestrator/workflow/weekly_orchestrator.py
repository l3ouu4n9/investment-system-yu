"""Weekly-level controlled orchestration (PR H1).

This is the top-level weekly entrypoint that routes a weekly run based on the
deterministic Step 1 degraded-mode decision:

- ``STRICT_FRESH`` with the actionable order-generating permission -> proceed to
  the existing manual Step 2 render path (which independently re-enforces the
  same fail-closed gate). The weekly run is *not* complete here; the operator
  still pastes the LLM output and runs Step 2 parse / Step 3 / Step 4.
- Any degraded / blocked / missing / malformed permission (e.g.
  ``DEGRADED_WITH_LAST_GOOD``, ``STRICT_STALE``, ``DEGRADED_NO_RESEARCH``,
  ``INVALID_CONTRACT``, ``NO_OUTPUT``, ``MANUAL_REVIEW_REQUIRED``) -> a
  deterministic, controlled ``NO_TRADE`` terminal outcome. The weekly run does
  **not** enter the Step 2 LLM path and does **not** touch Step 3/4. It writes
  the deterministic ``run_summary.json`` and a ``weekly_outcome.json`` terminal
  artifact and finishes as a *safe completion*.

This module changes no gate policy. The actionable-vs-terminal split *is* the
existing Step 2 research gate evaluator's ``.allowed`` result; this module only
reuses it and never widens it. It produces deterministic operational artifacts
only -- it never invokes an LLM and never fabricates a Step 2 decision packet,
Step 3 audit packet, or Step 4 order output. ``is_llm_generated`` is always
``False``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import write_json
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.state.blocked_run_summary import (
    RUN_SUMMARY_FILENAME,
    summarize_current_run,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    ResearchDegradedModeGateResult,
    load_and_evaluate_step2_research_gate,
)
from investment_orchestrator.workflow.step1_research import (
    step1_research_degraded_mode_decision_path,
)
from investment_orchestrator.workflow.step2_decision_builder import (
    render_step2_prompt,
    step2_blocked_by_research_gate_path,
    step2_prompt_path,
)
from investment_orchestrator.workflow.step3_audit_engine import (
    step3_blocked_by_upstream_gate_path,
)
from investment_orchestrator.workflow.step4_order_compiler import (
    step4_blocked_by_final_execution_safety_gate_path,
    step4_blocked_by_upstream_gate_path,
)


WEEKLY_OUTCOME_FILENAME = "weekly_outcome.json"

TERMINAL_NO_TRADE = "NO_TRADE"
DEGRADED_TERMINAL_REASON = "research_degraded_mode"
ACTIONABLE_REASON = "research_strict_fresh_actionable"

# Order-generating actions that a controlled NO_TRADE weekly run must never
# enter; surfaced in the terminal artifact for operator clarity.
_ORDER_GENERATING_ACTIONS = ("NEW_BUY", "ORDER_COMPILATION")


@dataclass(frozen=True)
class WeeklyRunResult:
    """Deterministic result of a weekly-level orchestration run."""

    actionable: bool
    weekly_completed: bool
    terminal_result: str | None
    research_state: str
    manual_review_required: bool
    allowed_actions: list[str]
    blocked_actions: list[str]
    blocker_reasons: list[str]
    weekly_outcome_path: Path
    run_summary_path: Path | None = None
    step2_prompt_path: Path | None = None
    source_artifacts: dict[str, str] = field(default_factory=dict)
    # 0 = controlled completion (actionable proceed OR controlled NO_TRADE
    # terminal). A non-zero code is reserved for genuine operational errors and
    # is raised as an exception by the CLI, never set here.
    exit_code: int = 0


def weekly_outcome_path(repo_root_path: Path | None = None) -> Path:
    """Return the deterministic weekly terminal-outcome artifact path."""
    root = repo_root_path if repo_root_path is not None else repo_root()
    return root / "artifacts" / "current" / WEEKLY_OUTCOME_FILENAME


def run_weekly(
    *,
    decision_path: Path | None = None,
    step2_block_path: Path | None = None,
    step3_block_path: Path | None = None,
    step4_block_path: Path | None = None,
    step4_final_safety_block_path: Path | None = None,
    run_summary_output_path: Path | None = None,
    weekly_outcome_output_path: Path | None = None,
    repo_root_path: Path | None = None,
    render_step2_when_actionable: bool = True,
) -> WeeklyRunResult:
    """Route the weekly run from the Step 1 degraded-mode decision.

    Reuses the existing Step 2 research gate evaluator as the single source of
    truth for whether the actionable path is permitted. Never widens that
    policy. On the not-allowed branch this is a deterministic controlled
    ``NO_TRADE`` terminal outcome that never enters the Step 2 LLM path and
    never fabricates downstream decision/audit/order artifacts.
    """
    root = repo_root_path if repo_root_path is not None else repo_root()
    decision_path = decision_path or step1_research_degraded_mode_decision_path()
    weekly_outcome_output_path = weekly_outcome_output_path or weekly_outcome_path(root)

    gate = load_and_evaluate_step2_research_gate(decision_path)

    if gate.allowed:
        return _proceed_actionable(
            gate=gate,
            decision_path=decision_path,
            weekly_outcome_output_path=weekly_outcome_output_path,
            repo_root_path=root,
            render_step2_when_actionable=render_step2_when_actionable,
        )

    return _terminal_no_trade(
        gate=gate,
        decision_path=decision_path,
        step2_block_path=step2_block_path or step2_blocked_by_research_gate_path(),
        step3_block_path=step3_block_path or step3_blocked_by_upstream_gate_path(),
        step4_block_path=step4_block_path or step4_blocked_by_upstream_gate_path(),
        step4_final_safety_block_path=(
            step4_final_safety_block_path
            or step4_blocked_by_final_execution_safety_gate_path()
        ),
        run_summary_output_path=(
            run_summary_output_path or (root / "artifacts" / "current" / RUN_SUMMARY_FILENAME)
        ),
        weekly_outcome_output_path=weekly_outcome_output_path,
        repo_root_path=root,
    )


def _proceed_actionable(
    *,
    gate: ResearchDegradedModeGateResult,
    decision_path: Path,
    weekly_outcome_output_path: Path,
    repo_root_path: Path,
    render_step2_when_actionable: bool,
) -> WeeklyRunResult:
    """STRICT_FRESH actionable path: proceed to the existing Step 2 render.

    The weekly run is intentionally *not* marked complete here: the manual Step
    2 paste/parse and Step 3/4 still follow. No terminal NO_TRADE is written.
    """
    source_artifacts: dict[str, str] = {
        "research_degraded_mode_decision": _display_path(decision_path, repo_root_path),
    }
    rendered_prompt_path: Path | None = None

    if render_step2_when_actionable:
        # The existing Step 2 render independently re-enforces the same
        # fail-closed gate; this is a deliberate defense-in-depth re-check, not
        # a bypass. Any failure here is a genuine error and propagates.
        render_step2_prompt()
        rendered_prompt_path = step2_prompt_path()
        source_artifacts["step2_prompt"] = _display_path(rendered_prompt_path, repo_root_path)

    payload = {
        "weekly_completed": False,
        "actionable": True,
        "terminal_result": None,
        "reason": ACTIONABLE_REASON,
        "research_state": gate.state,
        "allowed_actions": list(gate.allowed_actions),
        "blocked_actions": list(gate.blocked_actions),
        "manual_review_required": gate.manual_review_required,
        "blocker_reasons": list(gate.blocker_reasons),
        "next_step": "run_step2 parse -> run_step3 -> run_step4 (manual paste flow)",
        "is_llm_generated": False,
        "report_only": False,
        "source_artifacts": source_artifacts,
    }
    write_json(weekly_outcome_output_path, payload)

    return WeeklyRunResult(
        actionable=True,
        weekly_completed=False,
        terminal_result=None,
        research_state=gate.state,
        manual_review_required=gate.manual_review_required,
        allowed_actions=list(gate.allowed_actions),
        blocked_actions=list(gate.blocked_actions),
        blocker_reasons=list(gate.blocker_reasons),
        weekly_outcome_path=weekly_outcome_output_path,
        run_summary_path=None,
        step2_prompt_path=rendered_prompt_path,
        source_artifacts=source_artifacts,
        exit_code=0,
    )


def _terminal_no_trade(
    *,
    gate: ResearchDegradedModeGateResult,
    decision_path: Path,
    step2_block_path: Path,
    step3_block_path: Path,
    step4_block_path: Path,
    step4_final_safety_block_path: Path,
    run_summary_output_path: Path,
    weekly_outcome_output_path: Path,
    repo_root_path: Path,
) -> WeeklyRunResult:
    """Controlled NO_TRADE terminal: write run_summary + weekly_outcome, exit 0.

    Does not enter the Step 2 LLM path and does not call Step 3/4. The only
    artifacts written are the deterministic ``run_summary.json`` (via the
    existing blocked-run-summary builder) and ``weekly_outcome.json``.
    """
    # Deterministic operational run summary (reads existing artifacts only).
    summarize_current_run(
        step1_decision_path=decision_path,
        step2_block_path=step2_block_path,
        step3_block_path=step3_block_path,
        step4_block_path=step4_block_path,
        step4_final_safety_block_path=step4_final_safety_block_path,
        output_path=run_summary_output_path,
        repo_root_path=repo_root_path,
    )

    blocked_actions = _with_order_actions_blocked(gate.allowed_actions, gate.blocked_actions)

    payload = {
        "weekly_completed": True,
        "actionable": False,
        "terminal_result": TERMINAL_NO_TRADE,
        "reason": DEGRADED_TERMINAL_REASON,
        "research_state": gate.state,
        "allowed_actions": list(gate.allowed_actions),
        "blocked_actions": blocked_actions,
        "manual_review_required": gate.manual_review_required,
        "blocker_reasons": list(gate.blocker_reasons),
        "next_step": "inspect run_summary.json + weekly_outcome.json; HOLD / NO_TRADE",
        "is_llm_generated": False,
        "report_only": False,
        "source_artifacts": {
            "research_degraded_mode_decision": _display_path(decision_path, repo_root_path),
            "run_summary": _display_path(run_summary_output_path, repo_root_path),
        },
    }
    write_json(weekly_outcome_output_path, payload)

    return WeeklyRunResult(
        actionable=False,
        weekly_completed=True,
        terminal_result=TERMINAL_NO_TRADE,
        research_state=gate.state,
        manual_review_required=gate.manual_review_required,
        allowed_actions=list(gate.allowed_actions),
        blocked_actions=blocked_actions,
        blocker_reasons=list(gate.blocker_reasons),
        weekly_outcome_path=weekly_outcome_output_path,
        run_summary_path=run_summary_output_path,
        step2_prompt_path=None,
        source_artifacts=dict(payload["source_artifacts"]),
        exit_code=0,
    )


def _with_order_actions_blocked(
    allowed_actions: list[str],
    blocked_actions: list[str],
) -> list[str]:
    """Ensure order-generating actions are listed as blocked on a NO_TRADE run."""
    merged = list(blocked_actions)
    for action in _ORDER_GENERATING_ACTIONS:
        if action not in allowed_actions and action not in merged:
            merged.append(action)
    return merged


def _display_path(path: Path, repo_root_path: Path | None) -> str:
    if repo_root_path is not None:
        try:
            return str(path.relative_to(repo_root_path))
        except ValueError:
            pass
    return str(path)
