"""CLI entrypoint for V1 BUY review-only manual-entry artifact generation."""

from __future__ import annotations

import argparse
import sys

from investment_orchestrator.workflow.p8_v1_review_order_publication import (
    publish_h1_v1_review_order,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the V1 BUY review CLI."""
    parser = argparse.ArgumentParser(
        description="Generate one fresh review-only V1 BUY artifact for human manual broker entry. (No broker submission)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "publish",
        help="Evaluate a fresh V1 BUY order proposal and publish a review-only artifact. Does NOT submit to broker.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "publish":
            result = publish_h1_v1_review_order()
            print(result.immutable_path)
            return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
