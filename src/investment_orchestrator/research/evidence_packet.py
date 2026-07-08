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
from investment_orchestrator.research.active_research_anchor_registry import (
    OPERATOR_SOURCE_ID as BASELINE_ANCHOR_SOURCE_ID,
    SCHEMA_VERSION as BASELINE_ACTIVE_REGISTRY_SCHEMA_VERSION,
    active_anchor_registry_from_research_anchors_summary,
    compile_active_research_anchor_registry,
)
from investment_orchestrator.research.approval_registry_dual_read_diff import (
    build_approval_registry_dual_read_diff,
)
from investment_orchestrator.research.approval_registry_switch_readiness import (
    APPROVALS_SOURCE_ID,
    SWITCH_TARGET_APPROVALS,
    SWITCH_TARGET_BASELINE,
    SWITCH_TARGET_FAIL_CLOSED,
    evaluate_approval_registry_switch_readiness,
)
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    build_active_research_anchor_registry_with_approvals,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    validate_research_anchor_approvals,
)
from investment_orchestrator.research.research_anchor_revocation_manifest import (
    validate_research_anchor_revocations,
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
EMBEDDED_REGISTRY_SELECTION_SCHEMA_VERSION = "embedded_active_anchor_registry_selection_v1"
FAIL_CLOSED_EMPTY_REASON = "approval_registry_switch_fail_closed_empty"

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
    active_anchor_registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the deterministic evidence packet mapping (pure; never raises).

    Only deterministic / operator-controlled inputs are read. Missing inputs are
    recorded as explicit ``data_gaps`` entries. No value here is LLM-derived.

    ``research_anchors_summary`` (R2E.5a, report-only) is the already-built
    deterministic anchor summary; when absent it defaults to an unavailable
    ``research_anchors`` section + a ``research_anchors_missing`` DATA_GAP. It is
    kept for backward compatibility / diagnostics only and is **no longer the
    authoritative grounding source**.

    ``active_anchor_registry`` is the first-class, report-only **source of truth**
    that ``support_signals`` consumes for anchor grounding. In the Step 1 writer it
    is selected by the R2G-5c-2 readiness-gated baseline/approvals-inclusive
    selector. When not supplied to this pure builder it is derived deterministically
    from the ``research_anchors`` summary. It never changes permissions and cannot
    authorize a trade.
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
        # R2E.5a diagnostic view (no longer authoritative for support grounding).
        "research_anchors": anchors_summary,
        # R2G-3 authoritative grounding source-of-truth. Supplied (compiled from the
        # operator YAML with a real source hash) by write_evidence_packet, else
        # derived deterministically from the research_anchors summary above.
        "active_anchor_registry": (
            dict(active_anchor_registry)
            if isinstance(active_anchor_registry, Mapping)
            else active_anchor_registry_from_research_anchors_summary(anchors_summary)
        ),
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


# --- embedded active-registry selection ---------------------------------------


def build_embedded_active_anchor_registry_selection(
    *,
    anchors_path: Any,
    approvals_path: Any = None,
    allowed_universe: Any,
    today: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Freshly compile, evaluate readiness, and select the embedded registry.

    This is the R2G-5c-2 behavior switch, updated in R2G-5d-2 so the approvals
    branch is revocation-aware. It does not read any on-disk registry, readiness,
    revocation-validation, or dry-run diff JSON as authority. The baseline
    registry, approvals-inclusive registry, revocation validation, dual-read diff,
    readiness result, and selected embedded registry are all derived from the same
    in-memory input bytes.
    """
    try:
        baseline = compile_active_research_anchor_registry(
            anchors_path=anchors_path,
            allowed_universe=allowed_universe,
            today=today,
            generated_at=generated_at,
        )
        approvals_validation = validate_research_anchor_approvals(
            manifest_path=approvals_path,
            allowed_universe=allowed_universe,
            today=today,
            as_of_date=baseline.get("as_of_date") if isinstance(baseline, Mapping) else None,
            generated_at=generated_at,
        )
        revocations_validation = validate_research_anchor_revocations(
            manifest_path=approvals_path,
            allowed_universe=allowed_universe,
            today=today,
            as_of_date=baseline.get("as_of_date") if isinstance(baseline, Mapping) else None,
            generated_at=generated_at,
        )
        approvals = build_active_research_anchor_registry_with_approvals(
            baseline=baseline,
            approvals_validation=approvals_validation,
            revocations_validation=revocations_validation,
            generated_at=generated_at,
        )
        diff = build_approval_registry_dual_read_diff(
            baseline_registry=baseline,
            approvals_registry=approvals,
            generated_at=generated_at,
        )
        readiness = evaluate_approval_registry_switch_readiness(
            baseline_registry=baseline,
            approvals_registry=approvals,
            dual_read_diff=diff,
            current_research_anchors_sha256=_source_sha(baseline, BASELINE_ANCHOR_SOURCE_ID),
            current_research_anchor_approvals_sha256=_source_sha(approvals, APPROVALS_SOURCE_ID),
            approvals_source_present=_source_present(approvals, APPROVALS_SOURCE_ID),
            as_of_date=baseline.get("as_of_date") if isinstance(baseline, Mapping) else None,
            generated_at=generated_at,
        )
        selected, selected_source = _select_embedded_registry(
            baseline=baseline,
            approvals=approvals,
            readiness=readiness,
            generated_at=generated_at,
        )
        return {
            "schema_version": EMBEDDED_REGISTRY_SELECTION_SCHEMA_VERSION,
            "is_llm_generated": False,
            "report_only": True,
            "permission_effect": "none",
            "not_authorization": True,
            "not_execution_authorization": True,
            "generated_at": generated_at,
            "selected_source": selected_source,
            "selected_registry": selected,
            "baseline_registry": baseline,
            "approvals_registry": approvals,
            "dual_read_diff": diff,
            "readiness": readiness,
        }
    except Exception:  # noqa: BLE001 - evidence packet must fail closed, never raise
        selected = fail_closed_empty_active_anchor_registry(
            reason="embedded_registry_selection_internal_error",
            generated_at=generated_at,
        )
        return {
            "schema_version": EMBEDDED_REGISTRY_SELECTION_SCHEMA_VERSION,
            "is_llm_generated": False,
            "report_only": True,
            "permission_effect": "none",
            "not_authorization": True,
            "not_execution_authorization": True,
            "generated_at": generated_at,
            "selected_source": SWITCH_TARGET_FAIL_CLOSED,
            "selected_registry": selected,
            "baseline_registry": {},
            "approvals_registry": {},
            "dual_read_diff": {},
            "readiness": {
                "ready": False,
                "switch_target": SWITCH_TARGET_FAIL_CLOSED,
                "baseline_fallback_safe": False,
                "fail_closed_empty_required": True,
            },
        }


def fail_closed_empty_active_anchor_registry(
    *,
    reason: str = FAIL_CLOSED_EMPTY_REASON,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Consumable, intentionally empty registry: support_signals sees zero anchors."""
    return {
        "schema_version": BASELINE_ACTIVE_REGISTRY_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "compiler_version": "embedded_registry_selector_v1",
        "as_of_date": None,
        "generated_at": generated_at,
        "source_manifest": [],
        "active_anchors": [],
        "inactive_anchors": [],
        "counts": {"active": 0, "expired": 0, "revoked": 0, "invalid": 0, "superseded": 0},
        "registry_valid": True,
        "registry_blockers": [reason] if reason else [],
        "audit_trail": [{"event": "fail_closed_empty_selected", "reason": reason}],
        "notes": "Fail-closed empty embedded registry selected by R2G-5c-2 readiness gate; zero usable anchors.",
    }


def _select_embedded_registry(
    *,
    baseline: Mapping[str, Any],
    approvals: Mapping[str, Any],
    readiness: Mapping[str, Any],
    generated_at: str | None,
) -> tuple[dict[str, Any], str]:
    target = readiness.get("switch_target")
    if readiness.get("ready") is True and target == SWITCH_TARGET_APPROVALS:
        return approvals if isinstance(approvals, dict) else dict(approvals), SWITCH_TARGET_APPROVALS
    if target == SWITCH_TARGET_BASELINE and readiness.get("baseline_fallback_safe") is True:
        return baseline if isinstance(baseline, dict) else dict(baseline), SWITCH_TARGET_BASELINE
    return (
        fail_closed_empty_active_anchor_registry(generated_at=generated_at),
        SWITCH_TARGET_FAIL_CLOSED,
    )


def _source_sha(registry: Mapping[str, Any], source_id: str) -> str | None:
    for entry in _as_list(registry.get("source_manifest")):
        if isinstance(entry, Mapping) and entry.get("source_id") == source_id:
            sha = entry.get("sha256")
            return sha if isinstance(sha, str) else None
    return None


def _source_present(registry: Mapping[str, Any], source_id: str) -> bool:
    for entry in _as_list(registry.get("source_manifest")):
        if isinstance(entry, Mapping) and entry.get("source_id") == source_id:
            return entry.get("present") is True
    return False


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


# --- disk wrapper ------------------------------------------------------------


def build_evidence_packet_and_selection(
    *,
    strategy_settings: Mapping[str, Any] | None,
    portfolio_snapshot_text: str | None,
    portfolio_snapshot_path: str | Path | None = None,
    last_good_available: bool = False,
    last_good_metadata: Mapping[str, Any] | None = None,
    now_date: str | None = None,
    generated_at: str | None = None,
    source_artifacts: Mapping[str, str] | None = None,
    research_anchors_path: str | Path | None = None,
    research_anchor_approvals_path: str | Path | None = None,
    embedded_selection_out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the evidence packet (and capture the embedded selection) in memory.

    Pure build-only core of :func:`write_evidence_packet`: it never writes a file
    and never calls an LLM. Behavior is byte-identical to the pre-extraction
    inline build for the same ``generated_at`` — ``write_evidence_packet`` now
    delegates here and only adds the ``write_json``.

    It is extracted so the guarded S1A-11 evidence_packet disk writer can obtain
    the legacy/current packet in memory (to compare against the Step 1A candidate
    via ``compare_evidence_packet_runtime_parity``) without writing it first. The
    caller controls ``generated_at`` so the two lineages can share one wall-clock
    stamp and differ only where the comparator would flag it.

    ``embedded_selection_out``: when provided, the in-memory embedded registry
    selection is copied into it so the caller can persist the report-only
    selection witness. It changes no selection/packet/permission/gate/order-path
    behavior.
    """
    anchors_summary = None
    active_anchor_registry = None
    if research_anchors_path is not None:
        allowed_universe = _allowed_buy_from_settings(strategy_settings)
        anchors_summary = build_research_anchors_summary(
            research_anchors_path,
            allowed_universe=allowed_universe,
            today=now_date,
        )
        # R2G-5c-2: select the embedded grounding registry from the same fresh
        # in-memory compile that readiness evaluates. No on-disk registry,
        # readiness, or dry-run diff JSON is read as switch authority.
        selection = build_embedded_active_anchor_registry_selection(
            anchors_path=research_anchors_path,
            approvals_path=research_anchor_approvals_path,
            allowed_universe=allowed_universe,
            today=now_date,
            generated_at=generated_at,
        )
        active_anchor_registry = selection["selected_registry"]
        if embedded_selection_out is not None:
            embedded_selection_out.update(selection)
    return build_evidence_packet(
        strategy_settings=strategy_settings,
        portfolio_snapshot_text=portfolio_snapshot_text,
        portfolio_snapshot_path=portfolio_snapshot_path,
        last_good_available=last_good_available,
        last_good_metadata=last_good_metadata,
        now_date=now_date,
        generated_at=generated_at,
        source_artifacts=source_artifacts,
        research_anchors_summary=anchors_summary,
        active_anchor_registry=active_anchor_registry,
    )


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
    research_anchor_approvals_path: str | Path | None = None,
    now: datetime | None = None,
    embedded_selection_out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the evidence packet and write it as JSON. Returns the packet mapping.

    When ``research_anchors_path`` is provided, the deterministic (report-only)
    research-anchor summary is built from it (a missing file → an unavailable
    ``research_anchors`` section + DATA_GAP). The embedded grounding registry is
    selected from a fresh readiness-coupled baseline/approvals-inclusive compile.
    This can affect only report-only support-signal grounding; it never changes
    permissions, gates, or order paths.

    ``embedded_selection_out`` is a diagnostic-only capture: when provided, the
    in-memory embedded registry selection is copied into it so the caller can
    persist it for report-only parity comparison. It changes no selection,
    packet, permission, gate, or order-path behavior.
    """
    from investment_orchestrator.common.io import write_json

    generated_at = (now or datetime.now(timezone.utc)).isoformat()
    packet = build_evidence_packet_and_selection(
        strategy_settings=strategy_settings,
        portfolio_snapshot_text=portfolio_snapshot_text,
        portfolio_snapshot_path=portfolio_snapshot_path,
        last_good_available=last_good_available,
        last_good_metadata=last_good_metadata,
        now_date=now_date,
        generated_at=generated_at,
        source_artifacts=source_artifacts,
        research_anchors_path=research_anchors_path,
        research_anchor_approvals_path=research_anchor_approvals_path,
        embedded_selection_out=embedded_selection_out,
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


# --- S1A-10 evidence_packet shadow-parity hardening --------------------------
#
# Pure, deterministic, fail-closed helpers that compare a PRODUCTION evidence
# packet against a Step 1A-sourced evidence packet before the future S1A-11
# guarded disk-writer switch. They read no disk, call no LLM, write no files,
# touch no switch status / shadow diff, and change no runtime behavior. The
# in-memory packet handed to support_signals is never mutated (the normalizer
# deep-copies).
#
# Only ``generated_at`` is run-varying between the two lineages: the production
# writer stamps a wall-clock timestamp (threaded into the packet top level and
# the embedded active_anchor_registry), while a Step 1A recompute uses
# generated_at=None. Everything else in the runtime-relevant subtree must match
# exactly; an unknown ISO-8601 *datetime* string surfacing inside that subtree
# fails closed rather than being silently normalized.

# The ONLY field key whose value may be normalized for parity.
_PARITY_GENERATED_AT_KEY = "generated_at"
# Sentinel written in place of a normalized generated_at value.
_PARITY_NORMALIZED_SENTINEL = "<normalized_generated_at>"

# Top-level packet keys whose (generated_at-normalized) content must match
# EXACTLY for the two lineages to be runtime-equivalent. A mismatch here blocks
# the S1A-11 guard.
_RUNTIME_RELEVANT_PACKET_KEYS = (
    "schema_version",
    "source",
    "report_only",
    "is_llm_generated",
    "strategy_settings_hash",
    "universe",
    "budget_settings",
    "data_gaps",
    "active_anchor_registry",
)

# Recognizes an ISO-8601 *datetime* (date + "T" + at least HH:MM). Deliberately
# does NOT match bare dates (e.g. as_of_date "2026-06-28", valid_until,
# anchor_date_et) — those are operator/content date fields, never run-varying
# wall-clock timestamps, and must never be normalized or flagged.
def _looks_like_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str) or len(value) < 16:
        return False
    v = value.strip()
    if v[4:5] != "-" or v[7:8] != "-" or v[10:11] != "T" or v[13:14] != ":":
        return False
    return v[:4].isdigit() and v[5:7].isdigit() and v[8:10].isdigit() and v[11:13].isdigit()


def _deep_copy_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _deep_copy_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy_json(v) for v in value]
    return value


def _normalize_generated_at_in_place(node: Any, path: str, normalized_paths: list[str]) -> None:
    """Recursively replace every ``generated_at`` value with the sentinel."""
    if isinstance(node, dict):
        for key, value in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key == _PARITY_GENERATED_AT_KEY:
                node[key] = _PARITY_NORMALIZED_SENTINEL
                normalized_paths.append(child_path)
            else:
                _normalize_generated_at_in_place(value, child_path, normalized_paths)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _normalize_generated_at_in_place(value, f"{path}[{index}]", normalized_paths)


def _collect_unknown_runtime_timestamps(node: Any, path: str, hits: list[str]) -> None:
    """Flag ISO-datetime strings inside an ALREADY-normalized runtime subtree.

    generated_at has already been replaced by the sentinel, so any remaining
    ISO-datetime string here is an unknown run-varying field -> fail closed.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            _collect_unknown_runtime_timestamps(value, f"{path}.{key}" if path else str(key), hits)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _collect_unknown_runtime_timestamps(value, f"{path}[{index}]", hits)
    elif _looks_like_iso_datetime(node):
        hits.append(path)


def normalize_evidence_packet_for_parity(
    packet: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Return a generated_at-normalized deep copy of ``packet`` plus diagnostics.

    Pure and non-mutating: the input packet (including the runtime object handed
    to support_signals) is deep-copied before any normalization. ONLY
    ``generated_at`` values are replaced (recursively, wherever they appear) with
    a fixed sentinel; every other value — anchor content, per-anchor
    content_sha256, source_manifest hashes, as_of_date, registry_valid,
    fail-closed markers, blockers, revocations, universe, budget, data_gaps,
    schema_version — is left byte-for-byte intact. Diagnostics record the exact
    normalized paths and any unknown ISO-datetime field found inside the
    runtime-relevant subtree (which the comparator treats as fail-closed).
    """
    normalized: dict[str, Any] = _deep_copy_json(packet) if isinstance(packet, Mapping) else {}
    normalized_paths: list[str] = []
    _normalize_generated_at_in_place(normalized, "", normalized_paths)

    unknown_runtime_timestamp_fields: list[str] = []
    for key in _RUNTIME_RELEVANT_PACKET_KEYS:
        if key in normalized:
            _collect_unknown_runtime_timestamps(
                normalized[key], key, unknown_runtime_timestamp_fields
            )

    diagnostics = {
        "normalized_paths": sorted(normalized_paths),
        "normalization_allowlist": [_PARITY_GENERATED_AT_KEY],
        "unknown_runtime_timestamp_fields": sorted(unknown_runtime_timestamp_fields),
    }
    return normalized, sorted(normalized_paths), diagnostics


def _diff_paths(prod: Any, step1a: Any, path: str, out: list[dict[str, Any]]) -> None:
    """Record the first-level dotted paths where two JSON values differ."""
    if isinstance(prod, Mapping) and isinstance(step1a, Mapping):
        for key in sorted(set(prod) | set(step1a)):
            child = f"{path}.{key}" if path else str(key)
            if key not in prod:
                out.append({"path": child, "reason": "absent_in_production"})
            elif key not in step1a:
                out.append({"path": child, "reason": "absent_in_step1a"})
            else:
                _diff_paths(prod[key], step1a[key], child, out)
    elif isinstance(prod, list) and isinstance(step1a, list):
        if len(prod) != len(step1a):
            out.append(
                {"path": path, "reason": f"list_length_differs({len(prod)}!={len(step1a)})"}
            )
        else:
            for index, (a, b) in enumerate(zip(prod, step1a)):
                _diff_paths(a, b, f"{path}[{index}]", out)
    elif prod != step1a:
        out.append({"path": path, "reason": "value_differs"})


def compare_evidence_packet_runtime_parity(
    production_packet: Mapping[str, Any] | None,
    step1a_packet: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compare a production vs Step 1A evidence packet for runtime parity.

    Deterministic, fail-closed, side-effect-free. Both packets are
    generated_at-normalized (deep copies), then:

    * the runtime-relevant subtree (``active_anchor_registry`` recursively,
      ``strategy_settings_hash``, ``universe``, ``budget_settings``,
      ``data_gaps``, ``schema_version``, ``source``, ``report_only``,
      ``is_llm_generated``) must match EXACTLY -> ``subtree_match``;
    * any unknown ISO-datetime field inside that subtree forces
      ``subtree_match=False`` (fail-closed, never silently normalized);
    * every other top-level key (source_artifacts, last_good_research_summary,
      portfolio_snapshot_summary, research_anchors, strategy_settings_summary,
      market/event stubs, top-level generated_at) is compared exactly and any
      delta is recorded in ``report_only_differences`` — reported, non-blocking.

    Returns a diagnostics dict only; nothing consumes it as authority.
    """
    prod_norm, prod_paths, prod_diag = normalize_evidence_packet_for_parity(production_packet)
    step_norm, step_paths, step_diag = normalize_evidence_packet_for_parity(step1a_packet)

    unknown_runtime_timestamp_fields = sorted(
        set(prod_diag["unknown_runtime_timestamp_fields"])
        | set(step_diag["unknown_runtime_timestamp_fields"])
    )

    differences: list[dict[str, Any]] = []
    for key in _RUNTIME_RELEVANT_PACKET_KEYS:
        _diff_paths(prod_norm.get(key), step_norm.get(key), key, differences)

    report_only_keys = sorted(
        (set(prod_norm) | set(step_norm)) - set(_RUNTIME_RELEVANT_PACKET_KEYS)
    )
    report_only_differences: list[dict[str, Any]] = []
    for key in report_only_keys:
        _diff_paths(prod_norm.get(key), step_norm.get(key), key, report_only_differences)

    subtree_match = not differences and not unknown_runtime_timestamp_fields

    return {
        "schema_version": "evidence_packet_runtime_parity_v1",
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_order_input": True,
        "consumed_by_gates": False,
        "consumed_by_order_path": False,
        "safe_to_ignore": True,
        "subtree_match": subtree_match,
        "runtime_relevant_keys": list(_RUNTIME_RELEVANT_PACKET_KEYS),
        "normalization_allowlist": [_PARITY_GENERATED_AT_KEY],
        "normalized_paths": sorted(set(prod_paths) | set(step_paths)),
        "differences": differences,
        "report_only_differences": report_only_differences,
        "unknown_runtime_timestamp_fields": unknown_runtime_timestamp_fields,
    }
