"""Closed four-field receipt contract for one manual LH2 exact-byte capture.

This module owns only the pure receipt contract: its schema version, its fixed
source-role requirement, its fixed repository-relative path components, its
builder, its validator, and its canonical serialization.  It opens no
descriptor, reads no source, writes no file, and imports no persistence owner;
capture belongs to ``mmi.source_capture`` and persistence belongs to the
invoking CLI.

Authority & Provenance Boundary:
--------------------------------
- The receipt records only that deterministic code obtained two consistent
  content samples from the fixed ``LONG_HORIZON_RESEARCH`` catalog source under
  the existing repository/path/witness provenance checks, and that
  ``observed_sha256`` and ``observed_size_bytes`` describe those exact final
  stable bytes.
- It establishes no external authenticity, no publisher truth, no V2 payload
  validity, no freshness, no availability, no Step 2 permission, and no
  disposition, budget, publication, or order authority.
- ``observed_sha256`` binds the captured source bytes.  It is deliberately not
  a receipt artifact identity, and no identity digest is carried: the content
  digest itself is the cross-run continuity binding.
- Malformed or empty source bytes may be captured on purpose.  Rejecting an
  unparseable payload belongs strictly to the downstream V2 reader.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, NoReturn

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MmiCanonicalizationError,
    canonical_json_bytes,
)
from investment_orchestrator.mmi.contracts import MmiSourceRole


__all__ = (
    "LH2_MANUAL_CAPTURE_RECEIPT_PATH_COMPONENTS",
    "LH2_MANUAL_CAPTURE_RECEIPT_V1_SCHEMA_NAME",
    "LH2_MANUAL_CAPTURE_RECEIPT_V1_SCHEMA_VERSION",
    "Lh2ManualCaptureReceiptV1Error",
    "build_lh2_manual_capture_receipt_v1",
    "lh2_manual_capture_receipt_v1_canonical_text",
    "validate_lh2_manual_capture_receipt_v1",
)

LH2_MANUAL_CAPTURE_RECEIPT_V1_SCHEMA_VERSION: Final = (
    "lh2_manual_capture_receipt_v1"
)
LH2_MANUAL_CAPTURE_RECEIPT_V1_SCHEMA_NAME: Final = (
    "lh2_manual_capture_receipt_v1.schema.json"
)
LH2_MANUAL_CAPTURE_RECEIPT_PATH_COMPONENTS: Final = (
    "inputs",
    "current",
    "lh2_manual_capture_receipt.json",
)
_RECEIPT_SOURCE_ROLE: Final = MmiSourceRole.LONG_HORIZON_RESEARCH


class Lh2ManualCaptureReceiptV1Error(ValueError):
    """Raised when an LH2 manual capture receipt violates its closed contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise Lh2ManualCaptureReceiptV1Error(code) from None


def validate_lh2_manual_capture_receipt_v1(
    receipt: Mapping[str, object],
) -> None:
    """Validate one receipt against the closed four-field schema.

    Structural closure -- exact key set, fixed schema version, fixed source
    role, lowercase 64-hex digest, and a bounded non-negative byte length -- is
    owned entirely by the schema asset, so no second independent validator is
    maintained here.
    """
    try:
        validate_artifact_schema(
            receipt,
            schema_name=LH2_MANUAL_CAPTURE_RECEIPT_V1_SCHEMA_NAME,
        )
    except Exception:
        _fail("LH2_MANUAL_CAPTURE_RECEIPT_V1_INVALID")


def build_lh2_manual_capture_receipt_v1(
    *,
    source_role: MmiSourceRole,
    observed_sha256: str,
    observed_size_bytes: int,
) -> dict[str, object]:
    """Build and validate one closed four-field receipt.

    The source role is fixed by this contract; no other catalog role may be
    recorded through it.
    """
    if source_role is not _RECEIPT_SOURCE_ROLE:
        _fail("LH2_MANUAL_CAPTURE_RECEIPT_V1_SOURCE_ROLE_INVALID")
    receipt: dict[str, object] = {
        "schema_version": LH2_MANUAL_CAPTURE_RECEIPT_V1_SCHEMA_VERSION,
        "source_role": source_role.value,
        "observed_sha256": observed_sha256,
        "observed_size_bytes": observed_size_bytes,
    }
    validate_lh2_manual_capture_receipt_v1(receipt)
    return receipt


def lh2_manual_capture_receipt_v1_canonical_text(
    receipt: Mapping[str, object],
) -> str:
    """Render one validated receipt through the frozen MMI canonical profile."""
    validate_lh2_manual_capture_receipt_v1(receipt)
    try:
        return canonical_json_bytes(receipt).decode("utf-8")
    except (MmiCanonicalizationError, UnicodeDecodeError):
        _fail("LH2_MANUAL_CAPTURE_RECEIPT_V1_CANONICALIZATION_FAILED")
