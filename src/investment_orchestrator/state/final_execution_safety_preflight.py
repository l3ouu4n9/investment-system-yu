"""Report-only, ROWLESS final-safety preflight for the promoted path.

R2E.5b-7c: this module answers "if the system were ever to approach Step 4 /
order readiness in the future, which deterministic final-safety and budget/cap
conditions are currently satisfied or missing?" — and nothing else.

It grants nothing. It opens no Step 4 path, adds no state or action, changes no
gate, and is consumed by nothing (not availability, not Step 4, not the final
execution safety gate, not weekly). It is rowless by construction: it never
produces order rows, preview rows, notional rows, ticker selections, budget
allocations, manual-order artifacts, or broker-ready artifacts — only aggregate
counts/totals and pass/fail diagnostics.

The final gate itself is reused strictly as a PURE function
(``evaluate_final_execution_safety``); its behavior is not modified. On the
promoted path the gate stays closed, and this artifact's own ``preflight_passed``
is false BY DESIGN while the real gate remains closed: the policy blockers
(``final_gate_still_closed_by_policy`` / ``order_compilation_not_allowed`` /
``state_not_literal_strict_fresh``) are appended after the deterministic
diagnostics so the artifact itself testifies that no order path is open. A
future explicit PR (and its post-audit) must REQUIRE those blockers here.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from typing import Any

from investment_orchestrator.parsers.portfolio_snapshot_existing_orders import (
    parse_existing_buy_open_orders_summary,
)
from investment_orchestrator.state.final_execution_safety_gate import (
    ACTIONABLE_REQUIRED_STATE,
    REQUIRED_ALLOWED_ACTION,
    evaluate_final_execution_safety,
)
from investment_orchestrator.state.research_availability import (
    STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY,
)


PREFLIGHT_SCHEMA_VERSION = "promoted_final_safety_preflight_v1"

# Mirrored literals from the research layer (importing ``research.*`` here would
# break the state->research layering this repo deliberately keeps one-way).
# Drift-guard unit tests assert these equal the real constants.
EXPECTED_STEP4_VERIFICATION_SCHEMA_VERSION = "promoted_step4_readiness_verification_v1"
EXPECTED_STEP4_DRY_RUN_SCHEMA_VERSION = "promoted_step4_preview_gate_dry_run_v1"
EXPECTED_REAL_GATE_POLICY_BLOCKER = "real_gate_still_closed_by_policy"

SEVERITY_BLOCKER = "blocker"
SEVERITY_POLICY = "policy"

# Policy blockers: present BY DESIGN while the real gate stays closed. They are
# facts about the current permission model, not defects in the inputs.
BLOCKER_FINAL_GATE_STILL_CLOSED_BY_POLICY = "final_gate_still_closed_by_policy"
BLOCKER_ORDER_COMPILATION_NOT_ALLOWED = "order_compilation_not_allowed"
BLOCKER_STATE_NOT_LITERAL_STRICT_FRESH = "state_not_literal_strict_fresh"

# Deterministic (non-policy) blockers: missing/malformed/stale prerequisites.
BLOCKER_DECISION_MISSING = "final_gate_input_decision_missing"
BLOCKER_FINAL_GATE_UNEXPECTEDLY_READY = "final_gate_unexpectedly_ready_in_report_only_preflight"
BLOCKER_STEP4_VERIFICATION_MISSING = "step4_readiness_verification_missing"
BLOCKER_STEP4_VERIFICATION_NOT_REPORT_ONLY = "step4_readiness_verification_not_report_only"
BLOCKER_STEP4_VERIFICATION_NOT_VALID = "step4_readiness_verification_not_valid"
BLOCKER_STEP4_DRY_RUN_MISSING = "step4_readiness_dry_run_missing"
BLOCKER_STEP4_DRY_RUN_NOT_REPORT_ONLY = "step4_readiness_dry_run_not_report_only"
BLOCKER_STEP4_DRY_RUN_REAL_GATE_NOT_CLOSED = "step4_readiness_dry_run_real_gate_not_closed"
BLOCKER_STEP4_DRY_RUN_POLICY_BLOCKER_MISSING = "step4_readiness_dry_run_policy_blocker_missing"
BLOCKER_STEP4_DRY_RUN_NOT_WOULD_ALLOW = "step4_readiness_dry_run_not_would_allow"
BLOCKER_BUDGET_SETTINGS_MISSING = "budget_settings_missing"
BLOCKER_BUDGET_SETTINGS_INVALID = "budget_settings_invalid"
BLOCKER_PORTFOLIO_SNAPSHOT_MISSING = "portfolio_snapshot_missing"
BLOCKER_PORTFOLIO_SNAPSHOT_UNPARSEABLE = "portfolio_snapshot_unparseable"
BLOCKER_HARD_CAP_HEADROOM_NOT_COMPUTABLE = "hard_cap_headroom_not_computable"
BLOCKER_BUY_UNIVERSE_MISSING = "effective_allowed_buy_universe_missing_or_empty"
BLOCKER_PREFLIGHT_INTERNAL_ERROR = "final_safety_preflight_internal_error"

# The report-only boundary every 7b artifact must carry to be summarized here.
_STEP4_ARTIFACT_ORDER_FLAGS = (
    "order_compilation_allowed",
    "new_buy_permission",
    "step4_allowed",
    "final_execution_allowed",
    "broker_automation_allowed",
)
_STEP4_ARTIFACT_CONSUMED_FLAGS = (
    "consumed_by_availability",
    "consumed_by_step4",
    "consumed_by_gates",
)


def evaluate_promoted_final_safety_preflight(
    *,
    research_decision: Mapping[str, Any] | None,
    step4_readiness_verification: Mapping[str, Any] | None,
    step4_preview_gate_dry_run: Mapping[str, Any] | None,
    strategy_settings: Mapping[str, Any] | None,
    portfolio_snapshot_text: str | None,
    step2_decision_packet: Mapping[str, Any] | None = None,
    step2_promoted_marker: Mapping[str, Any] | None = None,
    step3_promoted_marker: Mapping[str, Any] | None = None,
    step3_downstream_block: Mapping[str, Any] | None = None,
    source_artifacts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the rowless final-safety preflight report. Report-only, fail-closed."""
    try:
        return _evaluate(
            research_decision=research_decision,
            step4_readiness_verification=step4_readiness_verification,
            step4_preview_gate_dry_run=step4_preview_gate_dry_run,
            strategy_settings=strategy_settings,
            portfolio_snapshot_text=portfolio_snapshot_text,
            step2_decision_packet=step2_decision_packet,
            step2_promoted_marker=step2_promoted_marker,
            step3_promoted_marker=step3_promoted_marker,
            step3_downstream_block=step3_downstream_block,
            source_artifacts=source_artifacts,
        )
    except Exception as exc:  # noqa: BLE001 - report-only preflight must never raise
        return _preflight_result(
            deterministic_prerequisites_ready=False,
            blockers=[
                BLOCKER_PREFLIGHT_INTERNAL_ERROR,
                BLOCKER_FINAL_GATE_STILL_CLOSED_BY_POLICY,
            ],
            warnings=[],
            checks=[
                _check(
                    "final_safety_preflight_never_raise_fallback",
                    False,
                    BLOCKER_PREFLIGHT_INTERNAL_ERROR,
                    details={"error": str(exc)},
                )
            ],
            current_state=None,
            current_allowed_actions=[],
            final_gate_diagnostics=None,
            step4_readiness_summary=None,
            budget_cap_readiness=None,
            research_decision=None,
            step4_readiness_verification=None,
            step4_preview_gate_dry_run=None,
            strategy_settings=None,
            portfolio_snapshot_text=None,
            step2_promoted_marker=None,
            step2_decision_packet=None,
            step3_promoted_marker=None,
            step3_downstream_block=None,
            source_artifacts=source_artifacts,
        )


