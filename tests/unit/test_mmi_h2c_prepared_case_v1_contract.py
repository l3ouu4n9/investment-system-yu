from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
from types import MappingProxyType

from jsonschema import Draft202012Validator
import pytest

import investment_orchestrator as package
import investment_orchestrator.mmi as mmi
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi import canonical
from investment_orchestrator.offline import mmi_h2c_case_bundle_v1
from investment_orchestrator.offline import mmi_h2c_prepared_case_v1 as owner


SCHEMA_NAME = "mmi_h2c_prepared_case_v1.schema.json"
IDENTITY_FIELD = "prepared_case_identity_sha256"
IDENTITY_DOMAIN = b"mmi_h2c_prepared_case_v1\0"
EXPECTED_FIELDS = {
    "schema_version",
    "artifact_kind",
    "preparation_contract_version",
    "report_only",
    "authority_effect",
    "workflow_status",
    "evaluation_timestamp_utc",
    "strategy_settings_source",
    "portfolio_snapshot_source",
    "legacy_prompt_template",
    "grounded_prompt",
    "h1_prompt",
    "legacy_prompt",
    "response_leaves",
    "result_leaves",
    IDENTITY_FIELD,
}


def _schema() -> dict[str, object]:
    value = json.loads(
        (repo_root() / "schemas" / SCHEMA_NAME).read_text(encoding="utf-8")
    )
    assert type(value) is dict
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _identity(value: dict[str, object]) -> str:
    preimage = deepcopy(value)
    preimage.pop(IDENTITY_FIELD, None)
    encoded = _canonical_bytes(preimage)
    framed = IDENTITY_DOMAIN + len(encoded).to_bytes(8, "big") + encoded
    return hashlib.sha256(framed).hexdigest()


def _reidentify(value: dict[str, object]) -> dict[str, object]:
    value[IDENTITY_FIELD] = _identity(value)
    return value


def _inputs() -> dict[str, object]:
    return {
        "evaluation_timestamp_utc": "2026-08-04T01:15:34.942524Z",
        "strategy_settings_source_record": {
            "opaque_strategy": {
                "complete": ["alpha", {"nested": True}],
                "unrecognized_semantics": "preserved",
            }
        },
        "portfolio_snapshot_source_record": {
            "opaque_portfolio": {
                "positions": ["QQQ", "VOO"],
                "unknown_is_not_zero": None,
            }
        },
        "legacy_prompt_template_bytes": b"legacy-template\n",
        "grounded_prompt": {
            "opaque_grounded_prompt": {
                "prompt_text": "grounded prompt",
                "extension": [1, 2, 3],
            }
        },
        "h1_prompt_bytes": b"h1 prompt\n",
        "legacy_prompt_bytes": b"legacy prompt\n",
    }


@pytest.fixture()
def prepared_case() -> dict[str, object]:
    return owner._build_mmi_h2c_prepared_case_v1(**_inputs())


def test_schema_is_closed_exact_and_report_only() -> None:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == EXPECTED_FIELDS
    properties = schema["properties"]
    assert type(properties) is dict
    assert set(properties) == EXPECTED_FIELDS
    assert len(EXPECTED_FIELDS) == 16
    assert properties["schema_version"] == {
        "const": "mmi_h2c_prepared_case_v1"
    }
    assert properties["artifact_kind"] == {
        "const": "MMI_H2C_PREPARED_CASE"
    }
    assert properties["preparation_contract_version"] == {
        "const": "mmi_h2c_persisted_case_prepare_v1"
    }
    assert properties["report_only"] == {"const": True}
    assert properties["authority_effect"] == {"const": "NONE"}
    assert properties["workflow_status"] == {
        "const": "AWAITING_OPERATOR_RESPONSES"
    }


