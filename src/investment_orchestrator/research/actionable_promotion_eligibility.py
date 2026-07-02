"""Step 1C actionable-handoff promotion ELIGIBILITY checker (R2E.5b-3, report-only).

Evaluates — deterministically and fail-closed — whether the current report-only
`compiled_actionable_research_handoff_candidate.json` **would** be eligible for a
*future* promotion into the active compiled handoff (the R2E.5b-2 §25 design).
It answers only "would promotion be eligible?"; it **never promotes**.

This module changes **no** production behavior:

* It writes only its own artifact (`compiled_actionable_handoff_promotion_eligibility.json`).
* It does NOT create the future `active_research_handoff_source.json` pointer and
  does NOT create any effective handoff file.
* It is NEVER fed into the availability evaluator, the degraded-mode decision,
  Step 2 render, the weekly path, the order compiler, or any gate.
* It NEVER authorizes a trade and NEVER adds `NEW_BUY` / `ORDER_COMPILATION`;
  `STRICT_FRESH_COMPILED_ACTIONABLE` / `STRICT_FRESH_WITH_LLM_MEMO` are NOT enabled.

Every artifact carries `is_llm_generated: false`, `report_only: true`,
`permission_effect: "none"`, `not_authorization: true`. Even
`eligible_for_promotion: true` is an *observation about a hypothetical future
step* — promotion itself (the pointer, R2E.5b-4) and any gate opening
(R2E.5b-5..7) each require their own future explicit PR.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from typing import Any

from investment_orchestrator.research.actionable_handoff_candidate import (
    CANDIDATE_SCHEMA_VERSION as ACTIONABLE_CANDIDATE_SCHEMA_VERSION,
)
from investment_orchestrator.research.actionable_handoff_preview import (
    SCHEMA_VERSION as ACTIONABLE_PREVIEW_SCHEMA_VERSION,
)
from investment_orchestrator.research.evidence_packet import (
    SCHEMA_VERSION as EVIDENCE_PACKET_SCHEMA_VERSION,
)
from investment_orchestrator.research.research_anchors import normalize_iso_date_value
from investment_orchestrator.research.support_signals import REASON_MEMO_CONFIDENCE_LOW
from investment_orchestrator.state.last_good_research_handoff import (
    decision_relevant_settings,
    strategy_settings_hash,
)
from investment_orchestrator.validators.validate_research_handoff import DATA_GAP_MARKERS


SCHEMA_VERSION = "compiled_actionable_handoff_promotion_eligibility_v1"

_ACTIONABLE_STATUS = "actionable_this_run"

SEVERITY_BLOCKER = "blocker"
SEVERITY_WARNING = "warning"

# --- deterministic blocker reason codes (fail-closed checklist, §25.2) --------
BLOCKER_MISSING_EVIDENCE_PACKET = "missing_evidence_packet"
BLOCKER_INVALID_RESEARCH_ANCHORS = "invalid_research_anchors"
BLOCKER_NO_VALID_RESEARCH_ANCHOR = "no_valid_research_anchor"
BLOCKER_ANALYST_MEMO_ABSENT_OR_INVALID = "analyst_memo_absent_or_invalid"
BLOCKER_MEMO_CONFIDENCE_LOW = "memo_confidence_low"
BLOCKER_NO_ACCEPTED_SUPPORT_SIGNALS = "no_accepted_support_signals"
BLOCKER_MISSING_ACTIONABLE_PREVIEW = "missing_actionable_preview"
BLOCKER_NO_PREVIEW_ACTIONABLE_ROWS = "no_preview_actionable_rows"
BLOCKER_MISSING_ACTIONABLE_CANDIDATE = "missing_actionable_candidate"
BLOCKER_CANDIDATE_VALIDATION_FAILED = "candidate_validation_failed"
BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS = "no_candidate_actionable_rows"
BLOCKER_MAX_NEW_TICKERS_CAP_MISSING_OR_ZERO = "max_new_tickers_cap_missing_or_zero"
BLOCKER_CANDIDATE_EXCEEDS_MAX_NEW_TICKERS = "candidate_exceeds_max_new_tickers"
BLOCKER_EXTENDED_ETF_ENABLED = "extended_etf_enabled"
BLOCKER_OUT_OF_UNIVERSE_ACTIONABLE_TICKER = "out_of_universe_actionable_ticker"
BLOCKER_BLOCKING_DATA_GAP_ON_ACTIONABLE_ROW = "blocking_data_gap_on_actionable_row"
BLOCKER_MISSING_PRIMARY_ANCHOR = "missing_primary_anchor"
BLOCKER_STALE_REFERENCED_ANCHOR = "stale_referenced_anchor"
BLOCKER_HASH_CHAIN_MISMATCH = "hash_chain_mismatch"
BLOCKER_STRATEGY_SETTINGS_HASH_MISMATCH = "strategy_settings_hash_mismatch"
BLOCKER_UNIVERSE_MISMATCH = "universe_mismatch"
BLOCKER_MISSING_BUDGET_CONTEXT = "missing_budget_context"

PROMOTION_BLOCKER_REASON_CODES = (
    BLOCKER_MISSING_EVIDENCE_PACKET,
    BLOCKER_INVALID_RESEARCH_ANCHORS,
    BLOCKER_NO_VALID_RESEARCH_ANCHOR,
    BLOCKER_ANALYST_MEMO_ABSENT_OR_INVALID,
    BLOCKER_MEMO_CONFIDENCE_LOW,
    BLOCKER_NO_ACCEPTED_SUPPORT_SIGNALS,
    BLOCKER_MISSING_ACTIONABLE_PREVIEW,
    BLOCKER_NO_PREVIEW_ACTIONABLE_ROWS,
    BLOCKER_MISSING_ACTIONABLE_CANDIDATE,
    BLOCKER_CANDIDATE_VALIDATION_FAILED,
    BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS,
    BLOCKER_MAX_NEW_TICKERS_CAP_MISSING_OR_ZERO,
    BLOCKER_CANDIDATE_EXCEEDS_MAX_NEW_TICKERS,
    BLOCKER_EXTENDED_ETF_ENABLED,
    BLOCKER_OUT_OF_UNIVERSE_ACTIONABLE_TICKER,
    BLOCKER_BLOCKING_DATA_GAP_ON_ACTIONABLE_ROW,
    BLOCKER_MISSING_PRIMARY_ANCHOR,
    BLOCKER_STALE_REFERENCED_ANCHOR,
    BLOCKER_HASH_CHAIN_MISMATCH,
    BLOCKER_STRATEGY_SETTINGS_HASH_MISMATCH,
    BLOCKER_UNIVERSE_MISMATCH,
    BLOCKER_MISSING_BUDGET_CONTEXT,
)

# --- deterministic warning reason codes (never affect eligibility) ------------
WARNING_RECOMPILED_BASE_USED = "recompiled_base_used_not_active_compiled_handoff"
WARNING_NON_BLOCKING_DATA_GAPS_PRESENT = "non_blocking_data_gaps_present"

PROMOTION_WARNING_REASON_CODES = (
    WARNING_RECOMPILED_BASE_USED,
    WARNING_NON_BLOCKING_DATA_GAPS_PRESENT,
)

_NON_AUTHORIZATION_NOTE = (
    "Report-only promotion-ELIGIBILITY check (R2E.5b-3). This artifact answers only whether the "
    "separate report-only actionable compiled-handoff candidate WOULD be eligible for a FUTURE "
    "promotion (R2E.5b-2 design). It never promotes: no active_research_handoff_source.json pointer "
    "and no effective handoff file is created, the active compiled_research_handoff_candidate.json "
    "stays non-actionable, and this artifact is never fed into the availability evaluator, the "
    "degraded-mode decision, Step 2, the weekly path, the order compiler, or any gate. It NEVER "
    "authorizes a trade and adds no NEW_BUY / ORDER_COMPILATION permission "
    "(permission_effect=none, not_authorization=true). Promotion (the pointer) and any gate opening "
    "each require a future explicit PR."
)


# --- pure evaluator ------------------------------------------------------------


def evaluate_actionable_handoff_promotion_eligibility(
    *,
    evidence_packet: Mapping[str, Any] | None,
    compiled_support_signals: Mapping[str, Any] | None,
    actionable_preview: Mapping[str, Any] | None,
    actionable_candidate: Mapping[str, Any] | None,
    actionable_candidate_validation: Mapping[str, Any] | None,
    actionable_candidate_metadata: Mapping[str, Any] | None,
    active_compiled_handoff: Mapping[str, Any] | None = None,
    strategy_settings: Mapping[str, Any] | None = None,
    today: Any = None,
    generated_at: str | None = None,
    source_artifacts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the fail-closed promotion-eligibility checklist (pure; never raises).

    Any missing / malformed input fails the corresponding check (never crashes),
    yielding ``eligible_for_promotion: false`` with deterministic blocker codes.
    ``today`` (ISO string or ``date``) re-checks anchor expiry at eligibility time;
    when omitted, the packet's own precomputed stale/usable flags still apply.
    """
    packet = evidence_packet if isinstance(evidence_packet, Mapping) else None
    signals = compiled_support_signals if isinstance(compiled_support_signals, Mapping) else None
    preview = actionable_preview if isinstance(actionable_preview, Mapping) else None
    candidate = actionable_candidate if isinstance(actionable_candidate, Mapping) else None
    validation = (
        actionable_candidate_validation if isinstance(actionable_candidate_validation, Mapping) else None
    )
    metadata = (
        actionable_candidate_metadata if isinstance(actionable_candidate_metadata, Mapping) else None
    )
    today_iso = normalize_iso_date_value(today)
    today_date = date.fromisoformat(today_iso) if today_iso is not None else None

    checks: list[dict[str, Any]] = []

    def check(check_id: str, passed: bool, *, severity: str = SEVERITY_BLOCKER, **details: Any) -> bool:
        checks.append(
            {"check_id": check_id, "passed": bool(passed), "severity": severity, "details": details}
        )
        return bool(passed)

    # --- A. input-chain validity / freshness ----------------------------------
    check(
        "evidence_packet_present_and_deterministic",
        packet is not None
        and packet.get("schema_version") == EVIDENCE_PACKET_SCHEMA_VERSION
        and packet.get("is_llm_generated") is False,
        schema_version=packet.get("schema_version") if packet else None,
    )

    anchors_summary = packet.get("research_anchors") if packet else None
    anchors_summary = anchors_summary if isinstance(anchors_summary, Mapping) else None
    check(
        "research_anchors_valid",
        anchors_summary is not None
        and anchors_summary.get("available") is True
        and anchors_summary.get("valid") is True,
        available=anchors_summary.get("available") if anchors_summary else None,
        valid=anchors_summary.get("valid") if anchors_summary else None,
    )

    valid_anchor_count = anchors_summary.get("valid_anchor_count") if anchors_summary else None
    check(
        "valid_research_anchor_exists",
        isinstance(valid_anchor_count, int)
        and not isinstance(valid_anchor_count, bool)
        and valid_anchor_count >= 1,
        valid_anchor_count=valid_anchor_count,
    )

    check(
        "analyst_memo_present_and_valid",
        signals is not None
        and signals.get("analyst_memo_present") is True
        and signals.get("analyst_memo_valid") is True,
        analyst_memo_present=signals.get("analyst_memo_present") if signals else None,
        analyst_memo_valid=signals.get("analyst_memo_valid") if signals else None,
    )

    global_blockers = _string_items(signals.get("global_blockers")) if signals else []
    check(
        "memo_confidence_not_low",
        signals is not None and REASON_MEMO_CONFIDENCE_LOW not in global_blockers,
        global_blockers=global_blockers,
    )

    accepted = signals.get("accepted_support_signals") if signals else None
    accepted_count = len(accepted) if isinstance(accepted, list) else 0
    check(
        "accepted_support_signals_non_empty",
        accepted_count > 0,
        accepted_support_signal_count=accepted_count,
    )

    check(
        "actionable_preview_present",
        preview is not None
        and preview.get("schema_version") == ACTIONABLE_PREVIEW_SCHEMA_VERSION
        and preview.get("report_only") is True
        and preview.get("not_authorization") is True,
        schema_version=preview.get("schema_version") if preview else None,
    )

    preview_rows = preview.get("preview_actionable_rows") if preview else None
    preview_row_count = len(preview_rows) if isinstance(preview_rows, list) else 0
    check(
        "preview_actionable_rows_non_empty",
        preview_row_count > 0,
        preview_actionable_row_count=preview_row_count,
    )

    # --- B. candidate quality --------------------------------------------------
    check(
        "actionable_candidate_present",
        candidate is not None
        and candidate.get("schema_version") == ACTIONABLE_CANDIDATE_SCHEMA_VERSION
        and candidate.get("is_llm_generated") is False
        and candidate.get("report_only") is True
        and candidate.get("not_authorization") is True,
        schema_version=candidate.get("schema_version") if candidate else None,
    )

    validation_passed = validation is not None and validation.get("valid") is True
    check(
        "candidate_validation_passed",
        validation_passed,
        valid=validation.get("valid") if validation else None,
    )

    actionable_rows = _actionable_rows(candidate)
    actionable_tickers = [row["ticker"] for row in actionable_rows]
    row_count = len(actionable_rows)
    check("candidate_actionable_rows_exist", row_count > 0, candidate_actionable_row_count=row_count)

    base_cap = _base_new_ticker_cap(packet)
    check(
        "new_ticker_cap_present_and_positive",
        base_cap is not None and base_cap > 0,
        base_universe_new_tickers_per_week=base_cap,
    )
    check(
        "candidate_within_new_ticker_cap",
        base_cap is not None and row_count <= base_cap,
        candidate_actionable_row_count=row_count,
        base_universe_new_tickers_per_week=base_cap,
    )

    sleeve = candidate.get("optional_extended_etf_sleeve") if candidate else None
    sleeve = sleeve if isinstance(sleeve, Mapping) else None
    approved_extended = _ticker_set(_universe(packet).get("approved_extended_etf"))
    extended_promoted = sorted(t for t in actionable_tickers if t in approved_extended)
    check(
        "extended_etf_sleeve_disabled",
        sleeve is not None and sleeve.get("enabled") is False and not extended_promoted,
        sleeve_enabled=sleeve.get("enabled") if sleeve else None,
        extended_tickers_promoted=extended_promoted,
    )

    allowed_buy = _ticker_set(_universe(packet).get("allowed_buy_tickers"))
    out_of_universe = sorted(t for t in actionable_tickers if t not in allowed_buy)
    check(
        "actionable_tickers_in_base_universe",
        bool(allowed_buy) and not out_of_universe,
        out_of_universe_tickers=out_of_universe,
    )

    tainted = sorted({row["ticker"] for row in actionable_rows if _row_has_data_gap(row["row"])})
    check(
        "no_blocking_data_gap_on_actionable_rows",
        not tainted,
        tainted_tickers=tainted,
    )

    missing_anchor_fields = sorted(
        {row["ticker"] for row in actionable_rows if not _primary_anchor_fields_present(row["row"])}
    )
    check(
        "primary_anchor_fields_present",
        row_count > 0 and not missing_anchor_fields,
        tickers_missing_primary_anchor=missing_anchor_fields,
    )

    anchors_by_id = _anchors_by_id(anchors_summary)
    anchor_problems, earliest_valid_until = _referenced_anchor_freshness(
        actionable_rows, anchors_by_id=anchors_by_id, today=today_date
    )
    check(
        "referenced_anchors_fresh",
        row_count > 0 and not anchor_problems,
        problems=anchor_problems,
        earliest_anchor_valid_until=earliest_valid_until,
        today=today_iso,
    )

    # --- C. hash-chain consistency ----------------------------------------------
    source_hashes, hash_chain_valid = _verify_hash_chain(
        metadata=metadata,
        evidence_packet=packet,
        compiled_support_signals=signals,
        actionable_preview=preview,
        active_compiled_handoff=active_compiled_handoff
        if isinstance(active_compiled_handoff, Mapping)
        else None,
    )
    check("hash_chain_valid", hash_chain_valid, source_hashes=source_hashes)

    current_settings_hash = strategy_settings_hash(decision_relevant_settings(strategy_settings))
    recorded_settings_hash = packet.get("strategy_settings_hash") if packet else None
    recorded_settings_hash = recorded_settings_hash if isinstance(recorded_settings_hash, str) else None
    check(
        "strategy_settings_hash_match",
        current_settings_hash is not None
        and recorded_settings_hash is not None
        and current_settings_hash == recorded_settings_hash,
        current=current_settings_hash,
        recorded=recorded_settings_hash,
    )

    trade_universe = candidate.get("trade_universe") if candidate else None
    trade_universe = trade_universe if isinstance(trade_universe, Mapping) else {}
    candidate_universe = _ticker_set(trade_universe.get("allowed_buy_tickers"))
    check(
        "universe_match",
        bool(allowed_buy) and candidate_universe == allowed_buy,
        evidence_packet_universe=sorted(allowed_buy),
        candidate_universe=sorted(candidate_universe),
    )

    # --- D. downstream budget context --------------------------------------------
    budget = packet.get("budget_settings") if packet else None
    budget = budget if isinstance(budget, Mapping) else {}
    missing_budget_fields = [
        field
        for field in (
            "hard_cap_open_orders_budget",
            "target_new_buy_budget_this_run",
            "max_new_tickers_per_week",
        )
        if budget.get(field) is None
    ]
    check(
        "budget_context_present",
        packet is not None and not missing_budget_fields,
        missing_budget_fields=missing_budget_fields,
    )

    # --- warnings (never affect eligibility) --------------------------------------
    check(
        "used_active_compiled_handoff_as_base",
        metadata is not None and metadata.get("used_active_compiled_handoff_as_base") is True,
        severity=SEVERITY_WARNING,
    )
    data_gaps = packet.get("data_gaps") if packet else None
    data_gap_fields = (
        [g.get("field") for g in data_gaps if isinstance(g, Mapping)] if isinstance(data_gaps, list) else []
    )
    check(
        "no_non_blocking_data_gaps",
        not data_gap_fields,
        severity=SEVERITY_WARNING,
        data_gap_fields=data_gap_fields,
    )

    blockers = [
        _CHECK_FAILURE_REASON[c["check_id"]]
        for c in checks
        if c["severity"] == SEVERITY_BLOCKER and not c["passed"]
    ]
    warnings = [
        _CHECK_FAILURE_REASON[c["check_id"]]
        for c in checks
        if c["severity"] == SEVERITY_WARNING and not c["passed"]
    ]
    eligible = not blockers

    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "generated_at": generated_at,
        "today": today_iso,
        "eligible_for_promotion": eligible,
        "promotion_blockers": blockers,
        "promotion_warnings": warnings,
        "checks": checks,
        "source_artifacts": {str(k): str(v) for k, v in (source_artifacts or {}).items()},
        "source_hashes": source_hashes,
        "hash_chain_valid": hash_chain_valid,
        # Content hash of the evaluated candidate itself, so the future pointer
        # (previewed in R2E.5b-4) can verify it points at exactly the candidate
        # this eligibility verdict was computed over.
        "candidate_sha256": _sha256_of(candidate) if candidate is not None else None,
        "candidate_validation_passed": validation_passed,
        "candidate_actionable_row_count": row_count,
        "preview_actionable_row_count": preview_row_count,
        "accepted_support_signal_count": accepted_count,
        "actionable_this_run_tickers": actionable_tickers,
        "strategy_settings_hash": current_settings_hash,
        "earliest_anchor_valid_until": earliest_valid_until,
        # A future promotion must never be consumed past the earliest cited-anchor
        # expiry (§25.2 #10 / §25.4); recorded here for the future pointer PR.
        "promotion_expires_at": earliest_valid_until,
        "consumed_by_availability": False,
        "consumed_by_step2": False,
        "consumed_by_gates": False,
        "notes": _NON_AUTHORIZATION_NOTE,
    }


