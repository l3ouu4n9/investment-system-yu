"""WS01c deterministic untrusted-response validation tests."""

from __future__ import annotations

import ast
import base64
import builtins
from collections.abc import Mapping
import copy
from dataclasses import dataclass
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
        "contract_roots": [],
        "adapter_filesystem_roots": [],
        "validator_filesystem_roots": [],
    }
    original_build = validator._BUILD_PACKAGE_FROM_SOURCE_SELECTION
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
    assert len(observed["contract_roots"]) == 1
    assert observed["builder_roots"][0] is observed["contract_roots"][0]
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
    counts = {
        "root": 0,
        "verify": 0,
        "snapshot": 0,
        "package": 0,
        "render": 0,
        "parse": 0,
        "capture": 0,
        "validation": 0,
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
    }

    def wrap(name: str):
        original = originals[name]

        def wrapped(*args: object, **kwargs: object) -> object:
            counts[name] += 1
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
    assert _call(response_context).ok is True
    assert counts == {name: 1 for name in counts}


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


def test_deterministic_repeated_validation_is_byte_identical(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _expect_success(_call(response_context))
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("WS01C_BOUND_OVERRIDE", "1")
    second = _expect_success(_call(response_context))
    assert first == second
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
    ),
    ids=(
        "package-result",
        "built-package",
        "render-result",
        "mapping",
        "tuple",
        "serialized-mapping",
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


def test_zero_write_network_subprocess_and_environment_effects(
    response_context: _ResponseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = builtins.open
    original_os_open = os.open
    environment = dict(os.environ)
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
    assert writes == []
    assert dict(os.environ) == environment


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
