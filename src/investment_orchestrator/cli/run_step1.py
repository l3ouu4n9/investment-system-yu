"""CLI entrypoint for the manual Step 1 workflow."""

from __future__ import annotations

import argparse
import sys

from investment_orchestrator.workflow.step1_research import parse_step1_output, render_step1_prompt


def build_parser() -> argparse.ArgumentParser:
    """Build the Step 1 CLI."""
    parser = argparse.ArgumentParser(description="Run the minimal manual Step 1 workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("render", help="Render Step 1 prompt and prepare raw_output.txt")
    subparsers.add_parser("parse", help="Parse raw_output.txt into research_output.json")
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
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
