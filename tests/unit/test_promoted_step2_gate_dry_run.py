"""R2E.5b-6b dry-run evaluator tests.

The dry-run simulates the FUTURE promoted Step 2 decision-only gate. It is
report-only: ``would_allow_step2_promoted_decision: true`` is diagnostic, never
permission; ``current_real_gate_allows`` stays false; and the evaluator never
raises on malformed input.
"""

from __future__ import annotations

from typing import Any

from investment_orchestrator.research.actionable_promotion_pointer import (
    PERMISSION_EFFECT_PENDING_GATES,
    PROMOTION_STATUS_PENDING_GATES,
)
from investment_orchestrator.research.promoted_handoff_verifier import (
    FUTURE_PERMISSION_REQUIRED,
    SCHEMA_VERSION as VERIFICATION_SCHEMA_VERSION,
)
from investment_orchestrator.research.promoted_step2_gate_dry_run import (
    DRY_RUN_BLOCKER_DECISION_ACTIONS_NOT_HOLD_NO_TRADE,
    DRY_RUN_BLOCKER_DECISION_MISSING,
    DRY_RUN_BLOCKER_DECISION_STATE_NOT_PENDING_GATES,
    DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY,
    DRY_RUN_BLOCKER_REASON_CODES,
    DRY_RUN_BLOCKER_VERIFICATION_INVALID,
    DRY_RUN_BLOCKER_VERIFICATION_MARKERS_INVALID,
    DRY_RUN_BLOCKER_VERIFICATION_MISSING,
    DRY_RUN_BLOCKER_VERIFICATION_PERMISSION_MISMATCH,
    FUTURE_STATE_REQUIRED,
    SCHEMA_VERSION,
    evaluate_promoted_step2_gate_dry_run,
)
from investment_orchestrator.state.research_availability import (
    STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
)