# Positive check ids -> deterministic failure reason codes.
_CHECK_FAILURE_REASON: dict[str, str] = {
    "evidence_packet_present_and_deterministic": BLOCKER_MISSING_EVIDENCE_PACKET,
    "research_anchors_valid": BLOCKER_INVALID_RESEARCH_ANCHORS,
    "valid_research_anchor_exists": BLOCKER_NO_VALID_RESEARCH_ANCHOR,
    "analyst_memo_present_and_valid": BLOCKER_ANALYST_MEMO_ABSENT_OR_INVALID,
    "memo_confidence_not_low": BLOCKER_MEMO_CONFIDENCE_LOW,
    "accepted_support_signals_non_empty": BLOCKER_NO_ACCEPTED_SUPPORT_SIGNALS,
    "actionable_preview_present": BLOCKER_MISSING_ACTIONABLE_PREVIEW,
    "preview_actionable_rows_non_empty": BLOCKER_NO_PREVIEW_ACTIONABLE_ROWS,
    "actionable_candidate_present": BLOCKER_MISSING_ACTIONABLE_CANDIDATE,
    "candidate_validation_passed": BLOCKER_CANDIDATE_VALIDATION_FAILED,
    "candidate_actionable_rows_exist": BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS,
    "new_ticker_cap_present_and_positive": BLOCKER_MAX_NEW_TICKERS_CAP_MISSING_OR_ZERO,
    "candidate_within_new_ticker_cap": BLOCKER_CANDIDATE_EXCEEDS_MAX_NEW_TICKERS,
    "extended_etf_sleeve_disabled": BLOCKER_EXTENDED_ETF_ENABLED,
    "actionable_tickers_in_base_universe": BLOCKER_OUT_OF_UNIVERSE_ACTIONABLE_TICKER,
    "no_blocking_data_gap_on_actionable_rows": BLOCKER_BLOCKING_DATA_GAP_ON_ACTIONABLE_ROW,
    "primary_anchor_fields_present": BLOCKER_MISSING_PRIMARY_ANCHOR,
    "referenced_anchors_fresh": BLOCKER_STALE_REFERENCED_ANCHOR,
    "hash_chain_valid": BLOCKER_HASH_CHAIN_MISMATCH,
    "strategy_settings_hash_match": BLOCKER_STRATEGY_SETTINGS_HASH_MISMATCH,
    "universe_match": BLOCKER_UNIVERSE_MISMATCH,
    "budget_context_present": BLOCKER_MISSING_BUDGET_CONTEXT,
    "used_active_compiled_handoff_as_base": WARNING_RECOMPILED_BASE_USED,
    "no_non_blocking_data_gaps": WARNING_NON_BLOCKING_DATA_GAPS_PRESENT,
}


