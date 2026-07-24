"""WS01c deterministic untrusted-response validation tests."""

from __future__ import annotations

import ast
import base64
import builtins
from collections.abc import Mapping
import copy
from dataclasses import FrozenInstanceError, asdict, dataclass, fields
from datetime import date
import hashlib
import inspect
import json
import os
from pathlib import Path
import pickle
import socket
import subprocess
import sys
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator
import pytest
import yaml

from investment_orchestrator.observability import (
    ltetf_target_architecture_gap_report as gap,
)
from investment_orchestrator.observability import weekly_shadow_01_contracts as contracts
from investment_orchestrator.observability import weekly_shadow_01_package_builder as builder
from investment_orchestrator.observability import weekly_shadow_01_response_validator as validator
from investment_orchestrator.observability import weekly_shadow_01_source_adapter as adapter
from investment_orchestrator.research import replacement_observation as r2f


def _write(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if type(value) is bytes:
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")


def _anchor(index: int) -> dict[str, Any]:
    return {
        "anchor_id": f"ANCHOR_{index:02d}",
        "anchor_type": "structural_theme",
        "applicable_tickers": ["FIX00"],
        "anchor_date_et": "2026-07-01",
        "valid_from": "2026-07-01",
        "valid_until": "2026-12-31",
        "source_type": "operator",
        "confidence_floor": "medium",
        "summary": f"Evidence summary {index}",
    }


def _setup_repo(root: Path) -> None:
    source_root = Path(__file__).parents[2]
    _write(
        root / "inputs/current/strategy_settings.yaml",
        """as_of: "2026-07-12"
benchmark: "FIX00"
core_universe: [FIX00]
satellite_universe: [FIX01]
user_approved_extended_etf_static_list: [FIX02]
hard_cap_open_orders_budget: 100
target_new_buy_budget_this_run: 10
max_new_tickers_per_week: 0
ticker_role_fallback:
  FIX00: benchmark_carrier_core
  FIX01: sector_alpha_tilt
  FIX02: extended_etf_minority_sleeve
""",
    )
    _write(root / "inputs/current/portfolio_snapshot.txt", "fixture portfolio\n")
    _write(
        root / "inputs/current/research_anchors.yaml",
        yaml.safe_dump(
            {
                "schema_version": "research_anchors_v1",
                "as_of_date": "2026-07-12",
                "is_llm_generated": False,
                "anchors": [_anchor(index) for index in range(16)],
            },
            sort_keys=False,
        ),
    )
    _write(
        root / "inputs/current/research_anchor_approvals.yaml",
        yaml.safe_dump(
            {
                "schema_version": "research_anchor_approvals_v1",
                "is_llm_generated": False,
                "as_of_date": "2026-07-12",
                "approvals": [],
            },
            sort_keys=False,
        ),
    )
    _write(
        root / "prompts/r2f_analyst_memo_content_v2.txt",
        (source_root / "prompts/r2f_analyst_memo_content_v2.txt").read_bytes(),
    )
    for relative in contracts.SCHEMA_FILENAME_BY_VERSION.values():
        _write(root / relative, (source_root / relative).read_bytes())
    contract_path = (
        "src/investment_orchestrator/observability/weekly_shadow_01_contracts.py"
    )
    _write(root / contract_path, (source_root / contract_path).read_bytes())


@dataclass(frozen=True)
class _ResponseContext:
    root: Path
    generation_id: str
    package: object
    response: dict[str, Any]
    raw_response: bytes


@pytest.fixture(scope="module")
def response_context(tmp_path_factory: pytest.TempPathFactory) -> _ResponseContext:
    root = tmp_path_factory.mktemp("ws01c-repo")
    _setup_repo(root)
    patch = pytest.MonkeyPatch()
    patch.setattr(r2f, "repo_root", lambda: root)
    patch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    try:
        generation = r2f.replacement_render()
    finally:
        patch.undo()
    generation_id = generation["generation_id"]
    package_result = builder.build_analyst_input_package(
        generation_id,
        repository_root=root,
    )
    assert package_result.ok is True
    package = package_result.value
    payload = package.to_dict()
    evidence_bindings = [
        {
            "evidence_record_id": record["evidence_record_id"],
            "evidence_record_identity_sha256": record[
                "evidence_record_identity_sha256"
            ],
        }
        for record in payload["evidence_records"]
    ]
    response = {
        "schema_version": "weekly_shadow_01_analyst_response_v2",
        "stage_version": "weekly_shadow_01_stage_a_v1",
        "run_id": payload["run_id"],
        "input_package_identity_sha256": payload[
            "input_package_identity_sha256"
        ],
        "prompt_template_identity_sha256": payload[
            "prompt_template_identity_sha256"
        ],
        "source_generation_id": payload["source_generation_id"],
        "source_artifact_bindings": copy.deepcopy(
            payload["source_artifact_bindings"]
        ),
        "evidence_record_bindings": evidence_bindings,
        "analyst_conclusion": "OBSERVATIONS_AVAILABLE",
        "analyst_confidence": "MEDIUM",
        "analytical_sections": {
            "observations": [
                {
                    "entry_id": "observation-01",
                    "statement": "The supplied evidence supports a bounded observation.",
                    "evidence_record_ids": [
                        payload["evidence_records"][0]["evidence_record_id"]
                    ],
                }
            ],
            "risks_and_uncertainties": [],
            "missing_evidence_notes": [],
        },
        "analyst_limitation_codes": [],
        "negative_authority": copy.deepcopy(payload["negative_authority"]),
    }
    raw = contracts.canonical_json_bytes(response)
    return _ResponseContext(root, generation_id, package, response, raw)


def _call(
    context: _ResponseContext,
    raw: bytes | object | None = None,
) -> object:
    return validator.validate_analyst_response(
        context.generation_id,
        raw_response_bytes=context.raw_response if raw is None else raw,
        repository_root=context.root,
    )


def _call_downstream(context: _ResponseContext) -> object:
    return validator._validate_analyst_response_for_downstream(
        context.generation_id,
        raw_response_bytes=context.raw_response,
        repository_root=context.root,
    )


def _expect_downstream_success(result: object) -> object:
    assert type(result) is validator._WS01cDownstreamResult
    assert result.ok is True
    assert result.reason_code is None
    assert type(result.value) is validator._WS01cDownstreamContext
    return result.value


def _expect_downstream_failure(
    result: object,
    reason_code: str,
    *secrets: object,
) -> None:
    assert type(result) is validator._WS01cDownstreamResult
    assert result.ok is False
    assert result.value is None
    assert result.reason_code == reason_code
    assert not hasattr(result, "__dict__")
    assert type(result).__slots__ == ("ok", "value", "reason_code")
    _assert_no_reachable_exception(result)
    rendered = repr(result)
    for secret in secrets:
        assert str(secret) not in rendered


def _raw(response: dict[str, Any]) -> bytes:
    return json.dumps(
        response,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _expect_success(result: object) -> object:
    assert type(result) is validator._WS01cResult
    assert result.ok is True
    assert result.reason_code is None
    assert type(result.value) is validator._ValidatedAnalystResponse
    return result.value


def _expect_failure(
    result: object,
    reason_code: str,
    *secrets: object,
) -> None:
    assert type(result) is validator._WS01cResult
    assert result.ok is False
    assert result.value is None
    assert result.reason_code == reason_code
    assert not hasattr(result, "__dict__")
    assert not isinstance(result, BaseException)
    assert type(result).__slots__ == ("ok", "value", "reason_code")
    rendered = repr(result)
    for secret in secrets:
        assert str(secret) not in rendered
    with pytest.raises((AttributeError, TypeError)):
        result.value = object()


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(member) for key, member in value.items()}
    if type(value) is tuple:
        return [_thaw(member) for member in value]
    return value


def _changed_response(
    context: _ResponseContext,
    mutation,
) -> bytes:
    response = copy.deepcopy(context.response)
    mutation(response)
    return _raw(response)


def _assert_no_reachable_exception(value: object) -> None:
    pending = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        assert not isinstance(current, BaseException)
        assert type(current).__name__ not in {"traceback", "frame", "code"}
        if type(current) in {tuple, list, frozenset}:
            pending.extend(current)
        elif isinstance(current, Mapping):
            pending.extend(current.values())
        else:
            for slot in getattr(type(current), "__slots__", ()):
                if hasattr(current, slot):
                    pending.append(getattr(current, slot))


def _assert_recursively_immutable(value: object) -> None:
    pending = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, Mapping):
            assert isinstance(current, MappingProxyType)
            pending.extend(current.keys())
            pending.extend(current.values())
        elif type(current) in {tuple, frozenset}:
            pending.extend(current)
        elif type(current) in {
            validator._ValidatedAnalystResponse,
            validator._AuthenticatedArtifactContract,
            validator._WS01cDownstreamContext,
        }:
            assert not hasattr(current, "__dict__")
            pending.extend(getattr(current, field.name) for field in fields(current))
        else:
            assert type(current) in {str, int, bool, bytes, type(None)}


def _reachable_context_state(value: object) -> tuple[object, ...]:
    """Traverse every owned value without following class/module referents."""
    pending = [value]
    seen: set[int] = set()
    reachable: list[object] = []
    record_types = {
        validator._AuthenticatedArtifactContract,
        validator._WS01cDownstreamContext,
    }
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        reachable.append(current)
        if isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif type(current) in {tuple, frozenset, list, set}:
            pending.extend(current)
        elif type(current) in record_types:
            pending.extend(getattr(current, field.name) for field in fields(current))
        else:
            assert type(current) in {
                str,
                int,
                bool,
                bytes,
                bytearray,
                memoryview,
                type(None),
            }
    return tuple(reachable)


