"""Permanent contract tests for the frozen WEEKLY-SHADOW-01 (WS01a) foundation."""

from __future__ import annotations

import ast
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.observability import weekly_shadow_01_contracts as m


WS01A_SCHEMA_FILENAMES = (
    "weekly_shadow_01_analyst_input.schema.json",
    "weekly_shadow_01_analyst_response.schema.json",
    "weekly_shadow_01_response_capture.schema.json",
    "weekly_shadow_01_response_validation.schema.json",
    "weekly_shadow_01_analyst_report.schema.json",
    "weekly_shadow_01_run_summary.schema.json",
)

_SHA = "a" * 64
_NEG_AUTH = {
    "authority_effect": "none",
    "permission_effect": "none",
    "approval_eligible": False,
    "precompile_eligible": False,
    "order_eligible": False,
    "portfolio_effect": "none",
    "order_path_effect": "none",
    "execution_authority": False,
}


def _load_schema(filename: str) -> dict:
    return json.loads((repo_root() / "schemas" / filename).read_text(encoding="utf-8"))


def _assert_closed(node: object, *, location: str = "$") -> None:
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, location
        for key, value in node.items():
            _assert_closed(value, location=f"{location}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_closed(value, location=f"{location}/{index}")


def _find_type_nodes(node: object, banned_types: set, *, location: str = "$") -> list:
    hits: list = []
    if isinstance(node, dict):
        declared = node.get("type")
        declared_set = {declared} if isinstance(declared, str) else set(declared or [])
        if declared_set & banned_types:
            hits.append(location)
        for key, value in node.items():
            hits.extend(_find_type_nodes(value, banned_types, location=f"{location}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(_find_type_nodes(value, banned_types, location=f"{location}/{index}"))
    return hits


def _find_external_refs(node: object) -> list:
    refs: list = []
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#/"):
            refs.append(ref)
        for value in node.values():
            refs.extend(_find_external_refs(value))
    elif isinstance(node, list):
        for value in node:
            refs.extend(_find_external_refs(value))
    return refs


def _all_property_names(node: object) -> set:
    names: set = set()
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for value in node.values():
            names.update(_all_property_names(value))
    elif isinstance(node, list):
        for value in node:
            names.update(_all_property_names(value))
    return names


def _all_enum_and_const_values(node: object) -> set:
    values: set = set()
    if isinstance(node, dict):
        if "enum" in node and isinstance(node["enum"], list):
            values.update(item for item in node["enum"] if isinstance(item, str))
        if "const" in node and isinstance(node["const"], str):
            values.add(node["const"])
        for value in node.values():
            values.update(_all_enum_and_const_values(value))
    elif isinstance(node, list):
        for value in node:
            values.update(_all_enum_and_const_values(value))
    return values


def _valid_analyst_input() -> dict:
    return {
        "schema_version": "weekly_shadow_01_analyst_input_v1",
        "run_id": "run-2026-07-20",
        "adapter_id": "r2f-legacy-adapter",
        "adapter_version": "r2f_legacy_adapter_v1",
        "source_generation_id": "gen-2026-07-20",
        "evaluation_timestamp_utc": "2026-07-20T00:00:00Z",
        "source_artifact_bindings": [
            {"source_id": f"source-{i}", "source_artifact_identity_sha256": _SHA} for i in range(4)
        ],
        "evidence_records": [],
        "availability_freshness_diagnostics": [],
        "permitted_question_ids": [],
        "prohibited_conclusion_ids": list(m.PROHIBITED_ANALYST_CONCLUSION_VALUES),
        "resource_bound_profile_identity_sha256": _SHA,
        "prompt_template_identity_sha256": _SHA,
        "negative_authority_profile": dict(_NEG_AUTH),
        "input_package_identity_sha256": _SHA,
    }


def _valid_analytical_sections() -> dict:
    return {"observations": [], "risks_and_uncertainties": [], "missing_evidence_notes": []}


def _valid_analyst_response(*, conclusion: str = "OBSERVATIONS_AVAILABLE", confidence: str = "MEDIUM") -> dict:
    return {
        "schema_version": "weekly_shadow_01_analyst_response_v1",
        "stage_version": "weekly_shadow_01_stage_a_v1",
        "run_id": "run-2026-07-20",
        "input_package_identity_sha256": _SHA,
        "prompt_template_identity_sha256": _SHA,
        "source_generation_id": "gen-2026-07-20",
        "source_artifact_bindings": [
            {"source_id": f"source-{i}", "source_artifact_identity_sha256": _SHA} for i in range(4)
        ],
        "evidence_record_bindings": [],
        "analyst_conclusion": conclusion,
        "analyst_confidence": confidence,
        "analytical_sections": _valid_analytical_sections(),
        "analyst_limitation_codes": [],
        "negative_authority": dict(_NEG_AUTH),
    }


def _valid_response_capture() -> dict:
    return {
        "schema_version": "weekly_shadow_01_response_capture_v1",
        "run_id": "run-2026-07-20",
        "input_package_identity_sha256": _SHA,
        "source_generation_id": "gen-2026-07-20",
        "raw_response_base64": "",
        "raw_response_sha256": _SHA,
        "raw_response_byte_size": 0,
        "negative_authority_profile": dict(_NEG_AUTH),
        "response_capture_identity_sha256": _SHA,
    }


def _valid_response_validation(*, status: str = "VALID") -> dict:
    if status == "VALID":
        return {
            "schema_version": "weekly_shadow_01_response_validation_v1",
            "run_id": "run-2026-07-20",
            "input_package_identity_sha256": _SHA,
            "response_capture_identity_sha256": _SHA,
            "validation_status": "VALID",
            "blocking_reason_codes": [],
            "validator_diagnostics": [],
            "report_payload_constructible": True,
            "negative_authority_profile": dict(_NEG_AUTH),
            "validation_identity_sha256": _SHA,
        }
    return {
        "schema_version": "weekly_shadow_01_response_validation_v1",
        "run_id": "run-2026-07-20",
        "input_package_identity_sha256": _SHA,
        "response_capture_identity_sha256": _SHA,
        "validation_status": "INVALID",
        "blocking_reason_codes": ["WS01_BR_RESPONSE_MISSING"],
        "validator_diagnostics": [],
        "report_payload_constructible": False,
        "negative_authority_profile": dict(_NEG_AUTH),
        "validation_identity_sha256": _SHA,
    }


def _valid_analyst_report() -> dict:
    return {
        "schema_version": "weekly_shadow_01_analyst_report_v1",
        "run_id": "run-2026-07-20",
        "input_package_identity_sha256": _SHA,
        "response_capture_identity_sha256": _SHA,
        "validation_identity_sha256": _SHA,
        "code_owned_status": {
            "run_status": "ANALYSIS_COMPLETE",
            "validation_status": "VALID",
            "publication_status": "PUBLISHED",
            "blocking_reason_codes": [],
        },
        "validated_analyst_content": {
            "analyst_conclusion": "OBSERVATIONS_AVAILABLE",
            "analyst_confidence": "MEDIUM",
            "analytical_sections": _valid_analytical_sections(),
            "analyst_limitation_codes": [],
        },
        "negative_authority_profile": dict(_NEG_AUTH),
        "report_identity_sha256": _SHA,
    }


def _valid_run_summary(*, status: str = "ANALYSIS_COMPLETE") -> dict:
    if status == "ANALYSIS_COMPLETE":
        return {
            "schema_version": "weekly_shadow_01_run_summary_v1",
            "run_id": "run-2026-07-20",
            "run_status": "ANALYSIS_COMPLETE",
            "validation_status": "VALID",
            "publication_status": "PUBLISHED",
            "blocking_reason_codes": [],
            "report_identity_sha256": _SHA,
            "negative_authority_profile": dict(_NEG_AUTH),
            "run_summary_identity_sha256": _SHA,
        }
    return {
        "schema_version": "weekly_shadow_01_run_summary_v1",
        "run_id": "run-2026-07-20",
        "run_status": "BLOCKED",
        "validation_status": "INVALID",
        "publication_status": "NOT_ATTEMPTED",
        "blocking_reason_codes": ["WS01_BR_RESPONSE_MISSING"],
        "report_identity_sha256": None,
        "negative_authority_profile": dict(_NEG_AUTH),
        "run_summary_identity_sha256": _SHA,
    }


# --- schema discovery and closure --------------------------------------------


def test_all_six_schemas_are_discovered_and_no_existing_schema_was_removed() -> None:
    discovered = {path.name for path in (repo_root() / "schemas").glob("*.schema.json")}
    assert set(WS01A_SCHEMA_FILENAMES) <= discovered
    assert tuple(sorted(m.SCHEMA_FILENAME_BY_VERSION[key].rsplit("/", 1)[-1] for key in m.SCHEMA_FILENAME_BY_VERSION)) == tuple(
        sorted(WS01A_SCHEMA_FILENAMES)
    )
    # Pre-existing LTETF-02a1 schemas must still all be present.
    preexisting = {
        "ltetf_source_authority_policy.schema.json",
        "ltetf_authorized_source_registry.schema.json",
        "ltetf_field_freshness_policy.schema.json",
        "ltetf_operator_policy_acceptance.schema.json",
        "ltetf_generic_evidence_manifest.schema.json",
        "ltetf_trusted_evaluation_epoch.schema.json",
        "ltetf_structured_market_metrics.schema.json",
        "ltetf_structured_scheduled_events.schema.json",
        "ltetf_prior_thesis_continuity.schema.json",
        "blocked_run_summary.schema.json",
        "run_context.schema.json",
    }
    assert preexisting <= discovered


@pytest.mark.parametrize("filename", WS01A_SCHEMA_FILENAMES)
def test_each_schema_is_draft_2020_12_closed_and_has_no_external_ref(filename: str) -> None:
    schema = _load_schema(filename)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == f"https://investment-system.local/schemas/{filename}"
    assert schema["additionalProperties"] is False
    _assert_closed(schema)
    assert _find_external_refs(schema) == []


def test_analyst_response_schema_has_no_numeric_instance_type_anywhere() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_response.schema.json")
    assert _find_type_nodes(schema, {"number", "integer"}) == []


def test_analyst_response_schema_contains_no_code_owned_field_or_prohibited_value() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_response.schema.json")
    names = _all_property_names(schema)
    forbidden_fields = {
        "run_status",
        "validation_status",
        "publication_status",
        "blocking_reason_codes",
        "integrity_codes",
        "validator_diagnostics",
        "publication_diagnostics",
        "analyst_outcome",
    }
    assert not forbidden_fields & names

    values = _all_enum_and_const_values(schema)
    forbidden_values = {"ANALYSIS_COMPLETE", "BLOCKED", "NO_TRADE"} | set(m.PROHIBITED_ANALYST_CONCLUSION_VALUES)
    assert not forbidden_values & values


def test_response_validation_and_run_summary_only_expose_blocking_reason_codes_not_limitation_codes() -> None:
    for filename in (
        "weekly_shadow_01_response_validation.schema.json",
        "weekly_shadow_01_run_summary.schema.json",
    ):
        schema = _load_schema(filename)
        values = _all_enum_and_const_values(schema)
        assert not set(m.ANALYST_LIMITATION_CODES) & values
        assert set(m.BLOCKING_REASON_CODES) <= values


def test_analyst_response_and_analyst_report_only_expose_limitation_codes_not_blocking_reasons() -> None:
    for filename in (
        "weekly_shadow_01_analyst_response.schema.json",
        "weekly_shadow_01_analyst_report.schema.json",
    ):
        schema = _load_schema(filename)
        values = _all_enum_and_const_values(schema)
        assert not set(m.BLOCKING_REASON_CODES) & values
        assert set(m.ANALYST_LIMITATION_CODES) <= values


def test_only_code_produced_schemas_declare_a_run_status_field() -> None:
    run_status_bearing = {
        "weekly_shadow_01_analyst_report.schema.json",
        "weekly_shadow_01_run_summary.schema.json",
    }
    for filename in WS01A_SCHEMA_FILENAMES:
        schema = _load_schema(filename)
        has_run_status = "run_status" in _all_property_names(schema)
        assert has_run_status == (filename in run_status_bearing), filename


# --- schema instance validation (state ownership) ----------------------------


def test_valid_analyst_input_instance_validates() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    Draft202012Validator(schema).validate(_valid_analyst_input())


@pytest.mark.parametrize("conclusion", list(m.ANALYST_CONCLUSION_VALUES))
@pytest.mark.parametrize("confidence", list(m.ANALYST_CONFIDENCE_VALUES))
def test_all_conclusion_and_confidence_combinations_validate(conclusion: str, confidence: str) -> None:
    schema = _load_schema("weekly_shadow_01_analyst_response.schema.json")
    Draft202012Validator(schema).validate(_valid_analyst_response(conclusion=conclusion, confidence=confidence))


@pytest.mark.parametrize(
    "forbidden_field,forbidden_value",
    [
        ("run_status", "ANALYSIS_COMPLETE"),
        ("run_status", "BLOCKED"),
        ("validation_status", "VALID"),
        ("publication_status", "PUBLISHED"),
        ("blocking_reason_codes", []),
        ("analyst_outcome", "NO_TRADE"),
        ("integrity_codes", []),
    ],
)
def test_analyst_response_rejects_any_code_owned_field(forbidden_field: str, forbidden_value: object) -> None:
    schema = _load_schema("weekly_shadow_01_analyst_response.schema.json")
    instance = _valid_analyst_response()
    instance[forbidden_field] = forbidden_value
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


@pytest.mark.parametrize("prohibited", list(m.PROHIBITED_ANALYST_CONCLUSION_VALUES))
def test_analyst_response_rejects_prohibited_conclusion_values(prohibited: str) -> None:
    schema = _load_schema("weekly_shadow_01_analyst_response.schema.json")
    instance = _valid_analyst_response()
    instance["analyst_conclusion"] = prohibited
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


@pytest.mark.parametrize("bad_confidence", [0, 1, 0.5, "1", "50%", "0.9", "HIGH_CONFIDENCE", None, True])
def test_analyst_response_rejects_numeric_or_unknown_confidence(bad_confidence: object) -> None:
    schema = _load_schema("weekly_shadow_01_analyst_response.schema.json")
    instance = _valid_analyst_response()
    instance["analyst_confidence"] = bad_confidence
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


def test_response_capture_and_validation_and_report_valid_instances_validate() -> None:
    Draft202012Validator(_load_schema("weekly_shadow_01_response_capture.schema.json")).validate(
        _valid_response_capture()
    )
    Draft202012Validator(_load_schema("weekly_shadow_01_response_validation.schema.json")).validate(
        _valid_response_validation(status="VALID")
    )
    Draft202012Validator(_load_schema("weekly_shadow_01_response_validation.schema.json")).validate(
        _valid_response_validation(status="INVALID")
    )
    Draft202012Validator(_load_schema("weekly_shadow_01_analyst_report.schema.json")).validate(
        _valid_analyst_report()
    )


def test_run_summary_valid_analysis_complete_and_blocked_instances_validate() -> None:
    schema = _load_schema("weekly_shadow_01_run_summary.schema.json")
    Draft202012Validator(schema).validate(_valid_run_summary(status="ANALYSIS_COMPLETE"))
    Draft202012Validator(schema).validate(_valid_run_summary(status="BLOCKED"))


def test_run_summary_blocked_due_to_publication_failure_with_valid_validation_status_validates() -> None:
    schema = _load_schema("weekly_shadow_01_run_summary.schema.json")
    instance = _valid_run_summary(status="BLOCKED")
    instance["validation_status"] = "VALID"
    instance["publication_status"] = "FAILED"
    Draft202012Validator(schema).validate(instance)


def test_run_summary_conclusion_and_confidence_fields_do_not_exist_and_cannot_alter_constraints() -> None:
    schema = _load_schema("weekly_shadow_01_run_summary.schema.json")
    assert "analyst_conclusion" not in _all_property_names(schema)
    assert "analyst_confidence" not in _all_property_names(schema)
    instance = _valid_run_summary(status="ANALYSIS_COMPLETE")
    instance["analyst_conclusion"] = "OBSERVATIONS_AVAILABLE"
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda i: i.update(report_identity_sha256=None),
        lambda i: i.update(publication_status="NOT_ATTEMPTED"),
        lambda i: i.update(blocking_reason_codes=["WS01_BR_RESPONSE_MISSING"]),
    ],
)
def test_run_summary_analysis_complete_consistency_is_enforced(mutation) -> None:
    schema = _load_schema("weekly_shadow_01_run_summary.schema.json")
    instance = _valid_run_summary(status="ANALYSIS_COMPLETE")
    mutation(instance)
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda i: i.update(publication_status="PUBLISHED"),
        lambda i: i.update(report_identity_sha256=_SHA),
        lambda i: i.update(blocking_reason_codes=[]),
    ],
)
def test_run_summary_blocked_consistency_is_enforced(mutation) -> None:
    schema = _load_schema("weekly_shadow_01_run_summary.schema.json")
    instance = _valid_run_summary(status="BLOCKED")
    mutation(instance)
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


