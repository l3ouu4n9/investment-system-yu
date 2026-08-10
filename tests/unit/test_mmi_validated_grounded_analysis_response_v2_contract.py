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
    MAXIMUM_CANONICAL_DEPTH,
    MAXIMUM_CANONICAL_JSON_BYTES,
    MAXIMUM_CANONICAL_NODES,
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    MAX_MMI_GROUNDED_ANALYSIS_RESPONSE_V2_CANONICAL_BYTES,
)


SCHEMA_NAME = "mmi_validated_grounded_analysis_response_v2.schema.json"
SCHEMA_PATH = repo_root() / "schemas" / SCHEMA_NAME
SHA256 = "c" * 64
EXPECTED_WRAPPER_FIELDS = {
    "schema_version",
    "artifact_kind",
    "report_only",
    "authority_effect",
    "manual_handoff_required",
    "raw_response_envelope_identity_sha256",
    "response_payload",
    "validated_grounded_analysis_response_identity_sha256",
}
EXPECTED_PAYLOAD_FIELDS = {
    "response_schema_version",
    "prompt_context_binding_sha256",
    "analysis_status",
    "instrument_views",
    "anchor_associations_status",
    "scheduled_events_status",
    "regime_observation_status",
    "evidence_observations",
    "risks",
    "uncertainties",
    "contradictions",
    "research_questions",
    "summary",
}
EXPECTED_ANALYSIS_STATUSES = [
    "QUALITATIVE_ANALYSIS_PROVIDED",
    "INSUFFICIENT_EVIDENCE",
    "EVIDENCE_CONTRADICTIONS_IDENTIFIED",
]
EXPECTED_EVIDENCE_STATUSES = [
    "EVIDENCE_SUPPORTED",
    "INSUFFICIENT_EVIDENCE",
    "CONTRADICTED",
    "UNAVAILABLE",
]
EXPECTED_FIXED_TIER_A_REFERENCES = (
        "VIEW.EVALUATION_TIMESTAMP",
        "VIEW.COMPLETENESS_STATUS",
        "POLICY.AS_OF_DATE",
        "POLICY.METHOD",
        "POLICY.BENCHMARK.0001",
        "POLICY.EXTENDED_ACTIVATION_STATUS",
        "POLICY.INSTRUMENT_AVAILABILITY_STATUS",
        "POLICY.TARGET_WEIGHTS_ABSENCE_REASON",
        "PORTFOLIO.PRESENCE_STATUS",
        "PORTFOLIO.SOURCE_DATE",
        "PORTFOLIO.OPEN_BUY_STATUS",
        "PORTFOLIO.COVERAGE.HOLDINGS",
        "PORTFOLIO.COVERAGE.CASH",
        "PORTFOLIO.COVERAGE.DEPLOYABLE_CASH",
        "PORTFOLIO.COVERAGE.OPEN_SELLS",
        "PORTFOLIO.COVERAGE.TAX_LOTS",
        "PORTFOLIO.COVERAGE.HOLDING_DATES",
        "PORTFOLIO.COVERAGE.GAINS_LOSSES",
        "PORTFOLIO.COVERAGE.WEIGHTS",
        "PORTFOLIO.COVERAGE.NAV_CONCENTRATION",
        "PORTFOLIO.COVERAGE.LOOK_THROUGH_EXPOSURE",
)
EXPECTED_FIXED_TIER_A_REFERENCE_SET = frozenset(
    EXPECTED_FIXED_TIER_A_REFERENCES
)
EXACT_EVIDENCE_REFERENCE_ONE_OF = [
    {"enum": list(EXPECTED_FIXED_TIER_A_REFERENCES)},
    {
        "type": "string",
        "pattern": (
            "^POLICY\\.INSTRUMENT\\.(?:000[1-9]|00[1-9][0-9]|"
            "01[0-9]{2}|02[0-4][0-9]|025[0-6])(?![\\s\\S])"
        ),
    },
    {
        "type": "string",
        "pattern": (
            "^PORTFOLIO\\.OBSERVATION\\.(?:000[1-9]|00[1-9][0-9]|"
            "01[0-9]{2}|02[0-4][0-9]|025[0-6])(?![\\s\\S])"
        ),
    },
    {
        "type": "string",
        "pattern": "^LIMITATION\\.00(?:0[1-9]|1[0-4])(?![\\s\\S])",
    },
]


