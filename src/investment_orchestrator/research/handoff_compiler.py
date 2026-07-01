"""Step 1C deterministic strict research-handoff compiler (R2D, report-only).

Compiles ``evidence_packet.json`` (deterministic, R2B) plus an *optional*
``analyst_memo.json`` (qualitative LLM opinion, R2C) into a candidate that is
**structurally complete** for the existing strict validator
(:func:`validate_research_handoff`): it always emits every
``REQUIRED_TOP_LEVEL_FIELDS`` with the correct container types, so the
``narrative_lanes`` / ``unrecoverable`` failure modes can no longer occur.

Hard invariants (the compiler never hallucinates and never authorizes):

* **Universe / settings stay deterministic.** ``trade_universe`` /
  ``user_approved_extended_etf_static_list`` / extended candidate universe come
  only from the evidence packet (settings/portfolio derived). The analyst memo
  can never widen them.
* **The memo is advisory only.** A present *and valid* memo contributes
  qualitative rationale / regime view / ranking hints for in-universe tickers; a
  missing or invalid memo is ignored and the candidate degrades to an
  evidence-only, **non-actionable** handoff.
* **Fail closed for NEW_BUY.** In R2D the compiled handoff is *always*
  non-actionable: every scorecard row is watch-only,
  ``positive_delta_research_supported`` is empty, and the extended sleeve is
  disabled. The compiled candidate is **not** fed into the availability /
  degraded-mode decision (that switch is R2E), so it cannot change
  ``allowed_actions`` and evidence-only can never enter NEW_BUY.

This module is strictly report-only. ``compile_research_handoff`` is a pure
function (mappings in, mapping out; never raises).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from investment_orchestrator.research.analyst_memo import (
    evidence_universe_from_packet,
    validate_analyst_memo,
)
from investment_orchestrator.validators.validate_research_handoff import (
    BASE_ROLE_KEYS,
    REQUIRED_TOP_LEVEL_FIELDS,
    research_handoff_validation_result_to_dict,
    validate_research_handoff,
)


COMPILED_SCHEMA_VERSION = "research_handoff_compiled_v1"
METADATA_SCHEMA_VERSION = "compiled_research_handoff_metadata_v1"
HANDOFF_VERSION = "strategy_a_research_handoff_v1"
HANDOFF_SCOPE = "research_to_decision_builder_only"

# Compilation modes (also written into the metadata artifact).
COMPILATION_MODE_EVIDENCE_PLUS_MEMO = "evidence_plus_memo"
COMPILATION_MODE_EVIDENCE_ONLY = "evidence_only"
COMPILATION_MODE_INVALID_MEMO_IGNORED = "invalid_memo_ignored"

_ROLE_BENCHMARK = "benchmark_carrier_core"
_ROLE_DIVERSIFIED = "diversified_core_buffer"
_ROLE_SECTOR = "sector_alpha_tilt"

# Deterministic, role-based scaffolding text (contains no DATA_GAP markers so a
# watch-only row is never spuriously flagged).
_ROLE_ENTRY_DRIVER = {
    _ROLE_BENCHMARK: "benchmark_anchor",
    _ROLE_DIVERSIFIED: "broad_market_buffer",
    _ROLE_SECTOR: "sector_structural_growth",
}
_ROLE_THESIS = {
    _ROLE_BENCHMARK: "Benchmark core exposure retained as the long-term reference anchor.",
    _ROLE_DIVERSIFIED: "Broad-market diversified buffer retained for core diversification.",
    _ROLE_SECTOR: "Sector / thematic structural-growth tilt retained as a long-cycle watch candidate.",
}

# Explicit, machine-readable reason the compiled handoff is non-actionable.
_NO_FRESH_MEMO_REASON = "missing_fresh_analyst_memo"
_NO_FRESH_MEMO_GAP = "DATA_GAP: no_fresh_analyst_memo"
_NO_TICKER_VIEW_GAP = "DATA_GAP: no_analyst_view_for_ticker"

_STRATEGY_A_MUST_STILL_APPLY = (
    "Portfolio Snapshot",
    "Strategy Settings",
    "market data",
    "role caps",
    "existing open orders",
    "hard cap",
    "template map",
    "compile contract",
)


# --- pure compiler -----------------------------------------------------------


def compile_research_handoff(
    evidence_packet: Mapping[str, Any],
    analyst_memo: Mapping[str, Any] | None = None,
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a structurally complete strict-handoff candidate (pure; never raises).

    ``evidence_packet`` is the only source of the allowed universe / approved
    extended list. ``analyst_memo`` is used only when present *and* valid (it is
    re-validated here against the evidence universe); otherwise the candidate is
    an evidence-only, non-actionable handoff. ``strategy_settings`` is read only
    for the deterministic ticker→role map / benchmark; it never adds tickers.
    """
    packet = evidence_packet if isinstance(evidence_packet, Mapping) else {}
    universe = packet.get("universe") if isinstance(packet.get("universe"), Mapping) else {}

    allowed_buy = _string_list(universe.get("allowed_buy_tickers"))
    core = _string_list(universe.get("core_universe"))
    satellite = _string_list(universe.get("satellite_universe"))
    approved_extended = _string_list(universe.get("approved_extended_etf"))

    evidence_uni = evidence_universe_from_packet(packet)
    memo, mode = _classify_memo(analyst_memo, evidence_uni)

    role_map, benchmark = _deterministic_role_inputs(strategy_settings)
    role_layer_by_ticker = {
        ticker: _role_layer_for(ticker, role_map=role_map, satellite=set(satellite), benchmark=benchmark)
        for ticker in allowed_buy
    }
    memo_views = _memo_views_by_ticker(memo) if memo is not None else {}

    scorecard = [
        _scorecard_row(
            ticker=ticker,
            index=index,
            role_layer=role_layer_by_ticker[ticker],
            memo_view=memo_views.get(ticker),
            mode=mode,
        )
        for index, ticker in enumerate(allowed_buy)
    ]

    disable_reason = "extended ETF sleeve disabled by default in the deterministic compiler."
    why_not_enabled = (
        "deterministic compiler never admits an extended ETF from analyst-memo opinion alone; "
        "activation requires deterministic preconditions not present in R2D."
    )

    candidate: dict[str, Any] = {
        "schema_version": COMPILED_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "compiled_by": "deterministic_handoff_compiler",
        "compilation_mode": mode,
        "trade_universe": {
            "allowed_buy_tickers": list(allowed_buy),
            "notes": "Deterministically compiled from evidence_packet.universe; the analyst memo cannot widen it.",
        },
        "buy_universe_scorecard": scorecard,
        "scheduled_events": [],
        "structural_themes_6_18m": [],
        "regime_inputs": _regime_inputs(memo, mode),
        "policy_items": [],
        "top5_next_week": [],
        "user_approved_extended_etf_static_list": list(approved_extended),
        "proposed_extended_etf_candidates": [],
        "extended_etf_candidate_universe": [],
        "extended_etf_predecision_scorecard": [],
        "approved_static_list_screening_log": [
            {
                "ticker": ticker,
                "screening_status": "approved_static_list_member",
                "admitted_to_effective_universe_this_run": False,
                "note": "deterministic compiler keeps approved extended ETFs out of the base buy universe.",
            }
            for ticker in approved_extended
        ],
        "optional_extended_etf_sleeve": {
            "enabled": False,
            "allowed_extended_etf_tickers": [],
            "disable_reason": disable_reason,
            "why_not_enabled": why_not_enabled,
        },
        "strategy_a_research_handoff": _strategy_a_research_handoff(
            allowed_buy=allowed_buy,
            approved_extended=approved_extended,
            role_layer_by_ticker=role_layer_by_ticker,
            mode=mode,
            disable_reason=disable_reason,
            why_not_enabled=why_not_enabled,
        ),
    }
    if mode == COMPILATION_MODE_EVIDENCE_PLUS_MEMO:
        candidate["analyst_memo_qualitative_context"] = _qualitative_context(memo)
    return candidate