def test_response_validation_status_consistency_is_enforced() -> None:
    schema = _load_schema("weekly_shadow_01_response_validation.schema.json")
    valid = _valid_response_validation(status="VALID")
    valid["report_payload_constructible"] = False
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(valid)

    invalid = _valid_response_validation(status="INVALID")
    invalid["blocking_reason_codes"] = []
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(invalid)


def test_source_artifact_bindings_must_be_exactly_four() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    instance = _valid_analyst_input()
    instance["source_artifact_bindings"].append(
        {"source_id": "source-4", "source_artifact_identity_sha256": _SHA}
    )
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)

    instance = _valid_analyst_input()
    instance["source_artifact_bindings"].pop()
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


def test_analytical_entries_require_at_least_one_reference() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_response.schema.json")
    instance = _valid_analyst_response()
    instance["analytical_sections"]["observations"] = [
        {"entry_id": "obs-1", "statement": "x", "evidence_record_ids": []}
    ]
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)

    instance = _valid_analyst_response()
    instance["analytical_sections"]["missing_evidence_notes"] = [
        {"entry_id": "mn-1", "statement": "x", "diagnostic_ids": []}
    ]
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


def test_analyst_limitation_entries_require_at_least_one_reference() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_response.schema.json")
    instance = _valid_analyst_response()
    instance["analyst_limitation_codes"] = [{"code": m.ANALYST_LIMITATION_CODES[0], "reference_ids": []}]
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


