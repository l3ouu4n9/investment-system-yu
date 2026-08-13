"""PR-P2B: terminal, report-only, SAME-RUN positive research-admission observer.

These tests prove the load-bearing admission conditions, fail-closed behavior,
non-positive evidence suppression, identity doctrine, and authority isolation.
They do NOT retest: P1 qualitative projection internals (test_h1_replacement_
handoff.py), P2A availability selection/projection (test_h1_mapped_availability.py
/ test_h1_mapped_research_selection_projection.py), Phase-3A.1 refusal observer
(test_report_only_phase3a_research_admission.py), or upstream validator internals.

All integration tests use hermetic tmp roots.  No current artifact is touched.
No availability is refreshed.  No staging, committing, pushing, or network call.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi import contracts, source_capture
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
)
from investment_orchestrator.observability.report_only_phase3_same_run_research_admission import (
    Phase3SameRunResearchAdmissionObservationResult,
    Phase3SameRunResearchAdmissionObservationStatus as Status,
    observe_same_run_report_only_phase3_research_admission,
)
from investment_orchestrator.state.research_availability import (
    H1_MAPPED_FRESH_NON_ACTIONABLE,
    H1_ROLE_MAPPED_SOURCE,
    H1MappedResearchSelectionProjection,
)
from investment_orchestrator.workflow import h1_replacement_handoff as handoff
from investment_orchestrator.workflow import step1_research
from investment_orchestrator.workflow.h1_replacement_handoff import (
    H1ConsumeResult,
    H1QualitativeInstrumentView,
    H1QualitativeResearchFacts,
    consume_h1_replacement_handoff,
    prepare_h1_replacement_handoff,
)

import _mmi_hermetic_source_checkout as hermetic


# --------------------------------------------------------------------------
# Shared test constants.
# --------------------------------------------------------------------------
_IDENTITY_A = "a" * 64
_IDENTITY_B = "b" * 64
_IDENTITY_C = "c" * 64

_PREPARED_TIME_A = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
_PREPARED_TIME_B = datetime(2026, 7, 31, 13, tzinfo=timezone.utc)
_TIMESTAMP_A = "2026-07-31T12:00:00.000000Z"
_TIMESTAMP_B = "2026-07-31T13:00:00.000000Z"

_PREPARED_LEAF = "h1_prepared_handoff.json"
_RESPONSE_LEAF = "h1_response.raw"
_MAPPING_LEAF = "h1_legacy_step1_mapping_report.json"


# --------------------------------------------------------------------------
# Projection builder helpers.
# --------------------------------------------------------------------------
_SELECTION_PROJECTION_SCHEMA = "h1_mapped_research_selection_projection_v1"


def _selected_projection(
    *,
    mapping_report_identity_sha256: str = _IDENTITY_A,
    state: str = H1_MAPPED_FRESH_NON_ACTIONABLE,
    source: str = H1_ROLE_MAPPED_SOURCE,
) -> H1MappedResearchSelectionProjection:
    """Build a synthetically selected projection for unit tests."""
    return H1MappedResearchSelectionProjection(
        schema_version=_SELECTION_PROJECTION_SCHEMA,
        h1_mapped_selected=True,
        state=state,
        source=source,
        mapping_report_identity_sha256=mapping_report_identity_sha256,
        report_only=True,
        not_authorization=True,
        authority_effect="NONE",
    )


def _unselected_projection() -> H1MappedResearchSelectionProjection:
    return H1MappedResearchSelectionProjection(
        schema_version=_SELECTION_PROJECTION_SCHEMA,
        h1_mapped_selected=False,
        state="MANUAL_REVIEW_REQUIRED",
        source="raw_research_handoff",
        mapping_report_identity_sha256=None,
        report_only=True,
        not_authorization=True,
        authority_effect="NONE",
    )


def _qualitative_facts(
    *,
    response_identity: str = _IDENTITY_A,
) -> H1QualitativeResearchFacts:
    """Build a minimal synthetic H1QualitativeResearchFacts for unit tests."""
    facts = object.__new__(H1QualitativeResearchFacts)
    object.__setattr__(facts, "analysis_status", "QUALITATIVE_ANALYSIS_PROVIDED")
    object.__setattr__(
        facts,
        "instrument_views",
        (
            H1QualitativeInstrumentView(
                ticker="QQQ",
                evidence_status="EVIDENCE_SUPPORTED",
                rationale_12m_plus="Qualitative evidence text.",
                references=("POLICY.INSTRUMENT.0001",),
            ),
        ),
    )
    object.__setattr__(
        facts,
        "validated_grounded_analysis_response_identity_sha256",
        response_identity,
    )
    return facts


def _recognition_facts(
    *,
    response_identity: str = _IDENTITY_A,
    mapping_report_identity: str = _IDENTITY_A,
) -> object:
    """Build a synthetic H1MappedRecognitionFacts-shaped object for unit tests.

    We use object.__new__ to bypass the constructor restriction, exactly as the
    production factory does.  The fields used here are only those the observer
    inspects: the two identity attributes.
    """
    from investment_orchestrator.research.h1_mapped_recognition import (
        H1MappedRecognitionFacts,
    )

    facts = object.__new__(H1MappedRecognitionFacts)
    # Set only the fields the observer reads; leave others unset (they are not
    # accessed).
    object.__setattr__(
        facts,
        "validated_grounded_analysis_response_identity_sha256",
        response_identity,
    )
    object.__setattr__(
        facts,
        "mapping_report_identity_sha256",
        mapping_report_identity,
    )
    return facts


def _consume_result(
    *,
    qual_response_identity: str = _IDENTITY_A,
    recog_response_identity: str = _IDENTITY_A,
    mapping_report_identity: str = _IDENTITY_A,
) -> H1ConsumeResult:
    """Build a synthetic H1ConsumeResult for unit tests."""
    return H1ConsumeResult(
        workflow_status="COMPLETED",
        prepared_handoff_identity_sha256=_IDENTITY_B,
        mapping_report_identity_sha256=mapping_report_identity,
        portfolio_snapshot_presence="PRESENT",
        mapped_recognition_facts=_recognition_facts(
            response_identity=recog_response_identity,
            mapping_report_identity=mapping_report_identity,
        ),
        qualitative_research_facts=_qualitative_facts(
            response_identity=qual_response_identity,
        ),
    )


# --------------------------------------------------------------------------
# A. Valid trusted consume result + matching selected projection
#    → ADMITTED_REPORT_ONLY
# --------------------------------------------------------------------------
def test_valid_consume_with_matching_selection_produces_admitted_report_only() -> (
    None
):
    result = observe_same_run_report_only_phase3_research_admission(
        consume_result=_consume_result(),
        h1_selection=_selected_projection(),
    )

    assert result.status is Status.ADMITTED_REPORT_ONLY
    assert result.reason_codes == ()
    assert result.authority_effect == "NONE"
    assert result.report_only is True
    assert result.not_authorization is True
    assert result.qualitative_research_facts is not None
    assert result.validated_grounded_analysis_response_identity_sha256 == _IDENTITY_A
    assert result.mapping_report_identity_sha256 == _IDENTITY_A
    assert result.selected_state == H1_MAPPED_FRESH_NON_ACTIONABLE
    assert result.selected_source == H1_ROLE_MAPPED_SOURCE


# --------------------------------------------------------------------------
# B. selected=False → MANUAL_REVIEW / NOT_PROVEN
# --------------------------------------------------------------------------
def test_unselected_h1_yields_manual_review_not_proven() -> None:
    result = observe_same_run_report_only_phase3_research_admission(
        consume_result=_consume_result(),
        h1_selection=_unselected_projection(),
    )

    assert result.status is Status.MANUAL_REVIEW
    assert result.reason_codes == ("PHASE3_RESEARCH_ADMISSION_NOT_PROVEN",)
    # Non-positive: all evidence fields suppressed.
    assert result.qualitative_research_facts is None
    assert result.validated_grounded_analysis_response_identity_sha256 is None
    assert result.mapping_report_identity_sha256 is None
    assert result.selected_state is None
    assert result.selected_source is None


# --------------------------------------------------------------------------
# C. h1_selection=None → UNAVAILABLE
# --------------------------------------------------------------------------
def test_none_selection_yields_unavailable() -> None:
    result = observe_same_run_report_only_phase3_research_admission(
        consume_result=_consume_result(),
        h1_selection=None,
    )

    assert result.status is Status.UNAVAILABLE
    assert result.reason_codes == (
        "PHASE3_RESEARCH_SELECTION_PROJECTION_UNAVAILABLE",
    )
    assert result.qualitative_research_facts is None
    assert result.validated_grounded_analysis_response_identity_sha256 is None
    assert result.mapping_report_identity_sha256 is None
    assert result.selected_state is None
    assert result.selected_source is None


# --------------------------------------------------------------------------
# D. Validated-response identity mismatch → INVALID (not MANUAL_REVIEW)
# --------------------------------------------------------------------------
def test_response_identity_mismatch_yields_invalid() -> None:
    result = observe_same_run_report_only_phase3_research_admission(
        consume_result=_consume_result(
            qual_response_identity=_IDENTITY_A,
            recog_response_identity=_IDENTITY_B,  # differs
            mapping_report_identity=_IDENTITY_A,
        ),
        h1_selection=_selected_projection(mapping_report_identity_sha256=_IDENTITY_A),
    )

    assert result.status is Status.INVALID
    assert result.reason_codes == (
        "PHASE3_RESEARCH_ADMISSION_VALIDATED_RESPONSE_IDENTITY_MISMATCH",
    )
    # All evidence fields suppressed on INVALID.
    assert result.qualitative_research_facts is None
    assert result.validated_grounded_analysis_response_identity_sha256 is None
    assert result.mapping_report_identity_sha256 is None


# --------------------------------------------------------------------------
# E. Mapping-report identity mismatch → INVALID, no qualitative facts exposed
# --------------------------------------------------------------------------
def test_mapping_report_identity_mismatch_yields_invalid() -> None:
    result = observe_same_run_report_only_phase3_research_admission(
        consume_result=_consume_result(
            qual_response_identity=_IDENTITY_A,
            recog_response_identity=_IDENTITY_A,
            mapping_report_identity=_IDENTITY_B,  # recognition says B
        ),
        h1_selection=_selected_projection(
            mapping_report_identity_sha256=_IDENTITY_C  # selection says C ≠ B
        ),
    )

    assert result.status is Status.INVALID
    assert result.reason_codes == (
        "PHASE3_RESEARCH_ADMISSION_MAPPING_REPORT_IDENTITY_MISMATCH",
    )
    assert result.qualitative_research_facts is None
    assert result.validated_grounded_analysis_response_identity_sha256 is None
    assert result.mapping_report_identity_sha256 is None


# --------------------------------------------------------------------------
# F. Synthetically inconsistent selected projection → INVALID
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("state", "source", "mapping_id"),
    [
        # selected=True but wrong state
        ("STRICT_FRESH", H1_ROLE_MAPPED_SOURCE, _IDENTITY_A),
        # selected=True but wrong source
        (H1_MAPPED_FRESH_NON_ACTIONABLE, "raw_research_handoff", _IDENTITY_A),
        # selected=True but identity is None
        (H1_MAPPED_FRESH_NON_ACTIONABLE, H1_ROLE_MAPPED_SOURCE, None),
        # selected=True but identity is malformed
        (H1_MAPPED_FRESH_NON_ACTIONABLE, H1_ROLE_MAPPED_SOURCE, "not-a-hash"),
        # selected=True but identity is empty string
        (H1_MAPPED_FRESH_NON_ACTIONABLE, H1_ROLE_MAPPED_SOURCE, ""),
    ],
    ids=[
        "wrong_state",
        "wrong_source",
        "identity_none",
        "identity_malformed",
        "identity_empty",
    ],
)
def test_synthetically_inconsistent_selected_projection_yields_invalid(
    state: str,
    source: str,
    mapping_id: str | None,
) -> None:
    """Structurally inconsistent selected projections fail closed as INVALID."""
    projection = H1MappedResearchSelectionProjection(
        schema_version=_SELECTION_PROJECTION_SCHEMA,
        h1_mapped_selected=True,  # claims selected
        state=state,
        source=source,
        mapping_report_identity_sha256=mapping_id,
        report_only=True,
        not_authorization=True,
        authority_effect="NONE",
    )

    result = observe_same_run_report_only_phase3_research_admission(
        consume_result=_consume_result(),
        h1_selection=projection,
    )

    assert result.status is Status.INVALID
    assert result.reason_codes == (
        "PHASE3_RESEARCH_ADMISSION_SELECTION_STRUCTURALLY_INCONSISTENT",
    )
    assert result.qualitative_research_facts is None


# --------------------------------------------------------------------------
# G. Positive result carries qualitative facts; non-positive results suppress
# --------------------------------------------------------------------------
def test_positive_result_carries_qualitative_facts() -> None:
    result = observe_same_run_report_only_phase3_research_admission(
        consume_result=_consume_result(),
        h1_selection=_selected_projection(),
    )
    assert result.status is Status.ADMITTED_REPORT_ONLY
    assert isinstance(result.qualitative_research_facts, H1QualitativeResearchFacts)
    assert result.qualitative_research_facts.analysis_status == (
        "QUALITATIVE_ANALYSIS_PROVIDED"
    )


@pytest.mark.parametrize(
    "status",
    [Status.UNAVAILABLE, Status.INVALID, Status.MANUAL_REVIEW],
)
def test_non_positive_result_suppresses_all_evidence_fields(
    status: Status,
) -> None:
    """All non-positive statuses suppress qualitative facts and identity fields."""
    # Each status is reached via a different input; we reuse existing fixtures.
    if status is Status.UNAVAILABLE:
        result = observe_same_run_report_only_phase3_research_admission(
            consume_result=_consume_result(),
            h1_selection=None,
        )
    elif status is Status.MANUAL_REVIEW:
        result = observe_same_run_report_only_phase3_research_admission(
            consume_result=_consume_result(),
            h1_selection=_unselected_projection(),
        )
    else:
        # INVALID via response-identity mismatch
        result = observe_same_run_report_only_phase3_research_admission(
            consume_result=_consume_result(
                qual_response_identity=_IDENTITY_A,
                recog_response_identity=_IDENTITY_B,
            ),
            h1_selection=_selected_projection(),
        )

    assert result.status is status
    assert result.qualitative_research_facts is None
    assert result.validated_grounded_analysis_response_identity_sha256 is None
    assert result.mapping_report_identity_sha256 is None
    assert result.selected_state is None
    assert result.selected_source is None


# --------------------------------------------------------------------------
# H. P1 None-rationale / empty-reference integration — REQUIRED.
#    Carries the non-blocking P1 regression gap through the real validator/
#    consume path and into positive P2B admission.
# --------------------------------------------------------------------------
class _FixedClock:
    def now_utc(self) -> datetime:
        return _PREPARED_TIME_A


def _response_bytes_with_unavailable_row(
    view: dict[str, object],
    prompt: dict[str, object],
) -> bytes:
    """Build a valid response where one instrument view has UNAVAILABLE status.

    UNAVAILABLE requires rationale_12m_plus=None and references=[].
    All other rows use EVIDENCE_SUPPORTED.
    """
    policy_view = view["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list and len(instruments) >= 2, (
        "Need at least two instruments for the None-rationale fixture"
    )
    rows = []
    for index, item in enumerate(instruments, start=1):
        if type(item) is not dict:
            continue
        if index == 1:
            # First instrument: UNAVAILABLE with None rationale and empty refs.
            rows.append(
                {
                    "ticker": item["ticker"],
                    "evidence_status": "UNAVAILABLE",
                    "rationale_12m_plus": None,
                    "references": [],
                }
            )
        else:
            rows.append(
                {
                    "ticker": item["ticker"],
                    "evidence_status": "EVIDENCE_SUPPORTED",
                    "rationale_12m_plus": "Evidence-linked rationale.",
                    "references": [f"POLICY.INSTRUMENT.{index:04d}"],
                }
            )
    binding = prompt["prompt_context_binding_sha256"]
    assert type(binding) is str
    qualitative = {
        "text": "Report-only qualitative observation.",
        "references": ["VIEW.EVALUATION_TIMESTAMP"],
        "hypothesis": False,
    }
    return json.dumps(
        {
            "response_schema_version": "mmi_grounded_analysis_response_v2",
            "prompt_context_binding_sha256": binding,
            "analysis_status": "QUALITATIVE_ANALYSIS_PROVIDED",
            "instrument_views": rows,
            "anchor_associations_status": "UNAVAILABLE",
            "scheduled_events_status": "UNAVAILABLE",
            "regime_observation_status": "UNAVAILABLE",
            "evidence_observations": [qualitative],
            "risks": [],
            "uncertainties": [],
            "contradictions": [],
            "research_questions": [],
            "summary": dict(qualitative),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _response_bytes_all_supported(
    view: dict[str, object],
    prompt: dict[str, object],
) -> bytes:
    policy_view = view["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    rows = [
        {
            "ticker": item["ticker"],
            "evidence_status": "EVIDENCE_SUPPORTED",
            "rationale_12m_plus": "Evidence-linked qualitative rationale.",
            "references": [f"POLICY.INSTRUMENT.{index:04d}"],
        }
        for index, item in enumerate(instruments, start=1)
        if type(item) is dict
    ]
    binding = prompt["prompt_context_binding_sha256"]
    assert type(binding) is str
    qualitative = {
        "text": "Report-only qualitative observation.",
        "references": ["VIEW.EVALUATION_TIMESTAMP"],
        "hypothesis": False,
    }
    return json.dumps(
        {
            "response_schema_version": "mmi_grounded_analysis_response_v2",
            "prompt_context_binding_sha256": binding,
            "analysis_status": "QUALITATIVE_ANALYSIS_PROVIDED",
            "instrument_views": rows,
            "anchor_associations_status": "UNAVAILABLE",
            "scheduled_events_status": "UNAVAILABLE",
            "regime_observation_status": "UNAVAILABLE",
            "evidence_observations": [qualitative],
            "risks": [],
            "uncertainties": [],
            "contradictions": [],
            "research_questions": [],
            "summary": dict(qualitative),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _build_hermetic_consume_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    clock: object,
    response_builder,
) -> tuple[H1ConsumeResult, dict[str, object]]:
    """Run the real prepare→consume path in a hermetic tmp directory.

    Returns ``(H1ConsumeResult, prompt_dict)`` so callers can extract the
    upstream identity chain for independent verification.
    """
    from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
        build_mmi_analyst_visible_evidence_view_v2,
    )
    from investment_orchestrator.mmi.evidence_bundle import (
        build_mmi_authenticated_evidence_bundle,
    )
    from investment_orchestrator.mmi.grounded_prompt_v2 import (
        build_mmi_grounded_prompt_v2,
    )
    from investment_orchestrator.mmi.policy_projection import (
        build_mmi_policy_projection,
    )
    from investment_orchestrator.mmi.portfolio_projection import (
        build_mmi_portfolio_snapshot_projection,
    )

    checkout_root = tmp_path / "checkout"
    (checkout_root / "inputs" / "current").mkdir(parents=True)
    directory = tmp_path / "artifacts" / "current" / "h1_replacement"

    strategy_raw = hermetic.strategy_settings_bytes()
    portfolio_raw = hermetic.portfolio_snapshot_bytes()
    hermetic.install_source(checkout_root, role=MmiSourceRole.STRATEGY_SETTINGS, raw=strategy_raw)
    hermetic.install_source(checkout_root, role=MmiSourceRole.PORTFOLIO_SNAPSHOT, raw=portfolio_raw)
    strategy_sha256 = hashlib.sha256(strategy_raw).hexdigest()
    portfolio_sha256 = hashlib.sha256(portfolio_raw).hexdigest()

    def _capture(role, *, expected_source_sha256):
        return source_capture._capture_mmi_source_at_root(
            checkout_root, role=role, expected_source_sha256=expected_source_sha256
        )

    def _absence(role):
        return source_capture._capture_mmi_source_absence_at_root(checkout_root, role=role)

    monkeypatch.setattr(source_capture, "capture_current_mmi_source", _capture)
    monkeypatch.setattr(source_capture, "capture_current_mmi_source_absence", _absence)
    monkeypatch.setattr(contracts, "_SystemUtcClock", lambda: clock)
    monkeypatch.setattr(handoff, "_handoff_directory", lambda: directory)
    monkeypatch.setattr(step1_research, "repo_root", lambda: checkout_root)

    prepared = prepare_h1_replacement_handoff(
        strategy_settings_expected_sha256=strategy_sha256,
        portfolio_snapshot_expected_sha256=portfolio_sha256,
        portfolio_snapshot_absent=False,
    )

    # Build independent prompt for view/prompt oracle.
    run_context = _begin_mmi_projection_run_with_clock(clock)
    policy_source_capture = source_capture._capture_mmi_source_at_root(
        checkout_root,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=strategy_sha256,
    )
    assert policy_source_capture.valid
    policy_source = policy_source_capture.source

    portfolio_source_capture = source_capture._capture_mmi_source_at_root(
        checkout_root,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        expected_source_sha256=portfolio_sha256,
    )
    assert portfolio_source_capture.valid
    portfolio_source = portfolio_source_capture.source

    policy_result = build_mmi_policy_projection(policy_source, run_context=run_context)
    assert policy_result.valid
    policy = dict(policy_result.projection or {})

    portfolio_result = build_mmi_portfolio_snapshot_projection(
        portfolio_source,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert portfolio_result.valid
    portfolio = dict(portfolio_result.projection or {})

    evidence_result = build_mmi_authenticated_evidence_bundle(
        policy_projection=policy,
        policy_source=policy_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    assert evidence_result.valid
    evidence = dict(evidence_result.projection or {})

    view_result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=policy_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    assert view_result.valid
    view = dict(view_result.projection or {})

    prompt = build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=view,
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=policy_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )

    response_leaf = directory / _RESPONSE_LEAF
    response_leaf.parent.mkdir(parents=True, exist_ok=True)
    response_leaf.write_bytes(response_builder(view, prompt))

    consumed = consume_h1_replacement_handoff(
        expected_prepared_handoff_identity_sha256=(
            prepared.prepared_handoff_identity_sha256
        )
    )
    return consumed, prompt


def test_p1_none_rationale_carries_through_real_consume_to_positive_p2b_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: UNAVAILABLE row with None rationale and empty references
    passes through real validation→H1ConsumeResult→P2B positive admission
    without normalization.

    Proves:
    - evidence_status=UNAVAILABLE + rationale_12m_plus=None + references=()
      survives end-to-end unchanged.
    - The positive P2B binding still holds for a consume result with such a row.
    """
    clock = type("_FixedClock", (), {"now_utc": lambda self: _PREPARED_TIME_A})()

    consumed, _ = _build_hermetic_consume_result(
        tmp_path,
        monkeypatch,
        clock=clock,
        response_builder=_response_bytes_with_unavailable_row,
    )

    qualitative = consumed.qualitative_research_facts
    # Verify the UNAVAILABLE row survived the full validation chain unchanged.
    unavailable_views = [
        v for v in qualitative.instrument_views if v.evidence_status == "UNAVAILABLE"
    ]
    assert len(unavailable_views) >= 1, "Expected at least one UNAVAILABLE view"
    uv = unavailable_views[0]
    assert uv.rationale_12m_plus is None
    assert uv.references == ()

    # Now build a matching selected projection from the same consume result's
    # recognition facts and confirm positive P2B admission.
    recognition = consumed.mapped_recognition_facts
    mapping_report_id = recognition.mapping_report_identity_sha256
    response_id = recognition.validated_grounded_analysis_response_identity_sha256

    selection = _selected_projection(mapping_report_identity_sha256=mapping_report_id)
    # Also patch the response identity in the projection isn't needed; we use
    # the consume result's own identities for the production path.
    result = observe_same_run_report_only_phase3_research_admission(
        consume_result=consumed,
        h1_selection=selection,
    )

    assert result.status is Status.ADMITTED_REPORT_ONLY
    assert result.qualitative_research_facts is not None
    admitted_unavail = [
        v
        for v in result.qualitative_research_facts.instrument_views
        if v.evidence_status == "UNAVAILABLE"
    ]
    assert len(admitted_unavail) >= 1
    assert admitted_unavail[0].rationale_12m_plus is None
    assert admitted_unavail[0].references == ()


