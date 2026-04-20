"""Validation helpers for Step 2 DECISION_PACKET artifacts."""

from __future__ import annotations

from typing import Any

from investment_orchestrator.common.schema_validation import validate_artifact_schema


REQUIRED_DECISION_PACKET_KEYS = (
    "effective_allowed_buy_universe",
    "MARKET_DATA_SNAPSHOT",
    "active_shortlist",
    "buy_side_delta_table",
    "rotation_decision_layer_8_15",
    "sell_side_delta_table_8_2",
    "execution_plan_drafts_8_5",
    "sell_execution_plan_drafts_8_6",
    "assumptions_and_data_gaps",
    "decision_builder_ready_for_audit",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_decision_packet(payload: Any) -> dict[str, Any]:
    """Validate the parsed DECISION_PACKET plus its embedded market snapshot."""
    _require(isinstance(payload, dict), "decision_packet.json must be a JSON object.")

    missing = [key for key in REQUIRED_DECISION_PACKET_KEYS if key not in payload]
    _require(not missing, f"decision_packet.json is missing required keys: {', '.join(missing)}")

    allowed_buy_universe = payload["effective_allowed_buy_universe"]
    _require(
        isinstance(allowed_buy_universe, list)
        and bool(allowed_buy_universe)
        and all(isinstance(item, str) and item.strip() for item in allowed_buy_universe),
        "effective_allowed_buy_universe must be a non-empty string list.",
    )

    _require(
        payload["decision_builder_ready_for_audit"] is True,
        "decision_builder_ready_for_audit must be true.",
    )

    market_snapshot = payload["MARKET_DATA_SNAPSHOT"]
    _require(isinstance(market_snapshot, dict), "MARKET_DATA_SNAPSHOT must be a JSON object.")
    validate_artifact_schema(market_snapshot, schema_name="market_data_snapshot.schema.json")

    for key in (
        "active_shortlist",
        "buy_side_delta_table",
        "sell_side_delta_table_8_2",
        "execution_plan_drafts_8_5",
        "sell_execution_plan_drafts_8_6",
        "assumptions_and_data_gaps",
    ):
        _require(isinstance(payload[key], list), f"{key} must be a list.")

    return payload
