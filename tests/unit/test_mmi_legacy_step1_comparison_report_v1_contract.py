from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import inspect
import io
import json
from pathlib import Path
import struct

from jsonschema import Draft202012Validator
import pytest

import investment_orchestrator as package
import investment_orchestrator.mmi as mmi
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.mmi import canonical
from investment_orchestrator.mmi.canonical import (
    MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES,
    _MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_IDENTITY_DOMAIN,
)
from investment_orchestrator.offline import (
    mmi_legacy_step1_comparison_report_v1 as owner,
)
from investment_orchestrator.offline.mmi_legacy_step1_comparison_report_v1 import (
    COVERAGE_CATEGORIES,
    MAX_LEGACY_RESEARCH_RAW_BYTES,
    MAX_LEGACY_STRATEGY_SETTINGS_CANONICAL_BYTES,
    MmiLegacyStep1ComparisonReportV1Error,
    _comparison_class,
    _decode_legacy_bytes,
    _validate_report_canonical_size,
    build_mmi_legacy_step1_comparison_report_v1,
    validate_mmi_legacy_step1_comparison_report_v1,
)


SCHEMA_NAME = "mmi_legacy_step1_comparison_report_v1.schema.json"
SCHEMA_PATH = repo_root() / "schemas" / SCHEMA_NAME
OWNER_RELATIVE_PATH = (
    "src/investment_orchestrator/offline/"
    "mmi_legacy_step1_comparison_report_v1.py"
)
H1_MODULE = (
    "investment_orchestrator.mmi.legacy_step1_compatibility_candidate_v1"
)
H2_MODULE = (
    "investment_orchestrator.offline.mmi_legacy_step1_comparison_report_v1"
)
IDENTITY_FIELD = "comparison_report_identity_sha256"
IDENTITY_DOMAIN = b"mmi_legacy_step1_comparison_report_v1\0"
SHA256 = "a" * 64