def test_nested_envelopes_are_closed_and_paths_are_fixed() -> None:
    defs = _schema()["$defs"]
    assert type(defs) is dict
    expected = {
        "strategy_settings_source": {
            "source_record",
            "archive_relative_path",
        },
        "portfolio_snapshot_source": {
            "source_record",
            "archive_relative_path",
        },
        "legacy_prompt_template": {
            "repository_relative_locator",
            "archive_relative_path",
            "byte_length",
            "sha256",
        },
        "h1_prompt": {"relative_path", "byte_length", "sha256"},
        "legacy_prompt": {
            "relative_path",
            "byte_length",
            "sha256",
            "compiler_contract_version",
        },
        "response_leaves": {"h1", "legacy"},
        "result_leaves": {
            "case_evidence_bundle",
            "comparison_report",
            "receipt",
        },
    }
    for name, fields in expected.items():
        body = defs[name]
        assert body["type"] == "object"
        assert body["additionalProperties"] is False
        assert set(body["required"]) == fields
        assert set(body["properties"]) == fields
    assert defs["strategy_settings_source"]["properties"][
        "archive_relative_path"
    ] == {"const": "archive/strategy_settings.yaml"}
    assert defs["portfolio_snapshot_source"]["properties"][
        "archive_relative_path"
    ] == {"const": "archive/portfolio_snapshot.txt"}
    assert defs["legacy_prompt_template"]["properties"] == {
        "repository_relative_locator": {
            "const": "prompts/research_dual_lane.txt"
        },
        "archive_relative_path": {
            "const": "archive/research_dual_lane.txt"
        },
        "byte_length": {
            "type": "integer",
            "minimum": 1,
            "maximum": 262144,
        },
        "sha256": {"$ref": "#/$defs/sha256"},
    }
    assert defs["h1_prompt"]["properties"]["relative_path"] == {
        "const": "prompts/h1_prompt.txt"
    }
    assert defs["legacy_prompt"]["properties"]["relative_path"] == {
        "const": "prompts/legacy_prompt.txt"
    }
    assert defs["legacy_prompt"]["properties"][
        "compiler_contract_version"
    ] == {"const": "mmi_legacy_step1_compatibility_compiler_v1"}
    assert defs["response_leaves"]["properties"] == {
        "h1": {"const": "responses/h1_response.raw"},
        "legacy": {"const": "responses/legacy_response.raw"},
    }
    assert defs["result_leaves"]["properties"] == {
        "case_evidence_bundle": {
            "const": "artifacts/case_evidence_bundle.json"
        },
        "comparison_report": {
            "const": "artifacts/comparison_report.json"
        },
        "receipt": {"const": "artifacts/receipt.json"},
    }


def test_opaque_children_are_mapping_only_and_not_semantically_duplicated() -> None:
    defs = _schema()["$defs"]
    assert defs["opaque_mapping"] == {"type": "object"}
    for wrapper in (
        "strategy_settings_source",
        "portfolio_snapshot_source",
    ):
        assert defs[wrapper]["properties"]["source_record"] == {
            "$ref": "#/$defs/opaque_mapping"
        }
    assert _schema()["properties"]["grounded_prompt"] == {
        "$ref": "#/$defs/opaque_mapping"
    }
    text = Path(owner.__file__).read_text(encoding="utf-8")
    assert "validate_mmi_grounded_prompt" not in text
    assert "validate_mmi_source" not in text
    assert "MmiProjectionRunContext" not in text
    assert "MmiCapturedSource" not in text


def test_builder_preserves_complete_opaque_mappings_and_fixed_roles() -> None:
    supplied = _inputs()
    prepared = owner._build_mmi_h2c_prepared_case_v1(**supplied)
    settings = prepared["strategy_settings_source"]
    portfolio = prepared["portfolio_snapshot_source"]
    assert type(settings) is dict and type(portfolio) is dict
    assert settings["source_record"] == supplied[
        "strategy_settings_source_record"
    ]
    assert portfolio["source_record"] == supplied[
        "portfolio_snapshot_source_record"
    ]
    assert prepared["grounded_prompt"] == supplied["grounded_prompt"]
    assert prepared["response_leaves"] == {
        "h1": "responses/h1_response.raw",
        "legacy": "responses/legacy_response.raw",
    }
    assert prepared["result_leaves"] == {
        "case_evidence_bundle": "artifacts/case_evidence_bundle.json",
        "comparison_report": "artifacts/comparison_report.json",
        "receipt": "artifacts/receipt.json",
    }
    assert owner.validate_mmi_h2c_prepared_case_v1(
        prepared_case=prepared
    ) is None


