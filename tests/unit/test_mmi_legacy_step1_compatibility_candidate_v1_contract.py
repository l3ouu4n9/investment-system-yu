from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
import struct

from jsonschema import Draft202012Validator
import pytest

import investment_orchestrator.mmi as mmi
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.mmi import canonical
from investment_orchestrator.mmi.canonical import (
    MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES,
    _MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_IDENTITY_DOMAIN,
)
from investment_orchestrator.mmi.legacy_step1_compatibility_candidate_v1 import (
    MmiLegacyStep1CompatibilityCandidateV1Error,
    _candidate_identity,
    _validate_candidate_canonical_size,
    build_mmi_legacy_step1_compatibility_candidate_v1,
    validate_mmi_legacy_step1_compatibility_candidate_v1,
)


SCHEMA_NAME = "mmi_legacy_step1_compatibility_candidate_v1.schema.json"
SCHEMA_PATH = repo_root() / "schemas" / SCHEMA_NAME
R2_SCHEMA_PATH = (
    repo_root()
    / "schemas/mmi_validated_grounded_analysis_response_v2.schema.json"
)
IDENTITY_FIELD = (
    "legacy_step1_compatibility_candidate_identity_sha256"
)
IDENTITY_DOMAIN = b"mmi_legacy_step1_compatibility_candidate_v1\0"
SHA256 = "a" * 64
ROOT_FIELDS = {
    "schema_version",
    "artifact_kind",
    "compiler_contract_version",
    "report_only",
    "authority_effect",
    "provenance",
    "analysis_status",
    "ordered_instrument_assessments",
    "evidence_observations",
    "risks",
    "uncertainties",
    "contradictions",
    "research_questions",
    "summary",
    "source_capability_statuses",
    IDENTITY_FIELD,
}
PROVENANCE_FIELDS = {
    "analyst_visible_evidence_view_identity_sha256",
    "validated_grounded_analysis_response_identity_sha256",
}
ASSESSMENT_FIELDS = {
    "ticker",
    "policy_role",
    "evidence_status",
    "rationale_12m_plus",
    "references",
}
ANALYSIS_ITEM_FIELDS = {"text", "references", "hypothesis"}
SOURCE_CAPABILITY_FIELDS = {
    "anchor_associations_status",
    "scheduled_events_status",
    "regime_inputs_status",
    "target_weights_absence_reason",
}


def _schema() -> dict[str, object]:
    value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _record_identity(value: dict[str, object]) -> str:
    preimage = deepcopy(value)
    preimage.pop(IDENTITY_FIELD, None)
    canonical_bytes = _canonical(preimage)
    return hashlib.sha256(
        IDENTITY_DOMAIN
        + struct.pack(">Q", len(canonical_bytes))
        + canonical_bytes
    ).hexdigest()


def _artifact() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "mmi_legacy_step1_compatibility_candidate_v1",
        "artifact_kind": "MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE",
        "compiler_contract_version": (
            "mmi_legacy_step1_compatibility_compiler_v1"
        ),
        "report_only": True,
        "authority_effect": "NONE",
        "provenance": {
            "analyst_visible_evidence_view_identity_sha256": SHA256,
            "validated_grounded_analysis_response_identity_sha256": (
                "b" * 64
            ),
        },
        "analysis_status": "INSUFFICIENT_EVIDENCE",
        "ordered_instrument_assessments": [
            {
                "ticker": "QQQ",
                "policy_role": "CORE",
                "evidence_status": "UNAVAILABLE",
                "rationale_12m_plus": None,
                "references": [],
            },
            {
                "ticker": "SMH",
                "policy_role": "SATELLITE",
                "evidence_status": "EVIDENCE_SUPPORTED",
                "rationale_12m_plus": "Visible evidence supports review.",
                "references": ["POLICY.INSTRUMENT.0002"],
            },
        ],
        "evidence_observations": [],
        "risks": [],
        "uncertainties": [],
        "contradictions": [],
        "research_questions": [],
        "summary": {
            "text": "Qualitative evidence remains report-only.",
            "references": ["VIEW.EVALUATION_TIMESTAMP"],
            "hypothesis": False,
        },
        "source_capability_statuses": {
            "anchor_associations_status": "UNAVAILABLE",
            "scheduled_events_status": "UNAVAILABLE",
            "regime_inputs_status": "UNAVAILABLE",
            "target_weights_absence_reason": (
                "POLICY_METHOD_HAS_NO_TARGET_WEIGHTS"
            ),
        },
        IDENTITY_FIELD: "0" * 64,
    }
    value[IDENTITY_FIELD] = _record_identity(value)
    return value


