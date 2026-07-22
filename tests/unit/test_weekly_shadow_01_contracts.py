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
_OTHER_SHA = "b" * 64
_GENERATION_ID = "0" + "a" * 63
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

_V2_SCHEMA_ID_BY_FILENAME = {
    "weekly_shadow_01_analyst_input.schema.json": (
        "https://investment-system.local/schemas/weekly_shadow_01_analyst_input_v2.schema.json"
    ),
    "weekly_shadow_01_analyst_response.schema.json": (
        "https://investment-system.local/schemas/weekly_shadow_01_analyst_response_v2.schema.json"
    ),
    "weekly_shadow_01_response_capture.schema.json": (
        "https://investment-system.local/schemas/weekly_shadow_01_response_capture_v2.schema.json"
    ),
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


def _source_artifact_bindings() -> list[dict[str, str]]:
    return [
        {"source_id": role, "source_artifact_identity_sha256": _SHA}
        for role in m.CONSUMED_SOURCE_ARTIFACT_ROLES
    ]


def _valid_active_anchor_record(
    *,
    index: int = 0,
    record_id: str = "ev-active-anchor-0",
    summary: str | None = "Source-owned anchor summary.",
    applicable_tickers: tuple[str, ...] = ("QQQ",),
) -> dict:
    return {
        "evidence_record_id": record_id,
        "evidence_record_identity_sha256": _SHA,
        "value_type": "active_anchor_v1",
        "source_locator": {
            "locator_type": "active_anchor_by_id",
            "source_artifact_role": "evidence_packet.json",
            "anchor_id": f"ANCHOR_{index}",
        },
        "normalized_value": {
            "applicable_tickers": list(applicable_tickers),
            "anchor_date_et": "2026-07-01",
            "valid_from": "2026-07-01",
            "valid_until": "2027-07-01",
            "confidence_floor": "medium",
            "summary": summary,
            "validation": {"stale": False},
        },
        "authority_effect": "none",
    }


def _valid_availability_record(
    *,
    domain: str = "market_metrics",
    record_id: str = "ev-market-availability",
    available: bool = False,
    data_gap: str | None = "DATA_GAP: deterministic source unavailable.",
) -> dict:
    assert domain in {"market_metrics", "scheduled_events_deterministic"}
    return {
        "evidence_record_id": record_id,
        "evidence_record_identity_sha256": _SHA,
        "value_type": "availability_status_v1",
        "source_locator": {
            "locator_type": "availability_status",
            "source_artifact_role": "evidence_packet.json",
            "availability_subject": domain,
        },
        "normalized_value": {"available": available, "data_gap": data_gap},
        "authority_effect": "none",
    }


def _valid_diagnostic_record(*, record_id: str = "ev-empty-active-registry") -> dict:
    return {
        "evidence_record_id": record_id,
        "evidence_record_identity_sha256": _SHA,
        "value_type": "diagnostic_code_v1",
        "source_locator": {
            "locator_type": "manifest_diagnostic",
            "source_artifact_role": "replacement_input_manifest.json",
            "diagnostic_code": "EMPTY_ACTIVE_REGISTRY",
        },
        "authority_effect": "none",
    }


def _valid_analyst_input(*, include_evidence: bool = True) -> dict:
    evidence_records = (
        [
            _valid_active_anchor_record(),
            _valid_availability_record(),
            _valid_diagnostic_record(),
        ]
        if include_evidence
        else []
    )
    return {
        "schema_version": "weekly_shadow_01_analyst_input_v2",
        "run_id": "run-2026-07-20",
        "adapter_id": m.LEGACY_R2F_ADAPTER_ID,
        "adapter_version": "legacy_r2f_adapter_v1",
        "source_generation_id": _GENERATION_ID,
        "source_generation_version": m.R2F_SOURCE_GENERATION_VERSION,
        "evaluation_timestamp_utc": "2026-07-20T00:00:00Z",
        "source_artifact_bindings": _source_artifact_bindings(),
        "evidence_records": evidence_records,
        "availability_diagnostic_record_ids": (
            ["ev-market-availability", "ev-empty-active-registry"] if include_evidence else []
        ),
        "freshness_diagnostic_record_ids": ["ev-active-anchor-0"] if include_evidence else [],
        "permitted_question_ids": [],
        "prohibited_conclusion_ids": list(m.PROHIBITED_ANALYST_CONCLUSION_VALUES),
        "contract_catalog_identity_sha256": _SHA,
        "resource_bound_profile_identity_sha256": _SHA,
        "prompt_template_identity_sha256": _SHA,
        "negative_authority": dict(_NEG_AUTH),
        "input_package_identity_sha256": _SHA,
    }


def _valid_analytical_sections() -> dict:
    return {"observations": [], "risks_and_uncertainties": [], "missing_evidence_notes": []}


def _valid_analyst_response(*, conclusion: str = "OBSERVATIONS_AVAILABLE", confidence: str = "MEDIUM") -> dict:
    return {
        "schema_version": "weekly_shadow_01_analyst_response_v2",
        "stage_version": "weekly_shadow_01_stage_a_v1",
        "run_id": "run-2026-07-20",
        "input_package_identity_sha256": _SHA,
        "prompt_template_identity_sha256": _SHA,
        "source_generation_id": _GENERATION_ID,
        "source_artifact_bindings": _source_artifact_bindings(),
        "evidence_record_bindings": [],
        "analyst_conclusion": conclusion,
        "analyst_confidence": confidence,
        "analytical_sections": _valid_analytical_sections(),
        "analyst_limitation_codes": [],
        "negative_authority": dict(_NEG_AUTH),
    }


def _valid_response_capture() -> dict:
    return {
        "schema_version": "weekly_shadow_01_response_capture_v2",
        "run_id": "run-2026-07-20",
        "input_package_identity_sha256": _SHA,
        "source_generation_id": _GENERATION_ID,
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
    assert schema["$id"] == _V2_SCHEMA_ID_BY_FILENAME.get(
        filename, f"https://investment-system.local/schemas/{filename}"
    )
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


def test_source_artifact_bindings_have_the_frozen_exact_role_order() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    instance = _valid_analyst_input()
    assert tuple(binding["source_id"] for binding in instance["source_artifact_bindings"]) == (
        m.CONSUMED_SOURCE_ARTIFACT_ROLES
    )
    instance["source_artifact_bindings"][0], instance["source_artifact_bindings"][1] = (
        instance["source_artifact_bindings"][1],
        instance["source_artifact_bindings"][0],
    )
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


def test_source_role_policy_freezes_binding_only_unconsumed_and_incomplete_entries() -> None:
    assert m.CONSUMED_SOURCE_ARTIFACT_ROLES == (
        "replacement_input_manifest.json",
        "evidence_packet.json",
        "analyst_memo_prompt.txt",
        "render_generation_binding.json",
    )
    assert m.PERMANENTLY_UNCONSUMED_SOURCE_ARTIFACT_ROLE == "analyst_memo_raw_output.txt"
    assert m.INCOMPLETE_SOURCE_GENERATION_MARKER == ".render_in_progress"
    metadata = m._SEMANTIC_CONTRACT_RECORDS["weekly_shadow_01_analyst_input_v2"][
        "semantic_metadata"
    ]
    assert metadata["binding_only_source_artifact_roles"] == ["analyst_memo_prompt.txt"]


def test_source_locator_metadata_freezes_package_owned_context_and_phase_boundaries() -> None:
    assert m.SOURCE_LOCATOR_TYPES == (
        "active_anchor_by_id",
        "availability_status",
        "manifest_diagnostic",
    )
    assert m.AVAILABILITY_SUBJECTS == (
        "market_metrics",
        "scheduled_events_deterministic",
    )
    assert dict(m.ACTIVE_ANCHOR_SOURCE_LOCATOR_CONTRACT) == {
        "locator_type": "active_anchor_by_id",
        "source_artifact_role": "evidence_packet.json",
        "required_fields": ("locator_type", "source_artifact_role", "anchor_id"),
        "anchor_id_contract": "bounded_nonempty_source_owned_text_max_2048_code_points",
    }
    assert m.DIAGNOSTIC_SOURCE_LOCATOR_CONTRACT["diagnostic_code"] == (
        "EMPTY_ACTIVE_REGISTRY"
    )
    assert m.PACKAGE_OWNED_SOURCE_CONTEXT["package_owned_fields"] == (
        "source_generation_id",
        "source_generation_version",
        "source_artifact_bindings",
    )
    assert m.OBSOLETE_EVIDENCE_RECORD_FIELDS == (
        "source_artifact_identity_sha256",
        "source_field_bindings",
        "source_lineage",
    )
    assert "applicable_tickers_is_one_complete_ordered_source_array" in (
        m.SOURCE_LOCATOR_SEMANTICS
    )
    assert "perform_no_coercion_defaulting_truncation_or_summarization" in (
        m.WS01B_SOURCE_CORRELATION_RESPONSIBILITIES
    )
    assert "never_reconcile_or_repair_input_package_source_correlation" in (
        m.WS01C_RESPONSE_VALIDATION_RESPONSIBILITIES
    )

    metadata = m._SEMANTIC_CONTRACT_RECORDS["weekly_shadow_01_analyst_input_v2"][
        "semantic_metadata"
    ]
    assert "source_field_binding_sequences" not in metadata
    assert "source_field_binding_semantics" not in metadata
    assert "source_lineage" not in metadata
    assert metadata["record_level_source_lineage_present"] is False
    assert metadata["record_level_source_artifact_identity_present"] is False
    assert metadata["dynamic_source_field_bindings_present"] is False


def test_logical_locator_definition_and_uniqueness_policy_are_exact_and_fail_closed() -> None:
    assert dict(m.LOGICAL_LOCATOR_DEFINITION) == {
        "ordered_components": (
            "value_type",
            "source_locator",
            "package_source_generation_context",
            "resolved_source_artifact_binding",
        ),
        "package_source_generation_context_fields": (
            "source_generation_id",
            "source_generation_version",
        ),
        "resolved_source_artifact_binding_fields": (
            "source_id",
            "source_artifact_identity_sha256",
        ),
    }
    assert m.LOGICAL_LOCATOR_UNIQUENESS_RULES == (
        "reject_duplicate_complete_logical_locator",
        "reject_duplicate_evidence_record_id",
        "reject_one_logical_locator_with_multiple_normalized_values",
        "reject_duplicate_active_anchor_id",
        "reject_duplicate_availability_subject",
        "reject_duplicate_manifest_diagnostic",
        "reject_duplicates_before_input_package_identity_acceptance",
        "json_schema_unique_items_is_not_the_enforcement_mechanism",
        "no_first_write_last_write_merge_deduplication_or_silent_normalization",
    )
    assert {
        "reject_duplicate_logical_locators_and_evidence_record_ids_before_package_identity_acceptance",
        "reject_one_logical_locator_with_multiple_normalized_values",
        "reject_duplicate_active_anchor_availability_or_manifest_diagnostic_locators",
        "never_merge_deduplicate_or_choose_first_or_last_duplicate_record",
    } <= set(m.WS01B_SOURCE_CORRELATION_RESPONSIBILITIES)


@pytest.mark.parametrize(
    "required_rule",
    [
        "reject_duplicate_evidence_record_id",
        "reject_duplicate_active_anchor_id",
        "reject_duplicate_availability_subject",
        "reject_duplicate_manifest_diagnostic",
    ],
)
def test_each_duplicate_locator_class_has_explicit_ws01b_rejection_ownership(
    required_rule: str,
) -> None:
    assert required_rule in m.LOGICAL_LOCATOR_UNIQUENESS_RULES


def test_canonical_evidence_record_ordering_metadata_is_an_exact_total_order() -> None:
    assert dict(m.EVIDENCE_VARIANT_RANKS) == {
        "active_anchor_v1": 0,
        "availability_status_v1": 1,
        "diagnostic_code_v1": 2,
    }
    assert dict(m.AVAILABILITY_SUBJECT_RANKS) == {
        "market_metrics": 0,
        "scheduled_events_deterministic": 1,
    }
    assert dict(m.EVIDENCE_RECORD_CANONICAL_ORDERING) == {
        "ordering_key": (
            "variant_rank",
            "canonical_source_locator_bytes",
            "evidence_record_id",
        ),
        "direction": "ascending",
        "canonical_source_locator_encoding": "canonical_json_bytes",
        "canonical_source_locator_byte_comparison": "unsigned_lexicographic",
        "active_anchor_within_variant_order": "locator_anchor_id",
        "availability_subject_order": (
            "market_metrics",
            "scheduled_events_deterministic",
        ),
        "manifest_diagnostic_position": "single_fixed_position_under_variant_rank",
        "final_sequence_requirement": "strictly_increasing",
    }
    assert m.CANONICAL_EVIDENCE_ORDERING_RULES == (
        "construct_evidence_records_in_frozen_canonical_order",
        "reject_duplicate_canonical_ordering_keys",
        "verify_final_evidence_record_sequence_is_strictly_increasing",
        "never_rely_on_source_traversal_or_caller_mapping_order",
        "reject_caller_supplied_noncanonical_evidence_record_sequence",
        "never_silently_reorder_or_accept_a_noncanonical_input_package",
        "ws01c_never_repairs_analyst_input_evidence_record_order",
    )
    assert m.CANONICAL_ORDER_INDEPENDENCE_INPUTS == (
        "source_json_insertion_order",
        "filesystem_enumeration_order",
        "caller_dictionary_order",
        "hash_seed",
        "locale",
        "timezone",
        "process_identity",
        "repository_path",
    )


def test_schema_and_runtime_resource_bound_ownership_is_explicit_and_nonconflicting() -> None:
    assert m.ANALYST_INPUT_SCHEMA_ENFORCED_CONSTRAINTS == (
        "source_generation_id_lowercase_sha256_shape",
        "closed_source_artifact_role_membership_and_exact_four_position_binding_array",
        "closed_evidence_record_source_locator_and_normalized_value_shapes",
        "authority_effect_none",
        "evidence_records_max_items_256",
        "active_anchor_summary_max_code_points_2048",
        "active_anchor_applicable_tickers_max_items_1017",
        "availability_diagnostic_record_ids_max_items_256",
        "freshness_diagnostic_record_ids_max_items_256",
        "individual_text_array_and_object_shape_bounds",
        "closed_schema_shape_guarantees_nesting_below_max_nesting_depth",
    )
    assert m.WS01B_RUNTIME_DEFERRED_RESOURCE_BOUND_RESPONSIBILITIES == (
        "each_source_artifact_byte_length_le_source_artifact_max_bytes",
        "combined_source_artifact_byte_length_le_source_artifacts_total_max_bytes",
        "canonical_analyst_input_package_byte_length_le_analyst_input_max_bytes",
        "rendered_analyst_prompt_byte_length_le_rendered_prompt_max_bytes",
        "combined_diagnostic_reference_union_count_le_max_diagnostics",
        "diagnostic_reference_ids_unique_across_both_arrays",
        "logical_locator_count_and_uniqueness_le_max_evidence_records",
        "aggregate_analyst_text_code_points_le_max_aggregate_analyst_text_code_points",
    )
    assert m.WS01B_RUNTIME_BOUND_FAILURE_CODE == "WS01_BR_RESOURCE_BOUND_EXCEEDED"
    assert m.WS01B_RUNTIME_BOUND_FAILURE_CODE in m.BLOCKING_REASON_CODES
    assert "enforce_runtime_deferred_analyst_input_resource_bounds_fail_closed" in (
        m.WS01B_SOURCE_CORRELATION_RESPONSIBILITIES
    )


def test_static_runtime_responsibility_table_has_exact_disjoint_ownership() -> None:
    expected = {
        "schema_enforced": (
            "source_generation_id_shape",
            "closed_source_locator_variant",
            "source_artifact_role_membership",
            "closed_normalized_value_shape",
            "authority_effect_none",
            "individual_evidence_text_and_array_bounds",
            "individual_diagnostic_array_bounds",
            "closed_object_shapes_and_nesting",
        ),
        "ws01b_enforced": (
            "verified_source_generation_selection",
            "role_to_unique_package_artifact_resolution",
            "unique_active_anchor_lookup",
            "exact_normalized_value_equality_with_verified_source",
            "logical_locator_uniqueness",
            "evidence_record_id_uniqueness",
            "canonical_evidence_record_ordering",
            "diagnostic_reference_membership_category_cross_array_uniqueness_and_union_bound",
            "canonical_analyst_input_package_byte_bound",
            "rendered_analyst_prompt_byte_bound",
            "aggregate_analyst_input_resource_bounds",
            "record_and_package_identity_computation_before_acceptance",
        ),
        "ws01c_enforced": (
            "untrusted_analyst_response_schema_and_prohibited_content_validation",
            "analyst_response_artifact_and_evidence_identity_echo_validation",
            "analyst_response_reference_membership_validation",
            "analyst_response_negative_authority_validation",
        ),
        "not_required": (
            "record_level_source_generation_equality_reconciliation",
            "record_level_source_artifact_identity_equality_reconciliation",
            "dynamic_source_field_path_reconciliation",
            "input_package_duplicate_merge_or_deduplication",
            "ws01c_input_package_repair_reconciliation_or_reordering",
        ),
    }
    assert dict(m.STATIC_RUNTIME_RESPONSIBILITY_TABLE) == expected
    owned = [item for values in expected.values() for item in values]
    assert len(owned) == len(set(owned))
    assert "ws01c_input_package_repair_reconciliation_or_reordering" in expected["not_required"]


def test_analyst_input_v2_has_exact_required_package_level_bindings() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    assert tuple(schema["required"]) == (
        "schema_version",
        "run_id",
        "adapter_id",
        "adapter_version",
        "source_generation_id",
        "source_generation_version",
        "evaluation_timestamp_utc",
        "source_artifact_bindings",
        "evidence_records",
        "availability_diagnostic_record_ids",
        "freshness_diagnostic_record_ids",
        "permitted_question_ids",
        "prohibited_conclusion_ids",
        "contract_catalog_identity_sha256",
        "resource_bound_profile_identity_sha256",
        "prompt_template_identity_sha256",
        "negative_authority",
        "input_package_identity_sha256",
    )
    assert schema["properties"]["adapter_id"] == {"const": m.LEGACY_R2F_ADAPTER_ID}
    assert schema["properties"]["source_generation_version"] == {
        "const": m.R2F_SOURCE_GENERATION_VERSION
    }
    assert schema["properties"]["contract_catalog_identity_sha256"] == {
        "$ref": "#/$defs/sha256"
    }
    assert "const" not in schema["properties"]["contract_catalog_identity_sha256"]


@pytest.mark.parametrize(
    "required_field",
    [
        "source_generation_version",
        "contract_catalog_identity_sha256",
        "availability_diagnostic_record_ids",
        "freshness_diagnostic_record_ids",
        "negative_authority",
    ],
)
def test_new_package_level_bindings_are_required(required_field: str) -> None:
    instance = _valid_analyst_input()
    instance.pop(required_field)
    with pytest.raises(Exception):
        Draft202012Validator(_load_schema("weekly_shadow_01_analyst_input.schema.json")).validate(
            instance
        )


def test_analyst_response_v2_echoes_only_minimum_ordered_bindings() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_response.schema.json")
    properties = set(schema["properties"])
    assert {
        "input_package_identity_sha256",
        "prompt_template_identity_sha256",
        "source_generation_id",
        "source_artifact_bindings",
        "evidence_record_bindings",
    } <= properties
    assert not {
        "normalized_value",
        "source_lineage",
        "source_field_bindings",
        "availability_diagnostic_record_ids",
        "freshness_diagnostic_record_ids",
        "contract_catalog_identity_sha256",
        "source_artifact_raw_hashes",
        "source_artifact_canonical_hashes",
    } & properties
    response = _valid_analyst_response()
    response["source_artifact_bindings"][0], response["source_artifact_bindings"][1] = (
        response["source_artifact_bindings"][1],
        response["source_artifact_bindings"][0],
    )
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(response)


def test_response_capture_v2_remains_a_minimal_code_owned_byte_envelope() -> None:
    schema = _load_schema("weekly_shadow_01_response_capture.schema.json")
    assert set(schema["properties"]) == {
        "schema_version",
        "run_id",
        "input_package_identity_sha256",
        "source_generation_id",
        "raw_response_base64",
        "raw_response_sha256",
        "raw_response_byte_size",
        "negative_authority_profile",
        "response_capture_identity_sha256",
    }
    for forbidden in (
        "analyst_findings",
        "validation_status",
        "publication_status",
        "permission",
    ):
        instance = _valid_response_capture()
        instance[forbidden] = None
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


# --- WS01a2 migration and explicit R2F generation identity -------------------


def test_ws01a2_migrates_exactly_three_schema_versions_and_keeps_six_filenames() -> None:
    discovered = tuple(
        sorted(path.name for path in (repo_root() / "schemas").glob("weekly_shadow_01_*.schema.json"))
    )
    assert discovered == tuple(sorted(WS01A_SCHEMA_FILENAMES))
    assert tuple(m.SCHEMA_FILENAME_BY_VERSION) == (
        "weekly_shadow_01_analyst_input_v2",
        "weekly_shadow_01_analyst_response_v2",
        "weekly_shadow_01_response_capture_v2",
        "weekly_shadow_01_response_validation_v1",
        "weekly_shadow_01_analyst_report_v1",
        "weekly_shadow_01_run_summary_v1",
    )
    assert m.WEEKLY_SHADOW_STAGE_VERSION == "weekly_shadow_01_stage_a_v1"


@pytest.mark.parametrize(
    "filename,factory,old_version",
    [
        (
            "weekly_shadow_01_analyst_input.schema.json",
            _valid_analyst_input,
            "weekly_shadow_01_analyst_input_v1",
        ),
        (
            "weekly_shadow_01_analyst_response.schema.json",
            _valid_analyst_response,
            "weekly_shadow_01_analyst_response_v1",
        ),
        (
            "weekly_shadow_01_response_capture.schema.json",
            _valid_response_capture,
            "weekly_shadow_01_response_capture_v1",
        ),
    ],
)
def test_v1_analyst_input_response_and_capture_have_no_compatibility_shim(
    filename: str, factory, old_version: str
) -> None:
    instance = factory()
    instance["schema_version"] = old_version
    with pytest.raises(Exception):
        Draft202012Validator(_load_schema(filename)).validate(instance)


def _set_generation_id(instance: dict, generation_id: str) -> None:
    instance["source_generation_id"] = generation_id


@pytest.mark.parametrize(
    "filename,factory",
    [
        ("weekly_shadow_01_analyst_input.schema.json", _valid_analyst_input),
        ("weekly_shadow_01_analyst_response.schema.json", _valid_analyst_response),
        ("weekly_shadow_01_response_capture.schema.json", _valid_response_capture),
    ],
)
@pytest.mark.parametrize("generation_id", ["0" + "a" * 63, "a" * 64])
def test_real_r2f_digit_or_letter_leading_generation_ids_validate_in_all_three_schemas(
    filename: str, factory, generation_id: str
) -> None:
    instance = factory()
    _set_generation_id(instance, generation_id)
    Draft202012Validator(_load_schema(filename)).validate(instance)


@pytest.mark.parametrize(
    "generation_id",
    [
        "A" * 64,
        "g" + "a" * 63,
        "a" * 63,
        "a" * 65,
        " " + "a" * 64,
        "a" * 64 + " ",
        "a/" + "a" * 62,
        "a\\" + "a" * 62,
        "../" + "a" * 61,
        ".%2f" + "a" * 60,
    ],
)
@pytest.mark.parametrize(
    "filename,factory",
    [
        ("weekly_shadow_01_analyst_input.schema.json", _valid_analyst_input),
        ("weekly_shadow_01_analyst_response.schema.json", _valid_analyst_response),
        ("weekly_shadow_01_response_capture.schema.json", _valid_response_capture),
    ],
)
def test_invalid_r2f_generation_ids_fail_closed_in_all_three_schemas(
    filename: str, factory, generation_id: str
) -> None:
    instance = factory()
    _set_generation_id(instance, generation_id)
    with pytest.raises(Exception):
        Draft202012Validator(_load_schema(filename)).validate(instance)


def test_generation_id_uses_a_dedicated_definition_not_generic_identifier() -> None:
    for filename in (
        "weekly_shadow_01_analyst_input.schema.json",
        "weekly_shadow_01_analyst_response.schema.json",
        "weekly_shadow_01_response_capture.schema.json",
    ):
        schema = _load_schema(filename)
        assert schema["properties"]["source_generation_id"] == {"$ref": "#/$defs/r2f_generation_id"}
        assert schema["$defs"]["r2f_generation_id"] == {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        }


# --- value-bearing grounding records ----------------------------------------


def _input_with_records(*records: dict) -> dict:
    instance = _valid_analyst_input(include_evidence=False)
    instance["evidence_records"] = [copy.deepcopy(record) for record in records]
    return instance


@pytest.mark.parametrize(
    "record",
    [
        _valid_active_anchor_record(),
        _valid_availability_record(),
        _valid_availability_record(
            domain="scheduled_events_deterministic",
            record_id="ev-event-availability",
            available=True,
            data_gap=None,
        ),
    ],
)
def test_value_bearing_variants_carry_and_validate_actual_normalized_value(record: dict) -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    instance = _input_with_records(record)
    Draft202012Validator(schema).validate(instance)
    assert "normalized_value" in instance["evidence_records"][0]


def test_active_anchor_variant_is_exactly_the_accepted_source_owned_projection() -> None:
    record = _valid_active_anchor_record()
    value = record["normalized_value"]
    assert tuple(value) == (
        "applicable_tickers",
        "anchor_date_et",
        "valid_from",
        "valid_until",
        "confidence_floor",
        "summary",
        "validation",
    )
    assert value["validation"] == {"stale": False}
    assert tuple(m.ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS) == (
        "applicable_tickers",
        "anchor_date_et",
        "valid_from",
        "valid_until",
        "confidence_floor",
        "summary",
        "validation.stale",
    )


def test_active_anchor_locator_is_closed_and_selects_one_source_owned_anchor_id() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    record = _valid_active_anchor_record(index=7, applicable_tickers=("QQQ", "VOO"))
    assert record["source_locator"] == {
        "locator_type": "active_anchor_by_id",
        "source_artifact_role": "evidence_packet.json",
        "anchor_id": "ANCHOR_7",
    }
    assert record["normalized_value"]["applicable_tickers"] == ["QQQ", "VOO"]
    assert "anchor_id" not in record["normalized_value"]
    assert json.dumps(record, sort_keys=True).count('"applicable_tickers"') == 1
    Draft202012Validator(schema).validate(_input_with_records(record))

    for mutation in (
        lambda locator: locator.__setitem__("arbitrary", "field"),
        lambda locator: locator.__setitem__("locator_type", "active_anchor_by_index"),
        lambda locator: locator.__setitem__("source_artifact_role", "analyst_memo_prompt.txt"),
        lambda locator: locator.__setitem__("anchor_id", ""),
        lambda locator: locator.pop("anchor_id"),
    ):
        invalid = _valid_active_anchor_record()
        mutation(invalid["source_locator"])
        with pytest.raises(Exception):
            Draft202012Validator(schema).validate(_input_with_records(invalid))


def _obsolete_active_anchor_bindings(*, anchor_index: int = 0, ticker_count: int = 1) -> list:
    prefix = [
        ("/anchor_id", "anchor_id"),
        ("/anchor_date_et", "anchor_date_et"),
        ("/valid_from", "valid_from"),
        ("/valid_until", "valid_until"),
        ("/confidence_floor", "confidence_floor"),
        ("/summary", "summary"),
        ("/validation/stale", "validation/stale"),
    ]
    bindings = [
        {
            "value_path": value_path,
            "source_field_path": (
                f"/active_anchor_registry/active_anchors/{anchor_index}/{source_tail}"
            ),
        }
        for value_path, source_tail in prefix
    ]
    return bindings + [
        {
            "value_path": f"/applicable_tickers/{ticker_index}",
            "source_field_path": (
                f"/active_anchor_registry/active_anchors/{anchor_index}"
                f"/applicable_tickers/{ticker_index}"
            ),
        }
        for ticker_index in range(ticker_count)
    ]


def _add_incomplete_ticker_bindings(record: dict) -> None:
    record["source_field_bindings"] = _obsolete_active_anchor_bindings(ticker_count=1)


def _add_duplicate_value_path_bindings(record: dict) -> None:
    bindings = _obsolete_active_anchor_bindings(ticker_count=1)
    duplicate = copy.deepcopy(bindings[-1])
    duplicate["source_field_path"] = (
        "/active_anchor_registry/active_anchors/0/applicable_tickers/1"
    )
    record["source_field_bindings"] = [*bindings, duplicate]


def _add_reordered_ticker_bindings(record: dict) -> None:
    bindings = _obsolete_active_anchor_bindings(ticker_count=2)
    bindings[-2:] = list(reversed(bindings[-2:]))
    record["source_field_bindings"] = bindings


def _add_mixed_anchor_index_bindings(record: dict) -> None:
    bindings = _obsolete_active_anchor_bindings(ticker_count=2)
    bindings[0]["source_field_path"] = "/active_anchor_registry/active_anchors/1/anchor_id"
    record["source_field_bindings"] = bindings


def _add_ticker_index_mismatch_bindings(record: dict) -> None:
    bindings = _obsolete_active_anchor_bindings(ticker_count=1)
    bindings[-1]["source_field_path"] = (
        "/active_anchor_registry/active_anchors/0/applicable_tickers/1"
    )
    record["source_field_bindings"] = bindings


@pytest.mark.parametrize(
    "mutation",
    [
        _add_incomplete_ticker_bindings,
        _add_duplicate_value_path_bindings,
        _add_reordered_ticker_bindings,
        _add_mixed_anchor_index_bindings,
        _add_ticker_index_mismatch_bindings,
        lambda record: record.__setitem__(
            "source_lineage",
            {
                "lineage_type": "verified_r2f_v2_generation",
                "source_generation_id": "1" + "a" * 63,
                "source_generation_version": m.R2F_SOURCE_GENERATION_VERSION,
            },
        ),
        lambda record: record.__setitem__("source_artifact_identity_sha256", _OTHER_SHA),
    ],
    ids=(
        "incomplete-ticker-binding",
        "duplicate-value-path",
        "reordered-ticker-bindings",
        "mixed-anchor-indices",
        "ticker-source-index-mismatch",
        "record-package-generation-disagreement",
        "record-package-artifact-identity-disagreement",
    ),
)
def test_all_seven_rejected_source_correlation_states_are_structurally_impossible(
    mutation,
) -> None:
    record = _valid_active_anchor_record(applicable_tickers=("QQQ", "VOO"))
    mutation(record)
    with pytest.raises(Exception):
        Draft202012Validator(_load_schema("weekly_shadow_01_analyst_input.schema.json")).validate(
            _input_with_records(record)
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_field_bindings", []),
        ("source_lineage", {}),
        ("source_artifact_identity_sha256", _OTHER_SHA),
    ],
)
def test_obsolete_record_level_source_fields_are_rejected(field: str, value: object) -> None:
    record = _valid_active_anchor_record()
    record[field] = value
    with pytest.raises(Exception):
        Draft202012Validator(_load_schema("weekly_shadow_01_analyst_input.schema.json")).validate(
            _input_with_records(record)
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: record["normalized_value"].pop("summary"),
        lambda record: record["normalized_value"].__setitem__("unknown", "x"),
        lambda record: record["normalized_value"].__setitem__("applicable_tickers", ["QQQ", "QQQ"]),
        lambda record: record["normalized_value"].__setitem__("confidence_floor", "MEDIUM"),
        lambda record: record["normalized_value"].__setitem__("anchor_date_et", "07/01/2026"),
        lambda record: record["normalized_value"]["validation"].__setitem__("usable", True),
        lambda record: record["normalized_value"].__setitem__("anchor_id", "ANCHOR_0"),
        lambda record: record.__setitem__("authority_effect", "permission"),
        lambda record: record.__setitem__("permission", True),
    ],
)
def test_active_anchor_unknown_lossy_or_authority_shaped_mutations_fail(mutation) -> None:
    record = _valid_active_anchor_record()
    mutation(record)
    with pytest.raises(Exception):
        Draft202012Validator(_load_schema("weekly_shadow_01_analyst_input.schema.json")).validate(
            _input_with_records(record)
        )


