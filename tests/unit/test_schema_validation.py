import pytest

from investment_system.common.schema_validation import ArtifactSchemaError, validate_artifact_schema
from investment_system.pipelines.shared import init_run_context


def test_validate_artifact_schema_accepts_run_context_payload() -> None:
    payload = init_run_context(
        pipeline_name="weekly",
        as_of_date="2026-04-18",
        run_timestamp_et="2026-04-18 20:30 ET",
        extra_fields={"has_live_order": False},
    )

    validate_artifact_schema(payload, schema_name="run_context.schema.json")


def test_validate_artifact_schema_rejects_missing_required_field() -> None:
    payload = {
        "schema_version": "1.0",
        "as_of_date": "2026-04-18",
        "run_timestamp_et": "2026-04-18 20:30 ET",
        "run_mode": "normal",
        "blocking_issue": None,
        "degraded_steps": [],
        "warnings": [],
        "step_summary": [],
    }

    with pytest.raises(ArtifactSchemaError) as exc_info:
        validate_artifact_schema(payload, schema_name="run_context.schema.json")

    assert "run_context.schema.json" in str(exc_info.value)
    assert "pipeline" in str(exc_info.value)
