"""Explicit foreground CLI for one report-only H2c Phase A case preparation.

This wraps the existing dormant ``prepare_h2c_persisted_case`` engine only.
It prepares one persisted case and exits; it never reads a response, never
builds Phase B artifacts, and never resumes, repairs, or reuses a case.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1 import (
    H2cPrepareError,
    prepare_h2c_persisted_case,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m investment_orchestrator.cli.run_mmi_h2c_prepare",
        description=(
            "Prepare one persisted H2c Phase A case and exit without "
            "waiting for either operator response."
        ),
    )
    parser.add_argument("--strategy-settings-expected-sha256", required=True)
    parser.add_argument("--portfolio-snapshot-expected-sha256", required=True)
    parser.add_argument("--case-root", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse explicit arguments, prepare one case, and print only its identity."""
    args = _parser().parse_args(argv)
    try:
        result = prepare_h2c_persisted_case(
            strategy_settings_expected_sha256=(
                args.strategy_settings_expected_sha256
            ),
            portfolio_snapshot_expected_sha256=(
                args.portfolio_snapshot_expected_sha256
            ),
            case_root=args.case_root,
        )
    except H2cPrepareError as exc:
        sys.stderr.write(f"H2C_PREPARE_FAILED {exc.code.value}\n")
        return 3
    sys.stdout.write(
        f"workflow_status={result.workflow_status}\n"
        "prepared_case_identity_sha256="
        f"{result.prepared_case_identity_sha256}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
