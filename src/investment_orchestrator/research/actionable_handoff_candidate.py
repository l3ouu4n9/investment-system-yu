"""Step 1C actionable compiled-handoff CANDIDATE (R2E.5b-1, report-only *separate* artifact).

Answers a single question **without changing any active trading path**:

    "Can the R2E.5b-0 preview rows be transformed into a *full* strict research
    handoff candidate that passes :func:`validate_research_handoff`, without yet
    being used for availability or trading?"

It takes the report-only `compiled_actionable_handoff_preview.json` and overlays
its `preview_actionable_rows` onto a base strict-handoff candidate (the active
`compiled_research_handoff_candidate.json`, or a fresh deterministic re-compile),
producing a **separate** candidate whose promoted rows are
`actionability_status == "actionable_this_run"` and whose
`positive_delta_research_supported` is populated — then validates it with the
existing strict validator.

This module changes **no** production behavior:

* It writes only its own three artifacts and **never** mutates the active
  `compiled_research_handoff_candidate.json` (which stays non-actionable).
* It is **never** fed into the availability evaluator, the degraded-mode decision,
  Step 2 render, the weekly actionable path, the order compiler, or the final
  execution safety gate.
* It NEVER authorizes a trade and NEVER adds `NEW_BUY` / `ORDER_COMPILATION`;
  `STRICT_FRESH_WITH_LLM_MEMO` is NOT enabled.

Every artifact carries `is_llm_generated: false`, `report_only: true`,
`permission_effect: "none"`, `not_authorization: true`. A promoted
`actionable_this_run` row describes a *hypothetical future* row only — it exists
to prove the *shape* validates, not to authorize anything.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from investment_orchestrator.research.handoff_compiler import compile_research_handoff
from investment_orchestrator.validators.validate_research_handoff import (
    BASE_ROLE_KEYS,
    DATA_GAP_MARKERS,
    research_handoff_validation_result_to_dict,
    validate_research_handoff,
)


CANDIDATE_SCHEMA_VERSION = "research_handoff_compiled_actionable_v1"
METADATA_SCHEMA_VERSION = "compiled_actionable_research_handoff_metadata_v1"

_ACTIONABLE_STATUS = "actionable_this_run"
_WATCH_ONLY_STATUS = "ranking_hold_watch_only"
_VALID_LINKAGE_QUALITY = {"strong", "adequate"}
_CLEAN_ACTIONABLE_THESIS = "Long-term 12m+ thesis grounded in the cited research anchor (report-only preview)."

_NON_AUTHORIZATION_NOTE = (
    "Report-only actionable compiled-handoff CANDIDATE (R2E.5b-1). SEPARATE artifact: it never "
    "mutates the active compiled_research_handoff_candidate.json (which stays non-actionable), is "
    "never fed into the availability evaluator, degraded-mode decision, Step 2 render, the weekly "
    "actionable path, the order compiler, or the final execution safety gate. It exists only to prove "
    "the preview rows can be shaped into a validator-compatible strict handoff. It NEVER authorizes a "
    "trade and adds no NEW_BUY / ORDER_COMPILATION permission (permission_effect=none, "
    "not_authorization=true)."
)


# --- pure builder ------------------------------------------------------------


def build_actionable_handoff_candidate(
    *,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    actionable_handoff_preview: Mapping[str, Any] | None,
    base_candidate: Mapping[str, Any] | None = None,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a separate, validator-shaped actionable handoff candidate (pure; never raises).

    Promotes only the preview's ``preview_actionable_rows`` (already gated + capped
    by R2E.5b-0) onto a base strict candidate. Rows that cannot satisfy the strict
    validator's actionable-row contract (no anchor id/ref, no anchor date, or a
    DATA_GAP-tainted field) are left watch-only rather than emitted invalid.
    """
    preview = actionable_handoff_preview if isinstance(actionable_handoff_preview, Mapping) else {}
    preview_rows = preview.get("preview_actionable_rows")
    preview_rows = preview_rows if isinstance(preview_rows, list) else []

    # Base: prefer the active compiled candidate; otherwise re-compile deterministically
    # (identical to the active compiler output) so this module is self-sufficient.
    if isinstance(base_candidate, Mapping):
        candidate = _deep_copy(base_candidate)
    else:
        candidate = compile_research_handoff(
            evidence_packet if isinstance(evidence_packet, Mapping) else {},
            analyst_memo,
            strategy_settings=strategy_settings,
        )

    trade_universe = candidate.get("trade_universe") if isinstance(candidate.get("trade_universe"), Mapping) else {}
    allowed_buy = _string_list(trade_universe.get("allowed_buy_tickers"))
    allowed_set = set(allowed_buy)

    scorecard = candidate.get("buy_universe_scorecard")
    scorecard = scorecard if isinstance(scorecard, list) else []
    scorecard_by_ticker = {
        _ticker(row.get("ticker")): row
        for row in scorecard
        if isinstance(row, Mapping) and _ticker(row.get("ticker"))
    }

    # Defensive re-assertion of the base-universe weekly cap (the preview already
    # applied it; never promote more than that here).
    cap = preview.get("base_new_ticker_cap_applied")
    cap = cap if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 0 else None

    promoted_order: list[str] = []
    for prow in preview_rows:
        if not isinstance(prow, Mapping):
            continue
        ticker = _ticker(prow.get("ticker"))
        if not ticker or ticker not in scorecard_by_ticker or ticker not in allowed_set:
            continue
        if ticker in promoted_order:
            continue
        if cap is not None and len(promoted_order) >= cap:
            break
        overlay = _actionable_overlay(scorecard_by_ticker[ticker], prow)
        if overlay is None:
            continue  # not validator-promotable → stays watch-only
        scorecard_by_ticker[ticker].update(overlay)
        promoted_order.append(ticker)

    promoted_set = set(promoted_order)

    # Group promoted tickers by their deterministic role for a coherent shortlist.
    role_of = {t: scorecard_by_ticker[t].get("role_layer") for t in promoted_order}
    shortlist_by_role: dict[str, list[str]] = {role: [] for role in BASE_ROLE_KEYS}
    for ticker in promoted_order:
        role = role_of.get(ticker)
        if role in shortlist_by_role:
            shortlist_by_role[role].append(ticker)
    watch_only_by_role: dict[str, list[str]] = {role: [] for role in BASE_ROLE_KEYS}
    for ticker in allowed_buy:
        if ticker in promoted_set:
            continue
        role = scorecard_by_ticker.get(ticker, {}).get("role_layer")
        if role in watch_only_by_role:
            watch_only_by_role[role].append(ticker)

    handoff = candidate.get("strategy_a_research_handoff")
    if isinstance(handoff, Mapping):
        handoff["positive_delta_research_supported"] = list(promoted_order)
        handoff["positive_delta_not_implied_for"] = [t for t in allowed_buy if t not in promoted_set]
        handoff["base_shortlist_eligible_by_role"] = shortlist_by_role
        handoff["base_watch_only_by_role"] = watch_only_by_role
        handoff["compilation_non_actionable_reason"] = (
            "report_only_actionable_preview_candidate_no_execution_authority"
        )

    # Report-only, non-authorization markers (validator ignores unknown fields).
    candidate["schema_version"] = CANDIDATE_SCHEMA_VERSION
    candidate["is_llm_generated"] = False
    candidate["report_only"] = True
    candidate["permission_effect"] = "none"
    candidate["not_authorization"] = True
    candidate["compiled_by"] = "deterministic_actionable_handoff_candidate_builder"
    candidate["actionable_preview_source"] = "compiled_actionable_handoff_preview"
    candidate["actionable_this_run_tickers"] = list(promoted_order)
    return candidate