@pytest.mark.parametrize("bad_value", [1, 1.0, True, None, ["arbitrary"], {"arbitrary": "object"}])
def test_value_type_discrimination_rejects_integer_float_and_arbitrary_json(bad_value: object) -> None:
    record = _valid_active_anchor_record()
    record["normalized_value"] = bad_value
    with pytest.raises(Exception):
        Draft202012Validator(_load_schema("weekly_shadow_01_analyst_input.schema.json")).validate(
            _input_with_records(record)
        )


@pytest.mark.parametrize(
    "record,mutation",
    [
        (
            _valid_active_anchor_record(),
            lambda record: record.__setitem__("value_type", "availability_status_v1"),
        ),
        (
            _valid_availability_record(),
            lambda record: record.__setitem__("value_type", "active_anchor_v1"),
        ),
        (
            _valid_diagnostic_record(),
            lambda record: record.__setitem__("value_type", "active_anchor_v1"),
        ),
        (
            _valid_active_anchor_record(),
            lambda record: record.__setitem__(
                "source_locator", _valid_availability_record()["source_locator"]
            ),
        ),
    ],
)
def test_value_type_locator_and_normalized_value_cannot_disagree(record: dict, mutation) -> None:
    record = copy.deepcopy(record)
    mutation(record)
    with pytest.raises(Exception):
        Draft202012Validator(_load_schema("weekly_shadow_01_analyst_input.schema.json")).validate(
            _input_with_records(record)
        )