def _classify_memo(
    analyst_memo: Mapping[str, Any] | None,
    evidence_universe: list[str],
) -> tuple[Mapping[str, Any] | None, str]:
    """Decide the compilation mode by re-validating the memo (never trusted)."""
    if analyst_memo is None:
        return None, COMPILATION_MODE_EVIDENCE_ONLY
    if not isinstance(analyst_memo, Mapping):
        return None, COMPILATION_MODE_INVALID_MEMO_IGNORED
    problems = validate_analyst_memo(analyst_memo, evidence_universe=evidence_universe)
    if problems:
        return None, COMPILATION_MODE_INVALID_MEMO_IGNORED
    return analyst_memo, COMPILATION_MODE_EVIDENCE_PLUS_MEMO


def _scorecard_row(
    *,
    ticker: str,
    index: int,
    role_layer: str,
    memo_view: Mapping[str, Any] | None,
    mode: str,
) -> dict[str, Any]:
    has_view = isinstance(memo_view, Mapping)
    rationale = memo_view.get("rationale_12m_plus") if has_view else None
    has_rationale = isinstance(rationale, str) and rationale.strip() != ""

    if mode == COMPILATION_MODE_EVIDENCE_PLUS_MEMO and has_view:
        thesis_supported = True
        thesis_summary = rationale if has_rationale else _ROLE_THESIS[role_layer]
        compile_blocker = None
    elif mode == COMPILATION_MODE_EVIDENCE_PLUS_MEMO:
        # Valid memo present but it expresses no view for this ticker.
        thesis_supported = False
        thesis_summary = _ROLE_THESIS[role_layer]
        compile_blocker = _NO_TICKER_VIEW_GAP
    else:
        # evidence_only / invalid_memo_ignored: no fresh research support.
        thesis_supported = False
        thesis_summary = _ROLE_THESIS[role_layer]
        compile_blocker = _NO_FRESH_MEMO_GAP

    priority = _execution_priority(index, memo_view if has_view else None)
    return {
        "ticker": ticker,
        "role_layer": role_layer,
        "execution_priority_this_run": priority,
        # Never "actionable_this_run" in R2D — the compiled handoff is report-only
        # and must not authorize a NEW_BUY by itself.
        "actionability_status": "ranking_hold_watch_only",
        "entry_driver": _ROLE_ENTRY_DRIVER[role_layer],
        "primary_anchor_type": "structural_theme",
        "primary_anchor_event_id": None,
        "primary_anchor_date_et": None,
        "preferred_scheduled_theme_event_id": None,
        "thesis_12m_plus_supported": thesis_supported,
        "thesis_12m_plus_summary": thesis_summary,
        "thesis_linkage_quality": "adequate",
        "compile_blocker_if_any": compile_blocker,
        "event_id_refs": [],
        "structural_theme_refs": [],
    }


