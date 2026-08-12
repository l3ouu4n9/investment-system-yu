"""Foreground CLI that prepares one report-only H1 replacement handoff.

Standard output carries the exact grounded prompt bytes and nothing else, so
an operator can pipe or copy them verbatim into the LLM of their choosing.
Every operational message goes to standard error.  This CLI submits nothing,
retrieves nothing, and exposes no provider, model, network, response-path, or
availability option.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys

from investment_orchestrator.workflow.h1_replacement_handoff import (
    H1ReplacementHandoffError,
    prepare_h1_replacement_handoff,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=(
            "python -m "
            "investment_orchestrator.cli.run_h1_replacement_prepare"
        ),
        description=(
            "Prepare one H1 replacement handoff, publish its prepared "
            "artifact, and emit the exact prompt for manual submission."
        ),
    )
    parser.add_argument("--strategy-settings-expected-sha256", required=True)
    portfolio = parser.add_mutually_exclusive_group(required=True)
    portfolio.add_argument("--portfolio-snapshot-expected-sha256")
    portfolio.add_argument(
        "--portfolio-snapshot-absent",
        action="store_true",
        help=(
            "Declare the code-owned portfolio snapshot absent; preparation "
            "then proves that absence and fails closed otherwise."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Prepare one handoff, then write only its prompt bytes to stdout."""
    args = _parser().parse_args(argv)
    try:
        result = prepare_h1_replacement_handoff(
            strategy_settings_expected_sha256=(
                args.strategy_settings_expected_sha256
            ),
            portfolio_snapshot_expected_sha256=(
                args.portfolio_snapshot_expected_sha256
            ),
            portfolio_snapshot_absent=bool(args.portfolio_snapshot_absent),
        )
    except H1ReplacementHandoffError as exc:
        sys.stderr.write(
            f"H1_HANDOFF_PREPARE_FAILED code={exc.code.value} "
            f"owner_reason_codes={','.join(exc.owner_reason_codes)}\n"
        )
        return 3

    sys.stdout.buffer.write(result.prompt_text.encode("utf-8"))
    sys.stdout.buffer.flush()
    sys.stderr.write(
        f"workflow_status={result.workflow_status}\n"
        "prepared_handoff_identity_sha256="
        f"{result.prepared_handoff_identity_sha256}\n"
        "portfolio_snapshot_presence="
        f"{result.portfolio_snapshot_presence}\n"
        "next_manual_step=submit the exact stdout prompt to an LLM, save the "
        "exact raw response bytes to "
        "artifacts/current/h1_replacement/h1_response.raw, then run "
        "run_h1_replacement_consume with the identity above\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
