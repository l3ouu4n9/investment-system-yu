"""Strict validation for Phase-3 same-run research handoff identity cohesion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


_SHA256_HEX_DIGITS: Final = frozenset("0123456789abcdef")

# Explicit validator-owned reason codes.
PHASE3_RESEARCH_ADMISSION_SELECTION_STRUCTURALLY_INCONSISTENT: Final = (
    "PHASE3_RESEARCH_ADMISSION_SELECTION_STRUCTURALLY_INCONSISTENT"
)
PHASE3_RESEARCH_ADMISSION_VALIDATED_RESPONSE_IDENTITY_MISMATCH: Final = (
    "PHASE3_RESEARCH_ADMISSION_VALIDATED_RESPONSE_IDENTITY_MISMATCH"
)
PHASE3_RESEARCH_ADMISSION_MAPPING_REPORT_IDENTITY_MISMATCH: Final = (
    "PHASE3_RESEARCH_ADMISSION_MAPPING_REPORT_IDENTITY_MISMATCH"
)


class Phase3SameRunHandoffValidationStatus(str, Enum):
    """Closed validator status vocabulary for identity cohesion."""

    VALID = "VALID"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class Phase3SameRunHandoffIdentityClaims:
    """Narrow identity facts required to prove cross-object handoff cohesion.

    This is NOT authenticated or provenance-bearing. The observer constructs
    it only after its own legitimate eligibility/reporting preconditions have
    been met.
    """

    qualitative_response_identity_sha256: str | None
    recognition_response_identity_sha256: str | None
    recognition_mapping_report_identity_sha256: str | None
    selection_mapping_report_identity_sha256: str | None


@dataclass(frozen=True, slots=True)
class Phase3SameRunHandoffValidationResult:
    """Minimal immutable result proving same-run cohesion.

    A VALID status means ONLY that the supplied same-run identity claims
    satisfy the validator-owned cohesion contract. It grants no Step-2 admission,
    permission, availability, or investment authority.
    """

    status: Phase3SameRunHandoffValidationStatus
    reason_codes: tuple[str, ...]


def _is_sha256_hex(value: object) -> bool:
    """Return True iff ``value`` is a 64-character lowercase hex string."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _SHA256_HEX_DIGITS
    )


def validate_phase3_same_run_research_handoff_cohesion(
    claims: Phase3SameRunHandoffIdentityClaims,
) -> Phase3SameRunHandoffValidationResult:
    """Validate same-run cross-object identity cohesion.

    This validator enforces the mathematical identity cohesion of a nominally
    selected candidate. It is invoked ONLY when upstream selection/eligibility
    conditions are met (i.e. legitimate absence is handled by the caller).
    """
    Status = Phase3SameRunHandoffValidationStatus

    qual_resp = claims.qualitative_response_identity_sha256
    recog_resp = claims.recognition_response_identity_sha256
    recog_map = claims.recognition_mapping_report_identity_sha256
    sel_map = claims.selection_mapping_report_identity_sha256

    if not _is_sha256_hex(sel_map):
        return Phase3SameRunHandoffValidationResult(
            status=Status.INVALID,
            reason_codes=(
                PHASE3_RESEARCH_ADMISSION_SELECTION_STRUCTURALLY_INCONSISTENT,
            ),
        )

    if qual_resp != recog_resp:
        return Phase3SameRunHandoffValidationResult(
            status=Status.INVALID,
            reason_codes=(
                PHASE3_RESEARCH_ADMISSION_VALIDATED_RESPONSE_IDENTITY_MISMATCH,
            ),
        )

    if recog_map != sel_map:
        return Phase3SameRunHandoffValidationResult(
            status=Status.INVALID,
            reason_codes=(
                PHASE3_RESEARCH_ADMISSION_MAPPING_REPORT_IDENTITY_MISMATCH,
            ),
        )

    return Phase3SameRunHandoffValidationResult(
        status=Status.VALID,
        reason_codes=(),
    )