def _evaluate(
    *,
    research_decision: Mapping[str, Any] | None,
    step4_readiness_verification: Mapping[str, Any] | None,
    step4_preview_gate_dry_run: Mapping[str, Any] | None,
    strategy_settings: Mapping[str, Any] | None,
    portfolio_snapshot_text: str | None,
    step2_decision_packet: Mapping[str, Any] | None,
    step2_promoted_marker: Mapping[str, Any] | None,
    step3_promoted_marker: Mapping[str, Any] | None,
    step3_downstream_block: Mapping[str, Any] | None,
    source_artifacts: Mapping[str, str] | None,
) -> dict[str, Any]:
    decision = research_decision if isinstance(research_decision, Mapping) else None
    verification = (
        step4_readiness_verification
        if isinstance(step4_readiness_verification, Mapping)
        else None
    )
    dry_run = (
        step4_preview_gate_dry_run if isinstance(step4_preview_gate_dry_run, Mapping) else None
    )
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else None
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
            target = blockers if severity in (SEVERITY_BLOCKER, SEVERITY_POLICY) else warnings
            if reason_code not in target:
                target.append(reason_code)

    # 1. Final gate input (the degraded-mode decision the real gate reads).
    add_check(
        "final_gate_input_decision_present",
        decision is not None,
        BLOCKER_DECISION_MISSING,
    )
    current_state = _str_or_none(decision.get("state")) if decision else None
    current_allowed_actions = _string_items(decision.get("allowed_actions")) if decision else []

    # 2. Existing final gate conditions, via the UNCHANGED pure evaluator. The
    # legacy Step 2/3 packets are deliberately passed as absent: on the promoted
    # audit-only path they must not exist, and this preflight never fabricates
    # them. The gate is expected to be closed; that is recorded, not "fixed".
    gate_result = evaluate_final_execution_safety(
        step2_decision_packet=None,
        step3_audited_packet=None,
        step1_permission=decision,
    )
    add_check(
        "final_gate_not_unexpectedly_ready",
        gate_result.ready_for_order_compilation is False,
        BLOCKER_FINAL_GATE_UNEXPECTEDLY_READY,
        ready_for_order_compilation=gate_result.ready_for_order_compilation,
    )
    state_is_literal_strict_fresh = (
        decision is not None and current_state == ACTIONABLE_REQUIRED_STATE
    )
    order_compilation_in_allowed_actions = REQUIRED_ALLOWED_ACTION in current_allowed_actions
    final_gate_diagnostics = {
        "evaluator": "final_execution_safety_gate.evaluate_final_execution_safety",
        "evaluator_behavior_modified": False,
        "required_state": ACTIONABLE_REQUIRED_STATE,
        "required_allowed_action": REQUIRED_ALLOWED_ACTION,
        "state_is_literal_strict_fresh": state_is_literal_strict_fresh,
        "order_compilation_in_allowed_actions": order_compilation_in_allowed_actions,
        "current_state_is_promoted_step3_audit_only": (
            current_state == STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY
        ),
        "ready_for_order_compilation": gate_result.ready_for_order_compilation,
        "blocked": gate_result.blocked,
        "recommended_result": gate_result.recommended_result,
        "checked_conditions": dict(gate_result.checked_conditions),
        "fail_reasons": list(gate_result.fail_reasons),
    }

    # 3. 7b readiness verification artifact: summarized, never consumed as
    # authority. It must carry the full report-only boundary to be summarized.
    add_check(
        "step4_readiness_verification_present",
        verification is not None,
        BLOCKER_STEP4_VERIFICATION_MISSING,
    )
    verification_report_only = verification is not None and (
        verification.get("schema_version") == EXPECTED_STEP4_VERIFICATION_SCHEMA_VERSION
        and verification.get("is_llm_generated") is False
        and verification.get("report_only") is True
        and verification.get("permission_effect") == "none"
        and verification.get("not_authorization") is True
        and verification.get("not_execution_authorization") is True
        and verification.get("current_real_gate_allows") is False
        and all(verification.get(flag) is False for flag in _STEP4_ARTIFACT_ORDER_FLAGS)
        and all(verification.get(flag) is False for flag in _STEP4_ARTIFACT_CONSUMED_FLAGS)
    )
    if verification is not None:
        add_check(
            "step4_readiness_verification_report_only_boundary",
            verification_report_only,
            BLOCKER_STEP4_VERIFICATION_NOT_REPORT_ONLY,
            schema_version=verification.get("schema_version"),
            report_only=verification.get("report_only"),
            permission_effect=verification.get("permission_effect"),
            current_real_gate_allows=verification.get("current_real_gate_allows"),
        )
        add_check(
            "step4_readiness_verification_valid",
            verification.get("valid_for_promoted_step4_preview") is True
            and verification.get("verification_blockers") == [],
            BLOCKER_STEP4_VERIFICATION_NOT_VALID,
            valid_for_promoted_step4_preview=verification.get(
                "valid_for_promoted_step4_preview"
            ),
            verification_blockers=verification.get("verification_blockers"),
        )

    # 4. 7b preview-gate dry-run artifact: same treatment. The dry-run must
    # itself testify the real gate is closed (6b/6e self-consistency pattern).
    add_check(
        "step4_readiness_dry_run_present",
        dry_run is not None,
        BLOCKER_STEP4_DRY_RUN_MISSING,
    )
    dry_run_policy_blocker_present = False
    if dry_run is not None:
        dry_run_report_only = (
            dry_run.get("schema_version") == EXPECTED_STEP4_DRY_RUN_SCHEMA_VERSION
            and dry_run.get("is_llm_generated") is False
            and dry_run.get("report_only") is True
            and dry_run.get("dry_run_only") is True
            and dry_run.get("permission_effect") == "none"
            and dry_run.get("not_authorization") is True
            and dry_run.get("not_execution_authorization") is True
            and all(dry_run.get(flag) is False for flag in _STEP4_ARTIFACT_ORDER_FLAGS)
            and all(dry_run.get(flag) is False for flag in _STEP4_ARTIFACT_CONSUMED_FLAGS)
        )
        add_check(
            "step4_readiness_dry_run_report_only_boundary",
            dry_run_report_only,
            BLOCKER_STEP4_DRY_RUN_NOT_REPORT_ONLY,
            schema_version=dry_run.get("schema_version"),
            report_only=dry_run.get("report_only"),
            dry_run_only=dry_run.get("dry_run_only"),
            permission_effect=dry_run.get("permission_effect"),
        )
        add_check(
            "step4_readiness_dry_run_real_gate_closed",
            dry_run.get("current_real_gate_allows") is False,
            BLOCKER_STEP4_DRY_RUN_REAL_GATE_NOT_CLOSED,
            current_real_gate_allows=dry_run.get("current_real_gate_allows"),
        )
        dry_run_policy_blocker_present = EXPECTED_REAL_GATE_POLICY_BLOCKER in _string_items(
            dry_run.get("dry_run_blockers")
        )
        add_check(
            "step4_readiness_dry_run_policy_blocker_present",
            dry_run_policy_blocker_present,
            BLOCKER_STEP4_DRY_RUN_POLICY_BLOCKER_MISSING,
            expected_policy_blocker=EXPECTED_REAL_GATE_POLICY_BLOCKER,
            dry_run_blockers=dry_run.get("dry_run_blockers"),
        )
        add_check(
            "step4_readiness_dry_run_would_allow",
            dry_run.get("would_allow_promoted_step4_preview") is True,
            BLOCKER_STEP4_DRY_RUN_NOT_WOULD_ALLOW,
            would_allow_promoted_step4_preview=dry_run.get(
                "would_allow_promoted_step4_preview"
            ),
        )

    step4_readiness_summary = {
        "verification_present": verification is not None,
        "verification_schema_version": verification.get("schema_version") if verification else None,
        "verification_report_only_boundary_ok": bool(verification_report_only),
        "verification_valid_for_promoted_step4_preview": (
            verification.get("valid_for_promoted_step4_preview") is True if verification else False
        ),
        "dry_run_present": dry_run is not None,
        "dry_run_schema_version": dry_run.get("schema_version") if dry_run else None,
        "dry_run_would_allow_promoted_step4_preview": (
            dry_run.get("would_allow_promoted_step4_preview") is True if dry_run else False
        ),
        "dry_run_current_real_gate_allows": (
            dry_run.get("current_real_gate_allows") if dry_run else None
        ),
        "dry_run_real_gate_policy_blocker_present": dry_run_policy_blocker_present,
        "consumed_as_gate_authority": False,
    }

    # 5. Deterministic budget/cap readiness — ROWLESS. Aggregate counts and
    # totals only: no ticker selection, no per-ticker rows, no allocation.
    add_check(
        "strategy_settings_parseable",
        settings is not None,
        BLOCKER_BUDGET_SETTINGS_MISSING,
    )
    hard_cap = _decimal_or_none(settings.get("hard_cap_open_orders_budget")) if settings else None
    add_check(
        "hard_cap_open_orders_budget_valid",
        settings is not None and hard_cap is not None and hard_cap >= 0,
        BLOCKER_BUDGET_SETTINGS_INVALID,
        hard_cap_open_orders_budget=settings.get("hard_cap_open_orders_budget")
        if settings
        else None,
    )
    if hard_cap is not None and hard_cap < 0:
        hard_cap = None
    target_budget = (
        _decimal_or_none(settings.get("target_new_buy_budget_this_run")) if settings else None
    )
    add_check(
        "target_new_buy_budget_this_run_valid",
        settings is not None and target_budget is not None and target_budget >= 0,
        BLOCKER_BUDGET_SETTINGS_INVALID,
        target_new_buy_budget_this_run=settings.get("target_new_buy_budget_this_run")
        if settings
        else None,
    )
    if target_budget is not None and target_budget < 0:
        target_budget = None
    max_new_tickers_total = (
        _max_new_tickers_per_week_total(settings.get("max_new_tickers_per_week"))
        if settings is not None
        else None
    )
    add_check(
        "max_new_tickers_per_week_derivable",
        max_new_tickers_total is not None and max_new_tickers_total >= 0,
        BLOCKER_BUDGET_SETTINGS_INVALID,
        max_new_tickers_per_week=settings.get("max_new_tickers_per_week") if settings else None,
        derived_total=max_new_tickers_total,
    )
    if max_new_tickers_total is not None and max_new_tickers_total < 0:
        max_new_tickers_total = None

    snapshot_present = isinstance(portfolio_snapshot_text, str) and bool(
        portfolio_snapshot_text.strip()
    )
    add_check(
        "portfolio_snapshot_present",
        snapshot_present,
        BLOCKER_PORTFOLIO_SNAPSHOT_MISSING,
    )
    section_present = False
    ticker_count: int | None = None
    rows_with_data_gaps: int | None = None
    rows_missing_budget: int | None = None
    total_budget: Decimal | None = None
    total_stated_notional: Decimal | None = None
    if snapshot_present:
        try:
            parsed = parse_existing_buy_open_orders_summary(portfolio_snapshot_text)
            section_present = parsed.section_present is True
            if section_present:
                rows = list(parsed.orders.values())
                ticker_count = len(rows)
                rows_with_data_gaps = sum(1 for row in rows if row.data_gap)
                rows_missing_budget = sum(1 for row in rows if row.budget is None)
                total_budget = sum(
                    (row.budget for row in rows if row.budget is not None), Decimal("0")
                )
                stated = [
                    row.stated_compiled_notional
                    for row in rows
                    if row.stated_compiled_notional is not None
                ]
                total_stated_notional = sum(stated, Decimal("0")) if stated else Decimal("0")
        except Exception:  # noqa: BLE001 - fail closed on any parser surprise
            section_present = False
    add_check(
        "portfolio_snapshot_section_2a_parseable",
        section_present,
        BLOCKER_PORTFOLIO_SNAPSHOT_UNPARSEABLE if snapshot_present else None,
    )

    # Hard-cap headroom counts every existing (2a) target budget against the cap
    # (G3 semantics: total open-order exposure, KEEP_EXISTING included). It is
    # only reported when EVERY row parsed cleanly with a budget — fail closed.
    headroom_computable = (
        hard_cap is not None
        and section_present
        and rows_with_data_gaps == 0
        and rows_missing_budget == 0
        and total_budget is not None
    )
    add_check(
        "hard_cap_headroom_computable",
        headroom_computable,
        BLOCKER_HARD_CAP_HEADROOM_NOT_COMPUTABLE,
        rows_with_data_gaps=rows_with_data_gaps,
        rows_missing_budget=rows_missing_budget,
    )
    hard_cap_headroom = hard_cap - total_budget if headroom_computable else None
    # Rowless by construction: this run has compiled ZERO net-new rows, so the
    # per-run net-new notional is 0 and the target headroom is the full target
    # (G5 semantics). No rows are created to "use" any of it here.
    net_new_notional_this_run = Decimal("0")
    target_budget_headroom = (
        target_budget - net_new_notional_this_run if target_budget is not None else None
    )
    effective_new_buy_headroom = (
        min(target_budget_headroom, hard_cap_headroom)
        if target_budget_headroom is not None and hard_cap_headroom is not None
        else None
    )
    net_new_tickers_this_run = 0
    remaining_new_ticker_slots = (
        max_new_tickers_total - net_new_tickers_this_run
        if max_new_tickers_total is not None
        else None
    )

    universe = packet.get("effective_allowed_buy_universe") if packet else None
    universe_valid = (
        isinstance(universe, list)
        and bool(universe)
        and all(isinstance(item, str) and item.strip() for item in universe)
    )
    add_check(
        "effective_allowed_buy_universe_present_non_empty",
        universe_valid,
        BLOCKER_BUY_UNIVERSE_MISSING,
        effective_allowed_buy_universe_size=len(universe) if isinstance(universe, list) else None,
    )

    budget_cap_readiness = {
        "rowless": True,
        "contains_order_rows": False,
        "contains_preview_rows": False,
        "strategy_settings_parseable": settings is not None,
        "hard_cap_open_orders_budget": _decimal_str(hard_cap),
        "target_new_buy_budget_this_run": _decimal_str(target_budget),
        "max_new_tickers_per_week_total": max_new_tickers_total,
        "portfolio_snapshot_present": snapshot_present,
        "portfolio_snapshot_section_2a_present": section_present,
        "existing_open_buy_orders_ticker_count": ticker_count,
        "existing_open_buy_orders_rows_with_data_gaps": rows_with_data_gaps,
        "existing_open_buy_orders_rows_missing_budget": rows_missing_budget,
        "existing_open_buy_orders_total_target_budget": _decimal_str(total_budget),
        "existing_open_buy_orders_total_stated_notional": _decimal_str(total_stated_notional),
        "hard_cap_headroom_computable": headroom_computable,
        "hard_cap_headroom": _decimal_str(hard_cap_headroom),
        "net_new_notional_this_run": _decimal_str(net_new_notional_this_run),
        "target_new_buy_budget_headroom": _decimal_str(target_budget_headroom),
        "effective_new_buy_headroom": _decimal_str(effective_new_buy_headroom),
        "net_new_tickers_this_run": net_new_tickers_this_run,
        "remaining_new_ticker_slots": remaining_new_ticker_slots,
        "effective_allowed_buy_universe_present": universe_valid,
        "effective_allowed_buy_universe_size": (
            len(universe) if isinstance(universe, list) else None
        ),
    }

    # 6. Deterministic prerequisites verdict BEFORE the policy blockers, so the
    # artifact distinguishes "inputs broken" from "closed by policy" (the same
    # pattern as the 7b dry-run's would_allow / policy-blocker split).
    deterministic_prerequisites_ready = not blockers

    # 7. Policy blockers — appended LAST and unconditionally shaped so that
    # preflight_passed is false while the real gate stays closed. A future
    # explicit gate-opening PR must REQUIRE these blockers to be present here.
    add_check(
        "state_literal_strict_fresh",
        state_is_literal_strict_fresh,
        BLOCKER_STATE_NOT_LITERAL_STRICT_FRESH,
        severity=SEVERITY_POLICY,
        required_state=ACTIONABLE_REQUIRED_STATE,
        actual_state=current_state,
    )
    add_check(
        "order_compilation_in_allowed_actions",
        order_compilation_in_allowed_actions,
        BLOCKER_ORDER_COMPILATION_NOT_ALLOWED,
        severity=SEVERITY_POLICY,
        required_allowed_action=REQUIRED_ALLOWED_ACTION,
        actual_allowed_actions=current_allowed_actions,
    )
    add_check(
        "final_gate_open_for_promoted_path",
        False,
        BLOCKER_FINAL_GATE_STILL_CLOSED_BY_POLICY,
        severity=SEVERITY_POLICY,
        note=(
            "R2E.5b-7c opens no gate. This preflight is diagnostic only; the "
            "final execution safety gate remains closed for the promoted path "
            "and this artifact cannot open it."
        ),
    )

    return _preflight_result(
        deterministic_prerequisites_ready=deterministic_prerequisites_ready,
        blockers=blockers,
        warnings=warnings,
        checks=checks,
        current_state=current_state,
        current_allowed_actions=current_allowed_actions,
        final_gate_diagnostics=final_gate_diagnostics,
        step4_readiness_summary=step4_readiness_summary,
        budget_cap_readiness=budget_cap_readiness,
        research_decision=decision,
        step4_readiness_verification=verification,
        step4_preview_gate_dry_run=dry_run,
        strategy_settings=settings,
        portfolio_snapshot_text=portfolio_snapshot_text,
        step2_promoted_marker=step2_promoted_marker,
        step2_decision_packet=packet,
        step3_promoted_marker=step3_promoted_marker,
        step3_downstream_block=step3_downstream_block,
        source_artifacts=source_artifacts,
    )


