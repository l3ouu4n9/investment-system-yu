"""R2G-4: report-only research-anchor CANDIDATE workflow (advisory, inert).

Builds ``research_anchor_candidates.json`` — a deterministic, strictly report-only
artifact that suggests anchors an operator *might* want to author, so the human
can see what grounding is missing. It is **consumed by nothing**: not
``support_signals``, not the active registry, not the actionable preview /
candidate / promotion eligibility, not availability, not Step 2/3/4, not the final
gate, not weekly, not broker/live. A candidate can never become active.

Candidates are derived only from already-existing deterministic / report inputs
(analyst memo prefer-rows, the active-registry coverage, and — for enrichment —
the support-signal gap diagnostics). No web/news, no Deep Research, no LLM
authority: the analyst memo is treated as an *opinion source for a suggestion*,
never as an anchor. Every candidate is inert by construction:

* ``source_category: "B_candidate_only"`` — LLM/opinion-derived, never auto-active;
* the ``proposed_anchor`` carries only citation-target fields (ticker, type, dates,
  confidence_floor, summary) — never budget / order / allocation / action /
  execution fields (defensively rejected if present);
* ``status`` is ``candidate`` (or ``duplicate_of_active`` when the active registry
  already covers the ticker) — never ``active``;
* activation requires a future explicit operator-approval workflow (R2G-5), which
  this PR does not add.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from investment_orchestrator.research.research_anchors import (
    FORBIDDEN_ACTION_VALUE_TOKENS,
    FORBIDDEN_KEY_SUBSTRINGS,
    FORBIDDEN_KEYS,
    validate_research_anchors,
)


SCHEMA_VERSION = "research_anchor_candidates_v1"

SOURCE_CATEGORY_CANDIDATE_ONLY = "B_candidate_only"
SOURCE_TYPE_ANALYST_MEMO = "analyst_memo"

STATUS_CANDIDATE = "candidate"
STATUS_DUPLICATE_OF_ACTIVE = "duplicate_of_active"
STATUS_REJECTED = "rejected"

# Blocker reason codes (why a candidate is not — and cannot become — active here).
BLOCKER_REQUIRES_OPERATOR_APPROVAL = "requires_operator_authoring_and_approval"
BLOCKER_SOURCE_B_NEVER_AUTO_ACTIVATES = "source_category_b_never_auto_activates"
BLOCKER_INCOMPLETE_ANCHOR_SHAPE = "incomplete_anchor_shape_needs_operator"
BLOCKER_DUPLICATE_OF_ACTIVE = "duplicate_of_active_anchor"

# Rejection reason codes (a suggestion that must not even be surfaced as approvable).
REJECT_OUT_OF_UNIVERSE = "out_of_universe"
REJECT_FORBIDDEN_KEY = "forbidden_key_present"
REJECT_FORBIDDEN_ACTION_TOKEN = "forbidden_action_token_present"
REJECT_NO_TICKER = "missing_or_empty_ticker"

# Global candidate-generation blockers (whole-run).
GEN_BLOCKER_MEMO_ABSENT = "analyst_memo_absent"
GEN_BLOCKER_MEMO_INVALID = "analyst_memo_invalid"
GEN_BLOCKER_NO_UNIVERSE = "no_allowed_buy_universe"

_PREFER_STANCE = "prefer"
_DEFAULT_ANCHOR_TYPE = "structural_theme"
_CONFIDENCE_VALUES = ("low", "medium", "high")

_NOTES = (
    "Report-only research-anchor candidates (R2G-4). Advisory suggestions for human "
    "review ONLY. Consumed by nothing (support_signals, active registry, preview, "
    "actionable candidate, promotion eligibility, availability, gates, Step 2/3/4, "
    "final gate, weekly, broker/live all ignore this artifact). A candidate is never "
    "active and cannot affect allowed_actions; activation requires a future explicit "
    "operator-approval workflow (R2G-5). permission_effect=none, not_authorization=true."
)


def build_research_anchor_candidates(
    *,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    analyst_memo_valid: bool,
    compiled_support_signals: Mapping[str, Any] | None = None,
    active_registry: Mapping[str, Any] | None = None,
    as_of_date: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the report-only candidate artifact (pure; never raises)."""
    try:
        return _build(
            evidence_packet=evidence_packet,
            analyst_memo=analyst_memo,
            analyst_memo_valid=analyst_memo_valid,
            compiled_support_signals=compiled_support_signals,
            active_registry=active_registry,
            as_of_date=as_of_date,
            generated_at=generated_at,
        )
    except Exception:  # noqa: BLE001 - report-only builder must never raise
        return _result(
            candidates=[],
            rejected=[],
            generation_blockers=["candidate_builder_internal_error"],
            as_of_date=as_of_date,
            generated_at=generated_at,
        )


