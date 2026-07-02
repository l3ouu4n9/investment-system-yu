"""Unit tests for the R2E.5b-3 promotion-eligibility checker (report-only).

The checker evaluates whether the separate, report-only actionable compiled-handoff
candidate WOULD be eligible for a FUTURE promotion. It never promotes, never
creates the active pointer, and never changes permissions. These tests build the
*real* chain (anchors → support signals → preview → candidate → validation →
metadata) so the eligibility verdict — including the hash-chain verification —
is faithful to what Step 1 writes.
"""

from __future__ import annotations

from typing import Any

from investment_orchestrator.research.actionable_handoff_candidate import (
    build_actionable_handoff_candidate,
    build_actionable_handoff_metadata,
)
from investment_orchestrator.research.actionable_handoff_preview import (
    build_actionable_handoff_preview,
)
from investment_orchestrator.research.actionable_promotion_eligibility import (
    BLOCKER_ANALYST_MEMO_ABSENT_OR_INVALID,
    BLOCKER_BLOCKING_DATA_GAP_ON_ACTIONABLE_ROW,
    BLOCKER_CANDIDATE_EXCEEDS_MAX_NEW_TICKERS,
    BLOCKER_CANDIDATE_VALIDATION_FAILED,
    BLOCKER_EXTENDED_ETF_ENABLED,
    BLOCKER_HASH_CHAIN_MISMATCH,
    BLOCKER_INVALID_RESEARCH_ANCHORS,
    BLOCKER_MAX_NEW_TICKERS_CAP_MISSING_OR_ZERO,
    BLOCKER_MEMO_CONFIDENCE_LOW,
    BLOCKER_MISSING_ACTIONABLE_CANDIDATE,
    BLOCKER_MISSING_ACTIONABLE_PREVIEW,
    BLOCKER_MISSING_BUDGET_CONTEXT,
    BLOCKER_MISSING_EVIDENCE_PACKET,
    BLOCKER_NO_ACCEPTED_SUPPORT_SIGNALS,
    BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS,
    BLOCKER_NO_PREVIEW_ACTIONABLE_ROWS,
    BLOCKER_NO_VALID_RESEARCH_ANCHOR,
    BLOCKER_OUT_OF_UNIVERSE_ACTIONABLE_TICKER,
    BLOCKER_STALE_REFERENCED_ANCHOR,
    BLOCKER_STRATEGY_SETTINGS_HASH_MISMATCH,
    BLOCKER_UNIVERSE_MISMATCH,
    PROMOTION_BLOCKER_REASON_CODES,
    PROMOTION_WARNING_REASON_CODES,
    SCHEMA_VERSION,
    SEVERITY_BLOCKER,
    evaluate_actionable_handoff_promotion_eligibility,
)
from investment_orchestrator.research.research_anchors import (
    summarize_research_anchors,
    validate_research_anchors,
)
from investment_orchestrator.research.support_signals import build_compiled_support_signals
from investment_orchestrator.state.last_good_research_handoff import (
    decision_relevant_settings,
    strategy_settings_hash,
)
from investment_orchestrator.validators.validate_research_handoff import (
    research_handoff_validation_result_to_dict,
    validate_research_handoff,
)


TODAY = "2026-06-28"
_MODE = "evidence_plus_memo"


# --- builders (real chain) ----------------------------------------------------


def settings(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "as_of": TODAY,
        "benchmark": "QQQ",
        "core_universe": ["QQQ", "VOO"],
        "satellite_universe": ["SMH"],
        "user_approved_extended_etf_static_list": ["GRID"],
        "max_new_tickers_per_week": {
            "base_universe_new_tickers_per_week": 2,
            "extended_etf_sleeve_new_tickers_per_week": 2,
        },
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.0,
        "ticker_role_fallback": {
            "QQQ": "benchmark_carrier_core",
            "VOO": "diversified_core_buffer",
            "SMH": "sector_alpha_tilt",
        },
    }
    base.update(overrides)
    return base


def anchor_yaml_payload(**overrides: Any) -> dict[str, Any]:
    anchor: dict[str, Any] = {
        "anchor_id": "AI_CAPEX_2026H2",
        "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"],
        "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01",
        "valid_until": "2026-07-31",
        "source_type": "operator",
        "confidence_floor": "medium",
    }
    anchor.update(overrides)
    return {
        "schema_version": "research_anchors_v1",
        "as_of_date": TODAY,
        "is_llm_generated": False,
        "anchors": [anchor],
    }


