from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from jsonschema import Draft202012Validator

import investment_orchestrator as package
import investment_orchestrator.mmi as mmi
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi import canonical
from investment_orchestrator.offline import (
    mmi_h2c_dual_side_manual_handoff_context_receipt_v1 as owner,
)


SCHEMA_NAME = (
    "mmi_h2c_dual_side_manual_handoff_context_receipt_v1.schema.json"
)
EXPECTED_FIELDS = {
    "schema_version",
    "artifact_kind",
    "capture_contract_version",
    "report_only",
    "authority_effect",
    "live_context_validated_at_capture",
    "operator_h1_response_bytes_bound_at_capture",
    "operator_legacy_response_bytes_bound_at_capture",
    "provider_origin_authentication",
    "evaluation_timestamp_utc",
    "strategy_settings_source_record_identity_sha256",
    "portfolio_snapshot_source_record_identity_sha256",
    "legacy_prompt_template_sha256",
    "legacy_prompt_sha256",
    "comparison_report_identity_sha256",
    "receipt_identity_sha256",
}
PORTABLE_ARGUMENTS = (
    "receipt",
    "comparison_report",
    "legacy_step1_compatibility_candidate",
    "validated_grounded_analysis_response",
    "raw_response_envelope",
    "grounded_prompt",
    "archived_h1_prompt_bytes",
    "archived_h1_response_bytes",
    "archived_legacy_response_bytes",
    "archived_strategy_settings_bytes",
    "strategy_settings_source_record",
    "archived_portfolio_snapshot_bytes",
    "portfolio_snapshot_source_record",
    "archived_legacy_prompt_template_bytes",
    "archived_legacy_prompt_bytes",
)
PRIVATE_HELPERS = {
    "_validate_portable_grounded_prompt_v2",
    "_validate_portable_raw_response_envelope_v2",
    "_validate_portable_validated_grounded_analysis_response_v2",
    "_validate_portable_legacy_step1_compatibility_candidate_v1",
    "_validate_portable_legacy_step1_comparison_report_v1",
    "_validate_portable_source_record_v1",
    "_validate_receipt_snapshot",
    "_validate_portable_artifact_links",
    "_validate_portable_legacy_prompt_reconstruction",
}


def _schema() -> dict[str, object]:
    value = json.loads(
        (repo_root() / "schemas" / SCHEMA_NAME).read_text(encoding="utf-8")
    )
    assert type(value) is dict
    return value


def _production_paths() -> tuple[Path, ...]:
    return tuple(
        sorted(
            (repo_root() / "src/investment_orchestrator").rglob("*.py")
        )
    )


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


def test_schema_is_closed_exact_and_constants_are_report_only() -> None:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == EXPECTED_FIELDS
    properties = schema["properties"]
    assert type(properties) is dict
    assert set(properties) == EXPECTED_FIELDS
    assert properties["schema_version"] == {
        "const": "mmi_h2c_dual_side_manual_handoff_context_receipt_v1"
    }
    assert properties["artifact_kind"] == {
        "const": "MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT"
    }
    assert properties["capture_contract_version"] == {
        "const": "mmi_h2c_manual_capture_v1"
    }
    assert properties["report_only"] == {"const": True}
    assert properties["authority_effect"] == {"const": "NONE"}
    assert properties["provider_origin_authentication"] == {
        "const": "NOT_ESTABLISHED"
    }
    prohibited = {
        "context_proven",
        "migration_ready",
        "comparison_accepted",
        "safe_to_replace",
        "availability",
        "permission",
        "gate_result",
        "publication_eligibility",
        "order_readiness",
        "execution_authority",
        "provider_authorship",
        "external_prompt_response_causality",
    }
    assert not EXPECTED_FIELDS.intersection(prohibited)


def test_public_api_is_exact_and_keyword_only() -> None:
    assert owner.__all__ == (
        "MmiH2cDualSideManualHandoffContextReceiptV1Error",
        "validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1",
        "validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence",
    )
    receipt_signature = inspect.signature(
        owner.validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1
    )
    assert tuple(receipt_signature.parameters) == ("receipt",)
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in receipt_signature.parameters.values()
    )
    portable_signature = inspect.signature(
        owner.validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence
    )
    assert tuple(portable_signature.parameters) == PORTABLE_ARGUMENTS
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in portable_signature.parameters.values()
    )


def test_portable_owner_has_only_narrow_structural_helpers() -> None:
    source_path = (
        repo_root()
        / "src/investment_orchestrator/offline/"
        "mmi_h2c_dual_side_manual_handoff_context_receipt_v1.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    assert PRIVATE_HELPERS <= functions
    imported_names = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not {
        "begin_mmi_projection_run",
        "capture_current_mmi_source",
        "MmiCapturedSource",
        "MmiProjectionRunContext",
        "build_mmi_policy_projection",
        "validate_mmi_policy_projection",
        "validate_mmi_legacy_step1_comparison_report_v1",
    }.intersection(imported_names)
    assert "inputs/current" not in source.replace(
        "``inputs/current``", ""
    ).replace("inputs/current/strategy_settings.yaml", "")
    assert "PORTABLE_STRUCTURAL_VALIDATION" in source
    assert "source authentication" not in source.lower()
    assert "except Exception" not in source


def test_identity_domain_and_inventory_increase_once() -> None:
    domains = _identity_domains()
    assert len(domains) == len(set(domains)) == 19
    assert domains.count(
        b"mmi_h2c_dual_side_manual_handoff_context_receipt_v1\0"
    ) == 1
    assert (
        canonical.MAX_MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_CANONICAL_BYTES
        == 1114
    )
    assert len(_production_paths()) == 145
    assert len(tuple((repo_root() / "schemas").glob("*.schema.json"))) == 43
    assert mmi.__all__ == ()
    assert not hasattr(package, "__all__")