def test_an_invalid_response_cannot_publish_an_analyst_report() -> None:
    """An INVALID validation record structurally forbids report_payload_constructible."""
    schema = _load_schema("weekly_shadow_01_response_validation.schema.json")
    invalid = _valid_response_validation(status="INVALID")
    invalid["report_payload_constructible"] = True
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(invalid)


# --- diagnostics --------------------------------------------------------------


def test_blocking_reason_and_analyst_limitation_vocabularies_are_unique_prefixed_and_disjoint() -> None:
    assert len(m.BLOCKING_REASON_CODES) == len(set(m.BLOCKING_REASON_CODES)) == 32
    assert len(m.ANALYST_LIMITATION_CODES) == len(set(m.ANALYST_LIMITATION_CODES)) == 6
    assert all(code.startswith("WS01_BR_") for code in m.BLOCKING_REASON_CODES)
    assert all(code.startswith("WS01_AL_") for code in m.ANALYST_LIMITATION_CODES)
    assert not set(m.BLOCKING_REASON_CODES) & set(m.ANALYST_LIMITATION_CODES)


def test_wrong_prefix_or_unknown_code_fails_schema_validation() -> None:
    schema = _load_schema("weekly_shadow_01_response_validation.schema.json")
    instance = _valid_response_validation(status="INVALID")
    instance["blocking_reason_codes"] = ["WS01_AL_EVIDENCE_SPARSE"]
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)

    instance = _valid_response_validation(status="INVALID")
    instance["blocking_reason_codes"] = ["WS01_BR_NOT_A_REAL_CODE"]
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