def anchors_summary(payload: dict[str, Any] | None = None, *, today: str = TODAY) -> dict[str, Any]:
    result = validate_research_anchors(
        payload if payload is not None else anchor_yaml_payload(),
        allowed_universe=["QQQ", "VOO", "SMH"],
        today=today,
    )
    return summarize_research_anchors(result, path="/inputs/current/research_anchors.yaml")


def evidence_packet(*, stgs: dict[str, Any] | None = None, anchors: dict[str, Any] | None = None) -> dict[str, Any]:
    stgs = stgs if stgs is not None else settings()
    return {
        "schema_version": "evidence_packet_v1",
        "is_llm_generated": False,
        "strategy_settings_hash": strategy_settings_hash(decision_relevant_settings(stgs)),
        "universe": {
            "core_universe": ["QQQ", "VOO"],
            "satellite_universe": ["SMH"],
            "approved_extended_etf": ["GRID"],
            "allowed_buy_tickers": ["QQQ", "VOO", "SMH"],
        },
        "budget_settings": {
            "hard_cap_open_orders_budget": stgs.get("hard_cap_open_orders_budget"),
            "target_new_buy_budget_this_run": stgs.get("target_new_buy_budget_this_run"),
            "max_new_tickers_per_week": stgs.get("max_new_tickers_per_week"),
        },
        "research_anchors": anchors if anchors is not None else anchors_summary(),
        "data_gaps": [],
        "report_only": True,
    }


def memo(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "as_of_date": TODAY,
        "regime_view": "constructive",
        "confidence": "high",
        "ticker_relative_view": [
            {
                "ticker": "QQQ",
                "stance": "prefer",
                "rationale_12m_plus": "AI capex structural growth",
                "anchor_id_refs": ["AI_CAPEX_2026H2"],
            }
        ],
        "avoid_or_deprioritize": [],
        "data_gaps": [],
        "source_notes": [{"claim": "AI capex", "source": "10-K", "source_quality": "official"}],
    }
    base.update(overrides)
    return base


