"""PR-P2A: the narrow, immutable H1 selection projection off availability's
already-computed ``ResearchAvailabilityResult``.

Scope note: this module tests only the projector added for P2A —
``build_h1_mapped_research_selection_projection`` and
``H1MappedResearchSelectionProjection``. It does not retest H1 selection
precedence/freshness (``test_h1_mapped_availability.py``) or the refresh
threading seam (``test_h1_p3_availability_lifecycle.py``).
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import pytest

from investment_orchestrator.state.research_availability import (
    H1MappedResearchSelectionContractError,
    H1MappedResearchSelectionProjection,
    H1_MAPPED_FRESH_NON_ACTIONABLE,
    H1_ROLE_MAPPED_SOURCE,
    ResearchAvailabilityResult,
    build_h1_mapped_research_selection_projection,
    evaluate_research_availability,
)


IDENTITY = "a" * 64

_REQUIRED_RESULT_FIELDS: dict[str, Any] = {
    "state": "MANUAL_REVIEW_REQUIRED",
    "research_availability": "manual_review_required",
    "fresh_research_available": False,
    "handoff_valid": False,
    "handoff_stale": False,
    "handoff_age_days": None,
    "stale_label": "unknown",
    "last_good_available": False,
    "last_good_usable": False,
    "last_good_age_days": None,
    "settings_hash_match": None,
    "universe_match": None,
    "allowed_actions": ["HOLD", "NO_TRADE"],
    "blocked_actions": ["SELL", "NEW_BUY"],
    "manual_review_required": True,
}


def _result(**overrides: Any) -> ResearchAvailabilityResult:
    """Construct a bare ``ResearchAvailabilityResult`` for projector unit tests.

    Deliberately bypasses ``evaluate_research_availability`` so section G below
    can express an owner-internal contradiction that production evaluation can
    never reach.
    """
    kwargs = dict(_REQUIRED_RESULT_FIELDS)
    kwargs.update(overrides)
    return ResearchAvailabilityResult(**kwargs)


def _h1_recognition(*, identity: Any = IDENTITY) -> dict[str, Any]:
    return {
        "source_kind": "H1_ROLE_MAPPED",
        "freshness": "fresh",
        "age_days": 1,
        "required_fact_ages_days": {},
        "identity": {
            "mapping_schema_version": "mmi_h1_legacy_step1_mapping_report_v1",
            "mapping_report_identity_sha256": identity,
            "role_map_version": "h1_legacy_step1_role_map_v1",
            "target_legacy_validator_contract_version": "v1",
        },
        "current_source_identities": {},
        "temporal_evidence": {},
    }


# --- A. positive selected projection ------------------------------------------


def test_positive_selection_projects_exact_fields() -> None:
    result = _result(
        state=H1_MAPPED_FRESH_NON_ACTIONABLE,
        research_availability=H1_MAPPED_FRESH_NON_ACTIONABLE.lower(),
        allowed_actions=["HOLD", "NO_TRADE"],
        blocked_actions=["SELL", "NEW_BUY"],
        source=H1_ROLE_MAPPED_SOURCE,
        h1_mapped_recognition=_h1_recognition(),
        h1_mapped_selected=True,
    )

    projection = build_h1_mapped_research_selection_projection(result)

    assert projection.h1_mapped_selected is True
    assert projection.state == H1_MAPPED_FRESH_NON_ACTIONABLE
    assert projection.source == H1_ROLE_MAPPED_SOURCE
    assert projection.mapping_report_identity_sha256 == IDENTITY
    assert projection.report_only is True
    assert projection.not_authorization is True
    assert projection.authority_effect == "NONE"
    assert projection.schema_version == "h1_mapped_research_selection_projection_v1"


def test_positive_selection_via_real_evaluator(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Integration: the real evaluator's selected output projects cleanly."""
    from test_h1_mapped_recognition import _build, _inputs

    facts = _build(_inputs(tmp_path_factory))
    now = facts.context_evaluation_timestamp_utc[:10]
    result = evaluate_research_availability(
        candidate_validation=None,
        candidate=None,
        strategy_settings=None,
        source_as_of_date=None,
        now_date=now,
        h1_mapped_facts=facts,
    )
    assert result.h1_mapped_selected is True  # sanity: precondition for this test

    projection = build_h1_mapped_research_selection_projection(result)

    assert projection.h1_mapped_selected is True
    assert projection.state == H1_MAPPED_FRESH_NON_ACTIONABLE
    assert projection.source == H1_ROLE_MAPPED_SOURCE
    assert projection.mapping_report_identity_sha256 == facts.mapping_report_identity_sha256


# --- B. negative/unselected projection -----------------------------------------


@pytest.mark.parametrize(
    ("state", "source"),
    [
        ("STRICT_FRESH", "raw_research_handoff"),
        ("INVALID_CONTRACT", "raw_research_handoff"),
        ("DEGRADED_WITH_LAST_GOOD", "raw_research_handoff"),
        ("STRICT_FRESH_EVIDENCE_ONLY", "compiled_research_handoff"),
    ],
)
def test_negative_selection_preserves_state_and_source_but_no_identity(
    state: str, source: str
) -> None:
    result = _result(
        state=state,
        research_availability=state.lower(),
        source=source,
        h1_mapped_recognition=None,
        h1_mapped_selected=False,
    )

    projection = build_h1_mapped_research_selection_projection(result)

    assert projection.h1_mapped_selected is False
    assert projection.state == state
    assert projection.source == source
    # No positive candidate identity is usable for a future P2B consumer.
    assert projection.mapping_report_identity_sha256 is None