def _build(
    *,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    analyst_memo_valid: bool,
    compiled_support_signals: Mapping[str, Any] | None,
    active_registry: Mapping[str, Any] | None,
    as_of_date: str | None,
    generated_at: str | None,
) -> dict[str, Any]:
    packet = evidence_packet if isinstance(evidence_packet, Mapping) else {}
    universe = packet.get("universe") if isinstance(packet.get("universe"), Mapping) else {}
    allowed_buy = _ticker_set(universe.get("allowed_buy_tickers"))

    active_by_ticker = _active_anchor_by_ticker(active_registry)
    gap_tickers = _anchor_gap_tickers(compiled_support_signals)

    generation_blockers: list[str] = []
    if analyst_memo is None:
        generation_blockers.append(GEN_BLOCKER_MEMO_ABSENT)
    elif not analyst_memo_valid:
        generation_blockers.append(GEN_BLOCKER_MEMO_INVALID)
    if not allowed_buy:
        generation_blockers.append(GEN_BLOCKER_NO_UNIVERSE)

    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    memo = analyst_memo if isinstance(analyst_memo, Mapping) else {}
    memo_confidence = _confidence(memo.get("confidence"))
    seen_ids: set[str] = set()

    for row in _rows(memo.get("ticker_relative_view")):
        stance = _lower(row.get("stance"))
        if stance != _PREFER_STANCE:
            continue  # only a *prefer* stance suggests wanting buy-support grounding
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            rejected.append(_rejected_entry(None, [REJECT_NO_TICKER], row_ticker=row.get("ticker")))
            continue

        proposed = _proposed_anchor(
            ticker=ticker, rationale=row.get("rationale_12m_plus"), confidence_floor=memo_confidence
        )
        candidate_sha256 = _sha256_of(proposed)
        candidate_id = f"CAND-{ticker}-{candidate_sha256[:12]}"

        # Defensive: a proposed anchor must never carry authority-shaped content.
        forbidden = _forbidden_content(proposed)
        if forbidden:
            rejected.append(
                _rejected_entry(candidate_id, forbidden, proposed=proposed, sha=candidate_sha256, ticker=ticker)
            )
            continue

        if ticker not in allowed_buy:
            rejected.append(
                _rejected_entry(candidate_id, [REJECT_OUT_OF_UNIVERSE], proposed=proposed, sha=candidate_sha256, ticker=ticker)
            )
            continue

        would_validate, validation_problems = _evaluate_proposed_anchor(proposed, allowed_universe=allowed_buy)

        already_active = active_by_ticker.get(ticker)
        duplicate = already_active is not None

        blocker_reasons = [BLOCKER_REQUIRES_OPERATOR_APPROVAL, BLOCKER_SOURCE_B_NEVER_AUTO_ACTIVATES]
        if not would_validate:
            blocker_reasons.append(BLOCKER_INCOMPLETE_ANCHOR_SHAPE)
        if duplicate:
            blocker_reasons.append(BLOCKER_DUPLICATE_OF_ACTIVE)

        entry = {
            "candidate_id": candidate_id,
            "proposed_anchor": proposed,
            "candidate_sha256": candidate_sha256,
            "source_category": SOURCE_CATEGORY_CANDIDATE_ONLY,
            "source_type": SOURCE_TYPE_ANALYST_MEMO,
            "source_refs": _source_refs(ticker, gap_tickers),
            "confidence": memo_confidence,
            "status": STATUS_DUPLICATE_OF_ACTIVE if duplicate else STATUS_CANDIDATE,
            "would_validate_as_anchor": would_validate,
            "validation_problems": validation_problems,
            "blocker_reasons": blocker_reasons,
            # Only a shape-valid, in-universe, non-duplicate suggestion is worth an
            # operator's approval review. Never authorization — R2G-5 gates that.
            "eligible_for_operator_approval": bool(would_validate and not duplicate),
            "already_active_anchor_id": already_active,
        }
        if candidate_id not in seen_ids:
            seen_ids.add(candidate_id)
            candidates.append(entry)

    return _result(
        candidates=candidates,
        rejected=rejected,
        generation_blockers=generation_blockers,
        as_of_date=as_of_date,
        generated_at=generated_at,
    )


