"""Foreground CLI for one report-only archived H2c case consume.

This adapter delegates one prepared case to the existing archived consume
owner and exits.  Response placement remains a manual operator action.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 import (
    H2cConsumeError,
    consume_h2c_persisted_case_from_archives,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=(
            "python -m "
            "investment_orchestrator.cli.run_mmi_h2c_consume_archived"
        ),
        description=(
            "Consume one prepared H2c case from its authenticated archives "
            "and operator-supplied response files."
        ),
    )
    parser.add_argument("--case-root", required=True, type=Path)
    parser.add_argument(
        "--expected-prepared-case-identity-sha256", required=True
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Consume one archived case and print only stable result identities."""
    args = _parser().parse_args(argv)
    try:
        result = consume_h2c_persisted_case_from_archives(
            case_root=args.case_root,
            expected_prepared_case_identity_sha256=(
                args.expected_prepared_case_identity_sha256
            ),
        )
    except H2cConsumeError as exc:
        sys.stderr.write(
            "H2C_CONSUME_FAILED "
            f"code={exc.code.value} "
            f"failure_class={exc.failure_class.value}\n"
        )
        return 3

    if result.workflow_status != "COMPLETED":
        raise RuntimeError("archived H2c consume did not complete")

    sys.stdout.write(
        f"workflow_status={result.workflow_status}\n"
        "prepared_case_identity_sha256="
        f"{args.expected_prepared_case_identity_sha256}\n"
        "case_evidence_bundle_identity_sha256="
        f"{result.case_evidence_bundle_identity_sha256}\n"
        "comparison_report_identity_sha256="
        f"{result.comparison_report_identity_sha256}\n"
        "receipt_identity_sha256="
        f"{result.receipt_identity_sha256}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