# --- disk wrapper --------------------------------------------------------------


def write_actionable_promotion_eligibility(
    *,
    output_path: Any,
    evidence_packet: Mapping[str, Any] | None,
    compiled_support_signals: Mapping[str, Any] | None,
    actionable_preview: Mapping[str, Any] | None,
    actionable_candidate: Mapping[str, Any] | None,
    actionable_candidate_validation: Mapping[str, Any] | None,
    actionable_candidate_metadata: Mapping[str, Any] | None,
    active_compiled_handoff: Mapping[str, Any] | None = None,
    strategy_settings: Mapping[str, Any] | None = None,
    today: Any = None,
    generated_at: str | None = None,
    evidence_packet_path: Any = None,
    compiled_support_signals_path: Any = None,
    actionable_preview_path: Any = None,
    actionable_candidate_path: Any = None,
    actionable_candidate_validation_path: Any = None,
    actionable_candidate_metadata_path: Any = None,
    active_compiled_handoff_path: Any = None,
) -> dict[str, Any]:
    """Evaluate + write the report-only eligibility artifact; return a small summary."""
    from investment_orchestrator.common.io import write_json

    source_artifacts = {
        key: str(value)
        for key, value in {
            "evidence_packet": evidence_packet_path,
            "compiled_support_signals": compiled_support_signals_path,
            "actionable_handoff_preview": actionable_preview_path,
            "actionable_candidate": actionable_candidate_path,
            "actionable_candidate_validation": actionable_candidate_validation_path,
            "actionable_candidate_metadata": actionable_candidate_metadata_path,
            "active_compiled_handoff": active_compiled_handoff_path,
        }.items()
        if value is not None
    }
    payload = evaluate_actionable_handoff_promotion_eligibility(
        evidence_packet=evidence_packet,
        compiled_support_signals=compiled_support_signals,
        actionable_preview=actionable_preview,
        actionable_candidate=actionable_candidate,
        actionable_candidate_validation=actionable_candidate_validation,
        actionable_candidate_metadata=actionable_candidate_metadata,
        active_compiled_handoff=active_compiled_handoff,
        strategy_settings=strategy_settings,
        today=today,
        generated_at=generated_at,
        source_artifacts=source_artifacts,
    )
    write_json(output_path, payload)
    return {
        "actionable_promotion_eligibility_path": str(output_path),
        "eligible_for_promotion": str(payload["eligible_for_promotion"]),
        "promotion_blocker_count": str(len(payload["promotion_blockers"])),
    }