def test_availability_preserves_boolean_and_null_distinctions_without_coercion() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    Draft202012Validator(schema).validate(
        _input_with_records(_valid_availability_record(available=True, data_gap=None))
    )
    for bad_available in (1, 0, "false", None):
        record = _valid_availability_record()
        record["normalized_value"]["available"] = bad_available
        with pytest.raises(Exception):
            Draft202012Validator(schema).validate(_input_with_records(record))
    record = _valid_availability_record()
    record["normalized_value"].pop("data_gap")
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(_input_with_records(record))


def test_availability_locator_subjects_are_exact_closed_source_names() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    for subject in m.AVAILABILITY_SUBJECTS:
        Draft202012Validator(schema).validate(
            _input_with_records(_valid_availability_record(domain=subject))
        )
    for subject in ("scheduled_events", "unknown", "/market_metrics"):
        record = _valid_availability_record()
        record["source_locator"]["availability_subject"] = subject
        with pytest.raises(Exception):
            Draft202012Validator(schema).validate(_input_with_records(record))
    record = _valid_availability_record()
    record["source_locator"]["source_field_path"] = "/market_metrics"
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(_input_with_records(record))


def test_diagnostic_variant_carries_its_only_value_in_the_closed_manifest_locator() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    valid = _valid_diagnostic_record()
    Draft202012Validator(schema).validate(_input_with_records(valid))
    assert "normalized_value" not in valid
    assert valid["source_locator"] == {
        "locator_type": "manifest_diagnostic",
        "source_artifact_role": "replacement_input_manifest.json",
        "diagnostic_code": "EMPTY_ACTIVE_REGISTRY",
    }
    for mutation in (
        lambda record: record.__setitem__("normalized_value", "EMPTY_ACTIVE_REGISTRY"),
        lambda record: record["source_locator"].__setitem__(
            "diagnostic_code", "MARKET_METRICS_UNAVAILABLE"
        ),
        lambda record: record["source_locator"].__setitem__(
            "source_artifact_role", "evidence_packet.json"
        ),
        lambda record: record["source_locator"].__setitem__("diagnostic_index", 0),
    ):
        record = _valid_diagnostic_record()
        mutation(record)
        with pytest.raises(Exception):
            Draft202012Validator(schema).validate(_input_with_records(record))