def test_analyst_limitation_codes_cannot_populate_or_override_blocking_reason_codes() -> None:
    response_schema = _load_schema("weekly_shadow_01_analyst_response.schema.json")
    instance = _valid_analyst_response()
    instance["blocking_reason_codes"] = ["WS01_BR_RESPONSE_MISSING"]
    with pytest.raises(Exception):
        Draft202012Validator(response_schema).validate(instance)


# --- negative authority --------------------------------------------------------


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.__setitem__("authority_effect", "full"),
        lambda p: p.__setitem__("approval_eligible", True),
        lambda p: p.__setitem__("approval_eligible", "false"),
        lambda p: p.pop("execution_authority"),
        lambda p: p.__setitem__("extra_authority_field", "none"),
    ],
)
def test_negative_authority_profile_rejects_any_deviation(mutation) -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    instance = _valid_analyst_input()
    mutation(instance["negative_authority_profile"])
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


def test_no_schema_permits_a_trading_permission_or_execution_field() -> None:
    prohibited_property_names = {
        "side",
        "quantity",
        "shares",
        "weight",
        "allocation",
        "budget",
        "broker",
        "submit",
        "execute",
        "approval",
        "permission",
        "eligibility",
        "compiler",
        "order_side",
        "order_quantity",
    }
    for filename in WS01A_SCHEMA_FILENAMES:
        schema = _load_schema(filename)
        names = {name.lower() for name in _all_property_names(schema)}
        assert not prohibited_property_names & names, filename


