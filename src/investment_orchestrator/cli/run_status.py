"""CLI: deterministic run-level blocked / no-trade summary (PR UX1).

Aggregates the existing Step 1 degraded-mode decision and the Step 2/3/4
gate/guard blocked artifacts into a single ``artifacts/current/run_summary.json``
so an operator can see the run's NO_TRADE / blocked / manual-review status at a
glance. This reads existing artifacts only; it changes no gate behavior and
produces no LLM/decision/order output.
"""

from __future__ import annotations

import argparse
import sys

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.state.blocked_run_summary import (
    RUN_SUMMARY_FILENAME,
    summarize_current_run,
    terminal_observation_from_research_gate,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    load_and_evaluate_step2_research_gate,
)
from investment_orchestrator.workflow.step1_research import (
    step1_research_degraded_mode_decision_path,
)
from investment_orchestrator.workflow.step2_decision_builder import (
    step2_blocked_by_research_gate_path,
)
from investment_orchestrator.workflow.step3_audit_engine import (
    step3_blocked_by_upstream_gate_path,
)
from investment_orchestrator.workflow.step4_order_compiler import (
    step4_blocked_by_final_execution_safety_gate_path,
    step4_blocked_by_upstream_gate_path,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the run-status CLI."""
    parser = argparse.ArgumentParser(
        description="Summarize the current run's blocked / no-trade status into run_summary.json."
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="summarize",
        choices=["summarize"],
        help="Summarize the current run (default).",
    )
    return parser


def main() -> int:
    build_parser().parse_args()

    try:
        decision_path = step1_research_degraded_mode_decision_path()
        source_paths = (
            decision_path,
            step2_blocked_by_research_gate_path(),
            step3_blocked_by_upstream_gate_path(),
            step4_blocked_by_upstream_gate_path(),
            step4_blocked_by_final_execution_safety_gate_path(),
        )
        # Preserve the existing standalone "no observed run" result.  A
        # present Step 1 decision is the only source from which this standalone
        # command may project a research-gate terminal; downstream block files
        # retain their own earlier-stage precedence when Step 1 is unavailable.
        terminal_observation = None
        if decision_path.exists():
            gate = load_and_evaluate_step2_research_gate(decision_path)
            terminal_observation = terminal_observation_from_research_gate(gate)
        output_path = repo_root() / "artifacts" / "current" / RUN_SUMMARY_FILENAME
        result = summarize_current_run(
            step1_decision_path=decision_path,
            step2_block_path=source_paths[1],
            step3_block_path=source_paths[2],
            step4_block_path=source_paths[3],
            step4_final_safety_block_path=source_paths[4],
            output_path=output_path,
            repo_root_path=repo_root(),
            terminal_observation=terminal_observation,
        )
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    print(str(output_path))
    print(
        f"run_blocked={result.run_blocked} "
        f"recommended_result={result.recommended_result} "
        f"research_state={result.research_state} "
        f"manual_review_required={result.manual_review_required} "
        f"blocked_stages={result.blocked_stages}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
