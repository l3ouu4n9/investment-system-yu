"""Validation helpers for Step 1 RESEARCH_JSON artifacts."""

from __future__ import annotations

from typing import Any

from investment_orchestrator.common.schema_validation import validate_artifact_schema


def validate_research_output(payload: Any) -> dict[str, Any]:
    """Validate the parsed RESEARCH_JSON payload against the repo schema."""
    if not isinstance(payload, dict):
        raise ValueError("research_output.json must be a JSON object.")

    validate_artifact_schema(payload, schema_name="research_output.schema.json")
    return payload