def test_negative_selection_omits_identity_even_when_diagnostic_recognition_present() -> None:
    """An unselected (stale/future-dated) H1 candidate must never leak identity."""
    result = _result(
        state="MANUAL_REVIEW_REQUIRED",
        research_availability="manual_review_required",
        source="raw_research_handoff",
        h1_mapped_recognition=_h1_recognition(),
        h1_mapped_selected=False,
    )

    projection = build_h1_mapped_research_selection_projection(result)

    assert projection.h1_mapped_selected is False
    assert projection.mapping_report_identity_sha256 is None


# --- C. no permission vector / no mutable recognition container ----------------


def test_projection_surface_has_no_permission_or_mutable_fields() -> None:
    field_names = {f.name for f in fields(H1MappedResearchSelectionProjection)}
    assert field_names == {
        "schema_version",
        "h1_mapped_selected",
        "state",
        "source",
        "mapping_report_identity_sha256",
        "report_only",
        "not_authorization",
        "authority_effect",
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
        "h1_mapped_recognition",
    }
    assert field_names.isdisjoint(forbidden)


# --- D. frozen -------------------------------------------------------------


def test_projection_is_frozen() -> None:
    result = _result(
        state="STRICT_FRESH",
        research_availability="strict_fresh",
        source="raw_research_handoff",
        h1_mapped_recognition=None,
        h1_mapped_selected=False,
    )
    projection = build_h1_mapped_research_selection_projection(result)

    with pytest.raises(AttributeError):
        projection.state = "MANUAL_REVIEW_REQUIRED"  # type: ignore[misc]


# --- E / F. shallow-freeze isolation --------------------------------------


def test_mutating_original_allowed_actions_after_construction_leaves_projection_unchanged() -> None:
    allowed_actions = ["HOLD", "NO_TRADE"]
    result = _result(
        state=H1_MAPPED_FRESH_NON_ACTIONABLE,
        research_availability=H1_MAPPED_FRESH_NON_ACTIONABLE.lower(),
        allowed_actions=allowed_actions,
        source=H1_ROLE_MAPPED_SOURCE,
        h1_mapped_recognition=_h1_recognition(),
        h1_mapped_selected=True,
    )
    projection = build_h1_mapped_research_selection_projection(result)

    allowed_actions.append("NEW_BUY")
    result.allowed_actions.append("ORDER_COMPILATION")

    assert projection.h1_mapped_selected is True
    assert projection.state == H1_MAPPED_FRESH_NON_ACTIONABLE
    assert projection.source == H1_ROLE_MAPPED_SOURCE
    assert projection.mapping_report_identity_sha256 == IDENTITY
    assert not hasattr(projection, "allowed_actions")


def test_mutating_original_recognition_dict_after_construction_leaves_projection_unchanged() -> None:
    recognition = _h1_recognition()
    result = _result(
        state=H1_MAPPED_FRESH_NON_ACTIONABLE,
        research_availability=H1_MAPPED_FRESH_NON_ACTIONABLE.lower(),
        source=H1_ROLE_MAPPED_SOURCE,
        h1_mapped_recognition=recognition,
        h1_mapped_selected=True,
    )
    projection = build_h1_mapped_research_selection_projection(result)

    recognition["identity"]["mapping_report_identity_sha256"] = "f" * 64
    recognition["source_kind"] = "TAMPERED"

    assert projection.mapping_report_identity_sha256 == IDENTITY
    assert not hasattr(projection, "h1_mapped_recognition")


# --- G. impossible owner-internal contradiction fails closed --------------


@pytest.mark.parametrize(
    "overrides",
    [
        # Claims selected but state disagrees.
        {"state": "STRICT_FRESH", "source": H1_ROLE_MAPPED_SOURCE},
        # Claims selected but source disagrees.
        {"state": H1_MAPPED_FRESH_NON_ACTIONABLE, "source": "raw_research_handoff"},
        # Claims selected but recognition is entirely absent.
        {
            "state": H1_MAPPED_FRESH_NON_ACTIONABLE,
            "source": H1_ROLE_MAPPED_SOURCE,
            "h1_mapped_recognition": None,
        },
        # Claims selected but the identity is malformed (not sha256 hex).
        {
            "state": H1_MAPPED_FRESH_NON_ACTIONABLE,
            "source": H1_ROLE_MAPPED_SOURCE,
            "h1_mapped_recognition": _h1_recognition(identity="not-a-hash"),
        },
        # Claims selected but the identity is missing entirely.
        {
            "state": H1_MAPPED_FRESH_NON_ACTIONABLE,
            "source": H1_ROLE_MAPPED_SOURCE,
            "h1_mapped_recognition": _h1_recognition(identity=None),
        },
    ],
    ids=["state_mismatch", "source_mismatch", "recognition_absent", "identity_malformed", "identity_missing"],
)
def test_impossible_selected_contradiction_fails_closed(overrides: dict[str, Any]) -> None:
    base = {
        "research_availability": "irrelevant",
        "allowed_actions": ["HOLD", "NO_TRADE"],
        "blocked_actions": ["SELL", "NEW_BUY"],
        "h1_mapped_recognition": _h1_recognition(),
        "h1_mapped_selected": True,
    }
    base.update(overrides)
    result = _result(**base)

    with pytest.raises(H1MappedResearchSelectionContractError):
        build_h1_mapped_research_selection_projection(result)
