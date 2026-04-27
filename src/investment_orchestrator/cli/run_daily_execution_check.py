"""CLI entrypoint for the manual Daily Execution Check workflow."""

from __future__ import annotations

import argparse
import sys

from investment_orchestrator.workflow.daily_execution_check import (
    parse_daily_execution_check_output,
    render_daily_execution_check_prompt,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the Daily Execution Check CLI."""
    parser = argparse.ArgumentParser(description="Run the manual Daily Execution Check workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser(
        "render",
        help="Render Daily Execution Check prompt and prepare raw_output.txt",
    )
    render_parser.add_argument("--date", help="Daily check date in YYYY-MM-DD format. Defaults to local date.")
    render_parser.add_argument(
        "--generate-market-data",
        action="store_true",
        help="Attempt to generate daily market data before rendering. Failure does not block prompt rendering.",
    )

    parse_parser = subparsers.add_parser(
        "parse",
        help="Parse raw_output.txt into daily_execution_actions.json",
    )
    parse_parser.add_argument("--date", help="Daily check date in YYYY-MM-DD format. Defaults to local date.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "render":
            result = render_daily_execution_check_prompt(
                as_of_date=args.date,
                generate_market_data=args.generate_market_data,
            )
            print(result["prompt_path"])
            return 0

        if args.command == "parse":
            result = parse_daily_execution_check_output(as_of_date=args.date)
            print(result["daily_execution_actions_path"])
            return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