# --- proposed anchor + validation --------------------------------------------


def _proposed_anchor(*, ticker: str, rationale: Any, confidence_floor: str | None) -> dict[str, Any]:
    """A minimal, deterministic anchor SKELETON for operator review.

    Carries only citation-target fields — never budget/order/action/execution
    fields. The dated fields (``anchor_date_et`` / ``valid_from`` / ``valid_until``)
    are intentionally OMITTED: the operator must supply a real dated anchor, so the
    skeleton deliberately does NOT validate as a complete anchor as-is (the
    validator flags the missing dates, which surfaces exactly what the operator
    must add).
    """
    return {
        "anchor_id": f"{ticker}_CANDIDATE_ANCHOR",
        "anchor_type": _DEFAULT_ANCHOR_TYPE,
        "applicable_tickers": [ticker],
        "source_type": "operator",
        "confidence_floor": confidence_floor,
        "summary": rationale if isinstance(rationale, str) and rationale.strip() else None,
    }


def evaluate_proposed_anchor(
    proposed_anchor: Mapping[str, Any], *, allowed_universe: Any
) -> tuple[bool, list[str]]:
    """Public: would this proposed anchor validate as a real anchor? (pure)."""
    return _evaluate_proposed_anchor(proposed_anchor, allowed_universe=allowed_universe)


def _evaluate_proposed_anchor(
    proposed_anchor: Mapping[str, Any], *, allowed_universe: Any
) -> tuple[bool, list[str]]:
    """Reuse the real anchor validator (never loosened) over a single proposal."""
    payload = {
        "schema_version": "research_anchors_v1",
        "is_llm_generated": False,
        "anchors": [dict(proposed_anchor)],
    }
    result = validate_research_anchors(payload, allowed_universe=allowed_universe)
    if not result.anchors:
        return False, list(result.errors) or ["no_anchor_evaluated"]
    evaluated = result.anchors[0]
    problems = list(evaluated.get("problems") or [])
    return bool(evaluated.get("valid")) and not result.errors, problems + list(result.errors)


def _forbidden_content(proposed: Mapping[str, Any]) -> list[str]:
    """Defensive scan: reject any budget/order/action/execution key or token."""
    reasons: list[str] = []
    for key in _iter_keys(proposed):
        if not isinstance(key, str):
            continue
        low = key.strip().lower()
        if low in {k.lower() for k in FORBIDDEN_KEYS} or any(s in low for s in FORBIDDEN_KEY_SUBSTRINGS):
            reasons.append(REJECT_FORBIDDEN_KEY)
            break
    for value in _iter_string_values(proposed):
        if value.strip().lower() in FORBIDDEN_ACTION_VALUE_TOKENS:
            reasons.append(REJECT_FORBIDDEN_ACTION_TOKEN)
            break
    return reasons


# --- source diagnostics ------------------------------------------------------


def _active_anchor_by_ticker(active_registry: Mapping[str, Any] | None) -> dict[str, str]:
    """Map ticker -> an active anchor_id already covering it (for dedup)."""
    out: dict[str, str] = {}
    if not isinstance(active_registry, Mapping) or active_registry.get("registry_valid") is not True:
        return out
    for anchor in _as_list(active_registry.get("active_anchors")):
        if not isinstance(anchor, Mapping):
            continue
        anchor_id = anchor.get("anchor_id")
        if not (isinstance(anchor_id, str) and anchor_id.strip()):
            continue
        for ticker in _ticker_set(anchor.get("applicable_tickers")):
            out.setdefault(ticker, anchor_id.strip())
    return out


def _anchor_gap_tickers(compiled_support_signals: Mapping[str, Any] | None) -> set[str]:
    """Tickers the support signal flagged as passing qualitative gates but lacking
    anchor grounding — the strongest 'needs an anchor' signal (enrichment only)."""
    if not isinstance(compiled_support_signals, Mapping):
        return set()
    out: set[str] = set()
    for row in _as_list(compiled_support_signals.get("qualitative_support_only")):
        if isinstance(row, Mapping):
            ticker = _ticker(row.get("ticker"))
            if ticker:
                out.add(ticker)
    return out


