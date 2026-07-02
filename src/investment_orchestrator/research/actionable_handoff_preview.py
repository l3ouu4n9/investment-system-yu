"""Step 1C actionable-handoff PREVIEW (R2E.5b-0, report-only *separate* artifact).

Produces a **deterministic, non-authoritative** preview of *which tickers would
become actionable rows IF a future explicit PR opened an actionable path* from
the already-compiled report-only inputs (``accepted_support_signals`` +
``evidence_packet.research_anchors`` + the analyst memo). It answers "does the
future actionable mapping look reasonable?" **without changing anything today.**

This module changes **no** production behavior:

* It NEVER authorizes a trade and NEVER adds ``NEW_BUY`` / ``ORDER_COMPILATION``.
* It does NOT feed the active compiled handoff — ``positive_delta_research_supported``
  stays ``[]``, no ``actionable_this_run`` row is emitted there, and
  ``primary_anchor_event_id`` stays ``null``.
* It does NOT feed the availability evaluator, the Step 2/3/4 workflow, the order
  compiler, prompts, gates, or ``allowed_actions``. ``STRICT_FRESH_WITH_LLM_MEMO``
  is NOT enabled.

Every top-level artifact carries ``report_only: true``, ``permission_effect:
"none"``, and ``not_authorization: true``. A preview row's
``actionability_status_preview == "actionable_this_run"`` describes a *hypothetical
future* row only — paired with ``not_authorization: true`` so it can never read as
a live authorization. Promoting this preview into the active compiled handoff, and
then separately into the gates, each require a future explicit PR.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from investment_orchestrator.research.research_anchors import ANCHOR_TYPES
from investment_orchestrator.research.support_signals import (
    REASON_ANALYST_MEMO_ABSENT,
    REASON_ANALYST_MEMO_INVALID,
    REASON_ANCHOR_CONFIDENCE_FLOOR_NOT_MET,
    REASON_ANCHOR_NOT_APPLICABLE,
    REASON_ANCHOR_SOURCE_TYPE_NOT_ALLOWED,
    REASON_ANCHOR_TYPE_NOT_ALLOWED,
    REASON_BLOCKING_DATA_GAP,
    REASON_EXTENDED_ETF_NOT_ALLOWED,
    REASON_LISTED_IN_AVOID,
    REASON_MEMO_CONFIDENCE_LOW,
    REASON_MISSING_ANCHOR_ID_REFS,
    REASON_MISSING_RATIONALE,
    REASON_MISSING_SOURCE_NOTES,
    REASON_MISSING_VALID_ANCHOR_SOURCE,
    REASON_OUT_OF_UNIVERSE,
    REASON_REFERENCED_ANCHOR_NOT_FOUND,
    REASON_REFERENCED_ANCHOR_STALE,
    REASON_STANCE_NOT_PREFER,
)


SCHEMA_VERSION = "compiled_actionable_handoff_preview_v1"

# The preview never considers the extended ETF sleeve (base universe only), so
# this is a hard-coded ``false`` in the artifact and here for callers.
EXTENDED_ETF_SLEEVE_PREVIEW_ENABLED = False

# A hypothetical future actionable row's status label (paired with
# not_authorization=true so it can never read as a live authorization).
ACTIONABILITY_STATUS_PREVIEW = "actionable_this_run"

_PREFER_STANCE = "prefer"
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
_STRUCTURAL_ANCHOR_TYPE = "structural_theme"
_SCHEDULED_EVENT_ANCHOR_TYPES = frozenset(t for t in ANCHOR_TYPES if t.startswith("scheduled_"))

# --- deterministic preview rejection reason codes ----------------------------
PREVIEW_LIMIT_MAX_NEW_TICKERS_EXCEEDED = "preview_limit_max_new_tickers_exceeded"
PREVIEW_MISSING_ANCHOR = "preview_missing_anchor"
PREVIEW_EXTENDED_ETF_NOT_ALLOWED = "preview_extended_etf_not_allowed"
PREVIEW_MISSING_PRIMARY_ANCHOR_DATE = "preview_missing_primary_anchor_date"
PREVIEW_MISSING_EVENT_OR_THEME_REF = "preview_missing_event_or_theme_ref"
PREVIEW_BLOCKING_DATA_GAP = "preview_blocking_data_gap"
PREVIEW_LOW_CONFIDENCE = "preview_low_confidence"
PREVIEW_NO_ACCEPTED_SUPPORT_SIGNAL = "preview_no_accepted_support_signal"
PREVIEW_OUT_OF_UNIVERSE = "preview_out_of_base_allowed_universe"
PREVIEW_LISTED_IN_AVOID = "preview_avoid_or_deprioritize"
PREVIEW_MISSING_RATIONALE = "preview_missing_rationale"
PREVIEW_MISSING_SOURCE_NOTES = "preview_missing_source_notes"
PREVIEW_STANCE_NOT_PREFER = "preview_stance_not_prefer"
PREVIEW_ANALYST_MEMO_ABSENT = "preview_analyst_memo_absent"
PREVIEW_ANALYST_MEMO_INVALID = "preview_analyst_memo_invalid"

PREVIEW_REJECTION_REASON_CODES = (
    PREVIEW_LIMIT_MAX_NEW_TICKERS_EXCEEDED,
    PREVIEW_MISSING_ANCHOR,
    PREVIEW_EXTENDED_ETF_NOT_ALLOWED,
    PREVIEW_MISSING_PRIMARY_ANCHOR_DATE,
    PREVIEW_MISSING_EVENT_OR_THEME_REF,
    PREVIEW_BLOCKING_DATA_GAP,
    PREVIEW_LOW_CONFIDENCE,
    PREVIEW_NO_ACCEPTED_SUPPORT_SIGNAL,
    PREVIEW_OUT_OF_UNIVERSE,
    PREVIEW_LISTED_IN_AVOID,
    PREVIEW_MISSING_RATIONALE,
    PREVIEW_MISSING_SOURCE_NOTES,
    PREVIEW_STANCE_NOT_PREFER,
    PREVIEW_ANALYST_MEMO_ABSENT,
    PREVIEW_ANALYST_MEMO_INVALID,
)

# --- global (whole-run) blocker codes ----------------------------------------
GLOBAL_NO_ACCEPTED_SUPPORT_SIGNALS = "no_accepted_support_signals"
GLOBAL_BASE_NEW_TICKER_CAP_ZERO = "preview_base_new_ticker_cap_zero"

# Map the deterministic support-signal reason codes onto preview reason codes.
# Every anchor-grounding failure collapses to ``preview_missing_anchor`` (the
# preview does not re-litigate *why* the anchor was rejected — the granular code
# is retained per-row in ``source_rejection_reasons``).
_SUPPORT_REASON_TO_PREVIEW: dict[str, str] = {
    REASON_EXTENDED_ETF_NOT_ALLOWED: PREVIEW_EXTENDED_ETF_NOT_ALLOWED,
    REASON_OUT_OF_UNIVERSE: PREVIEW_OUT_OF_UNIVERSE,
    REASON_LISTED_IN_AVOID: PREVIEW_LISTED_IN_AVOID,
    REASON_MISSING_RATIONALE: PREVIEW_MISSING_RATIONALE,
    REASON_MISSING_SOURCE_NOTES: PREVIEW_MISSING_SOURCE_NOTES,
    REASON_BLOCKING_DATA_GAP: PREVIEW_BLOCKING_DATA_GAP,
    REASON_MEMO_CONFIDENCE_LOW: PREVIEW_LOW_CONFIDENCE,
    REASON_STANCE_NOT_PREFER: PREVIEW_STANCE_NOT_PREFER,
    REASON_ANALYST_MEMO_ABSENT: PREVIEW_ANALYST_MEMO_ABSENT,
    REASON_ANALYST_MEMO_INVALID: PREVIEW_ANALYST_MEMO_INVALID,
    REASON_MISSING_VALID_ANCHOR_SOURCE: PREVIEW_MISSING_ANCHOR,
    REASON_MISSING_ANCHOR_ID_REFS: PREVIEW_MISSING_ANCHOR,
    REASON_REFERENCED_ANCHOR_NOT_FOUND: PREVIEW_MISSING_ANCHOR,
    REASON_REFERENCED_ANCHOR_STALE: PREVIEW_MISSING_ANCHOR,
    REASON_ANCHOR_NOT_APPLICABLE: PREVIEW_MISSING_ANCHOR,
    REASON_ANCHOR_CONFIDENCE_FLOOR_NOT_MET: PREVIEW_MISSING_ANCHOR,
    REASON_ANCHOR_SOURCE_TYPE_NOT_ALLOWED: PREVIEW_MISSING_ANCHOR,
    REASON_ANCHOR_TYPE_NOT_ALLOWED: PREVIEW_MISSING_ANCHOR,
}

_NON_AUTHORIZATION_NOTE = (
    "Report-only actionable-handoff PREVIEW (R2E.5b-0). This is a SEPARATE observability "
    "artifact: it NEVER authorizes a trade, NEVER changes allowed_actions, NEVER feeds the "
    "active compiled handoff (positive_delta_research_supported stays [], no actionable_this_run "
    "row, primary_anchor_event_id stays null), and is NEVER passed into the availability "
    "evaluator or Step 2. A preview_actionable_row describes a HYPOTHETICAL future row only; "
    "actionability_status_preview and not_authorization=true make its non-authorization explicit. "
    "Promoting it into the active compiled handoff, then into the gates, each require a future "
    "explicit PR (permission_effect=none)."
)


# --- pure builder ------------------------------------------------------------


def build_actionable_handoff_preview(
    *,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    compiled_support_signals: Mapping[str, Any] | None,
    compiled_handoff_candidate: Mapping[str, Any] | None = None,
    evidence_packet_path: Any = None,
    compiled_support_signals_path: Any = None,
    compiled_handoff_candidate_path: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the deterministic, report-only actionable-handoff preview (never raises).

    The acceptance decision is inherited verbatim from the already-compiled
    ``compiled_support_signals`` (so the preview can never disagree with the
    support-signal extractor); the preview only *enriches* each accepted candidate
    with its anchor mapping and applies the preview-only weekly cap. Candidates the
    support extractor did not accept are surfaced under ``rejected_preview_rows``
    with mapped ``preview_*`` reason codes.
    """
    packet = evidence_packet if isinstance(evidence_packet, Mapping) else {}
    signals = compiled_support_signals if isinstance(compiled_support_signals, Mapping) else {}
    memo = analyst_memo if isinstance(analyst_memo, Mapping) else {}

    anchors_by_id = _anchors_by_id(packet)

    budget_settings = packet.get("budget_settings") if isinstance(packet.get("budget_settings"), Mapping) else {}
    max_new_snapshot = budget_settings.get("max_new_tickers_per_week")
    base_cap = _base_new_ticker_cap(max_new_snapshot)

    memo_confidence = memo.get("confidence")
    memo_confidence = memo_confidence.strip().lower() if isinstance(memo_confidence, str) else None

    candidate_signals = signals.get("candidate_ticker_signals")
    candidate_signals = candidate_signals if isinstance(candidate_signals, list) else []
    accepted_support_signals = signals.get("accepted_support_signals")
    accepted_support_signals = accepted_support_signals if isinstance(accepted_support_signals, list) else []

    rejected_preview_rows: list[dict[str, Any]] = []
    provisional_rows: list[dict[str, Any]] = []

    for signal in candidate_signals:
        if not isinstance(signal, Mapping):
            continue
        ticker = _ticker(signal.get("ticker"))
        if not ticker:
            continue
        source_reasons = _string_items(signal.get("rejection_reasons"))

        if signal.get("accepted_for_future_actionability") is not True:
            preview_reasons = _map_support_reasons(source_reasons) or [PREVIEW_MISSING_ANCHOR]
            rejected_preview_rows.append(
                {
                    "ticker": ticker,
                    "preview_rejection_reasons": preview_reasons,
                    "source_rejection_reasons": source_reasons,
                    "not_authorization": True,
                }
            )
            continue

        # Accepted by the support extractor: enrich with the deterministic anchor
        # mapping, then apply the preview-only structural gates.
        anchor_id = signal.get("matched_anchor_id")
        anchor = anchors_by_id.get(anchor_id) if isinstance(anchor_id, str) else None
        row, preview_reasons = _build_preview_row(
            ticker=ticker,
            signal=signal,
            anchor=anchor,
            memo_confidence=memo_confidence,
        )
        if preview_reasons:
            rejected_preview_rows.append(
                {
                    "ticker": ticker,
                    "preview_rejection_reasons": preview_reasons,
                    "source_rejection_reasons": source_reasons,
                    "not_authorization": True,
                }
            )
            continue
        provisional_rows.append(row)

    # Preview-only weekly cap (deterministic: preserve support-signal order, which
    # follows the memo's ticker_relative_view order). The extended ETF sleeve is
    # never previewed, so the base-universe cap is the only one that applies.
    preview_actionable_rows: list[dict[str, Any]] = []
    for index, row in enumerate(provisional_rows):
        if index < base_cap:
            preview_actionable_rows.append(row)
        else:
            rejected_preview_rows.append(
                {
                    "ticker": row["ticker"],
                    "preview_rejection_reasons": [PREVIEW_LIMIT_MAX_NEW_TICKERS_EXCEEDED],
                    "source_rejection_reasons": [],
                    "not_authorization": True,
                }
            )

    preview_positive_delta = [row["ticker"] for row in preview_actionable_rows]

    global_blockers: list[str] = []
    if not accepted_support_signals:
        global_blockers.append(GLOBAL_NO_ACCEPTED_SUPPORT_SIGNALS)
    if base_cap == 0:
        global_blockers.append(GLOBAL_BASE_NEW_TICKER_CAP_ZERO)
    # Carry the support-signal global blockers through verbatim so the preview is
    # self-explanatory (they explain why nothing could be accepted upstream).
    for code in _string_items(signals.get("global_blockers")):
        if code not in global_blockers:
            global_blockers.append(code)

    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "generated_at": generated_at,
        "source_compiled_support_signals": _source_ref(compiled_support_signals, compiled_support_signals_path),
        "source_compiled_handoff_candidate": _source_ref(compiled_handoff_candidate, compiled_handoff_candidate_path),
        "source_evidence_packet": _source_ref(evidence_packet, evidence_packet_path),
        "max_new_tickers_per_week_snapshot": max_new_snapshot,
        "base_new_ticker_cap_applied": base_cap,
        "extended_etf_sleeve_preview_enabled": EXTENDED_ETF_SLEEVE_PREVIEW_ENABLED,
        "preview_actionable_rows": preview_actionable_rows,
        "preview_positive_delta_research_supported": preview_positive_delta,
        "rejected_preview_rows": rejected_preview_rows,
        "global_blockers": global_blockers,
        "notes": _NON_AUTHORIZATION_NOTE,
    }