ROOT_FIELDS = {
    "schema_version",
    "artifact_kind",
    "comparison_contract_version",
    "report_only",
    "authority_effect",
    "provenance",
    "legacy_contract_status",
    "instrument_comparison",
    "coverage_comparison",
    "limitations",
    "comparison_summary",
    IDENTITY_FIELD,
}
PROVENANCE_FIELDS = {
    "legacy_step1_compatibility_candidate_identity_sha256",
    "legacy_raw_bytes_sha256",
    "legacy_parsed_payload_canonical_sha256",
    "legacy_normalized_candidate_canonical_sha256",
    "legacy_strategy_settings_canonical_sha256",
}
LEGACY_CONTRACT_STATUS_FIELDS = {
    "raw_parse_status",
    "strict_handoff_status",
    "strict_handoff_blocker_count",
    "legacy_source_shape",
    "legacy_self_reported_validation_passed",
}
INSTRUMENT_COMPARISON_FIELDS = {
    "comparison_basis",
    "h1_instrument_count",
    "h1_policy_roles_present",
    "legacy_instrument_count",
    "shared_instrument_count",
    "membership_equal",
    "shared_sequence_equal",
    "h1_only_tickers",
    "legacy_only_tickers",
    "legacy_duplicate_tickers",
    "legacy_role_layers_present",
}
COVERAGE_ROW_FIELDS = {
    "category",
    "h1_status",
    "legacy_status",
    "comparison_class",
    "h1_count",
    "legacy_count",
    "legacy_consumer_class",
}
COMPARISON_SUMMARY_FIELDS = {
    "coverage_available_in_both_count",
    "coverage_only_in_one_count",
    "coverage_not_comparable_count",
    "limitation_count",
}
LIMITATION_CODES = {
    "H1_AND_LEGACY_REFERENCE_SYSTEMS_STRUCTURALLY_DISTINCT",
    "H1_SOURCE_CAPABILITY_GAPS_REQUIRE_TIER_B",
    "LEGACY_INSTRUMENT_IDENTIFIERS_COMPARED_WITHOUT_NORMALIZATION",
    "LEGACY_STRATEGY_SETTINGS_NOT_PROVEN_IDENTICAL_TO_H1_POLICY_SOURCE",
    "LEGACY_STRICT_VALIDATOR_HAS_NO_DECLARED_CONTRACT_VERSION",
    "POLICY_ROLE_AND_LEGACY_ROLE_LAYER_MAPPING_UNDEFINED",
    "TARGET_WEIGHTS_NOT_DERIVABLE_FROM_CURRENT_POLICY_METHOD",
}
SOURCE_INPUT_NAMES = {
    "legacy_step1_compatibility_candidate",
    "validated_grounded_analysis_response",
    "raw_response_envelope",
    "evidence_bundle",
    "policy_projection",
    "policy_source",
    "portfolio_projection",
    "portfolio_source",
    "run_context",
    "legacy_research_raw_bytes",
    "legacy_strategy_settings",
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


def _coverage_rows() -> list[dict[str, object]]:
    return [
        {
            "category": category,
            "h1_status": "PRESENT",
            "legacy_status": "PRESENT",
            "comparison_class": "AVAILABLE_IN_BOTH",
            "h1_count": 1,
            "legacy_count": 1,
            "legacy_consumer_class": "NO_CONSUMER",
        }
        for category in COVERAGE_CATEGORIES
    ]


def _artifact() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "mmi_legacy_step1_comparison_report_v1",
        "artifact_kind": "MMI_LEGACY_STEP1_COMPARISON_REPORT",
        "comparison_contract_version": (
            "mmi_legacy_step1_comparison_compiler_v1"
        ),
        "report_only": True,
        "authority_effect": "NONE",
        "provenance": {
            "legacy_step1_compatibility_candidate_identity_sha256": SHA256,
            "legacy_raw_bytes_sha256": "b" * 64,
            "legacy_parsed_payload_canonical_sha256": "c" * 64,
            "legacy_normalized_candidate_canonical_sha256": "d" * 64,
            "legacy_strategy_settings_canonical_sha256": "e" * 64,
        },
        "legacy_contract_status": {
            "raw_parse_status": "PARSED",
            "strict_handoff_status": "STRICT_HANDOFF_VALID",
            "strict_handoff_blocker_count": 0,
            "legacy_source_shape": "strict",
            "legacy_self_reported_validation_passed": True,
        },
        "instrument_comparison": {
            "comparison_basis": "STRICT_VALID",
            "h1_instrument_count": 2,
            "h1_policy_roles_present": ["CORE", "SATELLITE"],
            "legacy_instrument_count": 2,
            "shared_instrument_count": 2,
            "membership_equal": True,
            "shared_sequence_equal": True,
            "h1_only_tickers": [],
            "legacy_only_tickers": [],
            "legacy_duplicate_tickers": [],
            "legacy_role_layers_present": ["sector_alpha_tilt"],
        },
        "coverage_comparison": _coverage_rows(),
        "limitations": sorted(LIMITATION_CODES),
        "comparison_summary": {
            "coverage_available_in_both_count": 15,
            "coverage_only_in_one_count": 0,
            "coverage_not_comparable_count": 0,
            "limitation_count": 7,
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
    assert len(ROOT_FIELDS) == 12
    assert properties["schema_version"] == {
        "const": "mmi_legacy_step1_comparison_report_v1"
    }
    assert properties["artifact_kind"] == {
        "const": "MMI_LEGACY_STEP1_COMPARISON_REPORT"
    }
    assert properties["comparison_contract_version"] == {
        "const": "mmi_legacy_step1_comparison_compiler_v1"
    }
    assert properties["report_only"] == {"const": True}
    assert properties["authority_effect"] == {"const": "NONE"}
    definitions = schema["$defs"]
    assert type(definitions) is dict
    expected = {
        "provenance": PROVENANCE_FIELDS,
        "legacy_contract_status": LEGACY_CONTRACT_STATUS_FIELDS,
        "instrument_comparison": INSTRUMENT_COMPARISON_FIELDS,
        "coverage_row": COVERAGE_ROW_FIELDS,
        "comparison_summary": COMPARISON_SUMMARY_FIELDS,
    }
    for name, fields in expected.items():
        definition = definitions[name]
        assert definition["additionalProperties"] is False, name
        assert set(definition["required"]) == fields, name
        assert set(definition["properties"]) == fields, name
    assert all(
        item.get("additionalProperties") is False
        for item in _object_schemas(schema)
    )
    assert set(definitions["limitations"]["items"]["enum"]) == LIMITATION_CODES
    coverage = definitions["coverage_comparison"]
    assert coverage["minItems"] == coverage["maxItems"] == 15
    assert tuple(
        entry["allOf"][1]["properties"]["category"]["const"]
        for entry in coverage["prefixItems"]
    ) == COVERAGE_CATEGORIES


def test_valid_artifact_passes_and_one_unknown_authority_field_is_rejected(
) -> None:
    value = _artifact()
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    changed = deepcopy(value)
    changed["new_buy_permission"] = True
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(changed, schema_name=SCHEMA_NAME)


def test_not_evaluated_strict_status_cannot_serialize_a_blocker_count(
) -> None:
    value = _artifact()
    status = value["legacy_contract_status"]
    assert type(status) is dict
    status["raw_parse_status"] = "LEGACY_PARSE_FAILURE"
    status["strict_handoff_status"] = "NOT_EVALUATED"
    status["legacy_source_shape"] = None
    status["legacy_self_reported_validation_passed"] = None
    status["strict_handoff_blocker_count"] = 0
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    status["strict_handoff_blocker_count"] = None
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)


@pytest.mark.parametrize(
    ("field", "empty_value"),
    [
        ("legacy_instrument_count", 0),
        ("shared_instrument_count", 0),
        ("membership_equal", False),
        ("shared_sequence_equal", False),
        ("h1_only_tickers", []),
        ("legacy_only_tickers", []),
        ("legacy_duplicate_tickers", []),
        ("legacy_role_layers_present", []),
    ],
)
def test_unavailable_basis_rejects_empty_values_and_requires_null(
    field: str,
    empty_value: object,
) -> None:
    value = _artifact()
    comparison = value["instrument_comparison"]
    assert type(comparison) is dict
    comparison["comparison_basis"] = "UNAVAILABLE_PARSE_FAILURE"
    for name in INSTRUMENT_COMPARISON_FIELDS - {
        "comparison_basis",
        "h1_instrument_count",
        "h1_policy_roles_present",
    }:
        comparison[name] = None
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    comparison[field] = empty_value
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(value, schema_name=SCHEMA_NAME)


def test_comparison_class_is_a_total_function_of_the_two_statuses() -> None:
    h1_statuses = (
        "PRESENT",
        "ABSENT",
        "EXPLICITLY_UNAVAILABLE_TIER_A",
        "POLICY_METHOD_ABSENT",
        "NOT_REPRESENTED",
    )
    legacy_statuses = (
        "PRESENT",
        "ABSENT",
        "NOT_REPRESENTED",
        "UNAVAILABLE_DUE_TO_LEGACY_CONTRACT",
    )
    allowed = {
        "AVAILABLE_IN_BOTH",
        "AVAILABLE_ONLY_IN_H1",
        "AVAILABLE_ONLY_IN_LEGACY",
        "AVAILABLE_IN_NEITHER",
        "NOT_COMPARABLE",
    }
    for h1_status in h1_statuses:
        for legacy_status in legacy_statuses:
            assert _comparison_class(h1_status, legacy_status) in allowed
    assert _comparison_class("PRESENT", "PRESENT") == "AVAILABLE_IN_BOTH"
    assert _comparison_class("PRESENT", "ABSENT") == "AVAILABLE_ONLY_IN_H1"
    assert _comparison_class(
        "EXPLICITLY_UNAVAILABLE_TIER_A",
        "PRESENT",
    ) == "AVAILABLE_ONLY_IN_LEGACY"
    assert _comparison_class(
        "POLICY_METHOD_ABSENT",
        "PRESENT",
    ) == "AVAILABLE_ONLY_IN_LEGACY"
    assert _comparison_class(
        "POLICY_METHOD_ABSENT",
        "ABSENT",
    ) == "AVAILABLE_IN_NEITHER"
    assert _comparison_class(
        "PRESENT",
        "UNAVAILABLE_DUE_TO_LEGACY_CONTRACT",
    ) == "NOT_COMPARABLE"


def test_identity_domain_framing_and_complete_preimage_are_exact() -> None:
    value = _artifact()
    assert set(value) == ROOT_FIELDS
    assert value[IDENTITY_FIELD] == _record_identity(value)
    nested = deepcopy(value)
    summary = nested["comparison_summary"]
    assert type(summary) is dict
    summary["limitation_count"] = 6
    assert _record_identity(nested) != value[IDENTITY_FIELD]
    self_only = deepcopy(value)
    self_only[IDENTITY_FIELD] = "f" * 64
    assert _record_identity(self_only) == value[IDENTITY_FIELD]
    assert (
        _MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_IDENTITY_DOMAIN
        == IDENTITY_DOMAIN
    )
    assert IDENTITY_DOMAIN.endswith(b"\0")
    assert b"\0" not in IDENTITY_DOMAIN[:-1]
    assert IDENTITY_DOMAIN.decode("ascii")


def test_private_complete_report_resource_guard_has_exact_boundary() -> None:
    maximum = MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES
    assert maximum == 65_536
    empty_size = len(_canonical({"padding": ""}))
    at_limit = {"padding": "x" * (maximum - empty_size)}
    above_limit = {"padding": "x" * (maximum + 1 - empty_size)}
    assert len(_canonical(at_limit)) == maximum
    assert len(_canonical(above_limit)) == maximum + 1
    _validate_report_canonical_size(at_limit)
    with pytest.raises(MmiLegacyStep1ComparisonReportV1Error) as excinfo:
        _validate_report_canonical_size(above_limit)
    assert excinfo.value.code == (
        "MMI_LEGACY_STEP1_COMPARISON_RESOURCE_LIMIT_EXCEEDED"
    )


def test_in_memory_decoding_matches_the_legacy_read_text_behavior(
    tmp_path: Path,
) -> None:
    raw = b"\xef\xbb\xbf{\r\n  \"a\": 1\r}\n"
    path = tmp_path / "raw_output.txt"
    path.write_bytes(raw)
    assert _decode_legacy_bytes(raw) == path.read_text(encoding="utf-8")
    assert _decode_legacy_bytes(raw) == io.TextIOWrapper(
        io.BytesIO(raw),
        encoding="utf-8",
        errors="strict",
        newline=None,
    ).read()
    assert _decode_legacy_bytes(raw).startswith("﻿")
    assert "\r" not in _decode_legacy_bytes(raw)


def test_public_signatures_are_exact_keyword_only_and_default_free() -> None:
    builder = inspect.signature(build_mmi_legacy_step1_comparison_report_v1)
    validator = inspect.signature(
        validate_mmi_legacy_step1_comparison_report_v1
    )
    assert set(builder.parameters) == SOURCE_INPUT_NAMES
    assert len(builder.parameters) == 11
    assert set(validator.parameters) == SOURCE_INPUT_NAMES | {"value"}
    assert len(validator.parameters) == 12
    for signature in (builder, validator):
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            and parameter.default is inspect.Parameter.empty
            for parameter in signature.parameters.values()
        )