# --- helpers ---------------------------------------------------------------------


def _actionable_rows(candidate: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Promoted rows derived from the candidate scorecard (the source of truth)."""
    if not isinstance(candidate, Mapping):
        return []
    scorecard = candidate.get("buy_universe_scorecard")
    if not isinstance(scorecard, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in scorecard:
        if not isinstance(row, Mapping) or row.get("actionability_status") != _ACTIONABLE_STATUS:
            continue
        ticker = _ticker(row.get("ticker"))
        if ticker:
            rows.append({"ticker": ticker, "row": row})
    return rows


def _universe(packet: Mapping[str, Any] | None) -> Mapping[str, Any]:
    universe = packet.get("universe") if isinstance(packet, Mapping) else None
    return universe if isinstance(universe, Mapping) else {}


def _base_new_ticker_cap(packet: Mapping[str, Any] | None) -> int | None:
    """Base-universe weekly new-ticker cap from the packet snapshot (None when absent)."""
    if not isinstance(packet, Mapping):
        return None
    budget = packet.get("budget_settings")
    if not isinstance(budget, Mapping):
        return None
    snapshot = budget.get("max_new_tickers_per_week")
    if isinstance(snapshot, bool):
        return None
    if isinstance(snapshot, int):
        return snapshot if snapshot >= 0 else None
    if isinstance(snapshot, Mapping):
        value = snapshot.get("base_universe_new_tickers_per_week")
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value >= 0:
            return value
    return None


def _row_has_data_gap(row: Mapping[str, Any]) -> bool:
    fields = (
        row.get("thesis_12m_plus_summary"),
        row.get("primary_anchor_event_id"),
        row.get("primary_anchor_date_et"),
        row.get("primary_anchor_type"),
        *(_string_items(row.get("event_id_refs"))),
        *(_string_items(row.get("structural_theme_refs"))),
    )
    for value in fields:
        if isinstance(value, str) and _has_data_gap_marker(value):
            return True
    return False


def _has_data_gap_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in DATA_GAP_MARKERS)


def _primary_anchor_fields_present(row: Mapping[str, Any]) -> bool:
    event_id = row.get("primary_anchor_event_id")
    date_et = row.get("primary_anchor_date_et")
    refs = _string_items(row.get("event_id_refs")) + _string_items(row.get("structural_theme_refs"))
    return (
        isinstance(event_id, str)
        and bool(event_id.strip())
        and isinstance(date_et, str)
        and bool(date_et.strip())
        and bool(refs)
    )


def _anchors_by_id(anchors_summary: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(anchors_summary, Mapping):
        return {}
    anchors = anchors_summary.get("anchors")
    if not isinstance(anchors, list):
        return {}
    by_id: dict[str, Mapping[str, Any]] = {}
    for anchor in anchors:
        if not isinstance(anchor, Mapping):
            continue
        anchor_id = anchor.get("anchor_id")
        if isinstance(anchor_id, str) and anchor_id.strip():
            by_id[anchor_id.strip()] = anchor
    return by_id


def _referenced_anchor_freshness(
    actionable_rows: list[dict[str, Any]],
    *,
    anchors_by_id: Mapping[str, Mapping[str, Any]],
    today: date | None,
) -> tuple[list[str], str | None]:
    """Re-check every anchor cited by a promoted row; return (problems, earliest valid_until).

    Fail closed: an unresolvable ref, a missing ``valid_until``, a stale/unusable
    summary flag, or (when ``today`` is known) ``valid_until < today`` is a problem.
    """
    problems: list[str] = []
    valid_until_dates: list[date] = []
    for entry in actionable_rows:
        ticker = entry["ticker"]
        row = entry["row"]
        refs = _string_items(row.get("event_id_refs")) + _string_items(row.get("structural_theme_refs"))
        if not refs:
            problems.append(f"{ticker}: no anchor refs on actionable row.")
            continue
        for ref in refs:
            anchor = anchors_by_id.get(ref)
            if anchor is None:
                problems.append(f"{ticker}: referenced anchor {ref!r} not found in evidence packet.")
                continue
            if anchor.get("valid") is not True or anchor.get("usable") is not True or anchor.get("stale") is True:
                problems.append(f"{ticker}: referenced anchor {ref!r} is stale or not usable.")
                continue
            valid_until_iso = normalize_iso_date_value(anchor.get("valid_until"))
            if valid_until_iso is None:
                problems.append(f"{ticker}: referenced anchor {ref!r} has no valid_until date.")
                continue
            valid_until = date.fromisoformat(valid_until_iso)
            if today is not None and valid_until < today:
                problems.append(
                    f"{ticker}: referenced anchor {ref!r} expired {valid_until_iso} < today."
                )
                continue
            valid_until_dates.append(valid_until)
    if problems or not valid_until_dates:
        return problems, min(valid_until_dates).isoformat() if valid_until_dates else None
    return [], min(valid_until_dates).isoformat()


def _verify_hash_chain(
    *,
    metadata: Mapping[str, Any] | None,
    evidence_packet: Mapping[str, Any] | None,
    compiled_support_signals: Mapping[str, Any] | None,
    actionable_preview: Mapping[str, Any] | None,
    active_compiled_handoff: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Recompute the candidate metadata's recorded source hashes and compare.

    The three decision inputs (evidence packet, support signals, preview) are
    REQUIRED to match (fail closed on missing metadata / recorded hash / object).
    The active-base ref is verified when both sides are available; when the base
    object is not supplied it is recorded as unverified (``match: null``) — the
    strict validator already re-checked the candidate itself, and the future
    pointer PR performs the full re-verification at promotion time.
    """
    required = {
        "evidence_packet": ("source_evidence_packet", evidence_packet),
        "compiled_support_signals": ("source_compiled_support_signals", compiled_support_signals),
        "actionable_handoff_preview": ("source_actionable_handoff_preview", actionable_preview),
    }
    source_hashes: dict[str, Any] = {}
    all_match = isinstance(metadata, Mapping)
    for label, (metadata_key, obj) in required.items():
        recorded = _recorded_hash(metadata, metadata_key)
        recomputed = _sha256_of(obj) if isinstance(obj, Mapping) else None
        match = recorded is not None and recomputed is not None and recorded == recomputed
        source_hashes[label] = {"recorded": recorded, "recomputed": recomputed, "match": match}
        all_match = all_match and match

    recorded = _recorded_hash(metadata, "source_active_compiled_handoff")
    recomputed = _sha256_of(active_compiled_handoff) if isinstance(active_compiled_handoff, Mapping) else None
    if recorded is not None and recomputed is not None:
        match: bool | None = recorded == recomputed
        all_match = all_match and match
    else:
        match = None
    source_hashes["active_compiled_handoff"] = {
        "recorded": recorded,
        "recomputed": recomputed,
        "match": match,
    }
    return source_hashes, all_match


def _recorded_hash(metadata: Mapping[str, Any] | None, key: str) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    ref = metadata.get(key)
    if not isinstance(ref, Mapping):
        return None
    value = ref.get("sha256")
    return value if isinstance(value, str) and value else None


def _sha256_of(value: Any) -> str | None:
    """Canonical content hash — identical serialization to the candidate metadata."""
    if value is None:
        return None
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _ticker_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item.strip().upper() for item in value if isinstance(item, str) and item.strip()}


def _ticker(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()