def test_evidence_cannot_come_from_prompt_or_raw_analyst_output() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    for role in ("analyst_memo_prompt.txt", "analyst_memo_raw_output.txt"):
        record = _valid_active_anchor_record()
        record["source_locator"]["source_artifact_role"] = role
        with pytest.raises(Exception):
            Draft202012Validator(schema).validate(_input_with_records(record))
    assert m.PERMANENTLY_UNCONSUMED_SOURCE_ARTIFACT_ROLE == "analyst_memo_raw_output.txt"


# --- descriptive diagnostic references --------------------------------------


def test_split_diagnostic_reference_arrays_validate_and_legacy_combined_field_fails() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    instance = _valid_analyst_input()
    Draft202012Validator(schema).validate(instance)
    instance["availability_freshness_diagnostics"] = []
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


@pytest.mark.parametrize(
    "field",
    ["availability_diagnostic_record_ids", "freshness_diagnostic_record_ids"],
)
def test_duplicate_diagnostic_id_within_each_array_fails(field: str) -> None:
    instance = _valid_analyst_input()
    instance[field] = ["ev-duplicate", "ev-duplicate"]
    with pytest.raises(Exception):
        Draft202012Validator(_load_schema("weekly_shadow_01_analyst_input.schema.json")).validate(
            instance
        )


