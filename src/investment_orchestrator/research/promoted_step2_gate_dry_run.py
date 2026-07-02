"""Report-only DRY-RUN of the Step 2 promoted decision-only gate.

R2E.5b-6b: this module simulates whether the ``PROMOTED_RESEARCH_DECISION``
Step 2 decision-only gate WOULD pass for the current promoted handoff, judged
from the PRE-UPGRADE (pending-gates) posture. The evaluator itself grants
nothing: ``would_allow_step2_promoted_decision: true`` is a diagnostic verdict,
not permission, and this module never changes any gate, workflow, prompt, or
the order compiler.

Since R2E.5b-6c, the availability evaluator consumes this artifact (together
with the R2E.5b-6a verification) as one input of the fail-closed upgrade to
``STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY`` — a state that permits
Step 2 decision-only and still no ``NEW_BUY`` / ``ORDER_COMPILATION``. For the
pending-gates posture this dry-run diagnoses, the real Step 2 gate still blocks
(``current_real_gate_allows: false`` / ``real_gate_still_closed_by_policy``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from investment_orchestrator.research.actionable_promotion_pointer import (
    PERMISSION_EFFECT_PENDING_GATES,
    PROMOTION_STATUS_PENDING_GATES,
)
from investment_orchestrator.research.promoted_handoff_verifier import (
    FUTURE_PERMISSION_REQUIRED,
    SCHEMA_VERSION as VERIFICATION_SCHEMA_VERSION,
)
from investment_orchestrator.state.research_availability import (
    STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    evaluate_step2_research_gate,
)


SCHEMA_VERSION = "promoted_step2_gate_dry_run_v1"

# The decision-only state named by the R2E.5b-6 design (§30.2) and implemented
# by R2E.5b-6c. The dry-run still evaluates the PRE-UPGRADE pending-gates
# posture, so the field name keeps its artifact-contract meaning: the state the
# upgrade would move to.
FUTURE_STATE_REQUIRED = "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"

HOLD_NO_TRADE_ONLY = ("HOLD", "NO_TRADE")

SEVERITY_BLOCKER = "blocker"
SEVERITY_POLICY = "policy"

DRY_RUN_BLOCKER_DECISION_MISSING = "decision_missing"
DRY_RUN_BLOCKER_DECISION_STATE_NOT_PENDING_GATES = "decision_state_not_pending_gates"
DRY_RUN_BLOCKER_DECISION_ACTIONS_NOT_HOLD_NO_TRADE = "decision_actions_not_hold_no_trade"
DRY_RUN_BLOCKER_VERIFICATION_MISSING = "verification_missing"
DRY_RUN_BLOCKER_VERIFICATION_INVALID = "verification_invalid"
DRY_RUN_BLOCKER_VERIFICATION_PERMISSION_MISMATCH = "verification_permission_mismatch"
DRY_RUN_BLOCKER_VERIFICATION_MARKERS_INVALID = "verification_markers_invalid"
DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY = "real_gate_still_closed_by_policy"

DRY_RUN_BLOCKER_REASON_CODES = (
    DRY_RUN_BLOCKER_DECISION_MISSING,
    DRY_RUN_BLOCKER_DECISION_STATE_NOT_PENDING_GATES,
    DRY_RUN_BLOCKER_DECISION_ACTIONS_NOT_HOLD_NO_TRADE,
    DRY_RUN_BLOCKER_VERIFICATION_MISSING,
    DRY_RUN_BLOCKER_VERIFICATION_INVALID,
    DRY_RUN_BLOCKER_VERIFICATION_PERMISSION_MISMATCH,
    DRY_RUN_BLOCKER_VERIFICATION_MARKERS_INVALID,
    DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY,
)


def evaluate_promoted_step2_gate_dry_run(
    *,
    research_decision: Mapping[str, Any] | None,
    promoted_verification: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    """Simulate the future promoted Step 2 decision-only gate. Report-only.

    Deterministic and fail-closed: never raises; malformed inputs yield
    ``would_allow_step2_promoted_decision: false`` with reason codes. The result
    is diagnostic only — it grants nothing and no consumer reads it.
    """
    try:
        return _evaluate(
            research_decision=research_decision,
            promoted_verification=promoted_verification,
        )
    except Exception as exc:  # noqa: BLE001 - dry-run must never raise
        return _result(
            would_allow=False,
            current_real_gate_allows=False,
            current_state=None,
            current_allowed_actions=[],
            verification_valid=False,
            blockers=[DRY_RUN_BLOCKER_DECISION_MISSING, DRY_RUN_BLOCKER_VERIFICATION_MISSING],
            warnings=[],
            checks=[
                _check(
                    "dry_run_never_raise_fallback",
                    False,
                    DRY_RUN_BLOCKER_DECISION_MISSING,
                    details={"error": str(exc)},
                )
            ],
        )


def _evaluate(
    *,
    research_decision: Mapping[str, Any] | None,
    promoted_verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    decision = research_decision if isinstance(research_decision, Mapping) else None
    verification = promoted_verification if isinstance(promoted_verification, Mapping) else None

    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []

    def add_check(
        check_id: str,
        passed: bool,
        reason_code: str | None = None,
        *,
        severity: str = SEVERITY_BLOCKER,
        **details: Any,
    ) -> None:
        checks.append(_check(check_id, passed, reason_code, severity=severity, details=details))
        if not passed and reason_code is not None:
            target = blockers if severity in (SEVERITY_BLOCKER, SEVERITY_POLICY) else warnings
            if reason_code not in target:
                target.append(reason_code)

    # --- current decision must be the promoted pending-gates posture -----------
    add_check("decision_present", decision is not None, DRY_RUN_BLOCKER_DECISION_MISSING)

    current_state = _str_or_none(decision.get("state")) if decision else None
    current_allowed_actions = _string_items(decision.get("allowed_actions")) if decision else []
    if decision is not None:
        add_check(
            "decision_state_pending_gates",
            current_state == STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
            DRY_RUN_BLOCKER_DECISION_STATE_NOT_PENDING_GATES,
            expected_state=STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
            actual_state=current_state,
        )
        add_check(
            "decision_actions_hold_no_trade_only",
            bool(current_allowed_actions)
            and set(current_allowed_actions) == set(HOLD_NO_TRADE_ONLY),
            DRY_RUN_BLOCKER_DECISION_ACTIONS_NOT_HOLD_NO_TRADE,
            expected_allowed_actions=list(HOLD_NO_TRADE_ONLY),
            actual_allowed_actions=current_allowed_actions,
        )

    # --- promoted verification (R2E.5b-6a) must fully pass ---------------------
    add_check("verification_present", verification is not None, DRY_RUN_BLOCKER_VERIFICATION_MISSING)

    verification_valid = False
    if verification is not None:
        verification_blockers = verification.get("verification_blockers")
        verification_valid = (
            verification.get("valid_for_step2_decision") is True
            and isinstance(verification_blockers, list)
            and not verification_blockers
        )
        add_check(
            "verification_valid_no_blockers",
            verification_valid,
            DRY_RUN_BLOCKER_VERIFICATION_INVALID,
            valid_for_step2_decision=verification.get("valid_for_step2_decision"),
            verification_blockers=verification_blockers,
        )
        add_check(
            "verification_future_permission_match",
            verification.get("future_permission_required") == FUTURE_PERMISSION_REQUIRED,
            DRY_RUN_BLOCKER_VERIFICATION_PERMISSION_MISMATCH,
            expected_future_permission=FUTURE_PERMISSION_REQUIRED,
            actual_future_permission=verification.get("future_permission_required"),
        )
        markers_ok = (
            verification.get("schema_version") == VERIFICATION_SCHEMA_VERSION
            and verification.get("report_only") is True
            and verification.get("permission_effect") == "none"
            and verification.get("not_authorization") is True
            and verification.get("is_llm_generated") is False
            and verification.get("promotion_status") == PROMOTION_STATUS_PENDING_GATES
            and verification.get("pointer_permission_effect") == PERMISSION_EFFECT_PENDING_GATES
            and verification.get("consumed_by_step2") is False
        )
        add_check(
            "verification_report_only_markers_intact",
            markers_ok,
            DRY_RUN_BLOCKER_VERIFICATION_MARKERS_INVALID,
            schema_version=verification.get("schema_version"),
            expected_schema=VERIFICATION_SCHEMA_VERSION,
            report_only=verification.get("report_only"),
            permission_effect=verification.get("permission_effect"),
            not_authorization=verification.get("not_authorization"),
            promotion_status=verification.get("promotion_status"),
            pointer_permission_effect=verification.get("pointer_permission_effect"),
            consumed_by_step2=verification.get("consumed_by_step2"),
        )

    # The eligibility blockers above decide the diagnostic verdict BEFORE the
    # policy blocker below is appended.
    would_allow = not blockers

    # --- the REAL Step 2 gate stays closed for this posture ---------------------
    # Evaluated with the production gate (read-only) against the supplied
    # decision: the pending-gates posture this dry-run diagnoses never passes it
    # (no order actions, no PROMOTED_RESEARCH_DECISION). The policy blocker is
    # recorded even when would_allow is true, so the artifact can never be read
    # as an actual Step 2 render permission.
    current_real_gate_allows = False
    try:
        current_real_gate_allows = evaluate_step2_research_gate(decision).allowed is True
    except Exception:  # noqa: BLE001 - fail closed: an evaluation error means "closed"
        current_real_gate_allows = False
    add_check(
        "current_real_gate_allows",
        current_real_gate_allows,
        DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY,
        severity=SEVERITY_POLICY,
        note=(
            "The real Step 2 gate is unchanged and still closed for promoted pending-gates "
            "research; would_allow_step2_promoted_decision is diagnostic only and grants no "
            "render permission."
        ),
    )

    return _result(
        would_allow=would_allow,
        current_real_gate_allows=current_real_gate_allows,
        current_state=current_state,
        current_allowed_actions=current_allowed_actions,
        verification_valid=verification_valid,
        blockers=blockers,
        warnings=warnings,
        checks=checks,
    )


def _result(
    *,
    would_allow: bool,
    current_real_gate_allows: bool,
    current_state: str | None,
    current_allowed_actions: list[str],
    verification_valid: bool,
    blockers: list[str],
    warnings: list[str],
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "dry_run_only": True,
        "would_allow_step2_promoted_decision": bool(would_allow),
        "current_real_gate_allows": bool(current_real_gate_allows),
        "future_permission_required": FUTURE_PERMISSION_REQUIRED,
        "future_state_required": FUTURE_STATE_REQUIRED,
        "current_state": current_state,
        "current_allowed_actions": list(current_allowed_actions),
        "verification_valid_for_step2_decision": bool(verification_valid),
        "dry_run_blockers": list(blockers),
        "dry_run_warnings": list(warnings),
        "checks": list(checks),
        "consumed_by_step2": False,
        "consumed_by_gates": False,
    }


def _check(
    check_id: str,
    passed: bool,
    reason_code: str | None,
    *,
    severity: str = SEVERITY_BLOCKER,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "passed": bool(passed),
        "severity": severity,
        "reason_code": reason_code,
        "details": dict(details or {}),
    }


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