def _expected_tier_a_references() -> frozenset[str]:
    return EXPECTED_FIXED_TIER_A_REFERENCE_SET | frozenset(
        f"POLICY.INSTRUMENT.{number:04d}" for number in range(1, 257)
    ) | frozenset(
        f"PORTFOLIO.OBSERVATION.{number:04d}" for number in range(1, 257)
    ) | frozenset(
        f"LIMITATION.{number:04d}" for number in range(1, 15)
    )


def _schema() -> dict[str, object]:
    value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _instrument_view(
    *,
    ticker: str,
    evidence_status: str = "EVIDENCE_SUPPORTED",
) -> dict[str, object]:
    if evidence_status == "UNAVAILABLE":
        return {
            "ticker": ticker,
            "evidence_status": evidence_status,
            "rationale_12m_plus": None,
            "references": [],
        }
    return {
        "ticker": ticker,
        "evidence_status": evidence_status,
        "rationale_12m_plus": "Visible evidence supports this rationale.",
        "references": ["POLICY.INSTRUMENT.0001"],
    }


def _artifact() -> dict[str, object]:
    return {
        "schema_version": "mmi_validated_grounded_analysis_response_v2",
        "artifact_kind": "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE",
        "report_only": True,
        "authority_effect": "NONE",
        "manual_handoff_required": True,
        "raw_response_envelope_identity_sha256": SHA256,
        "response_payload": {
            "response_schema_version": "mmi_grounded_analysis_response_v2",
            "prompt_context_binding_sha256": SHA256,
            "analysis_status": "QUALITATIVE_ANALYSIS_PROVIDED",
            "instrument_views": [
                _instrument_view(ticker="VOO"),
                _instrument_view(ticker="QQQ", evidence_status="UNAVAILABLE"),
            ],
            "anchor_associations_status": "UNAVAILABLE",
            "scheduled_events_status": "UNAVAILABLE",
            "regime_observation_status": "UNAVAILABLE",
            "evidence_observations": [],
            "risks": [],
            "uncertainties": [],
            "contradictions": [],
            "research_questions": [],
            "summary": {
                "text": "Research-only synthesis.",
                "references": ["VIEW.EVALUATION_TIMESTAMP"],
                "hypothesis": False,
            },
        },
        "validated_grounded_analysis_response_identity_sha256": SHA256,
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


def _assert_all_object_boundaries_closed(node: object) -> None:
    if type(node) is dict:
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
        for value in node.values():
            _assert_all_object_boundaries_closed(value)
    elif type(node) is list:
        for value in node:
            _assert_all_object_boundaries_closed(value)


def test_wrapper_payload_constants_and_closed_shape_are_exact() -> None:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    _assert_all_object_boundaries_closed(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == EXPECTED_WRAPPER_FIELDS
    properties = schema["properties"]
    assert type(properties) is dict
    assert set(properties) == EXPECTED_WRAPPER_FIELDS
    assert properties["schema_version"] == {
        "const": (
            contracts._MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_V2_SCHEMA_VERSION
        )
    }
    assert properties["artifact_kind"] == {
        "const": contracts.MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_ARTIFACT_KIND
    }
    definitions = schema["$defs"]
    assert type(definitions) is dict
    payload = definitions["response_payload"]
    assert type(payload) is dict
    assert set(payload["required"]) == EXPECTED_PAYLOAD_FIELDS
    assert set(payload["properties"]) == EXPECTED_PAYLOAD_FIELDS
    assert payload["properties"]["response_schema_version"] == {
        "const": contracts._MMI_GROUNDED_ANALYSIS_RESPONSE_V2_SCHEMA_VERSION
    }


def test_schema_accepts_the_closed_tier_a_payload_shape() -> None:
    value = _artifact()
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    payload = value["response_payload"]
    assert type(payload) is dict
    assert payload["anchor_associations_status"] == "UNAVAILABLE"
    assert payload["scheduled_events_status"] == "UNAVAILABLE"
    assert payload["regime_observation_status"] == "UNAVAILABLE"


def test_status_vocabulary_and_component_status_constants_are_exact() -> None:
    schema = _schema()
    definitions = schema["$defs"]
    assert type(definitions) is dict
    payload = definitions["response_payload"]
    assert type(payload) is dict
    properties = payload["properties"]
    assert type(properties) is dict
    assert properties["analysis_status"] == {"enum": EXPECTED_ANALYSIS_STATUSES}
    assert properties["anchor_associations_status"] == {"const": "UNAVAILABLE"}
    assert properties["scheduled_events_status"] == {"const": "UNAVAILABLE"}
    assert properties["regime_observation_status"] == {"const": "UNAVAILABLE"}

    instrument_view = definitions["instrument_view"]
    assert type(instrument_view) is dict
    instrument_properties = instrument_view["properties"]
    assert type(instrument_properties) is dict
    assert instrument_properties["evidence_status"] == {
        "enum": EXPECTED_EVIDENCE_STATUSES
    }


@pytest.mark.parametrize(
    ("status", "rationale", "references"),
    [
        ("UNAVAILABLE", "must be absent", []),
        ("UNAVAILABLE", None, ["POLICY.INSTRUMENT.0001"]),
        ("EVIDENCE_SUPPORTED", None, ["POLICY.INSTRUMENT.0001"]),
        ("INSUFFICIENT_EVIDENCE", "", ["POLICY.INSTRUMENT.0001"]),
        ("CONTRADICTED", "Explanation.", []),
    ],
)
def test_instrument_view_status_conditionals_fail_closed(
    status: str,
    rationale: object,
    references: list[str],
) -> None:
    candidate = _artifact()
    row = candidate["response_payload"]["instrument_views"][0]  # type: ignore[index]
    row["evidence_status"] = status
    row["rationale_12m_plus"] = rationale
    row["references"] = references
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)


def test_schema_rejects_invalid_rows_statuses_references_and_extensions() -> None:
    candidate = _artifact()
    row = candidate["response_payload"]["instrument_views"][0]  # type: ignore[index]
    row["evidence_status"] = "SUPPORTED"
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)

    candidate = _artifact()
    row = candidate["response_payload"]["instrument_views"][0]  # type: ignore[index]
    row["references"] = ["ANCHOR.0001"]
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)

    candidate = _artifact()
    row = candidate["response_payload"]["instrument_views"][0]  # type: ignore[index]
    row["anchor_id"] = "forbidden"
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)

    candidate = _artifact()
    candidate["response_payload"]["recommended_action"] = "BUY"  # type: ignore[index]
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)


