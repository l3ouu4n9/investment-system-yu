"""CLI: weekly-level controlled orchestration (PR H1).

Top-level weekly entrypoint. Routes the weekly run from the deterministic Step 1
degraded-mode decision:

- ``STRICT_FRESH`` + actionable permission -> proceed to the existing manual
  Step 2 render path (which re-enforces the same fail-closed gate).
- Any degraded / blocked / missing / malformed permission -> a controlled,
  deterministic ``NO_TRADE`` terminal outcome (writes ``run_summary.json`` and
  ``weekly_outcome.json``), and finishes as a *safe completion*.

Exit behavior (deliberate):

- This weekly-level command exits ``0`` for a controlled ``NO_TRADE`` terminal
  outcome -- at the weekly level a fail-closed degraded-research gate is a
  legitimate no-trade terminal result, not a broken run. Automation MUST read
  ``weekly_outcome.json`` (``terminal_result`` / ``weekly_completed``) rather
  than inferring "tradeable" from the exit code.
- Step-level commands (e.g. ``run_step2 render``) deliberately keep exiting
  non-zero when a gate blocks them; that fail-closed behavior is unchanged.
- A non-zero exit from this command means a genuine operational error (could not
  read inputs, unexpected failure), never a controlled NO_TRADE.

This command changes no gate policy, never invokes an LLM, and never fabricates
a decision / audit / order artifact.
"""

from __future__ import annotations

import argparse
import sys

from investment_orchestrator.workflow.weekly_orchestrator import run_weekly


def build_parser() -> argparse.ArgumentParser:
    """Build the weekly orchestration CLI."""
    parser = argparse.ArgumentParser(
        description=(
            "Run the weekly-level controlled orchestration: proceed to Step 2 when "
            "research is STRICT_FRESH/actionable, otherwise produce a deterministic "
            "NO_TRADE terminal outcome."
        )
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run"],
        help="Run the weekly orchestration (default).",
    )
    parser.add_argument(
        "--no-render-step2",
        action="store_true",
        help=(
            "On the actionable (STRICT_FRESH) path, do not render the Step 2 prompt; "
            "only report that the run may proceed to the manual Step 2 path."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        result = run_weekly(render_step2_when_actionable=not args.no_render_step2)
    except Exception as exc:  # noqa: BLE001 - genuine operational error -> exit 1
        print(str(exc), file=sys.stderr)
        return 1

    print(str(result.weekly_outcome_path))
    if result.actionable:
        print(
            "weekly_actionable=true "
            f"research_state={result.research_state} "
            f"weekly_completed={result.weekly_completed} "
            f"terminal_result={result.terminal_result} "
            "next=run_step2"
        )
    else:
        print(
            "weekly_actionable=false "
            f"terminal_result={result.terminal_result} "
            f"reason=research_degraded_mode "
            f"research_state={result.research_state} "
            f"manual_review_required={result.manual_review_required} "
            f"run_summary={result.run_summary_path}"
        )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