def _execution_priority(index: int, memo_view: Mapping[str, Any] | None) -> int:
    """Deterministic 1-based priority, nudged by memo stance (never actionable)."""
    base = index + 1
    if not isinstance(memo_view, Mapping):
        return base
    stance = memo_view.get("stance")
    stance = stance.strip().lower() if isinstance(stance, str) else ""
    # A qualitative ranking hint only: prefer sorts earlier, deprioritize later.
    if stance == "prefer":
        return base
    if stance == "deprioritize":
        return base + 1000
    return base + 500


def _regime_inputs(memo: Mapping[str, Any] | None, mode: str) -> dict[str, Any]:
    if mode == COMPILATION_MODE_EVIDENCE_PLUS_MEMO and isinstance(memo, Mapping):
        regime_view = memo.get("regime_view")
        return {
            "regime_view": regime_view if isinstance(regime_view, str) else None,
            "source": "analyst_memo",
            "is_llm_qualitative": True,
        }
    return {
        "regime_view": None,
        "source": "deterministic_compiler",
        "data_gap": _NO_FRESH_MEMO_GAP,
    }


def _strategy_a_research_handoff(
    *,
    allowed_buy: list[str],
    approved_extended: list[str],
    role_layer_by_ticker: dict[str, str],
    mode: str,
    disable_reason: str,
    why_not_enabled: str,
) -> dict[str, Any]:
    watch_only_by_role = _group_by_role(allowed_buy, role_layer_by_ticker)
    empty_by_role = {role: [] for role in BASE_ROLE_KEYS}

    no_action_hint = (
        f"{_NO_FRESH_MEMO_GAP} — compiled handoff is non-actionable; NEW_BUY not supported this run."
        if mode != COMPILATION_MODE_EVIDENCE_PLUS_MEMO
        else "compiled handoff is qualitative/report-only; NEW_BUY enablement is not decided here."
    )

    return {
        "handoff_version": HANDOFF_VERSION,
        "handoff_scope": HANDOFF_SCOPE,
        "not_order_instruction": True,
        "strategy_a_must_still_apply": list(_STRATEGY_A_MUST_STILL_APPLY),
        # Shortlist (eligible-to-buy) is always empty in R2D: the compiler never
        # authorizes a new buy. Every ticker is watch-only by its role.
        "base_shortlist_eligible_by_role": {role: [] for role in BASE_ROLE_KEYS},
        "base_watch_only_by_role": watch_only_by_role,
        "positive_delta_research_supported": [],
        "positive_delta_not_implied_for": list(allowed_buy),
        "replacement_ranking_by_role": watch_only_by_role,
        "rotation_handoff": [],
        "buy_side_no_action_hints": [no_action_hint],
        "extended_lane_downstream_gate": {
            "effective_allowed_extended_etf_tickers_this_run": [],
            "predecision_only_tickers": [],
            "proposed_only_tickers": [],
            "approved_but_excluded_tickers": list(approved_extended),
            "must_not_enter_strategy_a_effective_universe": list(approved_extended),
            "disable_reason": disable_reason,
            "why_not_enabled": why_not_enabled,
        },
        "sell_side_research_boundary": {
            "research_template_sell_decision_scope": "base_etf_research_only_unless_current_portfolio_sell_diagnostics_provided",
            "sell_side_full_diagnostic_inputs_present": False,
            "portfolio_snapshot_ltcg_lots_are_eligibility_not_sell_thesis": True,
            "sell_side_positive_action_supported_tickers": [],
            "sell_side_watch_only_tickers": list(allowed_buy),
            "sell_side_data_gap_note": "",
        },
        # Extra, non-authoritative report-only marker (validator ignores it).
        "compilation_non_actionable_reason": (
            _NO_FRESH_MEMO_REASON if mode != COMPILATION_MODE_EVIDENCE_PLUS_MEMO else "report_only_compiler_no_execution_authority"
        ),
    }


