"""CLI entrypoint for the manual Step 1 workflow."""

from __future__ import annotations

import argparse
import os
import sys

from investment_orchestrator.workflow.step1_research import (
    compile_step1_research_handoff,
    parse_step1_analyst_memo_output,
    parse_step1_output,
    render_step1_analyst_memo_prompt,
    render_step1_prompt,
)


_COMMITTED_DISPLAY_FAILURE = object()


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
    subparsers.add_parser(
        "replacement-render",
        help="Build the immutable R2F-1a Step 1A render observation (report-only)",
    )
    return parser


def _display_committed_replacement_result_noexcept(value: str) -> object | None:
    """Best-effort observability after R2F-1a publication has committed.

    This is deliberately replacement-render-only.  A write may buffer
    successfully and fail only when Python flushes stdout during interpreter
    shutdown, so the write *and* flush must occur in this protected boundary.
    A failed stream can be permanently broken or impossible to redirect safely;
    report that committed-only condition to the real process entrypoint instead
    of relying on fallible stream-silencing operations.
    """
    try:
        sys.stdout.write(f"{value}\n")
        sys.stdout.flush()
    except BaseException:  # noqa: BLE001 - display cannot invalidate publication
        return _COMMITTED_DISPLAY_FAILURE
    return None


def main() -> int | object:
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

        if args.command == "replacement-render":
            # Keep the legacy Step 1 import surface unchanged unless the
            # explicitly report-only R2F-1a command is selected.
            from investment_orchestrator.research.replacement_observation import replacement_render

            result = replacement_render()
            output_text = result["cli_output"]
            if _display_committed_replacement_result_noexcept(output_text) is _COMMITTED_DISPLAY_FAILURE:
                return _COMMITTED_DISPLAY_FAILURE
            return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


def _run_process_entrypoint() -> None:
    """Translate only a committed replacement display failure into exit zero."""
    result = main()
    if result is _COMMITTED_DISPLAY_FAILURE:
        # replacement_render has returned: its marker commit and no-throw
        # publication cleanup are complete.  Skip interpreter shutdown only to
        # prevent a permanently failed stdout object from flushing again.
        os._exit(0)
    raise SystemExit(result)


if __name__ == "__main__":
    _run_process_entrypoint()
