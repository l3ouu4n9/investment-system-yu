"""Report-only DRY-RUN for a future promoted Step 3 audit-only gate.

R2E.5b-6e: this module verifies whether the committed promoted Step 2
decision-only artifacts would be safe inputs for a future Step 3 audit-only
permission PR. It grants nothing, opens no Step 3 path, and never implies
NEW_BUY, ORDER_COMPILATION, Step 4, final execution, broker automation, or live
order authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import hashlib
import json
from typing import Any

from investment_orchestrator.research.promoted_handoff_verifier import (
    verify_promoted_handoff_for_step2_decision,
)
from investment_orchestrator.state.research_availability import (
    PROMOTED_RESEARCH_DECISION_ACTION,
    STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    MODE_PROMOTED_STEP2_DECISION_ONLY,
    PROMOTED_SOURCE,
)
from investment_orchestrator.validators.validate_decision_packet import validate_decision_packet


VERIFICATION_SCHEMA_VERSION = "promoted_handoff_step3_audit_verification_v1"
DRY_RUN_SCHEMA_VERSION = "promoted_step3_audit_gate_dry_run_v1"
STEP2_MARKER_SCHEMA_VERSION = "step2_promoted_decision_only_v1"

FUTURE_STATE_REQUIRED = "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY"
FUTURE_ACTION_REQUIRED = "PROMOTED_RESEARCH_AUDIT"
FUTURE_STEP3_SOURCE_ARTIFACT = "research_handoff_candidate_effective.json"
RAW_DEEP_RESEARCH_ARTIFACT = "research_output.json"

STEP2_DECISION_ONLY_ALLOWED_ACTIONS = (
    "HOLD",
    "NO_TRADE",
    PROMOTED_RESEARCH_DECISION_ACTION,
)
ORDER_ACTIONS = ("NEW_BUY", "ORDER_COMPILATION")

SEVERITY_BLOCKER = "blocker"
SEVERITY_POLICY = "policy"

BLOCKER_MARKER_MISSING = "step2_promoted_marker_missing"
BLOCKER_MARKER_MALFORMED = "step2_promoted_marker_malformed"
BLOCKER_MARKER_SCHEMA_INVALID = "step2_promoted_marker_schema_invalid"
BLOCKER_MARKER_NOT_DETERMINISTIC = "step2_promoted_marker_not_deterministic"
BLOCKER_MARKER_MISSING_NOT_EXECUTION_AUTHORIZATION = (
    "step2_promoted_marker_missing_not_execution_authorization"
)
BLOCKER_MARKER_MODE_INVALID = "step2_promoted_marker_mode_invalid"
BLOCKER_MARKER_ALLOWED_ACTIONS_INVALID = "step2_promoted_marker_allowed_actions_invalid"
BLOCKER_MARKER_WIDENED_NEW_BUY = "step2_promoted_marker_widened_new_buy"
BLOCKER_MARKER_WIDENED_ORDER_COMPILATION = "step2_promoted_marker_widened_order_compilation"
BLOCKER_MARKER_BLOCKED_ACTIONS_INVALID = "step2_promoted_marker_blocked_actions_invalid"
BLOCKER_MARKER_ORDER_PERMISSION_IMPLIED = "step2_promoted_marker_order_permission_implied"
BLOCKER_MARKER_HASH_MISMATCH = "step2_promoted_marker_hash_mismatch"
BLOCKER_DECISION_PACKET_MISSING = "step2_decision_packet_missing"
BLOCKER_DECISION_PACKET_INVALID = "step2_decision_packet_invalid"
BLOCKER_PROMOTED_HANDOFF_INVALID = "promoted_handoff_verification_invalid"
BLOCKER_RAW_DEEP_RESEARCH_SOURCE = (
    "raw_deep_research_source_not_allowed_for_promoted_step3_audit"
)

DRY_RUN_BLOCKER_DECISION_MISSING = "decision_missing"
DRY_RUN_BLOCKER_DECISION_NOT_STEP2_DECISION_ONLY = "decision_not_step2_decision_only"
DRY_RUN_BLOCKER_DECISION_ALLOWED_ACTIONS_INVALID = "decision_allowed_actions_invalid"
DRY_RUN_BLOCKER_DECISION_WIDENED_NEW_BUY = "decision_widened_new_buy"
DRY_RUN_BLOCKER_DECISION_WIDENED_ORDER_COMPILATION = "decision_widened_order_compilation"
DRY_RUN_BLOCKER_VERIFICATION_MISSING = "verification_missing"
DRY_RUN_BLOCKER_VERIFICATION_INVALID = "verification_invalid"
DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY = "real_gate_still_closed_by_policy"


def verify_promoted_handoff_for_step3_audit(
    *,
    active_pointer: Mapping[str, Any] | None,
    effective_handoff: Mapping[str, Any] | None,
    effective_validation: Mapping[str, Any] | None,
    step2_promoted_marker: Mapping[str, Any] | None,
    step2_decision_packet: Mapping[str, Any] | None,
    today: date | None = None,
    source_artifacts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Verify future Step 3 audit-only inputs. Report-only and fail-closed."""
    try:
        return _verify(
            active_pointer=active_pointer,
            effective_handoff=effective_handoff,
            effective_validation=effective_validation,
            step2_promoted_marker=step2_promoted_marker,
            step2_decision_packet=step2_decision_packet,
            today=today,
            source_artifacts=source_artifacts,
        )
    except Exception as exc:  # noqa: BLE001 - report-only verifier must never raise
        return _verification_result(
            valid=False,
            blockers=[BLOCKER_MARKER_MALFORMED],
            warnings=[],
            checks=[
                _check(
                    "step3_audit_verifier_never_raise_fallback",
                    False,
                    BLOCKER_MARKER_MALFORMED,
                    details={"error": str(exc)},
                )
            ],
            live_step2_verification=None,
            active_pointer=active_pointer,
            effective_handoff=effective_handoff,
            step2_promoted_marker=step2_promoted_marker,
            step2_decision_packet=step2_decision_packet,
            source_artifacts=source_artifacts,
            decision_packet_valid=False,
            decision_packet_error=str(exc),
        )