def _preflight_result(
    *,
    deterministic_prerequisites_ready: bool,
    blockers: list[str],
    warnings: list[str],
    checks: list[dict[str, Any]],
    current_state: str | None,
    current_allowed_actions: list[str],
    final_gate_diagnostics: Mapping[str, Any] | None,
    step4_readiness_summary: Mapping[str, Any] | None,
    budget_cap_readiness: Mapping[str, Any] | None,
    research_decision: Mapping[str, Any] | None,
    step4_readiness_verification: Mapping[str, Any] | None,
    step4_preview_gate_dry_run: Mapping[str, Any] | None,
    strategy_settings: Mapping[str, Any] | None,
    portfolio_snapshot_text: str | None,
    step2_promoted_marker: Mapping[str, Any] | None,
    step2_decision_packet: Mapping[str, Any] | None,
    step3_promoted_marker: Mapping[str, Any] | None,
    step3_downstream_block: Mapping[str, Any] | None,
    source_artifacts: Mapping[str, str] | None,
) -> dict[str, Any]:
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "dry_run_only": True,
        "rowless": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "contains_order_rows": False,
        "contains_preview_rows": False,
        "current_real_gate_allows": False,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "step4_allowed": False,
        "final_execution_allowed": False,
        "broker_automation_allowed": False,
        # False by design while the policy blockers are present (the real gate
        # is closed); deterministic_prerequisites_ready carries the diagnostics.
        "preflight_passed": not blockers,
        "deterministic_prerequisites_ready": bool(deterministic_prerequisites_ready),
        "preflight_blockers": list(blockers),
        "preflight_warnings": list(warnings),
        "checks": list(checks),
        "current_state": current_state,
        "current_allowed_actions": list(current_allowed_actions),
        "final_gate_diagnostics": dict(final_gate_diagnostics or {}) or None,
        "step4_readiness_summary": dict(step4_readiness_summary or {}) or None,
        "budget_cap_readiness": dict(budget_cap_readiness or {}) or None,
        "source_artifacts": dict(source_artifacts or {}),
        "source_artifact_hashes": {
            "research_degraded_mode_decision": _sha256_of(research_decision),
            "promoted_step4_readiness_verification": _sha256_of(step4_readiness_verification),
            "promoted_step4_preview_gate_dry_run": _sha256_of(step4_preview_gate_dry_run),
            "step2_promoted_decision_only": _sha256_of(step2_promoted_marker),
            "step2_decision_packet": _sha256_of(step2_decision_packet),
            "step3_promoted_audit_only": _sha256_of(step3_promoted_marker),
            "step3_promoted_audit_only_downstream_block": _sha256_of(step3_downstream_block),
            "strategy_settings": _sha256_of(strategy_settings),
            "portfolio_snapshot": _sha256_of_text(portfolio_snapshot_text),
        },
        "consumed_by_availability": False,
        "consumed_by_step4": False,
        "consumed_by_gates": False,
    }


def _max_new_tickers_per_week_total(value: Any) -> int | None:
    """Mirror of ``step4_order_compiler._max_new_tickers_per_week_total``.

    Kept as a local mirror (a state->workflow import would be circular); a
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


def _decimal_or_none(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:  # pragma: no cover - str() of a finite number parses
        return None


def _decimal_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


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
