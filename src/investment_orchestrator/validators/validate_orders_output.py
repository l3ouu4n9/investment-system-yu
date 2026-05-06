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
FIELD_TICKER_RE = re.compile(r"(?:^|[\n|]\s*)ticker=([A-Za-z][A-Za-z0-9.\-]*)")
BUY_ACTION_VALUES = {
    "NEW_ORDER",
    "REPLACE_EXISTING",
    "BUY",
    "REPLACE",
    "SUBMIT_BUY",
    "EXECUTE_BUY",
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


def _buy_order_tickers(template4_orders: str) -> set[str]:
    return {_normalize_ticker(match.group(1)) for match in FIELD_TICKER_RE.finditer(template4_orders)}


def _executable_buy_tickers(audited_decision_packet: Any) -> set[str]:
    if not isinstance(audited_decision_packet, dict):
        return set()
    rows = audited_decision_packet.get("final_execution_plans")
    if not isinstance(rows, list):
        return set()
    tickers: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("compile_ready") is not True:
            continue
        action = str(
            row.get("final_action")
            or row.get("action")
            or row.get("order_intent")
            or ""
        ).strip()
        if action in BUY_ACTION_VALUES:
            ticker = _normalize_ticker(row.get("ticker"))
            if ticker:
                tickers.add(ticker)
    return tickers


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
        buy_order_tickers = _buy_order_tickers(template4_orders)
        executable_buy_tickers = _executable_buy_tickers(audited_decision_packet)
        unexpected_tickers = buy_order_tickers - executable_buy_tickers
        _require(
            not unexpected_tickers,
            "BUY_ORDERS contains tickers that are not executable final_execution_plans: "
            + ", ".join(sorted(unexpected_tickers)),
        )
        _require_diagnostic_summary(exec_summary, audited_decision_packet)

    return {
        "template4_orders": template4_orders,
        "order_state_export": order_state_export,
        "exec_summary": exec_summary,
    }