def evaluate_promoted_step3_audit_gate_dry_run(
    *,
    research_decision: Mapping[str, Any] | None,
    promoted_step3_verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Simulate a future promoted Step 3 audit-only gate. Report-only."""
    try:
        return _evaluate_dry_run(
            research_decision=research_decision,
            promoted_step3_verification=promoted_step3_verification,
        )
    except Exception as exc:  # noqa: BLE001 - dry-run must never raise
        return _dry_run_result(
            would_allow=False,
            current_real_gate_allows=False,
            current_state=None,
            current_allowed_actions=[],
            verification_valid=False,
            blockers=[DRY_RUN_BLOCKER_DECISION_MISSING, DRY_RUN_BLOCKER_VERIFICATION_MISSING],
            warnings=[],
            checks=[
                _check(
                    "step3_audit_dry_run_never_raise_fallback",
                    False,
                    DRY_RUN_BLOCKER_DECISION_MISSING,
                    details={"error": str(exc)},
                )
            ],
            promoted_step3_verification=promoted_step3_verification,
        )


def _verify(
    *,
    active_pointer: Mapping[str, Any] | None,
    effective_handoff: Mapping[str, Any] | None,
    effective_validation: Mapping[str, Any] | None,
    step2_promoted_marker: Mapping[str, Any] | None,
    step2_decision_packet: Mapping[str, Any] | None,
    today: date | None,
    source_artifacts: Mapping[str, str] | None,
) -> dict[str, Any]:
    pointer = active_pointer if isinstance(active_pointer, Mapping) else None
    effective = effective_handoff if isinstance(effective_handoff, Mapping) else None
    marker = step2_promoted_marker if isinstance(step2_promoted_marker, Mapping) else None
    packet = step2_decision_packet if isinstance(step2_decision_packet, Mapping) else None

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
            target = blockers if severity == SEVERITY_BLOCKER else warnings
            if reason_code not in target:
                target.append(reason_code)

    live_step2_verification = verify_promoted_handoff_for_step2_decision(
        active_pointer=active_pointer,
        effective_handoff=effective_handoff,
        effective_validation=effective_validation,
        today=today,
    )
    live_valid = (
        live_step2_verification.get("valid_for_step2_decision") is True
        and live_step2_verification.get("verification_blockers") == []
    )
    add_check(
        "live_promoted_handoff_verification_passed",
        live_valid,
        BLOCKER_PROMOTED_HANDOFF_INVALID,
        verification_blockers=live_step2_verification.get("verification_blockers"),
        promotion_expires_at=live_step2_verification.get("promotion_expires_at"),
    )

    add_check("step2_promoted_marker_present", step2_promoted_marker is not None, BLOCKER_MARKER_MISSING)
    add_check("step2_promoted_marker_is_mapping", marker is not None, BLOCKER_MARKER_MALFORMED)
    marker_allowed_actions = _string_items(marker.get("allowed_actions")) if marker else []
    marker_blocked_actions = _string_items(marker.get("blocked_actions")) if marker else []
    marker_effective_hash = _str_or_none(marker.get("effective_handoff_sha256")) if marker else None
    marker_pointer_hash = _str_or_none(marker.get("active_pointer_sha256")) if marker else None
    live_effective_hash = _str_or_none(live_step2_verification.get("effective_handoff_sha256"))
    pointer_hash = _sha256_of(pointer) if pointer is not None else None
    if marker is not None:
        add_check(
            "step2_promoted_marker_schema_expected",
            marker.get("schema_version") == STEP2_MARKER_SCHEMA_VERSION,
            BLOCKER_MARKER_SCHEMA_INVALID,
            expected_schema=STEP2_MARKER_SCHEMA_VERSION,
            actual_schema=marker.get("schema_version"),
        )
        add_check(
            "step2_promoted_marker_deterministic",
            marker.get("is_llm_generated") is False,
            BLOCKER_MARKER_NOT_DETERMINISTIC,
            is_llm_generated=marker.get("is_llm_generated"),
        )
        add_check(
            "step2_promoted_marker_not_execution_authorization",
            marker.get("not_execution_authorization") is True,
            BLOCKER_MARKER_MISSING_NOT_EXECUTION_AUTHORIZATION,
            not_execution_authorization=marker.get("not_execution_authorization"),
        )
        mode_ok = (
            marker.get("mode") == MODE_PROMOTED_STEP2_DECISION_ONLY
            and marker.get("promoted_step2_decision_only") is True
            and marker.get("research_state") == STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY
            and marker.get("source") == PROMOTED_SOURCE
        )
        add_check(
            "step2_promoted_marker_mode_state_source",
            mode_ok,
            BLOCKER_MARKER_MODE_INVALID,
            mode=marker.get("mode"),
            research_state=marker.get("research_state"),
            source=marker.get("source"),
        )
        add_check(
            "step2_promoted_marker_allowed_actions_exact",
            marker_allowed_actions == list(STEP2_DECISION_ONLY_ALLOWED_ACTIONS),
            BLOCKER_MARKER_ALLOWED_ACTIONS_INVALID,
            expected_allowed_actions=list(STEP2_DECISION_ONLY_ALLOWED_ACTIONS),
            actual_allowed_actions=marker_allowed_actions,
        )
        add_check(
            "step2_promoted_marker_new_buy_absent",
            "NEW_BUY" not in marker_allowed_actions,
            BLOCKER_MARKER_WIDENED_NEW_BUY,
            actual_allowed_actions=marker_allowed_actions,
        )
        add_check(
            "step2_promoted_marker_order_compilation_absent",
            "ORDER_COMPILATION" not in marker_allowed_actions,
            BLOCKER_MARKER_WIDENED_ORDER_COMPILATION,
            actual_allowed_actions=marker_allowed_actions,
        )
        add_check(
            "step2_promoted_marker_order_actions_blocked",
            all(action in marker_blocked_actions for action in ORDER_ACTIONS),
            BLOCKER_MARKER_BLOCKED_ACTIONS_INVALID,
            expected_blocked_actions=list(ORDER_ACTIONS),
            actual_blocked_actions=marker_blocked_actions,
        )
        order_permission_implied = (
            marker.get("order_compilation_allowed") is not False
            or marker.get("new_buy_permission") is not False
            or marker.get("step3_allowed") is not False
            or marker.get("step4_allowed") is not False
        )
        add_check(
            "step2_promoted_marker_no_order_permission_implied",
            not order_permission_implied,
            BLOCKER_MARKER_ORDER_PERMISSION_IMPLIED,
            order_compilation_allowed=marker.get("order_compilation_allowed"),
            new_buy_permission=marker.get("new_buy_permission"),
            step3_allowed=marker.get("step3_allowed"),
            step4_allowed=marker.get("step4_allowed"),
        )
        add_check(
            "step2_promoted_marker_hashes_match_live_sources",
            marker_effective_hash is not None
            and live_effective_hash is not None
            and marker_effective_hash == live_effective_hash
            and marker_pointer_hash is not None
            and pointer_hash is not None
            and marker_pointer_hash == pointer_hash,
            BLOCKER_MARKER_HASH_MISMATCH,
            marker_effective_handoff_sha256=marker_effective_hash,
            live_effective_handoff_sha256=live_effective_hash,
            marker_active_pointer_sha256=marker_pointer_hash,
            live_active_pointer_sha256=pointer_hash,
        )

    add_check(
        "step2_decision_packet_present",
        step2_decision_packet is not None,
        BLOCKER_DECISION_PACKET_MISSING,
    )
    decision_packet_valid = False
    decision_packet_error: str | None = None
    if packet is not None:
        try:
            validate_decision_packet(packet)
            decision_packet_valid = True
        except Exception as exc:  # noqa: BLE001 - fail closed into deterministic artifact
            decision_packet_error = str(exc)
        add_check(
            "step2_decision_packet_structurally_valid",
            decision_packet_valid,
            BLOCKER_DECISION_PACKET_INVALID,
            validation_error=decision_packet_error,
        )

    raw_source_used = _raw_deep_research_source_used(source_artifacts)
    add_check(
        "future_step3_source_is_effective_handoff_not_raw_deep_research",
        not raw_source_used,
        BLOCKER_RAW_DEEP_RESEARCH_SOURCE,
        future_step3_source_artifact=FUTURE_STEP3_SOURCE_ARTIFACT,
        raw_deep_research_source_used=raw_source_used,
    )

    return _verification_result(
        valid=not blockers,
        blockers=blockers,
        warnings=warnings,
        checks=checks,
        live_step2_verification=live_step2_verification,
        active_pointer=pointer,
        effective_handoff=effective,
        step2_promoted_marker=marker,
        step2_decision_packet=packet,
        source_artifacts=source_artifacts,
        decision_packet_valid=decision_packet_valid,
        decision_packet_error=decision_packet_error,
    )


def _evaluate_dry_run(
    *,
    research_decision: Mapping[str, Any] | None,
    promoted_step3_verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    decision = research_decision if isinstance(research_decision, Mapping) else None
    verification = (
        promoted_step3_verification
        if isinstance(promoted_step3_verification, Mapping)
        else None
    )
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

    add_check("decision_present", decision is not None, DRY_RUN_BLOCKER_DECISION_MISSING)
    current_state = _str_or_none(decision.get("state")) if decision else None
    current_allowed_actions = _string_items(decision.get("allowed_actions")) if decision else []
    if decision is not None:
        add_check(
            "decision_state_step2_decision_only",
            current_state == STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY
            and decision.get("promoted_step2_decision_only") is True,
            DRY_RUN_BLOCKER_DECISION_NOT_STEP2_DECISION_ONLY,
            expected_state=STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY,
            actual_state=current_state,
            promoted_step2_decision_only=decision.get("promoted_step2_decision_only"),
        )
        add_check(
            "decision_allowed_actions_exact_step2_decision_only",
            current_allowed_actions == list(STEP2_DECISION_ONLY_ALLOWED_ACTIONS),
            DRY_RUN_BLOCKER_DECISION_ALLOWED_ACTIONS_INVALID,
            expected_allowed_actions=list(STEP2_DECISION_ONLY_ALLOWED_ACTIONS),
            actual_allowed_actions=current_allowed_actions,
        )
        add_check(
            "decision_new_buy_absent",
            "NEW_BUY" not in current_allowed_actions,
            DRY_RUN_BLOCKER_DECISION_WIDENED_NEW_BUY,
            actual_allowed_actions=current_allowed_actions,
        )
        add_check(
            "decision_order_compilation_absent",
            "ORDER_COMPILATION" not in current_allowed_actions,
            DRY_RUN_BLOCKER_DECISION_WIDENED_ORDER_COMPILATION,
            actual_allowed_actions=current_allowed_actions,
        )

    add_check(
        "promoted_step3_verification_present",
        verification is not None,
        DRY_RUN_BLOCKER_VERIFICATION_MISSING,
    )
    verification_valid = False
    if verification is not None:
        verification_valid = (
            verification.get("schema_version") == VERIFICATION_SCHEMA_VERSION
            and verification.get("valid_for_promoted_step3_audit") is True
            and verification.get("verification_blockers") == []
            and verification.get("report_only") is True
            and verification.get("permission_effect") == "none"
            and verification.get("not_authorization") is True
            and verification.get("not_execution_authorization") is True
            and verification.get("future_step3_source_artifact") == FUTURE_STEP3_SOURCE_ARTIFACT
            and verification.get("raw_deep_research_source_used") is False
        )
        add_check(
            "promoted_step3_verification_valid_no_blockers",
            verification_valid,
            DRY_RUN_BLOCKER_VERIFICATION_INVALID,
            schema_version=verification.get("schema_version"),
            valid_for_promoted_step3_audit=verification.get("valid_for_promoted_step3_audit"),
            verification_blockers=verification.get("verification_blockers"),
            report_only=verification.get("report_only"),
            permission_effect=verification.get("permission_effect"),
        )

    would_allow = not blockers
    current_real_gate_allows = False
    add_check(
        "current_real_gate_allows",
        current_real_gate_allows,
        DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY,
        severity=SEVERITY_POLICY,
        note=(
            "No real Step 3 audit permission exists in R2E.5b-6e. "
            "would_allow_promoted_step3_audit is diagnostic only."
        ),
    )

    return _dry_run_result(
        would_allow=would_allow,
        current_real_gate_allows=current_real_gate_allows,
        current_state=current_state,
        current_allowed_actions=current_allowed_actions,
        verification_valid=verification_valid,
        blockers=blockers,
        warnings=warnings,
        checks=checks,
        promoted_step3_verification=verification,
    )


def _verification_result(
    *,
    valid: bool,
    blockers: list[str],
    warnings: list[str],
    checks: list[dict[str, Any]],
    live_step2_verification: Mapping[str, Any] | None,
    active_pointer: Mapping[str, Any] | None,
    effective_handoff: Mapping[str, Any] | None,
    step2_promoted_marker: Mapping[str, Any] | None,
    step2_decision_packet: Mapping[str, Any] | None,
    source_artifacts: Mapping[str, str] | None,
    decision_packet_valid: bool,
    decision_packet_error: str | None,
) -> dict[str, Any]:
    live = live_step2_verification if isinstance(live_step2_verification, Mapping) else {}
    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "valid_for_promoted_step3_audit": bool(valid),
        "verification_blockers": list(blockers),
        "verification_warnings": list(warnings),
        "checks": list(checks),
        "source": PROMOTED_SOURCE,
        "promotion_status": live.get("promotion_status"),
        "pointer_permission_effect": live.get("pointer_permission_effect"),
        "candidate_actionable_row_count": live.get("candidate_actionable_row_count"),
        "actionable_this_run_tickers": list(live.get("actionable_this_run_tickers") or []),
        "promotion_expires_at": live.get("promotion_expires_at"),
        "effective_handoff_sha256": live.get("effective_handoff_sha256"),
        "pointer_effective_handoff_sha256": live.get("pointer_effective_handoff_sha256"),
        "active_pointer_sha256": _sha256_of(active_pointer) if active_pointer is not None else None,
        "step2_promoted_marker_sha256": (
            _sha256_of(step2_promoted_marker) if step2_promoted_marker is not None else None
        ),
        "step2_decision_packet_sha256": (
            _sha256_of(step2_decision_packet) if step2_decision_packet is not None else None
        ),
        "step2_decision_packet_valid": bool(decision_packet_valid),
        "step2_decision_packet_validation_error": decision_packet_error,
        "live_step2_verification_valid": live.get("valid_for_step2_decision") is True,
        "live_step2_verification_blockers": list(live.get("verification_blockers") or []),
        "future_state_required": FUTURE_STATE_REQUIRED,
        "future_action_required": FUTURE_ACTION_REQUIRED,
        "future_step3_source_artifact": FUTURE_STEP3_SOURCE_ARTIFACT,
        "raw_deep_research_source_used": _raw_deep_research_source_used(source_artifacts),
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "step4_allowed": False,
        "final_execution_allowed": False,
        "broker_automation_allowed": False,
        "source_artifacts": dict(source_artifacts or {}),
        "source_artifact_hashes": {
            "active_research_handoff_source": (
                _sha256_of(active_pointer) if active_pointer is not None else None
            ),
            "research_handoff_candidate_effective": (
                _sha256_of(effective_handoff) if effective_handoff is not None else None
            ),
            "step2_promoted_decision_only": (
                _sha256_of(step2_promoted_marker) if step2_promoted_marker is not None else None
            ),
            "step2_decision_packet": (
                _sha256_of(step2_decision_packet) if step2_decision_packet is not None else None
            ),
        },
    }


def _dry_run_result(
    *,
    would_allow: bool,
    current_real_gate_allows: bool,
    current_state: str | None,
    current_allowed_actions: list[str],
    verification_valid: bool,
    blockers: list[str],
    warnings: list[str],
    checks: list[dict[str, Any]],
    promoted_step3_verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    verification = (
        promoted_step3_verification
        if isinstance(promoted_step3_verification, Mapping)
        else {}
    )
    return {
        "schema_version": DRY_RUN_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "dry_run_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "would_allow_promoted_step3_audit": bool(would_allow),
        "current_real_gate_allows": bool(current_real_gate_allows),
        "future_state_required": FUTURE_STATE_REQUIRED,
        "future_action_required": FUTURE_ACTION_REQUIRED,
        "current_state": current_state,
        "current_allowed_actions": list(current_allowed_actions),
        "verification_valid_for_promoted_step3_audit": bool(verification_valid),
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "step4_allowed": False,
        "final_execution_allowed": False,
        "broker_automation_allowed": False,
        "dry_run_blockers": list(blockers),
        "dry_run_warnings": list(warnings),
        "checks": list(checks),
        "future_step3_source_artifact": FUTURE_STEP3_SOURCE_ARTIFACT,
        "raw_deep_research_source_used": verification.get("raw_deep_research_source_used") is True,
        "source_artifacts": dict(verification.get("source_artifacts") or {}),
        "source_artifact_hashes": dict(verification.get("source_artifact_hashes") or {}),
        "freshness": {
            "promotion_expires_at": verification.get("promotion_expires_at"),
            "promotion_status": verification.get("promotion_status"),
        },
        "consumed_by_availability": False,
        "consumed_by_step3": False,
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


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _raw_deep_research_source_used(source_artifacts: Mapping[str, str] | None) -> bool:
    """Return true if source metadata points at raw Deep Research output."""
    if not isinstance(source_artifacts, Mapping):
        return False
    for key, value in source_artifacts.items():
        if _is_raw_deep_research_source_token(key) or _is_raw_deep_research_source_token(value):
            return True
    return False


def _is_raw_deep_research_source_token(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    token = value.strip().replace("\\", "/")
    if token == RAW_DEEP_RESEARCH_ARTIFACT or token.endswith(f"/{RAW_DEEP_RESEARCH_ARTIFACT}"):
        return True
    return token in {"research_output", "raw_deep_research", "raw_deep_research_output"}
