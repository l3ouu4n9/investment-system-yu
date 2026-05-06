"""Validation helpers for Daily Execution Check artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from investment_orchestrator.common.schema_validation import validate_artifact_schema
from investment_orchestrator.validators.strategy_settings import (
    is_near_edge_monitor_enabled,
    lowest_live_limit_is_evidence_only,
)


ACTION_VALUES = {
    "KEEP",
    "REPLACE",
    "CANCEL",
    "HOLD_FOR_WEEKLY_REVIEW",
    "DATA_GAP",
}
REASON_CODE_VALUES = {
    "execution_drift_within_tolerance",
    "execution_reanchor_required",
    "execution_state_cancel_required",
    "weekly_review_required",
    "daily_market_data_gap",
    "live_order_state_data_gap",
    "weekly_source_data_gap",
    "operator_override_hold",
    "operator_override_cancel",
    "manual_smoke_test_keep",
}
ACTION_REASON_CODE_VALUES = {
    "KEEP": {
        "execution_drift_within_tolerance",
        "manual_smoke_test_keep",
    },
    "REPLACE": {
        "execution_reanchor_required",
    },
    "CANCEL": {
        "execution_state_cancel_required",
        "operator_override_cancel",
    },
    "HOLD_FOR_WEEKLY_REVIEW": {
        "weekly_review_required",
        "operator_override_hold",
    },
    "DATA_GAP": {
        "daily_market_data_gap",
        "live_order_state_data_gap",
        "weekly_source_data_gap",
    },
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


def _operator_note_matches(note: Any, ticker: str, note_types: set[str]) -> bool:
    if isinstance(note, Mapping):
        note_ticker = _normalize_ticker(note.get("ticker", ""))
        note_type = str(note.get("note_type", "")).strip()
        return note_ticker == ticker and note_type in note_types
    text = str(note).lower()
    return ticker.lower() in text and any(note_type.lower() in text for note_type in note_types)


def _diagnostic_rows(precomputed_diagnostics: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(precomputed_diagnostics, Mapping):
        return {}
    rows = precomputed_diagnostics.get("diagnostics")
    if not isinstance(rows, list):
        return {}
    output: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ticker = _normalize_ticker(row.get("ticker", ""))
        if ticker:
            output[ticker] = row
    return output


def _evidence_sentences(text: str) -> list[str]:
    return [part.strip().lower() for part in re.split(r"[.;\n]+", text) if part.strip()]


def _forbidden_lowest_limit_comparison(source_evidence: str, tolerance_name: str) -> bool:
    comparison_terms = ("<=", ">=", "less than", "greater than", "within", "threshold", "cap", "compare")
    for sentence in _evidence_sentences(source_evidence):
        if "distance_to_lowest_live_limit_pct" not in sentence or tolerance_name not in sentence:
            continue
        if "not" in sentence or "不得" in sentence or "only" in sentence:
            continue
        if any(term in sentence for term in comparison_terms):
            return True
    return False


def _has_lowest_evidence_only_statement(source_evidence: str) -> bool:
    text = source_evidence.lower()
    return (
        "distance_to_lowest_live_limit_pct" in text
        and (
            "evidence only" in text
            or "evidence-only" in text
            or "not an action threshold" in text
        )
    )


def validate_daily_execution_actions(
    payload: Any,
    *,
    audited_decision_packet: Any | None = None,
    template4_orders_text: str | None = None,
    order_state_export_text: str | None = None,
    strategy_settings: Any | None = None,
    precomputed_diagnostics: Any | None = None,
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

    operator_notes = payload.get("operator_notes")
    _require(isinstance(operator_notes, list), "operator_notes must be a list.")
    diagnostic_rows = _diagnostic_rows(precomputed_diagnostics)
    near_edge_enabled = is_near_edge_monitor_enabled(strategy_settings)
    lowest_limit_evidence_only = lowest_live_limit_is_evidence_only(strategy_settings)

    for index, action_item in enumerate(actions):
        label = f"actions[{index}]"
        _require(isinstance(action_item, dict), f"{label} must be an object.")

        ticker = _normalize_ticker(action_item.get("ticker", ""))
        _require(ticker in weekly_allowlist, f"{label}.ticker {ticker!r} is not in weekly-approved ticker allowlist.")

        action = action_item.get("action")
        _require(action in ACTION_VALUES, f"{label}.action must be one of {sorted(ACTION_VALUES)}.")

        reason_code = action_item.get("reason_code")
        _require(
            reason_code in REASON_CODE_VALUES,
            f"{label}.reason_code must be one of {sorted(REASON_CODE_VALUES)}.",
        )
        _require(
            reason_code in ACTION_REASON_CODE_VALUES[action],
            f"{label}.reason_code {reason_code!r} is not valid for action {action!r}.",
        )
        if action == "KEEP":
            _require(
                reason_code in {"execution_drift_within_tolerance", "manual_smoke_test_keep"},
                f"{label}.reason_code must be execution_drift_within_tolerance for KEEP outside manual smoke tests.",
            )

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

        source_evidence = str(action_item.get("source_evidence", ""))
        _require(
            not _forbidden_lowest_limit_comparison(source_evidence, "anchor_drift_tolerance"),
            f"{label}.source_evidence must not compare anchor_drift_tolerance to distance_to_lowest_live_limit_pct.",
        )
        _require(
            not _forbidden_lowest_limit_comparison(
                source_evidence,
                "max_negative_distance_to_highest_live_limit_pct",
            ),
            (
                f"{label}.source_evidence must not compare "
                "max_negative_distance_to_highest_live_limit_pct to distance_to_lowest_live_limit_pct."
            ),
        )
        if lowest_limit_evidence_only:
            _require(
                _has_lowest_evidence_only_statement(source_evidence),
                (
                    f"{label}.source_evidence must state distance_to_lowest_live_limit_pct is "
                    "evidence-only / not an action threshold."
                ),
            )

        diagnostic = diagnostic_rows.get(ticker)
        if near_edge_enabled and diagnostic is not None:
            required_note_types: set[str] = set()
            if diagnostic.get("near_anchor_edge") is True:
                required_note_types.add("near_anchor_drift_edge")
            if diagnostic.get("near_highest_live_limit_edge") is True:
                required_note_types.add("near_highest_live_limit_edge")
            for note_type in required_note_types:
                _require(
                    any(_operator_note_matches(note, ticker, {note_type}) for note in operator_notes),
                    f"{label} computed {note_type} but operator_notes is missing a matching note.",
                )

    return payload