def test_schema_does_not_falsely_claim_cross_record_or_cross_array_diagnostic_enforcement() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    instance = _valid_analyst_input()
    # Structurally valid but relationally invalid: the active-anchor ID is in
    # the availability array and duplicated across the two arrays.  WS01b must
    # reject it; JSON Schema cannot express this membership/union relation.
    instance["availability_diagnostic_record_ids"] = ["ev-active-anchor-0"]
    instance["freshness_diagnostic_record_ids"] = ["ev-active-anchor-0"]
    Draft202012Validator(schema).validate(instance)
    assert tuple(m.DIAGNOSTIC_REFERENCE_INVARIANTS) == (
        "every_diagnostic_id_references_one_evidence_record",
        "availability_ids_reference_only_availability_or_empty_active_registry_records",
        "freshness_ids_reference_only_active_anchor_records",
        "diagnostic_id_union_count_does_not_exceed_max_diagnostics",
        "no_duplicate_id_across_availability_and_freshness_arrays",
        "referential_and_union_invariants_are_enforced_by_future_ws01b",
    )


def test_diagnostic_array_individual_bound_is_static_and_union_bound_is_deferred() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    instance = _valid_analyst_input(include_evidence=False)
    instance["availability_diagnostic_record_ids"] = [f"av-{index}" for index in range(256)]
    instance["freshness_diagnostic_record_ids"] = [f"fr-{index}" for index in range(256)]
    Draft202012Validator(schema).validate(instance)
    instance["availability_diagnostic_record_ids"].append("av-256")
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


def test_projection_exclusions_are_frozen_and_absent_from_package_properties() -> None:
    expected = {
        "analyst_memo_prompt_prose",
        "analyst_memo_raw_output",
        "existing_analyst_recommendation_or_conclusion",
        "universe_membership",
        "allowed_buy_lists",
        "budget_or_cap_configuration",
        "allocation_configuration",
        "portfolio_positions_or_targets",
        "existing_or_proposed_orders",
        "inactive_anchor_approval_revocation_readiness_or_audit_fields",
        "permission_approval_gate_or_compiler_eligibility",
        "broker_or_execution_data",
    }
    assert set(m.PROJECTION_EXCLUSIONS) == expected
    properties = set(_load_schema("weekly_shadow_01_analyst_input.schema.json")["properties"])
    assert not {
        "stage_version",
        "source_lineage_bindings",
        "source_artifact_raw_hashes",
        "source_artifact_canonical_hashes",
        "negative_authority_profile_identity_sha256",
        "analyst_memo_prompt",
        "analyst_memo_raw_output",
        "universe",
        "allowed_buy_tickers",
        "budget",
        "portfolio",
        "orders",
        "permission",
        "approval",
        "compiler_eligibility",
        "broker",
    } & properties


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
    mutation(instance["negative_authority"])
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


def test_exactly_three_schema_identities_migrate_and_the_other_three_remain_exact() -> None:
    old = {
        "weekly_shadow_01_analyst_input_v1": (
            "809c61a4569e3bd408ad32bd509377768ede4b1325146fee0b9d8a1cb1d51af5"
        ),
        "weekly_shadow_01_analyst_response_v1": (
            "2fad9bf5f216fb000c3c17e742a839cff3878f7acbbcacbdc0bdef6cd15426d7"
        ),
        "weekly_shadow_01_response_capture_v1": (
            "529127017ce3fb541d2ca41959b252d1b9992d2fadaf94b1e6c056ee9d927bab"
        ),
    }
    migrated = (
        ("weekly_shadow_01_analyst_input_v2", old["weekly_shadow_01_analyst_input_v1"]),
        ("weekly_shadow_01_analyst_response_v2", old["weekly_shadow_01_analyst_response_v1"]),
        ("weekly_shadow_01_response_capture_v2", old["weekly_shadow_01_response_capture_v1"]),
    )
    assert all(m.SCHEMA_IDENTITY_SHA256_BY_VERSION[key] != prior for key, prior in migrated)
    assert {
        key: m.SCHEMA_IDENTITY_SHA256_BY_VERSION[key]
        for key, _ in migrated
    } == {
        "weekly_shadow_01_analyst_input_v2": (
            "41c6258b3d27b97554a785628ab3e990e0f1f89bbaad7d70a787dd230853f5f0"
        ),
        "weekly_shadow_01_analyst_response_v2": (
            "3625d86dd84ae1243ccb4992e339d0935dff646c87e74f7792ecd635956ca160"
        ),
        "weekly_shadow_01_response_capture_v2": (
            "a2f727e89e29f2a3ab9791d8274236f8481b2c30175eb07bc4d4bf458d429a95"
        ),
    }
    assert {
        key: m.SCHEMA_IDENTITY_SHA256_BY_VERSION[key]
        for key in (
            "weekly_shadow_01_response_validation_v1",
            "weekly_shadow_01_analyst_report_v1",
            "weekly_shadow_01_run_summary_v1",
        )
    } == {
        "weekly_shadow_01_response_validation_v1": (
            "2990ad8fc4f22de8b21691f54b3a967aed66e733078bd75ea74bc1330ee02f02"
        ),
        "weekly_shadow_01_analyst_report_v1": (
            "7b415fa8eb7cb4ecce92ddf06eb394574f7d1435dd840657396dd2eeb0f4feb8"
        ),
        "weekly_shadow_01_run_summary_v1": (
            "114e92f0d151bba7266a651172cd7dac01f9652a4c6fe47557582b10dcf706a7"
        ),
    }


def test_semantic_contract_identities_are_independently_recomputed_without_self_identity() -> None:
    for version, record in m._SEMANTIC_CONTRACT_RECORDS.items():
        assert "semantic_contract_identity_sha256" not in record
        independent = m.domain_separated_sha256(
            m.DOMAIN_SEPARATORS["semantic_contract_identity"], record
        )
        assert independent == m.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION[version]


def test_input_semantic_identity_binds_uniqueness_ordering_bounds_and_ownership_table() -> None:
    baseline_record = m._SEMANTIC_CONTRACT_RECORDS["weekly_shadow_01_analyst_input_v2"]
    baseline_identity = m.domain_separated_sha256(
        m.DOMAIN_SEPARATORS["semantic_contract_identity"], baseline_record
    )

    for mutation in (
        lambda metadata: metadata["logical_locator_uniqueness_rules"].append("changed"),
        lambda metadata: metadata["evidence_variant_ranks"].__setitem__(
            "active_anchor_v1", 99
        ),
        lambda metadata: metadata["canonical_evidence_record_ordering"].__setitem__(
            "direction", "descending"
        ),
        lambda metadata: metadata[
            "future_ws01b_runtime_deferred_resource_bound_responsibilities"
        ].append("changed"),
        lambda metadata: metadata["static_runtime_responsibility_table"][
            "ws01b_enforced"
        ].append("changed"),
    ):
        changed = copy.deepcopy(baseline_record)
        mutation(changed["semantic_metadata"])
        assert m.domain_separated_sha256(
            m.DOMAIN_SEPARATORS["semantic_contract_identity"], changed
        ) != baseline_identity


def test_exactly_three_semantic_contract_identities_migrate_and_three_remain_exact() -> None:
    old_changed = (
        "007e7be18fad1943f56f61fdb6c8f360baffb20bff5e88b840d78f3978b3b154",
        "a5d26aa62af8ff6a69ce9254337ea629634e4d5c4590463518b91dfc87e8d7f8",
        "fdc30c644623b9446d24e1cb32b3303b81b19ef0257bd292522c61648a9d5765",
    )
    new_changed = tuple(
        m.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION[key]
        for key in (
            "weekly_shadow_01_analyst_input_v2",
            "weekly_shadow_01_analyst_response_v2",
            "weekly_shadow_01_response_capture_v2",
        )
    )
    assert new_changed == (
        "b49a1fa7bdd3affbf2c25c4f9184bcbdf54c9e1201327bad565c35c1de066eb1",
        "a3a14276ec697ad4e806f6c6d16250b95f279ba4c13aee573bbd8263039ea546",
        "2ff319f61fd445458b9cb897e9a2db83265deb5c3ea93313a73572f39efab19b",
    )
    assert new_changed[0] != (
        "a9728b922082b585b0751974992cda2ca8c7556c5b350dae6e7513d9ad11123b"
    )
    assert all(new != old for new, old in zip(new_changed, old_changed, strict=True))
    assert {
        key: m.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION[key]
        for key in (
            "weekly_shadow_01_response_validation_v1",
            "weekly_shadow_01_analyst_report_v1",
            "weekly_shadow_01_run_summary_v1",
        )
    } == {
        "weekly_shadow_01_response_validation_v1": (
            "3a41c1b6149aaa471d3dd94bd007b74cfcddbbdd97790bf00c7cdebd9b5000d5"
        ),
        "weekly_shadow_01_analyst_report_v1": (
            "195112bf9087b1f63f680c93a77d41487e4bceae4564a621c55c15b6cb684014"
        ),
        "weekly_shadow_01_run_summary_v1": (
            "88bc37d815c348fa0791c51fbdc660f2527c2d9975a01ab2bde2b9853c2a99b3"
        ),
    }