# --- canonicalization and identities --------------------------------------------


def test_canonical_json_bytes_is_insertion_order_independent() -> None:
    a = {"x": 1, "y": 2, "z": [1, 2, 3]}
    b = {"z": [1, 2, 3], "y": 2, "x": 1}
    assert m.canonical_json_bytes(a) == m.canonical_json_bytes(b)


def test_canonical_json_bytes_preserves_array_order() -> None:
    assert m.canonical_json_bytes({"a": [1, 2]}) != m.canonical_json_bytes({"a": [2, 1]})


def test_canonical_json_bytes_distinguishes_booleans_from_integers() -> None:
    assert m.canonical_json_bytes({"a": True}) != m.canonical_json_bytes({"a": 1})
    assert b"true" in m.canonical_json_bytes({"a": True})


def test_canonical_json_bytes_rejects_float_and_nonfinite() -> None:
    with pytest.raises(m.CanonicalizationError):
        m.canonical_json_bytes({"a": 1.5})
    with pytest.raises(m.CanonicalizationError):
        m.canonical_json_bytes({"a": float("nan")})
    with pytest.raises(m.CanonicalizationError):
        m.canonical_json_bytes({"a": float("inf")})


def test_canonical_json_bytes_rejects_unsupported_type() -> None:
    with pytest.raises(m.CanonicalizationError):
        m.canonical_json_bytes({"a": object()})