def _ordered_tuple_projection(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(
            (key, _ordered_tuple_projection(member))
            for key, member in value.items()
        )
    if type(value) in {list, tuple}:
        return tuple(_ordered_tuple_projection(member) for member in value)
    return value


def _marked_response(
    context: _ResponseContext,
) -> tuple[dict[str, Any], bytes]:
    response = copy.deepcopy(context.response)
    response["analytical_sections"]["observations"][0]["entry_id"] = (
        "selected-retention-marker-77a9"
    )
    response["analytical_sections"]["observations"][0]["statement"] = (
        "PERMITTED-SELECTED-ANALYST-CONTENT-77a9 remains byte exact."
    )
    raw = (
        json.dumps(
            response,
            ensure_ascii=True,
            allow_nan=False,
            indent=3,
        )
        + "\n"
    ).encode("utf-8")
    return response, raw


def _independent_capture_and_validation(
    context: _ResponseContext,
    raw: bytes,
) -> tuple[dict[str, object], bytes, dict[str, object], bytes]:
    package = context.package.to_dict()
    negative_authority = {
        "authority_effect": "none",
        "permission_effect": "none",
        "approval_eligible": False,
        "precompile_eligible": False,
        "order_eligible": False,
        "portfolio_effect": "none",
        "order_path_effect": "none",
        "execution_authority": False,
    }
    capture: dict[str, object] = {
        "schema_version": "weekly_shadow_01_response_capture_v2",
        "run_id": package["run_id"],
        "input_package_identity_sha256": package[
            "input_package_identity_sha256"
        ],
        "source_generation_id": package["source_generation_id"],
        "raw_response_base64": base64.b64encode(raw).decode("ascii"),
        "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_response_byte_size": len(raw),
        "negative_authority_profile": negative_authority,
    }
    capture["response_capture_identity_sha256"] = contracts.compute_identity(
        "response_capture",
        capture,
        exclude_fields=("response_capture_identity_sha256",),
    )
    validation: dict[str, object] = {
        "schema_version": "weekly_shadow_01_response_validation_v1",
        "run_id": package["run_id"],
        "input_package_identity_sha256": package[
            "input_package_identity_sha256"
        ],
        "response_capture_identity_sha256": capture[
            "response_capture_identity_sha256"
        ],
        "validation_status": "VALID",
        "blocking_reason_codes": [],
        "validator_diagnostics": [],
        "report_payload_constructible": True,
        "negative_authority_profile": negative_authority,
    }
    validation["validation_identity_sha256"] = contracts.compute_identity(
        "validation",
        validation,
        exclude_fields=("validation_identity_sha256",),
    )
    return (
        capture,
        contracts.canonical_json_bytes(capture),
        validation,
        contracts.canonical_json_bytes(validation),
    )


def test_public_namespace_and_signature_are_exact() -> None:
    assert set(validator.__all__) == {"validate_analyst_response"}
    assert {name for name in dir(validator) if not name.startswith("_")} == set(
        validator.__all__
    )
    signature = inspect.signature(validator.validate_analyst_response)
    assert tuple(signature.parameters) == (
        "generation_id",
        "raw_response_bytes",
        "repository_root",
    )
    assert signature.parameters["generation_id"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert signature.parameters["raw_response_bytes"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["repository_root"].kind is inspect.Parameter.KEYWORD_ONLY


def test_private_downstream_entry_point_signature_is_narrow_and_unexported() -> None:
    assert (
        validator._validate_analyst_response_for_downstream.__name__.startswith(
            "_"
        )
    )
    assert (
        "_validate_analyst_response_for_downstream"
        not in validator.__all__
    )
    signature = inspect.signature(
        validator._validate_analyst_response_for_downstream
    )
    assert tuple(signature.parameters) == (
        "generation_id",
        "raw_response_bytes",
        "repository_root",
    )
    assert (
        signature.parameters["generation_id"].kind
        is inspect.Parameter.POSITIONAL_OR_KEYWORD
    )
    assert (
        signature.parameters["raw_response_bytes"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    assert (
        signature.parameters["repository_root"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )


def test_private_downstream_entry_point_performs_no_root_normalization() -> None:
    source = inspect.getsource(
        validator._validate_analyst_response_for_downstream
    )
    source += inspect.getsource(validator._require_normalized_repository_root)
    tree = ast.parse(source)
    forbidden_names = {
        "_Path",
        "fspath",
        "resolve",
        "expanduser",
        "absolute",
    }
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    calls.update(
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    )
    assert calls.isdisjoint(forbidden_names)


def test_public_projection_is_exactly_the_existing_validated_value(
    response_context: _ResponseContext,
) -> None:
    context = _expect_downstream_success(_call_downstream(response_context))
    public_value = _expect_success(_call(response_context))
    assert tuple(field.name for field in fields(public_value)) == (
        "analyst_response",
        "response_capture",
        "response_capture_canonical_bytes",
        "response_capture_identity_sha256",
        "response_validation",
        "response_validation_canonical_bytes",
        "response_validation_identity_sha256",
    )
    response = _thaw(public_value.analyst_response)
    assert context.run_id == response["run_id"]
    assert context.input_package_identity_sha256 == response[
        "input_package_identity_sha256"
    ]
    assert (
        context.response_capture_identity_sha256
        == public_value.response_capture_identity_sha256
    )
    assert (
        context.validation_identity_sha256
        == public_value.response_validation_identity_sha256
    )
    assert _thaw(context.validated_analyst_content) == {
        field: response[field]
        for field in (
            "analyst_conclusion",
            "analyst_confidence",
            "analytical_sections",
            "analyst_limitation_codes",
        )
    }
    assert not any(
        type(value) is validator._ValidatedAnalystResponse
        for value in _reachable_context_state(context)
    )


def test_private_downstream_rejects_non_code_owned_or_unnormalized_roots(
    response_context: _ResponseContext,
) -> None:
    class StatefulPathLike:
        def __init__(self) -> None:
            self.calls = 0

        def __fspath__(self) -> str:
            self.calls += 1
            return str(response_context.root)

    class PathSubclass(type(response_context.root)):
        pass

    selector = StatefulPathLike()
    values = (
        str(response_context.root),
        selector,
        Path("relative-root"),
        PathSubclass(response_context.root),
    )
    for value in values:
        result = validator._validate_analyst_response_for_downstream(
            response_context.generation_id,
            raw_response_bytes=response_context.raw_response,
            repository_root=value,
        )
        _expect_downstream_failure(
            result,
            "WS01_BR_SOURCE_GENERATION_INVALID",
            response_context.root,
        )
    assert selector.calls == 0


def test_private_downstream_uses_exact_supplied_root_object(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[object] = []
    original_build = validator._BUILD_PACKAGE_FROM_SOURCE_SELECTION
    original_authenticate = validator._authenticate_response_contracts
    original_read = validator._read_response_schemas_stably

    def forbidden_normalization(_value: object) -> object:
        raise AssertionError("private pipeline attempted root normalization")

    def build(
        generation_id: str,
        *,
        repository_root: Path,
    ) -> object:
        observed.append(repository_root)
        return original_build(
            generation_id,
            repository_root=repository_root,
        )

    def authenticate(package: object, *, root: Path) -> object:
        observed.append(root)
        return original_authenticate(package, root=root)

    def read(root: Path, *, maximum_bytes: int) -> object:
        observed.append(root)
        value = original_read(root, maximum_bytes=maximum_bytes)
        raw_schemas, contract_source = value
        assert set(raw_schemas) == {
            "weekly_shadow_01_analyst_response_v2",
            "weekly_shadow_01_response_capture_v2",
            "weekly_shadow_01_response_validation_v1",
            "weekly_shadow_01_analyst_report_v1",
            "weekly_shadow_01_run_summary_v1",
        }
        assert hashlib.sha256(contract_source).hexdigest() == (
            "cc6659754275991a5d244aec8f26f725dc74d339be766cdf7694e97e6f19792a"
        )
        return value

    monkeypatch.setattr(validator, "_repository_root", forbidden_normalization)
    monkeypatch.setattr(validator, "_BUILD_PACKAGE_FROM_SOURCE_SELECTION", build)
    monkeypatch.setattr(
        validator,
        "_authenticate_response_contracts",
        authenticate,
    )
    monkeypatch.setattr(validator, "_read_response_schemas_stably", read)
    _expect_downstream_success(_call_downstream(response_context))
    assert len(observed) == 3
    assert all(root is response_context.root for root in observed)


def test_static_import_is_one_exact_builder_module_binding() -> None:
    source = Path(validator.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    observer_imports = [
        node
        for node in imports
        if isinstance(node, ast.ImportFrom)
        and node.module == "investment_orchestrator.observability"
    ]
    assert len(observer_imports) == 1
    assert [(item.name, item.asname) for item in observer_imports[0].names] == [
        ("weekly_shadow_01_package_builder", "_package_builder")
    ]
    assert "weekly_shadow_01_source_adapter" not in source
    assert "importlib" not in source
    assert "sys.modules" not in source


def test_real_ltetf_inventory_and_absent_publisher_remain_exact() -> None:
    root = Path(__file__).parents[2]
    publisher_paths = (
        root
        / "src/investment_orchestrator/observability"
        / "weekly_shadow_01_report_publisher.py",
        root / "tests/unit/test_weekly_shadow_01_report_publisher.py",
    )
    assert not any(path.exists() for path in publisher_paths)
    inventory = gap._scan_production_inventory(root)
    assert inventory.observer_external_consumers == (
        "src/investment_orchestrator/cli/"
        "observe_ltetf_target_architecture_gaps.py",
    )
    assert inventory.dynamic_findings == ()
    assert inventory.report_artifact_readers == ()
    assert inventory.policy_artifact_consumers == ()
    assert inventory.prohibited_observer_capability_imports == ()
    assert inventory.p4a_runtime_consumers == ()
    assert inventory.broker_capability_imports == ()
    assert inventory.weekly_llm_invocation_markers == ()


def test_valid_response_constructs_exact_immutable_capture_and_validation(
    response_context: _ResponseContext,
) -> None:
    value = _expect_success(_call(response_context))
    response = _thaw(value.analyst_response)
    capture = _thaw(value.response_capture)
    validation = _thaw(value.response_validation)
    assert response == response_context.response
    assert base64.b64decode(capture["raw_response_base64"], validate=True) == (
        response_context.raw_response
    )
    assert capture["raw_response_sha256"] == hashlib.sha256(
        response_context.raw_response
    ).hexdigest()
    assert capture["raw_response_byte_size"] == len(response_context.raw_response)
    assert capture["response_capture_identity_sha256"] == contracts.compute_identity(
        "response_capture",
        capture,
        exclude_fields=("response_capture_identity_sha256",),
    )
    assert validation["validation_status"] == "VALID"
    assert validation["blocking_reason_codes"] == []
    assert validation["validator_diagnostics"] == []
    assert validation["report_payload_constructible"] is True
    assert validation["validation_identity_sha256"] == contracts.compute_identity(
        "validation",
        validation,
        exclude_fields=("validation_identity_sha256",),
    )
    assert value.response_capture_canonical_bytes == contracts.canonical_json_bytes(
        capture
    )
    assert value.response_validation_canonical_bytes == contracts.canonical_json_bytes(
        validation
    )
    for schema_version, payload in (
        ("weekly_shadow_01_response_capture_v2", capture),
        ("weekly_shadow_01_response_validation_v1", validation),
    ):
        schema_path = response_context.root / contracts.SCHEMA_FILENAME_BY_VERSION[
            schema_version
        ]
        Draft202012Validator(json.loads(schema_path.read_text())).validate(payload)
    with pytest.raises((AttributeError, TypeError)):
        value.response_capture["run_id"] = "changed"


def test_private_context_contains_only_the_frozen_downstream_contract_minimum(
    response_context: _ResponseContext,
) -> None:
    context = _expect_downstream_success(_call_downstream(response_context))
    field_destinations = {
        "run_id": (
            "analyst-report.run_id and run-summary.run_id",
            "one shared run binding is already the narrowest value",
        ),
        "input_package_identity_sha256": (
            "analyst-report.input_package_identity_sha256",
            "the report requires the authenticated package binding scalar",
        ),
        "response_capture_identity_sha256": (
            "analyst-report.response_capture_identity_sha256",
            "the report requires the capture identity but not its envelope",
        ),
        "validation_identity_sha256": (
            "analyst-report.validation_identity_sha256",
            "the report requires the validation identity but not its envelope",
        ),
        "validated_analyst_content": (
            "analyst-report.validated_analyst_content",
            "all four selected child fields are required by that exact object",
        ),
        "analyst_report_contract": (
            "analyst-report schema/semantic/domain/maximum validation",
            "the future constructor must validate and identify the report",
        ),
        "run_summary_contract": (
            "run-summary schema/semantic/domain/maximum validation",
            "the future constructor must validate and identify the summary",
        ),
        "negative_authority_profile": (
            "both output schemas.negative_authority_profile",
            "both artifacts require the exact frozen profile value",
        ),
        "negative_authority_profile_identity_sha256": (
            "both semantic-contracts.required_profile_identities_sha256",
            "the profile must remain bound to both authenticated semantics",
        ),
    }
    assert tuple(field.name for field in fields(context)) == tuple(
        field_destinations
    )
    assert all(destination and reason for destination, reason in field_destinations.values())
    assert tuple(field.name for field in fields(context.analyst_report_contract)) == (
        "schema_version",
        "schema",
        "schema_identity_sha256",
        "semantic_contract",
        "semantic_contract_identity_sha256",
        "identity_domain",
        "maximum_canonical_bytes",
    )
    assert not hasattr(context, "__dict__")
    assert not hasattr(context, "to_dict")
    assert not hasattr(context, "write")
    assert not hasattr(context, "publish")
    assert set(type(context).__slots__) == {
        field.name for field in fields(context)
    }


def test_report_and_run_summary_contracts_match_independent_frozen_oracles(
    response_context: _ResponseContext,
) -> None:
    context = _expect_downstream_success(_call_downstream(response_context))
    rows = (
        (
            context.analyst_report_contract,
            "weekly_shadow_01_analyst_report_v1",
            "schemas/weekly_shadow_01_analyst_report.schema.json",
            "1791f934d59607a70df55c80df31d6cbc2e897c86879ab5bf6e24772167a3c53",
            "7b415fa8eb7cb4ecce92ddf06eb394574f7d1435dd840657396dd2eeb0f4feb8",
            "195112bf9087b1f63f680c93a77d41487e4bceae4564a621c55c15b6cb684014",
            b"weekly_shadow_01_report_v1\0",
            262_144,
        ),
        (
            context.run_summary_contract,
            "weekly_shadow_01_run_summary_v1",
            "schemas/weekly_shadow_01_run_summary.schema.json",
            "35fca249f89ecc5294f57daf3577d53158042c2b239163f8794e5d1ba15502b9",
            "114e92f0d151bba7266a651172cd7dac01f9652a4c6fe47557582b10dcf706a7",
            "88bc37d815c348fa0791c51fbdc660f2527c2d9975a01ab2bde2b9853c2a99b3",
            b"weekly_shadow_01_run_summary_v1\0",
            65_536,
        ),
    )
    for (
        artifact_contract,
        schema_version,
        schema_path,
        raw_identity,
        schema_identity,
        semantic_identity,
        domain,
        maximum,
    ) in rows:
        assert artifact_contract.schema_version == schema_version
        assert hashlib.sha256(
            (response_context.root / schema_path).read_bytes()
        ).hexdigest() == raw_identity
        assert artifact_contract.schema_identity_sha256 == schema_identity
        assert (
            artifact_contract.semantic_contract_identity_sha256
            == semantic_identity
        )
        assert artifact_contract.identity_domain == domain
        assert artifact_contract.maximum_canonical_bytes == maximum
        schema = _thaw(artifact_contract.schema)
        semantic_contract = _thaw(artifact_contract.semantic_contract)
        assert type(schema) is dict
        assert type(semantic_contract) is dict
        assert schema == json.loads(
            (response_context.root / schema_path).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        assert schema["properties"]["schema_version"]["const"] == schema_version
        assert semantic_contract["schema_identity_sha256"] == schema_identity
        assert contracts.compute_identity(
            "semantic_contract_identity",
            semantic_contract,
        ) == semantic_identity


def test_context_carries_only_required_authenticated_output_identities(
    response_context: _ResponseContext,
) -> None:
    context = _expect_downstream_success(_call_downstream(response_context))
    assert context.analyst_report_contract.schema_identity_sha256 == (
        "7b415fa8eb7cb4ecce92ddf06eb394574f7d1435dd840657396dd2eeb0f4feb8"
    )
    assert context.run_summary_contract.schema_identity_sha256 == (
        "114e92f0d151bba7266a651172cd7dac01f9652a4c6fe47557582b10dcf706a7"
    )
    assert (
        context.analyst_report_contract.semantic_contract_identity_sha256
        == "195112bf9087b1f63f680c93a77d41487e4bceae4564a621c55c15b6cb684014"
    )
    assert (
        context.run_summary_contract.semantic_contract_identity_sha256
        == "88bc37d815c348fa0791c51fbdc660f2527c2d9975a01ab2bde2b9853c2a99b3"
    )
    assert context.negative_authority_profile_identity_sha256 == (
        "b20ea7218880c5799897d7d3fbd74515af88ad6fcc9e2f4c1d4cc83649e61ff1"
    )
    for artifact_contract in (
        context.analyst_report_contract,
        context.run_summary_contract,
    ):
        assert (
            context.negative_authority_profile_identity_sha256
            in artifact_contract.semantic_contract[
                "required_profile_identities_sha256"
            ]
        )
    assert dict(context.negative_authority_profile) == {
        "authority_effect": "none",
        "permission_effect": "none",
        "approval_eligible": False,
        "precompile_eligible": False,
        "order_eligible": False,
        "portfolio_effect": "none",
        "order_path_effect": "none",
        "execution_authority": False,
    }
    assert not any(
        hasattr(context, field_name)
        for field_name in (
            "resource_bound_profile_identity_sha256",
            "contract_catalog_identity_sha256",
            "contract_surface_schema_identities",
            "contract_surface_semantic_identities",
            "contract_surface_seal_sha256",
        )
    )


def test_private_context_is_frozen_slot_based_and_recursively_immutable(
    response_context: _ResponseContext,
) -> None:
    context = _expect_downstream_success(_call_downstream(response_context))
    _assert_recursively_immutable(context)
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        context.run_id = "changed-run"
    with pytest.raises(TypeError):
        context.negative_authority_profile["authority_effect"] = "changed"
    with pytest.raises(TypeError):
        context.analyst_report_contract.schema["title"] = "changed"
    with pytest.raises(TypeError):
        context.analyst_report_contract.semantic_contract["owner"] = "changed"
    with pytest.raises((TypeError, pickle.PicklingError)):
        pickle.dumps(context)
    with pytest.raises(TypeError):
        context.__reduce__()
    with pytest.raises(TypeError):
        dict(context)
    with pytest.raises(TypeError):
        asdict(context)
    with pytest.raises(TypeError):
        json.dumps(context)


def test_private_context_has_no_path_raw_input_or_publication_capability(
    response_context: _ResponseContext,
) -> None:
    response, raw = _marked_response(response_context)
    capture, capture_canonical, validation, validation_canonical = (
        _independent_capture_and_validation(response_context, raw)
    )
    public_value = _expect_success(_call(response_context, raw))
    assert _thaw(public_value.analyst_response) == response
    assert _thaw(public_value.response_capture) == capture
    assert public_value.response_capture_canonical_bytes == capture_canonical
    assert (
        base64.b64decode(
            public_value.response_capture["raw_response_base64"],
            validate=True,
        )
        == raw
    )
    assert _thaw(public_value.response_validation) == validation
    assert public_value.response_validation_canonical_bytes == validation_canonical

    context = _expect_downstream_success(
        validator._validate_analyst_response_for_downstream(
            response_context.generation_id,
            raw_response_bytes=raw,
            repository_root=response_context.root,
        )
    )
    reachable = _reachable_context_state(context)
    direct_values = {
        field.name: getattr(context, field.name) for field in fields(context)
    }
    forbidden_name_fragments = (
        "path",
        "root",
        "raw",
        "prompt",
        "writer",
        "publisher",
        "callback",
        "output",
        "destination",
        "exception",
        "traceback",
        "frame",
        "descriptor",
        "handle",
    )
    assert all(
        fragment not in field_name
        for field_name in direct_values
        for fragment in forbidden_name_fragments
    )
    assert not any(isinstance(value, Path) for value in reachable)
    assert not any(callable(value) for value in reachable)
    assert not any(
        type(value) is validator._ValidatedAnalystResponse
        for value in reachable
    )
    assert not any(
        isinstance(value, Mapping)
        and (
            "raw_response_base64" in value
            or _thaw(value) in (response, capture, validation)
        )
        for value in reachable
    )

    canonical_response = contracts.canonical_json_bytes(response)
    encoded_raw = base64.b64encode(raw)
    exact_forbidden_bytes = {
        raw,
        canonical_response,
        capture_canonical,
        validation_canonical,
    }
    exact_forbidden_text = {
        raw.decode("utf-8"),
        canonical_response.decode("utf-8"),
        capture_canonical.decode("utf-8"),
        validation_canonical.decode("utf-8"),
        encoded_raw.decode("ascii"),
        raw.hex(),
        canonical_response.hex(),
    }
    for value in reachable:
        if type(value) is bytes:
            assert value not in exact_forbidden_bytes
            assert encoded_raw not in value
            assert raw not in value
        elif type(value) in {bytearray, memoryview}:
            detached = bytes(value)
            assert detached not in exact_forbidden_bytes
            assert encoded_raw not in detached
            assert raw not in detached
        elif type(value) is str:
            assert value not in exact_forbidden_text
            assert encoded_raw.decode("ascii") not in value
            assert raw.hex() not in value
    assert str(response_context.root) not in {
        value for value in reachable if type(value) is str
    }


def test_private_context_excludes_complete_response_envelope_representations(
    response_context: _ResponseContext,
) -> None:
    response, raw = _marked_response(response_context)
    capture, capture_canonical, validation, validation_canonical = (
        _independent_capture_and_validation(response_context, raw)
    )
    context = _expect_downstream_success(
        validator._validate_analyst_response_for_downstream(
            response_context.generation_id,
            raw_response_bytes=raw,
            repository_root=response_context.root,
        )
    )
    reachable = _reachable_context_state(context)
    expected_mappings = (response, capture, validation)
    for value in reachable:
        if isinstance(value, Mapping):
            assert all(_thaw(value) != expected for expected in expected_mappings)
        if type(value) is tuple:
            assert value != _ordered_tuple_projection(response)
            assert value != _ordered_tuple_projection(capture)
            assert value != _ordered_tuple_projection(validation)
    forbidden_serializations = {
        raw,
        contracts.canonical_json_bytes(response),
        capture_canonical,
        validation_canonical,
        base64.b64encode(raw),
        raw.hex().encode("ascii"),
    }
    assert not any(
        type(value) is bytes and value in forbidden_serializations
        for value in reachable
    )


def test_private_context_preserves_only_selected_report_content(
    response_context: _ResponseContext,
) -> None:
    response, raw = _marked_response(response_context)
    capture, _, validation, _ = _independent_capture_and_validation(
        response_context,
        raw,
    )
    context = _expect_downstream_success(
        validator._validate_analyst_response_for_downstream(
            response_context.generation_id,
            raw_response_bytes=raw,
            repository_root=response_context.root,
        )
    )
    expected_content = {
        field: response[field]
        for field in (
            "analyst_conclusion",
            "analyst_confidence",
            "analytical_sections",
            "analyst_limitation_codes",
        )
    }
    assert _thaw(context.validated_analyst_content) == expected_content
    assert (
        context.validated_analyst_content["analytical_sections"]["observations"][0][
            "statement"
        ]
        == "PERMITTED-SELECTED-ANALYST-CONTENT-77a9 remains byte exact."
    )
    assert context.run_id == response["run_id"]
    assert context.input_package_identity_sha256 == response[
        "input_package_identity_sha256"
    ]
    assert context.response_capture_identity_sha256 == capture[
        "response_capture_identity_sha256"
    ]
    assert context.validation_identity_sha256 == validation[
        "validation_identity_sha256"
    ]
    assert not any(
        field in context.validated_analyst_content
        for field in (
            "schema_version",
            "stage_version",
            "run_id",
            "input_package_identity_sha256",
            "prompt_template_identity_sha256",
            "source_generation_id",
            "source_artifact_bindings",
            "evidence_record_bindings",
            "negative_authority",
        )
    )


@pytest.mark.parametrize(
    ("raw_value", "reason_code"),
    (
        ("{}", "WS01_BR_RESPONSE_UNREADABLE"),
        (bytearray(b"{}"), "WS01_BR_RESPONSE_UNREADABLE"),
        (memoryview(b"{}"), "WS01_BR_RESPONSE_UNREADABLE"),
        ({}, "WS01_BR_RESPONSE_UNREADABLE"),
        ((), "WS01_BR_RESPONSE_UNREADABLE"),
        (b"", "WS01_BR_RESPONSE_MISSING"),
        (b"\xef\xbb\xbf{}", "WS01_BR_RESPONSE_UNREADABLE"),
        (b"\xff", "WS01_BR_RESPONSE_UNREADABLE"),
        (b"x" * 131_073, "WS01_BR_RESPONSE_OVERSIZED"),
    ),
    ids=(
        "str",
        "bytearray",
        "memoryview",
        "mapping",
        "tuple",
        "empty",
        "bom",
        "invalid-utf8",
        "oversized",
    ),
)
def test_raw_response_exact_type_size_and_encoding_boundary(
    response_context: _ResponseContext,
    raw_value: object,
    reason_code: str,
) -> None:
    _expect_failure(_call(response_context, raw_value), reason_code)


def test_bytes_subclass_is_rejected(response_context: _ResponseContext) -> None:
    class BytesSubclass(bytes):
        pass

    _expect_failure(
        _call(response_context, BytesSubclass(response_context.raw_response)),
        "WS01_BR_RESPONSE_UNREADABLE",
    )


@pytest.mark.parametrize(
    "raw_value",
    (
        b"```json\n{}\n```",
        b"schema_version: weekly_shadow_01_analyst_response_v2",
        b"{} {}",
        b"{} trailing",
        b'{"number":NaN}',
        b'{"number":Infinity}',
        b"[1,2,3]",
        b"null",
        b'"text"',
        b"{",
    ),
    ids=(
        "fence",
        "yaml",
        "multiple-json",
        "trailing-prose",
        "nan",
        "infinity",
        "array",
        "null",
        "string",
        "malformed",
    ),
)
def test_strict_parser_performs_no_repair(
    response_context: _ResponseContext,
    raw_value: bytes,
) -> None:
    expected = (
        "WS01_BR_RESPONSE_SCHEMA_INVALID"
        if raw_value in {b"[1,2,3]", b"null", b'"text"'}
        else "WS01_BR_RESPONSE_PARSE_FAILED"
    )
    _expect_failure(_call(response_context, raw_value), expected)


@pytest.mark.parametrize(
    "raw_value",
    (
        b'{"schema_version":"a","schema_version":"b"}',
        b'{"outer":{"duplicate":1,"duplicate":2}}',
        b'{"outer":[{"duplicate":1,"duplicate":2}]}',
    ),
    ids=("root", "nested-object", "nested-array-object"),
)
def test_duplicate_keys_fail_at_every_depth(
    response_context: _ResponseContext,
    raw_value: bytes,
) -> None:
    _expect_failure(
        _call(response_context, raw_value),
        "WS01_BR_RESPONSE_DUPLICATE_KEY",
    )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value.__setitem__("unknown_field", None),
        lambda value: value.pop("run_id"),
        lambda value: value.__setitem__("run_id", None),
        lambda value: value.__setitem__("run_id", False),
        lambda value: value.__setitem__("analytical_sections", []),
        lambda value: value["analytical_sections"].__setitem__("observations", None),
    ),
    ids=(
        "unknown-field",
        "missing",
        "null",
        "false",
        "wrong-object-type",
        "null-array",
    ),
)
def test_schema_distinguishes_missing_null_false_and_wrong_shapes(
    response_context: _ResponseContext,
    mutation,
) -> None:
    _expect_failure(
        _call(response_context, _changed_response(response_context, mutation)),
        "WS01_BR_RESPONSE_SCHEMA_INVALID",
    )


def test_embedded_control_character_is_rejected(
    response_context: _ResponseContext,
) -> None:
    raw = _changed_response(
        response_context,
        lambda value: value["analytical_sections"]["observations"][0].__setitem__(
            "statement", "bounded\u0000text"
        ),
    )
    _expect_failure(
        _call(response_context, raw),
        "WS01_BR_RESPONSE_SCHEMA_INVALID",
    )


def test_text_bound_exact_and_one_over(
    response_context: _ResponseContext,
) -> None:
    exact = _changed_response(
        response_context,
        lambda value: value["analytical_sections"]["observations"][0].__setitem__(
            "statement", "x" * 2_048
        ),
    )
    assert _call(response_context, exact).ok is True
    one_over = _changed_response(
        response_context,
        lambda value: value["analytical_sections"]["observations"][0].__setitem__(
            "statement", "x" * 2_049
        ),
    )
    _expect_failure(
        _call(response_context, one_over),
        "WS01_BR_RESOURCE_BOUND_EXCEEDED",
    )


def test_reference_count_exact_and_one_over(
    response_context: _ResponseContext,
) -> None:
    ids = [
        item["evidence_record_id"]
        for item in response_context.response["evidence_record_bindings"]
    ]
    assert len(ids) >= 16
    exact = _changed_response(
        response_context,
        lambda value: value["analytical_sections"]["observations"][0].__setitem__(
            "evidence_record_ids", ids[:16]
        ),
    )
    assert _call(response_context, exact).ok is True
    one_over_response = copy.deepcopy(response_context.response)
    one_over_response["analytical_sections"]["observations"][0][
        "evidence_record_ids"
    ] = [*ids[:16], "unknown-reference"]
    _expect_failure(
        _call(response_context, _raw(one_over_response)),
        "WS01_BR_RESPONSE_SCHEMA_INVALID",
    )


def test_aggregate_text_bound_exact_and_one_over(
    response_context: _ResponseContext,
) -> None:
    evidence_id = response_context.response["evidence_record_bindings"][0][
        "evidence_record_id"
    ]

    def entries(lengths: list[int]) -> list[dict[str, object]]:
        return [
            {
                "entry_id": f"aggregate-{index:02d}",
                "statement": "x" * length,
                "evidence_record_ids": [evidence_id],
            }
            for index, length in enumerate(lengths)
        ]

    exact_response = copy.deepcopy(response_context.response)
    exact_response["analytical_sections"]["observations"] = entries([2_048] * 16)
    assert _call(response_context, _raw(exact_response)).ok is True
    one_over_response = copy.deepcopy(response_context.response)
    one_over_response["analytical_sections"]["observations"] = entries(
        [1_927] * 16 + [1_937]
    )
    assert sum(
        len(item["statement"])
        for item in one_over_response["analytical_sections"]["observations"]
    ) == 32_769
    _expect_failure(
        _call(response_context, _raw(one_over_response)),
        "WS01_BR_RESOURCE_BOUND_EXCEEDED",
    )


def test_depth_object_and_array_one_over_fail_resource_bound(
    response_context: _ResponseContext,
) -> None:
    deep: object = None
    for _ in range(17):
        deep = [deep]
    for extra in (
        {"extra": deep},
        {f"extra_{index}": None for index in range(1_025)},
        {"extra": [None] * 1_025},
    ):
        response = copy.deepcopy(response_context.response)
        response.update(extra)
        _expect_failure(
            _call(response_context, _raw(response)),
            "WS01_BR_RESOURCE_BOUND_EXCEEDED",
        )


@pytest.mark.parametrize(
    ("field", "reason_code"),
    (
        ("run_id", "WS01_BR_RUN_BINDING_MISMATCH"),
        ("input_package_identity_sha256", "WS01_BR_PACKAGE_BINDING_MISMATCH"),
        (
            "prompt_template_identity_sha256",
            "WS01_BR_PROMPT_TEMPLATE_BINDING_MISMATCH",
        ),
        (
            "source_generation_id",
            "WS01_BR_SOURCE_GENERATION_BINDING_MISMATCH",
        ),
    ),
)
def test_scalar_grounding_binding_mismatches_fail_closed(
    response_context: _ResponseContext,
    field: str,
    reason_code: str,
) -> None:
    replacement = (
        "forged-run"
        if field == "run_id"
        else ("0" * 64)
    )
    raw = _changed_response(
        response_context,
        lambda value: value.__setitem__(field, replacement),
    )
    _expect_failure(_call(response_context, raw), reason_code)


@pytest.mark.parametrize(
    ("kind", "reason_code"),
    (
        ("artifact-missing", "WS01_BR_ARTIFACT_ECHO_INCOMPLETE"),
        ("artifact-extra", "WS01_BR_ARTIFACT_ECHO_UNEXPECTED"),
        ("artifact-order", "WS01_BR_CROSS_FIELD_INVALID"),
        ("evidence-missing", "WS01_BR_EVIDENCE_ECHO_INCOMPLETE"),
        ("evidence-extra", "WS01_BR_EVIDENCE_ECHO_UNEXPECTED"),
        ("evidence-order", "WS01_BR_CROSS_FIELD_INVALID"),
    ),
)
def test_artifact_and_evidence_echo_closure(
    response_context: _ResponseContext,
    kind: str,
    reason_code: str,
) -> None:
    response = copy.deepcopy(response_context.response)
    field = (
        "source_artifact_bindings"
        if kind.startswith("artifact")
        else "evidence_record_bindings"
    )
    if kind.endswith("missing"):
        response[field].pop()
    elif kind.endswith("extra"):
        extra = copy.deepcopy(response[field][0])
        identity_field = (
            "source_id"
            if field == "source_artifact_bindings"
            else "evidence_record_id"
        )
        extra[identity_field] = "unexpected-record"
        response[field].append(extra)
    else:
        response[field][0], response[field][1] = (
            response[field][1],
            response[field][0],
        )
    _expect_failure(_call(response_context, _raw(response)), reason_code)


@pytest.mark.parametrize(
    "kind",
    ("artifact-identity", "evidence-identity"),
)
def test_identity_changed_echo_is_rejected(
    response_context: _ResponseContext,
    kind: str,
) -> None:
    response = copy.deepcopy(response_context.response)
    field = (
        "source_artifact_bindings"
        if kind == "artifact-identity"
        else "evidence_record_bindings"
    )
    identity = (
        "source_artifact_identity_sha256"
        if kind == "artifact-identity"
        else "evidence_record_identity_sha256"
    )
    response[field][0][identity] = "0" * 64
    _expect_failure(
        _call(response_context, _raw(response)),
        "WS01_BR_CROSS_FIELD_INVALID",
    )


@pytest.mark.parametrize(
    "reference_kind",
    ("observation", "missing-note", "limitation"),
)
def test_unknown_or_wrong_category_references_fail_closed(
    response_context: _ResponseContext,
    reference_kind: str,
) -> None:
    response = copy.deepcopy(response_context.response)
    if reference_kind == "observation":
        response["analytical_sections"]["observations"][0][
            "evidence_record_ids"
        ] = ["unknown-reference"]
    elif reference_kind == "missing-note":
        response["analytical_sections"]["missing_evidence_notes"] = [
            {
                "entry_id": "missing-01",
                "statement": "The supplied diagnostics do not cover this point.",
                "diagnostic_ids": ["unknown-diagnostic-reference"],
            }
        ]
    else:
        response["analyst_limitation_codes"] = [
            {
                "code": "WS01_AL_EVIDENCE_SPARSE",
                "reference_ids": ["unknown-reference"],
            }
        ]
    _expect_failure(
        _call(response_context, _raw(response)),
        "WS01_BR_EVIDENCE_REFERENCE_INVALID",
    )


def test_duplicate_entry_ids_and_limitation_codes_fail_cross_field(
    response_context: _ResponseContext,
) -> None:
    evidence_id = response_context.response["evidence_record_bindings"][0][
        "evidence_record_id"
    ]
    response = copy.deepcopy(response_context.response)
    response["analytical_sections"]["risks_and_uncertainties"] = [
        {
            "entry_id": "observation-01",
            "statement": "A bounded uncertainty remains.",
            "evidence_record_ids": [evidence_id],
        }
    ]
    _expect_failure(
        _call(response_context, _raw(response)),
        "WS01_BR_CROSS_FIELD_INVALID",
    )
    response = copy.deepcopy(response_context.response)
    response["analyst_limitation_codes"] = [
        {
            "code": "WS01_AL_EVIDENCE_SPARSE",
            "reference_ids": [evidence_id],
        },
        {
            "code": "WS01_AL_EVIDENCE_SPARSE",
            "reference_ids": [evidence_id],
        },
    ]
    _expect_failure(
        _call(response_context, _raw(response)),
        "WS01_BR_CROSS_FIELD_INVALID",
    )


@pytest.mark.parametrize(
    "key",
    (
        "order",
        "ORDER",
        "new-buy",
        "permission.value",
        "broker",
    ),
)
def test_prohibited_keys_are_normalized_and_rejected(
    response_context: _ResponseContext,
    key: str,
) -> None:
    response = copy.deepcopy(response_context.response)
    response["analytical_sections"]["observations"][0][key] = False
    _expect_failure(
        _call(response_context, _raw(response)),
        "WS01_BR_PROHIBITED_KEY",
    )


@pytest.mark.parametrize(
    "statement",
    (
        "BUY",
        "The evidence says SELL.",
        "NO_TRADE",
        "The operator should execute.",
        "A rebalance is warranted.",
        "APPROVED",
    ),
)
def test_prohibited_intent_and_conclusion_terms_are_rejected(
    response_context: _ResponseContext,
    statement: str,
) -> None:
    raw = _changed_response(
        response_context,
        lambda value: value["analytical_sections"]["observations"][0].__setitem__(
            "statement", statement
        ),
    )
    _expect_failure(
        _call(response_context, raw),
        "WS01_BR_PROHIBITED_INTENT",
    )


def test_negative_authority_is_exact_and_same_key_elsewhere_is_prohibited(
    response_context: _ResponseContext,
) -> None:
    assert _call(response_context).ok is True
    changed = _changed_response(
        response_context,
        lambda value: value["negative_authority"].__setitem__(
            "order_eligible", True
        ),
    )
    _expect_failure(
        _call(response_context, changed),
        "WS01_BR_RESPONSE_SCHEMA_INVALID",
    )
    response = copy.deepcopy(response_context.response)
    response["analytical_sections"]["observations"][0]["order_eligible"] = False
    _expect_failure(
        _call(response_context, _raw(response)),
        "WS01_BR_PROHIBITED_KEY",
    )


def test_result_failure_retains_no_response_or_exception_state(
    response_context: _ResponseContext,
) -> None:
    secret = "WS01C-SECRET-RAW-MARKER-7a81"
    result = _call(response_context, f'{{"secret":"{secret}",'.encode())
    _expect_failure(result, "WS01_BR_RESPONSE_PARSE_FAILED", secret, response_context.root)
    _assert_no_reachable_exception(result)
    try:
        raise RuntimeError("OUTER-EXCEPTION-MARKER")
    except RuntimeError:
        nested_result = _call(response_context, f'{{"secret":"{secret}",'.encode())
    _expect_failure(
        nested_result,
        "WS01_BR_RESPONSE_PARSE_FAILED",
        secret,
        "OUTER-EXCEPTION-MARKER",
    )
    _assert_no_reachable_exception(nested_result)


@pytest.mark.parametrize(
    "exception_type",
    (KeyboardInterrupt, SystemExit, GeneratorExit),
)
def test_control_flow_exceptions_propagate(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[BaseException],
) -> None:
    def interrupt(*_args: object, **_kwargs: object) -> object:
        raise exception_type("control-flow")

    monkeypatch.setattr(
        validator,
        "_BUILD_PACKAGE_FROM_SOURCE_SELECTION",
        interrupt,
    )
    with pytest.raises(exception_type, match="control-flow"):
        _call(response_context)


@pytest.mark.parametrize(
    "phase",
    (
        "_BUILD_PACKAGE_FROM_SOURCE_SELECTION",
        "_RENDER_ANALYST_PROMPT",
        "_authenticate_response_contracts",
        "_parse_untrusted_response",
        "_validate_response_bindings_and_semantics",
        "_build_response_capture",
        "_build_response_validation",
        "_new_downstream_context",
    ),
)
def test_unexpected_ordinary_exceptions_fail_closed(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    original = getattr(validator, phase)

    def unexpected(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("PRIVATE-FAILURE-MARKER")

    monkeypatch.setattr(validator, phase, unexpected)
    result = _call(response_context)
    _expect_failure(
        result,
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        "PRIVATE-FAILURE-MARKER",
    )
    monkeypatch.setattr(validator, phase, original)


def test_stateful_repository_root_is_normalized_once_and_cannot_split_authority(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_b = tmp_path / "repository-b"
    _setup_repo(repository_b)
    generation_b = (
        repository_b
        / "artifacts/current/step1_research/r2f_report_only/generations"
        / response_context.generation_id
    )
    assert not generation_b.exists()

    class StatefulRoot:
        def __init__(self) -> None:
            self.invocation_count = 0
            self.returned_paths: list[Path] = []

        def __fspath__(self) -> str:
            selected = (
                response_context.root
                if self.invocation_count == 0
                else repository_b
            )
            self.invocation_count += 1
            self.returned_paths.append(selected)
            return str(selected)

    selector = StatefulRoot()
    observed: dict[str, list[object]] = {
        "builder_roots": [],
        "ws01b_roots": [],
        "contract_roots": [],
        "adapter_filesystem_roots": [],
        "validator_filesystem_roots": [],
    }
    original_build = validator._BUILD_PACKAGE_FROM_SOURCE_SELECTION
    original_verify = builder._VERIFY_R2F_V2_GENERATION
    original_authenticate = validator._authenticate_response_contracts
    original_adapter_chain = adapter._open_absolute_directory_chain
    original_validator_chain = validator._open_absolute_directory_chain

    def build(
        generation_id: str,
        *,
        repository_root: object = None,
    ) -> object:
        observed["builder_roots"].append(repository_root)
        return original_build(
            generation_id,
            repository_root=repository_root,
        )

    def authenticate(package: object, *, root: Path) -> object:
        observed["contract_roots"].append(root)
        return original_authenticate(package, root=root)

    def verify(
        generation_id: str,
        *,
        repository_root: object = None,
    ) -> object:
        observed["ws01b_roots"].append(repository_root)
        return original_verify(
            generation_id,
            repository_root=repository_root,
        )

    def adapter_chain(root: Path, *, owner: object) -> object:
        observed["adapter_filesystem_roots"].append(root)
        return original_adapter_chain(root, owner=owner)

    def validator_chain(root: Path, *, owner: object) -> object:
        observed["validator_filesystem_roots"].append(root)
        return original_validator_chain(root, owner=owner)

    monkeypatch.setattr(
        validator,
        "_BUILD_PACKAGE_FROM_SOURCE_SELECTION",
        build,
    )
    monkeypatch.setattr(
        validator,
        "_authenticate_response_contracts",
        authenticate,
    )
    monkeypatch.setattr(builder, "_VERIFY_R2F_V2_GENERATION", verify)
    monkeypatch.setattr(adapter, "_open_absolute_directory_chain", adapter_chain)
    monkeypatch.setattr(
        validator,
        "_open_absolute_directory_chain",
        validator_chain,
    )

    result = validator.validate_analyst_response(
        response_context.generation_id,
        raw_response_bytes=response_context.raw_response,
        repository_root=selector,
    )
    _expect_success(result)
    assert selector.invocation_count == 1
    assert selector.returned_paths == [response_context.root]
    assert len(observed["builder_roots"]) == 1
    assert len(observed["ws01b_roots"]) == 1
    assert len(observed["contract_roots"]) == 1
    assert observed["builder_roots"][0] is observed["contract_roots"][0]
    assert observed["builder_roots"][0] is observed["ws01b_roots"][0]
    assert observed["validator_filesystem_roots"][0] is observed["builder_roots"][0]
    assert observed["builder_roots"][0] == response_context.root
    assert observed["adapter_filesystem_roots"] == [response_context.root]
    assert observed["validator_filesystem_roots"] == [response_context.root]
    assert repository_b not in {
        *observed["adapter_filesystem_roots"],
        *observed["validator_filesystem_roots"],
    }


def test_repository_root_pathlike_that_raises_on_second_call_succeeds(
    response_context: _ResponseContext,
) -> None:
    class SingleUseRoot:
        def __init__(self) -> None:
            self.invocation_count = 0

        def __fspath__(self) -> str:
            self.invocation_count += 1
            if self.invocation_count > 1:
                raise RuntimeError("SECOND-ROOT-RESOLUTION-MUST-NOT-OCCUR")
            return str(response_context.root)

    selector = SingleUseRoot()
    _expect_success(
        validator.validate_analyst_response(
            response_context.generation_id,
            raw_response_bytes=response_context.raw_response,
            repository_root=selector,
        )
    )
    assert selector.invocation_count == 1


def test_invalid_repository_root_pathlike_failures_are_code_only(
    response_context: _ResponseContext,
) -> None:
    secret = "ROOT-NORMALIZATION-SECRET-8E13"

    class ReturnsBytes:
        def __fspath__(self) -> bytes:
            return str(response_context.root).encode("utf-8")

    class ReturnsUnsupportedValue:
        def __fspath__(self) -> object:
            return {secret: response_context.root}

    class RaisesOrdinaryException:
        def __fspath__(self) -> str:
            raise RuntimeError(secret)

    _expect_failure(
        validator.validate_analyst_response(
            response_context.generation_id,
            raw_response_bytes=response_context.raw_response,
            repository_root=ReturnsBytes(),
        ),
        "WS01_BR_SOURCE_GENERATION_INVALID",
        response_context.root,
    )
    _expect_failure(
        validator.validate_analyst_response(
            response_context.generation_id,
            raw_response_bytes=response_context.raw_response,
            repository_root=ReturnsUnsupportedValue(),
        ),
        "WS01_BR_SOURCE_GENERATION_INVALID",
        secret,
        response_context.root,
    )
    _expect_failure(
        validator.validate_analyst_response(
            response_context.generation_id,
            raw_response_bytes=response_context.raw_response,
            repository_root=RaisesOrdinaryException(),
        ),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        secret,
        response_context.root,
    )


@pytest.mark.parametrize(
    "exception_type",
    (KeyboardInterrupt, SystemExit, GeneratorExit),
)
def test_repository_root_normalization_control_flow_exceptions_propagate(
    response_context: _ResponseContext,
    exception_type: type[BaseException],
) -> None:
    class InterruptingRoot:
        def __fspath__(self) -> str:
            raise exception_type("ROOT-NORMALIZATION-CONTROL-FLOW")

    with pytest.raises(exception_type, match="ROOT-NORMALIZATION-CONTROL-FLOW"):
        validator.validate_analyst_response(
            response_context.generation_id,
            raw_response_bytes=response_context.raw_response,
            repository_root=InterruptingRoot(),
        )


def test_string_path_and_default_repository_roots_remain_compatible(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_value = _expect_success(_call(response_context))
    string_value = _expect_success(
        validator.validate_analyst_response(
            response_context.generation_id,
            raw_response_bytes=response_context.raw_response,
            repository_root=str(response_context.root),
        )
    )
    assert string_value == path_value

    simulated_module = (
        response_context.root
        / "src/investment_orchestrator/observability"
        / "weekly_shadow_01_response_validator.py"
    )
    monkeypatch.setattr(validator, "__file__", str(simulated_module))
    default_value = _expect_success(
        validator.validate_analyst_response(
            response_context.generation_id,
            raw_response_bytes=response_context.raw_response,
        )
    )
    assert default_value == path_value


def test_one_public_call_runs_one_complete_private_pipeline(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    counts = {
        "root": 0,
        "verify": 0,
        "snapshot": 0,
        "package": 0,
        "render": 0,
        "parse": 0,
        "capture": 0,
        "validation": 0,
        "legacy_projection": 0,
        "context": 0,
    }
    originals = {
        "root": validator._repository_root,
        "verify": builder._VERIFY_R2F_V2_GENERATION,
        "snapshot": builder._BUILD_SOURCE_SNAPSHOT,
        "package": builder._build_analyst_input_package,
        "render": validator._RENDER_ANALYST_PROMPT,
        "parse": validator._parse_untrusted_response,
        "capture": validator._build_response_capture,
        "validation": validator._build_response_validation,
        "legacy_projection": validator._new_validated_response,
        "context": validator._new_downstream_context,
    }

    def wrap(name: str):
        original = originals[name]

        def wrapped(*args: object, **kwargs: object) -> object:
            counts[name] += 1
            order.append(name)
            return original(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(validator, "_repository_root", wrap("root"))
    monkeypatch.setattr(builder, "_VERIFY_R2F_V2_GENERATION", wrap("verify"))
    monkeypatch.setattr(builder, "_BUILD_SOURCE_SNAPSHOT", wrap("snapshot"))
    monkeypatch.setattr(builder, "_build_analyst_input_package", wrap("package"))
    monkeypatch.setattr(validator, "_RENDER_ANALYST_PROMPT", wrap("render"))
    monkeypatch.setattr(validator, "_parse_untrusted_response", wrap("parse"))
    monkeypatch.setattr(validator, "_build_response_capture", wrap("capture"))
    monkeypatch.setattr(validator, "_build_response_validation", wrap("validation"))
    monkeypatch.setattr(
        validator,
        "_new_validated_response",
        wrap("legacy_projection"),
    )
    monkeypatch.setattr(validator, "_new_downstream_context", wrap("context"))
    assert _call(response_context).ok is True
    assert counts == {name: 1 for name in counts}
    assert order == [
        "root",
        "verify",
        "snapshot",
        "package",
        "render",
        "parse",
        "capture",
        "validation",
        "legacy_projection",
        "context",
    ]


def test_schema_read_instability_fails_before_capture(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    import shutil

    shutil.copytree(response_context.root, root)
    target = root / "schemas/weekly_shadow_01_analyst_response.schema.json"
    original_reader = validator._read_complete_descriptor
    calls = 0

    def racing_reader(*args: object, **kwargs: object) -> bytes:
        nonlocal calls
        value = original_reader(*args, **kwargs)
        calls += 1
        if calls == 1:
            changed = bytearray(target.read_bytes())
            changed[-2] = 32 if changed[-2] != 32 else 10
            target.write_bytes(bytes(changed))
        return value

    monkeypatch.setattr(validator, "_read_complete_descriptor", racing_reader)
    result = validator.validate_analyst_response(
        response_context.generation_id,
        raw_response_bytes=response_context.raw_response,
        repository_root=root,
    )
    _expect_failure(result, "WS01_BR_SOURCE_READ_UNSTABLE")


@pytest.mark.parametrize(
    "schema_name",
    (
        "weekly_shadow_01_analyst_response.schema.json",
        "weekly_shadow_01_response_capture.schema.json",
        "weekly_shadow_01_response_validation.schema.json",
        "weekly_shadow_01_analyst_report.schema.json",
        "weekly_shadow_01_run_summary.schema.json",
    ),
)
def test_changed_response_contract_schema_fails_closed(
    response_context: _ResponseContext,
    tmp_path: Path,
    schema_name: str,
) -> None:
    import shutil

    root = tmp_path / "repo"
    shutil.copytree(response_context.root, root)
    path = root / "schemas" / schema_name
    path.write_bytes(path.read_bytes() + b" ")
    result = validator.validate_analyst_response(
        response_context.generation_id,
        raw_response_bytes=response_context.raw_response,
        repository_root=root,
    )
    _expect_failure(result, "WS01_BR_INTERNAL_INVARIANT_FAILURE")


def test_changed_contract_metadata_fails_before_response_acceptance(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    import shutil

    root = tmp_path / "repo"
    shutil.copytree(response_context.root, root)
    path = (
        root
        / "src/investment_orchestrator/observability/weekly_shadow_01_contracts.py"
    )
    source = path.read_text(encoding="utf-8")
    path.write_text(
        source.replace(
            '"content_trust": "untrusted_llm_content_validated_by_future_ws01c"',
            '"content_trust": "changed-contract-marker"',
            1,
        ),
        encoding="utf-8",
    )
    result = validator.validate_analyst_response(
        response_context.generation_id,
        raw_response_bytes=response_context.raw_response,
        repository_root=root,
    )
    _expect_failure(result, "WS01_BR_INTERNAL_INVARIANT_FAILURE")


@pytest.mark.parametrize(
    ("old", "new"),
    (
        (
            'owner="deterministic_code_and_validated_llm_content"',
            'owner="changed_report_owner"',
        ),
        (
            '"weekly_shadow_01_run_summary_v1",\n'
            '            owner="deterministic_code",',
            '"weekly_shadow_01_run_summary_v1",\n'
            '            owner="changed_summary_owner",',
        ),
        (
            '"analyst_report_max_bytes": 262_144',
            '"analyst_report_max_bytes": 262_143',
        ),
        (
            '"execution_authority": False',
            '"execution_authority": True',
        ),
        (
            '"catalog_version": "weekly_shadow_01_contract_catalog_v2"',
            '"catalog_version": "changed_catalog"',
        ),
    ),
    ids=(
        "report-semantic-metadata",
        "run-summary-semantic-metadata",
        "resource-profile",
        "negative-authority",
        "catalog-metadata",
    ),
)
def test_downstream_contract_source_tampering_fails_closed(
    response_context: _ResponseContext,
    tmp_path: Path,
    old: str,
    new: str,
) -> None:
    import shutil

    root = tmp_path / "repo"
    shutil.copytree(response_context.root, root)
    path = (
        root
        / "src/investment_orchestrator/observability/weekly_shadow_01_contracts.py"
    )
    source = path.read_text(encoding="utf-8")
    assert source.count(old) == 1
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    _expect_downstream_failure(
        validator._validate_analyst_response_for_downstream(
            response_context.generation_id,
            raw_response_bytes=response_context.raw_response,
            repository_root=root,
        ),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        new,
        root,
    )


@pytest.mark.parametrize(
    "schema_name",
    (
        "weekly_shadow_01_analyst_report.schema.json",
        "weekly_shadow_01_run_summary.schema.json",
    ),
)
def test_downstream_schema_stable_read_race_fails_without_context(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schema_name: str,
) -> None:
    import shutil

    root = tmp_path / "repo"
    shutil.copytree(response_context.root, root)
    target = root / "schemas" / schema_name
    original_reader = validator._read_complete_descriptor
    raced = False

    def racing_reader(
        descriptor: int,
        *,
        expected_size: int,
        maximum_bytes: int,
    ) -> bytes:
        nonlocal raced
        value = original_reader(
            descriptor,
            expected_size=expected_size,
            maximum_bytes=maximum_bytes,
        )
        descriptor_path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if not raced and descriptor_path == target:
            raced = True
            changed = bytearray(target.read_bytes())
            changed[-2] = 32 if changed[-2] != 32 else 10
            target.write_bytes(bytes(changed))
        return value

    monkeypatch.setattr(validator, "_read_complete_descriptor", racing_reader)
    result = validator._validate_analyst_response_for_downstream(
        response_context.generation_id,
        raw_response_bytes=response_context.raw_response,
        repository_root=root,
    )
    assert raced is True
    _expect_downstream_failure(
        result,
        "WS01_BR_SOURCE_READ_UNSTABLE",
        target,
        root,
    )


@pytest.mark.parametrize(
    ("old", "new"),
    (
        (
            'owner="deterministic_code_and_validated_llm_content"',
            'owner="raced_report_owner"',
        ),
        (
            '"weekly_shadow_01_run_summary_v1",\n'
            '            owner="deterministic_code",',
            '"weekly_shadow_01_run_summary_v1",\n'
            '            owner="raced_summary_owner",',
        ),
        (
            '"analyst_report_max_bytes": 262_144',
            '"analyst_report_max_bytes": 262_143',
        ),
        (
            '"execution_authority": False',
            '"execution_authority": True',
        ),
        (
            '"catalog_version": "weekly_shadow_01_contract_catalog_v2"',
            '"catalog_version": "raced_catalog"',
        ),
    ),
    ids=(
        "report-semantic-metadata",
        "run-summary-semantic-metadata",
        "resource-profile",
        "negative-authority",
        "catalog-metadata",
    ),
)
def test_downstream_contract_source_stable_read_race_fails_without_context(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old: str,
    new: str,
) -> None:
    import shutil

    root = tmp_path / "repo"
    shutil.copytree(response_context.root, root)
    target = (
        root
        / "src/investment_orchestrator/observability/weekly_shadow_01_contracts.py"
    )
    original_source = target.read_text(encoding="utf-8")
    assert original_source.count(old) == 1
    original_reader = validator._read_complete_descriptor
    raced = False

    def racing_reader(
        descriptor: int,
        *,
        expected_size: int,
        maximum_bytes: int,
    ) -> bytes:
        nonlocal raced
        value = original_reader(
            descriptor,
            expected_size=expected_size,
            maximum_bytes=maximum_bytes,
        )
        descriptor_path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if not raced and descriptor_path == target:
            raced = True
            target.write_text(
                original_source.replace(old, new, 1),
                encoding="utf-8",
            )
        return value

    monkeypatch.setattr(validator, "_read_complete_descriptor", racing_reader)
    result = validator._validate_analyst_response_for_downstream(
        response_context.generation_id,
        raw_response_bytes=response_context.raw_response,
        repository_root=root,
    )
    assert raced is True
    _expect_downstream_failure(
        result,
        "WS01_BR_SOURCE_READ_UNSTABLE",
        new,
        target,
        root,
    )


def test_authenticated_surface_metadata_tampering_fails_before_context(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = validator._REQUIRE_CONTRACT_SURFACE

    def changed_surface(value: object) -> dict[str, object]:
        surface = original(value)
        surface["resource_bound_profile_identity_sha256"] = "0" * 64
        return surface

    monkeypatch.setattr(
        validator,
        "_REQUIRE_CONTRACT_SURFACE",
        changed_surface,
    )
    _expect_downstream_failure(
        _call_downstream(response_context),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    )


def test_authenticated_surface_seal_replacement_fails_before_context(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_build = validator._BUILD_PACKAGE_FROM_SOURCE_SELECTION

    def changed_package(
        generation_id: str,
        *,
        repository_root: Path,
    ) -> object:
        package = original_build(
            generation_id,
            repository_root=repository_root,
        )
        surface = package._authenticated_contract_surface
        changed_surface = object.__new__(type(surface))
        for field in fields(surface):
            object.__setattr__(
                changed_surface,
                field.name,
                (
                    "0" * 64
                    if field.name == "seal_sha256"
                    else getattr(surface, field.name)
                ),
            )
        changed = object.__new__(type(package))
        for field in fields(package):
            value = getattr(package, field.name)
            if field.name == "_authenticated_contract_surface":
                value = changed_surface
            object.__setattr__(changed, field.name, value)
        return changed

    monkeypatch.setattr(
        validator,
        "_BUILD_PACKAGE_FROM_SOURCE_SELECTION",
        changed_package,
    )
    _expect_downstream_failure(
        _call_downstream(response_context),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    )


def test_mutable_exported_expected_values_cannot_update_private_oracles(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_identities = dict(contracts.SCHEMA_IDENTITY_SHA256_BY_VERSION)
    semantic_identities = dict(
        contracts.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION
    )
    schema_identities["weekly_shadow_01_analyst_report_v1"] = "0" * 64
    semantic_identities["weekly_shadow_01_run_summary_v1"] = "1" * 64
    monkeypatch.setattr(
        contracts,
        "SCHEMA_IDENTITY_SHA256_BY_VERSION",
        schema_identities,
    )
    monkeypatch.setattr(
        contracts,
        "SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION",
        semantic_identities,
    )
    context = _expect_downstream_success(_call_downstream(response_context))
    assert context.analyst_report_contract.schema_identity_sha256 == (
        "7b415fa8eb7cb4ecce92ddf06eb394574f7d1435dd840657396dd2eeb0f4feb8"
    )
    assert (
        context.run_summary_contract.semantic_contract_identity_sha256
        == "88bc37d815c348fa0791c51fbdc660f2527c2d9975a01ab2bde2b9853c2a99b3"
    )


def test_deterministic_repeated_validation_is_byte_identical(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _expect_success(_call(response_context))
    first_context = _expect_downstream_success(_call_downstream(response_context))
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("WS01C_BOUND_OVERRIDE", "1")
    second = _expect_success(_call(response_context))
    second_context = _expect_downstream_success(_call_downstream(response_context))
    assert first == second
    assert first_context == second_context
    assert (
        first.response_capture_canonical_bytes
        == second.response_capture_canonical_bytes
    )
    assert (
        first.response_validation_canonical_bytes
        == second.response_validation_canonical_bytes
    )


@pytest.mark.parametrize(
    "intermediate_factory",
    (
        lambda context: builder.build_analyst_input_package(
            context.generation_id, repository_root=context.root
        ),
        lambda context: context.package,
        lambda context: builder.render_analyst_prompt(
            context.generation_id, repository_root=context.root
        ),
        lambda context: context.package.to_dict(),
        lambda context: tuple(context.package.to_dict().items()),
        lambda context: pickle.loads(
            pickle.dumps(context.package.to_dict())
        ),
        lambda context: _expect_success(_call(context)),
        lambda context: _expect_downstream_success(_call_downstream(context)),
    ),
    ids=(
        "package-result",
        "built-package",
        "render-result",
        "mapping",
        "tuple",
        "serialized-mapping",
        "validated-response",
        "downstream-context",
    ),
)
def test_intermediate_objects_have_no_public_authority_route(
    response_context: _ResponseContext,
    intermediate_factory,
) -> None:
    intermediate = intermediate_factory(response_context)
    result = validator.validate_analyst_response(
        intermediate,
        raw_response_bytes=response_context.raw_response,
        repository_root=response_context.root,
    )
    _expect_failure(result, "WS01_BR_SOURCE_GENERATION_INVALID")


def test_exact_class_clone_proxy_and_property_object_have_no_authority_route(
    response_context: _ResponseContext,
) -> None:
    clone = object.__new__(type(response_context.package))
    for slot in type(response_context.package).__slots__:
        object.__setattr__(clone, slot, getattr(response_context.package, slot))

    class Proxy:
        def __getattr__(self, name: str) -> object:
            return getattr(response_context.package, name)

    class Properties:
        generation_id = property(lambda _self: response_context.generation_id)

    for value in (clone, Proxy(), Properties()):
        _expect_failure(
            validator.validate_analyst_response(
                value,
                raw_response_bytes=response_context.raw_response,
                repository_root=response_context.root,
            ),
            "WS01_BR_SOURCE_GENERATION_INVALID",
        )


@pytest.mark.parametrize(
    "keyword",
    (
        "verified_generation",
        "source_snapshot",
        "package",
        "package_result",
        "prompt_bytes",
        "render_result",
        "request_binding",
        "capture",
        "validation",
        "validated_response",
        "downstream_context",
        "context",
        "schema_bundle",
        "report_schema",
        "run_summary_schema",
        "writer",
        "output_path",
        "publication_destination",
        "callback",
    ),
)
def test_former_intermediate_keywords_are_absent_and_safe(
    response_context: _ResponseContext,
    keyword: str,
) -> None:
    secret = "INTERMEDIATE-SECRET-9c31"
    with pytest.raises(TypeError) as caught:
        validator.validate_analyst_response(
            response_context.generation_id,
            raw_response_bytes=response_context.raw_response,
            repository_root=response_context.root,
            **{keyword: secret},
        )
    assert secret not in str(caught.value)


def test_extra_positional_argument_is_rejected_without_value_leakage(
    response_context: _ResponseContext,
) -> None:
    secret = "POSITIONAL-SECRET-a828"
    with pytest.raises(TypeError) as caught:
        validator.validate_analyst_response(
            response_context.generation_id,
            secret,
            raw_response_bytes=response_context.raw_response,
            repository_root=response_context.root,
        )
    assert secret not in str(caught.value)


def test_private_context_has_no_public_reconstruction_or_adoption_route(
    response_context: _ResponseContext,
) -> None:
    context = _expect_downstream_success(_call_downstream(response_context))
    with pytest.raises(TypeError):
        validator._WS01cDownstreamContext()
    with pytest.raises(TypeError):
        validator._AuthenticatedArtifactContract()

    reconstructed = object.__new__(validator._WS01cDownstreamContext)
    for field in fields(context):
        object.__setattr__(
            reconstructed,
            field.name,
            getattr(context, field.name),
        )

    class ContextProxy:
        def __getattr__(self, name: str) -> object:
            return getattr(context, name)

    class ContextProperty:
        downstream_context = property(
            lambda _self: context
        )

    submissions = (
        context,
        reconstructed,
        ContextProxy(),
        ContextProperty(),
        {"context": context},
        (context,),
    )
    with pytest.raises(TypeError):
        copy.copy(context)
    with pytest.raises(TypeError):
        copy.deepcopy(context)
    with pytest.raises((TypeError, pickle.PicklingError)):
        pickle.dumps(context)
    for submitted in submissions:
        _expect_failure(
            validator.validate_analyst_response(
                submitted,
                raw_response_bytes=response_context.raw_response,
                repository_root=response_context.root,
            ),
            "WS01_BR_SOURCE_GENERATION_INVALID",
        )

    module_names = set(vars(validator))
    assert not any(
        marker in name
        for name in module_names
        for marker in (
            "set_downstream",
            "register_downstream",
            "context_registry",
            "context_cache",
        )
    )
    assert not any(
        type(value)
        in {
            validator._ValidatedAnalystResponse,
            validator._WS01cDownstreamContext,
            validator._WS01cCoreProjections,
            validator._WS01cCoreResult,
        }
        for value in vars(validator).values()
    )


def test_zero_write_network_subprocess_and_environment_effects(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = builtins.open
    original_os_open = os.open
    environment = dict(os.environ)
    paths_before = frozenset(
        path.relative_to(response_context.root)
        for path in response_context.root.rglob("*")
    )
    writes: list[object] = []

    def guarded_open(file: object, mode: str = "r", *args: object, **kwargs: object):
        if any(marker in mode for marker in ("w", "a", "x", "+")):
            writes.append((file, mode))
            raise AssertionError("write attempted")
        return original_open(file, mode, *args, **kwargs)

    def guarded_os_open(path: object, flags: int, *args: object, **kwargs: object):
        forbidden = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        if flags & forbidden:
            writes.append((path, flags))
            raise AssertionError("write attempted")
        return original_os_open(path, flags, *args, **kwargs)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("external capability attempted")

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    supported_dir_fd = set(os.supports_dir_fd)
    supported_dir_fd.discard(original_os_open)
    supported_dir_fd.add(guarded_os_open)
    monkeypatch.setattr(os, "supports_dir_fd", supported_dir_fd)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    assert _call(response_context).ok is True
    _expect_downstream_success(_call_downstream(response_context))
    assert writes == []
    assert dict(os.environ) == environment
    paths_after = frozenset(
        path.relative_to(response_context.root)
        for path in response_context.root.rglob("*")
    )
    assert paths_after == paths_before
    assert not any(
        path.name
        in {
            "weekly_shadow_01_analyst_report.json",
            "weekly_shadow_01_run_summary.json",
        }
        for path in response_context.root.rglob("*")
    )


def test_no_partial_capture_or_validation_on_failure(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validator,
        "_build_response_capture",
        lambda *_args, **_kwargs: pytest.fail("capture must not be constructed"),
    )
    result = _call(response_context, b"{")
    _expect_failure(result, "WS01_BR_RESPONSE_PARSE_FAILED")


def test_private_failure_envelope_retains_no_partial_context_or_intermediate(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "PRIVATE-CONTEXT-FAILURE-MARKER-45c1"
    parse_failure = validator._validate_analyst_response_for_downstream(
        response_context.generation_id,
        raw_response_bytes=f'{{"secret":"{marker}",'.encode(),
        repository_root=response_context.root,
    )
    _expect_downstream_failure(
        parse_failure,
        "WS01_BR_RESPONSE_PARSE_FAILED",
        marker,
        response_context.root,
    )

    def fail_context(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(marker)

    monkeypatch.setattr(validator, "_new_downstream_context", fail_context)
    construction_failure = _call_downstream(response_context)
    _expect_downstream_failure(
        construction_failure,
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        marker,
        response_context.root,
    )


@pytest.mark.parametrize(
    "exception_type",
    (KeyboardInterrupt, SystemExit, GeneratorExit),
)
def test_private_context_control_flow_exceptions_propagate(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[BaseException],
) -> None:
    def interrupt(*_args: object, **_kwargs: object) -> object:
        raise exception_type("PRIVATE-CONTEXT-CONTROL-FLOW")

    monkeypatch.setattr(validator, "_new_downstream_context", interrupt)
    with pytest.raises(exception_type, match="PRIVATE-CONTEXT-CONTROL-FLOW"):
        _call_downstream(response_context)