def test_tier_a_reference_grammar_is_exact_and_closed() -> None:
    schema = _schema()
    definitions = schema["$defs"]
    assert type(definitions) is dict
    evidence_reference = definitions["evidence_reference"]
    assert type(evidence_reference) is dict
    assert evidence_reference == {"oneOf": EXACT_EVIDENCE_REFERENCE_ONE_OF}

    expected_references = _expected_tier_a_references()
    assert len(expected_references) == 547
    reference_validator = Draft202012Validator(evidence_reference)
    assert all(reference_validator.is_valid(value) for value in expected_references)

    unrelated_reference = "UNRELATED.CLASS.0001"
    assert unrelated_reference not in expected_references
    assert not reference_validator.is_valid(unrelated_reference)


def test_item_summary_links_and_array_bounds_are_exact() -> None:
    schema = _schema()
    definitions = schema["$defs"]
    assert type(definitions) is dict

    instrument_view = definitions["instrument_view"]
    assert type(instrument_view) is dict
    assert instrument_view["additionalProperties"] is False
    assert set(instrument_view["required"]) == {
        "ticker",
        "evidence_status",
        "rationale_12m_plus",
        "references",
    }
    assert set(instrument_view["properties"]) == set(instrument_view["required"])
    assert instrument_view["properties"]["evidence_status"] == {
        "enum": EXPECTED_EVIDENCE_STATUSES
    }
    assert instrument_view["properties"]["references"] == {
        "$ref": "#/$defs/instrument_references"
    }
    assert instrument_view["properties"]["rationale_12m_plus"] == {
        "oneOf": [
            {"type": "null"},
            {"type": "string", "minLength": 1, "maxLength": 2000},
        ]
    }
    assert instrument_view["allOf"] == [
        {
            "if": {
                "properties": {"evidence_status": {"const": "UNAVAILABLE"}},
                "required": ["evidence_status"],
            },
            "then": {
                "properties": {
                    "rationale_12m_plus": {"type": "null"},
                    "references": {"maxItems": 0},
                }
            },
            "else": {
                "properties": {
                    "rationale_12m_plus": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2000,
                    },
                    "references": {"minItems": 1},
                }
            },
        }
    ]

    analysis_item = definitions["analysis_item"]
    assert type(analysis_item) is dict
    assert analysis_item["type"] == "object"
    assert analysis_item["additionalProperties"] is False
    assert set(analysis_item["required"]) == {"text", "references", "hypothesis"}
    assert analysis_item["properties"] == {
        "text": {"type": "string", "minLength": 1, "maxLength": 2000},
        "references": {"$ref": "#/$defs/references"},
        "hypothesis": {"type": "boolean"},
    }
    summary = definitions["summary"]
    assert type(summary) is dict
    assert summary["type"] == "object"
    assert summary["additionalProperties"] is False
    assert set(summary["required"]) == {"text", "references", "hypothesis"}
    assert summary["properties"] == {
        "text": {"type": "string", "minLength": 1, "maxLength": 4000},
        "references": {"$ref": "#/$defs/references"},
        "hypothesis": {"type": "boolean"},
    }

    payload = definitions["response_payload"]
    assert type(payload) is dict
    payload_properties = payload["properties"]
    assert type(payload_properties) is dict
    expected_array_links = {
        "evidence_observations": "#/$defs/analysis_items_12",
        "risks": "#/$defs/analysis_items_12",
        "uncertainties": "#/$defs/analysis_items_12",
        "contradictions": "#/$defs/analysis_items_8",
        "research_questions": "#/$defs/analysis_items_12",
    }
    for field, reference in expected_array_links.items():
        assert payload_properties[field] == {"$ref": reference}
    assert definitions["analysis_items_12"] == {
        "type": "array",
        "minItems": 0,
        "maxItems": 12,
        "items": {"$ref": "#/$defs/analysis_item"},
    }
    assert definitions["analysis_items_8"] == {
        "type": "array",
        "minItems": 0,
        "maxItems": 8,
        "items": {"$ref": "#/$defs/analysis_item"},
    }
    assert definitions["instrument_references"] == {
        "type": "array",
        "minItems": 0,
        "maxItems": 8,
        "uniqueItems": True,
        "items": {"$ref": "#/$defs/evidence_reference"},
    }
    assert definitions["references"] == {
        "type": "array",
        "minItems": 1,
        "maxItems": 8,
        "uniqueItems": True,
        "items": {"$ref": "#/$defs/evidence_reference"},
    }


def test_resource_contract_is_strictly_below_existing_global_limits() -> None:
    assert MAX_MMI_GROUNDED_ANALYSIS_RESPONSE_V2_CANONICAL_BYTES == 245_760
    assert (
        MAX_MMI_GROUNDED_ANALYSIS_RESPONSE_V2_CANONICAL_BYTES
        < MAXIMUM_MMI_RAW_RESPONSE_BYTES
        < MAXIMUM_CANONICAL_JSON_BYTES
    )
    assert MAXIMUM_CANONICAL_DEPTH == 32
    assert MAXIMUM_CANONICAL_NODES == 16_384


def test_h02c_has_r2c_runtime_without_standalone_r2b_artifact() -> None:
    root = repo_root()
    assert (
        root
        / "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response_v2.py"
    ).exists()
    assert not (
        root / "schemas/mmi_grounded_analysis_response_v2.schema.json"
    ).exists()
    domains = _identity_domains()
    assert len(domains) == len(set(domains)) == 20
    assert b"mmi_validated_grounded_analysis_response_v2\0" in domains
    assert mmi.__all__ == ()
