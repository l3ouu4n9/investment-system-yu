"""Terminal, report-only, SAME-RUN positive H1 research-admission observer.

This module implements the first positive research-admission recognition in the
Phase-3 pipeline: it proves that qualitative research and mapped-recognition
facts came from the same consume run, and that availability selected that same
candidate in this run.  On success it returns ``ADMITTED_REPORT_ONLY``.

Authority boundary
------------------
This observer has no authority effect.  An ``ADMITTED_REPORT_ONLY`` result
explicitly carries::

    authority_effect = "NONE"
    report_only = True
    not_authorization = True

It grants no HOLD, SELL, NEW_BUY, ROTATION, REBALANCE,
EXTENDED_ETF_ADMISSION, ORDER_COMPILATION, Step-2/3/4, final-safety, or
broker permission.  It is evidence admission only.

Trust-boundary doctrine
-----------------------
Python dataclass instances are NOT cryptographic or authenticated provenance
tokens.  ``H1ConsumeResult`` and ``H1QualitativeResearchFacts`` have open
constructors (or can be bypassed via ``object.__new__``).
``H1MappedRecognitionFacts`` has a restricted ``__init__`` but Python callers
can bypass it.  ``H1MappedResearchSelectionProjection`` is similarly open.

Therefore this observer's guarantee is:

    "When invoked on objects produced together by the reviewed production
    consume-then-availability path, deterministic same-run identity equalities
    prove cohesion."

It is NOT:

    "The Python types themselves are unforgeable proof."

No signing, trust tokens, capability objects, constructor secrecy machinery,
or cryptographic receipts are introduced.  Production call-path trust plus
deterministic identity binding is sufficient for this report-only seam, as
there is currently no authority-bearing downstream consumer.

Identity doctrine
-----------------
``validated_grounded_analysis_response_identity_sha256`` and
``mapping_report_identity_sha256`` are used here ONLY for SAME-RUN cohesion.
They MUST NOT be used as cross-run content identities, persistence-continuity
keys, cross-run currentness identities, policy-change identities, or
Phase-3B persistence/comparability keys.

Persistence
-----------
This module is in-memory only.  It writes no artifact, JSON file, schema,
pointer, LKG, cache, receipt, or publication of any kind.

Downstream consumer
-------------------
There is deliberately no production downstream consumer of this module after
this PR.  Future disposition recognition is a separately authorized change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from investment_orchestrator.mmi.contracts import AUTHORITY_EFFECT_NONE
from investment_orchestrator.state.research_availability import (
    H1_MAPPED_FRESH_NON_ACTIONABLE,
    H1_ROLE_MAPPED_SOURCE,
    H1MappedResearchSelectionProjection,
)
from investment_orchestrator.workflow.h1_replacement_handoff import (
    H1ConsumeResult,
    H1QualitativeResearchFacts,
)


__all__ = (
    "Phase3SameRunResearchAdmissionObservationResult",
    "Phase3SameRunResearchAdmissionObservationStatus",
    "observe_same_run_report_only_phase3_research_admission",
)


_RESULT_SCHEMA_VERSION: Final = (
    "phase3_same_run_research_admission_observation_result_v1"
)

# Reason codes: positive path omits reason_codes (empty tuple convention,
# consistent with the upstream-success case in most deterministic observers).
_REASON_NOT_PROVEN: Final = "PHASE3_RESEARCH_ADMISSION_NOT_PROVEN"
_REASON_SELECTION_UNAVAILABLE: Final = (
    "PHASE3_RESEARCH_SELECTION_PROJECTION_UNAVAILABLE"
)
_REASON_RESPONSE_IDENTITY_MISMATCH: Final = (
    "PHASE3_RESEARCH_ADMISSION_VALIDATED_RESPONSE_IDENTITY_MISMATCH"
)
_REASON_MAPPING_IDENTITY_MISMATCH: Final = (
    "PHASE3_RESEARCH_ADMISSION_MAPPING_REPORT_IDENTITY_MISMATCH"
)
_REASON_SELECTION_STRUCTURALLY_INCONSISTENT: Final = (
    "PHASE3_RESEARCH_ADMISSION_SELECTION_STRUCTURALLY_INCONSISTENT"
)
_REASON_ADMITTED: Final = "PHASE3_RESEARCH_ADMISSION_PROVEN_SAME_RUN"


class Phase3SameRunResearchAdmissionObservationStatus(str, Enum):
    """Closed status vocabulary for same-run positive research admission.

    Only ``ADMITTED_REPORT_ONLY`` expresses successful admission.  Every other
    member represents a form of non-admission, consistent with the fail-closed
    safety doctrine.  The positive name makes explicit that admission is
    research-evidence only, not trade authorization.
    """

    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ADMITTED_REPORT_ONLY = "ADMITTED_REPORT_ONLY"


@dataclass(frozen=True, slots=True)
class Phase3SameRunResearchAdmissionObservationResult:
    """Outcome of one same-run research-admission observation.

    Carries no permission, availability-state, ticker, disposition, sizing,
    order, or persistence fact.  On positive admission the qualitative facts
    are carried in-memory for a future report-only Phase-3 interpreter; on
    all non-positive outcomes they are ``None`` to prevent accidental
    downstream use of research from a failed admission.

    Fields
    ------
    schema_version
        Fixed versioning sentinel; never persisted.
    status
        One of the four closed vocabulary members.
    reason_codes
        Empty tuple on ``ADMITTED_REPORT_ONLY``; single-element tuple otherwise.
    authority_effect
        Always ``"NONE"``.  This result grants nothing.
    report_only
        Always ``True``.
    not_authorization
        Always ``True``.
    qualitative_research_facts
        Carried only on ``ADMITTED_REPORT_ONLY``; ``None`` otherwise.
    validated_grounded_analysis_response_identity_sha256
        The proven same-run response identity; ``None`` on non-positive results.
        SAME-RUN cohesion provenance only — not a cross-run content identity.
    mapping_report_identity_sha256
        The proven same-run mapping-report identity; ``None`` on non-positive.
        SAME-RUN cohesion provenance only — not a cross-run content identity.
    selected_state
        The availability state from the selection projection; ``None`` on
        non-positive results.
    selected_source
        The availability source from the selection projection; ``None`` on
        non-positive results.
    """

    schema_version: str
    status: Phase3SameRunResearchAdmissionObservationStatus
    reason_codes: tuple[str, ...]
    authority_effect: str
    report_only: bool
    not_authorization: bool
    qualitative_research_facts: H1QualitativeResearchFacts | None
    validated_grounded_analysis_response_identity_sha256: str | None
    mapping_report_identity_sha256: str | None
    selected_state: str | None
    selected_source: str | None


def _non_positive(
    status: Phase3SameRunResearchAdmissionObservationStatus,
    reason: str,
) -> Phase3SameRunResearchAdmissionObservationResult:
    """Build a non-admitting result with all evidence fields suppressed."""
    return Phase3SameRunResearchAdmissionObservationResult(
        schema_version=_RESULT_SCHEMA_VERSION,
        status=status,
        reason_codes=(reason,),
        authority_effect=AUTHORITY_EFFECT_NONE,
        report_only=True,
        not_authorization=True,
        qualitative_research_facts=None,
        validated_grounded_analysis_response_identity_sha256=None,
        mapping_report_identity_sha256=None,
        selected_state=None,
        selected_source=None,
    )


def observe_same_run_report_only_phase3_research_admission(
    *,
    consume_result: H1ConsumeResult,
    h1_selection: H1MappedResearchSelectionProjection | None,
) -> Phase3SameRunResearchAdmissionObservationResult:
    """Observe whether a same-run positive research admission is provable.

    Accepts the whole ``H1ConsumeResult`` (preferred over decomposed
    qualitative + recognition objects, because the production trusted path
    already produces them together) and the ``H1MappedResearchSelectionProjection``
    from the same run's availability refresh.

    Does NOT accept: raw response bytes, JSON, paths, mapping report dicts,
    availability dicts, ``ResearchAvailabilityResult``, ``allowed_actions``,
    X/H/r, or any sizing/order input.

    Does NOT read any artifact, recompute freshness, re-evaluate availability,
    re-run selection, or write any artifact.

    Positive admission conditions (all must hold):

    1. ``h1_selection`` is not ``None``.
    2. ``h1_selection.h1_mapped_selected is True``.
    3. The selection projection is internally structurally consistent:
       ``state == H1_MAPPED_FRESH_NON_ACTIONABLE``,
       ``source == H1_ROLE_MAPPED_SOURCE``, and
       ``mapping_report_identity_sha256`` is present and well-formed (64-char
       hex).  P2A guarantees this by construction; the check here is a narrow
       boundary consistency guard, not a second selection algorithm.
    4. The response identity ``consume_result.qualitative_research_facts
       .validated_grounded_analysis_response_identity_sha256`` equals
       ``consume_result.mapped_recognition_facts
       .validated_grounded_analysis_response_identity_sha256`` — proving
       qualitative↔recognition SAME-RUN cohesion.
    5. The mapping-report identity ``consume_result.mapped_recognition_facts
       .mapping_report_identity_sha256`` equals
       ``h1_selection.mapping_report_identity_sha256`` — proving
       recognition↔selection SAME-RUN cohesion.

    Status mapping
    --------------
    ``h1_selection is None``
        → ``UNAVAILABLE`` / ``PHASE3_RESEARCH_SELECTION_PROJECTION_UNAVAILABLE``
    ``h1_selection.h1_mapped_selected is False``
        → ``MANUAL_REVIEW`` / ``PHASE3_RESEARCH_ADMISSION_NOT_PROVEN``
    Structurally inconsistent selected projection
        → ``INVALID`` / ``PHASE3_RESEARCH_ADMISSION_SELECTION_STRUCTURALLY_INCONSISTENT``
    Response-identity mismatch (qual ≠ recognition)
        → ``INVALID`` / ``PHASE3_RESEARCH_ADMISSION_VALIDATED_RESPONSE_IDENTITY_MISMATCH``
    Mapping-report identity mismatch (recognition ≠ selection)
        → ``INVALID`` / ``PHASE3_RESEARCH_ADMISSION_MAPPING_REPORT_IDENTITY_MISMATCH``
    All five conditions satisfied
        → ``ADMITTED_REPORT_ONLY`` / empty reason_codes

    On all non-positive results, ``qualitative_research_facts``,
    ``validated_grounded_analysis_response_identity_sha256``,
    ``mapping_report_identity_sha256``, ``selected_state``, and
    ``selected_source`` are ``None`` so a future caller cannot accidentally
    consume research from a failed admission.
    """
    Status = Phase3SameRunResearchAdmissionObservationStatus

    # --- 1. Selection projection must be present. ----------------------------
    if h1_selection is None:
        return _non_positive(Status.UNAVAILABLE, _REASON_SELECTION_UNAVAILABLE)

    # --- 2. Selection owner must have selected H1. ---------------------------
    if not h1_selection.h1_mapped_selected:
        return _non_positive(Status.MANUAL_REVIEW, _REASON_NOT_PROVEN)

    # --- 3. Narrow boundary consistency check on the selected projection. ----
    # P2A guarantees structural consistency by construction; this guard detects
    # a synthetically constructed inconsistent object before any identity is
    # extracted or compared, keeping the fail-closed invariant locally.
    mapping_report_id = h1_selection.mapping_report_identity_sha256
    if (
        h1_selection.state != H1_MAPPED_FRESH_NON_ACTIONABLE
        or h1_selection.source != H1_ROLE_MAPPED_SOURCE
        or not _is_sha256_hex(mapping_report_id)
    ):
        return _non_positive(
            Status.INVALID, _REASON_SELECTION_STRUCTURALLY_INCONSISTENT
        )
    # mapping_report_id is now a confirmed str (checked by _is_sha256_hex).
    assert isinstance(mapping_report_id, str)

    # --- 4. First binding: qualitative ↔ recognition same-run cohesion. ------
    qualitative = consume_result.qualitative_research_facts
    recognition = consume_result.mapped_recognition_facts
    qual_response_id = (
        qualitative.validated_grounded_analysis_response_identity_sha256
    )
    recog_response_id = (
        recognition.validated_grounded_analysis_response_identity_sha256
    )
    if qual_response_id != recog_response_id:
        return _non_positive(
            Status.INVALID, _REASON_RESPONSE_IDENTITY_MISMATCH
        )

    # --- 5. Second binding: recognition ↔ selection same-run cohesion. -------
    recog_mapping_id = recognition.mapping_report_identity_sha256
    if recog_mapping_id != mapping_report_id:
        return _non_positive(
            Status.INVALID, _REASON_MAPPING_IDENTITY_MISMATCH
        )

    # --- All five conditions hold: positive admission. -----------------------
    return Phase3SameRunResearchAdmissionObservationResult(
        schema_version=_RESULT_SCHEMA_VERSION,
        status=Status.ADMITTED_REPORT_ONLY,
        reason_codes=(),
        authority_effect=AUTHORITY_EFFECT_NONE,
        report_only=True,
        not_authorization=True,
        qualitative_research_facts=qualitative,
        validated_grounded_analysis_response_identity_sha256=qual_response_id,
        mapping_report_identity_sha256=mapping_report_id,
        selected_state=h1_selection.state,
        selected_source=h1_selection.source,
    )


def _is_sha256_hex(value: object) -> bool:
    """Return True iff ``value`` is a 64-character lowercase hex string."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _SHA256_HEX_DIGITS
    )


_SHA256_HEX_DIGITS: Final = frozenset("0123456789abcdef")