def test_builder_deeply_detaches_every_caller_mapping() -> None:
    supplied = _inputs()
    expected_settings = deepcopy(supplied["strategy_settings_source_record"])
    expected_portfolio = deepcopy(supplied["portfolio_snapshot_source_record"])
    expected_prompt = deepcopy(supplied["grounded_prompt"])
    prepared = owner._build_mmi_h2c_prepared_case_v1(**supplied)

    for key in (
        "strategy_settings_source_record",
        "portfolio_snapshot_source_record",
        "grounded_prompt",
    ):
        mapping = supplied[key]
        assert type(mapping) is dict
        mapping["post_build_mutation"] = {"must_not_leak": True}

    assert prepared["strategy_settings_source"][
        "source_record"
    ] == expected_settings
    assert prepared["portfolio_snapshot_source"][
        "source_record"
    ] == expected_portfolio
    assert prepared["grounded_prompt"] == expected_prompt


def test_immutable_top_level_mapping_proxies_are_completely_materialized() -> None:
    supplied = _inputs()
    for key in (
        "strategy_settings_source_record",
        "portfolio_snapshot_source_record",
        "grounded_prompt",
    ):
        mapping = supplied[key]
        assert type(mapping) is dict
        supplied[key] = MappingProxyType(mapping)
    prepared = owner._build_mmi_h2c_prepared_case_v1(**supplied)
    assert prepared["strategy_settings_source"]["source_record"] == dict(
        supplied["strategy_settings_source_record"]
    )
    assert prepared["portfolio_snapshot_source"]["source_record"] == dict(
        supplied["portfolio_snapshot_source_record"]
    )
    assert prepared["grounded_prompt"] == dict(supplied["grounded_prompt"])


@pytest.mark.parametrize(
    "slot",
    (
        "strategy_settings_source",
        "portfolio_snapshot_source",
        "grounded_prompt",
    ),
)
def test_non_mapping_opaque_children_are_rejected(
    prepared_case: dict[str, object],
    slot: str,
) -> None:
    mutated = deepcopy(prepared_case)
    if slot == "grounded_prompt":
        mutated[slot] = []
    else:
        wrapper = mutated[slot]
        assert type(wrapper) is dict
        wrapper["source_record"] = []
    _reidentify(mutated)
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error):
        owner.validate_mmi_h2c_prepared_case_v1(prepared_case=mutated)


@pytest.mark.parametrize(
    ("slot", "field"),
    (
        ("strategy_settings_source", "archive_relative_path"),
        ("portfolio_snapshot_source", "archive_relative_path"),
        ("legacy_prompt_template", "repository_relative_locator"),
        ("legacy_prompt_template", "archive_relative_path"),
        ("h1_prompt", "relative_path"),
        ("legacy_prompt", "relative_path"),
        ("response_leaves", "h1"),
        ("response_leaves", "legacy"),
        ("result_leaves", "case_evidence_bundle"),
        ("result_leaves", "comparison_report"),
        ("result_leaves", "receipt"),
    ),
)
def test_path_substitution_and_traversal_are_rejected(
    prepared_case: dict[str, object],
    slot: str,
    field: str,
) -> None:
    mutated = deepcopy(prepared_case)
    member = mutated[slot]
    assert type(member) is dict
    member[field] = "../substituted"
    _reidentify(mutated)
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error):
        owner.validate_mmi_h2c_prepared_case_v1(prepared_case=mutated)


def test_missing_extra_and_nested_extra_fields_are_rejected(
    prepared_case: dict[str, object],
) -> None:
    missing = deepcopy(prepared_case)
    missing.pop("workflow_status")
    _reidentify(missing)
    extra = deepcopy(prepared_case)
    extra["unexpected"] = True
    _reidentify(extra)
    nested = deepcopy(prepared_case)
    wrapper = nested["h1_prompt"]
    assert type(wrapper) is dict
    wrapper["unexpected"] = True
    _reidentify(nested)
    for value in (missing, extra, nested):
        with pytest.raises(owner.MmiH2cPreparedCaseV1Error):
            owner.validate_mmi_h2c_prepared_case_v1(prepared_case=value)


@pytest.mark.parametrize("field", sorted(EXPECTED_FIELDS - {IDENTITY_FIELD}))
def test_mutation_of_every_preimage_top_level_field_invalidates_identity(
    prepared_case: dict[str, object],
    field: str,
) -> None:
    mutated = deepcopy(prepared_case)
    mutated[field] = {"changed": field}
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error):
        owner.validate_mmi_h2c_prepared_case_v1(prepared_case=mutated)


