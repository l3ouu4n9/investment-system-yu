"""Step 1A deterministic research-anchor parser / validator (R2E.5a, report-only).

A **research anchor** is a deterministic, operator-authored, dated, ticker-scoped
evidence item (structural theme or scheduled event) that a *future* analyst-memo
claim may **cite** to ground a 12m+ buy thesis. It supplies the structure the
strict validator's actionable-row contract needs (`primary_anchor_event_id` /
`primary_anchor_date_et` / `event_id_refs` / `structural_theme_refs`) — it is a
**citation target, never authorization**.

This module is strictly **report-only** in R2E.5a: it parses / validates
`inputs/current/research_anchors.yaml` and summarizes it into the deterministic
`evidence_packet.json`. It is **not** consumed for support-signal acceptance,
does **not** make any compiled row actionable, and never changes
`allowed_actions`, the availability state, or any gate. The parser never trusts
its input — every rule is enforced deterministically and a violation marks the
file / anchor invalid (it is simply not usable), never a crash.

Distinct from the daily-execution *price-baseline* anchor
(`anchor_baseline_last_close` in ``market/build_anchor_drift_snapshot.py``); that
is an unrelated concept. This module is the *research* anchor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterator

import yaml


SCHEMA_VERSION = "research_anchors_v1"

ANCHOR_TYPES = (
    "structural_theme",
    "scheduled_macro_event",
    "scheduled_earnings_event",
    "scheduled_rebalance_event",
)
SOURCE_TYPES = ("operator",)
CONFIDENCE_VALUES = ("low", "medium", "high")

REQUIRED_ANCHOR_FIELDS = (
    "anchor_id",
    "anchor_type",
    "applicable_tickers",
    "anchor_date_et",
    "valid_from",
    "valid_until",
    "source_type",
    "confidence_floor",
)

# Any key containing one of these substrings implies budget / sizing authority an
# anchor must never carry. The research_anchors_v1 schema contains none of these.
FORBIDDEN_KEY_SUBSTRINGS = ("budget", "cap", "allocation")

# Keys that would make an anchor an authoritative action / order intent. Reject.
FORBIDDEN_KEYS = (
    "final_action",
    "order_intent",
    "allowed_actions",
    "new_buy",
    "order_compilation",
    "buy_order",
    "sell_order",
    "order",
    "orders",
    "order_sizing",
    "order_instruction",
    "execution_authorization",
    "authorize",
    "authorize_execution",
    "compile_ready",
)

# Authoritative action tokens that must not appear as a standalone scalar value.
FORBIDDEN_ACTION_VALUE_TOKENS = (
    "new_buy",
    "order_compilation",
    "buy_order",
    "sell_order",
    "order_instruction",
)

# Deterministic DATA_GAP marker when no anchor file exists (mirrors the
# market_metrics / scheduled_events pattern in the evidence packet).
ANCHORS_MISSING_DATA_GAP = (
    "DATA_GAP: research_anchors_missing (no inputs/current/research_anchors.yaml; "
    "support signals stay qualitative_support_only)."
)


@dataclass(frozen=True)
class ResearchAnchorsResult:
    """Outcome of parsing + validating a research-anchors payload. Report-only."""

    present: bool
    valid: bool
    schema_version: str | None
    as_of_date: str | None
    anchors: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    parse_error: str | None = None


# --- parse + validate (pure; never raises) -----------------------------------


def validate_research_anchors(
    payload: Any,
    *,
    allowed_universe: Any,
    today: Any = None,
) -> ResearchAnchorsResult:
    """Validate a decoded research-anchors payload (pure; never raises).

    ``allowed_universe`` is the deterministic base buy universe
    (``allowed_buy_tickers``); an anchor may only apply to in-universe tickers. In
    v1, extended-only tickers (not in the base universe) are therefore rejected.
    """
    universe = _normalize_ticker_set(allowed_universe)
    today_date = _to_date(today)
    errors: list[str] = []

    if not isinstance(payload, Mapping):
        return ResearchAnchorsResult(
            present=True,
            valid=False,
            schema_version=None,
            as_of_date=None,
            anchors=[],
            errors=["research_anchors top-level must be a mapping/object."],
        )

    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r} (got {schema_version!r}).")

    if payload.get("is_llm_generated") is not False:
        errors.append("is_llm_generated must be exactly false (anchors are operator-authored).")

    as_of_date = payload.get("as_of_date") if isinstance(payload.get("as_of_date"), str) else None

    # Forbidden keys / tokens anywhere (defense in depth; anchors never authorize).
    for raw_key in _iter_keys(payload):
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip().lower()
        if key in {k.lower() for k in FORBIDDEN_KEYS}:
            errors.append(f"forbidden execution-authority/order-intent key present: {raw_key!r}.")
        if any(sub in key for sub in FORBIDDEN_KEY_SUBSTRINGS):
            errors.append(f"forbidden budget/sizing key present (implies authority): {raw_key!r}.")
    for value in _iter_string_values(payload):
        if value.strip().lower() in FORBIDDEN_ACTION_VALUE_TOKENS:
            errors.append(f"forbidden authoritative action token used as a value: {value!r}.")

    anchors_value = payload.get("anchors")
    anchors_eval: list[dict[str, Any]] = []
    if not isinstance(anchors_value, list):
        errors.append("anchors must be a list.")
    else:
        seen_ids: set[str] = set()
        for index, anchor in enumerate(anchors_value):
            evaluated = _evaluate_anchor(anchor, index=index, universe=universe, today=today_date)
            anchor_id = evaluated.get("anchor_id")
            if isinstance(anchor_id, str) and anchor_id:
                if anchor_id in seen_ids:
                    evaluated["problems"].append("duplicate anchor_id.")
                    evaluated["valid"] = False
                    errors.append(f"duplicate anchor_id: {anchor_id!r}.")
                seen_ids.add(anchor_id)
            anchors_eval.append(evaluated)

    overall_valid = not errors and all(a["valid"] for a in anchors_eval)
    return ResearchAnchorsResult(
        present=True,
        valid=overall_valid,
        schema_version=schema_version if isinstance(schema_version, str) else None,
        as_of_date=as_of_date,
        anchors=anchors_eval,
        errors=errors,
    )


def _evaluate_anchor(
    anchor: Any,
    *,
    index: int,
    universe: set[str],
    today: date | None,
) -> dict[str, Any]:
    """Evaluate one anchor deterministically. Returns a report dict (never raises)."""
    problems: list[str] = []
    if not isinstance(anchor, Mapping):
        return {
            "anchor_id": None,
            "valid": False,
            "stale": False,
            "usable": False,
            "problems": [f"anchors[{index}] must be an object."],
        }

    for field_name in REQUIRED_ANCHOR_FIELDS:
        if field_name not in anchor:
            problems.append(f"missing required field: {field_name}.")

    anchor_id_raw = anchor.get("anchor_id")
    anchor_id = anchor_id_raw.strip() if isinstance(anchor_id_raw, str) and anchor_id_raw.strip() else None
    if anchor_id is None:
        problems.append("anchor_id must be a non-empty string.")

    anchor_type = anchor.get("anchor_type")
    if anchor_type not in ANCHOR_TYPES:
        problems.append(f"anchor_type must be one of {list(ANCHOR_TYPES)} (got {anchor_type!r}).")

    source_type = anchor.get("source_type")
    if source_type not in SOURCE_TYPES:
        problems.append(f"source_type must be one of {list(SOURCE_TYPES)} (got {source_type!r}).")

    confidence_floor = anchor.get("confidence_floor")
    if not (isinstance(confidence_floor, str) and confidence_floor.strip().lower() in CONFIDENCE_VALUES):
        problems.append(f"confidence_floor must be one of {list(CONFIDENCE_VALUES)} (got {confidence_floor!r}).")

    tickers = _normalize_ticker_list(anchor.get("applicable_tickers"))
    if not isinstance(anchor.get("applicable_tickers"), list) or not tickers:
        problems.append("applicable_tickers must be a non-empty list of tickers.")
    else:
        out_of_universe = [t for t in tickers if t not in universe]
        for ticker in out_of_universe:
            problems.append(
                f"applicable ticker {ticker!r} is outside the deterministic allowed universe "
                "(v1 anchors are base-universe only; extended ETFs are not admissible)."
            )

    anchor_date = _to_date(anchor.get("anchor_date_et"))
    valid_from = _to_date(anchor.get("valid_from"))
    valid_until = _to_date(anchor.get("valid_until"))
    for label, value, raw in (
        ("anchor_date_et", anchor_date, anchor.get("anchor_date_et")),
        ("valid_from", valid_from, anchor.get("valid_from")),
        ("valid_until", valid_until, anchor.get("valid_until")),
    ):
        if raw is not None and value is None:
            problems.append(f"{label} must be an ISO date (YYYY-MM-DD); got {raw!r}.")
    if valid_from is not None and valid_until is not None and valid_from > valid_until:
        problems.append("valid_from must not be after valid_until.")

    blocks_if_stale = anchor.get("blocks_if_stale", True)
    if not isinstance(blocks_if_stale, bool):
        blocks_if_stale = True

    stale = bool(today is not None and valid_until is not None and valid_until < today)
    valid = not problems
    usable = valid and not (stale and blocks_if_stale)

    return {
        "anchor_id": anchor_id,
        "anchor_type": anchor_type if anchor_type in ANCHOR_TYPES else None,
        "applicable_tickers": tickers,
        "anchor_date_et": anchor.get("anchor_date_et") if isinstance(anchor.get("anchor_date_et"), str) else None,
        "valid_from": anchor.get("valid_from") if isinstance(anchor.get("valid_from"), str) else None,
        "valid_until": anchor.get("valid_until") if isinstance(anchor.get("valid_until"), str) else None,
        "source_type": source_type if source_type in SOURCE_TYPES else None,
        "confidence_floor": confidence_floor.strip().lower()
        if isinstance(confidence_floor, str) and confidence_floor.strip().lower() in CONFIDENCE_VALUES
        else None,
        "summary": anchor.get("summary") if isinstance(anchor.get("summary"), str) else None,
        "blocks_if_stale": blocks_if_stale,
        "valid": valid,
        "stale": stale,
        "usable": usable,
        "problems": problems,
    }


# --- disk load + summary -----------------------------------------------------


def load_research_anchors(
    path: Any,
    *,
    allowed_universe: Any,
    today: Any = None,
) -> ResearchAnchorsResult | None:
    """Read + validate the anchors YAML from disk. ``None`` when the file is absent/empty.

    Never raises: unreadable / malformed YAML yields a present-but-invalid result
    with a ``parse_error``.
    """
    from investment_orchestrator.common.io import file_exists, read_text

    if path is None or not file_exists(path):
        return None
    try:
        text = read_text(path)
    except Exception:  # noqa: BLE001 - report-only: unreadable file treated as absent
        return None
    if not text.strip():
        return None
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return ResearchAnchorsResult(
            present=True,
            valid=False,
            schema_version=None,
            as_of_date=None,
            anchors=[],
            errors=[f"research_anchors is not valid YAML: {exc}"],
            parse_error=str(exc),
        )
    return validate_research_anchors(payload, allowed_universe=allowed_universe, today=today)


def summarize_research_anchors(
    result: ResearchAnchorsResult,
    *,
    path: Any = None,
) -> dict[str, Any]:
    """Build the report-only ``research_anchors`` summary for the evidence packet."""
    anchors = result.anchors
    invalid = sum(1 for a in anchors if not a.get("valid"))
    stale = sum(1 for a in anchors if a.get("valid") and a.get("stale"))
    valid_fresh = sum(1 for a in anchors if a.get("valid") and not a.get("stale"))
    return {
        "available": True,
        "path": str(path) if path is not None else None,
        "schema_version": result.schema_version,
        "as_of_date": result.as_of_date,
        "valid": result.valid,
        "anchor_count": len(anchors),
        "valid_anchor_count": valid_fresh,
        "stale_anchor_count": stale,
        "invalid_anchor_count": invalid,
        "anchors": anchors,
        "errors": list(result.errors),
        "parse_error": result.parse_error,
        # Explicit: anchors are not yet consumed for support-signal acceptance.
        "consumed_for_support_acceptance": False,
        "permission_effect": "none",
    }


def build_research_anchors_summary(
    path: Any,
    *,
    allowed_universe: Any,
    today: Any = None,
) -> dict[str, Any]:
    """Convenience: load anchors and summarize; a missing file → available:false + DATA_GAP."""
    result = load_research_anchors(path, allowed_universe=allowed_universe, today=today)
    if result is None:
        return {
            "available": False,
            "path": str(path) if path is not None else None,
            "data_gap": ANCHORS_MISSING_DATA_GAP,
            "consumed_for_support_acceptance": False,
            "permission_effect": "none",
        }
    return summarize_research_anchors(result, path=path)


# --- helpers -----------------------------------------------------------------


def _iter_keys(obj: Any) -> Iterator[Any]:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            yield key
            yield from _iter_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_keys(item)


def _iter_string_values(obj: Any) -> Iterator[str]:
    if isinstance(obj, Mapping):
        for value in obj.values():
            yield from _iter_string_values(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_string_values(item)
    elif isinstance(obj, str):
        yield obj


def _normalize_ticker_list(value: Any) -> list[str]:
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


def _normalize_ticker_set(value: Any) -> set[str]:
    if isinstance(value, (set, frozenset)):
        return {str(t).strip().upper() for t in value if isinstance(t, str) and t.strip()}
    return set(_normalize_ticker_list(value if isinstance(value, list) else list(value or [])))


def _to_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None
