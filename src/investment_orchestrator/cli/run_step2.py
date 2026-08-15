"""CLI entrypoint for the manual Step 2 workflow."""

from __future__ import annotations

import argparse
import sys

from investment_orchestrator.workflow.step2_decision_builder import (
    parse_step2_output,
    render_step2_prompt,
    step2_h1_capture_receipt_path,
    step2_prompt_path,
    step2_raw_output_path,
    step2_render_commitment_path,
)
from investment_orchestrator.workflow.step2_h1_provenance import (
    capture_h1_response,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the Step 2 CLI."""
    parser = argparse.ArgumentParser(description="Run the minimal manual Step 2 workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("render", help="Render Step 2 prompt and prepare raw_output.txt")
    subparsers.add_parser("parse", help="Parse raw_output.txt into template2_output.txt and decision_packet.json")
    subparsers.add_parser("capture", help="Capture operator-supplied Step 2 H1 raw response")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "render":
            result = render_step2_prompt()
            print(result["prompt_path"])
            return 0

        if args.command == "parse":
            result = parse_step2_output()
            print(result["decision_packet_path"])
            return 0

        if args.command == "capture":
            receipt_path = step2_h1_capture_receipt_path()
            capture_h1_response(
                commitment_path=step2_render_commitment_path(),
                prompt_path=step2_prompt_path(),
                raw_output_path=step2_raw_output_path(),
                receipt_path=receipt_path,
            )
            print(str(receipt_path))
            return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