def _object_schemas(value: object):
    if type(value) is dict:
        if value.get("type") == "object":
            yield value
        for child in value.values():
            yield from _object_schemas(child)
    elif type(value) is list:
        for child in value:
            yield from _object_schemas(child)


def test_closed_schema_root_and_nested_property_sets_are_exact() -> None:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == ROOT_FIELDS
    properties = schema["properties"]
    assert type(properties) is dict
    assert set(properties) == ROOT_FIELDS
    assert properties["schema_version"] == {
        "const": "mmi_legacy_step1_compatibility_candidate_v1"
    }
    assert properties["artifact_kind"] == {
        "const": "MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE"
    }
    assert properties["compiler_contract_version"] == {
        "const": "mmi_legacy_step1_compatibility_compiler_v1"
    }
    assert properties["report_only"] == {"const": True}
    assert properties["authority_effect"] == {"const": "NONE"}
    definitions = schema["$defs"]
    assert type(definitions) is dict
    expected = {
        "provenance": PROVENANCE_FIELDS,
        "instrument_assessment": ASSESSMENT_FIELDS,
        "analysis_item": ANALYSIS_ITEM_FIELDS,
        "summary": ANALYSIS_ITEM_FIELDS,
        "source_capability_statuses": SOURCE_CAPABILITY_FIELDS,
    }
    for name, fields in expected.items():
        definition = definitions[name]
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == fields
        assert set(definition["properties"]) == fields
    assert all(
        item.get("additionalProperties") is False
        for item in _object_schemas(schema)
    )


def test_schema_reuses_exact_native_r2_qualitative_and_reference_bounds() -> None:
    schema = _schema()
    r2_schema = json.loads(R2_SCHEMA_PATH.read_text(encoding="utf-8"))
    definitions = schema["$defs"]
    r2_definitions = r2_schema["$defs"]
    assert definitions["evidence_reference"] == r2_definitions[
        "evidence_reference"
    ]
    assert definitions["instrument_references"] == r2_definitions[
        "instrument_references"
    ]
    assert definitions["references"] == r2_definitions["references"]
    assert definitions["analysis_items_12"] == r2_definitions[
        "analysis_items_12"
    ]
    assert definitions["analysis_items_8"] == r2_definitions[
        "analysis_items_8"
    ]
    assert definitions["analysis_item"] == r2_definitions["analysis_item"]
    assert definitions["summary"] == r2_definitions["summary"]
    assessment = definitions["instrument_assessment"]
    assert assessment["properties"]["evidence_status"] == r2_definitions[
        "instrument_view"
    ]["properties"]["evidence_status"]
    assert assessment["properties"]["rationale_12m_plus"] == (
        r2_definitions["instrument_view"]["properties"][
            "rationale_12m_plus"
        ]
    )
    assert assessment["allOf"] == r2_definitions["instrument_view"][
        "allOf"
    ]


def test_schema_accepts_only_minimal_provenance_and_capability_statuses() -> None:
    value = _artifact()
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    provenance = value["provenance"]
    statuses = value["source_capability_statuses"]
    assert type(provenance) is dict
    assert type(statuses) is dict
    assert set(provenance) == PROVENANCE_FIELDS
    assert set(statuses) == SOURCE_CAPABILITY_FIELDS
    assert "UNRESOLVED_LEGACY_CONTRACT" not in _canonical(value).decode()
    for field in (
        "prompt_context_binding_sha256",
        "grounded_prompt_artifact_identity_sha256",
        "raw_response_envelope_identity_sha256",
    ):
        changed = deepcopy(value)
        changed_provenance = changed["provenance"]
        assert type(changed_provenance) is dict
        changed_provenance[field] = SHA256
        with pytest.raises(ArtifactSchemaError):
            validate_artifact_schema(changed, schema_name=SCHEMA_NAME)