def _qualitative_context(memo: Mapping[str, Any]) -> dict[str, Any]:
    """Echo the memo's qualitative (non-authoritative) opinion for traceability."""
    return {
        "is_llm_generated": True,
        "note": "advisory qualitative context only; not an allowed universe, budget, or order intent.",
        "regime_view": memo.get("regime_view"),
        "key_risks": _string_or_list(memo.get("key_risks")),
        "opportunity_summary": memo.get("opportunity_summary"),
        "preferred_exposures": _string_or_list(memo.get("preferred_exposures")),
        "avoid_or_deprioritize": _string_or_list(memo.get("avoid_or_deprioritize")),
        "confidence": memo.get("confidence"),
    }


# --- metadata ----------------------------------------------------------------


def build_compiled_handoff_metadata(
    *,
    candidate: Mapping[str, Any],
    validation: Any,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    evidence_packet_path: str | Path | None = None,
    analyst_memo_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the report-only compiler metadata artifact."""
    mode = candidate.get("compilation_mode") if isinstance(candidate, Mapping) else None
    analyst_memo_present = analyst_memo is not None
    analyst_memo_valid = mode == COMPILATION_MODE_EVIDENCE_PLUS_MEMO
    emitted = [field_name for field_name in REQUIRED_TOP_LEVEL_FIELDS if field_name in candidate]
    missing = [field_name for field_name in REQUIRED_TOP_LEVEL_FIELDS if field_name not in candidate]
    return {
        "schema_version": METADATA_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "compilation_mode": mode,
        "analyst_memo_present": analyst_memo_present,
        "analyst_memo_valid": analyst_memo_valid,
        "source_evidence_packet": {
            "path": str(evidence_packet_path) if evidence_packet_path is not None else None,
            "schema_version": evidence_packet.get("schema_version") if isinstance(evidence_packet, Mapping) else None,
            "sha256": _sha256_of(evidence_packet),
        },
        "source_analyst_memo": {
            "present": analyst_memo_present,
            "path": str(analyst_memo_path) if analyst_memo_path is not None else None,
            "schema_version": analyst_memo.get("schema_version") if isinstance(analyst_memo, Mapping) else None,
            "sha256": _sha256_of(analyst_memo) if analyst_memo_present else None,
        },
        "compiled_candidate_valid": _validation_valid(validation),
        "required_top_level_fields_emitted": emitted,
        "missing_required_top_level_fields": missing,
        "generated_at": generated_at,
        "notes": (
            "Deterministic, report-only Step 1C compiler output. Not fed into "
            "research_degraded_mode_decision; does not change allowed_actions; "
            "evidence-only / invalid-memo modes never support NEW_BUY."
        ),
    }


# --- disk wrapper ------------------------------------------------------------


def write_compiled_research_handoff(
    *,
    candidate_path: str | Path,
    validation_path: str | Path,
    metadata_path: str | Path,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None = None,
    strategy_settings: Mapping[str, Any] | None = None,
    evidence_packet_path: str | Path | None = None,
    analyst_memo_path: str | Path | None = None,
    support_signals_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compile, validate, and write the report-only compiler artifacts.

    Returns a small summary dict (paths + mode + validity). Never feeds the
    degraded-mode decision. When ``support_signals_path`` is provided, also writes
    the R2E.3 report-only ``compiled_support_signals.json`` (non-authoritative;
    it never changes actionability or ``allowed_actions``).
    """
    from investment_orchestrator.common.io import write_json
    from investment_orchestrator.research.support_signals import (
        build_compiled_support_signals,
    )

    generated_at = (now or datetime.now(timezone.utc)).isoformat()
    candidate = compile_research_handoff(
        evidence_packet if isinstance(evidence_packet, Mapping) else {},
        analyst_memo,
        strategy_settings=strategy_settings,
    )
    validation = validate_research_handoff(candidate, strategy_settings=strategy_settings)
    metadata = build_compiled_handoff_metadata(
        candidate=candidate,
        validation=validation,
        evidence_packet=evidence_packet,
        analyst_memo=analyst_memo,
        evidence_packet_path=evidence_packet_path,
        analyst_memo_path=analyst_memo_path,
        generated_at=generated_at,
    )
    write_json(candidate_path, candidate)
    write_json(validation_path, research_handoff_validation_result_to_dict(validation))
    write_json(metadata_path, metadata)
    summary = {
        "compiled_research_handoff_candidate_path": str(candidate_path),
        "compiled_research_handoff_validation_path": str(validation_path),
        "compiled_research_handoff_metadata_path": str(metadata_path),
        "compilation_mode": candidate["compilation_mode"],
        "compiled_candidate_valid": str(_validation_valid(validation)),
    }
    if support_signals_path is not None:
        support_signals = build_compiled_support_signals(
            evidence_packet=evidence_packet,
            analyst_memo=analyst_memo,
            compilation_mode=candidate["compilation_mode"],
            generated_at=generated_at,
        )
        write_json(support_signals_path, support_signals)
        summary["compiled_support_signals_path"] = str(support_signals_path)
    return summary


# --- helpers -----------------------------------------------------------------


def _deterministic_role_inputs(
    strategy_settings: Mapping[str, Any] | None,
) -> tuple[dict[str, str], str | None]:
    """Return (ticker->role map, benchmark) from settings, both optional."""
    if not isinstance(strategy_settings, Mapping):
        return {}, None
    role_map: dict[str, str] = {}
    fallback = strategy_settings.get("ticker_role_fallback")
    if isinstance(fallback, Mapping):
        for ticker, role in fallback.items():
            if isinstance(ticker, str) and isinstance(role, str):
                role_map[ticker.strip().upper()] = role.strip()
    benchmark = strategy_settings.get("benchmark")
    benchmark = benchmark.strip().upper() if isinstance(benchmark, str) and benchmark.strip() else None
    return role_map, benchmark


def _role_layer_for(
    ticker: str,
    *,
    role_map: dict[str, str],
    satellite: set[str],
    benchmark: str | None,
) -> str:
    """Deterministically resolve a base role for a ticker (never from the memo).

    Priority: operator ticker_role_fallback (base roles only) > satellite-universe
    membership > benchmark match > diversified core buffer default.
    """
    mapped = role_map.get(ticker)
    if mapped in BASE_ROLE_KEYS:
        return mapped
    if ticker in satellite:
        return _ROLE_SECTOR
    if benchmark is not None and ticker == benchmark:
        return _ROLE_BENCHMARK
    return _ROLE_DIVERSIFIED


def _group_by_role(tickers: list[str], role_layer_by_ticker: dict[str, str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {role: [] for role in BASE_ROLE_KEYS}
    for ticker in tickers:
        role = role_layer_by_ticker.get(ticker, _ROLE_DIVERSIFIED)
        grouped.setdefault(role, []).append(ticker)
    return grouped


def _memo_views_by_ticker(memo: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    views: dict[str, Mapping[str, Any]] = {}
    rows = memo.get("ticker_relative_view")
    if not isinstance(rows, list):
        return views
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ticker = row.get("ticker")
        if isinstance(ticker, str) and ticker.strip():
            views[ticker.strip().upper()] = row
    return views


def _validation_valid(validation: Any) -> bool:
    if isinstance(validation, Mapping):
        return validation.get("valid") is True
    return getattr(validation, "valid", False) is True


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str) and item.strip():
            ticker = item.strip().upper()
            if ticker not in seen:
                seen.add(ticker)
                out.append(ticker)
    return out


def _string_or_list(value: Any) -> Any:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str):
        return value
    return None
