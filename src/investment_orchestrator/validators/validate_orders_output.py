"""Validation helpers for Step 4 order compiler text artifacts."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from investment_orchestrator.common.io import read_text


REQUIRED_OUTPUT_LABELS = (
    ("template4_orders.txt", "template4_orders"),
    ("order_state_export.txt", "order_state_export"),
    ("exec_summary.txt", "exec_summary"),
)
BUY_SECTION_RE = re.compile(
    r"(?ms)^BUY_ORDERS\s*(?P<body>.*?)(?=^(?:TEMPLATE4_ORDERS_END|ORDER_STATE_EXPORT|TEMPLATE5_EXEC_SUMMARY|SELL_ORDERS)\b|\Z)"
)
BUY_ACTION_VALUES = {
    "NEW_ORDER",
    "REPLACE_EXISTING",
    "BUY",
    "REPLACE",
    "SUBMIT_BUY",
    "EXECUTE_BUY",
}
BUY_SIDE_COMPILER_ACTION_VALUES = BUY_ACTION_VALUES | {"CANCEL_EXISTING"}
ROW_INTENTS_BY_FINAL_ACTION = {
    "NEW_ORDER": {"", "NEW_ORDER", "BUY", "SUBMIT_BUY", "EXECUTE_BUY"},
    "BUY": {"", "NEW_ORDER", "BUY", "SUBMIT_BUY", "EXECUTE_BUY"},
    "SUBMIT_BUY": {"", "NEW_ORDER", "BUY", "SUBMIT_BUY", "EXECUTE_BUY"},
    "EXECUTE_BUY": {"", "NEW_ORDER", "BUY", "SUBMIT_BUY", "EXECUTE_BUY"},
    "REPLACE_EXISTING": {
        "",
        "REPLACE_EXISTING",
        "REPLACE",
        "REPLACE_EXISTING_CANCEL_LEG",
        "REPLACE_EXISTING_SUBMIT_LEG",
    },
    "REPLACE": {
        "",
        "REPLACE_EXISTING",
        "REPLACE",
        "REPLACE_EXISTING_CANCEL_LEG",
        "REPLACE_EXISTING_SUBMIT_LEG",
    },
    "CANCEL_EXISTING": {"", "CANCEL_EXISTING"},
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_non_empty_text_file(path: str | Path, *, label: str) -> str:
    text_path = Path(path)
    _require(text_path.exists(), f"Missing required Step 4 artifact: {text_path}")
    text = read_text(text_path)
    _require(text.strip() != "", f"Step 4 artifact is empty for {label}: {text_path}")
    return text


def _normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_action(value: Any) -> str:
    return str(value or "").strip().upper()


def _parse_pipe_row(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in line.split("|"):
        key, separator, value = part.strip().partition("=")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def _buy_order_rows(template4_orders: str) -> list[dict[str, str]]:
    match = BUY_SECTION_RE.search(template4_orders)
    if match is None:
        return []
    rows: list[dict[str, str]] = []
    for line in match.group("body").splitlines():
        fields = _parse_pipe_row(line)
        if _normalize_ticker(fields.get("ticker")):
            rows.append(fields)
    return rows


def _compile_ready_final_plans(audited_decision_packet: Any) -> dict[str, set[str]]:
    if not isinstance(audited_decision_packet, dict):
        return {}
    rows = audited_decision_packet.get("final_execution_plans")
    if not isinstance(rows, list):
        return {}
    plans: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("compile_ready") is not True:
            continue
        action = _normalize_action(
            row.get("final_action")
            or row.get("action")
            or row.get("order_intent")
            or ""
        )
        if action in BUY_SIDE_COMPILER_ACTION_VALUES:
            ticker = _normalize_ticker(row.get("ticker"))
            if ticker:
                plans.setdefault(ticker, set()).add(action)
    return plans


def _require_buy_order_rows_match_final_plans(
    *,
    template4_orders: str,
    audited_decision_packet: Any,
) -> None:
    buy_order_rows = _buy_order_rows(template4_orders)
    final_plans = _compile_ready_final_plans(audited_decision_packet)
    unexpected_rows: list[str] = []
    unsupported_intents: list[str] = []

    for row in buy_order_rows:
        ticker = _normalize_ticker(row.get("ticker"))
        row_intent = _normalize_action(row.get("order_intent"))
        final_actions = final_plans.get(ticker, set())
        if not final_actions:
            unexpected_rows.append(ticker)
            continue

        if any(row_intent in ROW_INTENTS_BY_FINAL_ACTION.get(action, set()) for action in final_actions):
            continue
        unsupported_intents.append(
            f"{ticker} order_intent={row_intent or '<missing>'} final_action={','.join(sorted(final_actions))}"
        )

    _require(
        not unexpected_rows,
        "BUY_ORDERS contains tickers that are not compile-ready buy-side final_execution_plans: "
        + ", ".join(sorted(set(unexpected_rows))),
    )
    _require(
        not unsupported_intents,
        "BUY_ORDERS contains order_intent values that do not match final_execution_plans: "
        + "; ".join(unsupported_intents),
    )


def _require_diagnostic_summary(exec_summary: str, audited_decision_packet: Any) -> None:
    if not isinstance(audited_decision_packet, dict):
        return
    diagnostics = audited_decision_packet.get("core_deployment_diagnostics")
    if not isinstance(diagnostics, list) or not diagnostics:
        return

    summary_lower = exec_summary.lower()
    for index, diagnostic in enumerate(diagnostics):
        _require(isinstance(diagnostic, dict), f"core_deployment_diagnostics[{index}] must be an object.")
        for key in (
            "ticker",
            "role_layer",
            "deployment_status",
            "highest_live_limit",
            "current_last_close",
            "distance_to_highest_live_limit_pct",
            "why",
        ):
            value = diagnostic.get(key)
            _require(
                value is not None and str(value).strip().lower() in summary_lower,
                f"exec_summary missing core_deployment_diagnostics {key} value for item {index}.",
            )
        if diagnostic.get("deployment_status") == "underdeployment_review_required":
            _require(
                "weekly_review_needed: core etf deployment adequacy requires weekly decision; "
                "strategy c did not change orders."
                in summary_lower,
                "exec_summary missing WEEKLY_REVIEW_NEEDED diagnostic disclaimer.",
            )


def validate_orders_output(
    *,
    template4_orders_path: str | Path,
    order_state_export_path: str | Path,
    exec_summary_path: str | Path,
    audited_decision_packet: Any | None = None,
) -> dict[str, str]:
    """Validate that the required Step 4 output text artifacts exist and are non-empty."""
    template4_orders = _validate_non_empty_text_file(
        template4_orders_path,
        label="template4_orders",
    )
    order_state_export = _validate_non_empty_text_file(
        order_state_export_path,
        label="order_state_export",
    )
    exec_summary = _validate_non_empty_text_file(
        exec_summary_path,
        label="exec_summary",
    )
    if audited_decision_packet is not None:
        _require_buy_order_rows_match_final_plans(
            template4_orders=template4_orders,
            audited_decision_packet=audited_decision_packet,
        )
        _require_diagnostic_summary(exec_summary, audited_decision_packet)

    return {
        "template4_orders": template4_orders,
        "order_state_export": order_state_export,
        "exec_summary": exec_summary,
    }