def _imported_names(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.name for alias in node.names)
    return names


def test_owner_delegates_to_the_existing_contract_owners_only() -> None:
    path = repo_root() / OWNER_RELATIVE_PATH
    assert _imported_names(path, H1_MODULE) == {
        "MmiLegacyStep1CompatibilityCandidateV1Error",
        "validate_mmi_legacy_step1_compatibility_candidate_v1",
    }
    assert _imported_names(
        path,
        "investment_orchestrator.parsers.extract_research_json",
    ) == {"ResearchExtractionError", "parse_research_output_text"}
    assert _imported_names(
        path,
        "investment_orchestrator.normalizers.research_handoff_candidate",
    ) == {"normalize_research_handoff_candidate"}
    assert _imported_names(
        path,
        "investment_orchestrator.validators.validate_research_handoff",
    ) == {"validate_research_handoff"}
    source = path.read_text(encoding="utf-8")
    assert "build_mmi_legacy_step1_compatibility_candidate_v1" not in source


def test_owner_imports_no_runtime_authority_or_capability_module() -> None:
    tree = ast.parse((repo_root() / OWNER_RELATIVE_PATH).read_text("utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    prohibited = (
        "investment_orchestrator.workflow",
        "investment_orchestrator.state",
        "investment_orchestrator.observability",
        "investment_orchestrator.orders",
        "investment_orchestrator.broker",
        "investment_orchestrator.cli",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "subprocess",
        "urllib.request",
    )
    assert not [
        name
        for name in imported
        for prefix in prohibited
        if name == prefix or name.startswith(f"{prefix}.")
    ]


def _production_imports() -> dict[str, set[str]]:
    root = repo_root() / "src/investment_orchestrator"
    imports: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module or "")
        imports[
            path.relative_to(repo_root()).as_posix()
        ] = names
    return imports