# --------------------------------------------------------------------------
# I. Same content + different evaluation timestamp → cross-run mix → INVALID.
#    This is the "same-run cohesion" oracle.
# --------------------------------------------------------------------------
def test_cross_run_mix_of_qualitative_and_recognition_yields_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Qualitative facts from run A + recognition from run B → INVALID.

    The two consume results use the same source content but different evaluation
    timestamps.  The resulting ``validated_grounded_analysis_response_identity_sha256``
    values differ (they bind the evaluation timestamp), so crossing them proves
    that the identity is used for SAME-RUN cohesion, not stable content identity.
    """
    # Run A: timestamp = PREPARED_TIME_A
    clock_a = type("ClockA", (), {"now_utc": lambda self: _PREPARED_TIME_A})()
    tmp_a = tmp_path / "run_a"
    tmp_a.mkdir()
    consumed_a, _ = _build_hermetic_consume_result(
        tmp_a,
        monkeypatch,
        clock=clock_a,
        response_builder=_response_bytes_all_supported,
    )

    # Run B: timestamp = PREPARED_TIME_B (different timestamp, same content)
    clock_b = type("ClockB", (), {"now_utc": lambda self: _PREPARED_TIME_B})()
    tmp_b = tmp_path / "run_b"
    tmp_b.mkdir()
    consumed_b, _ = _build_hermetic_consume_result(
        tmp_b,
        monkeypatch,
        clock=clock_b,
        response_builder=_response_bytes_all_supported,
    )

    qual_a_id = (
        consumed_a.qualitative_research_facts
        .validated_grounded_analysis_response_identity_sha256
    )
    qual_b_id = (
        consumed_b.qualitative_research_facts
        .validated_grounded_analysis_response_identity_sha256
    )
    # Prove the identities differ even though the response content is identical
    # — they bind the evaluation timestamp transitively.
    assert qual_a_id != qual_b_id, (
        "Expected different run-instance identities for different timestamps"
    )

    # Build a mixed consume result: qualitative from A, recognition from B.
    # This simulates cross-run mixing and must fail.
    mixed = H1ConsumeResult(
        workflow_status="COMPLETED",
        prepared_handoff_identity_sha256=consumed_a.prepared_handoff_identity_sha256,
        mapping_report_identity_sha256=consumed_b.mapping_report_identity_sha256,
        portfolio_snapshot_presence="PRESENT",
        mapped_recognition_facts=consumed_b.mapped_recognition_facts,  # run B
        qualitative_research_facts=consumed_a.qualitative_research_facts,  # run A
    )
    # The selection uses run B's mapping identity (consistent with recognition B).
    selection = _selected_projection(
        mapping_report_identity_sha256=(
            consumed_b.mapped_recognition_facts.mapping_report_identity_sha256
        )
    )

    result = observe_same_run_report_only_phase3_research_admission(
        consume_result=mixed,
        h1_selection=selection,
    )

    assert result.status is Status.INVALID
    assert result.reason_codes == (
        "PHASE3_RESEARCH_ADMISSION_VALIDATED_RESPONSE_IDENTITY_MISMATCH",
    )
    assert result.qualitative_research_facts is None


# --------------------------------------------------------------------------
# J. Result surface has no permission, disposition, sizing, or order fields.
# --------------------------------------------------------------------------
def test_result_surface_has_no_permission_or_disposition_or_sizing_fields() -> None:
    field_names = {
        f.name for f in fields(Phase3SameRunResearchAdmissionObservationResult)
    }
    assert field_names == {
        "schema_version",
        "status",
        "reason_codes",
        "authority_effect",
        "report_only",
        "not_authorization",
        "qualitative_research_facts",
        "validated_grounded_analysis_response_identity_sha256",
        "mapping_report_identity_sha256",
        "selected_state",
        "selected_source",
    }
    forbidden = {
        "allowed_actions",
        "blocked_actions",
        "permission_effect",
        "new_buy_permission",
        "order_compilation_allowed",
        "step4_allowed",
        "final_execution_allowed",
        "broker_automation_allowed",
        "disposition",
        "eligibility",
        "priority",
        "quantity",
        "order",
        "budget",
        "score",
        "rank",
        "increment_cap_basis",
        "target_commitment",
    }
    assert field_names.isdisjoint(forbidden)


# --------------------------------------------------------------------------
# K. Observer signature requires H1ConsumeResult, not standalone qualitative.
#    Static inspection proves no standalone qualitative path exists.
# --------------------------------------------------------------------------
def test_observer_signature_requires_h1_consume_result_not_standalone_qualitative() -> (
    None
):
    """The observer's public API accepts H1ConsumeResult, not individual facts.

    This is a static proof: inspect the observer function's signature and
    verify it has no parameter accepting a standalone H1QualitativeResearchFacts.
    Direct-construction of qualitative facts has no accepted API path to the
    observer without a complete H1ConsumeResult wrapper.
    """
    import inspect
    import typing

    sig = inspect.signature(
        observe_same_run_report_only_phase3_research_admission
    )
    param_names = set(sig.parameters)
    # Required parameter present.
    assert "consume_result" in param_names
    assert "h1_selection" in param_names
    # No standalone qualitative path.
    assert "qualitative_research_facts" not in param_names
    assert "qualitative_facts" not in param_names
    assert "qualitative" not in param_names
    # Confirm annotation for consume_result via get_type_hints (resolves
    # stringified annotations from `from __future__ import annotations`).
    from investment_orchestrator.observability import (
        report_only_phase3_same_run_research_admission as _p2b_module,
    )
    hints = typing.get_type_hints(
        observe_same_run_report_only_phase3_research_admission,
        globalns=vars(_p2b_module),
    )
    assert hints["consume_result"] is H1ConsumeResult


# --------------------------------------------------------------------------
# L. No availability recomputation or artifact read.
# --------------------------------------------------------------------------
def test_observer_reads_no_availability_artifact_and_recomputes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The observer must not call any availability evaluation or read any file."""
    from investment_orchestrator.state import research_availability

    evaluations: list[object] = []
    real_evaluate = research_availability.evaluate_research_availability

    def _spy_evaluate(**kwargs):
        evaluations.append(kwargs)
        return real_evaluate(**kwargs)

    monkeypatch.setattr(
        research_availability, "evaluate_research_availability", _spy_evaluate
    )

    observe_same_run_report_only_phase3_research_admission(
        consume_result=_consume_result(),
        h1_selection=_selected_projection(),
    )

    assert evaluations == [], (
        "Observer must not call evaluate_research_availability"
    )