def pending_gates_decision(**overrides: Any) -> dict[str, Any]:
    decision = {
        "state": STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
        "allowed_actions": ["HOLD", "NO_TRADE"],
        "blocked_actions": [
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "manual_review_required": False,
        "blocker_reasons": ["promoted_actionable_handoff_pending_gates"],
        "report_only": True,
    }
    decision.update(overrides)
    return decision


def valid_verification(**overrides: Any) -> dict[str, Any]:
    verification = {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "is_llm_generated": False,
        "valid_for_step2_decision": True,
        "verification_blockers": [],
        "verification_warnings": [],
        "checks": [{"check_id": "pointer_present", "passed": True}],
        "promotion_status": PROMOTION_STATUS_PENDING_GATES,
        "pointer_permission_effect": PERMISSION_EFFECT_PENDING_GATES,
        "permission_effect": "none",
        "not_authorization": True,
        "consumed_by_step2": False,
        "future_permission_required": FUTURE_PERMISSION_REQUIRED,
        "report_only": True,
    }
    verification.update(overrides)
    return verification


def evaluate(
    *,
    decision: Any = "default",
    verification: Any = "default",
) -> dict[str, Any]:
    return dict(
        evaluate_promoted_step2_gate_dry_run(
            research_decision=pending_gates_decision() if decision == "default" else decision,
            promoted_verification=(
                valid_verification() if verification == "default" else verification
            ),
        )
    )


def assert_report_only_markers(result: dict[str, Any]) -> None:
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["is_llm_generated"] is False
    assert result["report_only"] is True
    assert result["permission_effect"] == "none"
    assert result["not_authorization"] is True
    assert result["dry_run_only"] is True
    assert result["consumed_by_step2"] is False
    assert result["consumed_by_gates"] is False
    assert result["future_permission_required"] == FUTURE_PERMISSION_REQUIRED
    assert result["future_state_required"] == FUTURE_STATE_REQUIRED


def assert_blocked(result: dict[str, Any], reason: str) -> None:
    assert result["would_allow_step2_promoted_decision"] is False
    assert reason in result["dry_run_blockers"]
    assert any(
        check["reason_code"] == reason and check["passed"] is False for check in result["checks"]
    )
    assert_report_only_markers(result)


# --- diagnostic pass: would allow, but the real gate stays closed --------------


def test_pending_gates_decision_with_valid_verification_would_allow_but_real_gate_closed() -> None:
    result = evaluate()

    assert result["would_allow_step2_promoted_decision"] is True
    assert result["current_real_gate_allows"] is False
    assert result["verification_valid_for_step2_decision"] is True
    assert result["current_state"] == STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES
    assert result["current_allowed_actions"] == ["HOLD", "NO_TRADE"]
    # The policy blocker is present even on a diagnostic pass, so the artifact
    # can never be read as an actual Step 2 render permission.
    assert result["dry_run_blockers"] == [DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY]
    assert_report_only_markers(result)


# --- decision-side blockers -----------------------------------------------------


def test_missing_decision_blocks() -> None:
    assert_blocked(evaluate(decision=None), DRY_RUN_BLOCKER_DECISION_MISSING)


def test_non_pending_state_blocks() -> None:
    result = evaluate(decision=pending_gates_decision(state="STRICT_FRESH_EVIDENCE_ONLY"))
    assert_blocked(result, DRY_RUN_BLOCKER_DECISION_STATE_NOT_PENDING_GATES)


def test_strict_fresh_state_blocks_dry_run_and_is_not_promoted_path() -> None:
    # Even a fully actionable raw STRICT_FRESH decision is NOT the promoted
    # pending-gates posture this dry-run simulates.
    result = evaluate(
        decision=pending_gates_decision(
            state="STRICT_FRESH",
            allowed_actions=["HOLD", "NO_TRADE", "SELL", "NEW_BUY", "ORDER_COMPILATION"],
        )
    )
    assert_blocked(result, DRY_RUN_BLOCKER_DECISION_STATE_NOT_PENDING_GATES)
    assert_blocked(result, DRY_RUN_BLOCKER_DECISION_ACTIONS_NOT_HOLD_NO_TRADE)


def test_allowed_actions_not_exactly_hold_no_trade_blocks() -> None:
    for allowed_actions in (
        ["HOLD"],
        ["HOLD", "NO_TRADE", "SELL"],
        ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"],
        [],
        "HOLD",
    ):
        result = evaluate(decision=pending_gates_decision(allowed_actions=allowed_actions))
        assert_blocked(result, DRY_RUN_BLOCKER_DECISION_ACTIONS_NOT_HOLD_NO_TRADE)


# --- verification-side blockers ---------------------------------------------------


def test_missing_verification_blocks() -> None:
    assert_blocked(evaluate(verification=None), DRY_RUN_BLOCKER_VERIFICATION_MISSING)


def test_invalid_verification_blocks() -> None:
    result = evaluate(verification=valid_verification(valid_for_step2_decision=False))
    assert_blocked(result, DRY_RUN_BLOCKER_VERIFICATION_INVALID)


def test_verification_with_blockers_blocks() -> None:
    result = evaluate(
        verification=valid_verification(verification_blockers=["promotion_expired"])
    )
    assert_blocked(result, DRY_RUN_BLOCKER_VERIFICATION_INVALID)


def test_verification_permission_mismatch_blocks() -> None:
    result = evaluate(verification=valid_verification(future_permission_required="NEW_BUY"))
    assert_blocked(result, DRY_RUN_BLOCKER_VERIFICATION_PERMISSION_MISMATCH)


def test_verification_markers_invalid_blocks() -> None:
    for overrides in (
        {"report_only": False},
        {"permission_effect": "actionable"},
        {"not_authorization": False},
        {"schema_version": "unexpected"},
        {"promotion_status": "consumed"},
        {"pointer_permission_effect": "none"},
        {"consumed_by_step2": True},
        {"is_llm_generated": True},
    ):
        result = evaluate(verification=valid_verification(**overrides))
        assert_blocked(result, DRY_RUN_BLOCKER_VERIFICATION_MARKERS_INVALID)


# --- fail-closed behavior ----------------------------------------------------------


def test_malformed_inputs_never_raise_and_fail_closed() -> None:
    for decision, verification in (
        (None, None),
        ("bad", "bad"),
        (["list"], ["list"]),
        ({"state": 7, "allowed_actions": 3}, {"valid_for_step2_decision": "yes"}),
        ({"unjsonable": object()}, {"checks": object()}),
    ):
        result = dict(
            evaluate_promoted_step2_gate_dry_run(
                research_decision=decision,  # type: ignore[arg-type]
                promoted_verification=verification,  # type: ignore[arg-type]
            )
        )
        assert result["would_allow_step2_promoted_decision"] is False
        assert result["current_real_gate_allows"] is False
        assert result["dry_run_blockers"]
        assert_report_only_markers(result)


def test_real_gate_policy_blocker_always_present_while_gate_closed() -> None:
    for result in (evaluate(), evaluate(decision=None), evaluate(verification=None)):
        assert result["current_real_gate_allows"] is False
        assert DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY in result["dry_run_blockers"]


def test_blocker_reason_codes_cover_expected_contract() -> None:
    assert DRY_RUN_BLOCKER_REASON_CODES == (
        "decision_missing",
        "decision_state_not_pending_gates",
        "decision_actions_not_hold_no_trade",
        "verification_missing",
        "verification_invalid",
        "verification_permission_mismatch",
        "verification_markers_invalid",
        "real_gate_still_closed_by_policy",
    )


def test_dry_run_true_from_real_verifier_output_still_no_real_gate() -> None:
    """End-to-end coupling: a real R2E.5b-6a verifier pass feeds the dry-run."""
    import hashlib
    import json
    from datetime import date

    from investment_orchestrator.research.actionable_handoff_candidate import (
        CANDIDATE_SCHEMA_VERSION,
    )
    from investment_orchestrator.research.actionable_promotion_pointer import (
        POINTER_SOURCE,
        SCHEMA_VERSION as ACTIVE_POINTER_SCHEMA_VERSION,
    )
    from investment_orchestrator.research.promoted_handoff_verifier import (
        verify_promoted_handoff_for_step2_decision,
    )

    effective = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "is_llm_generated": False,
        "trade_universe": {"allowed_buy_tickers": ["QQQ", "VOO"]},
        "optional_extended_etf_sleeve": {"enabled": False},
        "buy_universe_scorecard": [
            {"ticker": "QQQ", "actionability_status": "actionable_this_run"}
        ],
        "strategy_a_research_handoff": {"positive_delta_research_supported": ["QQQ"]},
    }
    digest = hashlib.sha256(
        json.dumps(effective, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    pointer = {
        "schema_version": ACTIVE_POINTER_SCHEMA_VERSION,
        "source": POINTER_SOURCE,
        "promotion_status": PROMOTION_STATUS_PENDING_GATES,
        "permission_effect": PERMISSION_EFFECT_PENDING_GATES,
        "not_authorization": True,
        "future_pr_required": True,
        "consumed_by_availability": False,
        "consumed_by_step2": False,
        "consumed_by_gates": False,
        "candidate_actionable_row_count": 1,
        "actionable_this_run_tickers": ["QQQ"],
        "promotion_expires_at": "2026-07-31",
        "effective_handoff_sha256": digest,
        "candidate_sha256": digest,
    }
    verification = verify_promoted_handoff_for_step2_decision(
        active_pointer=pointer,
        effective_handoff=effective,
        effective_validation={"valid": True},
        today=date(2026, 6, 28),
    )
    assert verification["valid_for_step2_decision"] is True

    result = dict(
        evaluate_promoted_step2_gate_dry_run(
            research_decision=pending_gates_decision(),
            promoted_verification=verification,
        )
    )
    assert result["would_allow_step2_promoted_decision"] is True
    assert result["current_real_gate_allows"] is False
    assert DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY in result["dry_run_blockers"]
    assert_report_only_markers(result)


def test_upgraded_decision_only_state_cannot_satisfy_dry_run_upgrade_criteria() -> None:
    """R2E.5b-6c guard: a dry-run computed against an ALREADY-upgraded decision
    fails the pending-gates criterion, and (because the real gate now allows the
    decision-only mode) records current_real_gate_allows=True without the policy
    blocker — so such an artifact can never satisfy the availability upgrade
    criteria (which require would_allow=true, current_real_gate_allows=false,
    and the policy blocker present)."""
    upgraded_decision = pending_gates_decision(
        state="STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY",
        allowed_actions=["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"],
        source="promoted_compiled_actionable_handoff",
        promoted_step2_decision_only=True,
    )
    result = evaluate(decision=upgraded_decision)

    assert result["would_allow_step2_promoted_decision"] is False
    assert DRY_RUN_BLOCKER_DECISION_STATE_NOT_PENDING_GATES in result["dry_run_blockers"]
    assert DRY_RUN_BLOCKER_DECISION_ACTIONS_NOT_HOLD_NO_TRADE in result["dry_run_blockers"]
    # The real gate DOES allow this decision (decision-only mode)...
    assert result["current_real_gate_allows"] is True
    assert DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY not in result["dry_run_blockers"]
    # ...which is exactly why the availability upgrade criteria reject it.
    assert_report_only_markers(result)
