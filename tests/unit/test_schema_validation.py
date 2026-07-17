import pytest

from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.state.blocked_run_summary import (
    blocked_run_summary_result_to_dict,
    build_blocked_run_summary,
)


def test_validate_artifact_schema_accepts_run_context_payload() -> None:
    payload = {
        "schema_version": "1.0",
        "pipeline": "weekly",
        "as_of_date": "2026-04-18",
        "run_timestamp_et": "2026-04-18 20:30 ET",
        "run_mode": "normal",
        "blocking_issue": None,
        "degraded_steps": [],
        "warnings": [],
        "step_summary": [],
        "has_live_order": False,
    }

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


def test_validate_artifact_schema_accepts_blocked_run_summary_payload() -> None:
    payload = blocked_run_summary_result_to_dict(
        build_blocked_run_summary(
            step1_decision={
                "state": "STRICT_FRESH",
                "research_availability": "strict_fresh",
                "allowed_actions": ["HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION"],
                "blocked_actions": [],
                "manual_review_required": False,
                "blocker_reasons": [],
            },
            step2_block=None,
            step3_block=None,
            step4_block=None,
        )
    )

    validate_artifact_schema(payload, schema_name="blocked_run_summary.schema.json")
