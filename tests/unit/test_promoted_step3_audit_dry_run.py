from __future__ import annotations

from datetime import date
import hashlib
import json
from typing import Any

from investment_orchestrator.research.actionable_handoff_candidate import (
    CANDIDATE_SCHEMA_VERSION,
)
from investment_orchestrator.research.actionable_promotion_pointer import (
    PERMISSION_EFFECT_PENDING_GATES,
    POINTER_SOURCE,
    PROMOTION_STATUS_PENDING_GATES,
    SCHEMA_VERSION as ACTIVE_POINTER_SCHEMA_VERSION,
)
from investment_orchestrator.research.promoted_handoff_verifier import (
    BLOCKER_EFFECTIVE_HANDOFF_HASH_MISMATCH,
    BLOCKER_EFFECTIVE_VALIDATION_FAILED,
    BLOCKER_PROMOTION_EXPIRED,
)
from investment_orchestrator.research.promoted_step3_audit_dry_run import (
    BLOCKER_MARKER_MALFORMED,
    BLOCKER_MARKER_MISSING_NOT_EXECUTION_AUTHORIZATION,
    BLOCKER_RAW_DEEP_RESEARCH_SOURCE,
    DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY,
    DRY_RUN_BLOCKER_VERIFICATION_INVALID,
    FUTURE_ACTION_REQUIRED,
    FUTURE_STATE_REQUIRED,
    FUTURE_STEP3_SOURCE_ARTIFACT,
    evaluate_promoted_step3_audit_gate_dry_run,
    verify_promoted_handoff_for_step3_audit,
)
from investment_orchestrator.state import research_availability


TODAY = date(2026, 6, 28)


def sha256_of(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def effective_handoff() -> dict[str, Any]:
    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "not_authorization": True,
        "trade_universe": {"allowed_buy_tickers": ["QQQ", "VOO", "SMH"]},
        "optional_extended_etf_sleeve": {"enabled": False},
        "buy_universe_scorecard": [
            {"ticker": "QQQ", "actionability_status": "actionable_this_run"},
            {"ticker": "VOO", "actionability_status": "ranking_hold_watch_only"},
        ],
        "strategy_a_research_handoff": {"positive_delta_research_supported": ["QQQ"]},
    }


def pointer_for(
    effective: dict[str, Any],
    *,
    promotion_expires_at: str = "2026-07-31",
) -> dict[str, Any]:
    digest = sha256_of(effective)
    return {
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
        "promotion_expires_at": promotion_expires_at,
        "effective_handoff_sha256": digest,
        "candidate_sha256": digest,
    }


