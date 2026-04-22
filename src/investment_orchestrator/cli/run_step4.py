"""CLI entrypoint for the manual Step 4 workflow."""

from __future__ import annotations

import argparse
import sys

from investment_orchestrator.workflow.step4_order_compiler import (
    parse_step4_output,
    render_step4_prompt,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the Step 4 CLI."""
    parser = argparse.ArgumentParser(description="Run the minimal manual Step 4 workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("render", help="Render Step 4 prompt and prepare raw_output.txt")
    subparsers.add_parser(
        "parse",
        help="Parse raw_output.txt into template4_orders.txt, order_state_export.txt, and exec_summary.txt",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "render":
            result = render_step4_prompt()
            print(result["prompt_path"])
            return 0

        if args.command == "parse":
            result = parse_step4_output()
            print(result["template4_orders_path"])
            return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
