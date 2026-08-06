from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

import investment_orchestrator as package
import investment_orchestrator.mmi as mmi
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi import canonical
from investment_orchestrator.offline import (
    mmi_h2c_dual_side_persisted_case_receipt_v2 as owner,
)


SCHEMA_NAME = (
    "mmi_h2c_dual_side_persisted_case_receipt_v2.schema.json"
)
IDENTITY_FIELD = "receipt_identity_sha256"
IDENTITY_DOMAIN = b"mmi_h2c_dual_side_persisted_case_receipt_v2\0"
EXPECTED_FIELDS = {
    "schema_version",
    "artifact_kind",
    "consumption_contract_version",
    "report_only",
    "authority_effect",
    "evaluation_timestamp_utc",
    "prepared_case_identity_sha256",
    "case_evidence_bundle_identity_sha256",
    "comparison_report_identity_sha256",
    "strategy_settings_source_record_identity_sha256",
    "portfolio_snapshot_source_record_identity_sha256",
    "h1_prompt_sha256",
    "legacy_prompt_sha256",
    "h1_operator_supplied_response_sha256",
    "legacy_operator_supplied_response_sha256",
    IDENTITY_FIELD,
}
LINK_FIELDS = EXPECTED_FIELDS - {
    "schema_version",
    "artifact_kind",
    "consumption_contract_version",
    "report_only",
    "authority_effect",
    "evaluation_timestamp_utc",
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


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _inputs() -> dict[str, str]:
    return {
        "evaluation_timestamp_utc": "2026-08-04T01:15:34.942524Z",
        "prepared_case_identity_sha256": _sha("prepared"),
        "case_evidence_bundle_identity_sha256": _sha("bundle"),
        "comparison_report_identity_sha256": _sha("comparison"),
        "strategy_settings_source_record_identity_sha256": _sha("settings"),
        "portfolio_snapshot_source_record_identity_sha256": _sha("portfolio"),
        "h1_prompt_sha256": _sha("h1-prompt"),
        "legacy_prompt_sha256": _sha("legacy-prompt"),
        "h1_operator_supplied_response_sha256": _sha("h1-response"),
        "legacy_operator_supplied_response_sha256": _sha(
            "legacy-response"
        ),
    }


@pytest.fixture()
def receipt() -> dict[str, object]:
    return owner.build_mmi_h2c_dual_side_persisted_case_receipt_v2(
        **_inputs()
    )


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
        "const": "mmi_h2c_dual_side_persisted_case_receipt_v2"
    }
    assert properties["artifact_kind"] == {
        "const": "MMI_H2C_DUAL_SIDE_PERSISTED_CASE_RECEIPT"
    }
    assert properties["consumption_contract_version"] == {
        "const": "mmi_h2c_persisted_case_consume_v1"
    }
    assert properties["report_only"] == {"const": True}
    assert properties["authority_effect"] == {"const": "NONE"}
    assert schema["$defs"]["sha256"] == {
        "type": "string",
        "pattern": "^[0-9a-f]{64}$",
    }


def test_receipt_field_names_truthfully_bind_only_persisted_inputs() -> None:
    assert LINK_FIELDS == {
        "prepared_case_identity_sha256",
        "case_evidence_bundle_identity_sha256",
        "comparison_report_identity_sha256",
        "strategy_settings_source_record_identity_sha256",
        "portfolio_snapshot_source_record_identity_sha256",
        "h1_prompt_sha256",
        "legacy_prompt_sha256",
        "h1_operator_supplied_response_sha256",
        "legacy_operator_supplied_response_sha256",
    }
    prohibited = {
        "live_context_validated_at_capture",
        "responses_bound_at_capture",
        "provider_authentication",
        "model_authentication",
        "conversation_authentication",
        "transport_authentication",
        "execution_authority",
        "provider_origin_authentication",
    }
    assert not EXPECTED_FIELDS.intersection(prohibited)
    response_fields = {
        field for field in EXPECTED_FIELDS if "response_sha256" in field
    }
    assert response_fields == {
        "h1_operator_supplied_response_sha256",
        "legacy_operator_supplied_response_sha256",
    }