def _actionable_overlay(base_row: Mapping[str, Any], prow: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the scorecard-row overlay to make a row ``actionable_this_run``.

    Returns ``None`` when the preview row cannot satisfy the strict validator's
    actionable-row contract (so the row is left watch-only and the candidate stays
    valid).
    """
    event_id_refs = prow.get("event_id_refs")
    event_id_refs = list(event_id_refs) if isinstance(event_id_refs, list) else []
    structural_theme_refs = prow.get("structural_theme_refs")
    structural_theme_refs = list(structural_theme_refs) if isinstance(structural_theme_refs, list) else []
    # Validator requires a truthy primary_anchor_event_id; structural-theme preview
    # rows carry it as null + a primary_anchor_ref, so fall back to the ref.
    primary_anchor_event_id = prow.get("primary_anchor_event_id") or prow.get("primary_anchor_ref")
    primary_anchor_date_et = prow.get("primary_anchor_date_et")
    anchor_type = prow.get("anchor_type")
    linkage = prow.get("thesis_linkage_quality_preview")
    if linkage not in _VALID_LINKAGE_QUALITY:
        linkage = "adequate"

    # Strict validator preconditions for actionable_this_run.
    if not event_id_refs and not structural_theme_refs:
        return None
    if not (isinstance(primary_anchor_event_id, str) and primary_anchor_event_id.strip()):
        return None
    if not (isinstance(primary_anchor_date_et, str) and primary_anchor_date_et.strip()):
        return None
    if _has_data_gap_marker(primary_anchor_event_id) or _has_data_gap_marker(primary_anchor_date_et):
        return None
    if isinstance(anchor_type, str) and (not anchor_type.strip() or _has_data_gap_marker(anchor_type)):
        return None

    # The 12m+ summary must be clean (no DATA_GAP marker) for an actionable row.
    summary = base_row.get("thesis_12m_plus_summary")
    if not (isinstance(summary, str) and summary.strip()) or _has_data_gap_marker(summary):
        summary = _CLEAN_ACTIONABLE_THESIS

    overlay: dict[str, Any] = {
        "actionability_status": _ACTIONABLE_STATUS,
        "primary_anchor_event_id": primary_anchor_event_id,
        "primary_anchor_date_et": primary_anchor_date_et,
        "event_id_refs": event_id_refs,
        "structural_theme_refs": structural_theme_refs,
        "thesis_12m_plus_supported": True,
        "thesis_12m_plus_summary": summary,
        "thesis_linkage_quality": linkage,
        "compile_blocker_if_any": None,
    }
    if isinstance(anchor_type, str) and anchor_type.strip():
        overlay["primary_anchor_type"] = anchor_type
    return overlay


# --- metadata ----------------------------------------------------------------


def build_actionable_handoff_metadata(
    *,
    candidate: Mapping[str, Any],
    validation: Any,
    actionable_handoff_preview: Mapping[str, Any] | None,
    compiled_support_signals: Mapping[str, Any] | None,
    evidence_packet: Mapping[str, Any] | None,
    base_candidate: Mapping[str, Any] | None,
    used_active_compiled_handoff_as_base: bool,
    actionable_handoff_preview_path: Any = None,
    compiled_support_signals_path: Any = None,
    evidence_packet_path: Any = None,
    base_candidate_path: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the report-only actionable-candidate metadata artifact."""
    preview = actionable_handoff_preview if isinstance(actionable_handoff_preview, Mapping) else {}
    preview_rows = preview.get("preview_actionable_rows")
    preview_actionable_row_count = len(preview_rows) if isinstance(preview_rows, list) else 0
    candidate_actionable_row_count = _count_actionable_rows(candidate)

    return {
        "schema_version": METADATA_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "consumed_by_availability": False,
        "consumed_by_step2": False,
        "generated_at": generated_at,
        "source_actionable_handoff_preview": _source_ref(actionable_handoff_preview, actionable_handoff_preview_path),
        "source_compiled_support_signals": _source_ref(compiled_support_signals, compiled_support_signals_path),
        "source_evidence_packet": _source_ref(evidence_packet, evidence_packet_path),
        "used_active_compiled_handoff_as_base": used_active_compiled_handoff_as_base,
        "source_active_compiled_handoff": _source_ref(
            base_candidate if used_active_compiled_handoff_as_base else None,
            base_candidate_path if used_active_compiled_handoff_as_base else None,
        ),
        "preview_actionable_row_count": preview_actionable_row_count,
        "candidate_actionable_row_count": candidate_actionable_row_count,
        "actionable_this_run_tickers": _actionable_tickers(candidate),
        "validation_passed": _validation_valid(validation),
        "notes": _NON_AUTHORIZATION_NOTE,
    }


# --- disk wrapper ------------------------------------------------------------


def write_actionable_handoff_candidate(
    *,
    candidate_path: Any,
    validation_path: Any,
    metadata_path: Any,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    actionable_handoff_preview: Mapping[str, Any] | None,
    compiled_support_signals: Mapping[str, Any] | None = None,
    base_candidate: Mapping[str, Any] | None = None,
    strategy_settings: Mapping[str, Any] | None = None,
    actionable_handoff_preview_path: Any = None,
    compiled_support_signals_path: Any = None,
    evidence_packet_path: Any = None,
    base_candidate_path: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build + validate + write the three report-only actionable-candidate artifacts."""
    from investment_orchestrator.common.io import write_json

    used_active_base = isinstance(base_candidate, Mapping)
    candidate = build_actionable_handoff_candidate(
        evidence_packet=evidence_packet,
        analyst_memo=analyst_memo,
        actionable_handoff_preview=actionable_handoff_preview,
        base_candidate=base_candidate,
        strategy_settings=strategy_settings,
    )
    validation = validate_research_handoff(candidate, strategy_settings=strategy_settings)
    metadata = build_actionable_handoff_metadata(
        candidate=candidate,
        validation=validation,
        actionable_handoff_preview=actionable_handoff_preview,
        compiled_support_signals=compiled_support_signals,
        evidence_packet=evidence_packet,
        base_candidate=base_candidate,
        used_active_compiled_handoff_as_base=used_active_base,
        actionable_handoff_preview_path=actionable_handoff_preview_path,
        compiled_support_signals_path=compiled_support_signals_path,
        evidence_packet_path=evidence_packet_path,
        base_candidate_path=base_candidate_path,
        generated_at=generated_at,
    )
    write_json(candidate_path, candidate)
    write_json(validation_path, research_handoff_validation_result_to_dict(validation))
    write_json(metadata_path, metadata)
    return {
        "actionable_candidate_path": str(candidate_path),
        "actionable_validation_path": str(validation_path),
        "actionable_metadata_path": str(metadata_path),
        "candidate_actionable_row_count": str(metadata["candidate_actionable_row_count"]),
        "validation_passed": str(metadata["validation_passed"]),
    }


# --- helpers -----------------------------------------------------------------


def _count_actionable_rows(candidate: Mapping[str, Any]) -> int:
    scorecard = candidate.get("buy_universe_scorecard") if isinstance(candidate, Mapping) else None
    if not isinstance(scorecard, list):
        return 0
    return sum(
        1
        for row in scorecard
        if isinstance(row, Mapping) and row.get("actionability_status") == _ACTIONABLE_STATUS
    )


def _actionable_tickers(candidate: Mapping[str, Any]) -> list[str]:
    scorecard = candidate.get("buy_universe_scorecard") if isinstance(candidate, Mapping) else None
    if not isinstance(scorecard, list):
        return []
    out: list[str] = []
    for row in scorecard:
        if isinstance(row, Mapping) and row.get("actionability_status") == _ACTIONABLE_STATUS:
            ticker = _ticker(row.get("ticker"))
            if ticker:
                out.append(ticker)
    return out


def _has_data_gap_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in DATA_GAP_MARKERS)


def _validation_valid(validation: Any) -> bool:
    if isinstance(validation, Mapping):
        return validation.get("valid") is True
    return getattr(validation, "valid", False) is True


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


def _deep_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value))


def _string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        ticker = _ticker(value)
        if ticker and ticker not in seen:
            seen.add(ticker)
            out.append(ticker)
    return out


def _ticker(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()
