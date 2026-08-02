from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
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
from investment_orchestrator.mmi.canonical import MAXIMUM_MMI_RAW_RESPONSE_BYTES


SCHEMA_NAME = "mmi_raw_response_envelope_v2.schema.json"
SCHEMA_PATH = repo_root() / "schemas" / SCHEMA_NAME
SHA256 = "b" * 64
MAXIMUM_BASE64_CHARACTERS = 349_528
EXACT_RAW_RESPONSE_BASE64_SCHEMA = {
    "type": "string",
    "minLength": 4,
    "maxLength": 349_528,
    "pattern": (
        "^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|"
        "[A-Za-z0-9+/]{3}=)?$"
    ),
}
EXPECTED_FIELDS = {
    "schema_version",
    "artifact_kind",
    "report_only",
    "authority_effect",
    "manual_handoff_required",
    "grounded_prompt_artifact_identity_sha256",
    "raw_response_byte_length",
    "raw_response_sha256",
    "raw_response_base64",
    "raw_response_envelope_identity_sha256",
}


def _schema() -> dict[str, object]:
    value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _artifact(raw_bytes: bytes = b"{}") -> dict[str, object]:
    return {
        "schema_version": "mmi_raw_response_envelope_v2",
        "artifact_kind": "MMI_RAW_RESPONSE_ENVELOPE",
        "report_only": True,
        "authority_effect": "NONE",
        "manual_handoff_required": True,
        "grounded_prompt_artifact_identity_sha256": SHA256,
        "raw_response_byte_length": len(raw_bytes),
        "raw_response_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "raw_response_base64": base64.b64encode(raw_bytes).decode("ascii"),
        "raw_response_envelope_identity_sha256": SHA256,
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
        "const": contracts._MMI_RAW_RESPONSE_ENVELOPE_V2_SCHEMA_VERSION
    }
    assert properties["artifact_kind"] == {
        "const": contracts.MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND
    }
    assert properties["raw_response_byte_length"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    }
    assert properties["raw_response_base64"] == EXACT_RAW_RESPONSE_BASE64_SCHEMA


def test_raw_response_base64_lexical_contract_is_exact() -> None:
    schema = _schema()
    properties = schema["properties"]
    assert type(properties) is dict
    raw_response_base64 = properties["raw_response_base64"]
    assert raw_response_base64 == EXACT_RAW_RESPONSE_BASE64_SCHEMA

    validator = Draft202012Validator(raw_response_base64)
    assert all(
        validator.is_valid(value)
        for value in ("TQ==", "TWE=", "TWFu")
    )
    assert all(
        not validator.is_valid(value)
        for value in (
            "TQ",  # incomplete terminal quantum
            "TQ===",  # padding may occur only as the terminal quantum
            "T=Fu",  # padding may not occur before the end
            "T-W_",  # URL-safe alphabet is not accepted
            "TW Fu",  # whitespace is not accepted
        )
    )


def test_schema_accepts_exact_byte_envelope_shape() -> None:
    value = _artifact()
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert MAXIMUM_MMI_RAW_RESPONSE_BYTES == 262_144
    assert MAXIMUM_BASE64_CHARACTERS == 4 * (
        (MAXIMUM_MMI_RAW_RESPONSE_BYTES + 2) // 3
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "mmi_raw_response_envelope_v1"),
        ("raw_response_byte_length", 0),
        ("raw_response_byte_length", 262_145),
        ("raw_response_base64", "not-base64"),
        ("raw_response_sha256", "B" * 64),
        ("manual_handoff_required", False),
    ],
)
def test_schema_rejects_invalid_envelope_values(
    field: str,
    value: object,
) -> None:
    candidate = _artifact()
    candidate[field] = value
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)


def test_schema_rejects_provider_path_payload_and_authority_fields() -> None:
    for field in (
        "provider",
        "model",
        "generated_at",
        "response_path",
        "response_payload",
        "availability",
        "action",
        "order",
    ):
        candidate = deepcopy(_artifact())
        candidate[field] = "forbidden"
        with pytest.raises(ArtifactSchemaError):
            validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)


def test_h02c_has_exact_r1c_v2_and_r2c_v2_phase_ownership() -> None:
    root = repo_root()
    assert (
        root / "src/investment_orchestrator/mmi/raw_response_envelope_v2.py"
    ).exists()
    domains = _identity_domains()
    assert len(domains) == len(set(domains)) == 16
    assert b"mmi_raw_response_envelope_v2\0" in domains
    assert (
        root
        / "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response_v2.py"
    ).exists()
    assert b"mmi_validated_grounded_analysis_response_v2\0" in domains
    assert mmi.__all__ == ()