def test_authority_vocabulary_resource_and_prohibited_profile_identities_are_unchanged() -> None:
    assert {
        "negative": m.NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256,
        "resource": m.RESOURCE_BOUND_PROFILE_IDENTITY_SHA256,
        "run_status": m.RUN_STATUS_VOCABULARY_IDENTITY_SHA256,
        "analyst_conclusion": m.ANALYST_CONCLUSION_VOCABULARY_IDENTITY_SHA256,
        "analyst_confidence": m.ANALYST_CONFIDENCE_VOCABULARY_IDENTITY_SHA256,
        "blocking_reason": m.BLOCKING_REASON_VOCABULARY_IDENTITY_SHA256,
        "analyst_limitation": m.ANALYST_LIMITATION_VOCABULARY_IDENTITY_SHA256,
        "prohibited_key": m.PROHIBITED_KEY_PROFILE_IDENTITY_SHA256,
        "prohibited_intent": m.PROHIBITED_INTENT_PROFILE_IDENTITY_SHA256,
    } == {
        "negative": "b20ea7218880c5799897d7d3fbd74515af88ad6fcc9e2f4c1d4cc83649e61ff1",
        "resource": "acef986d2728660acce561f7c0d6a86fb0a942fa07ba8d3aea64bd061eee0e2e",
        "run_status": "be57d34943541c65839ec1774387d70008633606705b888b64709087f72d6f8a",
        "analyst_conclusion": (
            "210dc5e43311a54ad09daf0f4a64405d6ce999b0818c40f1f7d0da1f644ba9cd"
        ),
        "analyst_confidence": (
            "6cd369043ddf149b7eba2a9550955ea7518ce3e48a1263499df9ab76548fed82"
        ),
        "blocking_reason": (
            "4c105e9074f5c8fa8ab4e13ee6065aa4e99752cde33116932a6bcc134f784c47"
        ),
        "analyst_limitation": (
            "0e9ac34fcc09308269385b6b80c4e5b62dfd74fa80ea3c9bbe24d54d84f0fefc"
        ),
        "prohibited_key": (
            "88247b4e04877b3925a988bce9185181e4d2c4214cf7e58a51b415907651dc9c"
        ),
        "prohibited_intent": (
            "5376a4e55d8bb6f1d79808355f5056e35e25a004869ba62ee6ff225f55f3b0ba"
        ),
    }


def _resolved_artifact_binding(package: dict, record: dict) -> dict:
    role = record["source_locator"]["source_artifact_role"]
    matches = [
        binding for binding in package["source_artifact_bindings"] if binding["source_id"] == role
    ]
    assert len(matches) == 1
    return copy.deepcopy(matches[0])


def _record_locator_payload(package: dict, record: dict) -> dict:
    recipe = m.EVIDENCE_RECORD_ID_RECIPE
    return {
        "payload_kind": recipe["payload_kind"],
        "record_contract_version": recipe["record_contract_version"],
        "source_generation_id": package["source_generation_id"],
        "source_generation_version": package["source_generation_version"],
        "resolved_source_artifact_binding": _resolved_artifact_binding(package, record),
        "value_type": record["value_type"],
        "source_locator": copy.deepcopy(record["source_locator"]),
    }


def _record_locator_id(package: dict, record: dict) -> str:
    digest = m.compute_identity("evidence_record", _record_locator_payload(package, record))
    return f"ws01ev-{digest}"


def _record_content_identity(package: dict, record: dict) -> str:
    detached = {
        key: copy.deepcopy(value)
        for key, value in record.items()
        if key != "evidence_record_identity_sha256"
    }
    return m.compute_identity(
        "evidence_record",
        {
            "payload_kind": m.EVIDENCE_RECORD_IDENTITY_RECIPE["payload_kind"],
            "source_generation_id": package["source_generation_id"],
            "source_generation_version": package["source_generation_version"],
            "resolved_source_artifact_binding": _resolved_artifact_binding(package, record),
            "evidence_record": detached,
        },
    )


def test_identity_consistent_duplicate_locator_is_schema_valid_but_explicitly_ws01b_rejected() -> None:
    first = _valid_active_anchor_record(index=7, summary="First source value.")
    second = _valid_active_anchor_record(
        index=7,
        record_id="ev-active-anchor-duplicate",
        summary="Conflicting second source value.",
    )
    package = _input_with_records(first, second)
    for record in package["evidence_records"]:
        record["evidence_record_id"] = _record_locator_id(package, record)
        record["evidence_record_identity_sha256"] = _record_content_identity(package, record)
    package["input_package_identity_sha256"] = m.compute_identity(
        "input_package",
        package,
        exclude_fields=("input_package_identity_sha256",),
    )

    left, right = package["evidence_records"]
    assert left["source_locator"] == right["source_locator"]
    assert left["evidence_record_id"] == right["evidence_record_id"]
    assert left["normalized_value"] != right["normalized_value"]
    assert left["evidence_record_identity_sha256"] != right["evidence_record_identity_sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", package["input_package_identity_sha256"])

    # JSON Schema uniqueItems compares complete records, so these distinct
    # value-bearing objects are structurally valid. WS01b must reject the
    # duplicate logical locator before accepting the package identity.
    Draft202012Validator(_load_schema("weekly_shadow_01_analyst_input.schema.json")).validate(
        package
    )
    expected_result = "WS01b responsibility requires fail-closed duplicate-locator rejection"
    actual_result = (
        "WS01b responsibility requires fail-closed duplicate-locator rejection"
        if {
            "reject_duplicate_complete_logical_locator",
            "reject_one_logical_locator_with_multiple_normalized_values",
            "reject_duplicates_before_input_package_identity_acceptance",
        } <= set(m.LOGICAL_LOCATOR_UNIQUENESS_RULES)
        else "missing duplicate-locator rejection responsibility"
    )
    assert actual_result == expected_result


def test_canonical_evidence_order_is_strict_total_and_noncanonical_order_is_not_equivalent() -> None:
    records = [
        _valid_availability_record(
            domain="scheduled_events_deterministic",
            record_id="ev-events",
            available=True,
            data_gap=None,
        ),
        _valid_active_anchor_record(index=1, record_id="ev-anchor-1"),
        _valid_diagnostic_record(),
        _valid_availability_record(record_id="ev-market"),
        _valid_active_anchor_record(index=0, record_id="ev-anchor-0"),
    ]
    package = _input_with_records(*records)
    for record in package["evidence_records"]:
        record["evidence_record_id"] = _record_locator_id(package, record)

    def ordering_key(record: dict) -> tuple[int, bytes, str]:
        return (
            m.EVIDENCE_VARIANT_RANKS[record["value_type"]],
            m.canonical_json_bytes(record["source_locator"]),
            record["evidence_record_id"],
        )

    canonical_records = sorted(package["evidence_records"], key=ordering_key)
    assert [record["value_type"] for record in canonical_records] == [
        "active_anchor_v1",
        "active_anchor_v1",
        "availability_status_v1",
        "availability_status_v1",
        "diagnostic_code_v1",
    ]
    assert [
        record["source_locator"]["anchor_id"] for record in canonical_records[:2]
    ] == ["ANCHOR_0", "ANCHOR_1"]
    assert [
        record["source_locator"]["availability_subject"]
        for record in canonical_records[2:4]
    ] == ["market_metrics", "scheduled_events_deterministic"]
    keys = [ordering_key(record) for record in canonical_records]
    assert all(left < right for left, right in zip(keys, keys[1:]))

    canonical_package = copy.deepcopy(package)
    canonical_package["evidence_records"] = canonical_records
    noncanonical_package = copy.deepcopy(canonical_package)
    noncanonical_package["evidence_records"][0], noncanonical_package["evidence_records"][1] = (
        noncanonical_package["evidence_records"][1],
        noncanonical_package["evidence_records"][0],
    )
    canonical_identity = m.compute_identity(
        "input_package",
        canonical_package,
        exclude_fields=("input_package_identity_sha256",),
    )
    noncanonical_identity = m.compute_identity(
        "input_package",
        noncanonical_package,
        exclude_fields=("input_package_identity_sha256",),
    )
    assert canonical_identity != noncanonical_identity
    assert "reject_caller_supplied_noncanonical_evidence_record_sequence" in (
        m.CANONICAL_EVIDENCE_ORDERING_RULES
    )
    assert "never_silently_reorder_or_accept_a_noncanonical_input_package" in (
        m.CANONICAL_EVIDENCE_ORDERING_RULES
    )