def test_h1_and_h2_gain_only_the_explicit_capture_consumer() -> None:
    imports = _production_imports()
    h1_consumers = sorted(
        path
        for path, names in imports.items()
        if any(
            name == H1_MODULE or name.startswith(f"{H1_MODULE}.")
            for name in names
        )
    )
    h2_consumers = sorted(
        path
        for path, names in imports.items()
        if path != OWNER_RELATIVE_PATH
        and any(
            name == H2_MODULE or name.startswith(f"{H2_MODULE}.")
            for name in names
        )
    )
    session_path = (
        "src/investment_orchestrator/offline/"
        "mmi_h2c_manual_capture_session.py"
    )
    assert h1_consumers == [session_path, OWNER_RELATIVE_PATH]
    assert h2_consumers == [session_path]


def test_inventory_domain_schema_and_package_posture_are_exact() -> None:
    production_paths = tuple(
        sorted((repo_root() / "src/investment_orchestrator").rglob("*.py"))
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
    assert len(production_paths) == 144
    assert len(schema_paths) == 42
    assert len(domains) == len(set(domains)) == 18
    assert IDENTITY_DOMAIN in domains
    assert SCHEMA_PATH in schema_paths
    assert mmi.__all__ == ()
    assert not hasattr(package, "__all__")
    assert owner.__all__ == (
        "MmiLegacyStep1ComparisonReportV1Error",
        "build_mmi_legacy_step1_comparison_report_v1",
        "validate_mmi_legacy_step1_comparison_report_v1",
    )


def test_h2_replay_input_ceilings_are_explicit_and_do_not_raise_upstream(
) -> None:
    assert MAX_LEGACY_RESEARCH_RAW_BYTES == 262_144
    assert MAX_LEGACY_STRATEGY_SETTINGS_CANONICAL_BYTES == 262_144
    assert (
        canonical.MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES
        == 262_144
    )
    assert canonical.MAXIMUM_MMI_RAW_RESPONSE_BYTES == 262_144
    assert MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES == 65_536
