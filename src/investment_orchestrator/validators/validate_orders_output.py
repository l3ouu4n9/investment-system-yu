"""Validation helpers for Step 4 order compiler text artifacts."""

from __future__ import annotations

from pathlib import Path

from investment_orchestrator.common.io import read_text


REQUIRED_OUTPUT_LABELS = (
    ("template4_orders.txt", "template4_orders"),
    ("order_state_export.txt", "order_state_export"),
    ("exec_summary.txt", "exec_summary"),
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_non_empty_text_file(path: str | Path, *, label: str) -> str:
    text_path = Path(path)
    _require(text_path.exists(), f"Missing required Step 4 artifact: {text_path}")
    text = read_text(text_path)
    _require(text.strip() != "", f"Step 4 artifact is empty for {label}: {text_path}")
    return text


def validate_orders_output(
    *,
    template4_orders_path: str | Path,
    order_state_export_path: str | Path,
    exec_summary_path: str | Path,
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

    return {
        "template4_orders": template4_orders,
        "order_state_export": order_state_export,
        "exec_summary": exec_summary,
    }