def test_evidence_record_locator_id_excludes_value_and_identity_includes_it() -> None:
    before = _valid_active_anchor_record()
    package = _input_with_records(before)
    before = package["evidence_records"][0]
    before["evidence_record_id"] = _record_locator_id(package, before)
    after = copy.deepcopy(before)
    after["normalized_value"]["summary"] = "Changed bound source value."
    assert _record_locator_id(package, after) == before["evidence_record_id"]
    assert _record_content_identity(package, after) != _record_content_identity(package, before)

    relocated = copy.deepcopy(before)
    relocated["source_locator"]["anchor_id"] = "ANCHOR_1"
    assert _record_locator_id(package, relocated) != before["evidence_record_id"]
    assert _record_content_identity(package, relocated) != _record_content_identity(
        package, before
    )
    Draft202012Validator(_load_schema("weekly_shadow_01_analyst_input.schema.json")).validate(
        package
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: record["source_locator"].__setitem__("anchor_id", "ANCHOR_1"),
        lambda record: record["source_locator"].__setitem__(
            "source_artifact_role", "replacement_input_manifest.json"
        ),
        lambda record: record["normalized_value"].__setitem__("summary", "changed"),
        lambda record: record.__setitem__("authority_effect", "changed"),
        lambda record: record.__setitem__("evidence_record_id", "ev-other"),
    ],
)
def test_evidence_record_identity_recipe_is_sensitive_to_every_bound_nonself_field(mutation) -> None:
    package = _input_with_records(_valid_active_anchor_record())
    record = package["evidence_records"][0]
    baseline = _record_content_identity(package, record)
    mutation(record)
    assert _record_content_identity(package, record) != baseline


def test_resolved_artifact_and_package_generation_change_record_and_package_identities() -> None:
    package = _input_with_records(_valid_active_anchor_record())
    record = package["evidence_records"][0]
    locator_before = _record_locator_id(package, record)
    record_identity_before = _record_content_identity(package, record)
    package_identity_before = m.compute_identity(
        "input_package", package, exclude_fields=("input_package_identity_sha256",)
    )

    artifact_changed = copy.deepcopy(package)
    artifact_changed["source_artifact_bindings"][1]["source_artifact_identity_sha256"] = _OTHER_SHA
    artifact_record = artifact_changed["evidence_records"][0]
    assert _record_locator_id(artifact_changed, artifact_record) != locator_before
    assert _record_content_identity(artifact_changed, artifact_record) != record_identity_before

    generation_changed = copy.deepcopy(package)
    generation_changed["source_generation_id"] = "1" + "a" * 63
    generation_record = generation_changed["evidence_records"][0]
    assert _record_locator_id(generation_changed, generation_record) != locator_before
    assert _record_content_identity(generation_changed, generation_record) != record_identity_before
    assert m.compute_identity(
        "input_package",
        generation_changed,
        exclude_fields=("input_package_identity_sha256",),
    ) != package_identity_before


def test_evidence_record_locator_and_identity_payloads_are_unambiguous_under_one_frozen_domain() -> None:
    assert m.EVIDENCE_RECORD_ID_RECIPE["domain_name"] == "evidence_record"
    assert m.EVIDENCE_RECORD_IDENTITY_RECIPE["domain_name"] == "evidence_record"
    assert m.EVIDENCE_RECORD_ID_RECIPE["payload_kind"] != (
        m.EVIDENCE_RECORD_IDENTITY_RECIPE["payload_kind"]
    )
    package = _input_with_records(_valid_active_anchor_record())
    record = package["evidence_records"][0]
    assert m.compute_identity("evidence_record", _record_locator_payload(package, record)) != (
        _record_content_identity(package, record)
    )


def test_input_package_identity_recipe_excludes_only_its_self_identity() -> None:
    package = _valid_analyst_input()
    baseline = m.compute_identity(
        "input_package", package, exclude_fields=("input_package_identity_sha256",)
    )
    package["input_package_identity_sha256"] = _OTHER_SHA
    assert m.compute_identity(
        "input_package", package, exclude_fields=("input_package_identity_sha256",)
    ) == baseline

    for mutation in (
        lambda value: value.__setitem__("source_generation_id", "1" + "a" * 63),
        lambda value: value["source_artifact_bindings"][0].__setitem__(
            "source_artifact_identity_sha256", _OTHER_SHA
        ),
        lambda value: value["evidence_records"][0]["normalized_value"].__setitem__(
            "summary", "changed"
        ),
        lambda value: value["availability_diagnostic_record_ids"].append("ev-new"),
        lambda value: value.__setitem__("contract_catalog_identity_sha256", _OTHER_SHA),
        lambda value: value["negative_authority"].__setitem__("permission_effect", "changed"),
    ):
        changed = copy.deepcopy(package)
        mutation(changed)
        assert m.compute_identity(
            "input_package",
            changed,
            exclude_fields=("input_package_identity_sha256",),
        ) != baseline
    assert tuple(m.INPUT_PACKAGE_IDENTITY_RECIPE["excluded_fields"]) == (
        "input_package_identity_sha256",
    )


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
        lambda p: p["evidence_value_variants"].append("unknown_variant_v1"),
        lambda p: p["ordered_consumed_source_artifact_roles"].reverse(),
        lambda p: p["source_locator_contracts"]["active_anchor_v1"].__setitem__(
            "locator_type", "changed"
        ),
        lambda p: p["source_locator_contracts"]["availability_status_v1"][
            "availability_subjects"
        ].append("changed"),
        lambda p: p["package_owned_source_context"].__setitem__("lineage_type", "changed"),
        lambda p: p["source_locator_semantics"].append("changed_semantic"),
        lambda p: p["logical_locator_uniqueness_rules"].append("changed"),
        lambda p: p["evidence_variant_ranks"].__setitem__("active_anchor_v1", 99),
        lambda p: p["canonical_evidence_record_ordering"].__setitem__(
            "direction", "descending"
        ),
        lambda p: p[
            "future_ws01b_runtime_deferred_resource_bound_responsibilities"
        ].append("changed"),
        lambda p: p["static_runtime_responsibility_table"]["ws01b_enforced"].append(
            "changed"
        ),
        lambda p: p["future_ws01b_source_correlation_responsibilities"].append("changed"),
        lambda p: p["projection_exclusions"].append("changed_exclusion"),
        lambda p: p["diagnostic_reference_invariants"].append("changed_invariant"),
        lambda p: p.__setitem__("r2f_source_generation_id_pattern", "^[a-f][0-9a-f]{63}$"),
        lambda p: p["evidence_record_id_recipe"].__setitem__("payload_kind", "changed"),
        lambda p: p.__setitem__("catalog_version", "weekly_shadow_01_contract_catalog_v1"),
    ):
        mutated = copy.deepcopy(m._CONTRACT_CATALOG_PAYLOAD)
        mutation(mutated)
        assert m.compute_identity("contract_catalog", mutated) != baseline


def test_ws01a2_catalog_identity_changed_and_has_no_self_identity_field() -> None:
    assert m.CONTRACT_CATALOG_IDENTITY_SHA256 != (
        "6228473eec358c52996b8d694667009363aa3e270a5e6f9704cfbc15756093e4"
    )
    assert m.CONTRACT_CATALOG_IDENTITY_SHA256 != (
        "09822a9a9b7ac880f892db72343930b1384ca23ef67000b6a6c50e311ca07431"
    )
    assert m.CONTRACT_CATALOG_IDENTITY_SHA256 == (
        "36a0f850a089c3276c62dfe677ebfbce1ee9d1289e0487c3aad358db6cb556d4"
    )
    assert "contract_catalog_identity_sha256" not in m._CONTRACT_CATALOG_PAYLOAD
    assert m.compute_identity("contract_catalog", m._CONTRACT_CATALOG_PAYLOAD) == (
        m.CONTRACT_CATALOG_IDENTITY_SHA256
    )


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
            env={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": seed,
                "PYTHONPATH": str(repo_root() / "src"),
                "PATH": "/usr/bin:/bin",
            },
            capture_output=True,
            text=True,
            check=True,
        )
        results.add(proc.stdout.strip())
    assert len(results) == 1
    assert next(iter(results)) == m.CONTRACT_CATALOG_IDENTITY_SHA256


def test_identities_are_stable_across_locale_timezone_and_cwd(tmp_path: Path) -> None:
    script = (
        "from investment_orchestrator.observability import weekly_shadow_01_contracts as m; "
        "print(m.CONTRACT_CATALOG_IDENTITY_SHA256)"
    )
    results = set()
    for locale_name, timezone, cwd in (
        ("C", "UTC", repo_root()),
        ("C.UTF-8", "America/Los_Angeles", tmp_path),
        ("C", "Pacific/Auckland", tmp_path),
    ):
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=cwd,
            env={
                "LC_ALL": locale_name,
                "TZ": timezone,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "123",
                "PYTHONPATH": str(repo_root() / "src"),
                "PATH": "/usr/bin:/bin",
            },
            capture_output=True,
            text=True,
            check=True,
        )
        results.add(proc.stdout.strip())
    assert results == {m.CONTRACT_CATALOG_IDENTITY_SHA256}


