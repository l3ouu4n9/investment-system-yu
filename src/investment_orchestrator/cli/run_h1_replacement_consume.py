"""Foreground CLI that consumes one report-only H1 replacement handoff.

The operator supplies only the expected prepared-handoff identity.  The raw
response is read from the code-owned
``artifacts/current/h1_replacement/h1_response.raw`` leaf that the operator
placed by hand; there is deliberately no response-path argument, no provider or
model option, no network option, and no availability activation option.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys

from investment_orchestrator.workflow.h1_replacement_handoff import (
    H1ReplacementHandoffError,
    consume_h1_replacement_handoff,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=(
            "python -m "
            "investment_orchestrator.cli.run_h1_replacement_consume"
        ),
        description=(
            "Consume one prepared H1 replacement handoff and its manually "
            "placed raw response, publishing only the H1 mapping report."
        ),
    )
    parser.add_argument(
        "--expected-prepared-handoff-identity-sha256", required=True
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Consume one prepared handoff and print only stable identities."""
    args = _parser().parse_args(argv)
    try:
        result = consume_h1_replacement_handoff(
            expected_prepared_handoff_identity_sha256=(
                args.expected_prepared_handoff_identity_sha256
            ),
        )
    except H1ReplacementHandoffError as exc:
        sys.stderr.write(
            f"H1_HANDOFF_CONSUME_FAILED code={exc.code.value} "
            f"owner_reason_codes={','.join(exc.owner_reason_codes)}\n"
        )
        return 3

    sys.stdout.write(
        f"workflow_status={result.workflow_status}\n"
        "prepared_handoff_identity_sha256="
        f"{result.prepared_handoff_identity_sha256}\n"
        "mapping_report_identity_sha256="
        f"{result.mapping_report_identity_sha256}\n"
        "portfolio_snapshot_presence="
        f"{result.portfolio_snapshot_presence}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