def test_schema_rejects_one_unknown_authority_field_and_has_no_authority_surface(
) -> None:
    value = _artifact()
    value["new_buy_permission"] = True
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    property_names = {
        key
        for item in _object_schemas(_schema())
        for key in item.get("properties", {})
    }
    assert not property_names & {
        "availability",
        "freshness",
        "recommendation",
        "ranking",
        "priority",
        "shortlist_eligibility",
        "hold",
        "no_trade",
        "sell",
        "new_buy",
        "order_compilation",
        "budget",
        "cap",
        "allocation",
        "quantity",
        "gate_result",
        "publication_readiness",
        "order_readiness",
        "execution_readiness",
    }


def test_identity_domain_framing_and_complete_preimage_are_exact() -> None:
    value = _artifact()
    assert set(value) - {IDENTITY_FIELD} == ROOT_FIELDS - {IDENTITY_FIELD}
    assert _candidate_identity(value) == _record_identity(value)
    assert value[IDENTITY_FIELD] == _record_identity(value)
    nested = deepcopy(value)
    summary = nested["summary"]
    assert type(summary) is dict
    summary["text"] = "Nested qualitative mutation."
    assert _record_identity(nested) != value[IDENTITY_FIELD]
    self_only = deepcopy(value)
    self_only[IDENTITY_FIELD] = "f" * 64
    assert _record_identity(self_only) == value[IDENTITY_FIELD]
    assert (
        _MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_IDENTITY_DOMAIN
        == IDENTITY_DOMAIN
    )


def test_private_complete_candidate_resource_guard_has_exact_boundary() -> None:
    maximum = (
        MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES
    )
    empty_size = len(_canonical({"padding": ""}))
    at_limit = {"padding": "x" * (maximum - empty_size)}
    above_limit = {"padding": "x" * (maximum + 1 - empty_size)}
    assert len(_canonical(at_limit)) == maximum
    assert len(_canonical(above_limit)) == maximum + 1
    _validate_candidate_canonical_size(at_limit)
    with pytest.raises(MmiLegacyStep1CompatibilityCandidateV1Error):
        _validate_candidate_canonical_size(above_limit)


def test_public_signatures_are_exact_keyword_only_and_default_free() -> None:
    source_inputs = (
        "validated_grounded_analysis_response",
        "raw_response_envelope",
        "evidence_bundle",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    )
    assert tuple(
        inspect.signature(
            build_mmi_legacy_step1_compatibility_candidate_v1
        ).parameters
    ) == source_inputs
    assert tuple(
        inspect.signature(
            validate_mmi_legacy_step1_compatibility_candidate_v1
        ).parameters
    ) == ("value", *source_inputs)
    for function in (
        build_mmi_legacy_step1_compatibility_candidate_v1,
        validate_mmi_legacy_step1_compatibility_candidate_v1,
    ):
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            and parameter.default is inspect.Parameter.empty
            for parameter in inspect.signature(function).parameters.values()
        )


def test_inventory_domain_schema_and_package_posture_are_exact() -> None:
    production_paths = tuple(
        sorted(
            (repo_root() / "src/investment_orchestrator").rglob("*.py")
        )
    )
    schema_paths = tuple(
        sorted((repo_root() / "schemas").glob("*.schema.json"))
    )
    domains = tuple(
        value
        for value in vars(canonical).values()
        if type(value) is bytes
        and value.startswith(b"mmi_")
        and value.endswith(b"\0")
    )
    assert len(production_paths) == 148
    assert len(schema_paths) == 45
    assert len(domains) == len(set(domains)) == 19
    assert IDENTITY_DOMAIN in domains
    assert SCHEMA_PATH in schema_paths
    assert mmi.__all__ == ()