def test_contract_identity_is_repository_relocation_independent(tmp_path: Path) -> None:
    relocated = tmp_path / "weekly_shadow_01_contracts.py"
    relocated.write_bytes(Path(m.__file__).read_bytes())
    namespace = __import__("runpy").run_path(str(relocated))
    assert namespace["CONTRACT_CATALOG_IDENTITY_SHA256"] == m.CONTRACT_CATALOG_IDENTITY_SHA256


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


def test_ws01a2_does_not_change_prompt_template_bytes_or_identity() -> None:
    assert len(m.PROMPT_TEMPLATE_BYTES) == 2023
    assert __import__("hashlib").sha256(m.PROMPT_TEMPLATE_BYTES).hexdigest() == (
        "527b0b8fea23f9fd7265e6287bdd14da55280fb3340269eae49af062c5c5e25c"
    )
    assert m.PROMPT_TEMPLATE_IDENTITY_SHA256 == (
        "e02839c54e4883af253158ab2295a61c2fe22ce483d296dae8af2e23bcd9dd37"
    )


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
    instance = _valid_analyst_input(include_evidence=False)
    instance["evidence_records"] = []
    for index in range(256):
        record = _valid_availability_record(record_id=f"ev-{index}")
        instance["evidence_records"].append(record)
    Draft202012Validator(schema).validate(instance)

    instance["evidence_records"].append(_valid_availability_record(record_id="ev-256"))
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(instance)


def test_active_anchor_summary_2048_code_point_boundary_is_schema_enforced() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    exact = _input_with_records(_valid_active_anchor_record(summary="é" * 2048))
    Draft202012Validator(schema).validate(exact)
    one_over = _input_with_records(_valid_active_anchor_record(summary="é" * 2049))
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(one_over)


@pytest.mark.parametrize("field", ["anchor_id", "ticker"])
def test_active_anchor_locator_and_ticker_strings_retain_2048_code_point_bound(
    field: str,
) -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    exact = _valid_active_anchor_record()
    one_over = _valid_active_anchor_record()
    if field == "anchor_id":
        exact["source_locator"]["anchor_id"] = "é" * 2048
        one_over["source_locator"]["anchor_id"] = "é" * 2049
    else:
        exact["normalized_value"]["applicable_tickers"] = ["é" * 2048]
        one_over["normalized_value"]["applicable_tickers"] = ["é" * 2049]
    Draft202012Validator(schema).validate(_input_with_records(exact))
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(_input_with_records(one_over))


def test_complete_active_anchor_ticker_array_retains_its_frozen_bound_without_truncation() -> None:
    schema = _load_schema("weekly_shadow_01_analyst_input.schema.json")
    exact_tickers = tuple(f"TICKER{index}" for index in range(1017))
    exact_record = _valid_active_anchor_record(applicable_tickers=exact_tickers)
    assert len(exact_record["normalized_value"]["applicable_tickers"]) == 1017
    Draft202012Validator(schema).validate(_input_with_records(exact_record))

    one_over_tickers = (*exact_tickers, "TICKER1017")
    one_over_record = _valid_active_anchor_record(applicable_tickers=one_over_tickers)
    assert len(one_over_record["normalized_value"]["applicable_tickers"]) == 1018
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(_input_with_records(one_over_record))


def test_canonical_helper_exact_array_object_and_depth_bounds() -> None:
    assert m.canonical_json_bytes([None] * 1024)
    with pytest.raises(m.CanonicalizationError, match="JSON_ARRAY_ITEM_BOUND_EXCEEDED"):
        m.canonical_json_bytes([None] * 1025)
    assert m.canonical_json_bytes({f"k{index}": None for index in range(1024)})
    with pytest.raises(m.CanonicalizationError, match="JSON_OBJECT_MEMBER_BOUND_EXCEEDED"):
        m.canonical_json_bytes({f"k{index}": None for index in range(1025)})

    exact_depth: object = None
    for _ in range(15):
        exact_depth = [exact_depth]
    assert m.canonical_json_bytes(exact_depth)
    one_over_depth: object = [exact_depth]
    with pytest.raises(m.CanonicalizationError, match="JSON_DEPTH_BOUND_EXCEEDED"):
        m.canonical_json_bytes(one_over_depth)


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


def test_all_new_public_contract_collections_are_immutable() -> None:
    assert isinstance(m.CONSUMED_SOURCE_ARTIFACT_ROLES, tuple)
    assert isinstance(m.EVIDENCE_VALUE_VARIANTS, tuple)
    assert isinstance(m.ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS, tuple)
    assert isinstance(m.AVAILABILITY_STATUS_NORMALIZED_VALUE_FIELDS, tuple)
    assert isinstance(m.DIAGNOSTIC_CODE_VALUES, tuple)
    assert isinstance(m.SOURCE_LOCATOR_TYPES, tuple)
    assert isinstance(m.AVAILABILITY_SUBJECTS, tuple)
    assert isinstance(m.OBSOLETE_EVIDENCE_RECORD_FIELDS, tuple)
    assert isinstance(m.OBSOLETE_ACTIVE_ANCHOR_NORMALIZED_VALUE_FIELDS, tuple)
    assert isinstance(m.SOURCE_LOCATOR_SEMANTICS, tuple)
    assert isinstance(m.LOGICAL_LOCATOR_UNIQUENESS_RULES, tuple)
    assert isinstance(m.CANONICAL_EVIDENCE_ORDERING_RULES, tuple)
    assert isinstance(m.CANONICAL_ORDER_INDEPENDENCE_INPUTS, tuple)
    assert isinstance(m.ANALYST_INPUT_SCHEMA_ENFORCED_CONSTRAINTS, tuple)
    assert isinstance(m.WS01B_RUNTIME_DEFERRED_RESOURCE_BOUND_RESPONSIBILITIES, tuple)
    assert isinstance(m.WS01B_SOURCE_CORRELATION_RESPONSIBILITIES, tuple)
    assert isinstance(m.WS01C_RESPONSE_VALIDATION_RESPONSIBILITIES, tuple)
    assert isinstance(m.DIAGNOSTIC_REFERENCE_INVARIANTS, tuple)
    assert isinstance(m.PROJECTION_EXCLUSIONS, tuple)
    with pytest.raises(TypeError):
        m.AVAILABILITY_SOURCE_LOCATOR_CONTRACT["locator_type"] = "changed"
    with pytest.raises(TypeError):
        m.ACTIVE_ANCHOR_SOURCE_LOCATOR_CONTRACT["source_artifact_role"] = "changed"
    with pytest.raises(TypeError):
        m.PACKAGE_OWNED_SOURCE_CONTEXT["lineage_type"] = "changed"
    with pytest.raises(TypeError):
        m.LOGICAL_LOCATOR_DEFINITION["ordered_components"] = ()
    with pytest.raises(TypeError):
        m.EVIDENCE_VARIANT_RANKS["active_anchor_v1"] = 99
    with pytest.raises(TypeError):
        m.AVAILABILITY_SUBJECT_RANKS["market_metrics"] = 99
    with pytest.raises(TypeError):
        m.EVIDENCE_RECORD_CANONICAL_ORDERING["direction"] = "descending"
    with pytest.raises(TypeError):
        m.STATIC_RUNTIME_RESPONSIBILITY_TABLE["ws01b_enforced"] = ()
    with pytest.raises(TypeError):
        m.EVIDENCE_RECORD_ID_RECIPE["payload_kind"] = "changed"
    with pytest.raises(TypeError):
        m.EVIDENCE_RECORD_IDENTITY_RECIPE["payload_kind"] = "changed"
    with pytest.raises(TypeError):
        m.INPUT_PACKAGE_IDENTITY_RECIPE["payload"] = "changed"


def test_caller_mutation_of_detached_contract_metadata_does_not_change_public_values_or_catalog() -> None:
    catalog_before = m.CONTRACT_CATALOG_IDENTITY_SHA256
    detached = dict(m.AVAILABILITY_SOURCE_LOCATOR_CONTRACT)
    detached["availability_subjects"] = list(detached["availability_subjects"])
    detached["availability_subjects"][0] = "changed"
    assert m.AVAILABILITY_SOURCE_LOCATOR_CONTRACT["availability_subjects"] == (
        "market_metrics",
        "scheduled_events_deterministic",
    )
    assert m.CONTRACT_CATALOG_IDENTITY_SHA256 == catalog_before


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
        env={
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(repo_root() / "src"),
            "PATH": "/usr/bin:/bin",
        },
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


def test_ws01a2_adds_no_runtime_adapter_builder_validator_publisher_or_cli_surface() -> None:
    source = Path(m.__file__).read_text(encoding="utf-8")
    forbidden_runtime_definitions = {
        "verify_r2f_v2_generation",
        "build_source_snapshot",
        "build_analyst_input_package",
        "render_analyst_prompt",
        "capture_analyst_response",
        "validate_analyst_response",
        "publish_analyst_report",
        "main",
    }
    definitions = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert not forbidden_runtime_definitions & definitions