def test_canonical_json_bytes_preserves_duplicate_array_entries_without_silent_dedup() -> None:
    payload = {"a": ["x", "x", "x"]}
    encoded = m.canonical_json_bytes(payload)
    assert json.loads(encoded)["a"] == ["x", "x", "x"]


def test_compute_identity_does_not_mutate_caller_payload() -> None:
    payload = {"a": 1, "self_id": "should-be-excluded"}
    frozen_copy = copy.deepcopy(payload)
    m.compute_identity("vocabulary_profile", payload, exclude_fields=("self_id",))
    assert payload == frozen_copy


def test_compute_identity_excludes_self_field_and_is_sensitive_to_other_changes() -> None:
    base = {"profile_version": "x", "value": 1, "self_id": _SHA}
    other_self_id = dict(base, self_id="b" * 64)
    changed_value = dict(base, value=2)
    identity_a = m.compute_identity("vocabulary_profile", base, exclude_fields=("self_id",))
    identity_b = m.compute_identity("vocabulary_profile", other_self_id, exclude_fields=("self_id",))
    identity_c = m.compute_identity("vocabulary_profile", changed_value, exclude_fields=("self_id",))
    assert identity_a == identity_b
    assert identity_a != identity_c


def test_mutating_a_returned_frozen_profile_copy_does_not_affect_the_module_constant() -> None:
    before = m.CONTRACT_CATALOG_IDENTITY_SHA256
    copied = dict(m.NEGATIVE_AUTHORITY_PROFILE)
    copied["authority_effect"] = "full"
    assert m.CONTRACT_CATALOG_IDENTITY_SHA256 == before
    assert m.NEGATIVE_AUTHORITY_PROFILE["authority_effect"] == "none"


def test_negative_authority_profile_and_resource_bound_profile_are_immutable_mappings() -> None:
    with pytest.raises(TypeError):
        m.NEGATIVE_AUTHORITY_PROFILE["authority_effect"] = "full"
    with pytest.raises(TypeError):
        m.RESOURCE_BOUND_PROFILE["max_diagnostics"] = 1


def test_all_sixteen_domain_separators_are_unique_and_nul_terminated() -> None:
    assert len(m.DOMAIN_SEPARATORS) == 16
    values = list(m.DOMAIN_SEPARATORS.values())
    assert len(values) == len(set(values))
    for value in values:
        assert isinstance(value, bytes)
        assert value.endswith(b"\0")
        assert b"\0" not in value[:-1]


def test_schema_and_semantic_contract_identities_are_all_distinct() -> None:
    schema_identities = list(m.SCHEMA_IDENTITY_SHA256_BY_VERSION.values())
    contract_identities = list(m.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION.values())
    combined = schema_identities + contract_identities
    assert len(combined) == len(set(combined)) == 12
    hex64 = re.compile(r"^[0-9a-f]{64}$")
    assert all(hex64.match(value) for value in combined)


def test_schema_identities_are_frozen_literals_that_match_the_actual_repository_bytes() -> None:
    for version, relative_path in m.SCHEMA_FILENAME_BY_VERSION.items():
        schema = json.loads((repo_root() / relative_path).read_bytes().decode("utf-8"))
        payload = {
            "schema_version": version,
            "schema_path": relative_path,
            "schema_id": schema["$id"],
            "schema": schema,
        }
        independent_identity = m.domain_separated_sha256(m.DOMAIN_SEPARATORS["schema_identity"], payload)
        assert independent_identity == m.SCHEMA_IDENTITY_SHA256_BY_VERSION[version]


def test_prompt_template_identity_is_sensitive_to_any_byte_change() -> None:
    mutated_bytes = m.PROMPT_TEMPLATE_BYTES[:-1] + b"X\n"
    mutated_identity = m.domain_separated_sha256(
        m.DOMAIN_SEPARATORS["prompt_template"],
        {
            "profile_version": "weekly_shadow_01_prompt_template_v1",
            "encoding": "utf-8",
            "newline_convention": "lf_only",
            "byte_size": len(mutated_bytes),
            "placeholder": m.PROMPT_TEMPLATE_PLACEHOLDER,
            "placeholder_occurrences": mutated_bytes.count(m.PROMPT_TEMPLATE_PLACEHOLDER.encode("utf-8")),
            "sha256": __import__("hashlib").sha256(mutated_bytes).hexdigest(),
        },
    )
    assert mutated_identity != m.PROMPT_TEMPLATE_IDENTITY_SHA256


