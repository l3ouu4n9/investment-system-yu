"""Step 1A deterministic evidence-packet builder (R2B, report-only).

Builds ``artifacts/current/step1_research/evidence_packet.json`` from
**deterministic / operator-controlled inputs only** — strategy settings, the
portfolio snapshot, and the persisted last-known-good (LKG) research metadata.
It contains **no LLM-generated claim** (``is_llm_generated: false``), represents
missing data as explicit ``data_gaps`` entries rather than guessing, and is
strictly report-only: nothing here gates the pipeline, changes any permission,
or feeds the degraded-mode decision.

The core ``build_evidence_packet`` is a pure function (inputs in, mapping out;
never raises) so it is fully testable without disk. ``write_evidence_packet``
is the thin disk wrapper used by the Step 1 workflow.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
from typing import Any

from investment_orchestrator.parsers.portfolio_snapshot_existing_orders import (
    parse_existing_buy_open_orders_summary,
)
from investment_orchestrator.research.research_anchors import (
    ANCHORS_MISSING_DATA_GAP,
    build_research_anchors_summary,
)
from investment_orchestrator.state.last_good_research_handoff import (
    decision_relevant_settings,
    strategy_settings_hash,
)


SCHEMA_VERSION = "evidence_packet_v1"
SOURCE = "deterministic_inputs"

EVIDENCE_PACKET_REQUIRED_FIELDS = (
    "schema_version",
    "is_llm_generated",
    "generated_at",
    "source",
    "strategy_settings_hash",
    "strategy_settings_summary",
    "universe",
    "budget_settings",
    "portfolio_snapshot_summary",
    "last_good_research_summary",
    "market_metrics",
    "scheduled_events_deterministic",
    "research_anchors",
    "data_gaps",
    "source_artifacts",
)

# Qualitative analyst_memo (Step 1B) field names. They are LLM opinion outputs
# and must never appear in the deterministic evidence packet. ``data_gaps`` is
# intentionally NOT listed (it is a shared, neutral deterministic field).
LLM_MEMO_FIELD_NAMES = (
    "regime_view",
    "key_risks",
    "opportunity_summary",
    "ticker_relative_view",
    "preferred_exposures",
    "avoid_or_deprioritize",
    "scheduled_event_interpretation",
    "confidence",
    "source_notes",
)

_NO_MARKET_FEED_REASON = (
    "DATA_GAP: no deterministic market-metrics feed is wired into Step 1A; "
    "market metrics are not LLM-filled here."
)
_NO_EVENT_FEED_REASON = (
    "DATA_GAP: no deterministic scheduled-events calendar source is wired into "
    "Step 1A; scheduled events are not LLM-filled here."
)


# --- pure builder ------------------------------------------------------------


def build_evidence_packet(
    *,
    strategy_settings: Mapping[str, Any] | None,
    portfolio_snapshot_text: str | None,
    portfolio_snapshot_path: str | Path | None = None,
    last_good_available: bool = False,
    last_good_metadata: Mapping[str, Any] | None = None,
    now_date: str | None = None,
    generated_at: str | None = None,
    source_artifacts: Mapping[str, str] | None = None,
    research_anchors_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the deterministic evidence packet mapping (pure; never raises).

    Only deterministic / operator-controlled inputs are read. Missing inputs are
    recorded as explicit ``data_gaps`` entries. No value here is LLM-derived.

    ``research_anchors_summary`` (R2E.5a, report-only) is the already-built
    deterministic anchor summary; when absent it defaults to an unavailable
    ``research_anchors`` section + a ``research_anchors_missing`` DATA_GAP. Anchors
    are report-only: they never change permissions and are not yet consumed for
    support-signal acceptance.
    """
    data_gaps: list[dict[str, str]] = []
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else None
    if settings is None:
        data_gaps.append(
            {"field": "strategy_settings", "reason": "DATA_GAP: strategy settings unavailable / unparseable."}
        )

    core = _normalize_tickers((settings or {}).get("core_universe"))
    satellite = _normalize_tickers((settings or {}).get("satellite_universe"))
    approved_extended = _normalize_tickers((settings or {}).get("user_approved_extended_etf_static_list"))
    allowed_buy = _dedupe_preserve_order([*core, *satellite])
    if not allowed_buy:
        data_gaps.append(
            {"field": "universe.allowed_buy_tickers", "reason": "DATA_GAP: no core/satellite universe in settings."}
        )

    settings_hash = strategy_settings_hash(decision_relevant_settings(settings))

    if isinstance(research_anchors_summary, Mapping):
        anchors_summary: dict[str, Any] = dict(research_anchors_summary)
    else:
        anchors_summary = {
            "available": False,
            "data_gap": ANCHORS_MISSING_DATA_GAP,
            "consumed_for_support_acceptance": False,
            "permission_effect": "none",
        }
    if not anchors_summary.get("available"):
        data_gaps.append(
            {"field": "research_anchors", "reason": anchors_summary.get("data_gap", ANCHORS_MISSING_DATA_GAP)}
        )

    packet: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "generated_at": generated_at,
        "source": SOURCE,
        "strategy_settings_hash": settings_hash,
        "strategy_settings_summary": _strategy_settings_summary(settings),
        "universe": {
            "core_universe": core,
            "satellite_universe": satellite,
            "approved_extended_etf": approved_extended,
            "allowed_buy_tickers": allowed_buy,
            "role_source_by_ticker": _role_source_by_ticker(core, satellite, approved_extended),
        },
        "budget_settings": _budget_settings(settings, data_gaps),
        "portfolio_snapshot_summary": _portfolio_snapshot_summary(
            portfolio_snapshot_text, portfolio_snapshot_path, data_gaps
        ),
        "last_good_research_summary": _last_good_summary(
            last_good_available, last_good_metadata, settings_hash, core, satellite, now_date
        ),
        "market_metrics": {"available": False, "data_gap": _NO_MARKET_FEED_REASON},
        "scheduled_events_deterministic": {"available": False, "data_gap": _NO_EVENT_FEED_REASON},
        "research_anchors": anchors_summary,
        "data_gaps": data_gaps,
        "source_artifacts": dict(source_artifacts) if isinstance(source_artifacts, Mapping) else {},
        "report_only": True,
    }
    return packet


