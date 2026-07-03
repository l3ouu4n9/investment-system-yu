"""Report-only DRY-RUN for a future promoted Step 4 preview-only gate.

R2E.5b-7b: this module verifies whether the committed promoted Step 3
audit-only artifacts would be safe prerequisites for a future Step 4
preview-only permission PR. It grants nothing, opens no Step 4 path, creates
no candidate order rows, and never implies NEW_BUY, ORDER_COMPILATION, order
compilation, final execution, broker automation, or live order authority.

The future state/action names below exist ONLY as report labels. They are not
registered in ``research_availability`` and no gate consumes these artifacts.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import hashlib
import json
import math
from typing import Any

from investment_orchestrator.parsers.portfolio_snapshot_existing_orders import (
    parse_existing_buy_open_orders_summary,
)
from investment_orchestrator.research.promoted_step3_audit_dry_run import (
    DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY,
    _raw_deep_research_source_used,
    verify_promoted_handoff_for_step3_audit,
)
from investment_orchestrator.state.research_availability import (
    PROMOTED_RESEARCH_AUDIT_ACTION,
    PROMOTED_RESEARCH_DECISION_ACTION,
    STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY,
)
from investment_orchestrator.state.research_degraded_mode_gate import PROMOTED_SOURCE


VERIFICATION_SCHEMA_VERSION = "promoted_step4_readiness_verification_v1"
DRY_RUN_SCHEMA_VERSION = "promoted_step4_preview_gate_dry_run_v1"

# Mirrored literals from ``workflow.step3_audit_engine`` (importing that module
# here would be circular: workflow imports research). A drift-guard unit test
# asserts these equal the step3 engine's real constants.
EXPECTED_STEP3_MARKER_SCHEMA_VERSION = "step3_promoted_audit_only_v1"
EXPECTED_STEP3_BLOCK_SCHEMA_VERSION = "step3_promoted_audit_only_downstream_block_v1"
EXPECTED_STEP3_MODE = "promoted_step3_audit_only"
EXPECTED_STEP3_BLOCK_REASON = "promoted_step3_audit_only_no_order_compilation_permission"

# FUTURE labels only. R2E.5b-7b must NOT register this state or action anywhere
# in the real permission model; they appear in artifacts as strings so a future
# explicit PR (and its post-audit) can be checked against the dry-run's claim.
FUTURE_STATE_REQUIRED = "STRICT_FRESH_COMPILED_ACTIONABLE_STEP4_PREVIEW_ONLY"
FUTURE_ACTION_REQUIRED = "PROMOTED_ORDER_PREVIEW"

STEP3_AUDIT_ONLY_ALLOWED_ACTIONS = (
    "HOLD",
    "NO_TRADE",
    PROMOTED_RESEARCH_DECISION_ACTION,
    PROMOTED_RESEARCH_AUDIT_ACTION,
)
ORDER_ACTIONS = ("NEW_BUY", "ORDER_COMPILATION")

SEVERITY_BLOCKER = "blocker"
SEVERITY_POLICY = "policy"

BLOCKER_STEP3_CHAIN_INVALID = "promoted_step3_chain_invalid_for_step4_readiness"
BLOCKER_STEP3_MARKER_MISSING = "step3_promoted_marker_missing"
BLOCKER_STEP3_MARKER_MALFORMED = "step3_promoted_marker_malformed"
BLOCKER_STEP3_MARKER_SCHEMA_INVALID = "step3_promoted_marker_schema_invalid"
BLOCKER_STEP3_MARKER_NOT_DETERMINISTIC = "step3_promoted_marker_not_deterministic"
BLOCKER_STEP3_MARKER_MODE_INVALID = "step3_promoted_marker_mode_invalid"
BLOCKER_STEP3_MARKER_NOT_AUDIT_ONLY = "step3_promoted_marker_not_audit_only"
BLOCKER_STEP3_MARKER_MISSING_NOT_EXECUTION_AUTHORIZATION = (
    "step3_promoted_marker_missing_not_execution_authorization"
)
BLOCKER_STEP3_MARKER_ORDER_PERMISSION_IMPLIED = "step3_promoted_marker_order_permission_implied"
BLOCKER_STEP3_MARKER_ALLOWED_ACTIONS_INVALID = "step3_promoted_marker_allowed_actions_invalid"
BLOCKER_STEP3_MARKER_WIDENED_NEW_BUY = "step3_promoted_marker_widened_new_buy"
BLOCKER_STEP3_MARKER_WIDENED_ORDER_COMPILATION = (
    "step3_promoted_marker_widened_order_compilation"
)
BLOCKER_STEP3_MARKER_BLOCKED_ACTIONS_INVALID = "step3_promoted_marker_blocked_actions_invalid"
BLOCKER_STEP3_MARKER_HASH_MISMATCH = "step3_promoted_marker_hash_mismatch"
BLOCKER_STEP3_BLOCK_MISSING = "step3_downstream_block_missing"
BLOCKER_STEP3_BLOCK_MALFORMED = "step3_downstream_block_malformed"
BLOCKER_STEP3_BLOCK_SCHEMA_INVALID = "step3_downstream_block_schema_invalid"
BLOCKER_STEP3_BLOCK_NOT_BLOCKING = "step3_downstream_block_not_blocking"
BLOCKER_STEP3_BLOCK_MISSING_NOT_EXECUTION_AUTHORIZATION = (
    "step3_downstream_block_missing_not_execution_authorization"
)
BLOCKER_STEP3_BLOCK_REASON_INVALID = "step3_downstream_block_reason_invalid"
BLOCKER_STEP3_AUDIT_OUTPUT_MISSING = "step3_audit_output_missing"
BLOCKER_STALE_LEGACY_AUDITED_PACKET = "stale_legacy_audited_packet_present"
BLOCKER_RAW_DEEP_RESEARCH_SOURCE = (
    "raw_deep_research_source_not_allowed_for_promoted_step4_preview"
)
BLOCKER_SETTINGS_UNAVAILABLE = "strategy_settings_unavailable"
BLOCKER_HARD_CAP_BUDGET_INVALID = "hard_cap_open_orders_budget_invalid"
BLOCKER_TARGET_NEW_BUY_BUDGET_INVALID = "target_new_buy_budget_this_run_invalid"
BLOCKER_MAX_NEW_TICKERS_INVALID = "max_new_tickers_per_week_invalid"
BLOCKER_PORTFOLIO_SNAPSHOT_INVALID = "portfolio_snapshot_unparseable"
BLOCKER_BUY_UNIVERSE_INVALID = "effective_allowed_buy_universe_invalid"
BLOCKER_VERIFIER_INTERNAL_ERROR = "step4_readiness_verifier_internal_error"

DRY_RUN_BLOCKER_DECISION_MISSING = "decision_missing"
DRY_RUN_BLOCKER_DECISION_NOT_STEP3_AUDIT_ONLY = "decision_not_step3_audit_only"
DRY_RUN_BLOCKER_DECISION_ALLOWED_ACTIONS_INVALID = "decision_allowed_actions_invalid"
DRY_RUN_BLOCKER_DECISION_WIDENED_NEW_BUY = "decision_widened_new_buy"
DRY_RUN_BLOCKER_DECISION_WIDENED_ORDER_COMPILATION = "decision_widened_order_compilation"
DRY_RUN_BLOCKER_VERIFICATION_MISSING = "verification_missing"
DRY_RUN_BLOCKER_VERIFICATION_INVALID = "verification_invalid"


def verify_promoted_step3_for_step4_readiness(
    *,
    active_pointer: Mapping[str, Any] | None,
    effective_handoff: Mapping[str, Any] | None,
    effective_validation: Mapping[str, Any] | None,
    step2_promoted_marker: Mapping[str, Any] | None,
    step2_decision_packet: Mapping[str, Any] | None,
    step3_promoted_marker: Mapping[str, Any] | None,
    step3_downstream_block: Mapping[str, Any] | None,
    step3_audit_output_text: str | None,
    legacy_audited_packet_present: bool,
    strategy_settings: Mapping[str, Any] | None,
    portfolio_snapshot_text: str | None,
    today: date | None = None,
    source_artifacts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Verify future Step 4 preview-only prerequisites. Report-only, fail-closed."""
    try:
        return _verify(
            active_pointer=active_pointer,
            effective_handoff=effective_handoff,
            effective_validation=effective_validation,
            step2_promoted_marker=step2_promoted_marker,
            step2_decision_packet=step2_decision_packet,
            step3_promoted_marker=step3_promoted_marker,
            step3_downstream_block=step3_downstream_block,
            step3_audit_output_text=step3_audit_output_text,
            legacy_audited_packet_present=legacy_audited_packet_present,
            strategy_settings=strategy_settings,
            portfolio_snapshot_text=portfolio_snapshot_text,
            today=today,
            source_artifacts=source_artifacts,
        )
    except Exception as exc:  # noqa: BLE001 - report-only verifier must never raise
        return _verification_result(
            valid=False,
            blockers=[BLOCKER_VERIFIER_INTERNAL_ERROR],
            warnings=[],
            checks=[
                _check(
                    "step4_readiness_verifier_never_raise_fallback",
                    False,
                    BLOCKER_VERIFIER_INTERNAL_ERROR,
                    details={"error": str(exc)},
                )
            ],
            live_step3_verification=None,
            active_pointer=active_pointer,
            effective_handoff=effective_handoff,
            step2_promoted_marker=step2_promoted_marker,
            step2_decision_packet=step2_decision_packet,
            step3_promoted_marker=step3_promoted_marker,
            step3_downstream_block=step3_downstream_block,
            step3_audit_output_text=step3_audit_output_text,
            source_artifacts=source_artifacts,
        )