def test_representative_nested_mutation_invalidates_identity(
    prepared_case: dict[str, object],
) -> None:
    mutated = deepcopy(prepared_case)
    grounded = mutated["grounded_prompt"]
    assert type(grounded) is dict
    nested = grounded["opaque_grounded_prompt"]
    assert type(nested) is dict
    nested["extension"] = [1, 2, 3, 4]
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error):
        owner.validate_mmi_h2c_prepared_case_v1(prepared_case=mutated)


@pytest.mark.parametrize(
    ("slot", "field"),
    (
        ("legacy_prompt_template", "sha256"),
        ("h1_prompt", "sha256"),
        ("legacy_prompt", "sha256"),
    ),
)
def test_malformed_nested_hashes_are_rejected_with_matching_outer_identity(
    prepared_case: dict[str, object],
    slot: str,
    field: str,
) -> None:
    mutated = deepcopy(prepared_case)
    member = mutated[slot]
    assert type(member) is dict
    member[field] = "A" * 64
    _reidentify(mutated)
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error):
        owner.validate_mmi_h2c_prepared_case_v1(prepared_case=mutated)


def test_identity_framing_is_independent_and_excludes_only_self(
    prepared_case: dict[str, object],
) -> None:
    assert len(IDENTITY_DOMAIN) == 25
    assert prepared_case[IDENTITY_FIELD] == _identity(prepared_case)
    changed_self = deepcopy(prepared_case)
    changed_self[IDENTITY_FIELD] = "f" * 64
    assert _identity(changed_self) == _identity(prepared_case)
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error):
        owner.validate_mmi_h2c_prepared_case_v1(prepared_case=changed_self)


def test_ceiling_equals_the_smallest_independently_derived_maximum() -> None:
    zero = "0" * 64
    framing = len(
        _canonical_bytes(
            {
                "schema_version": "mmi_h2c_prepared_case_v1",
                "artifact_kind": "MMI_H2C_PREPARED_CASE",
                "preparation_contract_version": (
                    "mmi_h2c_persisted_case_prepare_v1"
                ),
                "report_only": True,
                "authority_effect": "NONE",
                "workflow_status": "AWAITING_OPERATOR_RESPONSES",
                "evaluation_timestamp_utc": "2026-08-04T01:15:34.942524Z",
                "strategy_settings_source": {
                    "source_record": {},
                    "archive_relative_path": "archive/strategy_settings.yaml",
                },
                "portfolio_snapshot_source": {
                    "source_record": {},
                    "archive_relative_path": "archive/portfolio_snapshot.txt",
                },
                "legacy_prompt_template": {
                    "repository_relative_locator": (
                        "prompts/research_dual_lane.txt"
                    ),
                    "archive_relative_path": "archive/research_dual_lane.txt",
                    "byte_length": 262144,
                    "sha256": zero,
                },
                "grounded_prompt": {},
                "h1_prompt": {
                    "relative_path": "prompts/h1_prompt.txt",
                    "byte_length": 65536,
                    "sha256": zero,
                },
                "legacy_prompt": {
                    "relative_path": "prompts/legacy_prompt.txt",
                    "byte_length": 3170307,
                    "sha256": zero,
                    "compiler_contract_version": (
                        "mmi_legacy_step1_compatibility_compiler_v1"
                    ),
                },
                "response_leaves": {
                    "h1": "responses/h1_response.raw",
                    "legacy": "responses/legacy_response.raw",
                },
                "result_leaves": {
                    "case_evidence_bundle": (
                        "artifacts/case_evidence_bundle.json"
                    ),
                    "comparison_report": "artifacts/comparison_report.json",
                    "receipt": "artifacts/receipt.json",
                },
                IDENTITY_FIELD: zero,
            }
        )
    ) - 3 * len(b"{}")
    grounded_prompt_fixed = len(
        _canonical_bytes(
            {
                "schema_version": "mmi_grounded_prompt_v2",
                "artifact_kind": "MMI_GROUNDED_PROMPT",
                "report_only": True,
                "authority_effect": "NONE",
                "analyst_visible_evidence_view_identity_sha256": zero,
                "instruction_set_version": (
                    "mmi_grounded_prompt_instruction_set_v2"
                ),
                "expected_response_schema_version": (
                    "mmi_grounded_analysis_response_v2"
                ),
                "manual_handoff_required": True,
                "prompt_context_binding_sha256": zero,
                "prompt_text": "",
                "grounded_prompt_artifact_identity_sha256": zero,
            }
        )
    )
    # A one-byte control character has the six-byte JSON escape ``\u0000``.
    assert len(json.dumps("\x00", ensure_ascii=False).encode("utf-8")) - 2 == 6
    grounded_prompt_maximum = (
        grounded_prompt_fixed
        + canonical.MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES * 6
    )
    source_maximum = (
        2 * mmi_h2c_case_bundle_v1._SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES
    )
    assert framing == 1_517
    assert source_maximum == 16_384
    assert grounded_prompt_maximum == 393_852
    assert owner._GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES == (
        grounded_prompt_maximum
    )
    assert framing + source_maximum + grounded_prompt_maximum == 411_753
    assert owner._MAXIMUM_CANONICAL_BYTES == 411_753


