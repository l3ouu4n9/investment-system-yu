"""Explicit foreground CLI for one report-only H2c manual capture."""

from __future__ import annotations

import argparse
import errno
from pathlib import Path
from collections.abc import Sequence
import sys
from typing import Final

from investment_orchestrator.offline.mmi_h2c_manual_capture_session import (
    H2cManualCaptureError,
    H2cManualCaptureErrorCode,
    H2cManualCaptureFailureClass,
    run_h2c_manual_capture,
)


_CONTROL_RECORD: Final = b"H2C_RESPONSES_READY\n"
_CONTROL_READ_LIMIT: Final = 21
_INSTRUCTION: Final = (
    "H2C prompts are ready; populate both response files, then enter "
    "H2C_RESPONSES_READY exactly.\n"
)
_CONTROLLED_STDIN_ERRNOS: Final = frozenset(
    {errno.EBADF, errno.EIO, errno.ENXIO}
)


class _StdinH2cOperatorHandoff:
    __slots__ = ()

    def await_response_files_ready(self) -> None:
        sys.stderr.write(_INSTRUCTION)
        sys.stderr.flush()
        try:
            observed = sys.stdin.buffer.readline(_CONTROL_READ_LIMIT)
        except OSError as exc:
            if exc.errno not in _CONTROLLED_STDIN_ERRNOS:
                raise
            raise H2cManualCaptureError(
                code=(
                    H2cManualCaptureErrorCode.H2C_CAPABILITY_UNAVAILABLE
                ),
                failure_class=(
                    H2cManualCaptureFailureClass.AVAILABILITY_PERMISSION
                ),
            ) from None
        if observed != _CONTROL_RECORD:
            raise H2cManualCaptureError(
                code=(
                    H2cManualCaptureErrorCode.H2C_OPERATOR_CONTROL_INVALID
                ),
                failure_class=H2cManualCaptureFailureClass.OPERATOR_INPUT,
            ) from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m investment_orchestrator.cli.run_mmi_h2c_capture",
        description=(
            "Run one foreground report-only H2c dual-side manual capture."
        ),
    )
    parser.add_argument("--strategy-settings-expected-sha256", required=True)
    parser.add_argument("--portfolio-snapshot-expected-sha256", required=True)
    parser.add_argument("--h1-prompt-output-path", required=True, type=Path)
    parser.add_argument(
        "--legacy-prompt-output-path", required=True, type=Path
    )
    parser.add_argument("--h1-response-path", required=True, type=Path)
    parser.add_argument("--legacy-response-path", required=True, type=Path)
    parser.add_argument(
        "--comparison-report-output-path", required=True, type=Path
    )
    parser.add_argument("--receipt-output-path", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse explicit paths, run one capture, and print only identities."""
    args = _parser().parse_args(argv)
    try:
        result = run_h2c_manual_capture(
            strategy_settings_expected_sha256=(
                args.strategy_settings_expected_sha256
            ),
            portfolio_snapshot_expected_sha256=(
                args.portfolio_snapshot_expected_sha256
            ),
            h1_prompt_output_path=args.h1_prompt_output_path,
            legacy_prompt_output_path=args.legacy_prompt_output_path,
            h1_response_path=args.h1_response_path,
            legacy_response_path=args.legacy_response_path,
            comparison_report_output_path=(
                args.comparison_report_output_path
            ),
            receipt_output_path=args.receipt_output_path,
            operator_handoff=_StdinH2cOperatorHandoff(),
        )
    except H2cManualCaptureError as exc:
        sys.stderr.write(f"H2C_CAPTURE_FAILED {exc.code.value}\n")
        return 3
    sys.stdout.write(
        "comparison_report_identity_sha256="
        f"{result.comparison_report_identity_sha256}\n"
        "receipt_identity_sha256="
        f"{result.receipt_identity_sha256}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