def test_contract_catalog_identity_is_sensitive_to_the_full_frozen_surface() -> None:
    payload = copy.deepcopy(m._CONTRACT_CATALOG_PAYLOAD)
    baseline = m.compute_identity("contract_catalog", payload)
    assert baseline == m.CONTRACT_CATALOG_IDENTITY_SHA256

    for mutation in (
        lambda p: p["blocking_reason_codes"].append("WS01_BR_EXTRA"),
        lambda p: p["prohibited_key_terms"].append("extra_term"),
        lambda p: p.__setitem__("prompt_template_sha256", "0" * 64),
        lambda p: p["resource_bound_profile"].__setitem__("max_diagnostics", 999),
        lambda p: p["negative_authority_profile"].__setitem__("authority_effect", "full"),
    ):
        mutated = copy.deepcopy(m._CONTRACT_CATALOG_PAYLOAD)
        mutation(mutated)
        assert m.compute_identity("contract_catalog", mutated) != baseline


def test_identities_are_stable_across_fresh_interpreter_processes_and_hash_seeds() -> None:
    script = (
        "from investment_orchestrator.observability import weekly_shadow_01_contracts as m; "
        "print(m.CONTRACT_CATALOG_IDENTITY_SHA256)"
    )
    results = set()
    for seed in ("0", "1", "42"):
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root(),
            env={"PYTHONHASHSEED": seed, "PYTHONPATH": str(repo_root() / "src"), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=True,
        )
        results.add(proc.stdout.strip())
    assert len(results) == 1
    assert next(iter(results)) == m.CONTRACT_CATALOG_IDENTITY_SHA256


# --- prompt template -----------------------------------------------------------


def test_prompt_template_is_utf8_lf_only_no_bom_with_final_newline_and_one_placeholder() -> None:
    assert b"\r" not in m.PROMPT_TEMPLATE_BYTES
    assert not m.PROMPT_TEMPLATE_BYTES.startswith(b"\xef\xbb\xbf")
    assert m.PROMPT_TEMPLATE_BYTES.endswith(b"\n")
    assert m.PROMPT_TEMPLATE_BYTES.decode("utf-8")  # raises on invalid utf-8
    placeholder_bytes = m.PROMPT_TEMPLATE_PLACEHOLDER.encode("utf-8")
    assert m.PROMPT_TEMPLATE_BYTES.count(placeholder_bytes) == 1


@pytest.mark.parametrize(
    "expected_substring",
    [
        "qualitative research analysis only",
        "must not output a run status",
        "NO_TRADE",
        "BLOCKED",
        "OBSERVATIONS_AVAILABLE, NO_CHANGE_JUSTIFIED, INSUFFICIENT_EVIDENCE_FOR_CONCLUSION",
        "not a trade decision, not a HOLD decision",
        "not a validator result",
        "do not control, satisfy, or override any deterministic diagnostic",
        "never a number, a percentage, a probability, or a score",
        "Do not invent facts",
        "exactly one JSON object",
    ],
)
def test_prompt_template_contains_all_required_state_ownership_instructions(expected_substring: str) -> None:
    assert expected_substring in m.PROMPT_TEMPLATE_TEXT


def test_prompt_template_identity_is_deterministic() -> None:
    recomputed = m.compute_identity(
        "prompt_template",
        {
            "profile_version": "weekly_shadow_01_prompt_template_v1",
            "encoding": "utf-8",
            "newline_convention": "lf_only",
            "byte_size": len(m.PROMPT_TEMPLATE_BYTES),
            "placeholder": m.PROMPT_TEMPLATE_PLACEHOLDER,
            "placeholder_occurrences": 1,
            "sha256": __import__("hashlib").sha256(m.PROMPT_TEMPLATE_BYTES).hexdigest(),
        },
    )
    assert recomputed == m.PROMPT_TEMPLATE_IDENTITY_SHA256


# --- resource bounds ------------------------------------------------------------