def chain(
    packet: dict[str, Any] | None = None,
    m: dict[str, Any] | None = None,
    *,
    stgs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the full real artifact chain and return every eligibility input."""
    stgs = stgs if stgs is not None else settings()
    packet = packet if packet is not None else evidence_packet(stgs=stgs)
    m = m if m is not None else memo()
    signals = build_compiled_support_signals(
        evidence_packet=packet, analyst_memo=m, compilation_mode=_MODE
    )
    preview = build_actionable_handoff_preview(
        evidence_packet=packet, analyst_memo=m, compiled_support_signals=signals
    )
    candidate = build_actionable_handoff_candidate(
        evidence_packet=packet,
        analyst_memo=m,
        actionable_handoff_preview=preview,
        base_candidate=None,
        strategy_settings=stgs,
    )
    validation = research_handoff_validation_result_to_dict(
        validate_research_handoff(candidate, strategy_settings=stgs)
    )
    metadata = build_actionable_handoff_metadata(
        candidate=candidate,
        validation=validation,
        actionable_handoff_preview=preview,
        compiled_support_signals=signals,
        evidence_packet=packet,
        base_candidate=None,
        used_active_compiled_handoff_as_base=False,
    )
    return {
        "evidence_packet": packet,
        "compiled_support_signals": signals,
        "actionable_preview": preview,
        "actionable_candidate": candidate,
        "actionable_candidate_validation": validation,
        "actionable_candidate_metadata": metadata,
        "strategy_settings": stgs,
    }


def evaluate(inputs: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "evidence_packet": inputs["evidence_packet"],
        "compiled_support_signals": inputs["compiled_support_signals"],
        "actionable_preview": inputs["actionable_preview"],
        "actionable_candidate": inputs["actionable_candidate"],
        "actionable_candidate_validation": inputs["actionable_candidate_validation"],
        "actionable_candidate_metadata": inputs["actionable_candidate_metadata"],
        "strategy_settings": inputs["strategy_settings"],
        "today": TODAY,
    }
    kwargs.update(overrides)
    return evaluate_actionable_handoff_promotion_eligibility(**kwargs)


def _blocker_checks(result: dict[str, Any]) -> dict[str, bool]:
    return {
        c["check_id"]: c["passed"] for c in result["checks"] if c["severity"] == SEVERITY_BLOCKER
    }


# --- happy path ----------------------------------------------------------------


def test_full_real_chain_is_eligible() -> None:
    result = evaluate(chain())
    assert result["promotion_blockers"] == []
    assert result["eligible_for_promotion"] is True
    assert result["actionable_this_run_tickers"] == ["QQQ"]
    assert result["candidate_actionable_row_count"] == 1
    assert result["preview_actionable_row_count"] == 1
    assert result["accepted_support_signal_count"] == 1
    assert result["candidate_validation_passed"] is True
    assert result["hash_chain_valid"] is True


def test_eligible_artifact_is_still_report_only_and_not_authorization() -> None:
    result = evaluate(chain())
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["is_llm_generated"] is False
    assert result["report_only"] is True
    assert result["permission_effect"] == "none"
    assert result["not_authorization"] is True
    assert result["consumed_by_availability"] is False
    assert result["consumed_by_step2"] is False
    assert result["consumed_by_gates"] is False


def test_anchor_expiry_computed_from_referenced_anchor() -> None:
    result = evaluate(chain())
    assert result["earliest_anchor_valid_until"] == "2026-07-31"
    assert result["promotion_expires_at"] == "2026-07-31"
    assert result["today"] == TODAY


def test_checks_cover_every_blocker_reason_code() -> None:
    result = evaluate(chain())
    check_ids = {c["check_id"] for c in result["checks"]}
    # Every deterministic blocker reason code has exactly one owning check.
    assert len(PROMOTION_BLOCKER_REASON_CODES) == sum(
        1 for c in result["checks"] if c["severity"] == SEVERITY_BLOCKER
    )
    for check_id in (
        "candidate_validation_passed",
        "hash_chain_valid",
        "referenced_anchors_fresh",
        "budget_context_present",
        "strategy_settings_hash_match",
    ):
        assert check_id in check_ids, check_id
    assert len(PROMOTION_WARNING_REASON_CODES) == sum(
        1 for c in result["checks"] if c["severity"] == "warning"
    )


def test_source_hashes_recorded_and_matching() -> None:
    result = evaluate(chain())
    for label in ("evidence_packet", "compiled_support_signals", "actionable_handoff_preview"):
        ref = result["source_hashes"][label]
        assert ref["recorded"] and ref["recomputed"], label
        assert ref["match"] is True, label
    # The active base was not used (recompiled) → unverified, not a failure.
    assert result["source_hashes"]["active_compiled_handoff"]["match"] is None
    # The candidate's own content hash is recorded for the future pointer (R2E.5b-4).
    assert isinstance(result["candidate_sha256"], str) and result["candidate_sha256"]


# --- fail-closed: missing / malformed inputs -------------------------------------


def test_missing_evidence_packet_blocks() -> None:
    result = evaluate(chain(), evidence_packet=None)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_MISSING_EVIDENCE_PACKET in result["promotion_blockers"]


def test_invalid_anchors_summary_blocks() -> None:
    bad = anchors_summary(anchor_yaml_payload(anchor_type="hot_tip"))
    inputs = chain(packet=evidence_packet(anchors=bad))
    result = evaluate(inputs)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_INVALID_RESEARCH_ANCHORS in result["promotion_blockers"]
    assert BLOCKER_NO_VALID_RESEARCH_ANCHOR in result["promotion_blockers"]


def test_no_accepted_support_signals_blocks() -> None:
    # Memo references a non-existent anchor → nothing accepted upstream.
    m = memo()
    m["ticker_relative_view"][0]["anchor_id_refs"] = ["DOES_NOT_EXIST"]
    result = evaluate(chain(m=m))
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_NO_ACCEPTED_SUPPORT_SIGNALS in result["promotion_blockers"]
    assert BLOCKER_NO_PREVIEW_ACTIONABLE_ROWS in result["promotion_blockers"]
    assert BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS in result["promotion_blockers"]


def test_analyst_memo_absent_blocks() -> None:
    inputs = chain()
    signals = build_compiled_support_signals(
        evidence_packet=inputs["evidence_packet"], analyst_memo=None, compilation_mode="evidence_only"
    )
    result = evaluate(inputs, compiled_support_signals=signals)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_ANALYST_MEMO_ABSENT_OR_INVALID in result["promotion_blockers"]


def test_low_memo_confidence_blocks() -> None:
    inputs = chain()
    signals = build_compiled_support_signals(
        evidence_packet=inputs["evidence_packet"],
        analyst_memo=memo(confidence="low"),
        compilation_mode=_MODE,
    )
    result = evaluate(inputs, compiled_support_signals=signals)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_MEMO_CONFIDENCE_LOW in result["promotion_blockers"]


def test_missing_preview_blocks() -> None:
    result = evaluate(chain(), actionable_preview=None)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_MISSING_ACTIONABLE_PREVIEW in result["promotion_blockers"]


def test_missing_candidate_blocks() -> None:
    result = evaluate(chain(), actionable_candidate=None)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_MISSING_ACTIONABLE_CANDIDATE in result["promotion_blockers"]
    assert BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS in result["promotion_blockers"]


def test_failed_candidate_validation_blocks() -> None:
    result = evaluate(chain(), actionable_candidate_validation={"valid": False})
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_CANDIDATE_VALIDATION_FAILED in result["promotion_blockers"]
    assert result["candidate_validation_passed"] is False


def test_missing_validation_blocks() -> None:
    result = evaluate(chain(), actionable_candidate_validation=None)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_CANDIDATE_VALIDATION_FAILED in result["promotion_blockers"]


def test_never_raises_on_all_none_inputs() -> None:
    result = evaluate_actionable_handoff_promotion_eligibility(
        evidence_packet=None,
        compiled_support_signals=None,
        actionable_preview=None,
        actionable_candidate=None,
        actionable_candidate_validation=None,
        actionable_candidate_metadata=None,
    )
    assert result["eligible_for_promotion"] is False
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["not_authorization"] is True
    assert BLOCKER_MISSING_EVIDENCE_PACKET in result["promotion_blockers"]
    assert BLOCKER_HASH_CHAIN_MISMATCH in result["promotion_blockers"]
    assert BLOCKER_MISSING_BUDGET_CONTEXT in result["promotion_blockers"]


# --- fail-closed: cap / universe / sleeve -----------------------------------------


def test_missing_cap_blocks() -> None:
    stgs = settings(max_new_tickers_per_week=None)
    result = evaluate(chain(stgs=stgs))
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_MAX_NEW_TICKERS_CAP_MISSING_OR_ZERO in result["promotion_blockers"]


def test_zero_cap_blocks() -> None:
    stgs = settings(
        max_new_tickers_per_week={
            "base_universe_new_tickers_per_week": 0,
            "extended_etf_sleeve_new_tickers_per_week": 2,
        }
    )
    result = evaluate(chain(stgs=stgs))
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_MAX_NEW_TICKERS_CAP_MISSING_OR_ZERO in result["promotion_blockers"]
    # With cap 0 the preview surfaces no rows either.
    assert BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS in result["promotion_blockers"]


def test_candidate_exceeding_cap_blocks() -> None:
    # Build a valid chain, then shrink the packet cap AFTER the candidate was
    # built (an inconsistent re-run) — but keep hashes matched by rebuilding the
    # metadata over the mutated packet.
    inputs = chain()
    inputs["evidence_packet"]["budget_settings"]["max_new_tickers_per_week"] = {
        "base_universe_new_tickers_per_week": 0,
        "extended_etf_sleeve_new_tickers_per_week": 2,
    }
    metadata = build_actionable_handoff_metadata(
        candidate=inputs["actionable_candidate"],
        validation=inputs["actionable_candidate_validation"],
        actionable_handoff_preview=inputs["actionable_preview"],
        compiled_support_signals=inputs["compiled_support_signals"],
        evidence_packet=inputs["evidence_packet"],
        base_candidate=None,
        used_active_compiled_handoff_as_base=False,
    )
    result = evaluate(inputs, actionable_candidate_metadata=metadata)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_CANDIDATE_EXCEEDS_MAX_NEW_TICKERS in result["promotion_blockers"]


def test_out_of_universe_actionable_ticker_blocks() -> None:
    inputs = chain()
    # Simulate a corrupted candidate whose promoted row is out of the packet universe.
    for row in inputs["actionable_candidate"]["buy_universe_scorecard"]:
        if row["actionability_status"] == "actionable_this_run":
            row["ticker"] = "TSLA"
    result = evaluate(inputs)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_OUT_OF_UNIVERSE_ACTIONABLE_TICKER in result["promotion_blockers"]


def test_extended_etf_sleeve_enabled_blocks() -> None:
    inputs = chain()
    inputs["actionable_candidate"]["optional_extended_etf_sleeve"]["enabled"] = True
    result = evaluate(inputs)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_EXTENDED_ETF_ENABLED in result["promotion_blockers"]


def test_universe_mismatch_blocks() -> None:
    inputs = chain()
    inputs["actionable_candidate"]["trade_universe"]["allowed_buy_tickers"] = ["QQQ", "VOO"]
    result = evaluate(inputs)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_UNIVERSE_MISMATCH in result["promotion_blockers"]


# --- fail-closed: data gaps / anchors ----------------------------------------------


def test_data_gap_marker_on_actionable_row_blocks() -> None:
    inputs = chain()
    for row in inputs["actionable_candidate"]["buy_universe_scorecard"]:
        if row["actionability_status"] == "actionable_this_run":
            row["thesis_12m_plus_summary"] = "DATA_GAP: thesis unavailable"
    result = evaluate(inputs)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_BLOCKING_DATA_GAP_ON_ACTIONABLE_ROW in result["promotion_blockers"]


def test_stale_referenced_anchor_blocks() -> None:
    # Anchor expires before today → stale at eligibility-check time.
    stale = anchors_summary(
        anchor_yaml_payload(valid_until="2026-06-20"), today=TODAY
    )
    inputs = chain(packet=evidence_packet(anchors=stale))
    result = evaluate(inputs)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_STALE_REFERENCED_ANCHOR in result["promotion_blockers"]


def test_anchor_expiring_after_today_passes_and_sets_expiry() -> None:
    soon = anchors_summary(anchor_yaml_payload(valid_until="2026-06-29"))
    inputs = chain(packet=evidence_packet(anchors=soon))
    result = evaluate(inputs)
    assert result["eligible_for_promotion"] is True
    assert result["promotion_expires_at"] == "2026-06-29"


def test_anchor_expired_relative_to_later_today_blocks() -> None:
    # Same chain, but eligibility is checked days later than the build: the anchor
    # summary flags are fresh, yet valid_until < today → fail closed.
    inputs = chain()
    result = evaluate(inputs, today="2026-08-01")
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_STALE_REFERENCED_ANCHOR in result["promotion_blockers"]


# --- fail-closed: hash chain / settings ----------------------------------------------


def test_mutated_evidence_packet_fails_hash_chain() -> None:
    inputs = chain()
    inputs["evidence_packet"]["universe"]["allowed_buy_tickers"] = ["QQQ", "VOO", "SMH", "VTI"]
    result = evaluate(inputs)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_HASH_CHAIN_MISMATCH in result["promotion_blockers"]
    assert result["hash_chain_valid"] is False
    assert result["source_hashes"]["evidence_packet"]["match"] is False


def test_missing_metadata_fails_hash_chain() -> None:
    result = evaluate(chain(), actionable_candidate_metadata=None)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_HASH_CHAIN_MISMATCH in result["promotion_blockers"]


def test_changed_strategy_settings_fail_settings_hash() -> None:
    inputs = chain()
    drifted = settings(core_universe=["QQQ", "VOO", "VTI"])
    result = evaluate(inputs, strategy_settings=drifted)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_STRATEGY_SETTINGS_HASH_MISMATCH in result["promotion_blockers"]


def test_absent_strategy_settings_fail_settings_hash() -> None:
    result = evaluate(chain(), strategy_settings=None)
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_STRATEGY_SETTINGS_HASH_MISMATCH in result["promotion_blockers"]


# --- fail-closed: budget context -------------------------------------------------------


def test_missing_hard_cap_blocks() -> None:
    stgs = settings(hard_cap_open_orders_budget=None)
    result = evaluate(chain(stgs=stgs))
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_MISSING_BUDGET_CONTEXT in result["promotion_blockers"]


def test_missing_target_new_buy_budget_blocks() -> None:
    stgs = settings(target_new_buy_budget_this_run=None)
    result = evaluate(chain(stgs=stgs))
    assert result["eligible_for_promotion"] is False
    assert BLOCKER_MISSING_BUDGET_CONTEXT in result["promotion_blockers"]
