"""Explicit foreground CLI for one manual LH2 exact-byte capture receipt.

The invocation itself is the manual capture event: this CLI takes no source
path, no output path, no expected digest, no role, no date, and no permission
argument, so an operator cannot redirect what is captured or where the receipt
is written.  It captures the exact current ``LONG_HORIZON_RESEARCH`` bytes,
persists their SHA-256 and byte length as the closed four-field receipt, and
prints those two values.

It grants no availability, no freshness, no Step 2 permission, and no
disposition, budget, publication, or order authority.  It performs no payload
parsing: a malformed or empty source may be captured on purpose, and rejecting
it belongs to the downstream V2 reader.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys

from investment_orchestrator.common.io import atomic_write_text
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi.contracts import MmiSourceRole
from investment_orchestrator.mmi.lh2_manual_capture_receipt_v1 import (
    LH2_MANUAL_CAPTURE_RECEIPT_PATH_COMPONENTS,
    Lh2ManualCaptureReceiptV1Error,
    build_lh2_manual_capture_receipt_v1,
    lh2_manual_capture_receipt_v1_canonical_text,
)
from investment_orchestrator.mmi.source_capture import (
    MmiStableSourceDigestError,
    capture_current_mmi_stable_source_digest,
)


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="python -m investment_orchestrator.cli.run_lh2_manual_capture",
        description=(
            "Capture the exact current LONG_HORIZON_RESEARCH source bytes and "
            "persist their SHA-256 and byte length as the fixed report-only "
            "receipt. Grants no availability, freshness, permission, or order "
            "authority."
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run one manual capture, persisting the receipt only on full success."""
    # Declares no arguments, so any stray operator argument is rejected here
    # rather than silently ignored.
    _parser().parse_args(argv)
    try:
        digest = capture_current_mmi_stable_source_digest(
            MmiSourceRole.LONG_HORIZON_RESEARCH,
        )
    except MmiStableSourceDigestError as exc:
        sys.stderr.write(f"LH2_MANUAL_CAPTURE_FAILED {exc.code}\n")
        return 3
    try:
        receipt_text = lh2_manual_capture_receipt_v1_canonical_text(
            build_lh2_manual_capture_receipt_v1(
                source_role=digest.role,
                observed_sha256=digest.observed_sha256,
                observed_size_bytes=digest.observed_size_bytes,
            )
        )
    except Lh2ManualCaptureReceiptV1Error as exc:
        sys.stderr.write(f"LH2_MANUAL_CAPTURE_FAILED {exc.code}\n")
        return 4
    # The receipt is only ever replaced by a complete new receipt: the writer
    # renames a fully written temporary file onto the fixed path, so a failure
    # before that rename leaves any previous receipt exactly as it was.
    try:
        atomic_write_text(
            repo_root().joinpath(*LH2_MANUAL_CAPTURE_RECEIPT_PATH_COMPONENTS),
            receipt_text,
        )
    except OSError:
        sys.stderr.write(
            "LH2_MANUAL_CAPTURE_FAILED "
            "LH2_MANUAL_CAPTURE_RECEIPT_PERSISTENCE_FAILED\n"
        )
        return 5
    sys.stdout.write(
        f"observed_sha256={digest.observed_sha256}\n"
        f"observed_size_bytes={digest.observed_size_bytes}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