# --------------------------------------------------------------------------
# M. No persistence / write.
# --------------------------------------------------------------------------
def test_observer_writes_no_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive admission must produce no disk artifact anywhere."""
    writes: list[Path] = []
    real_write_bytes = Path.write_bytes

    def _spy_write(self, data):
        writes.append(self)
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", _spy_write)

    observe_same_run_report_only_phase3_research_admission(
        consume_result=_consume_result(),
        h1_selection=_selected_projection(),
    )

    assert writes == [], f"Observer must not write any file; wrote: {writes}"


# --------------------------------------------------------------------------
# N. No production downstream consumer of P2B observer/result.
# --------------------------------------------------------------------------
def test_no_production_downstream_consumer_of_p2b_observer() -> None:
    """No production module (outside the new P2B module itself) imports it."""
    p2b_module = (
        "investment_orchestrator.observability"
        ".report_only_phase3_same_run_research_admission"
    )
    p2b_file = (
        repo_root()
        / "src/investment_orchestrator/observability"
        / "report_only_phase3_same_run_research_admission.py"
    )

    production_root = repo_root() / "src"
    consumers: list[str] = []
    for path in sorted(production_root.rglob("*.py")):
        if path.resolve() == p2b_file.resolve():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if p2b_module in source or (
            "report_only_phase3_same_run_research_admission" in source
        ):
            consumers.append(
                path.relative_to(repo_root()).as_posix()
            )

    assert consumers == [], (
        f"Unexpected production consumers of P2B observer: {consumers}"
    )


# --------------------------------------------------------------------------
# Extra: result is frozen and carries correct schema version.
# --------------------------------------------------------------------------
def test_result_is_frozen_and_carries_correct_schema_version() -> None:
    result = observe_same_run_report_only_phase3_research_admission(
        consume_result=_consume_result(),
        h1_selection=_selected_projection(),
    )

    assert result.schema_version == (
        "phase3_same_run_research_admission_observation_result_v1"
    )
    with pytest.raises((AttributeError, TypeError)):
        result.status = Status.INVALID  # type: ignore[misc]


# --------------------------------------------------------------------------
# Extra: authority and non-authorization fields are always fixed.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "make_result",
    [
        lambda: observe_same_run_report_only_phase3_research_admission(
            consume_result=_consume_result(),
            h1_selection=_selected_projection(),
        ),
        lambda: observe_same_run_report_only_phase3_research_admission(
            consume_result=_consume_result(),
            h1_selection=None,
        ),
        lambda: observe_same_run_report_only_phase3_research_admission(
            consume_result=_consume_result(),
            h1_selection=_unselected_projection(),
        ),
    ],
    ids=["admitted", "unavailable", "manual_review"],
)
def test_authority_fields_are_always_none_report_only_not_authorization(
    make_result,
) -> None:
    result = make_result()
    assert result.authority_effect == "NONE"
    assert result.report_only is True
    assert result.not_authorization is True