def _strategy_settings_summary(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    if settings is None:
        return {"available": False}
    return {
        "available": True,
        "as_of": settings.get("as_of"),
        "run_timestamp_et": settings.get("run_timestamp_et"),
        "core_universe": _normalize_tickers(settings.get("core_universe")),
        "satellite_universe": _normalize_tickers(settings.get("satellite_universe")),
        "user_approved_extended_etf_static_list": _normalize_tickers(
            settings.get("user_approved_extended_etf_static_list")
        ),
        "max_new_tickers_per_week": settings.get("max_new_tickers_per_week"),
        "hard_cap_open_orders_budget": _budget_value(settings.get("hard_cap_open_orders_budget")),
        "target_new_buy_budget_this_run": _budget_value(settings.get("target_new_buy_budget_this_run")),
        "extended_etf_constraints": settings.get("extended_etf_constraints"),
        "active_shortlist_size_rule": settings.get("active_shortlist_size_rule"),
    }


def _budget_settings(settings: Mapping[str, Any] | None, data_gaps: list[dict[str, str]]) -> dict[str, Any]:
    hard_cap = _budget_value((settings or {}).get("hard_cap_open_orders_budget"))
    target = _budget_value((settings or {}).get("target_new_buy_budget_this_run"))
    max_new = (settings or {}).get("max_new_tickers_per_week")
    if hard_cap is None:
        data_gaps.append(
            {"field": "budget_settings.hard_cap_open_orders_budget", "reason": "DATA_GAP: hard cap not set in settings."}
        )
    if target is None:
        data_gaps.append(
            {"field": "budget_settings.target_new_buy_budget_this_run", "reason": "DATA_GAP: per-run new-buy budget not set in settings."}
        )
    return {
        "hard_cap_open_orders_budget": hard_cap,
        "target_new_buy_budget_this_run": target,
        "max_new_tickers_per_week": max_new if isinstance(max_new, (int, Mapping)) and not isinstance(max_new, bool) else None,
    }


def _portfolio_snapshot_summary(
    text: str | None,
    path: str | Path | None,
    data_gaps: list[dict[str, str]],
) -> dict[str, Any]:
    available = isinstance(text, str) and text.strip() != ""
    summary: dict[str, Any] = {
        "available": available,
        "path": str(path) if path is not None else None,
        "size_bytes": len(text.encode("utf-8")) if isinstance(text, str) else None,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if isinstance(text, str) else None,
    }
    if not available:
        data_gaps.append(
            {"field": "portfolio_snapshot", "reason": "DATA_GAP: portfolio snapshot missing or empty."}
        )
        summary["existing_buy_open_orders"] = {"section_present": False, "ticker_count": 0, "tickers": [], "total_budget": None}
    else:
        # Reliable structured parse: section (2a) only.
        parsed = parse_existing_buy_open_orders_summary(text)
        budgets = [o.budget for o in parsed.orders.values() if o.budget is not None]
        total_budget = sum(budgets, Decimal("0")) if budgets else None
        summary["existing_buy_open_orders"] = {
            "section_present": parsed.section_present,
            "ticker_count": len(parsed.orders),
            "tickers": sorted(parsed.orders.keys()),
            "total_budget": str(total_budget) if total_budget is not None else None,
            "data_gap_tickers": sorted(t for t, o in parsed.orders.items() if o.data_gap),
        }

    # No reliable structured parser exists for holdings (1) / sell orders (2b) /
    # LTCG lots (3); do NOT parse brittle free text. Mark them explicit DATA_GAPs.
    for section_key, label in (
        ("current_holdings", "section (1) current_holdings_base"),
        ("sell_open_orders", "section (2b) sell_open_orders"),
        ("ltcg_sellable_lots", "section (3) LTCG_ELIGIBLE_SELLABLE"),
    ):
        summary[section_key] = {
            "structured_parse_available": False,
            "data_gap": f"DATA_GAP: no deterministic structured parser for {label} in Step 1A.",
        }
    return summary


def _last_good_summary(
    available: bool,
    metadata: Mapping[str, Any] | None,
    current_settings_hash: str | None,
    core: list[str],
    satellite: list[str],
    now_date: str | None,
) -> dict[str, Any]:
    if not available or not isinstance(metadata, Mapping):
        return {"available": False}
    as_of = metadata.get("source_as_of_date")
    lg_hash = metadata.get("strategy_settings_hash")
    lg_universe = metadata.get("universe") if isinstance(metadata.get("universe"), Mapping) else {}
    lg_core = _normalize_tickers(lg_universe.get("core_universe"))
    lg_satellite = _normalize_tickers(lg_universe.get("satellite_universe"))
    return {
        "available": True,
        "source_as_of_date": as_of if isinstance(as_of, str) else None,
        "age_days": _age_days(now_date, as_of if isinstance(as_of, str) else None),
        "strategy_settings_hash_match": (
            (current_settings_hash == lg_hash)
            if (current_settings_hash is not None and isinstance(lg_hash, str))
            else None
        ),
        "universe_match": ((set(core) | set(satellite)) == (set(lg_core) | set(lg_satellite))) if (core or satellite or lg_core or lg_satellite) else None,
        "source_run_id": metadata.get("source_run_id"),
    }


# --- invariant checker -------------------------------------------------------


def check_evidence_packet_invariants(packet: Any) -> list[str]:
    """Return a list of invariant violations (empty list = OK). Report-only.

    Guards the deterministic contract: ``is_llm_generated`` is False, required
    top-level fields exist, ``data_gaps`` is a list, universe tickers are
    normalized non-empty strings, budget fields are present (deterministic value
    or explicit None), and no LLM analyst_memo field names appear at top level.
    """
    problems: list[str] = []
    if not isinstance(packet, Mapping):
        return ["evidence packet is not a JSON object."]

    if packet.get("is_llm_generated") is not False:
        problems.append("is_llm_generated must be exactly False.")

    for field_name in EVIDENCE_PACKET_REQUIRED_FIELDS:
        if field_name not in packet:
            problems.append(f"missing required field: {field_name}")

    if not isinstance(packet.get("data_gaps"), list):
        problems.append("data_gaps must be a list.")

    for memo_field in LLM_MEMO_FIELD_NAMES:
        if memo_field in packet:
            problems.append(f"LLM analyst_memo field must not appear in evidence packet: {memo_field}")

    universe = packet.get("universe")
    if isinstance(universe, Mapping):
        for key in ("core_universe", "satellite_universe", "approved_extended_etf", "allowed_buy_tickers"):
            value = universe.get(key)
            if not isinstance(value, list):
                problems.append(f"universe.{key} must be a list.")
                continue
            for ticker in value:
                if not isinstance(ticker, str) or ticker != ticker.strip().upper() or ticker == "":
                    problems.append(f"universe.{key} contains a non-normalized/empty ticker: {ticker!r}")
    else:
        problems.append("universe must be an object.")

    budget = packet.get("budget_settings")
    if isinstance(budget, Mapping):
        for key in ("hard_cap_open_orders_budget", "target_new_buy_budget_this_run", "max_new_tickers_per_week"):
            if key not in budget:
                problems.append(f"budget_settings.{key} must be present (value or explicit null).")
    else:
        problems.append("budget_settings must be an object.")

    return problems


# --- disk wrapper ------------------------------------------------------------


def write_evidence_packet(
    *,
    output_path: str | Path,
    strategy_settings: Mapping[str, Any] | None,
    portfolio_snapshot_text: str | None,
    portfolio_snapshot_path: str | Path | None = None,
    last_good_available: bool = False,
    last_good_metadata: Mapping[str, Any] | None = None,
    now_date: str | None = None,
    source_artifacts: Mapping[str, str] | None = None,
    research_anchors_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the evidence packet and write it as JSON. Returns the packet mapping.

    When ``research_anchors_path`` is provided, the deterministic (report-only)
    research-anchor summary is built from it (a missing file → an unavailable
    ``research_anchors`` section + DATA_GAP) and embedded in the packet. Anchors
    never change permissions and are not consumed for support acceptance (R2E.5a).
    """
    from investment_orchestrator.common.io import write_json

    generated_at = (now or datetime.now(timezone.utc)).isoformat()
    anchors_summary = None
    if research_anchors_path is not None:
        anchors_summary = build_research_anchors_summary(
            research_anchors_path,
            allowed_universe=_allowed_buy_from_settings(strategy_settings),
            today=now_date,
        )
    packet = build_evidence_packet(
        strategy_settings=strategy_settings,
        portfolio_snapshot_text=portfolio_snapshot_text,
        portfolio_snapshot_path=portfolio_snapshot_path,
        last_good_available=last_good_available,
        last_good_metadata=last_good_metadata,
        now_date=now_date,
        generated_at=generated_at,
        source_artifacts=source_artifacts,
        research_anchors_summary=anchors_summary,
    )
    write_json(output_path, packet)
    return packet


def _allowed_buy_from_settings(strategy_settings: Mapping[str, Any] | None) -> list[str]:
    """Deterministic base buy universe (core ∪ satellite) used to scope anchors."""
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else {}
    core = _normalize_tickers(settings.get("core_universe"))
    satellite = _normalize_tickers(settings.get("satellite_universe"))
    return _dedupe_preserve_order([*core, *satellite])


# --- helpers -----------------------------------------------------------------


def _normalize_tickers(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip().upper())
    return _dedupe_preserve_order(out)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _role_source_by_ticker(
    core: list[str], satellite: list[str], approved_extended: list[str]
) -> dict[str, str]:
    """Map each ticker to its deterministic role source (core > satellite > approved_extended)."""
    mapping: dict[str, str] = {}
    for ticker in approved_extended:
        mapping[ticker] = "approved_extended"
    for ticker in satellite:
        mapping[ticker] = "satellite"
    for ticker in core:  # highest precedence (matches per-bucket "in-both -> base")
        mapping[ticker] = "core"
    return mapping


def _budget_value(value: Any) -> str | int | float | None:
    """Copy a budget setting deterministically; return None when missing/unparseable."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            Decimal(value.strip().replace(",", ""))
        except InvalidOperation:
            return None
        return value
    return None


def _age_days(now_date: Any, as_of_date: Any) -> int | None:
    now = _parse_date(now_date)
    as_of = _parse_date(as_of_date)
    if now is None or as_of is None:
        return None
    return (now - as_of).days


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None
