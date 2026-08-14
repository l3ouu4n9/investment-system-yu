"""Fixed-source, report-only Phase-3A.1 research-admission refusal observer.

This module owns no research selection, availability, currentness, or
disposition behavior. It calls exactly one existing committed primitive —
``read_persisted_research_selection_refusal_only`` — and maps that closed
three-state read result onto a closed three-state Phase-3 admission
observation. It answers only: "is there enough authenticated evidence to
admit research into future Phase-3 disposition processing?" At this phase
the answer is never yes. There is no status, field, or code path in this
module through which a positive research-admission result can be produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from investment_orchestrator.state.research_availability import (
    ResearchSelectionRefusalReadStatus,
    read_persisted_research_selection_refusal_only,
)


__all__ = (
    "Phase3AResearchAdmissionObservationResult",
    "Phase3AResearchAdmissionObservationStatus",
    "observe_current_report_only_phase3a_research_admission",
)


_AUTHORITY_EFFECT_NONE: Final = "NONE"
_RESULT_SCHEMA_VERSION: Final = (
    "phase3a_research_admission_observation_result_v1"
)
_ADMISSION_NOT_PROVEN_REASON_CODE: Final = "PHASE3_RESEARCH_ADMISSION_NOT_PROVEN"


class Phase3AResearchAdmissionObservationStatus(str, Enum):
    """Closed status vocabulary. No member expresses successful admission."""

    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(frozen=True, slots=True)
class Phase3AResearchAdmissionObservationResult:
    """Refusal-mapped Phase-3 admission outcome. Carries no permission,
    persisted-state, persisted-source, ticker, or disposition fact."""

    schema_version: str
    status: Phase3AResearchAdmissionObservationStatus
    reason_codes: tuple[str, ...]
    authority_effect: str
    report_only: bool
    not_authorization: bool
    artifact_locator: str
    artifact_observed_sha256: str | None
    artifact_observed_size_bytes: int | None


_UPSTREAM_STATUS_MAP: Final = {
    ResearchSelectionRefusalReadStatus.UNAVAILABLE: (
        Phase3AResearchAdmissionObservationStatus.UNAVAILABLE
    ),
    ResearchSelectionRefusalReadStatus.INVALID: (
        Phase3AResearchAdmissionObservationStatus.INVALID
    ),
}


def observe_current_report_only_phase3a_research_admission() -> (
    Phase3AResearchAdmissionObservationResult
):
    """Observe the fixed current refusal-only read with no admission path.

    Accepts no path, availability state, source, currentness, research
    identity, candidate, ticker, disposition, or sizing input. Calls only
    ``read_persisted_research_selection_refusal_only`` and never opens the
    availability artifact or reproduces availability classification itself.
    """
    upstream = read_persisted_research_selection_refusal_only()

    if upstream.status is ResearchSelectionRefusalReadStatus.REFUSAL_ONLY:
        status = Phase3AResearchAdmissionObservationStatus.MANUAL_REVIEW
        reason_codes: tuple[str, ...] = (_ADMISSION_NOT_PROVEN_REASON_CODE,)
    else:
        status = _UPSTREAM_STATUS_MAP[upstream.status]
        reason_codes = upstream.reason_codes

    return Phase3AResearchAdmissionObservationResult(
        schema_version=_RESULT_SCHEMA_VERSION,
        status=status,
        reason_codes=reason_codes,
        authority_effect=_AUTHORITY_EFFECT_NONE,
        report_only=True,
        not_authorization=True,
        artifact_locator=upstream.artifact_locator,
        artifact_observed_sha256=upstream.artifact_observed_sha256,
        artifact_observed_size_bytes=upstream.artifact_observed_size_bytes,
    )