def test_ceiling_is_a_maximum_and_rejects_an_oversized_child() -> None:
    representative = owner._build_mmi_h2c_prepared_case_v1(**_inputs())
    assert len(_canonical_bytes(representative)) < owner._MAXIMUM_CANONICAL_BYTES
    supplied = _inputs()
    supplied["grounded_prompt"] = {"too_large": "x" * 393_852}
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error):
        owner._build_mmi_h2c_prepared_case_v1(**supplied)


def test_public_api_is_minimal_and_builder_is_private_keyword_only() -> None:
    assert owner.__all__ == (
        "MmiH2cPreparedCaseV1Error",
        "validate_mmi_h2c_prepared_case_v1",
    )
    assert "_build_mmi_h2c_prepared_case_v1" not in owner.__all__
    public = inspect.signature(owner.validate_mmi_h2c_prepared_case_v1)
    assert tuple(public.parameters) == ("prepared_case",)
    assert all(
        item.kind is inspect.Parameter.KEYWORD_ONLY
        for item in public.parameters.values()
    )
    private = inspect.signature(owner._build_mmi_h2c_prepared_case_v1)
    assert tuple(private.parameters) == (
        "evaluation_timestamp_utc",
        "strategy_settings_source_record",
        "portfolio_snapshot_source_record",
        "legacy_prompt_template_bytes",
        "grounded_prompt",
        "h1_prompt_bytes",
        "legacy_prompt_bytes",
    )
    assert all(
        item.kind is inspect.Parameter.KEYWORD_ONLY
        for item in private.parameters.values()
    )


def test_contract_has_no_response_presence_provider_or_authority_fields() -> None:
    schema_text = json.dumps(_schema(), sort_keys=True)
    prohibited = {
        "response_present",
        "result_present",
        "provider",
        "model",
        "availability",
        "permission",
        "gate_result",
        "publication",
        "order_readiness",
        "execution_authority",
    }
    assert not {item for item in prohibited if f'"{item}"' in schema_text}


def test_owner_has_no_forbidden_capability_or_workflow_imports() -> None:
    source = Path(owner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not {
        "os",
        "pathlib",
        "socket",
        "subprocess",
        "requests",
        "urllib",
        "http",
        "sched",
        "threading",
        "asyncio",
        "openai",
        "anthropic",
        "httpx",
        "selenium",
        "playwright",
    }.intersection(modules)
    assert {
        module
        for module in modules
        if module.startswith("investment_orchestrator.")
    } == {
        "investment_orchestrator.common.schema_validation",
        "investment_orchestrator.mmi.canonical",
    }
    assert "except Exception" not in source
    assert "rehydrat" not in source.lower()
    assert "capability" not in source.lower().replace(
        "no filesystem, live-source, clock, capability,", ""
    )


def test_owner_has_exactly_the_phase_a_consumer_and_no_package_export() -> None:
    owner_path = Path(owner.__file__).resolve()
    consumers: list[Path] = []
    for path in sorted(
        (repo_root() / "src/investment_orchestrator").rglob("*.py")
    ):
        if path.resolve() == owner_path:
            continue
        source = path.read_text(encoding="utf-8")
        if "mmi_h2c_prepared_case_v1" in source:
            consumers.append(path.relative_to(repo_root()))
    # D4b: the dormant envelope is built and validated by exactly one
    # production owner, the Phase A preparation engine.
    assert consumers == [
        Path(
            "src/investment_orchestrator/offline/"
            "mmi_h2c_prepare_persisted_case_v1.py"
        )
    ]
    assert mmi.__all__ == ()
    assert not hasattr(package, "__all__")
    assert IDENTITY_DOMAIN not in tuple(vars(canonical).values())