def evaluate_promoted_step4_preview_gate_dry_run(
    *,
    research_decision: Mapping[str, Any] | None,
    promoted_step4_verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Simulate a future promoted Step 4 preview-only gate. Report-only."""
    try:
        return _evaluate_dry_run(
            research_decision=research_decision,
            promoted_step4_verification=promoted_step4_verification,
        )
    except Exception as exc:  # noqa: BLE001 - dry-run must never raise
        return _dry_run_result(
            would_allow=False,
            current_state=None,
            current_allowed_actions=[],
            verification_valid=False,
            blockers=[DRY_RUN_BLOCKER_DECISION_MISSING, DRY_RUN_BLOCKER_VERIFICATION_MISSING],
            warnings=[],
            checks=[
                _check(
                    "step4_preview_dry_run_never_raise_fallback",
                    False,
                    DRY_RUN_BLOCKER_DECISION_MISSING,
                    details={"error": str(exc)},
                )
            ],
            promoted_step4_verification=promoted_step4_verification,
        )


def _verify(
    *,
    active_pointer: Mapping[str, Any] | None,
    effective_handoff: Mapping[str, Any] | None,
    effective_validation: Mapping[str, Any] | None,
    step2_promoted_marker: Mapping[str, Any] | None,
    step2_decision_packet: Mapping[str, Any] | None,
    step3_promoted_marker: Mapping[str, Any] | None,
    step3_downstream_block: Mapping[str, Any] | None,
    step3_audit_output_text: str | None,
    legacy_audited_packet_present: bool,
    strategy_settings: Mapping[str, Any] | None,
    portfolio_snapshot_text: str | None,
    today: date | None,
    source_artifacts: Mapping[str, str] | None,
) -> dict[str, Any]:
    pointer = active_pointer if isinstance(active_pointer, Mapping) else None
    marker = step3_promoted_marker if isinstance(step3_promoted_marker, Mapping) else None
    block = step3_downstream_block if isinstance(step3_downstream_block, Mapping) else None
    packet = step2_decision_packet if isinstance(step2_decision_packet, Mapping) else None
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else None

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

    # 1. Live promoted Step 3 chain (re-runs the 6e verifier, which itself
    # re-runs the 6a Step 2 verifier: pointer / effective / validation /
    # expiry / step2 marker / step2 packet are all re-verified live).
    live_step3_verification = verify_promoted_handoff_for_step3_audit(
        active_pointer=active_pointer,
        effective_handoff=effective_handoff,
        effective_validation=effective_validation,
        step2_promoted_marker=step2_promoted_marker,
        step2_decision_packet=step2_decision_packet,
        today=today,
        source_artifacts=source_artifacts,
    )
    live_valid = (
        live_step3_verification.get("valid_for_promoted_step3_audit") is True
        and live_step3_verification.get("verification_blockers") == []
    )
    add_check(
        "live_promoted_step3_chain_valid",
        live_valid,
        BLOCKER_STEP3_CHAIN_INVALID,
        verification_blockers=live_step3_verification.get("verification_blockers"),
        promotion_expires_at=live_step3_verification.get("promotion_expires_at"),
    )

    # 2. Step 3 promoted audit-only marker.
    add_check(
        "step3_promoted_marker_present",
        step3_promoted_marker is not None,
        BLOCKER_STEP3_MARKER_MISSING,
    )
    add_check("step3_promoted_marker_is_mapping", marker is not None, BLOCKER_STEP3_MARKER_MALFORMED)
    marker_allowed_actions = _string_items(marker.get("allowed_actions")) if marker else []
    marker_blocked_actions = _string_items(marker.get("blocked_actions")) if marker else []
    live_effective_hash = _str_or_none(live_step3_verification.get("effective_handoff_sha256"))
    live_pointer_effective_hash = _str_or_none(
        live_step3_verification.get("pointer_effective_handoff_sha256")
    )
    pointer_hash = _sha256_of(pointer) if pointer is not None else None
    step2_marker_hash = (
        _sha256_of(step2_promoted_marker) if step2_promoted_marker is not None else None
    )
    step2_packet_hash = (
        _sha256_of(step2_decision_packet) if step2_decision_packet is not None else None
    )
    if marker is not None:
        add_check(
            "step3_promoted_marker_schema_expected",
            marker.get("schema_version") == EXPECTED_STEP3_MARKER_SCHEMA_VERSION,
            BLOCKER_STEP3_MARKER_SCHEMA_INVALID,
            expected_schema=EXPECTED_STEP3_MARKER_SCHEMA_VERSION,
            actual_schema=marker.get("schema_version"),
        )
        add_check(
            "step3_promoted_marker_deterministic",
            marker.get("is_llm_generated") is False,
            BLOCKER_STEP3_MARKER_NOT_DETERMINISTIC,
            is_llm_generated=marker.get("is_llm_generated"),
        )
        mode_ok = (
            marker.get("mode") == EXPECTED_STEP3_MODE
            and marker.get("state") == STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY
            and marker.get("research_state")
            == STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY
            and marker.get("source") == PROMOTED_SOURCE
        )
        add_check(
            "step3_promoted_marker_mode_state_source",
            mode_ok,
            BLOCKER_STEP3_MARKER_MODE_INVALID,
            mode=marker.get("mode"),
            state=marker.get("state"),
            source=marker.get("source"),
        )
        add_check(
            "step3_promoted_marker_audit_only",
            marker.get("audit_only") is True,
            BLOCKER_STEP3_MARKER_NOT_AUDIT_ONLY,
            audit_only=marker.get("audit_only"),
        )
        add_check(
            "step3_promoted_marker_not_execution_authorization",
            marker.get("not_authorization") is True
            and marker.get("not_execution_authorization") is True,
            BLOCKER_STEP3_MARKER_MISSING_NOT_EXECUTION_AUTHORIZATION,
            not_authorization=marker.get("not_authorization"),
            not_execution_authorization=marker.get("not_execution_authorization"),
        )
        order_permission_implied = (
            marker.get("order_compilation_allowed") is not False
            or marker.get("new_buy_permission") is not False
            or marker.get("step4_allowed") is not False
            or marker.get("final_execution_allowed") is not False
            or marker.get("broker_automation_allowed") is not False
        )
        add_check(
            "step3_promoted_marker_no_order_permission_implied",
            not order_permission_implied,
            BLOCKER_STEP3_MARKER_ORDER_PERMISSION_IMPLIED,
            order_compilation_allowed=marker.get("order_compilation_allowed"),
            new_buy_permission=marker.get("new_buy_permission"),
            step4_allowed=marker.get("step4_allowed"),
            final_execution_allowed=marker.get("final_execution_allowed"),
            broker_automation_allowed=marker.get("broker_automation_allowed"),
        )
        add_check(
            "step3_promoted_marker_allowed_actions_exact",
            marker_allowed_actions == list(STEP3_AUDIT_ONLY_ALLOWED_ACTIONS),
            BLOCKER_STEP3_MARKER_ALLOWED_ACTIONS_INVALID,
            expected_allowed_actions=list(STEP3_AUDIT_ONLY_ALLOWED_ACTIONS),
            actual_allowed_actions=marker_allowed_actions,
        )
        add_check(
            "step3_promoted_marker_new_buy_absent",
            "NEW_BUY" not in marker_allowed_actions,
            BLOCKER_STEP3_MARKER_WIDENED_NEW_BUY,
            actual_allowed_actions=marker_allowed_actions,
        )
        add_check(
            "step3_promoted_marker_order_compilation_absent",
            "ORDER_COMPILATION" not in marker_allowed_actions,
            BLOCKER_STEP3_MARKER_WIDENED_ORDER_COMPILATION,
            actual_allowed_actions=marker_allowed_actions,
        )
        add_check(
            "step3_promoted_marker_order_actions_blocked",
            all(action in marker_blocked_actions for action in ORDER_ACTIONS),
            BLOCKER_STEP3_MARKER_BLOCKED_ACTIONS_INVALID,
            expected_blocked_actions=list(ORDER_ACTIONS),
            actual_blocked_actions=marker_blocked_actions,
        )
        add_check(
            "step3_promoted_marker_hashes_match_live_sources",
            _str_or_none(marker.get("effective_handoff_sha256")) is not None
            and marker.get("effective_handoff_sha256") == live_effective_hash
            and _str_or_none(marker.get("pointer_effective_handoff_sha256")) is not None
            and marker.get("pointer_effective_handoff_sha256") == live_pointer_effective_hash
            and _str_or_none(marker.get("active_pointer_sha256")) is not None
            and marker.get("active_pointer_sha256") == pointer_hash
            and _str_or_none(marker.get("step2_promoted_marker_sha256")) is not None
            and marker.get("step2_promoted_marker_sha256") == step2_marker_hash
            and _str_or_none(marker.get("step2_decision_packet_sha256")) is not None
            and marker.get("step2_decision_packet_sha256") == step2_packet_hash,
            BLOCKER_STEP3_MARKER_HASH_MISMATCH,
            marker_effective_handoff_sha256=marker.get("effective_handoff_sha256"),
            live_effective_handoff_sha256=live_effective_hash,
            marker_active_pointer_sha256=marker.get("active_pointer_sha256"),
            live_active_pointer_sha256=pointer_hash,
            marker_step2_promoted_marker_sha256=marker.get("step2_promoted_marker_sha256"),
            live_step2_promoted_marker_sha256=step2_marker_hash,
        )

    # 3. Step 3 downstream block (the artifact that keeps Step 4 closed).
    add_check(
        "step3_downstream_block_present",
        step3_downstream_block is not None,
        BLOCKER_STEP3_BLOCK_MISSING,
    )
    add_check("step3_downstream_block_is_mapping", block is not None, BLOCKER_STEP3_BLOCK_MALFORMED)
    if block is not None:
        add_check(
            "step3_downstream_block_schema_expected",
            block.get("schema_version") == EXPECTED_STEP3_BLOCK_SCHEMA_VERSION,
            BLOCKER_STEP3_BLOCK_SCHEMA_INVALID,
            expected_schema=EXPECTED_STEP3_BLOCK_SCHEMA_VERSION,
            actual_schema=block.get("schema_version"),
        )
        add_check(
            "step3_downstream_block_blocking",
            block.get("blocked") is True,
            BLOCKER_STEP3_BLOCK_NOT_BLOCKING,
            blocked=block.get("blocked"),
        )
        add_check(
            "step3_downstream_block_not_execution_authorization",
            block.get("not_execution_authorization") is True,
            BLOCKER_STEP3_BLOCK_MISSING_NOT_EXECUTION_AUTHORIZATION,
            not_execution_authorization=block.get("not_execution_authorization"),
        )
        add_check(
            "step3_downstream_block_reason_no_order_compilation",
            block.get("reason") == EXPECTED_STEP3_BLOCK_REASON,
            BLOCKER_STEP3_BLOCK_REASON_INVALID,
            expected_reason=EXPECTED_STEP3_BLOCK_REASON,
            actual_reason=block.get("reason"),
        )

    # 4. Step 3 audit actually produced output (existence/hash only; LLM prose
    # is NEVER parsed as authorization).
    audit_output_present = (
        isinstance(step3_audit_output_text, str) and bool(step3_audit_output_text.strip())
    )
    add_check(
        "step3_audit_output_present",
        audit_output_present,
        BLOCKER_STEP3_AUDIT_OUTPUT_MISSING,
    )

    # 5. Legacy stale artifact guard: a leftover audited_decision_packet.json
    # from a prior legacy run must never coexist with a promoted readiness pass.
    add_check(
        "no_stale_legacy_audited_packet",
        legacy_audited_packet_present is not True,
        BLOCKER_STALE_LEGACY_AUDITED_PACKET,
        legacy_audited_packet_present=legacy_audited_packet_present,
    )

    # 6. Raw Deep Research source guard (token-level, 6f.1 semantics).
    raw_source_used = _raw_deep_research_source_used(source_artifacts)
    add_check(
        "future_step4_sources_exclude_raw_deep_research",
        not raw_source_used,
        BLOCKER_RAW_DEEP_RESEARCH_SOURCE,
        raw_deep_research_source_used=raw_source_used,
    )

    # 7. Deterministic settings / budget readiness (existence + shape only;
    # 7b computes no preview rows and no budget math).
    add_check(
        "strategy_settings_available",
        settings is not None,
        BLOCKER_SETTINGS_UNAVAILABLE,
    )
    hard_cap_valid = settings is not None and _non_negative_finite_number(
        settings.get("hard_cap_open_orders_budget")
    )
    add_check(
        "hard_cap_open_orders_budget_deterministic",
        hard_cap_valid,
        BLOCKER_HARD_CAP_BUDGET_INVALID,
        hard_cap_open_orders_budget=settings.get("hard_cap_open_orders_budget")
        if settings
        else None,
    )
    target_budget_valid = settings is not None and _non_negative_finite_number(
        settings.get("target_new_buy_budget_this_run")
    )
    add_check(
        "target_new_buy_budget_deterministic",
        target_budget_valid,
        BLOCKER_TARGET_NEW_BUY_BUDGET_INVALID,
        target_new_buy_budget_this_run=settings.get("target_new_buy_budget_this_run")
        if settings
        else None,
    )
    max_new_tickers_total = (
        _max_new_tickers_per_week_total(settings.get("max_new_tickers_per_week"))
        if settings is not None
        else None
    )
    add_check(
        "max_new_tickers_per_week_deterministic",
        max_new_tickers_total is not None and max_new_tickers_total >= 0,
        BLOCKER_MAX_NEW_TICKERS_INVALID,
        max_new_tickers_per_week=settings.get("max_new_tickers_per_week") if settings else None,
        derived_total=max_new_tickers_total,
    )
    snapshot_parse_ok = False
    if isinstance(portfolio_snapshot_text, str) and portfolio_snapshot_text.strip():
        try:
            snapshot_parse_ok = (
                parse_existing_buy_open_orders_summary(portfolio_snapshot_text).section_present
                is True
            )
        except Exception:  # noqa: BLE001 - fail closed on any parser surprise
            snapshot_parse_ok = False
    add_check(
        "portfolio_snapshot_parseable",
        snapshot_parse_ok,
        BLOCKER_PORTFOLIO_SNAPSHOT_INVALID,
    )
    universe = packet.get("effective_allowed_buy_universe") if packet else None
    universe_valid = (
        isinstance(universe, list)
        and bool(universe)
        and all(isinstance(item, str) and item.strip() for item in universe)
    )
    add_check(
        "effective_allowed_buy_universe_present",
        universe_valid,
        BLOCKER_BUY_UNIVERSE_INVALID,
        effective_allowed_buy_universe=universe,
    )

    return _verification_result(
        valid=not blockers,
        blockers=blockers,
        warnings=warnings,
        checks=checks,
        live_step3_verification=live_step3_verification,
        active_pointer=pointer,
        effective_handoff=effective_handoff,
        step2_promoted_marker=step2_promoted_marker,
        step2_decision_packet=step2_decision_packet,
        step3_promoted_marker=marker,
        step3_downstream_block=block,
        step3_audit_output_text=step3_audit_output_text,
        source_artifacts=source_artifacts,
    )


def _evaluate_dry_run(
    *,
    research_decision: Mapping[str, Any] | None,
    promoted_step4_verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    decision = research_decision if isinstance(research_decision, Mapping) else None
    verification = (
        promoted_step4_verification
        if isinstance(promoted_step4_verification, Mapping)
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
            "decision_state_step3_audit_only",
            current_state == STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY
            and decision.get("promoted_step3_audit_only") is True,
            DRY_RUN_BLOCKER_DECISION_NOT_STEP3_AUDIT_ONLY,
            expected_state=STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY,
            actual_state=current_state,
            promoted_step3_audit_only=decision.get("promoted_step3_audit_only"),
        )
        add_check(
            "decision_allowed_actions_exact_step3_audit_only",
            current_allowed_actions == list(STEP3_AUDIT_ONLY_ALLOWED_ACTIONS),
            DRY_RUN_BLOCKER_DECISION_ALLOWED_ACTIONS_INVALID,
            expected_allowed_actions=list(STEP3_AUDIT_ONLY_ALLOWED_ACTIONS),
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
        "promoted_step4_verification_present",
        verification is not None,
        DRY_RUN_BLOCKER_VERIFICATION_MISSING,
    )
    verification_valid = False
    if verification is not None:
        verification_valid = (
            verification.get("schema_version") == VERIFICATION_SCHEMA_VERSION
            and verification.get("valid_for_promoted_step4_preview") is True
            and verification.get("verification_blockers") == []
            and verification.get("report_only") is True
            and verification.get("permission_effect") == "none"
            and verification.get("not_authorization") is True
            and verification.get("not_execution_authorization") is True
            and verification.get("raw_deep_research_source_used") is False
        )
        add_check(
            "promoted_step4_verification_valid_no_blockers",
            verification_valid,
            DRY_RUN_BLOCKER_VERIFICATION_INVALID,
            schema_version=verification.get("schema_version"),
            valid_for_promoted_step4_preview=verification.get(
                "valid_for_promoted_step4_preview"
            ),
            verification_blockers=verification.get("verification_blockers"),
            report_only=verification.get("report_only"),
            permission_effect=verification.get("permission_effect"),
        )

    # Self-consistency pattern (R2E.5b-6b/6e): would_allow is computed BEFORE
    # the policy check, and the policy blocker is then appended so the artifact
    # itself testifies the real gate is still closed. A future 7d upgrade must
    # REQUIRE this blocker to be present.
    would_allow = not blockers
    current_real_gate_allows = False
    add_check(
        "current_real_gate_allows",
        current_real_gate_allows,
        DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY,
        severity=SEVERITY_POLICY,
        note=(
            "No real Step 4 preview permission exists in R2E.5b-7b. "
            "would_allow_promoted_step4_preview is diagnostic only."
        ),
    )

    return _dry_run_result(
        would_allow=would_allow,
        current_state=current_state,
        current_allowed_actions=current_allowed_actions,
        verification_valid=verification_valid,
        blockers=blockers,
        warnings=warnings,
        checks=checks,
        promoted_step4_verification=verification,
    )


def _verification_result(
    *,
    valid: bool,
    blockers: list[str],
    warnings: list[str],
    checks: list[dict[str, Any]],
    live_step3_verification: Mapping[str, Any] | None,
    active_pointer: Mapping[str, Any] | None,
    effective_handoff: Mapping[str, Any] | None,
    step2_promoted_marker: Mapping[str, Any] | None,
    step2_decision_packet: Mapping[str, Any] | None,
    step3_promoted_marker: Mapping[str, Any] | None,
    step3_downstream_block: Mapping[str, Any] | None,
    step3_audit_output_text: str | None,
    source_artifacts: Mapping[str, str] | None,
) -> dict[str, Any]:
    live = live_step3_verification if isinstance(live_step3_verification, Mapping) else {}
    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "valid_for_promoted_step4_preview": bool(valid),
        # Mirrors the valid flag so every 7b artifact carries the explicit
        # would-allow diagnostic; the real gate simulation lives in the dry-run.
        "would_allow_promoted_step4_preview": bool(valid),
        "current_real_gate_allows": False,
        "verification_blockers": list(blockers),
        "verification_warnings": list(warnings),
        "checks": list(checks),
        "source": PROMOTED_SOURCE,
        "future_state_required": FUTURE_STATE_REQUIRED,
        "future_action_required": FUTURE_ACTION_REQUIRED,
        "promotion_status": live.get("promotion_status"),
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
        "step3_promoted_marker_sha256": (
            _sha256_of(step3_promoted_marker) if step3_promoted_marker is not None else None
        ),
        "step3_downstream_block_sha256": (
            _sha256_of(step3_downstream_block) if step3_downstream_block is not None else None
        ),
        "step3_audit_output_sha256": _sha256_of_text(step3_audit_output_text),
        "live_step3_verification_valid": live.get("valid_for_promoted_step3_audit") is True,
        "live_step3_verification_blockers": list(live.get("verification_blockers") or []),
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
            "step3_promoted_audit_only": (
                _sha256_of(step3_promoted_marker) if step3_promoted_marker is not None else None
            ),
            "step3_promoted_audit_only_downstream_block": (
                _sha256_of(step3_downstream_block)
                if step3_downstream_block is not None
                else None
            ),
            "step3_template3_audit": _sha256_of_text(step3_audit_output_text),
        },
        "consumed_by_availability": False,
        "consumed_by_step4": False,
        "consumed_by_gates": False,
    }


def _dry_run_result(
    *,
    would_allow: bool,
    current_state: str | None,
    current_allowed_actions: list[str],
    verification_valid: bool,
    blockers: list[str],
    warnings: list[str],
    checks: list[dict[str, Any]],
    promoted_step4_verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    verification = (
        promoted_step4_verification
        if isinstance(promoted_step4_verification, Mapping)
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
        "would_allow_promoted_step4_preview": bool(would_allow),
        "current_real_gate_allows": False,
        "future_state_required": FUTURE_STATE_REQUIRED,
        "future_action_required": FUTURE_ACTION_REQUIRED,
        "current_state": current_state,
        "current_allowed_actions": list(current_allowed_actions),
        "verification_valid_for_promoted_step4_preview": bool(verification_valid),
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "step4_allowed": False,
        "final_execution_allowed": False,
        "broker_automation_allowed": False,
        "dry_run_blockers": list(blockers),
        "dry_run_warnings": list(warnings),
        "checks": list(checks),
        "raw_deep_research_source_used": verification.get("raw_deep_research_source_used") is True,
        "source_artifacts": dict(verification.get("source_artifacts") or {}),
        "source_artifact_hashes": dict(verification.get("source_artifact_hashes") or {}),
        "freshness": {
            "promotion_expires_at": verification.get("promotion_expires_at"),
            "promotion_status": verification.get("promotion_status"),
        },
        "consumed_by_availability": False,
        "consumed_by_step4": False,
        "consumed_by_gates": False,
    }


def _max_new_tickers_per_week_total(value: Any) -> int | None:
    """Mirror of ``step4_order_compiler._max_new_tickers_per_week_total``.

    Kept as a local mirror (importing workflow here would be circular); a
    drift-guard unit test asserts behavioral parity on representative inputs.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Mapping):
        leaves = [v for v in value.values() if isinstance(v, int) and not isinstance(v, bool)]
        return sum(leaves) if leaves else None
    return None


def _non_negative_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and value >= 0


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


def _sha256_of_text(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