def test_builder_preserves_every_complete_expected_link() -> None:
    supplied = _inputs()
    receipt = owner.build_mmi_h2c_dual_side_persisted_case_receipt_v2(
        **supplied
    )
    assert set(receipt) == EXPECTED_FIELDS
    for key, value in supplied.items():
        assert receipt[key] == value
    assert receipt["schema_version"] == (
        "mmi_h2c_dual_side_persisted_case_receipt_v2"
    )
    assert receipt["artifact_kind"] == (
        "MMI_H2C_DUAL_SIDE_PERSISTED_CASE_RECEIPT"
    )
    assert receipt["consumption_contract_version"] == (
        "mmi_h2c_persisted_case_consume_v1"
    )
    assert receipt["report_only"] is True
    assert receipt["authority_effect"] == "NONE"
    assert owner.validate_mmi_h2c_dual_side_persisted_case_receipt_v2(
        receipt=receipt
    ) is None


def test_missing_and_extra_fields_are_rejected_with_matching_identity(
    receipt: dict[str, object],
) -> None:
    missing = deepcopy(receipt)
    missing.pop("h1_prompt_sha256")
    _reidentify(missing)
    extra = deepcopy(receipt)
    extra["unexpected"] = True
    _reidentify(extra)
    for value in (missing, extra):
        with pytest.raises(
            owner.MmiH2cDualSidePersistedCaseReceiptV2Error
        ):
            owner.validate_mmi_h2c_dual_side_persisted_case_receipt_v2(
                receipt=value
            )


@pytest.mark.parametrize("field", sorted(LINK_FIELDS))
def test_malformed_link_hashes_are_rejected_with_matching_identity(
    receipt: dict[str, object],
    field: str,
) -> None:
    mutated = deepcopy(receipt)
    mutated[field] = "A" * 64
    _reidentify(mutated)
    with pytest.raises(owner.MmiH2cDualSidePersistedCaseReceiptV2Error):
        owner.validate_mmi_h2c_dual_side_persisted_case_receipt_v2(
            receipt=mutated
        )


@pytest.mark.parametrize("field", sorted(EXPECTED_FIELDS - {IDENTITY_FIELD}))
def test_mutation_of_every_preimage_field_invalidates_identity(
    receipt: dict[str, object],
    field: str,
) -> None:
    mutated = deepcopy(receipt)
    mutated[field] = "mutated"
    with pytest.raises(owner.MmiH2cDualSidePersistedCaseReceiptV2Error):
        owner.validate_mmi_h2c_dual_side_persisted_case_receipt_v2(
            receipt=mutated
        )


def test_identity_framing_is_independent_and_excludes_only_self(
    receipt: dict[str, object],
) -> None:
    assert len(IDENTITY_DOMAIN) == 44
    assert receipt[IDENTITY_FIELD] == _identity(receipt)
    changed_self = deepcopy(receipt)
    changed_self[IDENTITY_FIELD] = "f" * 64
    assert _identity(changed_self) == _identity(receipt)
    with pytest.raises(owner.MmiH2cDualSidePersistedCaseReceiptV2Error):
        owner.validate_mmi_h2c_dual_side_persisted_case_receipt_v2(
            receipt=changed_self
        )


