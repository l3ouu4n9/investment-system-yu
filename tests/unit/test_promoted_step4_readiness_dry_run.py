"""R2E.5b-7b: report-only promoted Step 4 readiness verifier / dry-run tests.

These artifacts grant nothing. Every test here proves either a fail-closed
path or that the report-only outputs carry no order authority.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
from typing import Any

from investment_orchestrator.research.promoted_step4_readiness_dry_run import (
    BLOCKER_BUY_UNIVERSE_INVALID,
    BLOCKER_HARD_CAP_BUDGET_INVALID,
    BLOCKER_MAX_NEW_TICKERS_INVALID,
    BLOCKER_PORTFOLIO_SNAPSHOT_INVALID,
    BLOCKER_RAW_DEEP_RESEARCH_SOURCE,
    BLOCKER_STALE_LEGACY_AUDITED_PACKET,
    BLOCKER_STEP3_AUDIT_OUTPUT_MISSING,
    BLOCKER_STEP3_BLOCK_MALFORMED,
    BLOCKER_STEP3_BLOCK_MISSING,
    BLOCKER_STEP3_CHAIN_INVALID,
    BLOCKER_STEP3_MARKER_HASH_MISMATCH,
    BLOCKER_STEP3_MARKER_MALFORMED,
    BLOCKER_STEP3_MARKER_MISSING,
    BLOCKER_STEP3_MARKER_WIDENED_NEW_BUY,
    BLOCKER_STEP3_MARKER_WIDENED_ORDER_COMPILATION,
    BLOCKER_TARGET_NEW_BUY_BUDGET_INVALID,
    BLOCKER_VERIFIER_INTERNAL_ERROR,
    DRY_RUN_BLOCKER_DECISION_MISSING,
    DRY_RUN_BLOCKER_DECISION_NOT_STEP3_AUDIT_ONLY,
    DRY_RUN_BLOCKER_DECISION_WIDENED_NEW_BUY,
    DRY_RUN_BLOCKER_DECISION_WIDENED_ORDER_COMPILATION,
    DRY_RUN_BLOCKER_VERIFICATION_INVALID,
    DRY_RUN_BLOCKER_VERIFICATION_MISSING,
    EXPECTED_STEP3_BLOCK_REASON,
    EXPECTED_STEP3_BLOCK_SCHEMA_VERSION,
    EXPECTED_STEP3_MARKER_SCHEMA_VERSION,
    EXPECTED_STEP3_MODE,
    FUTURE_ACTION_REQUIRED,
    FUTURE_STATE_REQUIRED,
    STEP3_AUDIT_ONLY_ALLOWED_ACTIONS,
    _max_new_tickers_per_week_total,
    evaluate_promoted_step4_preview_gate_dry_run,
    verify_promoted_step3_for_step4_readiness,
)
from investment_orchestrator.research.actionable_handoff_candidate import (
    CANDIDATE_SCHEMA_VERSION,
)
from investment_orchestrator.research.actionable_promotion_pointer import (
    PERMISSION_EFFECT_PENDING_GATES,
    POINTER_SOURCE,
    PROMOTION_STATUS_PENDING_GATES,
    SCHEMA_VERSION as ACTIVE_POINTER_SCHEMA_VERSION,
)
from investment_orchestrator.research.promoted_step3_audit_dry_run import (
    DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY,
)


TODAY = date(2026, 6, 28)
STEP3_AUDIT_ONLY_STATE = "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY"
_DEFAULT = object()


def sha256_of(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# --- fixtures (mirroring the real promoted chain artifacts) -------------------


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


def step2_marker(pointer: dict[str, Any], effective: dict[str, Any]) -> dict[str, Any]:
    return {
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
            {"ticker": "QQQ", "action_draft": "KEEP_EXISTING", "why": "readiness fixture"}
        ],
        "sell_execution_plan_drafts_8_6": [],
        "assumptions_and_data_gaps": [],
        "decision_builder_ready_for_audit": True,
    }


def step3_marker(
    pointer: dict[str, Any],
    effective: dict[str, Any],
    marker2: dict[str, Any],
    packet: dict[str, Any],
    **overrides: Any,
) -> dict[str, Any]:
    marker = {
        "schema_version": EXPECTED_STEP3_MARKER_SCHEMA_VERSION,
        "is_llm_generated": False,
        "mode": EXPECTED_STEP3_MODE,
        "state": STEP3_AUDIT_ONLY_STATE,
        "research_state": STEP3_AUDIT_ONLY_STATE,
        "allowed_actions": list(STEP3_AUDIT_ONLY_ALLOWED_ACTIONS),
        "blocked_actions": [
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "audit_only": True,
        "permission_effect": "step3_audit_only",
        "not_authorization": True,
        "not_execution_authorization": True,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "step4_allowed": False,
        "final_execution_allowed": False,
        "broker_automation_allowed": False,
        "source": "promoted_compiled_actionable_handoff",
        "promotion_status": "pending_gates",
        "promotion_expires_at": pointer["promotion_expires_at"],
        "active_pointer_sha256": sha256_of(pointer),
        "effective_handoff_sha256": sha256_of(effective),
        "pointer_effective_handoff_sha256": sha256_of(effective),
        "step2_promoted_marker_sha256": sha256_of(marker2),
        "step2_decision_packet_sha256": sha256_of(packet),
        "report_only": False,
    }
    marker.update(overrides)
    return marker


def step3_block(marker3: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    block = {
        **{k: v for k, v in marker3.items() if k != "schema_version"},
        "schema_version": EXPECTED_STEP3_BLOCK_SCHEMA_VERSION,
        "blocked": True,
        "reason": EXPECTED_STEP3_BLOCK_REASON,
    }
    block.update(overrides)
    return block


def readiness_settings(**overrides: Any) -> dict[str, Any]:
    settings = {
        "as_of": "2026-06-28",
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.00,
        "max_new_tickers_per_week": {
            "base_universe_new_tickers_per_week": 2,
            "extended_etf_sleeve_new_tickers_per_week": 2,
        },
    }
    settings.update(overrides)
    return settings


def portfolio_snapshot_text() -> str:
    return (
        "(2a) existing_buy_open_orders_summary\n"
        "QQQ | 1000.00 | 900.00 | 100.00 | T4-E | - | - | - | - | - | - | "
        "starter=500 | starter=1\n"
    )


def source_artifacts() -> dict[str, str]:
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
        "step3_promoted_audit_only": (
            "artifacts/current/step3_audit_engine/step3_promoted_audit_only.json"
        ),
        "step3_promoted_audit_only_downstream_block": (
            "artifacts/current/step3_audit_engine/step3_promoted_audit_only_downstream_block.json"
        ),
        "step3_template3_audit": "artifacts/current/step3_audit_engine/template3_audit.txt",
    }


def run_verifier(**overrides: Any) -> dict[str, Any]:
    effective = overrides.pop("effective", _DEFAULT)
    effective = effective_handoff() if effective is _DEFAULT else effective
    pointer = overrides.pop("pointer", _DEFAULT)
    pointer = pointer_for(effective) if pointer is _DEFAULT else pointer
    validation = overrides.pop("validation", _DEFAULT)
    validation = {"valid": True} if validation is _DEFAULT else validation
    marker2 = overrides.pop("marker2", _DEFAULT)
    marker2 = step2_marker(pointer, effective) if marker2 is _DEFAULT else marker2
    packet = overrides.pop("packet", _DEFAULT)
    packet = decision_packet() if packet is _DEFAULT else packet
    marker3 = overrides.pop("marker3", _DEFAULT)
    if marker3 is _DEFAULT:
        marker3 = step3_marker(pointer, effective, marker2, packet)
    block3 = overrides.pop("block3", _DEFAULT)
    if block3 is _DEFAULT:
        block3 = step3_block(marker3) if isinstance(marker3, dict) else None
    kwargs: dict[str, Any] = dict(
        active_pointer=pointer,
        effective_handoff=effective,
        effective_validation=validation,
        step2_promoted_marker=marker2,
        step2_decision_packet=packet,
        step3_promoted_marker=marker3,
        step3_downstream_block=block3,
        step3_audit_output_text="Promoted Step 3 audit-only findings.\n",
        legacy_audited_packet_present=False,
        strategy_settings=readiness_settings(),
        portfolio_snapshot_text=portfolio_snapshot_text(),
        today=TODAY,
        source_artifacts=source_artifacts(),
    )
    kwargs.update(overrides)
    return verify_promoted_step3_for_step4_readiness(**kwargs)


def audit_only_research_decision() -> dict[str, Any]:
    return {
        "state": STEP3_AUDIT_ONLY_STATE,
        "allowed_actions": list(STEP3_AUDIT_ONLY_ALLOWED_ACTIONS),
        "blocked_actions": [
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "promoted_step2_decision_only": True,
        "promoted_step3_audit_only": True,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
    }


class _EvilMapping(dict):
    def get(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102
        raise RuntimeError("boom")


# --- verification: happy path -------------------------------------------------


def test_happy_path_verification_valid_and_report_only() -> None:
    result = run_verifier()

    assert result["schema_version"] == "promoted_step4_readiness_verification_v1"
    assert result["valid_for_promoted_step4_preview"] is True
    assert result["would_allow_promoted_step4_preview"] is True
    assert result["verification_blockers"] == []
    assert result["is_llm_generated"] is False
    assert result["report_only"] is True
    assert result["permission_effect"] == "none"
    assert result["not_authorization"] is True
    assert result["not_execution_authorization"] is True
    assert result["current_real_gate_allows"] is False
    assert result["order_compilation_allowed"] is False
    assert result["new_buy_permission"] is False
    assert result["step4_allowed"] is False
    assert result["final_execution_allowed"] is False
    assert result["broker_automation_allowed"] is False
    assert result["future_state_required"] == FUTURE_STATE_REQUIRED
    assert result["future_action_required"] == FUTURE_ACTION_REQUIRED
    assert result["raw_deep_research_source_used"] is False
    assert result["consumed_by_availability"] is False
    assert result["consumed_by_step4"] is False
    assert result["consumed_by_gates"] is False
    assert result["live_step3_verification_valid"] is True
    hashes = result["source_artifact_hashes"]
    assert hashes["step3_promoted_audit_only"] is not None
    assert hashes["step3_promoted_audit_only_downstream_block"] is not None
    assert hashes["step3_template3_audit"] is not None
    assert result["step3_audit_output_sha256"] == hashes["step3_template3_audit"]


# --- verification: fail-closed cases ------------------------------------------


def test_missing_step3_marker_fails_closed() -> None:
    result = run_verifier(marker3=None, block3=None)
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STEP3_MARKER_MISSING in result["verification_blockers"]
    assert BLOCKER_STEP3_BLOCK_MISSING in result["verification_blockers"]


def test_malformed_step3_marker_fails_closed() -> None:
    result = run_verifier(marker3="not-a-mapping", block3=None)
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STEP3_MARKER_MALFORMED in result["verification_blockers"]


def test_widened_step3_marker_with_new_buy_fails_closed() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective)
    marker2 = step2_marker(pointer, effective)
    packet = decision_packet()
    widened = step3_marker(
        pointer,
        effective,
        marker2,
        packet,
        allowed_actions=list(STEP3_AUDIT_ONLY_ALLOWED_ACTIONS) + ["NEW_BUY"],
    )
    result = run_verifier(
        effective=effective,
        pointer=pointer,
        marker2=marker2,
        packet=packet,
        marker3=widened,
        block3=step3_block(widened, allowed_actions=list(STEP3_AUDIT_ONLY_ALLOWED_ACTIONS)),
    )
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STEP3_MARKER_WIDENED_NEW_BUY in result["verification_blockers"]


def test_widened_step3_marker_with_order_compilation_fails_closed() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective)
    marker2 = step2_marker(pointer, effective)
    packet = decision_packet()
    widened = step3_marker(
        pointer,
        effective,
        marker2,
        packet,
        allowed_actions=list(STEP3_AUDIT_ONLY_ALLOWED_ACTIONS) + ["ORDER_COMPILATION"],
    )
    result = run_verifier(
        effective=effective,
        pointer=pointer,
        marker2=marker2,
        packet=packet,
        marker3=widened,
    )
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STEP3_MARKER_WIDENED_ORDER_COMPILATION in result["verification_blockers"]


def test_malformed_step3_downstream_block_fails_closed() -> None:
    result = run_verifier(block3=["not", "a", "mapping"])
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STEP3_BLOCK_MALFORMED in result["verification_blockers"]


def test_missing_step2_marker_fails_closed_via_live_chain() -> None:
    result = run_verifier(marker2=None)
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STEP3_CHAIN_INVALID in result["verification_blockers"]


def test_effective_hash_mismatch_fails_closed() -> None:
    effective = effective_handoff()
    other = {**effective_handoff(), "trade_universe": {"allowed_buy_tickers": ["VTI"]}}
    result = run_verifier(effective=effective, pointer=pointer_for(other))
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STEP3_CHAIN_INVALID in result["verification_blockers"]


def test_expired_pointer_fails_closed() -> None:
    effective = effective_handoff()
    result = run_verifier(
        effective=effective,
        pointer=pointer_for(effective, promotion_expires_at="2026-06-01"),
    )
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STEP3_CHAIN_INVALID in result["verification_blockers"]


def test_failed_effective_validation_fails_closed() -> None:
    result = run_verifier(validation={"valid": False})
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STEP3_CHAIN_INVALID in result["verification_blockers"]


def test_stale_step3_marker_hash_fails_closed() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective)
    marker2 = step2_marker(pointer, effective)
    packet = decision_packet()
    stale = step3_marker(pointer, effective, marker2, packet, effective_handoff_sha256="0" * 64)
    result = run_verifier(
        effective=effective,
        pointer=pointer,
        marker2=marker2,
        packet=packet,
        marker3=stale,
    )
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STEP3_MARKER_HASH_MISMATCH in result["verification_blockers"]


def test_raw_deep_research_source_token_fails_closed() -> None:
    result = run_verifier(
        source_artifacts={
            **source_artifacts(),
            "raw_research": "artifacts/current/step1_research/research_output.json",
        }
    )
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_RAW_DEEP_RESEARCH_SOURCE in result["verification_blockers"]
    assert result["raw_deep_research_source_used"] is True


def test_stale_legacy_audited_packet_fails_closed() -> None:
    result = run_verifier(legacy_audited_packet_present=True)
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STALE_LEGACY_AUDITED_PACKET in result["verification_blockers"]


def test_missing_step3_audit_output_fails_closed() -> None:
    result = run_verifier(step3_audit_output_text=None)
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_STEP3_AUDIT_OUTPUT_MISSING in result["verification_blockers"]
    assert result["step3_audit_output_sha256"] is None


def test_missing_or_malformed_budget_inputs_fail_closed() -> None:
    for settings, blocker in (
        (readiness_settings(hard_cap_open_orders_budget=None), BLOCKER_HARD_CAP_BUDGET_INVALID),
        (readiness_settings(hard_cap_open_orders_budget="38k"), BLOCKER_HARD_CAP_BUDGET_INVALID),
        (readiness_settings(hard_cap_open_orders_budget=-1), BLOCKER_HARD_CAP_BUDGET_INVALID),
        (
            readiness_settings(target_new_buy_budget_this_run="12000"),
            BLOCKER_TARGET_NEW_BUY_BUDGET_INVALID,
        ),
        (
            readiness_settings(target_new_buy_budget_this_run=float("inf")),
            BLOCKER_TARGET_NEW_BUY_BUDGET_INVALID,
        ),
        (readiness_settings(max_new_tickers_per_week=None), BLOCKER_MAX_NEW_TICKERS_INVALID),
        (
            readiness_settings(max_new_tickers_per_week={"base": "two"}),
            BLOCKER_MAX_NEW_TICKERS_INVALID,
        ),
        (readiness_settings(max_new_tickers_per_week=True), BLOCKER_MAX_NEW_TICKERS_INVALID),
    ):
        result = run_verifier(strategy_settings=settings)
        assert result["valid_for_promoted_step4_preview"] is False, blocker
        assert blocker in result["verification_blockers"]


def test_unparseable_portfolio_snapshot_fails_closed() -> None:
    for text in (None, "", "free text without the 2a section\n"):
        result = run_verifier(portfolio_snapshot_text=text)
        assert result["valid_for_promoted_step4_preview"] is False
        assert BLOCKER_PORTFOLIO_SNAPSHOT_INVALID in result["verification_blockers"]


def test_missing_effective_allowed_buy_universe_fails_closed() -> None:
    packet = decision_packet()
    packet["effective_allowed_buy_universe"] = []
    result = run_verifier(packet=packet)
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_BUY_UNIVERSE_INVALID in result["verification_blockers"]


def test_verifier_never_raises_and_falls_back_closed() -> None:
    result = run_verifier(strategy_settings=_EvilMapping())
    assert result["valid_for_promoted_step4_preview"] is False
    assert BLOCKER_VERIFIER_INTERNAL_ERROR in result["verification_blockers"]
    assert result["report_only"] is True
    assert result["order_compilation_allowed"] is False


# --- dry-run -------------------------------------------------------------------


def test_dry_run_happy_path_would_allow_but_real_gate_stays_closed() -> None:
    verification = run_verifier()
    dry_run = evaluate_promoted_step4_preview_gate_dry_run(
        research_decision=audit_only_research_decision(),
        promoted_step4_verification=verification,
    )

    assert dry_run["schema_version"] == "promoted_step4_preview_gate_dry_run_v1"
    assert dry_run["would_allow_promoted_step4_preview"] is True
    assert dry_run["current_real_gate_allows"] is False
    assert DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY in dry_run["dry_run_blockers"]
    assert dry_run["dry_run_only"] is True
    assert dry_run["report_only"] is True
    assert dry_run["is_llm_generated"] is False
    assert dry_run["permission_effect"] == "none"
    assert dry_run["not_authorization"] is True
    assert dry_run["not_execution_authorization"] is True
    assert dry_run["order_compilation_allowed"] is False
    assert dry_run["new_buy_permission"] is False
    assert dry_run["step4_allowed"] is False
    assert dry_run["final_execution_allowed"] is False
    assert dry_run["broker_automation_allowed"] is False
    assert dry_run["future_state_required"] == FUTURE_STATE_REQUIRED
    assert dry_run["future_action_required"] == FUTURE_ACTION_REQUIRED
    assert dry_run["current_state"] == STEP3_AUDIT_ONLY_STATE
    assert dry_run["consumed_by_availability"] is False
    assert dry_run["consumed_by_step4"] is False
    assert dry_run["consumed_by_gates"] is False
    assert dry_run["freshness"]["promotion_expires_at"] == "2026-07-31"


def test_dry_run_rejects_non_step3_audit_only_decision() -> None:
    verification = run_verifier()
    decision = audit_only_research_decision()
    decision["state"] = "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    decision["promoted_step3_audit_only"] = False
    decision["allowed_actions"] = ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]
    dry_run = evaluate_promoted_step4_preview_gate_dry_run(
        research_decision=decision,
        promoted_step4_verification=verification,
    )
    assert dry_run["would_allow_promoted_step4_preview"] is False
    assert DRY_RUN_BLOCKER_DECISION_NOT_STEP3_AUDIT_ONLY in dry_run["dry_run_blockers"]


def test_dry_run_rejects_widened_decision_actions() -> None:
    verification = run_verifier()
    for extra, blocker in (
        ("NEW_BUY", DRY_RUN_BLOCKER_DECISION_WIDENED_NEW_BUY),
        ("ORDER_COMPILATION", DRY_RUN_BLOCKER_DECISION_WIDENED_ORDER_COMPILATION),
    ):
        decision = audit_only_research_decision()
        decision["allowed_actions"] = list(STEP3_AUDIT_ONLY_ALLOWED_ACTIONS) + [extra]
        dry_run = evaluate_promoted_step4_preview_gate_dry_run(
            research_decision=decision,
            promoted_step4_verification=verification,
        )
        assert dry_run["would_allow_promoted_step4_preview"] is False, extra
        assert blocker in dry_run["dry_run_blockers"]


def test_dry_run_rejects_missing_or_invalid_verification() -> None:
    dry_run = evaluate_promoted_step4_preview_gate_dry_run(
        research_decision=audit_only_research_decision(),
        promoted_step4_verification=None,
    )
    assert dry_run["would_allow_promoted_step4_preview"] is False
    assert DRY_RUN_BLOCKER_VERIFICATION_MISSING in dry_run["dry_run_blockers"]

    invalid = run_verifier(marker3=None)
    dry_run = evaluate_promoted_step4_preview_gate_dry_run(
        research_decision=audit_only_research_decision(),
        promoted_step4_verification=invalid,
    )
    assert dry_run["would_allow_promoted_step4_preview"] is False
    assert DRY_RUN_BLOCKER_VERIFICATION_INVALID in dry_run["dry_run_blockers"]


def test_dry_run_never_raises_and_falls_back_closed() -> None:
    dry_run = evaluate_promoted_step4_preview_gate_dry_run(
        research_decision=_EvilMapping(),
        promoted_step4_verification=None,
    )
    assert dry_run["would_allow_promoted_step4_preview"] is False
    assert DRY_RUN_BLOCKER_DECISION_MISSING in dry_run["dry_run_blockers"]
    assert dry_run["report_only"] is True


# --- drift guards vs the real Step 3 engine / Step 4 compiler ------------------


def test_mirrored_step3_constants_match_engine() -> None:
    from investment_orchestrator.workflow import step3_audit_engine

    assert (
        EXPECTED_STEP3_MARKER_SCHEMA_VERSION
        == step3_audit_engine.STEP3_PROMOTED_AUDIT_ONLY_SCHEMA_VERSION
    )
    assert (
        EXPECTED_STEP3_BLOCK_SCHEMA_VERSION
        == step3_audit_engine.STEP3_PROMOTED_AUDIT_ONLY_DOWNSTREAM_BLOCK_SCHEMA_VERSION
    )
    assert EXPECTED_STEP3_MODE == step3_audit_engine.MODE_PROMOTED_STEP3_AUDIT_ONLY
    assert (
        EXPECTED_STEP3_BLOCK_REASON
        == step3_audit_engine.PROMOTED_STEP3_AUDIT_ONLY_NO_ORDER_COMPILATION_REASON
    )
    assert (
        STEP3_AUDIT_ONLY_ALLOWED_ACTIONS
        == step3_audit_engine.PROMOTED_STEP3_AUDIT_ONLY_ALLOWED_ACTIONS
    )


def test_mirrored_step3_paths_match_engine(tmp_path: Any, monkeypatch: Any) -> None:
    from investment_orchestrator.workflow import step1_research, step3_audit_engine

    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    assert (
        step1_research._step3_promoted_audit_only_report_path()
        == step3_audit_engine.step3_promoted_audit_only_path()
    )
    assert (
        step1_research._step3_promoted_audit_only_downstream_block_report_path()
        == step3_audit_engine.step3_promoted_audit_only_downstream_block_path()
    )
    assert (
        step1_research._step3_template3_audit_report_path()
        == step3_audit_engine.step3_template3_audit_path()
    )
    assert (
        step1_research._step3_audited_decision_packet_report_path()
        == step3_audit_engine.step3_audited_decision_packet_path()
    )


def test_max_new_tickers_mirror_matches_step4_compiler() -> None:
    from investment_orchestrator.workflow.step4_order_compiler import (
        _max_new_tickers_per_week_total as step4_total,
    )

    samples = [
        None,
        3,
        0,
        -1,
        True,
        False,
        {"a": 2, "b": 1},
        {"a": True, "b": 2},
        {"a": "two"},
        {},
        "3",
        3.5,
    ]
    for sample in samples:
        # step4's helper takes the whole settings mapping; the 7b mirror takes
        # the field value directly. Compare over the same effective input.
        assert _max_new_tickers_per_week_total(sample) == step4_total(
            {"max_new_tickers_per_week": sample}
        ), sample


def test_future_labels_are_not_real_states_or_actions() -> None:
    from investment_orchestrator.state.research_availability import (
        ACTIONS,
        _ALLOWED_ACTIONS_BY_STATE,
    )

    assert FUTURE_STATE_REQUIRED not in _ALLOWED_ACTIONS_BY_STATE
    assert FUTURE_ACTION_REQUIRED not in ACTIONS
    for state, actions in _ALLOWED_ACTIONS_BY_STATE.items():
        assert FUTURE_ACTION_REQUIRED not in actions, state
        assert "PROMOTED_ORDER_READINESS_CHECK" not in actions, state
