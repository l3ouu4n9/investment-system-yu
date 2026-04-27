"""Validation helpers for Daily Execution Check artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from investment_orchestrator.common.schema_validation import validate_artifact_schema


ACTION_VALUES = {
    "KEEP",
    "REPLACE",
    "CANCEL",
    "HOLD_FOR_WEEKLY_REVIEW",
    "DATA_GAP",
}
FORBIDDEN_REPLACE_KEYS = {
    "new_budget",
    "added_budget",
    "additional_budget",
    "budget_increase",
    "target_budget_increase",
    "thesis_update",
    "new_thesis",
    "ranking_update",
    "alpha_ranking_update",
    "ranking_change",
    "role_update",
    "weekly_role_update",
    "new_ticker",
}
FIELD_TICKER_RE = re.compile(r"(?:^|\|\s*)ticker=([A-Za-z][A-Za-z0-9.\-]*)")
FIXED_ROW_TICKER_RE = re.compile(r"^\s*([A-Z][A-Z0-9.\-]{0,9})\s*\|")


class DailyExecutionActionsValidationError(ValueError):
    """Raised when DAILY_EXECUTION_ACTIONS fails repo-specific validation."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DailyExecutionActionsValidationError(message)


def _normalize_ticker(value: Any) -> str:
    return str(value).strip().upper()


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DailyExecutionActionsValidationError(f"Budget value is not numeric: {value!r}") from exc


def _tickers_from_audited_packet(payload: Any) -> set[str]:
    if not isinstance(payload, Mapping):
        return set()

    tickers: set[str] = set()
    for section_name in ("final_execution_plans", "final_sell_execution_plans"):
        rows = payload.get(section_name)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if row.get("compile_ready") is not True:
                continue
            ticker = _normalize_ticker(row.get("ticker", ""))
            if ticker:
                tickers.add(ticker)
    return tickers


def _tickers_from_template4_orders(text: str | None) -> set[str]:
    if not text:
        return set()
    return {_normalize_ticker(match.group(1)) for match in FIELD_TICKER_RE.finditer(text)}


def _tickers_from_order_state_export(text: str | None) -> set[str]:
    if not text:
        return set()

    tickers: set[str] = set()
    in_open_order_section = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "(2a) existing_buy_open_orders_summary":
            in_open_order_section = True
            continue
        if line.startswith("(2b) ") or line == "DEFERRED_NOT_YET_LIVE":
            in_open_order_section = False
        if not in_open_order_section or not line or line.startswith("TICKER |"):
            continue
        match = FIXED_ROW_TICKER_RE.match(line)
        if match:
            tickers.add(_normalize_ticker(match.group(1)))

    return tickers


def build_weekly_ticker_allowlist(
    *,
    audited_decision_packet: Any | None = None,
    template4_orders_text: str | None = None,
    order_state_export_text: str | None = None,
) -> set[str]:
    """Build the weekly-approved ticker allowlist from available weekly artifacts."""
    tickers = _tickers_from_audited_packet(audited_decision_packet)
    if tickers:
        return tickers

    tickers = _tickers_from_template4_orders(template4_orders_text)
    if tickers:
        return tickers

    return _tickers_from_order_state_export(order_state_export_text)


def _contains_forbidden_key(mapping: Mapping[str, Any]) -> str | None:
    for key, value in mapping.items():
        normalized_key = str(key).strip().lower()
        if normalized_key in FORBIDDEN_REPLACE_KEYS:
            return normalized_key
        if isinstance(value, Mapping):
            nested = _contains_forbidden_key(value)
            if nested is not None:
                return nested
    return None


def validate_daily_execution_actions(
    payload: Any,
    *,
    audited_decision_packet: Any | None = None,
    template4_orders_text: str | None = None,
    order_state_export_text: str | None = None,
) -> dict[str, Any]:
    """Validate DAILY_EXECUTION_ACTIONS payload against schema and weekly-source guardrails."""
    validate_artifact_schema(payload, schema_name="daily_execution_actions.schema.json")
    _require(isinstance(payload, dict), "daily_execution_actions.json must be a JSON object.")

    weekly_allowlist = build_weekly_ticker_allowlist(
        audited_decision_packet=audited_decision_packet,
        template4_orders_text=template4_orders_text,
        order_state_export_text=order_state_export_text,
    )
    _require(
        bool(weekly_allowlist),
        "Unable to build weekly-approved ticker allowlist from audited packet, Template4 orders, or ORDER_STATE_EXPORT.",
    )

    actions = payload.get("actions")
    _require(isinstance(actions, list), "actions must be a list.")
    if not actions:
        blocked_items = payload.get("blocked_items")
        operator_notes = payload.get("operator_notes")
        _require(
            bool(blocked_items) or bool(operator_notes),
            "actions is empty; blocked_items or operator_notes must explain why no rows were emitted.",
        )

    for index, action_item in enumerate(actions):
        label = f"actions[{index}]"
        _require(isinstance(action_item, dict), f"{label} must be an object.")

        ticker = _normalize_ticker(action_item.get("ticker", ""))
        _require(ticker in weekly_allowlist, f"{label}.ticker {ticker!r} is not in weekly-approved ticker allowlist.")

        action = action_item.get("action")
        _require(action in ACTION_VALUES, f"{label}.action must be one of {sorted(ACTION_VALUES)}.")

        _require(action_item.get("execution_only") is True, f"{label}.execution_only must be true.")
        _require(action_item.get("no_budget_increase") is True, f"{label}.no_budget_increase must be true.")
        _require(action_item.get("no_new_thesis") is True, f"{label}.no_new_thesis must be true.")
        _require(action_item.get("no_ranking_change") is True, f"{label}.no_ranking_change must be true.")

        if action not in {"DATA_GAP", "HOLD_FOR_WEEKLY_REVIEW"}:
            _require(
                action_item.get("weekly_intent_preserved") is True,
                f"{label}.weekly_intent_preserved must be true for {action}.",
            )

        replace_packet = action_item.get("replace_packet")
        if action == "REPLACE":
            _require(isinstance(replace_packet, dict), f"{label}.replace_packet is required for REPLACE.")
            forbidden_key = _contains_forbidden_key(action_item)
            _require(
                forbidden_key is None,
                f"{label} contains forbidden replacement field {forbidden_key!r}.",
            )
            packet_ticker = _normalize_ticker(replace_packet.get("ticker", ""))
            _require(packet_ticker == ticker, f"{label}.replace_packet.ticker must equal action ticker.")
            _require(replace_packet.get("same_ticker") is True, f"{label}.replace_packet.same_ticker must be true.")
            _require(
                replace_packet.get("preserve_weekly_role") is True,
                f"{label}.replace_packet.preserve_weekly_role must be true.",
            )
            _require(
                replace_packet.get("preserve_event_binding") is True,
                f"{label}.replace_packet.preserve_event_binding must be true.",
            )
            budget_delta = _decimal(replace_packet.get("budget_delta"))
            _require(budget_delta == Decimal("0"), f"{label}.replace_packet.budget_delta must be 0.")
            before = _decimal(replace_packet.get("remaining_budget_before"))
            after = _decimal(replace_packet.get("remaining_budget_after"))
            _require(after == before, f"{label}.replace_packet must preserve remaining budget.")
        else:
            _require(replace_packet is None, f"{label}.replace_packet must be null for non-REPLACE actions.")

    return payload
