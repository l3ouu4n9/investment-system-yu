"""WS01b closed-locator package and deterministic prompt tests."""

from __future__ import annotations

import ast
from collections import namedtuple
import copy
from dataclasses import dataclass, make_dataclass
from datetime import date
import hashlib
import inspect
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
from types import MappingProxyType, ModuleType
from typing import Any

from jsonschema import Draft202012Validator
import pytest
import yaml

from investment_orchestrator.observability import weekly_shadow_01_contracts as contracts
from investment_orchestrator.observability import (
    ltetf_target_architecture_gap_report as gap,
)
from investment_orchestrator.observability import weekly_shadow_01_package_builder as builder
from investment_orchestrator.observability import weekly_shadow_01_source_adapter as adapter
from investment_orchestrator.research import replacement_observation as r2f


def _write(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")


def _anchor(
    anchor_id: str,
    tickers: list[str],
    *,
    summary: str | None = None,
) -> dict[str, Any]:
    return {
        "anchor_id": anchor_id,
        "anchor_type": "structural_theme",
        "applicable_tickers": tickers,
        "anchor_date_et": "2026-07-01",
        "valid_from": "2026-07-01",
        "valid_until": "2026-12-31",
        "source_type": "operator",
        "confidence_floor": "medium",
        "summary": summary if summary is not None else f"Summary {anchor_id}",
    }


def _setup_repo(root: Path, *, anchors: list[dict[str, Any]]) -> None:
    source_root = Path(__file__).parents[2]
    _write(
        root / "inputs/current/strategy_settings.yaml",
        """as_of: "2026-07-12"
benchmark: "FIXA"
core_universe: [FIXA]
satellite_universe: [FIXB]
user_approved_extended_etf_static_list: [FIXC]
hard_cap_open_orders_budget: 100
target_new_buy_budget_this_run: 10
max_new_tickers_per_week: 0
ticker_role_fallback:
  FIXA: benchmark_carrier_core
  FIXB: sector_alpha_tilt
  FIXC: extended_etf_minority_sleeve
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
                "anchors": anchors,
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
    contract_relative = (
        "src/investment_orchestrator/observability/weekly_shadow_01_contracts.py"
    )
    _write(root / contract_relative, (source_root / contract_relative).read_bytes())


@dataclass(frozen=True)
class _PackageContext:
    root: Path
    generation_id: str
    verified: object
    snapshot: object
    package: object

    def __iter__(self):
        yield self.snapshot
        yield self.package


@pytest.fixture
def package_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> _PackageContext:
    root = tmp_path / "repo"
    _setup_repo(
        root,
        anchors=[
            _anchor("ZZZ_ANCHOR", ["FIXB", "FIXA"]),
            _anchor("AAA_ANCHOR", ["FIXA"]),
        ],
    )
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    generation = r2f.replacement_render()
    generation_id = generation["generation_id"]
    verified = _expect_success(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root)
    )
    snapshot = _expect_success(
        adapter.build_source_snapshot(generation_id, repository_root=root)
    )
    package = _expect_success(
        builder.build_analyst_input_package(generation_id, repository_root=root)
    )
    return _PackageContext(root, generation_id, verified, snapshot, package)


def _expect_success(result: object) -> object:
    assert type(result).__name__ == "_WS01bResult"
    assert type(result).__module__.rsplit(".", 1)[-1] in {
        "weekly_shadow_01_source_adapter",
        "weekly_shadow_01_package_builder",
    }
    assert result.ok is True
    assert result.reason_code is None
    assert result.value is not None
    return result.value


def _build_package(snapshot: object) -> object:
    return builder._build_analyst_input_package(snapshot)


def _build_result(snapshot: object) -> object:
    try:
        return builder._result_success(builder._build_analyst_input_package(snapshot))
    except builder._PackageBuilderFailure as failure:
        return builder._result_failure(failure.code)
    except Exception:
        return builder._result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")


def _render_prompt(package: object) -> object:
    return builder._render_analyst_prompt(package)


def _render_result(package: object) -> object:
    try:
        return builder._result_success(builder._render_analyst_prompt(package))
    except builder._PackageBuilderFailure as failure:
        return builder._result_failure(failure.code)
    except Exception:
        return builder._result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")


def _build_public(context: _PackageContext) -> object:
    return builder.build_analyst_input_package(
        context.generation_id,
        repository_root=context.root,
    )


def _render_public(context: _PackageContext) -> object:
    return builder.render_analyst_prompt(
        context.generation_id,
        repository_root=context.root,
    )


def _unchecked_result(
    result_class: type,
    *,
    ok: bool,
    value: object | None,
    reason_code: str | None,
) -> object:
    result = object.__new__(result_class)
    object.__setattr__(result, "ok", ok)
    object.__setattr__(result, "value", value)
    object.__setattr__(result, "reason_code", reason_code)
    return result


def _exact_clone(value: object) -> object:
    clone = object.__new__(type(value))
    for name in type(value).__slots__:
        object.__setattr__(clone, name, getattr(value, name))
    return clone


def _result_lookalike(
    kind: str,
    *,
    module_name: str,
    authentic_result: object,
    value: object,
) -> object:
    namespace = {
        "__module__": module_name,
        "__qualname__": "_WS01bResult",
    }
    if kind == "matching_slots":
        result_class = type(
            "_WS01bResult",
            (),
            {**namespace, "__slots__": ("ok", "value", "reason_code")},
        )
        return _unchecked_result(
            result_class,
            ok=True,
            value=value,
            reason_code=None,
        )
    if kind == "properties":
        result_class = type(
            "_WS01bResult",
            (),
            {
                **namespace,
                "__slots__": (),
                "ok": property(lambda _self: True),
                "value": property(lambda _self: value),
                "reason_code": property(lambda _self: None),
            },
        )
        return object.__new__(result_class)
    if kind == "named_tuple":
        result_class = namedtuple(
            "_WS01bResult",
            ("ok", "value", "reason_code"),
            module=module_name,
        )
        result_class.__qualname__ = "_WS01bResult"
        return result_class(True, value, None)
    if kind == "dataclass":
        result_class = make_dataclass(
            "_WS01bResult",
            (("ok", bool), ("value", object), ("reason_code", str | None)),
            namespace=namespace,
            frozen=True,
            slots=True,
        )
        return result_class(True, value, None)
    if kind == "proxy":
        def forwarded(self: object, name: str) -> object:
            return getattr(self._target, name)

        result_class = type(
            "_WS01bResult",
            (),
            {
                **namespace,
                "__slots__": ("_target",),
                "__getattr__": forwarded,
            },
        )
        proxy = object.__new__(result_class)
        object.__setattr__(proxy, "_target", authentic_result)
        return proxy
    raise AssertionError(kind)


def _slot_lookalike(value: object, *, module_name: str, class_name: str) -> object:
    slots = tuple(type(value).__slots__)
    lookalike_class = type(
        class_name,
        (),
        {
            "__module__": module_name,
            "__qualname__": class_name,
            "__slots__": slots,
        },
    )
    lookalike = object.__new__(lookalike_class)
    for name in slots:
        object.__setattr__(lookalike, name, getattr(value, name))
    return lookalike


def _subclass_clone(value: object, *, class_name: str) -> object:
    subclass = type(
        class_name,
        (type(value),),
        {"__module__": type(value).__module__, "__slots__": ()},
    )
    clone = object.__new__(subclass)
    for name in type(value).__slots__:
        object.__setattr__(clone, name, getattr(value, name))
    return clone


def _forwarding_proxy(value: object, *, module_name: str, class_name: str) -> object:
    def forwarded(self: object, name: str) -> object:
        return getattr(self._target, name)

    proxy_class = type(
        class_name,
        (),
        {
            "__module__": module_name,
            "__qualname__": class_name,
            "__slots__": ("_target",),
            "__getattr__": forwarded,
        },
    )
    proxy = object.__new__(proxy_class)
    object.__setattr__(proxy, "_target", value)
    return proxy


def _binding_map(snapshot: object) -> dict[str, dict[str, str]]:
    return {
        item.source_id: item.to_package_dict()
        for item in snapshot.source_artifact_bindings
    }


def _assert_failure_result(
    result: object,
    expected_code: str,
    *forbidden_values: object,
) -> None:
    assert type(result) is builder._WS01bResult
    assert result.ok is False
    assert result.value is None
    assert result.reason_code == expected_code
    assert not isinstance(result, BaseException)
    assert not hasattr(result, "__dict__")
    assert not hasattr(result, "__traceback__")
    assert not hasattr(result, "__cause__")
    assert not hasattr(result, "__context__")
    assert type(result).__slots__ == ("ok", "value", "reason_code")
    rendered = repr(result)
    for forbidden in forbidden_values:
        assert str(forbidden) not in rendered
    with pytest.raises((AttributeError, TypeError)):
        result.value = object()


def _assert_no_reachable_exception(value: object) -> None:
    pending = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BaseException):
            BaseException.__getattribute__(current, "__traceback__")
            pytest.fail("an exception object is reachable from a WS01b result")
        if type(current) in {tuple, list, frozenset}:
            pending.extend(current)
        elif isinstance(current, MappingProxyType):
            pending.extend(current.values())
        else:
            for slot in getattr(type(current), "__slots__", ()):
                if hasattr(current, slot):
                    pending.append(getattr(current, slot))


def _active_record(package: object) -> dict[str, Any]:
    return next(
        copy.deepcopy(record)
        for record in package.to_dict()["evidence_records"]
        if record["value_type"] == "active_anchor_v1"
    )


def _rebuilt_package(
    package: object, payload: dict[str, Any]
) -> object:
    payload["input_package_identity_sha256"] = contracts.compute_identity(
        "input_package",
        payload,
        exclude_fields=("input_package_identity_sha256",),
    )
    canonical = contracts.canonical_json_bytes(payload)
    forged = object.__new__(builder._AnalystInputPackage)
    object.__setattr__(forged, "_payload", builder._deep_freeze(copy.deepcopy(payload)))
    object.__setattr__(forged, "_schema", package._schema)
    object.__setattr__(
        forged,
        "_authenticated_contract_surface",
        package._authenticated_contract_surface,
    )
    object.__setattr__(
        forged,
        "authenticated_contract_surface_seal_sha256",
        package.authenticated_contract_surface_seal_sha256,
    )
    object.__setattr__(forged, "canonical_json_bytes", canonical)
    object.__setattr__(
        forged,
        "input_package_identity_sha256",
        payload["input_package_identity_sha256"],
    )
    return forged


def _identity_consistent_forged_package(
    package: object,
    payload: dict[str, Any],
    *,
    rebuild_records: bool = False,
) -> object:
    if rebuild_records:
        binding_by_role = {
            binding["source_id"]: dict(binding)
            for binding in payload["source_artifact_bindings"]
        }
        record_id_map: dict[str, str] = {}
        rebuilt_records = []
        for record in payload["evidence_records"]:
            normalized_value = record.get(
                "normalized_value", builder._NO_NORMALIZED_VALUE
            )
            rebuilt = builder._build_evidence_record(
                source_generation_id=payload["source_generation_id"],
                source_generation_version=payload["source_generation_version"],
                binding_by_role=binding_by_role,
                value_type=record["value_type"],
                source_locator=copy.deepcopy(record["source_locator"]),
                normalized_value=copy.deepcopy(normalized_value),
            )
            record_id_map[record["evidence_record_id"]] = rebuilt[
                "evidence_record_id"
            ]
            rebuilt_records.append(rebuilt)
        payload["evidence_records"] = rebuilt_records
        for field in (
            "availability_diagnostic_record_ids",
            "freshness_diagnostic_record_ids",
        ):
            payload[field] = [record_id_map[value] for value in payload[field]]
    run_payload = {
        "payload_kind": "weekly_shadow_01_run_locator_v1",
        "adapter_id": payload["adapter_id"],
        "adapter_version": payload["adapter_version"],
        "source_generation_id": payload["source_generation_id"],
        "source_generation_version": payload["source_generation_version"],
        "evaluation_timestamp_utc": payload["evaluation_timestamp_utc"],
        "contract_catalog_identity_sha256": payload[
            "contract_catalog_identity_sha256"
        ],
    }
    payload["run_id"] = "ws01run-" + contracts.compute_identity("run", run_payload)
    return _rebuilt_package(package, payload)


def _package_with_authority(
    package: object,
    *,
    authenticated_surface: object | None = None,
    surface_seal: str | None = None,
) -> object:
    forged = object.__new__(builder._AnalystInputPackage)
    object.__setattr__(forged, "_payload", package._payload)
    object.__setattr__(forged, "_schema", package._schema)
    object.__setattr__(
        forged,
        "_authenticated_contract_surface",
        (
            package._authenticated_contract_surface
            if authenticated_surface is None
            else authenticated_surface
        ),
    )
    object.__setattr__(
        forged,
        "authenticated_contract_surface_seal_sha256",
        (
            package.authenticated_contract_surface_seal_sha256
            if surface_seal is None
            else surface_seal
        ),
    )
    object.__setattr__(forged, "canonical_json_bytes", package.canonical_json_bytes)
    object.__setattr__(
        forged,
        "input_package_identity_sha256",
        package.input_package_identity_sha256,
    )
    return forged


def _self_consistent_changed_surface(
    package: object,
    *,
    runtime_mutation,
    complete_mutation,
) -> object:
    authenticated = package._authenticated_contract_surface
    runtime_surface = adapter._deep_thaw(authenticated.runtime_surface)
    complete_surface = adapter._deep_thaw(authenticated.complete_surface)
    runtime_mutation(runtime_surface)
    complete_mutation(complete_surface)
    complete_surface["runtime_surface_sha256"] = hashlib.sha256(
        adapter._canonical_ws01_json_bytes(runtime_surface)
    ).hexdigest()
    seal = adapter._compute_authenticated_surface_seal(complete_surface)
    return adapter._AuthenticatedContractSurface(
        complete_surface=adapter._deep_freeze(complete_surface),
        runtime_surface=adapter._deep_freeze(runtime_surface),
        catalog_identity_sha256=authenticated.catalog_identity_sha256,
        seal_sha256=seal,
    )


def test_package_conforms_exactly_to_actual_committed_analyst_input_v2_schema(
    package_context: tuple[object, object],
) -> None:
    snapshot, package = package_context
    payload = package.to_dict()
    schema = json.loads(
        (Path(__file__).parents[2] / contracts.SCHEMA_FILENAME_BY_VERSION[
            "weekly_shadow_01_analyst_input_v2"
        ]).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(payload)
    assert payload["schema_version"] == "weekly_shadow_01_analyst_input_v2"
    assert payload["adapter_id"] == "legacy_r2f_v2_to_weekly_shadow_v1"
    assert payload["adapter_version"] == "legacy_r2f_adapter_v1"
    assert payload["source_generation_id"] == snapshot.source_generation_id
    assert package.canonical_json_bytes == contracts.canonical_json_bytes(payload)


def test_built_package_retains_exact_authenticated_surface_instance_and_seal(
    package_context: tuple[object, object],
) -> None:
    snapshot, _ = package_context
    package = _build_package(snapshot)
    assert (
        package._authenticated_contract_surface
        is snapshot.authenticated_contract_surface
    )
    assert package.authenticated_contract_surface_seal_sha256 == (
        snapshot.authenticated_contract_surface.seal_sha256
    )
    assert snapshot.contract_surface is (
        package._authenticated_contract_surface.runtime_surface
    )


def test_arbitrary_mapping_and_direct_package_construction_cannot_render(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    _assert_failure_result(
        builder.render_analyst_prompt(package.to_dict()),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )
    assert not hasattr(builder, "AnalystInputPackage")
    with pytest.raises(TypeError):
        builder._AnalystInputPackage(package.to_dict())


@pytest.mark.parametrize(
    ("forgery", "schema_valid"),
    (
        ("prompt_template_identity_sha256", True),
        ("contract_catalog_identity_sha256", True),
        ("resource_bound_profile_identity_sha256", True),
        ("adapter_id", False),
        ("adapter_version", True),
        ("source_generation_version", False),
        ("negative_authority", False),
        ("permitted_question_ids", True),
        ("prohibited_conclusion_ids", True),
    ),
)
def test_identity_consistent_frozen_package_forgery_is_rejected_before_render(
    package_context: tuple[object, object],
    forgery: str,
    schema_valid: bool,
) -> None:
    _, package = package_context
    payload = package.to_dict()
    rebuild_records = False
    if forgery in {
        "prompt_template_identity_sha256",
        "contract_catalog_identity_sha256",
        "resource_bound_profile_identity_sha256",
    }:
        payload[forgery] = "0" * 64
    elif forgery == "adapter_id":
        payload[forgery] = "forged_adapter_v1"
    elif forgery == "adapter_version":
        payload[forgery] = "legacy_r2f_adapter_v2"
    elif forgery == "source_generation_version":
        payload[forgery] = "step1_replacement_render_observation_v3"
        rebuild_records = True
    elif forgery == "negative_authority":
        payload[forgery]["approval_eligible"] = True
    elif forgery == "permitted_question_ids":
        payload[forgery] = ["explain_grounding"]
    elif forgery == "prohibited_conclusion_ids":
        payload[forgery] = list(reversed(payload[forgery]))
    else:
        raise AssertionError(forgery)
    forged = _identity_consistent_forged_package(
        package,
        payload,
        rebuild_records=rebuild_records,
    )
    assert forged.input_package_identity_sha256 == contracts.compute_identity(
        "input_package",
        forged.to_dict(),
        exclude_fields=("input_package_identity_sha256",),
    )
    schema = adapter._deep_thaw(package._schema)
    assert Draft202012Validator(schema).is_valid(forged.to_dict()) is schema_valid
    _assert_failure_result(
        _render_result(forged),
        "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
    )


def test_multi_field_identity_consistent_forgery_returns_no_render_result(
    package_context: tuple[object, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, package = package_context
    payload = package.to_dict()
    payload["prompt_template_identity_sha256"] = "0" * 64
    payload["contract_catalog_identity_sha256"] = "1" * 64
    payload["resource_bound_profile_identity_sha256"] = "2" * 64
    forged = _identity_consistent_forged_package(package, payload)
    monkeypatch.setattr(
        builder,
        "_new_rendered_analyst_prompt",
        lambda *_args, **_kwargs: pytest.fail("partial rendered result created"),
    )
    _assert_failure_result(
        _render_result(forged),
        "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
    )


def test_surface_seal_and_different_authenticated_surface_are_rejected(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    bad_seal_package = _package_with_authority(package, surface_seal="0" * 64)
    _assert_failure_result(
        _render_result(bad_seal_package),
        "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
    )

    authenticated = package._authenticated_contract_surface
    different_surface = adapter._AuthenticatedContractSurface(
        complete_surface=authenticated.complete_surface,
        runtime_surface=authenticated.runtime_surface,
        catalog_identity_sha256=authenticated.catalog_identity_sha256,
        seal_sha256="0" * 64,
    )
    different_package = _package_with_authority(
        package,
        authenticated_surface=different_surface,
        surface_seal=different_surface.seal_sha256,
    )
    _assert_failure_result(
        _render_result(different_package),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    )


def test_changed_authenticated_prompt_or_resource_surface_cannot_render(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    changed_prompt = _self_consistent_changed_surface(
        package,
        runtime_mutation=lambda value: value.__setitem__(
            "prompt_template_text",
            value["prompt_template_text"].replace("WEEKLY", "FORGED", 1),
        ),
        complete_mutation=lambda value: value["prompt_template"].__setitem__(
            "text",
            value["prompt_template"]["text"].replace("WEEKLY", "FORGED", 1),
        ),
    )
    changed_resource = _self_consistent_changed_surface(
        package,
        runtime_mutation=lambda value: value["resource_bound_profile"].__setitem__(
            "analyst_input_max_bytes", 524_287
        ),
        complete_mutation=lambda value: value["profile_identity_payloads"][
            "resource_bound"
        ].__setitem__("analyst_input_max_bytes", 524_287),
    )
    for changed in (changed_prompt, changed_resource):
        forged = _package_with_authority(
            package,
            authenticated_surface=changed,
            surface_seal=changed.seal_sha256,
        )
        _assert_failure_result(
            _render_result(forged),
            "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        )


def test_package_owns_source_context_once_and_records_carry_no_duplicate_lineage(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    payload = package.to_dict()
    assert tuple(item["source_id"] for item in payload["source_artifact_bindings"]) == (
        "replacement_input_manifest.json",
        "evidence_packet.json",
        "analyst_memo_prompt.txt",
        "render_generation_binding.json",
    )
    for record in payload["evidence_records"]:
        assert not {
            "source_generation_id",
            "source_generation_version",
            "source_lineage",
            "source_artifact_identity_sha256",
            "source_field_bindings",
        } & set(record)


def test_active_anchor_projection_is_closed_value_bearing_and_preserves_ticker_order(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    records = [
        record
        for record in package.to_dict()["evidence_records"]
        if record["value_type"] == "active_anchor_v1"
    ]
    assert [record["source_locator"]["anchor_id"] for record in records] == [
        "AAA_ANCHOR",
        "ZZZ_ANCHOR",
    ]
    assert records[1]["normalized_value"]["applicable_tickers"] == ["FIXB", "FIXA"]
    assert set(records[0]["source_locator"]) == {
        "locator_type",
        "source_artifact_role",
        "anchor_id",
    }
    assert set(records[0]["normalized_value"]) == {
        "applicable_tickers",
        "anchor_date_et",
        "valid_from",
        "valid_until",
        "confidence_floor",
        "summary",
        "validation",
    }
    assert "anchor_id" not in records[0]["normalized_value"]


def test_availability_projection_uses_exact_closed_subjects_and_source_values(
    package_context: tuple[object, object],
) -> None:
    snapshot, package = package_context
    records = [
        record
        for record in package.to_dict()["evidence_records"]
        if record["value_type"] == "availability_status_v1"
    ]
    assert [record["source_locator"]["availability_subject"] for record in records] == [
        "market_metrics",
        "scheduled_events_deterministic",
    ]
    assert [record["normalized_value"] for record in records] == [
        dict(item["normalized_value"]) for item in snapshot.availability_statuses
    ]
    assert all(record["normalized_value"]["available"] is False for record in records)
    assert all(type(record["normalized_value"]["data_gap"]) is str for record in records)


def test_empty_registry_diagnostic_is_closed_and_has_no_normalized_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    _setup_repo(root, anchors=[])
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    generation = r2f.replacement_render()
    package = _expect_success(
        builder.build_analyst_input_package(
            generation["generation_id"], repository_root=root
        )
    )
    record = next(
        record
        for record in package.to_dict()["evidence_records"]
        if record["value_type"] == "diagnostic_code_v1"
    )
    assert record["source_locator"] == {
        "locator_type": "manifest_diagnostic",
        "source_artifact_role": "replacement_input_manifest.json",
        "diagnostic_code": "EMPTY_ACTIVE_REGISTRY",
    }
    assert "normalized_value" not in record


def test_no_prohibited_source_or_action_fields_are_projected(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    serialized = package.canonical_json_bytes.decode("utf-8").casefold()
    for term in (
        "allowed_buy_tickers",
        "budget_settings",
        "portfolio_snapshot",
        "operator_editable",
        "analyst_memo_raw_output",
        "approval_eligible\":true",
        "order_eligible\":true",
        "execution_authority\":true",
    ):
        assert term not in serialized
    assert "run_status" not in serialized
    assert "validation_status" not in serialized
    assert "publication_status" not in serialized


def test_locator_id_is_value_insensitive_but_record_identity_is_value_sensitive(
    package_context: tuple[object, object],
) -> None:
    snapshot, package = package_context
    original = _active_record(package)
    changed_value = copy.deepcopy(original["normalized_value"])
    changed_value["summary"] = "A different exact source value."
    changed = builder._build_evidence_record(
        source_generation_id=snapshot.source_generation_id,
        source_generation_version=snapshot.source_generation_version,
        binding_by_role=_binding_map(snapshot),
        value_type=original["value_type"],
        source_locator=original["source_locator"],
        normalized_value=changed_value,
    )
    assert changed["evidence_record_id"] == original["evidence_record_id"]
    assert changed["evidence_record_identity_sha256"] != (
        original["evidence_record_identity_sha256"]
    )


def test_locator_artifact_and_generation_mutations_change_frozen_identities(
    package_context: tuple[object, object],
) -> None:
    snapshot, package = package_context
    original = _active_record(package)
    locator = copy.deepcopy(original["source_locator"])
    locator["anchor_id"] = "DIFFERENT_ANCHOR"
    locator_changed = builder._build_evidence_record(
        source_generation_id=snapshot.source_generation_id,
        source_generation_version=snapshot.source_generation_version,
        binding_by_role=_binding_map(snapshot),
        value_type="active_anchor_v1",
        source_locator=locator,
        normalized_value=original["normalized_value"],
    )
    artifact_bindings = _binding_map(snapshot)
    artifact_bindings["evidence_packet.json"] = {
        "source_id": "evidence_packet.json",
        "source_artifact_identity_sha256": "f" * 64,
    }
    artifact_changed = builder._build_evidence_record(
        source_generation_id=snapshot.source_generation_id,
        source_generation_version=snapshot.source_generation_version,
        binding_by_role=artifact_bindings,
        value_type="active_anchor_v1",
        source_locator=original["source_locator"],
        normalized_value=original["normalized_value"],
    )
    generation_changed = builder._build_evidence_record(
        source_generation_id="f" * 64,
        source_generation_version=snapshot.source_generation_version,
        binding_by_role=_binding_map(snapshot),
        value_type="active_anchor_v1",
        source_locator=original["source_locator"],
        normalized_value=original["normalized_value"],
    )
    for changed in (locator_changed, artifact_changed, generation_changed):
        assert changed["evidence_record_id"] != original["evidence_record_id"]
        assert changed["evidence_record_identity_sha256"] != (
            original["evidence_record_identity_sha256"]
        )


def test_authority_and_record_id_mutations_change_record_identity() -> None:
    record = {
        "evidence_record_id": "ws01ev-" + "a" * 64,
        "value_type": "diagnostic_code_v1",
        "source_locator": {
            "locator_type": "manifest_diagnostic",
            "source_artifact_role": "replacement_input_manifest.json",
            "diagnostic_code": "EMPTY_ACTIVE_REGISTRY",
        },
        "authority_effect": "none",
    }
    context = {
        "payload_kind": "weekly_shadow_01_evidence_record_identity_v2",
        "source_generation_id": "1" * 64,
        "source_generation_version": "step1_replacement_render_observation_v2",
        "resolved_source_artifact_binding": {
            "source_id": "replacement_input_manifest.json",
            "source_artifact_identity_sha256": "2" * 64,
        },
    }
    baseline = contracts.compute_identity(
        "evidence_record", {**context, "evidence_record": record}
    )
    for field, value in (("authority_effect", "trade"), ("evidence_record_id", "ws01ev-" + "b" * 64)):
        changed = copy.deepcopy(record)
        changed[field] = value
        assert contracts.compute_identity(
            "evidence_record", {**context, "evidence_record": changed}
        ) != baseline


def test_identity_consistent_duplicate_logical_locator_fails_closed(
    package_context: tuple[object, object],
) -> None:
    snapshot, package = package_context
    first = _active_record(package)
    changed_value = copy.deepcopy(first["normalized_value"])
    changed_value["summary"] = "Contradictory second value."
    second = builder._build_evidence_record(
        source_generation_id=snapshot.source_generation_id,
        source_generation_version=snapshot.source_generation_version,
        binding_by_role=_binding_map(snapshot),
        value_type=first["value_type"],
        source_locator=first["source_locator"],
        normalized_value=changed_value,
    )
    assert first["evidence_record_id"] == second["evidence_record_id"]
    assert first["evidence_record_identity_sha256"] != second["evidence_record_identity_sha256"]
    with pytest.raises(builder._PackageBuilderFailure) as caught:
        builder._canonicalize_evidence_records(
            [first, second],
            source_generation_id=snapshot.source_generation_id,
            source_generation_version=snapshot.source_generation_version,
            binding_by_role=_binding_map(snapshot),
            reject_noncanonical=False,
        )
    assert caught.value.code == "WS01_BR_PACKAGE_CONSTRUCTION_FAILED"


@pytest.mark.parametrize(
    "value_type",
    ["active_anchor_v1", "availability_status_v1", "diagnostic_code_v1"],
)
def test_duplicate_variant_locators_and_record_ids_fail_closed(
    package_context: tuple[object, object],
    value_type: str,
) -> None:
    snapshot, package = package_context
    record = next(
        copy.deepcopy(item)
        for item in package.to_dict()["evidence_records"]
        if item["value_type"] == value_type
    ) if value_type != "diagnostic_code_v1" else builder._build_evidence_record(
        source_generation_id=snapshot.source_generation_id,
        source_generation_version=snapshot.source_generation_version,
        binding_by_role=_binding_map(snapshot),
        value_type="diagnostic_code_v1",
        source_locator={
            "locator_type": "manifest_diagnostic",
            "source_artifact_role": "replacement_input_manifest.json",
            "diagnostic_code": "EMPTY_ACTIVE_REGISTRY",
        },
        normalized_value=builder._NO_NORMALIZED_VALUE,
    )
    with pytest.raises(builder._PackageBuilderFailure):
        builder._canonicalize_evidence_records(
            [record, copy.deepcopy(record)],
            source_generation_id=snapshot.source_generation_id,
            source_generation_version=snapshot.source_generation_version,
            binding_by_role=_binding_map(snapshot),
            reject_noncanonical=False,
        )


def test_canonical_order_is_exact_total_order_and_strictly_increasing(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    records = package.to_dict()["evidence_records"]
    keys = [
        (
            contracts.EVIDENCE_VARIANT_RANKS[record["value_type"]],
            contracts.canonical_json_bytes(record["source_locator"]),
            record["evidence_record_id"],
        )
        for record in records
    ]
    assert keys == sorted(keys)
    assert all(left < right for left, right in zip(keys, keys[1:]))
    assert contracts.EVIDENCE_VARIANT_RANKS == {
        "active_anchor_v1": 0,
        "availability_status_v1": 1,
        "diagnostic_code_v1": 2,
    }


def test_identity_valid_noncanonical_permutation_is_rejected_not_reordered(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    payload = package.to_dict()
    payload["evidence_records"][0], payload["evidence_records"][1] = (
        payload["evidence_records"][1],
        payload["evidence_records"][0],
    )
    payload["freshness_diagnostic_record_ids"] = [
        record["evidence_record_id"]
        for record in payload["evidence_records"]
        if record["value_type"] == "active_anchor_v1"
    ]
    noncanonical = _rebuilt_package(package, payload)
    _assert_failure_result(
        _render_result(noncanonical),
        "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
    )


def test_diagnostic_references_are_complete_typed_disjoint_and_canonical(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    payload = package.to_dict()
    by_id = {record["evidence_record_id"]: record for record in payload["evidence_records"]}
    assert not set(payload["availability_diagnostic_record_ids"]) & set(
        payload["freshness_diagnostic_record_ids"]
    )
    assert [by_id[value]["value_type"] for value in payload["availability_diagnostic_record_ids"]] == [
        "availability_status_v1",
        "availability_status_v1",
    ]
    assert all(
        by_id[value]["value_type"] == "active_anchor_v1"
        for value in payload["freshness_diagnostic_record_ids"]
    )


def test_package_identity_binds_values_diagnostics_catalog_profiles_adapter_and_time(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    baseline = package.input_package_identity_sha256
    mutations = (
        lambda value: value["evidence_records"][0]["normalized_value"].__setitem__("summary", "changed"),
        lambda value: value["freshness_diagnostic_record_ids"].reverse(),
        lambda value: value.__setitem__("contract_catalog_identity_sha256", "f" * 64),
        lambda value: value.__setitem__("resource_bound_profile_identity_sha256", "f" * 64),
        lambda value: value.__setitem__("prompt_template_identity_sha256", "f" * 64),
        lambda value: value.__setitem__("adapter_version", "legacy_r2f_adapter_v2"),
        lambda value: value.__setitem__("source_generation_id", "f" * 64),
        lambda value: value["source_artifact_bindings"][0].__setitem__(
            "source_artifact_identity_sha256", "f" * 64
        ),
        lambda value: value["negative_authority"].__setitem__(
            "authority_effect", "trade"
        ),
        lambda value: value.__setitem__("evaluation_timestamp_utc", "2026-07-13T00:00:00Z"),
    )
    for mutate in mutations:
        changed = package.to_dict()
        mutate(changed)
        assert contracts.compute_identity(
            "input_package",
            changed,
            exclude_fields=("input_package_identity_sha256",),
        ) != baseline


def test_exact_and_one_over_summary_and_ticker_bounds_fail_without_truncation() -> None:
    base = {
        "anchor_id": "BOUNDARY",
        "applicable_tickers": ["T0"],
        "anchor_date_et": None,
        "valid_from": None,
        "valid_until": None,
        "confidence_floor": "low",
        "summary": "x" * 2_048,
        "validation": {"stale": False},
    }
    assert adapter._project_active_anchor(base)["normalized_value"]["summary"] == "x" * 2_048
    too_long = copy.deepcopy(base)
    too_long["summary"] = "x" * 2_049
    with pytest.raises(adapter._SourceAdapterFailure) as summary:
        adapter._project_active_anchor(too_long)
    assert summary.value.code == "WS01_BR_RESOURCE_BOUND_EXCEEDED"
    exact_tickers = copy.deepcopy(base)
    exact_tickers["applicable_tickers"] = [f"T{index}" for index in range(1_017)]
    assert len(adapter._project_active_anchor(exact_tickers)["normalized_value"]["applicable_tickers"]) == 1_017
    too_many = copy.deepcopy(base)
    too_many["applicable_tickers"] = [f"T{index}" for index in range(1_018)]
    with pytest.raises(adapter._SourceAdapterFailure) as tickers:
        adapter._project_active_anchor(too_many)
    assert tickers.value.code == "WS01_BR_RESOURCE_BOUND_EXCEEDED"


def test_exact_and_one_over_evidence_record_count_bound(
    package_context: tuple[object, object],
) -> None:
    snapshot, package = package_context
    template = _active_record(package)
    records = []
    for index in range(257):
        locator = copy.deepcopy(template["source_locator"])
        locator["anchor_id"] = f"BOUND-{index:03d}"
        records.append(
            builder._build_evidence_record(
                source_generation_id=snapshot.source_generation_id,
                source_generation_version=snapshot.source_generation_version,
                binding_by_role=_binding_map(snapshot),
                value_type="active_anchor_v1",
                source_locator=locator,
                normalized_value=template["normalized_value"],
            )
        )
    assert len(
        builder._canonicalize_evidence_records(
            records[:256],
            source_generation_id=snapshot.source_generation_id,
            source_generation_version=snapshot.source_generation_version,
            binding_by_role=_binding_map(snapshot),
            reject_noncanonical=False,
        )
    ) == 256
    with pytest.raises(builder._PackageBuilderFailure) as caught:
        builder._canonicalize_evidence_records(
            records,
            source_generation_id=snapshot.source_generation_id,
            source_generation_version=snapshot.source_generation_version,
            binding_by_role=_binding_map(snapshot),
            reject_noncanonical=False,
        )
    assert caught.value.code == "WS01_BR_RESOURCE_BOUND_EXCEEDED"


def test_canonical_package_byte_bound_is_enforced_at_runtime(
    package_context: tuple[object, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, package = package_context
    profile = dict(builder._RESOURCE_BOUND_PROFILE)
    profile["analyst_input_max_bytes"] = len(package.canonical_json_bytes) - 1
    monkeypatch.setattr(builder, "_RESOURCE_BOUND_PROFILE", MappingProxyType(profile))
    _assert_failure_result(
        _render_result(package),
        "WS01_BR_RESOURCE_BOUND_EXCEEDED",
    )


def test_rendered_prompt_and_aggregate_analyst_text_bounds_fail_closed(
    package_context: tuple[object, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, package = package_context
    rendered = _render_prompt(package)
    profile = dict(builder._RESOURCE_BOUND_PROFILE)
    profile["rendered_prompt_max_bytes"] = len(rendered.prompt_bytes) - 1
    monkeypatch.setattr(builder, "_RESOURCE_BOUND_PROFILE", MappingProxyType(profile))
    _assert_failure_result(
        _render_result(package),
        "WS01_BR_RESOURCE_BOUND_EXCEEDED",
    )

    profile["rendered_prompt_max_bytes"] = contracts.RESOURCE_BOUND_PROFILE[
        "rendered_prompt_max_bytes"
    ]
    profile["max_aggregate_analyst_text_code_points"] = 1
    monkeypatch.setattr(builder, "_RESOURCE_BOUND_PROFILE", MappingProxyType(profile))
    _assert_failure_result(
        _render_result(package),
        "WS01_BR_RESOURCE_BOUND_EXCEEDED",
    )


def test_cross_array_diagnostic_duplicate_and_union_overflow_fail_closed(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    records = package.to_dict()["evidence_records"]
    record_id = records[0]["evidence_record_id"]
    with pytest.raises(builder._PackageBuilderFailure) as duplicate:
        builder._validate_diagnostic_references(
            records=records,
            availability_ids=[record_id],
            freshness_ids=[record_id],
        )
    assert duplicate.value.code == "WS01_BR_RESOURCE_BOUND_EXCEEDED"
    with pytest.raises(builder._PackageBuilderFailure) as union:
        builder._validate_diagnostic_references(
            records=records,
            availability_ids=[f"ws01ev-{index:064x}" for index in range(257)],
            freshness_ids=[],
        )
    assert union.value.code == "WS01_BR_RESOURCE_BOUND_EXCEEDED"


def test_runtime_tree_member_item_and_nesting_bounds_are_exact() -> None:
    builder._validate_json_resource_tree(
        {f"k{index}": None for index in range(1_024)}, depth=1
    )
    with pytest.raises(builder._PackageBuilderFailure) as members:
        builder._validate_json_resource_tree(
            {f"k{index}": None for index in range(1_025)}, depth=1
        )
    assert members.value.code == "WS01_BR_RESOURCE_BOUND_EXCEEDED"

    builder._validate_json_resource_tree([None] * 1_024, depth=1)
    with pytest.raises(builder._PackageBuilderFailure) as items:
        builder._validate_json_resource_tree([None] * 1_025, depth=1)
    assert items.value.code == "WS01_BR_RESOURCE_BOUND_EXCEEDED"

    exact: object = None
    for _ in range(15):
        exact = [exact]
    builder._validate_json_resource_tree(exact, depth=1)
    over = [exact]
    with pytest.raises(builder._PackageBuilderFailure) as nesting:
        builder._validate_json_resource_tree(over, depth=1)
    assert nesting.value.code == "WS01_BR_RESOURCE_BOUND_EXCEEDED"


def test_identity_valid_run_id_mutation_is_rejected_as_noncanonical(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    payload = package.to_dict()
    payload["run_id"] = "ws01run-" + "f" * 64
    changed = _rebuilt_package(package, payload)
    _assert_failure_result(
        _render_result(changed),
        "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
    )


def test_prompt_render_is_exact_utf8_lf_no_bom_single_insertion_and_final_newline(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    rendered = _render_prompt(package)
    placeholder = contracts.PROMPT_TEMPLATE_PLACEHOLDER.encode("utf-8")
    expected = contracts.PROMPT_TEMPLATE_BYTES.replace(
        placeholder, package.canonical_json_bytes
    )
    assert rendered.prompt_bytes == expected
    assert rendered.prompt_bytes.count(package.canonical_json_bytes) == 1
    assert placeholder not in rendered.prompt_bytes
    assert not rendered.prompt_bytes.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in rendered.prompt_bytes
    assert rendered.prompt_bytes.endswith(b"\n")
    assert not rendered.prompt_bytes.endswith(b"\n\n")
    rendered.prompt_bytes.decode("utf-8", errors="strict")


def test_prompt_binding_and_identity_are_independently_recomputed(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    rendered = _render_prompt(package)
    binding = dict(rendered.binding)
    expected_payload = {
        "payload_kind": "weekly_shadow_01_prompt_render_v1",
        "input_package_identity_sha256": package.input_package_identity_sha256,
        "prompt_template_identity_sha256": contracts.PROMPT_TEMPLATE_IDENTITY_SHA256,
        "rendered_prompt_byte_size": len(rendered.prompt_bytes),
        "rendered_prompt_sha256": hashlib.sha256(rendered.prompt_bytes).hexdigest(),
    }
    assert binding["prompt_render_identity_sha256"] == contracts.compute_identity(
        "prompt_render", expected_payload
    )
    surface = package._authenticated_contract_surface
    assert binding["prompt_template_identity_sha256"] == (
        surface.runtime_surface["prompt_template_identity_sha256"]
    )
    assert binding["contract_catalog_identity_sha256"] == (
        surface.catalog_identity_sha256
    )
    assert binding["resource_bound_profile_identity_sha256"] == (
        surface.runtime_surface["resource_bound_profile_identity_sha256"]
    )
    assert binding["authenticated_contract_surface_seal_sha256"] == (
        surface.seal_sha256
    )
    assert binding["authority_effect"] == "none"


def test_package_and_prompt_are_deeply_immutable_and_caller_mutation_safe(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    before = package.input_package_identity_sha256
    with pytest.raises(TypeError):
        package.payload["run_id"] = "mutated"
    detached = package.to_dict()
    detached["run_id"] = "mutated"
    first = _render_prompt(package)
    detached["prompt_template_identity_sha256"] = "0" * 64
    second = _render_prompt(package)
    with pytest.raises(TypeError):
        first.binding["authority_effect"] = "trade"
    assert package.input_package_identity_sha256 == before
    assert package["run_id"] != "mutated"
    assert first.prompt_bytes == second.prompt_bytes
    assert first.binding == second.binding


def test_runtime_builder_and_renderer_perform_no_filesystem_writes_or_model_calls(
    package_context: _PackageContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, package = package_context
    monkeypatch.setattr(Path, "write_bytes", lambda *_a, **_k: pytest.fail("write"))
    monkeypatch.setattr(Path, "write_text", lambda *_a, **_k: pytest.fail("write"))
    rebuilt = _expect_success(_build_public(package_context))
    rendered = _expect_success(_render_public(package_context))
    assert rebuilt.input_package_identity_sha256 == package.input_package_identity_sha256
    assert rendered.prompt_bytes


def test_identities_are_stable_across_hash_seed_locale_timezone_cwd_and_relocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    _setup_repo(root, anchors=[_anchor("STABLE_ANCHOR", ["FIXB", "FIXA"])])
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    generation_id = r2f.replacement_render()["generation_id"]
    relocated = tmp_path / "relocated" / "repo"
    import shutil

    shutil.copytree(root, relocated)
    script = """
import json
import sys
from investment_orchestrator.observability import weekly_shadow_01_package_builder as b
from investment_orchestrator.observability import weekly_shadow_01_source_adapter as a
verified = a.verify_r2f_v2_generation(sys.argv[2], repository_root=sys.argv[1])
snapshot = a.build_source_snapshot(sys.argv[2], repository_root=sys.argv[1])
package = b.build_analyst_input_package(sys.argv[2], repository_root=sys.argv[1])
prompt = b.render_analyst_prompt(sys.argv[2], repository_root=sys.argv[1])
assert verified.ok and snapshot.ok and package.ok and prompt.ok
print(json.dumps([snapshot.value.snapshot_identity_sha256, package.value.input_package_identity_sha256, prompt.value.prompt_render_identity_sha256]))
"""
    outputs = []
    for seed, timezone, source_root, cwd in (
        ("1", "UTC", root, root),
        ("987654", "America/Los_Angeles", relocated, Path("/")),
    ):
        environment = dict(os.environ)
        environment.update(
            {
                "PYTHONHASHSEED": seed,
                "LC_ALL": "C",
                "TZ": timezone,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPYCACHEPREFIX": "/tmp/ws01b-pycache",
            }
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(source_root), generation_id],
            cwd=cwd,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1]


def test_module_toplevels_have_no_io_network_subprocess_environment_or_registration() -> None:
    root = Path(__file__).parents[2]
    for filename in (
        "weekly_shadow_01_source_adapter.py",
        "weekly_shadow_01_package_builder.py",
    ):
        tree = ast.parse(
            (root / "src/investment_orchestrator/observability" / filename).read_text(
                encoding="utf-8"
            )
        )
        top_level = ast.Module(
            body=[
                node
                for node in tree.body
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ],
            type_ignores=[],
        )
        forbidden_calls = {
            "open",
            "read",
            "read_bytes",
            "read_text",
            "write",
            "write_bytes",
            "write_text",
            "mkdir",
            "system",
            "run",
            "Popen",
            "socket",
            "register",
            "publish",
        }
        assert not {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(top_level)
            if isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Attribute)
                or isinstance(node.func, ast.Name)
            )
            and (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
            )
            in forbidden_calls
        }


def test_production_dependency_direction_and_no_runtime_consumer_imports() -> None:
    root = Path(__file__).parents[2]
    relative_paths = (
        "src/investment_orchestrator/observability/weekly_shadow_01_source_adapter.py",
        "src/investment_orchestrator/observability/weekly_shadow_01_package_builder.py",
        "src/investment_orchestrator/observability/weekly_shadow_01_response_validator.py",
        "src/investment_orchestrator/observability/weekly_shadow_01_report_publisher.py",
    )
    sources: dict[str, gap._ParsedProductionSource] = {}
    for relative_path in relative_paths:
        path = root / relative_path
        module_name = gap._module_name_for_path(relative_path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        imports, findings, dynamic_imports = gap._imports_in_tree(tree, module_name)
        sources[module_name] = gap._ParsedProductionSource(
            relative_path=relative_path,
            module_name=module_name,
            tree=tree,
            imports=imports,
            dynamic_imports=dynamic_imports,
            findings=findings,
            report_reader=False,
            policy_reader=False,
            broker_capabilities=(),
        )

    adapter_module = (
        "investment_orchestrator.observability.weekly_shadow_01_source_adapter"
    )
    builder_module = (
        "investment_orchestrator.observability.weekly_shadow_01_package_builder"
    )
    validator_module = (
        "investment_orchestrator.observability.weekly_shadow_01_response_validator"
    )
    publisher_module = (
        "investment_orchestrator.observability.weekly_shadow_01_report_publisher"
    )
    adapter_tree = sources[adapter_module].tree
    builder_tree = sources[builder_module].tree
    validator_tree = sources[validator_module].tree
    publisher_tree = sources[publisher_module].tree
    builder_imports = {
        alias.name
        for node in ast.walk(builder_tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    adapter_imports = {
        alias.name
        for node in ast.walk(adapter_tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    adapter_module_bindings = [
        (node.module, tuple((alias.name, alias.asname) for alias in node.names))
        for node in builder_tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "investment_orchestrator.observability"
    ]
    assert adapter_module_bindings == [
        (
            "investment_orchestrator.observability",
            (("weekly_shadow_01_source_adapter", "_source_adapter"),),
        )
    ]
    assert "weekly_shadow_01_source_adapter" in builder_imports
    assert "weekly_shadow_01_contracts" not in builder_imports
    assert "weekly_shadow_01_contracts" not in adapter_imports
    validator_module_bindings = [
        (node.module, tuple((alias.name, alias.asname) for alias in node.names))
        for node in validator_tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "investment_orchestrator.observability"
    ]
    assert validator_module_bindings == [
        (
            "investment_orchestrator.observability",
            (("weekly_shadow_01_package_builder", "_package_builder"),),
        )
    ]
    publisher_module_bindings = [
        (node.module, tuple((alias.name, alias.asname) for alias in node.names))
        for node in publisher_tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "investment_orchestrator.observability"
    ]
    assert publisher_module_bindings == [
        (
            "investment_orchestrator.observability",
            (("weekly_shadow_01_response_validator", "_response_validator"),),
        )
    ]

    def observer_relations(module_name: str) -> tuple[gap._ClassifiedConsumerRelation, ...]:
        return tuple(
            relation
            for relation in gap._classify_consumer_relations(
                sources[module_name],
                sources=sources,
            )
            if relation.target_module in {
                adapter_module,
                builder_module,
                validator_module,
                publisher_module,
            }
        )

    builder_relations = observer_relations(builder_module)
    validator_relations = observer_relations(validator_module)
    publisher_relations = observer_relations(publisher_module)
    assert len(builder_relations) == 1
    assert len(validator_relations) == 1
    assert len(publisher_relations) == 1
    builder_relation = builder_relations[0]
    validator_relation = validator_relations[0]
    publisher_relation = publisher_relations[0]
    assert (
        builder_relation.category
        is gap._ConsumerRelationCategory.INTERNAL_IMPLEMENTATION_EDGE
    )
    assert (
        builder_relation.importer_relative_path
        == "src/investment_orchestrator/observability/weekly_shadow_01_package_builder.py"
    )
    assert builder_relation.importer_module == builder_module
    assert builder_relation.target_module == adapter_module
    assert builder_relation.lineno > 0
    assert builder_relation.col_offset == 0
    assert (
        validator_relation.category
        is gap._ConsumerRelationCategory.INTERNAL_IMPLEMENTATION_EDGE
    )
    assert (
        validator_relation.importer_relative_path
        == "src/investment_orchestrator/observability/weekly_shadow_01_response_validator.py"
    )
    assert validator_relation.importer_module == validator_module
    assert validator_relation.target_module == builder_module
    assert validator_relation.lineno > 0
    assert validator_relation.col_offset == 0
    assert (
        publisher_relation.category
        is gap._ConsumerRelationCategory.INTERNAL_IMPLEMENTATION_EDGE
    )
    assert (
        publisher_relation.importer_relative_path
        == "src/investment_orchestrator/observability/weekly_shadow_01_report_publisher.py"
    )
    assert publisher_relation.importer_module == publisher_module
    assert publisher_relation.target_module == validator_module
    assert publisher_relation.lineno > 0
    assert publisher_relation.col_offset == 0
    assert observer_relations(adapter_module) == ()

    inventory = gap._scan_production_inventory(root)
    assert inventory.observer_external_consumers == (
        "src/investment_orchestrator/cli/observe_ltetf_target_architecture_gaps.py",
    )
    assert inventory.dynamic_findings == ()
    assert inventory.report_artifact_readers == ()
    assert inventory.policy_artifact_consumers == ()
    assert inventory.prohibited_observer_capability_imports == ()
    assert inventory.p4a_runtime_consumers == ()
    assert inventory.broker_capability_imports == ()
    assert inventory.weekly_llm_invocation_markers == ()


def test_builder_public_results_are_immutable_code_only_and_unknown_codes_fail_closed(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    secret = "BUILDER-CALLER-SECRET-4B91"
    assert builder._BLOCKING_REASON_CODES == frozenset(
        {
            "WS01_BR_SOURCE_GENERATION_INVALID",
            "WS01_BR_SOURCE_ARTIFACT_SET_MISMATCH",
            "WS01_BR_SOURCE_VERSION_UNSUPPORTED",
            "WS01_BR_SOURCE_READ_UNSTABLE",
            "WS01_BR_SOURCE_BINDING_MISMATCH",
            "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
            "WS01_BR_RESOURCE_BOUND_EXCEEDED",
            "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        }
    )
    for reason_code in builder._BLOCKING_REASON_CODES:
        _assert_failure_result(
            builder._result_failure(reason_code),
            reason_code,
            secret,
        )
    _assert_failure_result(
        builder.render_analyst_prompt({secret: package.to_dict()}),
        "WS01_BR_SOURCE_GENERATION_INVALID",
        secret,
    )
    _assert_failure_result(
        builder._result_failure(secret),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        secret,
    )

    outer_secret = "BUILDER-OUTER-CONTEXT-SECRET-AB02"
    try:
        raise ValueError(outer_secret)
    except ValueError:
        nested_result = builder.render_analyst_prompt({secret: package.to_dict()})
    _assert_failure_result(
        nested_result,
        "WS01_BR_SOURCE_GENERATION_INVALID",
        secret,
        outer_secret,
    )
    _assert_no_reachable_exception(nested_result)


def test_builder_former_public_constructors_are_unavailable() -> None:
    result = builder._result_failure("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    with pytest.raises(TypeError):
        type(result)()
    assert not hasattr(builder, "WeeklyShadow01PackageBuilderError")
    assert not hasattr(builder, "AnalystInputPackage")
    assert not hasattr(builder, "RenderedAnalystPrompt")


def test_all_four_public_signatures_accept_only_primitive_source_selectors() -> None:
    operations = (
        adapter.verify_r2f_v2_generation,
        adapter.build_source_snapshot,
        builder.build_analyst_input_package,
        builder.render_analyst_prompt,
    )
    for operation in operations:
        signature = inspect.signature(operation)
        assert tuple(signature.parameters) == ("generation_id", "repository_root")
        assert signature.parameters["generation_id"].kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
        assert (
            signature.parameters["repository_root"].kind
            is inspect.Parameter.KEYWORD_ONLY
        )


def test_legacy_intermediate_positional_inputs_have_no_authority_route(
    package_context: _PackageContext,
) -> None:
    verified_result = adapter._result_success(package_context.verified)
    snapshot_result = adapter._result_success(package_context.snapshot)
    package_result = builder._result_success(package_context.package)
    cases = (
        (adapter.build_source_snapshot, verified_result, adapter._WS01bResult),
        (adapter.build_source_snapshot, package_context.verified, adapter._WS01bResult),
        (builder.build_analyst_input_package, snapshot_result, builder._WS01bResult),
        (builder.build_analyst_input_package, package_context.snapshot, builder._WS01bResult),
        (builder.render_analyst_prompt, package_result, builder._WS01bResult),
        (builder.render_analyst_prompt, package_context.package, builder._WS01bResult),
    )
    for operation, legacy_value, result_type in cases:
        result = operation(legacy_value)
        assert type(result) is result_type
        assert result.ok is False
        assert result.value is None
        assert result.reason_code == "WS01_BR_SOURCE_GENERATION_INVALID"


@pytest.mark.parametrize(
    ("operation", "legacy_keyword"),
    (
        (adapter.build_source_snapshot, "verified_result"),
        (builder.build_analyst_input_package, "snapshot_result"),
        (builder.render_analyst_prompt, "package_result"),
    ),
)
def test_legacy_keywords_and_additional_arguments_are_signature_rejected_without_values(
    package_context: _PackageContext,
    operation,
    legacy_keyword: str,
) -> None:
    secret = f"LEGACY-ARGUMENT-SECRET-{legacy_keyword}"
    with pytest.raises(TypeError) as legacy_error:
        operation(**{legacy_keyword: {secret: package_context.package}})
    assert secret not in str(legacy_error.value)
    with pytest.raises(TypeError) as positional_error:
        operation(package_context.generation_id, {secret: package_context.root})
    assert secret not in str(positional_error.value)
    with pytest.raises(TypeError) as keyword_error:
        operation(
            package_context.generation_id,
            repository_root=package_context.root,
            unexpected={secret: package_context.snapshot},
        )
    assert secret not in str(keyword_error.value)


def test_exact_class_reconstruction_copy_and_serialization_have_no_authority_route(
    package_context: _PackageContext,
) -> None:
    cases = (
        (adapter.build_source_snapshot, package_context.verified, adapter._WS01bResult),
        (builder.build_analyst_input_package, package_context.snapshot, builder._WS01bResult),
        (builder.render_analyst_prompt, package_context.package, builder._WS01bResult),
    )
    for operation, authentic_value, result_type in cases:
        reconstructed_values = [_exact_clone(authentic_value)]
        for copier in (copy.copy, copy.deepcopy):
            try:
                reconstructed_values.append(copier(authentic_value))
            except (TypeError, ValueError):
                pass
        try:
            serialized = pickle.dumps(authentic_value)
        except (AttributeError, TypeError, pickle.PicklingError):
            serialized = None
        if serialized is not None:
            reconstructed_values.append(serialized)
            try:
                reconstructed_values.append(pickle.loads(serialized))
            except (AttributeError, TypeError, pickle.UnpicklingError):
                pass
        assert type(reconstructed_values[0]) is type(authentic_value)
        for reconstructed in reconstructed_values:
            result = operation(reconstructed)
            assert type(result) is result_type
            assert result.ok is False
            assert result.value is None
            assert result.reason_code == "WS01_BR_SOURCE_GENERATION_INVALID"


def test_public_package_build_runs_one_private_verified_pipeline(
    package_context: _PackageContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = {"verify": 0, "snapshot": 0, "package": 0, "render": 0}
    originals = {
        "verify": builder._VERIFY_R2F_V2_GENERATION,
        "snapshot": builder._BUILD_SOURCE_SNAPSHOT,
        "package": builder._build_analyst_input_package,
        "render": builder._render_analyst_prompt,
    }

    def verify(*args: object, **kwargs: object) -> object:
        counts["verify"] += 1
        return originals["verify"](*args, **kwargs)

    def snapshot(*args: object, **kwargs: object) -> object:
        counts["snapshot"] += 1
        return originals["snapshot"](*args, **kwargs)

    def package(*args: object, **kwargs: object) -> object:
        counts["package"] += 1
        return originals["package"](*args, **kwargs)

    def render(*args: object, **kwargs: object) -> object:
        counts["render"] += 1
        return originals["render"](*args, **kwargs)

    monkeypatch.setattr(builder, "_VERIFY_R2F_V2_GENERATION", verify)
    monkeypatch.setattr(builder, "_BUILD_SOURCE_SNAPSHOT", snapshot)
    monkeypatch.setattr(builder, "_build_analyst_input_package", package)
    monkeypatch.setattr(builder, "_render_analyst_prompt", render)
    result = _build_public(package_context)
    assert result.ok is True
    assert type(result.value) is builder._AnalystInputPackage
    assert counts == {"verify": 1, "snapshot": 1, "package": 1, "render": 0}


def test_public_render_runs_one_uninterrupted_private_verified_pipeline(
    package_context: _PackageContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = {"verify": 0, "snapshot": 0, "package": 0, "render": 0}
    captured: dict[str, object] = {}
    originals = {
        "verify": builder._VERIFY_R2F_V2_GENERATION,
        "snapshot": builder._BUILD_SOURCE_SNAPSHOT,
        "package": builder._build_analyst_input_package,
        "render": builder._render_analyst_prompt,
    }

    def verify(*args: object, **kwargs: object) -> object:
        counts["verify"] += 1
        return originals["verify"](*args, **kwargs)

    def snapshot(*args: object, **kwargs: object) -> object:
        counts["snapshot"] += 1
        value = originals["snapshot"](*args, **kwargs)
        captured["snapshot"] = value
        return value

    def package(*args: object, **kwargs: object) -> object:
        counts["package"] += 1
        value = originals["package"](*args, **kwargs)
        captured["package"] = value
        return value

    def render(*args: object, **kwargs: object) -> object:
        counts["render"] += 1
        assert args == (captured["package"],)
        return originals["render"](*args, **kwargs)

    monkeypatch.setattr(builder, "_VERIFY_R2F_V2_GENERATION", verify)
    monkeypatch.setattr(builder, "_BUILD_SOURCE_SNAPSHOT", snapshot)
    monkeypatch.setattr(builder, "_build_analyst_input_package", package)
    monkeypatch.setattr(builder, "_render_analyst_prompt", render)
    result = _render_public(package_context)
    assert result.ok is True
    assert type(result.value) is builder._RenderedAnalystPrompt
    assert counts == {"verify": 1, "snapshot": 1, "package": 1, "render": 1}
    assert captured["package"]._authenticated_contract_surface is (
        captured["snapshot"].authenticated_contract_surface
    )
    assert not hasattr(result.value, "package")


@pytest.mark.parametrize("operation", ("build", "render"))
def test_top_level_source_instability_returns_no_partial_value(
    package_context: _PackageContext,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    def unstable(*_args: object, **_kwargs: object) -> None:
        raise adapter._SourceAdapterFailure("WS01_BR_SOURCE_READ_UNSTABLE")

    monkeypatch.setattr(builder, "_VERIFY_R2F_V2_GENERATION", unstable)
    result = (
        _build_public(package_context)
        if operation == "build"
        else _render_public(package_context)
    )
    _assert_failure_result(result, "WS01_BR_SOURCE_READ_UNSTABLE")


def test_repeated_primitive_selector_calls_remain_deterministic(
    package_context: _PackageContext,
) -> None:
    first_package = _expect_success(_build_public(package_context))
    second_package = _expect_success(_build_public(package_context))
    first_render = _expect_success(_render_public(package_context))
    second_render = _expect_success(_render_public(package_context))
    assert first_package.canonical_json_bytes == second_package.canonical_json_bytes
    assert (
        first_package.input_package_identity_sha256
        == second_package.input_package_identity_sha256
    )
    assert first_render.prompt_bytes == second_render.prompt_bytes
    assert (
        first_render.prompt_render_identity_sha256
        == second_render.prompt_render_identity_sha256
    )


@pytest.mark.parametrize(
    "kind",
    ("matching_slots", "properties", "named_tuple", "dataclass", "proxy"),
)
def test_fake_adapter_result_lookalikes_cannot_establish_package_authority(
    package_context: tuple[object, object],
    kind: str,
) -> None:
    snapshot, _ = package_context
    authentic = adapter._result_success(snapshot)
    lookalike = _result_lookalike(
        kind,
        module_name=adapter.__name__,
        authentic_result=authentic,
        value=snapshot,
    )
    assert type(lookalike) is not adapter._WS01bResult
    _assert_failure_result(
        builder.build_analyst_input_package(lookalike),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )


def test_fake_adapter_result_and_fake_snapshot_cannot_establish_package_authority(
    package_context: tuple[object, object],
) -> None:
    snapshot, _ = package_context
    fake_snapshot = _slot_lookalike(
        snapshot,
        module_name=adapter.__name__,
        class_name="_VerifiedSourceSnapshot",
    )
    fake_result = _result_lookalike(
        "matching_slots",
        module_name=adapter.__name__,
        authentic_result=adapter._result_success(snapshot),
        value=fake_snapshot,
    )
    assert type(fake_snapshot) is not adapter._VerifiedSourceSnapshot
    _assert_failure_result(
        builder.build_analyst_input_package(fake_result),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )


@pytest.mark.parametrize("kind", ("matching_slots", "subclass", "proxy"))
def test_authentic_adapter_result_rejects_non_authentic_snapshot_values(
    package_context: tuple[object, object],
    kind: str,
) -> None:
    snapshot, _ = package_context
    if kind == "matching_slots":
        fake_snapshot = _slot_lookalike(
            snapshot,
            module_name=adapter.__name__,
            class_name="_VerifiedSourceSnapshot",
        )
    elif kind == "subclass":
        fake_snapshot = _subclass_clone(
            snapshot,
            class_name="_VerifiedSourceSnapshot",
        )
    else:
        fake_snapshot = _forwarding_proxy(
            snapshot,
            module_name=adapter.__name__,
            class_name="_VerifiedSourceSnapshot",
        )
    exact_result_with_fake_value = _unchecked_result(
        adapter._WS01bResult,
        ok=True,
        value=fake_snapshot,
        reason_code=None,
    )
    assert type(exact_result_with_fake_value) is adapter._WS01bResult
    assert type(fake_snapshot) is not adapter._VerifiedSourceSnapshot
    _assert_failure_result(
        builder.build_analyst_input_package(exact_result_with_fake_value),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )


def test_adapter_result_subclass_is_rejected_even_with_authentic_snapshot(
    package_context: tuple[object, object],
) -> None:
    snapshot, _ = package_context
    subclass_result = _subclass_clone(
        adapter._result_success(snapshot),
        class_name="_WS01bResult",
    )
    assert isinstance(subclass_result, adapter._WS01bResult)
    assert type(subclass_result) is not adapter._WS01bResult
    _assert_failure_result(
        builder.build_analyst_input_package(subclass_result),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )


@pytest.mark.parametrize(
    "kind",
    ("matching_slots", "properties", "named_tuple", "dataclass", "proxy"),
)
def test_fake_builder_result_lookalikes_cannot_establish_render_authority(
    package_context: tuple[object, object],
    kind: str,
) -> None:
    _, package = package_context
    authentic = builder._result_success(package)
    lookalike = _result_lookalike(
        kind,
        module_name=builder.__name__,
        authentic_result=authentic,
        value=package,
    )
    assert type(lookalike) is not builder._WS01bResult
    _assert_failure_result(
        builder.render_analyst_prompt(lookalike),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )


def test_builder_result_subclass_is_rejected_even_with_authentic_package(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    subclass_result = _subclass_clone(
        builder._result_success(package),
        class_name="_WS01bResult",
    )
    assert isinstance(subclass_result, builder._WS01bResult)
    assert type(subclass_result) is not builder._WS01bResult
    _assert_failure_result(
        builder.render_analyst_prompt(subclass_result),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )


@pytest.mark.parametrize("kind", ("matching_slots", "subclass", "proxy"))
def test_authentic_builder_result_rejects_non_authentic_built_package_values(
    package_context: tuple[object, object],
    kind: str,
) -> None:
    _, package = package_context
    if kind == "matching_slots":
        fake_package = _slot_lookalike(
            package,
            module_name=builder.__name__,
            class_name="_AnalystInputPackage",
        )
    elif kind == "subclass":
        fake_package = _subclass_clone(
            package,
            class_name="_AnalystInputPackage",
        )
    else:
        fake_package = _forwarding_proxy(
            package,
            module_name=builder.__name__,
            class_name="_AnalystInputPackage",
        )
    exact_result_with_fake_value = _unchecked_result(
        builder._WS01bResult,
        ok=True,
        value=fake_package,
        reason_code=None,
    )
    assert type(exact_result_with_fake_value) is builder._WS01bResult
    assert type(fake_package) is not builder._AnalystInputPackage
    _assert_failure_result(
        builder.render_analyst_prompt(exact_result_with_fake_value),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )


def test_fake_builder_result_and_fake_package_return_no_partial_render(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    fake_package = _slot_lookalike(
        package,
        module_name=builder.__name__,
        class_name="_AnalystInputPackage",
    )
    fake_result = _result_lookalike(
        "matching_slots",
        module_name=builder.__name__,
        authentic_result=builder._result_success(package),
        value=fake_package,
    )
    _assert_failure_result(
        builder.render_analyst_prompt(fake_result),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )


def test_primitive_selectors_are_the_only_public_success_authority(
    package_context: _PackageContext,
) -> None:
    snapshot, package = package_context
    assert builder._source_adapter is adapter
    assert builder._ADAPTER_RESULT_TYPE is adapter._WS01bResult
    assert (
        builder._ADAPTER_VERIFIED_GENERATION_TYPE
        is adapter._VerifiedR2FGeneration
    )
    assert builder._ADAPTER_SNAPSHOT_TYPE is adapter._VerifiedSourceSnapshot
    assert (
        builder._ADAPTER_SURFACE_TYPE
        is adapter._AuthenticatedContractSurface
    )

    built_result = _build_public(package_context)
    assert type(built_result) is builder._WS01bResult
    assert built_result.ok is True
    assert type(built_result.value) is builder._AnalystInputPackage

    rendered_result = _render_public(package_context)
    assert type(rendered_result) is builder._WS01bResult
    assert rendered_result.ok is True
    assert type(rendered_result.value) is builder._RenderedAnalystPrompt

    source_failure = adapter._result_failure(
        "WS01_BR_SOURCE_READ_UNSTABLE"
    )
    _assert_failure_result(
        builder.build_analyst_input_package(source_failure),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )
    local_failure = builder._result_failure(
        "WS01_BR_PACKAGE_CONSTRUCTION_FAILED"
    )
    _assert_failure_result(
        builder.render_analyst_prompt(local_failure),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )


def test_adapter_class_authority_is_immutable_after_runtime_registry_replacement(
    package_context: _PackageContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, package = package_context
    authentic_result_type = builder._ADAPTER_RESULT_TYPE
    authentic_snapshot_type = builder._ADAPTER_SNAPSHOT_TYPE
    authentic_surface_type = builder._ADAPTER_SURFACE_TYPE
    authentic_verify = builder._VERIFY_R2F_V2_GENERATION
    authentic_snapshot_builder = builder._BUILD_SOURCE_SNAPSHOT
    authentic_surface = snapshot.authenticated_contract_surface

    replacement = ModuleType(adapter.__name__)
    replacement_result_type = type(
        "_WS01bResult",
        (),
        {
            "__module__": adapter.__name__,
            "__qualname__": "_WS01bResult",
            "__slots__": ("ok", "value", "reason_code"),
        },
    )
    replacement_snapshot_type = type(
        "_VerifiedSourceSnapshot",
        (),
        {
            "__module__": adapter.__name__,
            "__qualname__": "_VerifiedSourceSnapshot",
            "__slots__": tuple(adapter._VerifiedSourceSnapshot.__slots__),
        },
    )
    replacement_surface_type = type(
        "_AuthenticatedContractSurface",
        (),
        {
            "__module__": adapter.__name__,
            "__qualname__": "_AuthenticatedContractSurface",
            "__slots__": tuple(adapter._AuthenticatedContractSurface.__slots__),
        },
    )
    replacement._WS01bResult = replacement_result_type
    replacement._VerifiedSourceSnapshot = replacement_snapshot_type
    replacement._AuthenticatedContractSurface = replacement_surface_type

    fake_snapshot = object.__new__(replacement_snapshot_type)
    for name in adapter._VerifiedSourceSnapshot.__slots__:
        object.__setattr__(fake_snapshot, name, getattr(snapshot, name))
    fake_surface = object.__new__(replacement_surface_type)
    for name in adapter._AuthenticatedContractSurface.__slots__:
        object.__setattr__(fake_surface, name, getattr(authentic_surface, name))
    authentic_snapshot_with_fake_surface = object.__new__(
        adapter._VerifiedSourceSnapshot
    )
    for name in adapter._VerifiedSourceSnapshot.__slots__:
        object.__setattr__(
            authentic_snapshot_with_fake_surface,
            name,
            fake_surface
            if name == "authenticated_contract_surface"
            else getattr(snapshot, name),
        )

    with monkeypatch.context() as registry_patch:
        registry_patch.setitem(sys.modules, adapter.__name__, replacement)
        assert sys.modules[adapter.__name__] is replacement
        assert builder._source_adapter is adapter
        assert builder._ADAPTER_RESULT_TYPE is authentic_result_type
        assert builder._ADAPTER_SNAPSHOT_TYPE is authentic_snapshot_type
        assert builder._ADAPTER_SURFACE_TYPE is authentic_surface_type
        assert builder._VERIFY_R2F_V2_GENERATION is authentic_verify
        assert builder._BUILD_SOURCE_SNAPSHOT is authentic_snapshot_builder

        fake_result_with_authentic_snapshot = _unchecked_result(
            replacement_result_type,
            ok=True,
            value=snapshot,
            reason_code=None,
        )
        _assert_failure_result(
            builder.build_analyst_input_package(
                fake_result_with_authentic_snapshot
            ),
            "WS01_BR_SOURCE_GENERATION_INVALID",
        )

        authentic_result_with_fake_snapshot = _unchecked_result(
            adapter._WS01bResult,
            ok=True,
            value=fake_snapshot,
            reason_code=None,
        )
        _assert_failure_result(
            builder.build_analyst_input_package(
                authentic_result_with_fake_snapshot
            ),
            "WS01_BR_SOURCE_GENERATION_INVALID",
        )

        authentic_result_with_fake_surface = _unchecked_result(
            adapter._WS01bResult,
            ok=True,
            value=authentic_snapshot_with_fake_surface,
            reason_code=None,
        )
        _assert_failure_result(
            builder.build_analyst_input_package(
                authentic_result_with_fake_surface
            ),
            "WS01_BR_SOURCE_GENERATION_INVALID",
        )

        genuine_build = _build_public(package_context)
        assert genuine_build.ok is True
        assert type(genuine_build.value) is builder._AnalystInputPackage
        genuine_render = _render_public(package_context)
        assert genuine_render.ok is True
        assert type(genuine_render.value) is builder._RenderedAnalystPrompt

    assert sys.modules[adapter.__name__] is adapter
    assert builder._ADAPTER_RESULT_TYPE is authentic_result_type
    assert builder._ADAPTER_SNAPSHOT_TYPE is authentic_snapshot_type
    assert builder._ADAPTER_SURFACE_TYPE is authentic_surface_type
    assert builder._VERIFY_R2F_V2_GENERATION is authentic_verify
    assert builder._BUILD_SOURCE_SNAPSHOT is authentic_snapshot_builder


def test_public_boundaries_accept_only_primitive_source_selectors() -> None:
    root = Path(__file__).parents[2]
    tree = ast.parse(
        (
            root
            / "src/investment_orchestrator/observability/weekly_shadow_01_package_builder.py"
        ).read_text(encoding="utf-8")
    )
    guarded_functions = {
        "_build_package_from_source_selection",
        "_require_snapshot_contract",
        "_require_contract_surface",
        "_result_success",
    }
    guarded_nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in guarded_functions
    ]
    assert {node.name for node in guarded_nodes} == guarded_functions
    prohibited_attributes = {
        "__module__",
        "__name__",
        "__qualname__",
        "__slots__",
    }
    assert not {
        node.attr
        for function in guarded_nodes
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr in prohibited_attributes
    }
    assert not {
        node.func.id
        for function in guarded_nodes
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"getattr", "hasattr", "isinstance"}
    }
    assert not hasattr(builder, "_source_adapter_private_classes")
    assert not hasattr(builder, "_sys")
    assert not hasattr(builder, "_ModuleType")
    assert not hasattr(builder, "_unwrap_exact_result")
    assert not hasattr(builder, "_unwrap_adapter_snapshot_result")
    assert not hasattr(builder, "_unwrap_local_package_result")
    public_functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in builder.__all__
    }
    assert set(public_functions) == set(builder.__all__)
    for function in public_functions.values():
        assert [argument.arg for argument in function.args.args] == ["generation_id"]
        assert [argument.arg for argument in function.args.kwonlyargs] == [
            "repository_root"
        ]


@pytest.mark.parametrize("reason_code", tuple(sorted(builder._BLOCKING_REASON_CODES)))
def test_failure_results_propagate_across_ws01b_stages_without_partial_values(
    reason_code: str,
    package_context: _PackageContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(
        _generation_id: str,
        *,
        repository_root: object = None,
    ) -> None:
        del repository_root
        raise adapter._SourceAdapterFailure(reason_code)

    monkeypatch.setattr(builder, "_VERIFY_R2F_V2_GENERATION", fail)
    _assert_failure_result(
        _build_public(package_context),
        reason_code,
    )
    _assert_failure_result(
        _render_public(package_context),
        reason_code,
    )


@pytest.mark.parametrize(
    "helper_name",
    (
        "_require_snapshot_contract",
        "_require_snapshot_identity",
        "_binding_map",
        "_canonicalize_evidence_records",
        "_validate_against_schema",
        "_new_analyst_input_package",
    ),
)
def test_package_builder_unexpected_internal_failures_are_sanitized(
    package_context: _PackageContext,
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
) -> None:
    secret = f"UNEXPECTED-BUILD-SECRET-{helper_name}"

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(builder, helper_name, fail)
    result = _build_public(package_context)
    _assert_failure_result(
        result,
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        secret,
    )


@pytest.mark.parametrize(
    "helper_name",
    (
        "_require_contract_surface",
        "_validate_package",
        "_compute_identity",
        "_new_rendered_analyst_prompt",
    ),
)
def test_prompt_renderer_unexpected_internal_failures_are_sanitized(
    package_context: _PackageContext,
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
) -> None:
    secret = f"UNEXPECTED-RENDER-SECRET-{helper_name}"

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(builder, helper_name, fail)
    result = _render_public(package_context)
    _assert_failure_result(
        result,
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        secret,
    )


@pytest.mark.parametrize("control_flow", (KeyboardInterrupt, SystemExit, GeneratorExit))
@pytest.mark.parametrize("operation", ("build", "render"))
def test_builder_public_boundaries_do_not_convert_control_flow_exceptions(
    package_context: _PackageContext,
    monkeypatch: pytest.MonkeyPatch,
    control_flow: type[BaseException],
    operation: str,
) -> None:
    secret = f"CONTROL-FLOW-{operation}-{control_flow.__name__}"

    def stop(*_args: object, **_kwargs: object) -> None:
        raise control_flow(secret)

    if operation == "build":
        monkeypatch.setattr(builder, "_require_snapshot_contract", stop)
        invoke = lambda: _build_public(package_context)
    else:
        monkeypatch.setattr(builder, "_require_contract_surface", stop)
        invoke = lambda: _render_public(package_context)
    with pytest.raises(control_flow, match=secret):
        invoke()


def test_identity_consistent_forged_binding_failure_is_code_only_and_returns_nothing(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    forged_hash = "f" * 64
    payload = package.to_dict()
    payload["prompt_template_identity_sha256"] = forged_hash
    forged = _identity_consistent_forged_package(package, payload)
    result = _render_result(forged)
    _assert_failure_result(
        result,
        "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
        forged_hash,
    )


def test_actual_schema_validation_failure_leaks_no_schema_value_or_message(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    secret_key = "SCHEMA-INVALID-SECRET-DA20"
    secret_value = "SCHEMA-VALUE-SECRET-A915"
    payload = package.to_dict()
    payload[secret_key] = secret_value
    forged = _rebuilt_package(package, payload)
    result = _render_result(forged)
    _assert_failure_result(
        result,
        "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
        secret_key,
        secret_value,
    )


def test_prompt_placeholder_and_resource_failures_expose_no_internal_values(
    package_context: tuple[object, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, package = package_context
    original_require_surface = builder._require_contract_surface
    placeholder_secret = "PROMPT-PLACEHOLDER-SECRET-809E"

    def prompt_without_placeholder(surface: object) -> dict[str, object]:
        value = dict(original_require_surface(surface))
        value["prompt_template_text"] = placeholder_secret + "\n"
        return value

    monkeypatch.setattr(builder, "_require_contract_surface", prompt_without_placeholder)
    _assert_failure_result(
        _render_result(package),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        placeholder_secret,
    )
    monkeypatch.undo()

    profile = dict(builder._RESOURCE_BOUND_PROFILE)
    rejected_bound = len(package.canonical_json_bytes) - 1
    profile["analyst_input_max_bytes"] = rejected_bound
    monkeypatch.setattr(builder, "_RESOURCE_BOUND_PROFILE", MappingProxyType(profile))
    _assert_failure_result(
        _render_result(package),
        "WS01_BR_RESOURCE_BOUND_EXCEEDED",
        rejected_bound,
    )


def test_builder_narrow_private_reason_mapping_has_no_exception_chain(
    package_context: tuple[object, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, package = package_context
    secret = "FORGED-BINDING-PRIVATE-SECRET-770A"

    def fail(_payload: object, _surface: object) -> None:
        try:
            raise ValueError(secret)
        except ValueError:
            builder._raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")

    monkeypatch.setattr(builder, "_require_frozen_package_bindings", fail)
    _assert_failure_result(
        _render_result(package),
        "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
        secret,
    )


def test_public_namespace_is_exact_and_has_no_mutable_registry_leak() -> None:
    public = {name for name in vars(builder) if not name.startswith("_")}
    assert public == set(builder.__all__)
    assert builder.__all__ == (
        "build_analyst_input_package",
        "render_analyst_prompt",
    )


def test_all_state_action_order_and_authority_effects_remain_absent(
    package_context: tuple[object, object],
) -> None:
    _, package = package_context
    payload = package.to_dict()
    assert payload["negative_authority"] == dict(contracts.NEGATIVE_AUTHORITY_PROFILE)
    assert all(record["authority_effect"] == "none" for record in payload["evidence_records"])
    forbidden_fields = {
        "run_status",
        "analyst_conclusion",
        "validation_status",
        "publication_status",
        "permission_result",
        "gate_result",
        "portfolio_target",
        "order",
        "broker",
        "execution",
    }
    assert not forbidden_fields & set(payload)
