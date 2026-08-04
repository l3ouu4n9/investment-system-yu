from __future__ import annotations

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

import investment_orchestrator.mmi as mmi
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.mmi import canonical, contracts
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES,
)


SCHEMA_NAME = "mmi_grounded_prompt_v2.schema.json"
SCHEMA_PATH = repo_root() / "schemas" / SCHEMA_NAME
SHA256 = "a" * 64
EXPECTED_FIELDS = {
    "schema_version",
    "artifact_kind",
    "report_only",
    "authority_effect",
    "analyst_visible_evidence_view_identity_sha256",
    "instruction_set_version",
    "expected_response_schema_version",
    "manual_handoff_required",
    "prompt_context_binding_sha256",
    "prompt_text",
    "grounded_prompt_artifact_identity_sha256",
}
EXPECTED_IDENTITY_DOMAINS = {
    b"mmi_source_record_v1\0",
    b"mmi_universe_projection_v1\0",
    b"mmi_policy_projection_v1\0",
    b"mmi_portfolio_snapshot_projection_v1\0",
    b"mmi_authenticated_evidence_bundle_v1\0",
    b"mmi_analyst_visible_evidence_view_v1\0",
    b"mmi_analyst_visible_evidence_view_v2\0",
    b"mmi_grounded_prompt_context_binding_v1\0",
    b"mmi_grounded_prompt_artifact_v1\0",
    b"mmi_grounded_prompt_context_binding_v2\0",
    b"mmi_grounded_prompt_artifact_v2\0",
    b"mmi_raw_response_envelope_v1\0",
    b"mmi_raw_response_envelope_v2\0",
    b"mmi_validated_grounded_analysis_response_v1\0",
    b"mmi_validated_grounded_analysis_response_v2\0",
    b"mmi_legacy_step1_compatibility_candidate_v1\0",
    b"mmi_legacy_step1_comparison_report_v1\0",
    b"mmi_h2c_dual_side_manual_handoff_context_receipt_v1\0",
    b"mmi_h2c_case_evidence_bundle_v1\0",
}


def _schema() -> dict[str, object]:
    value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _artifact() -> dict[str, object]:
    return {
        "schema_version": "mmi_grounded_prompt_v2",
        "artifact_kind": "MMI_GROUNDED_PROMPT",
        "report_only": True,
        "authority_effect": "NONE",
        "analyst_visible_evidence_view_identity_sha256": SHA256,
        "instruction_set_version": (
            "mmi_grounded_prompt_instruction_set_v2"
        ),
        "expected_response_schema_version": (
            "mmi_grounded_analysis_response_v2"
        ),
        "manual_handoff_required": True,
        "prompt_context_binding_sha256": SHA256,
        "prompt_text": "Future deterministic prompt bytes.\n",
        "grounded_prompt_artifact_identity_sha256": SHA256,
    }


def _identity_domains() -> tuple[bytes, ...]:
    return tuple(
        value
        for value in vars(canonical).values()
        if (
            type(value) is bytes
            and value.startswith(b"mmi_")
            and value.endswith(b"\0")
        )
    )


def test_schema_constants_and_closed_shape_are_exact() -> None:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == EXPECTED_FIELDS
    properties = schema["properties"]
    assert type(properties) is dict
    assert set(properties) == EXPECTED_FIELDS
    assert properties["schema_version"] == {
        "const": contracts._MMI_GROUNDED_PROMPT_V2_SCHEMA_VERSION
    }
    assert properties["artifact_kind"] == {
        "const": contracts.MMI_GROUNDED_PROMPT_ARTIFACT_KIND
    }
    assert properties["instruction_set_version"] == {
        "const": contracts._MMI_GROUNDED_PROMPT_V2_INSTRUCTION_SET_VERSION
    }
    assert properties["expected_response_schema_version"] == {
        "const": contracts._MMI_GROUNDED_ANALYSIS_RESPONSE_V2_SCHEMA_VERSION
    }
    assert properties["report_only"] == {"const": True}
    assert properties["authority_effect"] == {"const": "NONE"}
    assert properties["manual_handoff_required"] == {"const": True}


def test_schema_validates_a_closed_shape_without_freezing_prompt_bytes() -> None:
    value = _artifact()
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    schema = _schema()
    properties = schema["properties"]
    assert type(properties) is dict
    assert properties["prompt_text"] == {
        "type": "string",
        "minLength": 1,
    }
    assert MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES == 65_536


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "mmi_grounded_prompt_v1"),
        ("report_only", False),
        ("authority_effect", "HOLD"),
        ("manual_handoff_required", False),
        ("prompt_text", ""),
        ("prompt_context_binding_sha256", "A" * 64),
    ],
)
def test_schema_rejects_invalid_closed_values(
    field: str,
    value: object,
) -> None:
    candidate = _artifact()
    candidate[field] = value
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)


def test_schema_rejects_extension_and_authority_fields() -> None:
    candidate = _artifact()
    candidate["provider"] = "untrusted"
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    for field in (
        "availability",
        "permission",
        "allowed_actions",
        "budget",
        "quantity",
        "gate",
        "order",
        "publication",
        "execution",
    ):
        candidate = deepcopy(_artifact())
        candidate[field] = "forbidden"
        with pytest.raises(ArtifactSchemaError):
            validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)


def test_h02c_has_only_the_accepted_runtime_identity_domains() -> None:
    root = repo_root()
    assert (
        root / "src/investment_orchestrator/mmi/grounded_prompt_v2.py"
    ).exists()
    domains = _identity_domains()
    assert set(domains) == EXPECTED_IDENTITY_DOMAINS
    assert len(domains) == len(set(domains)) == 19
    assert b"mmi_validated_grounded_analysis_response_v2\0" in domains
    assert mmi.__all__ == ()


def test_schema_inventory_includes_the_exact_dormant_contract_additions() -> None:
    schema_paths = tuple(sorted((repo_root() / "schemas").glob("*.schema.json")))
    assert len(schema_paths) == 45
    assert {path.name for path in schema_paths} >= {
        "mmi_grounded_prompt_v2.schema.json",
        "mmi_raw_response_envelope_v2.schema.json",
        "mmi_validated_grounded_analysis_response_v2.schema.json",
        "mmi_legacy_step1_compatibility_candidate_v1.schema.json",
        "mmi_h2c_dual_side_manual_handoff_context_receipt_v1.schema.json",
        "mmi_h2c_prepared_case_v1.schema.json",
        "mmi_h2c_dual_side_persisted_case_receipt_v2.schema.json",
    }
    assert not (
        repo_root() / "schemas/mmi_grounded_analysis_response_v2.schema.json"
    ).exists()