def write_actionable_handoff_preview(
    *,
    output_path: Any,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    compiled_support_signals: Mapping[str, Any] | None,
    compiled_handoff_candidate: Mapping[str, Any] | None = None,
    evidence_packet_path: Any = None,
    compiled_support_signals_path: Any = None,
    compiled_handoff_candidate_path: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build + write the report-only preview artifact; return a small summary dict."""
    from investment_orchestrator.common.io import write_json

    preview = build_actionable_handoff_preview(
        evidence_packet=evidence_packet,
        analyst_memo=analyst_memo,
        compiled_support_signals=compiled_support_signals,
        compiled_handoff_candidate=compiled_handoff_candidate,
        evidence_packet_path=evidence_packet_path,
        compiled_support_signals_path=compiled_support_signals_path,
        compiled_handoff_candidate_path=compiled_handoff_candidate_path,
        generated_at=generated_at,
    )
    write_json(output_path, preview)
    return {
        "actionable_handoff_preview_path": str(output_path),
        "preview_actionable_row_count": str(len(preview["preview_actionable_rows"])),
        "preview_positive_delta_research_supported": list(
            preview["preview_positive_delta_research_supported"]
        ),
    }


# --- row construction --------------------------------------------------------


def _build_preview_row(
    *,
    ticker: str,
    signal: Mapping[str, Any],
    anchor: Mapping[str, Any] | None,
    memo_confidence: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """Return (preview_row, preview_reasons) for a support-accepted candidate.

    ``preview_reasons`` is non-empty only when a preview-only structural gate fails
    (missing event/theme ref or a scheduled-event anchor with no date); in that
    case the row must NOT enter ``preview_actionable_rows``.
    """
    anchor = anchor if isinstance(anchor, Mapping) else {}
    anchor_id = signal.get("matched_anchor_id")
    anchor_type = signal.get("anchor_source_type")
    anchor_date_et = anchor.get("anchor_date_et")
    anchor_confidence_floor = anchor.get("confidence_floor")

    is_scheduled_event = anchor_type in _SCHEDULED_EVENT_ANCHOR_TYPES
    is_structural = anchor_type == _STRUCTURAL_ANCHOR_TYPE

    ref = anchor_id if isinstance(anchor_id, str) and anchor_id.strip() else None
    structural_theme_refs = [ref] if (is_structural and ref) else []
    event_id_refs = [ref] if (is_scheduled_event and ref) else []
    primary_anchor_event_id = ref if is_scheduled_event else None
    primary_anchor_date_et = (
        anchor_date_et if isinstance(anchor_date_et, str) and anchor_date_et.strip() else None
    )

    reasons: list[str] = []
    if not structural_theme_refs and not event_id_refs:
        reasons.append(PREVIEW_MISSING_EVENT_OR_THEME_REF)
    if is_scheduled_event and primary_anchor_date_et is None:
        reasons.append(PREVIEW_MISSING_PRIMARY_ANCHOR_DATE)

    row = {
        "ticker": ticker,
        "source_anchor_id": anchor_id,
        "anchor_type": anchor_type,
        "primary_anchor_event_id": primary_anchor_event_id,
        "primary_anchor_ref": ref,
        "primary_anchor_date_et": primary_anchor_date_et,
        "structural_theme_refs": structural_theme_refs,
        "event_id_refs": event_id_refs,
        "thesis_12m_plus_supported_preview": True,
        "thesis_linkage_quality_preview": _thesis_linkage_quality(memo_confidence, anchor_confidence_floor),
        "actionability_status_preview": ACTIONABILITY_STATUS_PREVIEW,
        "not_authorization": True,
    }
    return row, reasons


def _thesis_linkage_quality(memo_confidence: str | None, anchor_confidence_floor: Any) -> str:
    """Deterministic linkage-quality label from memo confidence vs anchor floor."""
    memo_rank = _CONFIDENCE_RANK.get(memo_confidence) if isinstance(memo_confidence, str) else None
    floor_rank = _CONFIDENCE_RANK.get(anchor_confidence_floor) if isinstance(anchor_confidence_floor, str) else None
    if memo_rank is None or floor_rank is None:
        return "adequate"
    return "strong" if memo_rank > floor_rank else "adequate"


def _map_support_reasons(reasons: list[str]) -> list[str]:
    out: list[str] = []
    for code in reasons:
        mapped = _SUPPORT_REASON_TO_PREVIEW.get(code)
        if mapped is not None and mapped not in out:
            out.append(mapped)
    return out


# --- helpers -----------------------------------------------------------------


def _anchors_by_id(packet: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Index the evidence packet's deterministic research anchors by anchor_id."""
    research_anchors = packet.get("research_anchors")
    if not isinstance(research_anchors, Mapping) or research_anchors.get("available") is not True:
        return {}
    anchors = research_anchors.get("anchors")
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


def _base_new_ticker_cap(snapshot: Any) -> int:
    """Resolve the base-universe new-ticker cap (fail closed to 0).

    Accepts either the structured ``max_new_tickers_per_week`` mapping (reads
    ``base_universe_new_tickers_per_week``) or a plain integer scalar. Missing /
    negative / malformed → 0 (the preview never implies more new buys than policy).
    """
    if isinstance(snapshot, bool):
        return 0
    if isinstance(snapshot, int):
        return snapshot if snapshot >= 0 else 0
    if isinstance(snapshot, Mapping):
        value = snapshot.get("base_universe_new_tickers_per_week")
        if isinstance(value, bool):
            return 0
        if isinstance(value, int) and value >= 0:
            return value
    return 0


def _source_ref(value: Any, path: Any) -> dict[str, Any]:
    return {
        "path": str(path) if path is not None else None,
        "schema_version": value.get("schema_version") if isinstance(value, Mapping) else None,
        "sha256": _sha256_of(value) if isinstance(value, Mapping) else None,
    }


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _string_items(value: Any) -> list[str]:
    """De-duped list of non-empty strings, order preserved."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str) and item.strip():
            code = item.strip()
            if code not in seen:
                seen.add(code)
                out.append(code)
    return out


def _ticker(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()
