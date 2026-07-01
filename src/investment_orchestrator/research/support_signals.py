"""Step 1C deterministic support-signal extraction (R2E.3, report-only).

Extracts a **deterministic, non-authoritative** view of which analyst-memo
opinions *would* be buy-support candidates for a *future* actionable
evidence+memo path (``STRICT_FRESH_WITH_LLM_MEMO``), together with the exact
deterministic reason each candidate is currently rejected.

This module changes **no** production behavior. It never authorizes a trade,
never changes ``allowed_actions``, never feeds the availability / degraded-mode
decision, and never makes the compiled handoff actionable. In R2E.3:

* ``accepted_support_signals`` is **always empty** — no candidate can be accepted
  for actionability because no deterministic anchor source exists yet (the
  ``missing_valid_anchor_source`` global blocker always applies). Candidates that
  pass every *qualitative* gate are surfaced under ``qualitative_support_only``
  (clearly named so it is never mistaken for buy authorization).
* Every candidate carries ``accepted_for_future_actionability: false`` and
  ``has_valid_anchor_source: false``.

The core ``build_compiled_support_signals`` is a pure function (mappings in,
mapping out; never raises) so it is fully testable without disk.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SCHEMA_VERSION = "compiled_support_signals_v1"

# Compilation-mode literals mirrored from the compiler (kept as literals to avoid
# a circular import; the compiler passes the mode it computed).
_MODE_EVIDENCE_ONLY = "evidence_only"
_MODE_EVIDENCE_PLUS_MEMO = "evidence_plus_memo"
_MODE_INVALID_MEMO_IGNORED = "invalid_memo_ignored"

# A candidate is only ever a buy-support candidate when the memo prefers it.
_PREFER_STANCE = "prefer"
_LOW_CONFIDENCE = "low"

# --- deterministic rejection reason codes ------------------------------------
# Global (whole-run) blockers.
REASON_MISSING_VALID_ANCHOR_SOURCE = "missing_valid_anchor_source"
REASON_MEMO_CONFIDENCE_LOW = "memo_confidence_low"
REASON_ANALYST_MEMO_ABSENT = "analyst_memo_absent"
REASON_ANALYST_MEMO_INVALID = "analyst_memo_invalid"
# Per-ticker rejection reasons.
REASON_OUT_OF_UNIVERSE = "out_of_universe"
REASON_LISTED_IN_AVOID = "listed_in_avoid_or_deprioritize"
REASON_MISSING_RATIONALE = "missing_rationale"
REASON_MISSING_SOURCE_NOTES = "missing_source_notes"
REASON_BLOCKING_DATA_GAP = "blocking_data_gap"
REASON_EXTENDED_ETF_NOT_ALLOWED = "extended_etf_not_allowed_in_v1"
REASON_STANCE_NOT_PREFER = "stance_not_prefer"

REJECTION_REASON_CODES = (
    REASON_MISSING_VALID_ANCHOR_SOURCE,
    REASON_MEMO_CONFIDENCE_LOW,
    REASON_OUT_OF_UNIVERSE,
    REASON_LISTED_IN_AVOID,
    REASON_MISSING_RATIONALE,
    REASON_MISSING_SOURCE_NOTES,
    REASON_BLOCKING_DATA_GAP,
    REASON_EXTENDED_ETF_NOT_ALLOWED,
    REASON_STANCE_NOT_PREFER,
    REASON_ANALYST_MEMO_ABSENT,
    REASON_ANALYST_MEMO_INVALID,
)

# In R2E.3 no deterministic anchor source is wired in, so every candidate is
# non-actionable by construction.
ANCHOR_SOURCE_NONE = "none_available"

_NON_AUTHORIZATION_NOTE = (
    "Report-only support signals. This artifact NEVER authorizes a trade, never "
    "changes allowed_actions, never enables STRICT_FRESH_WITH_LLM_MEMO, and does "
    "not feed the availability / degraded-mode decision. accepted_support_signals "
    "is empty in this version because no deterministic anchor source exists."
)


# --- pure builder ------------------------------------------------------------


def build_compiled_support_signals(
    *,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    compilation_mode: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the deterministic, report-only support-signal artifact (never raises).

    ``analyst_memo`` is the *raw* memo as handed to the compiler (may be absent /
    invalid). ``compilation_mode`` is the mode the compiler already computed
    (``evidence_only`` / ``evidence_plus_memo`` / ``invalid_memo_ignored``); it is
    the single source of truth for present/valid so this artifact can never
    disagree with the compiler.
    """
    packet = evidence_packet if isinstance(evidence_packet, Mapping) else {}
    universe = packet.get("universe") if isinstance(packet.get("universe"), Mapping) else {}
    allowed_buy = _normalized_set(universe.get("allowed_buy_tickers"))
    approved_extended = _normalized_set(universe.get("approved_extended_etf"))

    analyst_memo_present = analyst_memo is not None
    analyst_memo_valid = compilation_mode == _MODE_EVIDENCE_PLUS_MEMO

    memo = analyst_memo if isinstance(analyst_memo, Mapping) else {}
    confidence = memo.get("confidence")
    confidence_str = confidence.strip().lower() if isinstance(confidence, str) else None
    confidence_low = analyst_memo_valid and confidence_str == _LOW_CONFIDENCE
    avoid_set = _normalized_set(memo.get("avoid_or_deprioritize"))
    data_gaps = _string_items(memo.get("data_gaps"))
    source_notes_present = _has_source_notes(memo.get("source_notes"))

    # --- global blockers (whole run) ---
    global_blockers: list[str] = []
    if not analyst_memo_present:
        global_blockers.append(REASON_ANALYST_MEMO_ABSENT)
    elif not analyst_memo_valid:
        global_blockers.append(REASON_ANALYST_MEMO_INVALID)
    if confidence_low:
        global_blockers.append(REASON_MEMO_CONFIDENCE_LOW)
    # R2E.3 invariant: no deterministic anchor source exists yet.
    global_blockers.append(REASON_MISSING_VALID_ANCHOR_SOURCE)

    # --- per-ticker candidate signals (defensive; works on raw/invalid memo) ---
    candidate_ticker_signals: list[dict[str, Any]] = []
    rejected_support_signals: list[dict[str, Any]] = []
    qualitative_support_only: list[dict[str, Any]] = []

    for row in _ticker_rows(memo.get("ticker_relative_view")):
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            continue
        stance = row.get("stance")
        stance_str = stance.strip().lower() if isinstance(stance, str) else None
        rationale = row.get("rationale_12m_plus")
        rationale_present = isinstance(rationale, str) and rationale.strip() != ""

        in_allowed_universe = ticker in allowed_buy
        is_extended = ticker in approved_extended
        listed_in_avoid = ticker in avoid_set
        has_blocking_data_gap = _ticker_mentioned(ticker, data_gaps)

        reasons: list[str] = []
        if stance_str != _PREFER_STANCE:
            reasons.append(REASON_STANCE_NOT_PREFER)
        if not rationale_present:
            reasons.append(REASON_MISSING_RATIONALE)
        if not source_notes_present:
            reasons.append(REASON_MISSING_SOURCE_NOTES)
        # Universe classification: recognized-but-extended vs genuinely out-of-universe.
        if is_extended:
            reasons.append(REASON_EXTENDED_ETF_NOT_ALLOWED)
        elif not in_allowed_universe:
            reasons.append(REASON_OUT_OF_UNIVERSE)
        if listed_in_avoid:
            reasons.append(REASON_LISTED_IN_AVOID)
        if has_blocking_data_gap:
            reasons.append(REASON_BLOCKING_DATA_GAP)
        # Global reasons apply to every candidate too (self-contained rows).
        if confidence_low:
            reasons.append(REASON_MEMO_CONFIDENCE_LOW)
        if not analyst_memo_valid:
            # An invalid / absent memo is never trusted for support.
            reasons.append(
                REASON_ANALYST_MEMO_ABSENT if not analyst_memo_present else REASON_ANALYST_MEMO_INVALID
            )
        reasons.append(REASON_MISSING_VALID_ANCHOR_SOURCE)

        signal = {
            "ticker": ticker,
            "stance": stance_str,
            "confidence": confidence_str,
            "rationale_present": rationale_present,
            "source_notes_present": source_notes_present,
            "in_allowed_universe": in_allowed_universe,
            "listed_in_avoid_or_deprioritize": listed_in_avoid,
            "has_blocking_data_gap": has_blocking_data_gap,
            "has_valid_anchor_source": False,
            "anchor_source_type": ANCHOR_SOURCE_NONE,
            "accepted_for_future_actionability": False,
            "rejection_reasons": reasons,
        }
        candidate_ticker_signals.append(signal)

        # A candidate blocked *only* by the missing anchor source has passed every
        # qualitative gate — surface it as qualitative-support-only (NOT accepted).
        if reasons == [REASON_MISSING_VALID_ANCHOR_SOURCE]:
            qualitative_support_only.append({"ticker": ticker, "stance": stance_str})
        rejected_support_signals.append({"ticker": ticker, "rejection_reasons": reasons})

    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "generated_at": generated_at,
        "analyst_memo_present": analyst_memo_present,
        "analyst_memo_valid": analyst_memo_valid,
        "compilation_mode": compilation_mode,
        "anchor_source_available": False,
        "actionable_signals_possible": False,
        "candidate_ticker_signals": candidate_ticker_signals,
        # Always empty in R2E.3: acceptance-for-actionability requires an anchor source.
        "accepted_support_signals": [],
        "qualitative_support_only": qualitative_support_only,
        "rejected_support_signals": rejected_support_signals,
        "global_blockers": global_blockers,
        "notes": _NON_AUTHORIZATION_NOTE,
    }


# --- helpers -----------------------------------------------------------------


def _ticker_rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _normalized_set(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, list):
        for item in value:
            ticker = _ticker(item)
            if ticker:
                out.add(ticker)
    return out


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _has_source_notes(value: Any) -> bool:
    """True when the memo carries at least one non-empty source-note entry."""
    if not isinstance(value, list):
        return False
    for item in value:
        if isinstance(item, Mapping) and any(
            isinstance(v, str) and v.strip() for v in item.values()
        ):
            return True
        if isinstance(item, str) and item.strip():
            return True
    return False


def _ticker_mentioned(ticker: str, data_gaps: list[str]) -> bool:
    """Heuristic: a data_gap that names the ticker is treated as ticker-blocking."""
    needle = ticker.upper()
    for gap in data_gaps:
        if needle in gap.upper():
            return True
    return False


def _ticker(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()