def step2_marker(pointer: dict[str, Any], effective: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    marker = {
        "schema_version": "step2_promoted_decision_only_v1",
        "is_llm_generated": False,
        "mode": "promoted_step2_decision_only",
        "promoted_step2_decision_only": True,
        "decision_only": True,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "step3_allowed": False,
        "step4_allowed": False,
        "not_execution_authorization": True,
        "recommended_terminal_result_after_step2": "NO_TRADE_PENDING_FINAL_GATES",
        "research_state": "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY",
        "allowed_actions": ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"],
        "blocked_actions": [
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "source": "promoted_compiled_actionable_handoff",
        "promotion_status": "pending_gates",
        "active_pointer_sha256": sha256_of(pointer),
        "effective_handoff_sha256": sha256_of(effective),
        "promotion_expires_at": pointer["promotion_expires_at"],
        "actionable_this_run_tickers": ["QQQ"],
        "source_artifacts": {
            "research_handoff_candidate_effective": (
                "artifacts/current/step1_research/research_handoff_candidate_effective.json"
            ),
        },
        "report_only": False,
    }
    marker.update(overrides)
    return marker


def decision_packet() -> dict[str, Any]:
    return {
        "effective_allowed_buy_universe": ["QQQ"],
        "MARKET_DATA_SNAPSHOT": {
            "schema_version": "1.0",
            "snapshot_type": "MARKET_DATA_SNAPSHOT",
            "run_timestamp_et": "2026-06-28 16:00 ET",
            "execution_date_et": "2026-06-28",
            "market_data_target_close_date_et": "2026-06-28",
            "close_time_zone": "America/New_York",
            "display_time_zone": "America/Los_Angeles",
            "primary_source": "fixture",
            "fallback_source_for_last_close_and_price_asof_only": "fixture",
            "holiday_aware_close_resolution": True,
            "tickers": [
                {
                    "ticker": "QQQ",
                    "last_close": 420.0,
                    "price_asof": "2026-06-28",
                    "atr_20_30d_pct": 2.0,
                    "ma50": 410.0,
                    "ma200": 390.0,
                    "avg_volume_3m": 50000000,
                    "last_close_source": "fixture",
                    "price_asof_source": "fixture",
                    "technicals_source": "fixture",
                    "retrieved_at_utc": None,
                    "same_day_close_required": False,
                    "freshness_ok": True,
                    "data_gap": False,
                    "data_gap_reason": None,
                    "notes": [],
                }
            ],
        },
        "active_shortlist": [],
        "buy_side_delta_table": [],
        "rotation_decision_layer_8_15": [],
        "sell_side_delta_table_8_2": [],
        "execution_plan_drafts_8_5": [
            {"ticker": "QQQ", "action_draft": "KEEP_EXISTING", "why": "audit-only fixture"}
        ],
        "sell_execution_plan_drafts_8_6": [],
        "assumptions_and_data_gaps": [],
        "decision_builder_ready_for_audit": True,
    }


def decision_only_research_decision() -> dict[str, Any]:
    return {
        "state": "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY",
        "allowed_actions": ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"],
        "blocked_actions": [
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "promoted_step2_decision_only": True,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
    }


def effective_source_artifacts() -> dict[str, str]:
    return {
        "active_research_handoff_source": (
            "artifacts/current/step1_research/active_research_handoff_source.json"
        ),
        "research_handoff_candidate_effective": (
            "artifacts/current/step1_research/research_handoff_candidate_effective.json"
        ),
        "research_handoff_candidate_effective_validation": (
            "artifacts/current/step1_research/research_handoff_candidate_effective_validation.json"
        ),
        "step2_promoted_decision_only": (
            "artifacts/current/step2_decision_builder/step2_promoted_decision_only.json"
        ),
        "step2_decision_packet": "artifacts/current/step2_decision_builder/decision_packet.json",
    }


def verify(
    *,
    pointer: Any = None,
    effective: Any = None,
    validation: Any = None,
    marker: Any = None,
    packet: Any = None,
    source_artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    effective = effective if effective is not None else effective_handoff()
    pointer = pointer if pointer is not None else pointer_for(effective)
    validation = validation if validation is not None else {"valid": True}
    marker = marker if marker is not None else step2_marker(pointer, effective)
    packet = packet if packet is not None else decision_packet()
    return verify_promoted_handoff_for_step3_audit(
        active_pointer=pointer,
        effective_handoff=effective,
        effective_validation=validation,
        step2_promoted_marker=marker,
        step2_decision_packet=packet,
        today=TODAY,
        source_artifacts=source_artifacts or effective_source_artifacts(),
    )


def dry_run(verification: dict[str, Any]) -> dict[str, Any]:
    return evaluate_promoted_step3_audit_gate_dry_run(
        research_decision=decision_only_research_decision(),
        promoted_step3_verification=verification,
    )


def assert_fail_closed(verification: dict[str, Any], blocker: str) -> None:
    assert verification["valid_for_promoted_step3_audit"] is False
    assert blocker in verification["verification_blockers"]
    result = dry_run(verification)
    assert result["would_allow_promoted_step3_audit"] is False
    assert result["current_real_gate_allows"] is False
    assert DRY_RUN_BLOCKER_VERIFICATION_INVALID in result["dry_run_blockers"]
    assert DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY in result["dry_run_blockers"]


def test_happy_path_reports_effective_source_and_real_gate_closed() -> None:
    verification = verify()

    assert verification["valid_for_promoted_step3_audit"] is True
    assert verification["verification_blockers"] == []
    assert verification["future_step3_source_artifact"] == FUTURE_STEP3_SOURCE_ARTIFACT
    assert verification["raw_deep_research_source_used"] is False
    assert verification["order_compilation_allowed"] is False
    assert verification["new_buy_permission"] is False
    assert verification["step4_allowed"] is False
    assert verification["final_execution_allowed"] is False
    assert verification["broker_automation_allowed"] is False

    result = dry_run(verification)
    assert result["would_allow_promoted_step3_audit"] is True
    assert result["current_real_gate_allows"] is False
    assert result["raw_deep_research_source_used"] is False
    assert DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY in result["dry_run_blockers"]


def test_raw_deep_research_source_rejected_and_diagnostic_is_true() -> None:
    source_artifacts = {
        **effective_source_artifacts(),
        "promoted_step3_source": "artifacts/current/step1_research/research_output.json",
    }
    verification = verify(source_artifacts=source_artifacts)

    assert verification["raw_deep_research_source_used"] is True
    assert_fail_closed(verification, BLOCKER_RAW_DEEP_RESEARCH_SOURCE)
    result = dry_run(verification)
    assert result["raw_deep_research_source_used"] is True


def test_raw_deep_research_source_key_rejected_even_without_path_value() -> None:
    source_artifacts = {**effective_source_artifacts(), "research_output": "unexpected"}
    verification = verify(source_artifacts=source_artifacts)

    assert verification["raw_deep_research_source_used"] is True
    assert_fail_closed(verification, BLOCKER_RAW_DEEP_RESEARCH_SOURCE)


def test_expired_pointer_fails_closed() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective, promotion_expires_at="2026-06-27")
    verification = verify(pointer=pointer, effective=effective, marker=step2_marker(pointer, effective))

    assert BLOCKER_PROMOTION_EXPIRED in verification["live_step2_verification_blockers"]
    assert_fail_closed(verification, "promoted_handoff_verification_invalid")


def test_failed_effective_validation_fails_closed() -> None:
    verification = verify(validation={"valid": False})

    assert BLOCKER_EFFECTIVE_VALIDATION_FAILED in verification["live_step2_verification_blockers"]
    assert_fail_closed(verification, "promoted_handoff_verification_invalid")


def test_non_object_marker_fails_closed() -> None:
    verification = verify(marker=["not", "an", "object"])  # type: ignore[arg-type]

    assert_fail_closed(verification, BLOCKER_MARKER_MALFORMED)


def test_marker_missing_not_execution_authorization_fails_closed() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective)
    verification = verify(
        pointer=pointer,
        effective=effective,
        marker=step2_marker(pointer, effective, not_execution_authorization=False),
    )

    assert_fail_closed(verification, BLOCKER_MARKER_MISSING_NOT_EXECUTION_AUTHORIZATION)


def test_marker_hash_mismatch_fails_closed() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective)
    marker = step2_marker(pointer, effective, effective_handoff_sha256="bad-hash")
    verification = verify(pointer=pointer, effective=effective, marker=marker)

    assert_fail_closed(verification, "step2_promoted_marker_hash_mismatch")
    assert BLOCKER_EFFECTIVE_HANDOFF_HASH_MISMATCH not in verification[
        "live_step2_verification_blockers"
    ]


def test_step3_audit_only_state_and_action_are_real_but_non_ordering() -> None:
    assert hasattr(research_availability, FUTURE_STATE_REQUIRED)
    assert FUTURE_STATE_REQUIRED in research_availability._ALLOWED_ACTIONS_BY_STATE
    assert FUTURE_ACTION_REQUIRED not in research_availability.ACTIONS
    assert research_availability._ALLOWED_ACTIONS_BY_STATE[
        research_availability.STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY
    ] == ("HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION")
    assert research_availability._ALLOWED_ACTIONS_BY_STATE[FUTURE_STATE_REQUIRED] == (
        "HOLD",
        "NO_TRADE",
        "PROMOTED_RESEARCH_DECISION",
        FUTURE_ACTION_REQUIRED,
    )
    assert "NEW_BUY" not in research_availability._ALLOWED_ACTIONS_BY_STATE[FUTURE_STATE_REQUIRED]
    assert "ORDER_COMPILATION" not in research_availability._ALLOWED_ACTIONS_BY_STATE[
        FUTURE_STATE_REQUIRED
    ]
    for state, allowed_actions in research_availability._ALLOWED_ACTIONS_BY_STATE.items():
        if state != FUTURE_STATE_REQUIRED:
            assert FUTURE_ACTION_REQUIRED not in allowed_actions
