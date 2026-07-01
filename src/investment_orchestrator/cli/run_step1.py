"""CLI entrypoint for the manual Step 1 workflow."""

from __future__ import annotations

import argparse
import sys

from investment_orchestrator.workflow.step1_research import (
    compile_step1_research_handoff,
    parse_step1_analyst_memo_output,
    parse_step1_output,
    render_step1_analyst_memo_prompt,
    render_step1_prompt,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the Step 1 CLI."""
    parser = argparse.ArgumentParser(description="Run the minimal manual Step 1 workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("render", help="Render Step 1 prompt and prepare raw_output.txt")
    subparsers.add_parser("parse", help="Parse raw_output.txt into research_output.json")
    subparsers.add_parser(
        "analyst-memo-render",
        help="Render the small Step 1B analyst-memo prompt from the evidence packet (report-only)",
    )
    subparsers.add_parser(
        "analyst-memo-parse",
        help="Parse analyst_memo_raw_output.txt into analyst_memo.json (report-only)",
    )
    subparsers.add_parser(
        "compile-handoff",
        help="Compile evidence_packet (+ optional analyst_memo) into a strict handoff candidate (report-only)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "render":
            result = render_step1_prompt()
            print(result["prompt_path"])
            return 0

        if args.command == "parse":
            result = parse_step1_output()
            print(result["research_output_path"])
            return 0

        if args.command == "analyst-memo-render":
            result = render_step1_analyst_memo_prompt()
            print(result["analyst_memo_prompt_path"])
            return 0

        if args.command == "analyst-memo-parse":
            result = parse_step1_analyst_memo_output()
            print(result["validation_path"])
            return 0

        if args.command == "compile-handoff":
            result = compile_step1_research_handoff()
            print(result["candidate_path"])
            return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