def test_resource_bound_profile_matches_frozen_exact_constants() -> None:
    expected = {
        "source_artifact_count": 4,
        "source_artifact_max_bytes": 1_048_576,
        "source_artifacts_total_max_bytes": 4_194_304,
        "analyst_input_max_bytes": 524_288,
        "rendered_prompt_max_bytes": 786_432,
        "raw_response_max_bytes": 131_072,
        "response_capture_max_bytes": 196_608,
        "response_validation_max_bytes": 131_072,
        "analyst_report_max_bytes": 262_144,
        "run_summary_max_bytes": 65_536,
        "max_nesting_depth": 16,
        "max_object_members": 1_024,
        "max_array_items": 1_024,
        "max_evidence_records": 256,
        "max_entries_per_analytical_section": 32,
        "max_total_analytical_entries": 128,
        "max_references_per_entry": 16,
        "max_diagnostics": 256,
        "max_text_code_points": 2_048,
        "max_aggregate_analyst_text_code_points": 32_768,
    }
    assert dict(m.RESOURCE_BOUND_PROFILE) == expected
    assert all(isinstance(value, int) and value > 0 for value in m.RESOURCE_BOUND_PROFILE.values())


def test_resource_bound_fields_partition_into_enforced_and_deferred_with_no_overlap() -> None:
    enforced = set(m.RESOURCE_BOUND_SCHEMA_OR_HELPER_ENFORCED_FIELDS)
    deferred = set(m.RESOURCE_BOUND_RUNTIME_DEFERRED_FIELDS)
    assert enforced | deferred == set(m.RESOURCE_BOUND_PROFILE)
    assert not enforced & deferred
    assert len(enforced) == len(deferred) == 10


def test_evidence_records_maxitems_boundary_is_schema_enforced() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    instance = _valid_analyst_input()
    instance["evidence_records"] = [
        {"evidence_record_id": f"ev-{i}", "evidence_record_identity_sha256": _SHA} for i in range(256)
    ]
    Draft202012Validator(schema).validate(instance)

    instance["evidence_records"].append({"evidence_record_id": "ev-256", "evidence_record_identity_sha256": _SHA})
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


def test_analytical_section_maxitems_boundary_is_schema_enforced() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_response.schema.json")
    instance = _valid_analyst_response()
    instance["analytical_sections"]["observations"] = [
        {"entry_id": f"obs-{i}", "statement": "s", "evidence_record_ids": ["ev-0"]} for i in range(32)
    ]
    Draft202012Validator(schema).validate(instance)

    instance["analytical_sections"]["observations"].append(
        {"entry_id": "obs-32", "statement": "s", "evidence_record_ids": ["ev-0"]}
    )
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


# --- namespace and side effects --------------------------------------------------


def test_public_namespace_exactly_equals_dunder_all() -> None:
    public = {name for name in dir(m) if not name.startswith("_")}
    assert public == set(m.__all__)
    assert len(m.__all__) == len(set(m.__all__))


def test_module_has_no_filesystem_network_or_dynamic_import_surface() -> None:
    source = Path(m.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                assert not name.startswith("investment_orchestrator"), name
                assert name not in {"os", "pathlib", "socket", "subprocess", "urllib", "requests", "importlib"}, name
        if isinstance(node, ast.Call):
            called = node.func.id if isinstance(node.func, ast.Name) else None
            assert called not in {"open", "exec", "eval", "__import__"}, called


def test_fresh_process_import_succeeds_and_matches_in_process_identity() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from investment_orchestrator.observability import weekly_shadow_01_contracts as m; "
            "print(m.CONTRACT_CATALOG_IDENTITY_SHA256)",
        ],
        cwd=repo_root(),
        env={"PYTHONPATH": str(repo_root() / "src"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == m.CONTRACT_CATALOG_IDENTITY_SHA256


# --- downstream isolation --------------------------------------------------------


def test_module_has_no_investment_orchestrator_import_at_all() -> None:
    source = Path(m.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("investment_orchestrator")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("investment_orchestrator")


def test_analyst_conclusion_values_do_not_collide_with_real_production_terminal_or_action_values() -> None:
    from investment_orchestrator.state import research_availability
    from investment_orchestrator.workflow import weekly_orchestrator

    production_values = {
        weekly_orchestrator.TERMINAL_NO_TRADE,
        weekly_orchestrator.TERMINAL_NO_TRADE_PENDING_FINAL_GATES,
        research_availability.STRICT_FRESH,
        research_availability.STRICT_STALE,
        "HOLD",
        "NEW_BUY",
        "ORDER_COMPILATION",
    }
    assert not production_values & set(m.ANALYST_CONCLUSION_VALUES)
    assert not production_values & set(m.RUN_STATUS_VALUES)
    assert "ANALYSIS_COMPLETE" not in production_values


def test_weekly_shadow_01_contracts_has_no_production_consumer() -> None:
    from investment_orchestrator.observability import ltetf_target_architecture_gap_report as gap

    inventory = gap._scan_production_inventory(repo_root())
    assert m.__name__ not in "".join(inventory.observer_external_consumers)
    gap._validate_observer_inventory_isolation(inventory)
