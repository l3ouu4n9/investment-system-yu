"""Step 1C deterministic support-signal extraction (R2E.3 / R2E.5a-2, report-only).

Extracts a **deterministic, non-authoritative** view of which analyst-memo
opinions *would* be buy-support candidates for a *future* actionable
evidence+memo path (``STRICT_FRESH_WITH_LLM_MEMO``), together with the exact
deterministic reason each candidate is currently accepted or rejected.

This module changes **no** production behavior. It never authorizes a trade,
never changes ``allowed_actions``, never feeds the availability / degraded-mode
decision, and never makes the compiled handoff actionable.

R2E.5a-2: a candidate may enter ``accepted_support_signals`` when a *valid* memo
references a *valid, fresh, applicable* deterministic research anchor and meets
every qualitative gate. Acceptance is **still report-only and NOT authorization**
(``permission_effect: "none"``, ``not_authorization: true``). Candidates that pass
every qualitative gate but lack valid anchor grounding are surfaced under
``qualitative_support_only``.

R2G-3: the grounding-relevant anchor view is the **active anchor registry**
consumed directly from ``evidence_packet.active_anchor_registry`` (the R2G-1
registry embedded upstream by the evidence-packet builder — the same compiler over
the same operator source as ``active_research_anchor_registry.json``). Grounding is
**no longer** derived from the legacy ``evidence_packet.research_anchors`` view.
This is a **safe tightening**, never a broadening (proven by the R2G-2 equivalence
oracle / R2G-2.1 readiness corpus):

* the embedded registry must be consumable — expected ``schema_version``,
  ``is_llm_generated: false``, ``report_only: true``, ``permission_effect: "none"``,
  ``not_authorization: true``, and ``registry_valid: true`` — else no usable anchors;
* happy/valid anchors ground exactly as before;
* a **missing / malformed / invalid** registry (incl. a file-level integrity
  failure such as ``is_llm_generated: true``, duplicate ``anchor_id``, or a
  forbidden budget/order/action key that flips ``registry_valid`` to false) yields
  **zero usable anchors** → the memo ref is rejected (``missing_valid_anchor_source``)
  where the legacy per-anchor view might have accepted a structurally-valid row;
* only registry ``active`` anchors can ground; ``expired`` resolve to
  ``referenced_anchor_stale`` and everything else to ``referenced_anchor_not_found``.

No permission, gate, availability, Step 2/3/4, or order behavior changes: this is
still report-only and never authorizes a trade.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from investment_orchestrator.research.active_research_anchor_registry import (
    SCHEMA_VERSION as ACTIVE_REGISTRY_SCHEMA_VERSION,
    STATUS_ACTIVE,
    STATUS_EXPIRED,
)
from investment_orchestrator.research.research_anchors import (
    ANCHOR_TYPES,
    SOURCE_TYPES,
)


SCHEMA_VERSION = "compiled_support_signals_v1"

# Compilation-mode literals mirrored from the compiler (kept as literals to avoid
# a circular import; the compiler passes the mode it computed).
_MODE_EVIDENCE_ONLY = "evidence_only"
_MODE_EVIDENCE_PLUS_MEMO = "evidence_plus_memo"
_MODE_INVALID_MEMO_IGNORED = "invalid_memo_ignored"

# A candidate is only ever a buy-support candidate when the memo prefers it.
_PREFER_STANCE = "prefer"
_LOW_CONFIDENCE = "low"
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

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
# Per-ticker anchor-grounding reasons (R2E.5a-2).
REASON_MISSING_ANCHOR_ID_REFS = "missing_anchor_id_refs"
REASON_REFERENCED_ANCHOR_NOT_FOUND = "referenced_anchor_not_found"
REASON_REFERENCED_ANCHOR_STALE = "referenced_anchor_stale"
REASON_ANCHOR_NOT_APPLICABLE = "anchor_not_applicable_to_ticker"
REASON_ANCHOR_CONFIDENCE_FLOOR_NOT_MET = "anchor_confidence_floor_not_met"
REASON_ANCHOR_SOURCE_TYPE_NOT_ALLOWED = "anchor_source_type_not_allowed"
REASON_ANCHOR_TYPE_NOT_ALLOWED = "anchor_type_not_allowed"

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
    REASON_MISSING_ANCHOR_ID_REFS,
    REASON_REFERENCED_ANCHOR_NOT_FOUND,
    REASON_REFERENCED_ANCHOR_STALE,
    REASON_ANCHOR_NOT_APPLICABLE,
    REASON_ANCHOR_CONFIDENCE_FLOOR_NOT_MET,
    REASON_ANCHOR_SOURCE_TYPE_NOT_ALLOWED,
    REASON_ANCHOR_TYPE_NOT_ALLOWED,
)

# Anchor-grounding reasons: a candidate rejected *only* by these has passed every
# qualitative gate and is surfaced as qualitative_support_only (not authorization).
_ANCHOR_RELATED_REASONS = frozenset(
    {
        REASON_MISSING_VALID_ANCHOR_SOURCE,
        REASON_MISSING_ANCHOR_ID_REFS,
        REASON_REFERENCED_ANCHOR_NOT_FOUND,
        REASON_REFERENCED_ANCHOR_STALE,
        REASON_ANCHOR_NOT_APPLICABLE,
        REASON_ANCHOR_CONFIDENCE_FLOOR_NOT_MET,
        REASON_ANCHOR_SOURCE_TYPE_NOT_ALLOWED,
        REASON_ANCHOR_TYPE_NOT_ALLOWED,
    }
)

# No accepted anchor grounding for a candidate.
ANCHOR_SOURCE_NONE = "none_available"

_NON_AUTHORIZATION_NOTE = (
    "Report-only support signals. This artifact NEVER authorizes a trade, never "
    "changes allowed_actions, never enables STRICT_FRESH_WITH_LLM_MEMO, and does "
    "not feed the availability / degraded-mode decision. accepted_support_signals "
    "means only that a candidate has valid deterministic anchor grounding for a "
    "FUTURE actionable path; it is NOT buy authorization (permission_effect=none)."
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

    anchors_by_id = _registry_backed_anchors_by_id(packet)
    any_usable_anchor = any(
        entry["valid"] and entry["usable"] and not entry["stale"]
        for entry in anchors_by_id.values()
    )

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
    # Whole-run anchor blocker only when NO usable deterministic anchor exists.
    if not any_usable_anchor:
        global_blockers.append(REASON_MISSING_VALID_ANCHOR_SOURCE)

    # --- per-ticker candidate signals (defensive; works on raw/invalid memo) ---
    candidate_ticker_signals: list[dict[str, Any]] = []
    accepted_support_signals: list[dict[str, Any]] = []
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
        anchor_id_refs = _string_refs(row.get("anchor_id_refs"))

        in_allowed_universe = ticker in allowed_buy
        is_extended = ticker in approved_extended
        listed_in_avoid = ticker in avoid_set
        has_blocking_data_gap = _ticker_mentioned(ticker, data_gaps)

        # Non-anchor (qualitative + global) gates.
        non_anchor_reasons: list[str] = []
        if stance_str != _PREFER_STANCE:
            non_anchor_reasons.append(REASON_STANCE_NOT_PREFER)
        if not rationale_present:
            non_anchor_reasons.append(REASON_MISSING_RATIONALE)
        if not source_notes_present:
            non_anchor_reasons.append(REASON_MISSING_SOURCE_NOTES)
        if is_extended:
            non_anchor_reasons.append(REASON_EXTENDED_ETF_NOT_ALLOWED)
        elif not in_allowed_universe:
            non_anchor_reasons.append(REASON_OUT_OF_UNIVERSE)
        if listed_in_avoid:
            non_anchor_reasons.append(REASON_LISTED_IN_AVOID)
        if has_blocking_data_gap:
            non_anchor_reasons.append(REASON_BLOCKING_DATA_GAP)
        if confidence_low:
            non_anchor_reasons.append(REASON_MEMO_CONFIDENCE_LOW)
        if not analyst_memo_valid:
            non_anchor_reasons.append(
                REASON_ANALYST_MEMO_ABSENT if not analyst_memo_present else REASON_ANALYST_MEMO_INVALID
            )

        # Anchor-grounding gate (R2E.5a-2): find the first fully-valid referenced anchor.
        matched_anchor_id, anchor_reasons, matched_anchor_type = _evaluate_anchor_refs(
            anchor_id_refs, ticker=ticker, anchors_by_id=anchors_by_id, memo_confidence=confidence_str
        )
        has_valid_anchor = matched_anchor_id is not None

        # Accept only when every qualitative/global gate passes AND a valid anchor grounds it.
        accepted = not non_anchor_reasons and has_valid_anchor
        reasons = list(non_anchor_reasons) if accepted else [*non_anchor_reasons, *anchor_reasons]

        signal = {
            "ticker": ticker,
            "stance": stance_str,
            "confidence": confidence_str,
            "rationale_present": rationale_present,
            "source_notes_present": source_notes_present,
            "in_allowed_universe": in_allowed_universe,
            "listed_in_avoid_or_deprioritize": listed_in_avoid,
            "has_blocking_data_gap": has_blocking_data_gap,
            "anchor_id_refs": anchor_id_refs,
            "matched_anchor_id": matched_anchor_id,
            "has_valid_anchor_source": has_valid_anchor,
            "anchor_source_type": matched_anchor_type if has_valid_anchor else ANCHOR_SOURCE_NONE,
            # "for a FUTURE actionable path" — never a live authorization (see notes).
            "accepted_for_future_actionability": accepted,
            "rejection_reasons": [] if accepted else reasons,
        }
        candidate_ticker_signals.append(signal)

        if accepted:
            accepted_support_signals.append(
                {
                    "ticker": ticker,
                    "stance": stance_str,
                    "anchor_id": matched_anchor_id,
                    "anchor_type": matched_anchor_type,
                    "not_authorization": True,
                }
            )
        elif not non_anchor_reasons:
            # Passed every qualitative gate but lacks valid anchor grounding.
            qualitative_support_only.append(
                {"ticker": ticker, "stance": stance_str, "anchor_gap_reasons": anchor_reasons}
            )
        else:
            rejected_support_signals.append({"ticker": ticker, "rejection_reasons": reasons})

    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        # Unambiguous: acceptance here is grounding for a FUTURE path, not authorization.
        "not_authorization": True,
        "generated_at": generated_at,
        "analyst_memo_present": analyst_memo_present,
        "analyst_memo_valid": analyst_memo_valid,
        "compilation_mode": compilation_mode,
        "anchor_source_available": any_usable_anchor,
        # Diagnostic only (paired with not_authorization/permission_effect=none): a
        # future actionable path could ground these accepted signals.
        "actionable_signals_possible": bool(accepted_support_signals),
        "candidate_ticker_signals": candidate_ticker_signals,
        "accepted_support_signals": accepted_support_signals,
        "qualitative_support_only": qualitative_support_only,
        "rejected_support_signals": rejected_support_signals,
        "global_blockers": global_blockers,
        "notes": _NON_AUTHORIZATION_NOTE,
    }


# --- anchor grounding --------------------------------------------------------


def _registry_backed_anchors_by_id(packet: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Index the embedded **active anchor registry** by anchor_id (R2G-3).

    Consumes ``evidence_packet.active_anchor_registry`` (the R2G-1 registry embedded
    upstream) as the authoritative grounding source. Every registry anchor (active +
    inactive) is indexed and projected onto exactly the fields
    ``_evaluate_anchor_refs`` reads, with usability derived from the registry's
    authoritative ``status``:

    * ``active``  -> valid + usable + not stale (groundable);
    * ``expired`` -> valid + stale (resolves to ``referenced_anchor_stale``);
    * anything else (``invalid`` …) -> not valid (``referenced_anchor_not_found``).

    Fails closed to an empty map when the registry is missing / malformed / invalid
    (``registry_valid`` not true, wrong schema, or a missing report-only marker) —
    including any file-level integrity failure, which flips ``registry_valid`` to
    false. Never raises. Never broadens vs the legacy view.
    """
    registry = packet.get("active_anchor_registry")
    if not _registry_is_consumable(registry):
        return {}
    by_id: dict[str, Mapping[str, Any]] = {}
    for anchor in (
        *_as_list(registry.get("active_anchors")),
        *_as_list(registry.get("inactive_anchors")),
    ):
        if not isinstance(anchor, Mapping):
            continue
        anchor_id = anchor.get("anchor_id")
        if not (isinstance(anchor_id, str) and anchor_id.strip()):
            continue
        # Last occurrence wins (stable, deterministic).
        by_id[anchor_id.strip()] = _projected_acceptance_fields(anchor)
    return by_id


