"""Extract DAILY_EXECUTION_ACTIONS from a manual Daily Execution Check output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import read_json, read_text, write_json, write_text
from investment_orchestrator.validators.validate_daily_execution_actions import (
    validate_daily_execution_actions,
)
from investment_orchestrator.validators.strategy_settings import load_strategy_settings


class DailyExecutionCheckExtractionError(ValueError):
    """Raised when a Daily Execution Check raw output cannot be parsed safely."""


def extract_required_block(text: str, start_marker: str, end_marker: str) -> str:
    """Return the text between two required markers."""
    start = text.find(start_marker)
    if start == -1:
        raise DailyExecutionCheckExtractionError(f"Missing required marker {start_marker!r}.")
    end = text.rfind(end_marker)
    if end == -1 or end <= start:
        raise DailyExecutionCheckExtractionError(f"Missing or malformed closing marker {end_marker!r}.")
    return text[start + len(start_marker) : end].strip()


def strip_code_fence(text: str) -> str:
    """Remove one surrounding Markdown fence when present."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return stripped
    return "\n".join(lines[1:-1]).strip()


def parse_daily_execution_actions_text(raw_text: str) -> dict[str, Any]:
    """Parse a raw Daily Execution Check response into a JSON object."""
    block = extract_required_block(
        raw_text,
        "DAILY_EXECUTION_ACTIONS_START",
        "DAILY_EXECUTION_ACTIONS_END",
    )
    cleaned = strip_code_fence(block)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise DailyExecutionCheckExtractionError(f"DAILY_EXECUTION_ACTIONS block is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise DailyExecutionCheckExtractionError("DAILY_EXECUTION_ACTIONS block must be a single JSON object.")
    return payload


def extract_daily_execution_check(
    *,
    raw_output_path: str | Path,
    daily_execution_actions_path: str | Path,
    daily_execution_check_text_path: str | Path,
    audited_decision_packet_path: str | Path,
    template4_orders_path: str | Path,
    order_state_export_path: str | Path,
    strategy_settings_path: str | Path | None = None,
    precomputed_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read, parse, validate, and write Daily Execution Check artifacts."""
    raw_text = read_text(raw_output_path)
    block_text = extract_required_block(
        raw_text,
        "DAILY_EXECUTION_ACTIONS_START",
        "DAILY_EXECUTION_ACTIONS_END",
    )
    payload = parse_daily_execution_actions_text(raw_text)

    audited_packet = read_json(audited_decision_packet_path)
    template4_orders_text = read_text(template4_orders_path)
    order_state_export_text = read_text(order_state_export_path)
    strategy_settings = load_strategy_settings(strategy_settings_path) if strategy_settings_path is not None else None
    validate_daily_execution_actions(
        payload,
        audited_decision_packet=audited_packet,
        template4_orders_text=template4_orders_text,
        order_state_export_text=order_state_export_text,
        strategy_settings=strategy_settings,
        precomputed_diagnostics=precomputed_diagnostics,
    )

    write_text(daily_execution_check_text_path, block_text.rstrip() + "\n")
    write_json(daily_execution_actions_path, payload)
    return payload


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract DAILY_EXECUTION_ACTIONS from a Daily Execution Check output."
    )
    parser.add_argument("--raw-output", required=True, help="Path to daily raw_output.txt")
    parser.add_argument(
        "--daily-execution-actions",
        required=True,
        help="Path to write daily_execution_actions.json",
    )
    parser.add_argument(
        "--daily-execution-check-text",
        required=True,
        help="Path to write extracted daily_execution_check.txt",
    )
    parser.add_argument("--audited-decision-packet", required=True)
    parser.add_argument("--template4-orders", required=True)
    parser.add_argument("--order-state-export", required=True)
    args = parser.parse_args()

    extract_daily_execution_check(
        raw_output_path=Path(args.raw_output),
        daily_execution_actions_path=Path(args.daily_execution_actions),
        daily_execution_check_text_path=Path(args.daily_execution_check_text),
        audited_decision_packet_path=Path(args.audited_decision_packet),
        template4_orders_path=Path(args.template4_orders),
        order_state_export_path=Path(args.order_state_export),
    )
    print(args.daily_execution_actions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
