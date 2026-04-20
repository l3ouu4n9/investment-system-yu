"""Validation helpers for Step 3 AUDITED_DECISION_PACKET artifacts."""

from __future__ import annotations

from typing import Any


REQUIRED_AUDITED_PACKET_KEYS = (
    "audit_passed",
    "order_compiler_ready",
    "final_buy_side_delta_table",
    "final_sell_side_delta_table",
    "final_execution_plans",
    "final_sell_execution_plans",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_audited_decision_packet(payload: Any) -> dict[str, Any]:
    """Validate the parsed AUDITED_DECISION_PACKET payload."""
    _require(isinstance(payload, dict), "audited_decision_packet.json must be a JSON object.")

    missing = [key for key in REQUIRED_AUDITED_PACKET_KEYS if key not in payload]
    _require(
        not missing,
        "audited_decision_packet.json is missing required keys: " + ", ".join(missing),
    )

    _require(
        isinstance(payload["audit_passed"], bool),
        "audit_passed must exist and be a boolean.",
    )
    _require(
        isinstance(payload["order_compiler_ready"], bool),
        "order_compiler_ready must exist and be a boolean.",
    )

    for key in (
        "final_buy_side_delta_table",
        "final_sell_side_delta_table",
        "final_execution_plans",
        "final_sell_execution_plans",
    ):
        _require(isinstance(payload[key], list), f"{key} must exist and be a list.")

    return payload