def test_exact_canonical_size_is_independently_derived_and_enforced(
    receipt: dict[str, object],
) -> None:
    zero = "0" * 64
    independently_constructed = {
        "schema_version": (
            "mmi_h2c_dual_side_persisted_case_receipt_v2"
        ),
        "artifact_kind": "MMI_H2C_DUAL_SIDE_PERSISTED_CASE_RECEIPT",
        "consumption_contract_version": (
            "mmi_h2c_persisted_case_consume_v1"
        ),
        "report_only": True,
        "authority_effect": "NONE",
        "evaluation_timestamp_utc": "2026-08-04T01:15:34.942524Z",
        "prepared_case_identity_sha256": zero,
        "case_evidence_bundle_identity_sha256": zero,
        "comparison_report_identity_sha256": zero,
        "strategy_settings_source_record_identity_sha256": zero,
        "portfolio_snapshot_source_record_identity_sha256": zero,
        "h1_prompt_sha256": zero,
        "legacy_prompt_sha256": zero,
        "h1_operator_supplied_response_sha256": zero,
        "legacy_operator_supplied_response_sha256": zero,
        IDENTITY_FIELD: zero,
    }
    assert len(_canonical_bytes(independently_constructed)) == 1_320
    assert len(_canonical_bytes(receipt)) == 1_320
    assert owner._EXACT_CANONICAL_BYTES == 1_320
    shorter = deepcopy(receipt)
    shorter["evaluation_timestamp_utc"] = "2026-08-04T01:15:34.94252Z"
    _reidentify(shorter)
    with pytest.raises(owner.MmiH2cDualSidePersistedCaseReceiptV2Error):
        owner.validate_mmi_h2c_dual_side_persisted_case_receipt_v2(
            receipt=shorter
        )


def test_public_api_is_minimal_and_builder_is_public_keyword_only() -> None:
    assert owner.__all__ == (
        "MmiH2cDualSidePersistedCaseReceiptV2Error",
        "build_mmi_h2c_dual_side_persisted_case_receipt_v2",
        "validate_mmi_h2c_dual_side_persisted_case_receipt_v2",
    )
    public = inspect.signature(
        owner.validate_mmi_h2c_dual_side_persisted_case_receipt_v2
    )
    assert tuple(public.parameters) == ("receipt",)
    assert all(
        item.kind is inspect.Parameter.KEYWORD_ONLY
        for item in public.parameters.values()
    )
    private = inspect.signature(
        owner.build_mmi_h2c_dual_side_persisted_case_receipt_v2
    )
    assert tuple(private.parameters) == tuple(_inputs())
    assert all(
        item.kind is inspect.Parameter.KEYWORD_ONLY
        for item in private.parameters.values()
    )


def test_error_surface_is_stable_and_nonleaking(
    receipt: dict[str, object],
) -> None:
    code = "MMI_H2C_PERSISTED_CASE_RECEIPT_V2_INVALID"
    error = owner.MmiH2cDualSidePersistedCaseReceiptV2Error(code)
    assert error.code == code
    assert error.args == (code,)
    assert str(error) == code
    with pytest.raises(TypeError):
        owner.MmiH2cDualSidePersistedCaseReceiptV2Error("OTHER")
    secret = "SENTINEL-RESPONSE-CONTENT"
    mutated = deepcopy(receipt)
    mutated["unexpected"] = secret
    with pytest.raises(
        owner.MmiH2cDualSidePersistedCaseReceiptV2Error
    ) as captured:
        owner.validate_mmi_h2c_dual_side_persisted_case_receipt_v2(
            receipt=mutated
        )
    assert secret not in str(captured.value)


def test_owner_has_no_false_live_provider_or_workflow_capability() -> None:
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
    assert "live_context_validated_at_capture" not in source
    assert "operator_h1_response_bytes_bound_at_capture" not in source
    assert "operator_legacy_response_bytes_bound_at_capture" not in source


def test_owner_is_dormant_and_package_exports_remain_empty() -> None:
    owner_path = Path(owner.__file__).resolve()
    consumers: list[Path] = []
    for path in sorted(
        (repo_root() / "src/investment_orchestrator").rglob("*.py")
    ):
        if path.resolve() == owner_path:
            continue
        source = path.read_text(encoding="utf-8")
        if "mmi_h2c_dual_side_persisted_case_receipt_v2" in source:
            consumers.append(path.relative_to(repo_root()))
    assert consumers == [
        Path(
            "src/investment_orchestrator/offline/"
            "mmi_h2c_consume_persisted_case_v1.py"
        ),
    ]
    assert mmi.__all__ == ()
    assert not hasattr(package, "__all__")
    assert IDENTITY_DOMAIN not in tuple(vars(canonical).values())