def _source_refs(ticker: str, gap_tickers: set[str]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = [
        {"kind": "analyst_memo_ticker_relative_view", "ticker": ticker}
    ]
    if ticker in gap_tickers:
        refs.append({"kind": "support_signal_qualitative_support_only_gap", "ticker": ticker})
    return refs


# --- result assembly ---------------------------------------------------------


def _rejected_entry(
    candidate_id: str | None,
    rejection_reasons: list[str],
    *,
    proposed: Mapping[str, Any] | None = None,
    sha: str | None = None,
    ticker: str | None = None,
    row_ticker: Any = None,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "proposed_anchor": dict(proposed) if isinstance(proposed, Mapping) else None,
        "candidate_sha256": sha,
        "source_category": SOURCE_CATEGORY_CANDIDATE_ONLY,
        "source_type": SOURCE_TYPE_ANALYST_MEMO,
        "ticker": ticker if ticker is not None else (row_ticker if isinstance(row_ticker, str) else None),
        "status": STATUS_REJECTED,
        "rejection_reasons": list(rejection_reasons),
    }


def _result(
    *,
    candidates: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    generation_blockers: list[str],
    as_of_date: str | None,
    generated_at: str | None,
) -> dict[str, Any]:
    duplicates = sum(1 for c in candidates if c.get("status") == STATUS_DUPLICATE_OF_ACTIVE)
    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "consumed_by_support_signals": False,
        "consumed_by_compiler": False,
        "consumed_by_promotion_eligibility": False,
        "consumed_by_availability": False,
        "consumed_by_gates": False,
        "consumed_by_step2": False,
        "consumed_by_step4": False,
        "cannot_affect_allowed_actions": True,
        "candidates": candidates,
        "rejected_candidates": rejected,
        "counts": {
            "candidates": len(candidates),
            "duplicates_of_active": duplicates,
            "eligible_for_operator_approval": sum(
                1 for c in candidates if c.get("eligible_for_operator_approval")
            ),
            "rejected": len(rejected),
        },
        "candidate_generation_blockers": list(generation_blockers),
        "notes": _NOTES,
    }


# --- disk wrapper ------------------------------------------------------------


def write_research_anchor_candidates(
    *,
    output_path: Any,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    analyst_memo_valid: bool,
    compiled_support_signals: Mapping[str, Any] | None = None,
    active_registry: Mapping[str, Any] | None = None,
    as_of_date: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build + write the report-only candidate artifact; return a small summary."""
    from investment_orchestrator.common.io import write_json

    payload = build_research_anchor_candidates(
        evidence_packet=evidence_packet,
        analyst_memo=analyst_memo,
        analyst_memo_valid=analyst_memo_valid,
        compiled_support_signals=compiled_support_signals,
        active_registry=active_registry,
        as_of_date=as_of_date,
        generated_at=generated_at,
    )
    write_json(output_path, payload)
    return {
        "research_anchor_candidates_path": str(output_path),
        "candidate_count": str(payload["counts"]["candidates"]),
        "rejected_count": str(payload["counts"]["rejected"]),
    }


# --- helpers -----------------------------------------------------------------


def _rows(value: Any) -> list[Mapping[str, Any]]:
    return [r for r in value if isinstance(r, Mapping)] if isinstance(value, list) else []


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _ticker_set(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, list):
        for item in value:
            t = _ticker(item)
            if t:
                out.add(t)
    return out


def _ticker(value: Any) -> str:
    return value.strip().upper() if isinstance(value, str) else ""


def _lower(value: Any) -> str | None:
    return value.strip().lower() if isinstance(value, str) else None


def _confidence(value: Any) -> str | None:
    low = _lower(value)
    return low if low in _CONFIDENCE_VALUES else None


def _iter_keys(obj: Any):
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            yield key
            yield from _iter_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_keys(item)


def _iter_string_values(obj: Any):
    if isinstance(obj, Mapping):
        for value in obj.values():
            yield from _iter_string_values(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_string_values(item)
    elif isinstance(obj, str):
        yield obj


def _sha256_of(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
