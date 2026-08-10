"""Mapped-H1 availability recognition: precedence, freshness, and isolation.

Scope note: this module tests what ``research_availability`` owns — freshness,
source selection, Legacy/H1 precedence, and the permission row. The H1 bridge
owns structural / provenance / current-source validity and has its own mutation
suite in ``test_h1_mapped_recognition.py``; that suite is deliberately not
duplicated here. One integration test below uses the real factory to prove the
availability owner consumes genuine factory-created facts.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import pytest

from investment_orchestrator.research.h1_mapped_recognition import (
    H1MappedRecognitionFacts,
)
from investment_orchestrator.state.blocked_run_summary import _STATE_SEVERITY
from investment_orchestrator.state.final_execution_safety_gate import (
    evaluate_final_execution_safety,
)
from investment_orchestrator.state.research_availability import (
    _ALLOWED_ACTIONS_BY_STATE,
    evaluate_research_availability,
    research_availability_result_to_dict,
    research_degraded_mode_decision_to_dict,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    evaluate_step2_research_gate,
)


# --- independently declared expectations -------------------------------------
# Literals, not imports from the production module under test.

H1_STATE = "H1_MAPPED_FRESH_NON_ACTIONABLE"
H1_SOURCE_KIND = "H1_ROLE_MAPPED"
H1_ALLOWED_ACTIONS = ("HOLD", "NO_TRADE")
H1_BLOCKED_ACTIONS = (
    "SELL",
    "NEW_BUY",
    "ROTATION",
    "REBALANCE",
    "EXTENDED_ETF_ADMISSION",
    "ORDER_COMPILATION",
)
PROMOTED_ACTIONS = ("PROMOTED_RESEARCH_DECISION", "PROMOTED_RESEARCH_AUDIT")

NOW = "2026-06-30"

# The complete permission table as it must remain. Declared here so any silent
# change to an existing state's action set fails this module, not just the new row.
EXPECTED_PERMISSION_TABLE: dict[str, tuple[str, ...]] = {
    "STRICT_FRESH": (
        "HOLD",
        "NO_TRADE",
        "SELL",
        "NEW_BUY",
        "ROTATION",
        "REBALANCE",
        "EXTENDED_ETF_ADMISSION",
        "ORDER_COMPILATION",
    ),
    "STRICT_STALE": ("HOLD", "NO_TRADE", "SELL"),
    "STRICT_FRESH_EVIDENCE_ONLY": ("HOLD", "NO_TRADE"),
    "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE": ("HOLD", "NO_TRADE"),
    "STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES": ("HOLD", "NO_TRADE"),
    "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY": (
        "HOLD",
        "NO_TRADE",
        "PROMOTED_RESEARCH_DECISION",
    ),
    "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY": (
        "HOLD",
        "NO_TRADE",
        "PROMOTED_RESEARCH_DECISION",
        "PROMOTED_RESEARCH_AUDIT",
    ),
    "DEGRADED_WITH_LAST_GOOD": ("HOLD", "NO_TRADE"),
    "DEGRADED_NO_RESEARCH": ("HOLD", "NO_TRADE"),
    "INVALID_CONTRACT": ("HOLD", "NO_TRADE"),
    "NO_OUTPUT": ("HOLD", "NO_TRADE"),
    "MANUAL_REVIEW_REQUIRED": ("HOLD", "NO_TRADE"),
    H1_STATE: ("HOLD", "NO_TRADE"),
}


# --- typed-facts helpers ------------------------------------------------------


def h1_facts(
    *,
    policy_as_of_date: str = "2026-06-25",
    portfolio_source_date: str = "2026-06-25",
    policy_source_run_timestamp_utc: str | None = "2026-06-25T09:00:00.000000Z",
    context_evaluation_timestamp_utc: str = "2026-06-25T12:00:00.000000Z",
) -> H1MappedRecognitionFacts:
    """Build typed facts the way the factory does, varying only temporal facts.

    Structural/provenance validity is the bridge's contract and is proven by its
    own suite; here the identity fields only need to be well-formed so the audit
    block can be asserted.
    """
    facts = object.__new__(H1MappedRecognitionFacts)
    for field in fields(H1MappedRecognitionFacts):
        object.__setattr__(facts, field.name, f"{field.name}_value")
    object.__setattr__(facts, "source_kind", H1_SOURCE_KIND)
    object.__setattr__(facts, "mapping_schema_version", "mmi_h1_legacy_step1_mapping_report_v1")
    object.__setattr__(facts, "role_map_version", "h1_legacy_step1_role_map_v1")
    object.__setattr__(facts, "mapping_report_identity_sha256", "a" * 64)
    object.__setattr__(facts, "policy_projection_identity_sha256", "b" * 64)
    object.__setattr__(facts, "policy_as_of_date", policy_as_of_date)
    object.__setattr__(facts, "portfolio_source_date", portfolio_source_date)
    object.__setattr__(
        facts, "policy_source_run_timestamp_utc", policy_source_run_timestamp_utc
    )
    object.__setattr__(
        facts, "context_evaluation_timestamp_utc", context_evaluation_timestamp_utc
    )
    return facts


def strict_fresh_validation() -> dict[str, Any]:
    return {"valid": True}


def evaluate(**overrides: Any) -> Any:
    """Evaluate availability with a no-Legacy baseline unless overridden."""
    base: dict[str, Any] = {
        "candidate_validation": None,
        "candidate": None,
        "strategy_settings": None,
        "source_as_of_date": None,
        "now_date": NOW,
    }
    base.update(overrides)
    return evaluate_research_availability(**base)


# --- permission table ---------------------------------------------------------


def test_permission_table_is_exactly_as_declared() -> None:
    """Complete equality: the new row is closed and no existing row changed."""
    assert _ALLOWED_ACTIONS_BY_STATE == EXPECTED_PERMISSION_TABLE


def test_h1_state_allows_exactly_hold_and_no_trade() -> None:
    assert _ALLOWED_ACTIONS_BY_STATE[H1_STATE] == H1_ALLOWED_ACTIONS
    for action in H1_BLOCKED_ACTIONS + PROMOTED_ACTIONS:
        assert action not in _ALLOWED_ACTIONS_BY_STATE[H1_STATE], action


def test_selected_h1_result_reports_exact_allowed_and_blocked_actions() -> None:
    result = evaluate(h1_mapped_facts=h1_facts())

    assert result.state == H1_STATE
    assert tuple(result.allowed_actions) == H1_ALLOWED_ACTIONS
    assert tuple(result.blocked_actions) == H1_BLOCKED_ACTIONS
    for action in PROMOTED_ACTIONS:
        assert action not in result.allowed_actions


def test_h1_state_is_not_actionable_fresh_and_requires_no_manual_review() -> None:
    result = evaluate(h1_mapped_facts=h1_facts())

    # "FRESH" in the state name must not create actionable semantics.
    assert result.fresh_research_available is False
    assert result.manual_review_required is False
    assert result.source == H1_SOURCE_KIND
    assert result.h1_mapped_selected is True

    for payload in (
        research_availability_result_to_dict(result),
        research_degraded_mode_decision_to_dict(result),
    ):
        assert payload["state"] == H1_STATE
        assert payload["fresh_research_available"] is False
        assert payload["order_compilation_allowed"] is False
        assert payload["new_buy_permission"] is False
        assert payload["permission_effect"] == "none"
        assert payload["report_only"] is True


# --- Legacy / H1 source precedence -------------------------------------------
# Scenario expectations are declared independently of the production precedence
# set: each case states the Legacy inputs and the required winning state.


def test_precedence_a_strict_fresh_legacy_beats_fresh_h1() -> None:
    result = evaluate(
        candidate_validation=strict_fresh_validation(),
        candidate={"handoff": True},
        source_as_of_date="2026-06-28",
        h1_mapped_facts=h1_facts(),
    )

    assert result.state == "STRICT_FRESH"
    assert result.h1_mapped_selected is False
    assert result.source == "raw_research_handoff"
    assert "NEW_BUY" in result.allowed_actions


def test_precedence_b_strict_stale_legacy_beats_fresh_h1_and_keeps_sell() -> None:
    """Fresh H1 must never demote STRICT_STALE or remove its SELL right."""
    without_h1 = evaluate(
        candidate_validation=strict_fresh_validation(),
        candidate={"handoff": True},
        source_as_of_date="2026-06-18",
    )
    with_h1 = evaluate(
        candidate_validation=strict_fresh_validation(),
        candidate={"handoff": True},
        source_as_of_date="2026-06-18",
        h1_mapped_facts=h1_facts(),
    )

    assert without_h1.state == "STRICT_STALE"
    assert with_h1.state == "STRICT_STALE"
    assert "SELL" in with_h1.allowed_actions
    assert with_h1.allowed_actions == without_h1.allowed_actions
    assert with_h1.h1_mapped_selected is False


def test_precedence_c_compiled_evidence_only_legacy_beats_fresh_h1() -> None:
    compiled = {
        "compiled_candidate_validation": strict_fresh_validation(),
        "compiled_metadata": {"compilation_mode": "evidence_only"},
        "compiled_source_as_of_date": NOW,
    }
    result = evaluate(**compiled, h1_mapped_facts=h1_facts())

    assert result.state == "STRICT_FRESH_EVIDENCE_ONLY"
    assert result.h1_mapped_selected is False
    assert result.source == "compiled_research_handoff"


def test_precedence_d_degraded_with_last_good_yields_to_fresh_h1() -> None:
    last_good = {
        "last_good_handoff": {"handoff": True},
        "last_good_metadata": {
            "source_as_of_date": "2026-06-26",
            "strategy_settings_hash": "h",
            "universe": {"core_universe": ["QQQ"]},
        },
        "strategy_settings": {"core_universe": ["QQQ"]},
    }
    without_h1 = evaluate(**last_good)
    with_h1 = evaluate(**last_good, h1_mapped_facts=h1_facts())

    assert without_h1.state == "DEGRADED_WITH_LAST_GOOD"
    assert with_h1.state == H1_STATE
    assert with_h1.h1_mapped_selected is True
    # Legacy LKG bytes are untouched and H1 never becomes last-good: the handoff
    # is still reported as available, it is simply no longer the selected source.
    assert with_h1.last_good_available is True
    assert with_h1.last_good_usable is False
    assert with_h1.last_good_as_of_date == "2026-06-26"


@pytest.mark.parametrize(
    ("label", "legacy_kwargs", "expected_without_h1"),
    [
        ("no_output", {}, "NO_OUTPUT"),
        (
            "degraded_no_research",
            {"parsed_output_available": True},
            "DEGRADED_NO_RESEARCH",
        ),
        (
            "invalid_contract",
            {"candidate": {"handoff": True}, "parsed_output_available": True},
            "INVALID_CONTRACT",
        ),
    ],
)
def test_precedence_e_f_fallback_states_yield_to_fresh_h1(
    label: str,
    legacy_kwargs: dict[str, Any],
    expected_without_h1: str,
) -> None:
    assert evaluate(**legacy_kwargs).state == expected_without_h1

    result = evaluate(**legacy_kwargs, h1_mapped_facts=h1_facts())

    assert result.state == H1_STATE
    assert result.h1_mapped_selected is True
    assert tuple(result.allowed_actions) == H1_ALLOWED_ACTIONS


@pytest.mark.parametrize(
    "h1_candidate",
    [
        None,
        {"source_kind": "H1_ROLE_MAPPED", "policy_as_of_date": "2026-06-25"},
        "H1_ROLE_MAPPED",
        object(),
    ],
    ids=["absent", "raw_dict", "string", "arbitrary_object"],
)
def test_precedence_g_invalid_or_missing_h1_never_changes_legacy(
    h1_candidate: Any,
) -> None:
    """Only factory-created typed facts are consumed; nothing else is.

    An untyped candidate must neither be recognized nor suppress Legacy, so the
    result is identical to a run with no H1 candidate at all.
    """
    legacy = {
        "candidate_validation": strict_fresh_validation(),
        "candidate": {"handoff": True},
        "source_as_of_date": "2026-06-28",
    }
    baseline = evaluate(**legacy)
    result = evaluate(**legacy, h1_mapped_facts=h1_candidate)

    assert result.state == baseline.state == "STRICT_FRESH"
    assert result.allowed_actions == baseline.allowed_actions
    assert result.h1_mapped_selected is False
    assert result.h1_mapped_recognition is None


def test_invalid_h1_does_not_suppress_a_degraded_legacy_result() -> None:
    baseline = evaluate(parsed_output_available=True)
    result = evaluate(parsed_output_available=True, h1_mapped_facts={"not": "typed"})

    assert result.state == baseline.state == "DEGRADED_NO_RESEARCH"
    assert result.h1_mapped_recognition is None


# --- freshness boundaries -----------------------------------------------------
# Policy under test: 0..8 fresh, 9..16 stale, >16 too old, negative fail closed.


@pytest.mark.parametrize(
    ("age_days", "as_of", "expected_freshness", "expected_state"),
    [
        (0, "2026-06-30", "fresh", H1_STATE),
        (8, "2026-06-22", "fresh", H1_STATE),
        (9, "2026-06-21", "stale", "NO_OUTPUT"),
        (16, "2026-06-14", "stale", "NO_OUTPUT"),
        (17, "2026-06-13", "too_old", "MANUAL_REVIEW_REQUIRED"),
        (-1, "2026-07-01", "future_dated", "MANUAL_REVIEW_REQUIRED"),
    ],
    ids=["0d", "8d_boundary", "9d_boundary", "16d_boundary", "17d_boundary", "future"],
)
def test_h1_freshness_boundaries(
    age_days: int,
    as_of: str,
    expected_freshness: str,
    expected_state: str,
) -> None:
    result = evaluate(
        h1_mapped_facts=h1_facts(
            policy_as_of_date=as_of,
            portfolio_source_date=as_of,
            policy_source_run_timestamp_utc=f"{as_of}T09:00:00.000000Z",
            context_evaluation_timestamp_utc=f"{as_of}T12:00:00.000000Z",
        )
    )

    assert result.h1_mapped_recognition["freshness"] == expected_freshness
    assert result.h1_mapped_recognition["age_days"] == age_days
    assert result.state == expected_state
    # Stale H1 must never become STRICT_STALE and must never gain SELL.
    assert result.state != "STRICT_STALE"
    assert "SELL" not in result.allowed_actions
    assert result.h1_mapped_selected is (expected_state == H1_STATE)


def test_oldest_required_fact_controls_freshness() -> None:
    """A newer source never compensates for an older required source."""
    result = evaluate(
        h1_mapped_facts=h1_facts(
            policy_as_of_date="2026-06-29",  # 1d
            portfolio_source_date="2026-06-18",  # 12d -> stale, controls
            policy_source_run_timestamp_utc="2026-06-29T09:00:00.000000Z",
            context_evaluation_timestamp_utc="2026-06-29T12:00:00.000000Z",
        )
    )

    recognition = result.h1_mapped_recognition
    assert recognition["age_days"] == 12
    assert recognition["freshness"] == "stale"
    assert recognition["required_fact_ages_days"]["policy_as_of_date"] == 1
    assert recognition["required_fact_ages_days"]["portfolio_source_date"] == 12
    assert result.state == "NO_OUTPUT"
    assert result.h1_mapped_selected is False


def test_optional_policy_run_timestamp_participates_when_present() -> None:
    """When present the optional fact can only make the run look older."""
    result = evaluate(
        h1_mapped_facts=h1_facts(
            policy_as_of_date="2026-06-29",
            portfolio_source_date="2026-06-29",
            policy_source_run_timestamp_utc="2026-06-17T09:00:00.000000Z",  # 13d
            context_evaluation_timestamp_utc="2026-06-29T12:00:00.000000Z",
        )
    )

    assert result.h1_mapped_recognition["age_days"] == 13
    assert result.h1_mapped_recognition["freshness"] == "stale"
    assert result.state == "NO_OUTPUT"


def test_absent_optional_policy_run_timestamp_is_omitted_not_substituted() -> None:
    """Omitting the optional fact must not borrow the context/clock/mapping time."""
    result = evaluate(
        h1_mapped_facts=h1_facts(
            policy_as_of_date="2026-06-24",
            portfolio_source_date="2026-06-24",
            policy_source_run_timestamp_utc=None,
            context_evaluation_timestamp_utc="2026-06-24T12:00:00.000000Z",
        )
    )

    recognition = result.h1_mapped_recognition
    assert "policy_source_run_timestamp_utc" not in recognition["required_fact_ages_days"]
    assert set(recognition["required_fact_ages_days"]) == {
        "policy_as_of_date",
        "portfolio_source_date",
    }
    assert recognition["age_days"] == 6
    assert recognition["freshness"] == "fresh"
    assert result.state == H1_STATE
    assert recognition["temporal_evidence"]["policy_source_run_timestamp_utc"] is None


def test_future_dated_context_fails_closed_even_when_sources_are_fresh() -> None:
    """The context timestamp serves only the conservative integrity check."""
    result = evaluate(
        h1_mapped_facts=h1_facts(
            policy_as_of_date="2026-06-28",
            portfolio_source_date="2026-06-28",
            policy_source_run_timestamp_utc="2026-06-28T09:00:00.000000Z",
            context_evaluation_timestamp_utc="2026-07-05T12:00:00.000000Z",
        )
    )

    assert result.h1_mapped_recognition["freshness"] == "future_dated"
    assert result.state == "MANUAL_REVIEW_REQUIRED"
    assert result.manual_review_required is True
    assert result.h1_mapped_selected is False


def test_unknown_age_is_never_fresh() -> None:
    result = evaluate(now_date=None, h1_mapped_facts=h1_facts())

    assert result.h1_mapped_recognition["freshness"] == "unknown"
    assert result.h1_mapped_recognition["age_days"] is None
    assert result.h1_mapped_selected is False
    assert result.state == "NO_OUTPUT"


@pytest.mark.parametrize("as_of", ["2026-06-13", "2026-07-01"], ids=["too_old", "future"])
def test_defective_h1_never_escalates_a_protected_legacy_result(as_of: str) -> None:
    """A protected Legacy state keeps its state and actions; H1 is diagnostic only."""
    legacy = {
        "candidate_validation": strict_fresh_validation(),
        "candidate": {"handoff": True},
        "source_as_of_date": "2026-06-28",
    }
    baseline = evaluate(**legacy)
    result = evaluate(
        **legacy,
        h1_mapped_facts=h1_facts(
            policy_as_of_date=as_of,
            portfolio_source_date=as_of,
            policy_source_run_timestamp_utc=f"{as_of}T09:00:00.000000Z",
            context_evaluation_timestamp_utc=f"{as_of}T12:00:00.000000Z",
        ),
    )

    assert result.state == baseline.state == "STRICT_FRESH"
    assert result.allowed_actions == baseline.allowed_actions
    assert result.manual_review_required is False
    assert any("h1_mapped_research" in reason for reason in result.non_blocker_reasons)


# --- artifact / compatibility -------------------------------------------------


def test_legacy_artifacts_are_unchanged_when_no_h1_candidate() -> None:
    """No H1 key may appear in any artifact for a Legacy run — no migration."""
    legacy = {
        "candidate_validation": strict_fresh_validation(),
        "candidate": {"handoff": True},
        "source_as_of_date": "2026-06-28",
    }
    baseline = research_availability_result_to_dict(evaluate(**legacy))
    decision = research_degraded_mode_decision_to_dict(evaluate(**legacy))

    for payload in (baseline, decision):
        assert [key for key in payload if key.startswith("h1_")] == []


def test_h1_diagnostics_are_not_injected_when_h1_is_not_selected() -> None:
    """Present-but-unselected H1 leaves Legacy serialization byte-identical."""
    legacy = {
        "candidate_validation": strict_fresh_validation(),
        "candidate": {"handoff": True},
        "source_as_of_date": "2026-06-28",
    }
    without_h1 = research_availability_result_to_dict(evaluate(**legacy))
    with_h1 = research_availability_result_to_dict(
        evaluate(**legacy, h1_mapped_facts=h1_facts())
    )

    assert with_h1 == without_h1


def test_selected_h1_artifact_carries_minimum_audit_provenance() -> None:
    payload = research_availability_result_to_dict(evaluate(h1_mapped_facts=h1_facts()))

    assert payload["h1_mapped_selected"] is True
    assert payload["h1_mapped_source_kind"] == H1_SOURCE_KIND
    assert payload["h1_mapped_freshness"] == "fresh"
    assert payload["h1_mapped_age_days"] == 5
    assert payload["h1_mapped_identity"]["role_map_version"] == "h1_legacy_step1_role_map_v1"
    assert payload["h1_mapped_identity"]["mapping_report_identity_sha256"] == "a" * 64
    assert (
        payload["h1_mapped_current_source_identities"]["policy_projection_identity_sha256"]
        == "b" * 64
    )
    assert payload["h1_mapped_temporal_evidence"]["policy_as_of_date"] == "2026-06-25"
    # Not a recognition receipt: the full bridge fact set is deliberately absent.
    serialized = str(payload)
    for field in fields(H1MappedRecognitionFacts):
        if field.name in {
            "raw_response_sha256",
            "evidence_bundle_identity_sha256",
            "grounded_prompt_artifact_identity_sha256",
            "prompt_context_binding_sha256",
            "raw_response_envelope_identity_sha256",
        }:
            assert field.name not in serialized, field.name


# --- observability ------------------------------------------------------------


def test_h1_state_has_benign_severity_matching_other_fresh_non_order_states() -> None:
    assert _STATE_SEVERITY[H1_STATE] == _STATE_SEVERITY["STRICT_FRESH_EVIDENCE_ONLY"]
    assert _STATE_SEVERITY[H1_STATE] > _STATE_SEVERITY["STRICT_FRESH"]
    assert _STATE_SEVERITY[H1_STATE] < _STATE_SEVERITY["STRICT_STALE"]


# --- downstream authority isolation ------------------------------------------


def h1_permission_artifact() -> dict[str, Any]:
    """The permission decision a selected-H1 run actually writes."""
    return research_degraded_mode_decision_to_dict(evaluate(h1_mapped_facts=h1_facts()))


def test_h1_state_cannot_enter_step2_render_or_parse() -> None:
    gate = evaluate_step2_research_gate(h1_permission_artifact())

    assert gate.allowed is False
    assert gate.mode == "blocked"
    assert gate.step3_allowed is False
    assert gate.step4_allowed is False
    assert gate.order_compilation_allowed is False
    assert gate.new_buy_permission is False
    assert any("is not STRICT_FRESH" in reason for reason in gate.blocker_reasons)


def test_h1_state_cannot_enter_step3() -> None:
    """The committed Step 3 admission blocks H1 with no H1-specific branch."""
    gate = evaluate_step2_research_gate(h1_permission_artifact())

    # Step 3 admits only the STRICT_FRESH actionable gate result.
    assert not (gate.allowed and gate.mode == "strict_fresh_actionable" and gate.step3_allowed)


def test_h1_state_cannot_satisfy_final_safety_or_produce_order_readiness() -> None:
    result = evaluate_final_execution_safety(
        step1_permission=h1_permission_artifact(),
        step2_decision_packet={
            "buy_side_delta_table": [{"ticker": "QQQ", "action": "NEW_BUY"}],
        },
        step3_audited_packet={
            "audit_passed": True,
            "order_compiler_ready": True,
            "final_buy_side_delta_table": [],
            "final_sell_side_delta_table": [],
            "final_execution_plans": [],
            "final_sell_execution_plans": [],
        },
    )

    assert result.ready_for_order_compilation is False
    assert result.checked_conditions["step1_state_strict_fresh"] is False
    assert result.checked_conditions["order_compilation_allowed"] is False


def test_selected_h1_writes_no_lkg_publication_pointer_or_order_field() -> None:
    """The H1 decision artifact carries no last-good, pointer, or order authority."""
    payload = h1_permission_artifact()

    assert payload["order_compilation_allowed"] is False
    assert payload["new_buy_permission"] is False
    assert payload["promoted_step2_decision_only"] is False
    assert payload["promoted_step3_audit_only"] is False
    assert payload["promoted_pointer_present"] is False
    assert payload["promoted_pointer_valid"] is False
    assert payload["promotion_status"] is None
    assert payload["effective_handoff_present"] is False
    assert payload["actionable_this_run_tickers"] == []
    assert payload["source_artifacts"] == {}


# --- real-factory integration -------------------------------------------------


def test_availability_consumes_real_factory_created_facts(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """One representative proof against genuine bridge output.

    The bridge's provenance mutation matrix is not duplicated here; this only
    shows the availability owner accepts real factory facts and classifies them
    with its own freshness policy.
    """
    from test_h1_mapped_recognition import _build, _inputs

    facts = _build(_inputs(tmp_path_factory))
    assert isinstance(facts, H1MappedRecognitionFacts)
    assert facts.source_kind == H1_SOURCE_KIND

    # The canonical fixture evaluates at 2026-07-31; use its own context date.
    now = facts.context_evaluation_timestamp_utc[:10]
    result = evaluate(now_date=now, h1_mapped_facts=facts)

    assert result.state == H1_STATE
    assert result.h1_mapped_selected is True
    assert tuple(result.allowed_actions) == H1_ALLOWED_ACTIONS
    assert result.fresh_research_available is False
    recognition = result.h1_mapped_recognition
    assert recognition["freshness"] == "fresh"
    assert (
        recognition["identity"]["mapping_report_identity_sha256"]
        == facts.mapping_report_identity_sha256
    )
    assert (
        recognition["temporal_evidence"]["policy_as_of_date"] == facts.policy_as_of_date
    )