def _registry_is_consumable(registry: Any) -> bool:
    """Fail-closed gate: the embedded registry must be a valid, report-only registry.

    Anything else (absent, malformed, wrong schema, missing markers, or
    ``registry_valid`` not true — which covers every file-level integrity failure)
    yields no usable anchors.
    """
    return (
        isinstance(registry, Mapping)
        and registry.get("schema_version") == ACTIVE_REGISTRY_SCHEMA_VERSION
        and registry.get("is_llm_generated") is False
        and registry.get("report_only") is True
        and registry.get("permission_effect") == "none"
        and registry.get("not_authorization") is True
        and registry.get("registry_valid") is True
    )


def _projected_acceptance_fields(anchor: Mapping[str, Any]) -> dict[str, Any]:
    """Project a registry anchor onto the fields ``_evaluate_anchor_refs`` reads.

    Usability (valid / stale / usable) is derived from the registry's authoritative
    ``status`` — so grounding can never be more permissive than the registry's active
    set. The remaining fields are the registry anchor's values so the operator-source
    / type / applicability / confidence checks still fire.
    """
    status = anchor.get("status")
    if status == STATUS_ACTIVE:
        valid, stale, usable = True, False, True
    elif status == STATUS_EXPIRED:
        valid, stale, usable = True, True, False
    else:  # invalid / revoked / superseded / unknown -> not groundable
        valid, stale, usable = False, False, False
    return {
        "valid": valid,
        "stale": stale,
        "usable": usable,
        "anchor_type": anchor.get("anchor_type"),
        "source_type": anchor.get("source_type"),
        "applicable_tickers": list(anchor.get("applicable_tickers") or []),
        "confidence_floor": anchor.get("confidence_floor"),
    }


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _evaluate_anchor_refs(
    refs: list[str],
    *,
    ticker: str,
    anchors_by_id: dict[str, Mapping[str, Any]],
    memo_confidence: str | None,
) -> tuple[str | None, list[str], str | None]:
    """Return (matched_anchor_id, reasons, anchor_type) for a candidate's anchor refs.

    Deterministic and defensive. Accepts the first referenced anchor that is
    present + valid + fresh/usable + operator-sourced + type-allowed + applicable
    to the ticker + confidence-floor-met. Otherwise returns the specific per-ref
    reasons plus the umbrella ``missing_valid_anchor_source``.
    """
    if not refs:
        return None, [REASON_MISSING_ANCHOR_ID_REFS, REASON_MISSING_VALID_ANCHOR_SOURCE], None

    reasons: list[str] = []
    for ref in refs:
        anchor = anchors_by_id.get(ref)
        if anchor is None or anchor.get("valid") is not True:
            _add(reasons, REASON_REFERENCED_ANCHOR_NOT_FOUND)
            continue
        if anchor.get("stale") is True or anchor.get("usable") is not True:
            _add(reasons, REASON_REFERENCED_ANCHOR_STALE)
            continue
        if anchor.get("anchor_type") not in ANCHOR_TYPES:
            _add(reasons, REASON_ANCHOR_TYPE_NOT_ALLOWED)
            continue
        if anchor.get("source_type") not in SOURCE_TYPES:
            _add(reasons, REASON_ANCHOR_SOURCE_TYPE_NOT_ALLOWED)
            continue
        if ticker not in _normalized_set(anchor.get("applicable_tickers")):
            _add(reasons, REASON_ANCHOR_NOT_APPLICABLE)
            continue
        if not _confidence_meets_floor(memo_confidence, anchor.get("confidence_floor")):
            _add(reasons, REASON_ANCHOR_CONFIDENCE_FLOOR_NOT_MET)
            continue
        return ref, [], anchor.get("anchor_type")  # first fully-valid grounding anchor

    _add(reasons, REASON_MISSING_VALID_ANCHOR_SOURCE)
    return None, reasons, None


def _confidence_meets_floor(memo_confidence: str | None, floor: Any) -> bool:
    memo_rank = _CONFIDENCE_RANK.get(memo_confidence) if isinstance(memo_confidence, str) else None
    floor_rank = _CONFIDENCE_RANK.get(floor) if isinstance(floor, str) else None
    if memo_rank is None or floor_rank is None:
        return False
    return memo_rank >= floor_rank


def _string_refs(value: Any) -> list[str]:
    """Normalize a ticker row's anchor_id_refs to a de-duped list of non-empty strings."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str) and item.strip():
            ref = item.strip()
            if ref not in seen:
                seen.add(ref)
                out.append(ref)
    return out


def _add(reasons: list[str], code: str) -> None:
    if code not in reasons:
        reasons.append(code)


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
