"""Validation helpers for core ETF deployment diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DEPLOYMENT_STATUS_VALUES = {
    "adequately_deployed",
    "watch_near_underdeployment",
    "underdeployment_review_required",
}
WEEKLY_ALLOWED_RESPONSE_VALUES = {
    "keep_existing",
    "same_budget_reanchor",
    "new_budget_if_hard_cap_and_strategy_allow",
    "hold_no_new_budget_with_explicit_reason",
}
KEEP_OR_HOLD_ACTIONS = {
    "KEEP_EXISTING",
    "HOLD_NO_NEW_BUDGET",
    "keep_existing",
    "hold_no_new_budget_with_explicit_reason",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _as_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _row_action(row: Any) -> str:
    if not isinstance(row, Mapping):
        return ""
    for key in ("final_action", "action", "action_draft", "weekly_action", "order_intent"):
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _row_why(row: Any) -> str:
    if not isinstance(row, Mapping):
        return ""
    for key in ("why", "explicit_why", "final_why", "reason", "audit_reason"):
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _has_underdeployment_blocker(payload: Mapping[str, Any]) -> bool:
    for key in ("compiler_blockers", "audit_fail_reasons", "blockers"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            haystack = str(item).lower()
            if "underdeployment" in haystack or "core deployment" in haystack:
                return True
    return False


def _final_rows_for_ticker(payload: Mapping[str, Any], ticker: str, *, step: str) -> list[Any]:
    section_names = ["execution_plan_drafts_8_5"] if step == "strategy_a" else ["final_execution_plans"]
    rows: list[Any] = []
    for section_name in section_names:
        section = payload.get(section_name)
        if not isinstance(section, list):
            continue
        for row in section:
            if isinstance(row, Mapping) and _as_ticker(row.get("ticker")) == ticker:
                rows.append(row)
    return rows


def validate_core_deployment_diagnostics(
    payload: Mapping[str, Any],
    *,
    step: str,
) -> None:
    """Validate optional core_deployment_diagnostics for Strategy A or B packets."""
    diagnostics = payload.get("core_deployment_diagnostics")
    if diagnostics is None:
        return

    _require(isinstance(diagnostics, list), "core_deployment_diagnostics must be a list when present.")

    required_keys = {
        "ticker",
        "role_layer",
        "current_holding_shares",
        "existing_open_order_budget",
        "compiled_open_order_notional",
        "residual_cash_not_allocated",
        "highest_live_limit",
        "current_last_close",
        "distance_to_highest_live_limit_pct",
        "anchor_drift_pct",
        "near_edge_flags",
        "deployment_status",
        "weekly_allowed_responses",
        "why",
    }

    for index, diagnostic in enumerate(diagnostics):
        label = f"core_deployment_diagnostics[{index}]"
        _require(isinstance(diagnostic, Mapping), f"{label} must be an object.")
        missing = [key for key in required_keys if key not in diagnostic]
        _require(not missing, f"{label} is missing required keys: {', '.join(missing)}")

        ticker = _as_ticker(diagnostic.get("ticker"))
        _require(bool(ticker), f"{label}.ticker must be non-empty.")
        _require(
            diagnostic.get("deployment_status") in DEPLOYMENT_STATUS_VALUES,
            f"{label}.deployment_status must be one of {sorted(DEPLOYMENT_STATUS_VALUES)}.",
        )
        responses = diagnostic.get("weekly_allowed_responses")
        _require(isinstance(responses, list), f"{label}.weekly_allowed_responses must be a list.")
        invalid_responses = [item for item in responses if item not in WEEKLY_ALLOWED_RESPONSE_VALUES]
        _require(
            not invalid_responses,
            f"{label}.weekly_allowed_responses contains invalid values: {invalid_responses}",
        )

        if diagnostic.get("deployment_status") != "underdeployment_review_required":
            continue

        rows = _final_rows_for_ticker(payload, ticker, step=step)
        keep_or_hold_rows = [row for row in rows if _row_action(row) in KEEP_OR_HOLD_ACTIONS]
        if not keep_or_hold_rows:
            continue

        explicit_why = _text(diagnostic.get("why")) or any(_row_why(row) for row in keep_or_hold_rows)
        if step == "strategy_b" and not explicit_why:
            explicit_why = _has_underdeployment_blocker(payload)
        _require(
            bool(explicit_why),
            f"{label} has underdeployment_review_required with keep/hold final action but no explicit why.",
        )
