from __future__ import annotations

import ast
import copy
import copyreg
import dataclasses
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import inspect
import json
from pathlib import Path
import pickle
from typing import Any, Callable

import pytest

from investment_orchestrator.parsers.parse_step2_market_source_policy_approvals import (
    FrozenJsonArray,
    FrozenJsonObject,
    Step2MarketSourcePolicyApprovalParseResult,
    Step2MarketSourcePolicyApprovalParseState,
    parse_step2_market_source_policy_approvals_bytes,
)
from investment_orchestrator.validators import (
    validate_step2_market_source_policy_approval_artifact as artifact_contract,
)
from investment_orchestrator.validators.validate_step2_market_source_policy_approval_artifact import (
    ACTIVATION_EVALUATION_PERFORMED,
    ARTIFACT_RESULT_BOOLEAN_COERCION_ERROR,
    AUTHORITY_SCOPE,
    CANDIDATE_VALIDITY_EVALUATED,
    COMPOSITION_VERSION,
    FRESHNESS_EVALUATION_PERFORMED,
    MAX_APPROVAL_ARTIFACT_COMPOSITION_DIAGNOSTICS,
    MAX_ARRAY_ITEM_COUNT,
    MAX_ARTIFACT_VALIDATION_BINDING_BYTES,
    MAX_DECODED_STRING_CODE_POINTS,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODE_COUNT,
    MAX_OBJECT_MEMBER_COUNT,
    MAX_PARSED_VALUE_CANONICAL_BYTES,
    NOT_TRADE_AUTHORIZATION,
    OPERATOR_AUTHENTICATION_PERFORMED,
    ORDER_COMPILATION_EVALUATED,
    PUBLICATION_EVALUATION_PERFORMED,
    RESULT_VERSION,
    SOURCE_RESOLUTION_PERFORMED,
    TRADE_PERMISSION_EFFECT,
    UNIVERSE_RESOLUTION_PERFORMED,
    WORKFLOW_PERMISSION_EVALUATED,
    Step2MarketSourcePolicyApprovalArtifactDiagnostic,
    Step2MarketSourcePolicyApprovalArtifactState,
    Step2MarketSourcePolicyApprovalArtifactValidationResult,
    validate_step2_market_source_policy_approval_artifact_bytes,
)
from investment_orchestrator.validators.validate_step2_market_source_policy_approvals import (
    APPROVAL_SCHEMA_VERSION,
    Step2MarketSourcePolicyApprovalDiagnostic,
    Step2MarketSourcePolicyApprovalObjectState,
    Step2MarketSourcePolicyApprovalsObjectValidationResult,
    validate_step2_market_source_policy_approvals_object,
)


State = Step2MarketSourcePolicyApprovalArtifactState
Diagnostic = Step2MarketSourcePolicyApprovalArtifactDiagnostic
ParseState = Step2MarketSourcePolicyApprovalParseState
ObjectState = Step2MarketSourcePolicyApprovalObjectState
ObjectDiagnostic = Step2MarketSourcePolicyApprovalDiagnostic

_PARSED_DOMAIN = b"step2_market_source_policy_approval_parsed_value_v1\0"
_ARTIFACT_DOMAIN = (
    b"step2_market_source_policy_approval_artifact_validation_v1\0"
)
_APPROVAL_DOMAIN = b"step2_market_source_policy_approvals_v1\0"
_INVARIANT_MESSAGE = (
    "Step 2 source-policy approval artifact composition invariant violated"
)
_CONSTRUCTION_ERROR = (
    "Step 2 source-policy approval artifact validation results are created "
    "only by the public validator"
)


def _permission() -> dict[str, str]:
    return {
        "source_role": "LAST_CLOSE_VALUE",
        "content_type": "application/json",
        "capture_adapter_id": "capture.http",
        "capture_adapter_version": "v1",
    }


def _source() -> dict[str, Any]:
    return {
        "canonical_source_id": "primary.market",
        "source_version": "v1",
        "exact_aliases": ["Primary Market"],
        "permissions": [_permission()],
        "approval_reason": "Approved for composition contract testing.",
    }


def _approval_content(
    *,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "policy_version": "policy_v1",
        "supersedes_policy_version": None,
        "policy_change_reason": "Initial artifact composition fixture.",
        "approved_by": "operator.primary",
        "approved_at_utc": "2026-07-15T12:34:56Z",
        "sources": [] if sources is None else sources,
    }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _approval(
    *,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    content = _approval_content(sources=sources)
    return {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "operator_approved_source_policy_sha256": hashlib.sha256(
            _APPROVAL_DOMAIN + _canonical_json(content)
        ).hexdigest(),
        "approval_content": content,
    }


def _raw_approval(
    *,
    sources: list[dict[str, Any]] | None = None,
    indent: int | None = None,
) -> bytes:
    return json.dumps(
        _approval(sources=sources),
        ensure_ascii=False,
        separators=None if indent is not None else (",", ":"),
        indent=indent,
    ).encode("utf-8")


def _validate(
    value: object,
) -> Step2MarketSourcePolicyApprovalArtifactValidationResult:
    return validate_step2_market_source_policy_approval_artifact_bytes(value)


def _assert_fixed_markers(
    result: Step2MarketSourcePolicyApprovalArtifactValidationResult,
) -> None:
    assert result.result_version == RESULT_VERSION
    assert result.composition_version == COMPOSITION_VERSION
    assert result.authority_scope == AUTHORITY_SCOPE
    assert result.authority_scope == "raw_and_object_contract_validation_only"
    assert result.not_trade_authorization is NOT_TRADE_AUTHORIZATION is True
    assert result.trade_permission_effect == TRADE_PERMISSION_EFFECT == "none"
    assert (
        result.operator_authentication_performed
        is OPERATOR_AUTHENTICATION_PERFORMED
        is False
    )
    assert result.source_resolution_performed is SOURCE_RESOLUTION_PERFORMED is False
    assert (
        result.freshness_evaluation_performed
        is FRESHNESS_EVALUATION_PERFORMED
        is False
    )
    assert (
        result.universe_resolution_performed
        is UNIVERSE_RESOLUTION_PERFORMED
        is False
    )
    assert (
        result.candidate_validity_evaluated
        is CANDIDATE_VALIDITY_EVALUATED
        is False
    )
    assert (
        result.activation_evaluation_performed
        is ACTIVATION_EVALUATION_PERFORMED
        is False
    )
    assert (
        result.publication_evaluation_performed
        is PUBLICATION_EVALUATION_PERFORMED
        is False
    )
    assert (
        result.workflow_permission_evaluated
        is WORKFLOW_PERMISSION_EVALUATED
        is False
    )
    assert (
        result.order_compilation_evaluated
        is ORDER_COMPILATION_EVALUATED
        is False
    )


def _assert_boolean_rejected(
    result: Step2MarketSourcePolicyApprovalArtifactValidationResult,
) -> None:
    with pytest.raises(TypeError) as exc_info:
        bool(result)
    assert str(exc_info.value) == ARTIFACT_RESULT_BOOLEAN_COERCION_ERROR


def _assert_branch(
    result: Step2MarketSourcePolicyApprovalArtifactValidationResult,
    *,
    state: State,
    object_result: bool,
    artifact_identity: bool,
    root: tuple[bool, bool | None],
    conversion: tuple[bool, bool | None],
    recheck: tuple[bool, bool | None],
    object_validation: bool,
    composition_validation: bool,
    artifact_valid: bool | None,
    diagnostics: tuple[Diagnostic, ...],
) -> None:
    assert result.composition_state is state
    assert (result.approval_object_validation_result is not None) is object_result
    assert (result.artifact_validation_identity_sha256 is not None) is artifact_identity
    assert (result.root_object_check_performed, result.root_object_valid) == root
    assert (
        result.exact_builtin_conversion_performed,
        result.exact_builtin_conversion_valid,
    ) == conversion
    assert (
        result.parsed_identity_recheck_performed,
        result.parsed_identity_recheck_matches,
    ) == recheck
    assert result.object_contract_validation_performed is object_validation
    assert result.composition_validation_performed is composition_validation
    assert result.artifact_contract_valid is artifact_valid
    assert result.diagnostics == diagnostics
    _assert_fixed_markers(result)
    _assert_boolean_rejected(result)


def _binding_record_oracle(
    result: Step2MarketSourcePolicyApprovalArtifactValidationResult,
) -> dict[str, Any]:
    parser_result = result.parser_result
    object_result = result.approval_object_validation_result
    assert object_result is not None
    return {
        "approval_object_result": {
            "approval_identity_matches": object_result.approval_identity_matches,
            "approval_object_valid": object_result.approval_object_valid,
            "approval_policy_version": object_result.approval_policy_version,
            "approval_schema_version": object_result.approval_schema_version,
            "approval_state": object_result.approval_state.value,
            "canonical_approval_content_sha256": (
                object_result.canonical_approval_content_sha256
            ),
            "declared_operator_approved_source_policy_sha256": (
                object_result.declared_operator_approved_source_policy_sha256
            ),
            "diagnostics": [
                diagnostic.value for diagnostic in object_result.diagnostics
            ],
            "object_structure_valid": object_result.object_structure_valid,
            "object_validation_performed": object_result.object_validation_performed,
            "result_version": object_result.result_version,
            "semantic_validation_performed": (
                object_result.semantic_validation_performed
            ),
            "source_count": object_result.source_count,
        },
        "artifact_contract_valid": result.artifact_contract_valid,
        "composition_diagnostics": [
            diagnostic.value for diagnostic in result.diagnostics
        ],
        "composition_state": result.composition_state.value,
        "composition_validation_performed": result.composition_validation_performed,
        "composition_version": result.composition_version,
        "conversion": {
            "exact_builtin_conversion_performed": (
                result.exact_builtin_conversion_performed
            ),
            "exact_builtin_conversion_valid": result.exact_builtin_conversion_valid,
        },
        "object_contract_validation_performed": (
            result.object_contract_validation_performed
        ),
        "parsed_identity_binding": {
            "parsed_identity_recheck_matches": (
                result.parsed_identity_recheck_matches
            ),
            "parsed_identity_recheck_performed": (
                result.parsed_identity_recheck_performed
            ),
        },
        "parser_result": {
            "diagnostics": [
                diagnostic.value for diagnostic in parser_result.diagnostics
            ],
            "parse_state": parser_result.parse_state.value,
            "parse_valid": parser_result.parse_valid,
            "parsed_value_available": parser_result.parsed_value_available,
            "parsed_value_identity_sha256": (
                parser_result.parsed_value_identity_sha256
            ),
            "parser_version": parser_result.parser_version,
            "parsing_performed": parser_result.parsing_performed,
            "raw_artifact_sha256": parser_result.raw_artifact_sha256,
            "raw_artifact_size_bytes": parser_result.raw_artifact_size_bytes,
            "result_version": parser_result.result_version,
        },
        "result_version": result.result_version,
        "root_object": {
            "root_object_check_performed": result.root_object_check_performed,
            "root_object_valid": result.root_object_valid,
        },
    }


def _binding_identity_oracle(
    result: Step2MarketSourcePolicyApprovalArtifactValidationResult,
) -> tuple[bytes, str]:
    encoded = _canonical_json(_binding_record_oracle(result))
    return encoded, hashlib.sha256(_ARTIFACT_DOMAIN + encoded).hexdigest()


def _replace_frozen_fields(value: Any, **changes: Any) -> Any:
    for name, replacement in changes.items():
        object.__setattr__(value, name, replacement)
    return value


def _patched_parser_result(
    raw: bytes,
    **changes: Any,
) -> Step2MarketSourcePolicyApprovalParseResult:
    result = parse_step2_market_source_policy_approvals_bytes(raw)
    assert result.parse_valid is True
    return _replace_frozen_fields(result, **changes)


def _assert_invariant(
    call: Callable[[], Any],
) -> None:
    with pytest.raises(artifact_contract._CompositionInvariantError) as exc_info:
        call()
    assert str(exc_info.value) == _INVARIANT_MESSAGE


def _assert_construction_rejected(call: Callable[[], Any]) -> None:
    with pytest.raises(TypeError) as exc_info:
        call()
    assert str(exc_info.value) == _CONSTRUCTION_ERROR


def test_constants_versions_and_limits_are_exact() -> None:
    assert RESULT_VERSION == (
        "step2_market_source_policy_approval_artifact_validation_result_v1"
    )
    assert COMPOSITION_VERSION == (
        "step2_market_source_policy_approval_artifact_composition_v1"
    )
    assert MAX_JSON_NESTING_DEPTH == 8
    assert MAX_JSON_NODE_COUNT == 4096
    assert MAX_DECODED_STRING_CODE_POINTS == 262_144
    assert MAX_OBJECT_MEMBER_COUNT == 1024
    assert MAX_ARRAY_ITEM_COUNT == 1024
    assert MAX_PARSED_VALUE_CANONICAL_BYTES == 12_582_912
    assert MAX_ARTIFACT_VALIDATION_BINDING_BYTES == 16_384
    assert MAX_APPROVAL_ARTIFACT_COMPOSITION_DIAGNOSTICS == 1


def test_public_api_signature_is_exact() -> None:
    signature = inspect.signature(
        validate_step2_market_source_policy_approval_artifact_bytes
    )
    assert tuple(signature.parameters) == ("value",)
    assert signature.parameters["value"].annotation == "object"
    assert signature.return_annotation == (
        "Step2MarketSourcePolicyApprovalArtifactValidationResult"
    )


def test_one_private_factory_owns_all_result_construction_and_identity() -> None:
    factory_signature = inspect.signature(artifact_contract._create_result)
    assert "artifact_validation_identity_sha256" not in (
        factory_signature.parameters
    )
    source = Path(artifact_contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    public_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "validate_step2_market_source_policy_approval_artifact_bytes"
    )
    public_calls = [
        node.func.id
        for node in ast.walk(public_function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert public_calls.count("_create_result") == 6
    assert (
        public_calls.count(
            "Step2MarketSourcePolicyApprovalArtifactValidationResult"
        )
        == 0
    )
    factory = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_create_result"
    )
    stored_identity_names = [
        node.value
        for node in ast.walk(factory)
        if isinstance(node, ast.Constant)
        and node.value == "artifact_validation_identity_sha256"
    ]
    assert stored_identity_names == ["artifact_validation_identity_sha256"]


def test_factory_is_the_only_production_result_allocation_site() -> None:
    source = Path(artifact_contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allocation_sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "object"
        and node.func.attr == "__new__"
    ]
    assert len(allocation_sites) == 1
    factory = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_create_result"
    )
    assert allocation_sites[0] in tuple(ast.walk(factory))
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id
        == "Step2MarketSourcePolicyApprovalArtifactValidationResult"
        and node.func.attr == "__new__"
        for node in ast.walk(tree)
    )
    assert "copyreg" not in source
    assert "pickle" not in source


def test_state_enum_order_is_exact_and_has_no_conversion_failure() -> None:
    assert tuple(State) == (
        State.INPUT_ABSENT,
        State.INPUT_TYPE_INVALID,
        State.RAW_PARSE_INVALID,
        State.ROOT_TYPE_INVALID,
        State.PARSED_IDENTITY_BINDING_INVALID,
        State.OBJECT_CONTRACT_INVALID,
        State.VALID_EMPTY,
        State.VALID_NONEMPTY,
    )
    assert not hasattr(State, "EXACT_BUILTIN_CONVERSION_INVALID")


def test_diagnostic_enum_order_and_singleton_bound_are_exact() -> None:
    assert tuple(Diagnostic) == (
        Diagnostic.APPROVAL_ARTIFACT_INPUT_MISSING,
        Diagnostic.APPROVAL_ARTIFACT_INPUT_TYPE_INVALID,
        Diagnostic.APPROVAL_ARTIFACT_RAW_PARSE_INVALID,
        Diagnostic.APPROVAL_ARTIFACT_ROOT_NOT_OBJECT,
        Diagnostic.APPROVAL_ARTIFACT_PARSED_IDENTITY_BINDING_INVALID,
        Diagnostic.APPROVAL_ARTIFACT_OBJECT_CONTRACT_INVALID,
    )
    assert not hasattr(
        Diagnostic,
        "APPROVAL_ARTIFACT_EXACT_BUILTIN_CONVERSION_INVALID",
    )
    assert MAX_APPROVAL_ARTIFACT_COMPOSITION_DIAGNOSTICS == 1


def test_result_fields_are_exact() -> None:
    assert tuple(
        item.name
        for item in fields(
            Step2MarketSourcePolicyApprovalArtifactValidationResult
        )
    ) == (
        "result_version",
        "composition_version",
        "composition_state",
        "parser_result",
        "approval_object_validation_result",
        "artifact_validation_identity_sha256",
        "root_object_check_performed",
        "root_object_valid",
        "exact_builtin_conversion_performed",
        "exact_builtin_conversion_valid",
        "parsed_identity_recheck_performed",
        "parsed_identity_recheck_matches",
        "object_contract_validation_performed",
        "composition_validation_performed",
        "artifact_contract_valid",
        "diagnostics",
        "authority_scope",
        "not_trade_authorization",
        "trade_permission_effect",
        "operator_authentication_performed",
        "source_resolution_performed",
        "freshness_evaluation_performed",
        "universe_resolution_performed",
        "candidate_validity_evaluated",
        "activation_evaluation_performed",
        "publication_evaluation_performed",
        "workflow_permission_evaluated",
        "order_compilation_evaluated",
    )


def test_input_absent_branch_is_exact() -> None:
    result = _validate(None)
    assert result.parser_result.parse_state is ParseState.INPUT_ABSENT
    _assert_branch(
        result,
        state=State.INPUT_ABSENT,
        object_result=False,
        artifact_identity=False,
        root=(False, None),
        conversion=(False, None),
        recheck=(False, None),
        object_validation=False,
        composition_validation=False,
        artifact_valid=None,
        diagnostics=(Diagnostic.APPROVAL_ARTIFACT_INPUT_MISSING,),
    )


@pytest.mark.parametrize("value", [True, 1, "{}", bytearray(b"{}"), memoryview(b"{}")])
def test_input_type_invalid_branch_is_exact(value: object) -> None:
    result = _validate(value)
    assert result.parser_result.parse_state is ParseState.INPUT_TYPE_INVALID
    _assert_branch(
        result,
        state=State.INPUT_TYPE_INVALID,
        object_result=False,
        artifact_identity=False,
        root=(False, None),
        conversion=(False, None),
        recheck=(False, None),
        object_validation=False,
        composition_validation=False,
        artifact_valid=None,
        diagnostics=(Diagnostic.APPROVAL_ARTIFACT_INPUT_TYPE_INVALID,),
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"   ",
        b"\xef\xbb\xbf{}",
        b"\xff",
        b"{",
        b'{"a":null,"a":null}',
        b"true",
        b"[[[[[[[[[null]]]]]]]]]",
    ],
)
def test_raw_parser_failures_collapse_to_exact_outer_branch(raw: bytes) -> None:
    result = _validate(raw)
    assert result.parser_result.parse_state is not ParseState.VALID
    _assert_branch(
        result,
        state=State.RAW_PARSE_INVALID,
        object_result=False,
        artifact_identity=False,
        root=(False, None),
        conversion=(False, None),
        recheck=(False, None),
        object_validation=False,
        composition_validation=True,
        artifact_valid=False,
        diagnostics=(Diagnostic.APPROVAL_ARTIFACT_RAW_PARSE_INVALID,),
    )
    assert len(result.parser_result.diagnostics) == 1


def test_oversized_parser_failure_propagates_to_raw_parse_invalid() -> None:
    raw = b"x" * 2_097_153
    result = _validate(raw)
    assert result.parser_result.parse_state is ParseState.RAW_SIZE_INVALID
    assert result.parser_result.raw_artifact_size_bytes == len(raw)
    assert result.parser_result.raw_artifact_sha256 is None
    _assert_branch(
        result,
        state=State.RAW_PARSE_INVALID,
        object_result=False,
        artifact_identity=False,
        root=(False, None),
        conversion=(False, None),
        recheck=(False, None),
        object_validation=False,
        composition_validation=True,
        artifact_valid=False,
        diagnostics=(Diagnostic.APPROVAL_ARTIFACT_RAW_PARSE_INVALID,),
    )


@pytest.mark.parametrize("raw", [b"[]", b'"approval"', b"null"])
def test_parse_valid_non_object_roots_are_owned_by_composition(raw: bytes) -> None:
    result = _validate(raw)
    assert result.parser_result.parse_valid is True
    _assert_branch(
        result,
        state=State.ROOT_TYPE_INVALID,
        object_result=False,
        artifact_identity=False,
        root=(True, False),
        conversion=(False, None),
        recheck=(False, None),
        object_validation=False,
        composition_validation=True,
        artifact_valid=False,
        diagnostics=(Diagnostic.APPROVAL_ARTIFACT_ROOT_NOT_OBJECT,),
    )


def test_structurally_invalid_object_branch_is_exact() -> None:
    result = _validate(b"{}")
    assert result.approval_object_validation_result is not None
    assert (
        result.approval_object_validation_result.approval_state
        is ObjectState.STRUCTURALLY_INVALID
    )
    _assert_branch(
        result,
        state=State.OBJECT_CONTRACT_INVALID,
        object_result=True,
        artifact_identity=True,
        root=(True, True),
        conversion=(True, True),
        recheck=(True, True),
        object_validation=True,
        composition_validation=True,
        artifact_valid=False,
        diagnostics=(Diagnostic.APPROVAL_ARTIFACT_OBJECT_CONTRACT_INVALID,),
    )


def test_semantically_invalid_object_branch_is_exact() -> None:
    approval = _approval()
    approval["operator_approved_source_policy_sha256"] = "0" * 64
    result = _validate(_canonical_json(approval))
    object_result = result.approval_object_validation_result
    assert object_result is not None
    assert object_result.approval_state is ObjectState.SEMANTICALLY_INVALID
    assert object_result.approval_identity_matches is False
    _assert_branch(
        result,
        state=State.OBJECT_CONTRACT_INVALID,
        object_result=True,
        artifact_identity=True,
        root=(True, True),
        conversion=(True, True),
        recheck=(True, True),
        object_validation=True,
        composition_validation=True,
        artifact_valid=False,
        diagnostics=(Diagnostic.APPROVAL_ARTIFACT_OBJECT_CONTRACT_INVALID,),
    )


@pytest.mark.parametrize(
    ("kind", "expected_policy", "expected_declared"),
    [
        ("invalid_policy", None, "valid"),
        ("self_supersession", "policy_v1", "valid"),
        ("invalid_declared", "policy_v1", None),
    ],
)
def test_local_retention_rules_match_object_result_contract(
    kind: str,
    expected_policy: str | None,
    expected_declared: str | None,
) -> None:
    approval = _approval()
    if kind == "invalid_policy":
        approval["approval_content"]["policy_version"] = "latest"
        approval["operator_approved_source_policy_sha256"] = hashlib.sha256(
            _APPROVAL_DOMAIN + _canonical_json(approval["approval_content"])
        ).hexdigest()
    elif kind == "self_supersession":
        approval["approval_content"]["supersedes_policy_version"] = (
            "policy_v1"
        )
        approval["operator_approved_source_policy_sha256"] = hashlib.sha256(
            _APPROVAL_DOMAIN + _canonical_json(approval["approval_content"])
        ).hexdigest()
    else:
        approval["operator_approved_source_policy_sha256"] = "G" * 64
    result = _validate(_canonical_json(approval))
    object_result = result.approval_object_validation_result
    assert object_result is not None
    assert result.composition_state is State.OBJECT_CONTRACT_INVALID
    assert object_result.approval_policy_version == expected_policy
    if expected_declared == "valid":
        assert object_result.declared_operator_approved_source_policy_sha256 == (
            approval["operator_approved_source_policy_sha256"]
        )
    else:
        assert object_result.declared_operator_approved_source_policy_sha256 is None


@pytest.mark.parametrize(
    ("sources", "state", "object_state", "source_count"),
    [
        ([], State.VALID_EMPTY, ObjectState.VALID_EMPTY, 0),
        ([_source()], State.VALID_NONEMPTY, ObjectState.VALID_NONEMPTY, 1),
    ],
)
def test_valid_branches_are_exact(
    sources: list[dict[str, Any]],
    state: State,
    object_state: ObjectState,
    source_count: int,
) -> None:
    result = _validate(_raw_approval(sources=sources))
    object_result = result.approval_object_validation_result
    assert object_result is not None
    assert object_result.approval_state is object_state
    assert object_result.source_count == source_count
    assert object_result.approval_identity_matches is True
    _assert_branch(
        result,
        state=state,
        object_result=True,
        artifact_identity=True,
        root=(True, True),
        conversion=(True, True),
        recheck=(True, True),
        object_validation=True,
        composition_validation=True,
        artifact_valid=True,
        diagnostics=(),
    )


def test_parser_is_called_exactly_once_and_receives_original_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval()
    calls: list[object] = []

    def wrapper(value: object) -> Step2MarketSourcePolicyApprovalParseResult:
        calls.append(value)
        return parse_step2_market_source_policy_approvals_bytes(value)

    monkeypatch.setattr(
        artifact_contract,
        "parse_step2_market_source_policy_approvals_bytes",
        wrapper,
    )
    result = _validate(raw)
    assert result.artifact_contract_valid is True
    assert calls == [raw]
    assert calls[0] is raw


@pytest.mark.parametrize(
    "value",
    [None, "{}", b"", b"null", b"{}"],
)
def test_parser_is_called_exactly_once_across_all_stage_classes(
    monkeypatch: pytest.MonkeyPatch,
    value: object,
) -> None:
    calls: list[object] = []

    def wrapper(candidate: object) -> Step2MarketSourcePolicyApprovalParseResult:
        calls.append(candidate)
        return parse_step2_market_source_policy_approvals_bytes(candidate)

    monkeypatch.setattr(
        artifact_contract,
        "parse_step2_market_source_policy_approvals_bytes",
        wrapper,
    )
    _validate(value)
    assert calls == [value]


def test_object_validator_is_not_called_before_binding_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(value: object) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
        raise AssertionError(value)

    monkeypatch.setattr(
        artifact_contract,
        "validate_step2_market_source_policy_approvals_object",
        forbidden,
    )
    assert _validate(b"null").composition_state is State.ROOT_TYPE_INVALID
    assert _validate(b"{").composition_state is State.RAW_PARSE_INVALID


def test_object_validator_is_called_once_with_exact_converted_builtins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval(sources=[_source()])
    captured: list[dict[str, Any]] = []

    def wrapper(
        value: object,
    ) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
        assert type(value) is dict
        captured.append(value)
        return validate_step2_market_source_policy_approvals_object(value)

    monkeypatch.setattr(
        artifact_contract,
        "validate_step2_market_source_policy_approvals_object",
        wrapper,
    )
    result = _validate(raw)
    assert result.artifact_contract_valid is True
    assert len(captured) == 1
    converted = captured[0]
    assert tuple(converted) == (
        "schema_version",
        "operator_approved_source_policy_sha256",
        "approval_content",
    )
    assert type(converted["approval_content"]) is dict
    assert type(converted["approval_content"]["sources"]) is list
    assert type(converted["approval_content"]["sources"][0]) is dict
    assert type(
        converted["approval_content"]["sources"][0]["permissions"]
    ) is list
    assert not hasattr(result, "converted_root")


def test_caller_visible_result_retains_no_converted_mutable_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval()
    captured: list[dict[str, Any]] = []

    def wrapper(
        value: object,
    ) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
        assert type(value) is dict
        captured.append(value)
        return validate_step2_market_source_policy_approvals_object(value)

    monkeypatch.setattr(
        artifact_contract,
        "validate_step2_market_source_policy_approvals_object",
        wrapper,
    )
    result = _validate(raw)
    identity = result.artifact_validation_identity_sha256
    object_result = result.approval_object_validation_result
    captured[0].clear()
    assert result.artifact_validation_identity_sha256 == identity
    assert result.approval_object_validation_result is object_result
    assert object_result is not None
    assert object_result.approval_object_valid is True


def test_conversion_preserves_nested_source_and_array_order_and_exact_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = (
        '{"z":[null,"é",{"b":null,"a":[]}],"a":{}}'
    ).encode("utf-8")
    captured: list[dict[str, Any]] = []

    def wrapper(
        value: object,
    ) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
        assert type(value) is dict
        captured.append(value)
        return validate_step2_market_source_policy_approvals_object(value)

    monkeypatch.setattr(
        artifact_contract,
        "validate_step2_market_source_policy_approvals_object",
        wrapper,
    )
    result = _validate(raw)
    assert result.composition_state is State.OBJECT_CONTRACT_INVALID
    assert len(captured) == 1
    converted = captured[0]
    assert tuple(converted) == ("z", "a")
    assert type(converted["z"]) is list
    assert converted["z"][0] is None
    assert type(converted["z"][1]) is str
    assert converted["z"][1] == "é"
    assert type(converted["z"][2]) is dict
    assert tuple(converted["z"][2]) == ("b", "a")
    assert type(converted["z"][2]["a"]) is list
    assert type(converted["a"]) is dict


def test_complete_root_identity_recheck_matches_independent_oracle() -> None:
    raw = _raw_approval(sources=[_source()])
    decoded = json.loads(raw)
    expected = hashlib.sha256(
        _PARSED_DOMAIN + _canonical_json(decoded)
    ).hexdigest()
    result = _validate(raw)
    assert result.parsed_identity_recheck_performed is True
    assert result.parsed_identity_recheck_matches is True
    assert result.parser_result.parsed_value_identity_sha256 == expected


def test_local_approval_content_identity_matches_independent_oracle() -> None:
    approval = _approval(sources=[_source()])
    expected = hashlib.sha256(
        _APPROVAL_DOMAIN + _canonical_json(approval["approval_content"])
    ).hexdigest()
    result = _validate(_canonical_json(approval))
    object_result = result.approval_object_validation_result
    assert result.artifact_contract_valid is True
    assert object_result is not None
    assert object_result.canonical_approval_content_sha256 == expected
    assert (
        object_result.declared_operator_approved_source_policy_sha256
        == expected
    )
    assert object_result.approval_identity_matches is True


def test_recheck_is_key_order_neutral_and_array_order_sensitive() -> None:
    first_keys = _validate(b'{"b":null,"a":["x","y"]}')
    second_keys = _validate(b'{"a":["x","y"],"b":null}')
    reversed_array = _validate(b'{"a":["y","x"],"b":null}')
    assert first_keys.parsed_identity_recheck_matches is True
    assert second_keys.parsed_identity_recheck_matches is True
    assert reversed_array.parsed_identity_recheck_matches is True
    assert first_keys.parser_result.parsed_value_identity_sha256 == (
        second_keys.parser_result.parsed_value_identity_sha256
    )
    assert first_keys.parser_result.raw_artifact_sha256 != (
        second_keys.parser_result.raw_artifact_sha256
    )
    assert second_keys.parser_result.parsed_value_identity_sha256 != (
        reversed_array.parser_result.parsed_value_identity_sha256
    )


def test_complete_root_recheck_preserves_json_canonical_rules() -> None:
    raw = (
        b'{"schema_version":"wrong","z":["\\u00e9","\\ud83d\\ude00",null],'
        b'"operator_approved_source_policy_sha256":"0",'
        b'"approval_content":{"control":"\\u0000\\n\\t",'
        b'"slash":"a\\/b","quote":"\\\"","backslash":"\\\\"}}'
    )
    decoded = json.loads(raw)
    result = _validate(raw)
    expected = hashlib.sha256(
        _PARSED_DOMAIN + _canonical_json(decoded)
    ).hexdigest()
    assert result.composition_state is State.OBJECT_CONTRACT_INVALID
    assert result.parser_result.parsed_value_identity_sha256 == expected
    assert result.parsed_identity_recheck_matches is True


def test_parsed_identity_mismatch_fails_closed_before_object_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval()
    genuine = parse_step2_market_source_policy_approvals_bytes(raw)
    assert genuine.parsed_value_identity_sha256 != "0" * 64
    altered = _replace_frozen_fields(
        genuine,
        parsed_value_identity_sha256="0" * 64,
    )
    parser_calls = 0

    def parser_wrapper(value: object) -> Step2MarketSourcePolicyApprovalParseResult:
        nonlocal parser_calls
        parser_calls += 1
        assert value is raw
        return altered

    def forbidden(value: object) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
        raise AssertionError(value)

    monkeypatch.setattr(
        artifact_contract,
        "parse_step2_market_source_policy_approvals_bytes",
        parser_wrapper,
    )
    monkeypatch.setattr(
        artifact_contract,
        "validate_step2_market_source_policy_approvals_object",
        forbidden,
    )
    result = _validate(raw)
    assert parser_calls == 1
    _assert_branch(
        result,
        state=State.PARSED_IDENTITY_BINDING_INVALID,
        object_result=False,
        artifact_identity=False,
        root=(True, True),
        conversion=(True, True),
        recheck=(True, False),
        object_validation=False,
        composition_validation=True,
        artifact_valid=False,
        diagnostics=(
            Diagnostic.APPROVAL_ARTIFACT_PARSED_IDENTITY_BINDING_INVALID,
        ),
    )


def test_binding_record_shape_and_identity_match_independent_oracle() -> None:
    result = _validate(_raw_approval(sources=[_source()]))
    record = _binding_record_oracle(result)
    assert tuple(sorted(record)) == (
        "approval_object_result",
        "artifact_contract_valid",
        "composition_diagnostics",
        "composition_state",
        "composition_validation_performed",
        "composition_version",
        "conversion",
        "object_contract_validation_performed",
        "parsed_identity_binding",
        "parser_result",
        "result_version",
        "root_object",
    )
    assert tuple(sorted(record["approval_object_result"])) == (
        "approval_identity_matches",
        "approval_object_valid",
        "approval_policy_version",
        "approval_schema_version",
        "approval_state",
        "canonical_approval_content_sha256",
        "declared_operator_approved_source_policy_sha256",
        "diagnostics",
        "object_structure_valid",
        "object_validation_performed",
        "result_version",
        "semantic_validation_performed",
        "source_count",
    )
    assert tuple(sorted(record["parser_result"])) == (
        "diagnostics",
        "parse_state",
        "parse_valid",
        "parsed_value_available",
        "parsed_value_identity_sha256",
        "parser_version",
        "parsing_performed",
        "raw_artifact_sha256",
        "raw_artifact_size_bytes",
        "result_version",
    )
    assert tuple(sorted(record["conversion"])) == (
        "exact_builtin_conversion_performed",
        "exact_builtin_conversion_valid",
    )
    assert tuple(sorted(record["parsed_identity_binding"])) == (
        "parsed_identity_recheck_matches",
        "parsed_identity_recheck_performed",
    )
    assert tuple(sorted(record["root_object"])) == (
        "root_object_check_performed",
        "root_object_valid",
    )
    encoded, expected = _binding_identity_oracle(result)
    assert result.artifact_validation_identity_sha256 == expected
    assert result.artifact_validation_identity_sha256 != hashlib.sha256(
        encoded
    ).hexdigest()


def test_binding_identity_covers_raw_spelling_and_keeps_parsed_identity() -> None:
    compact = _validate(_raw_approval())
    pretty = _validate(_raw_approval(indent=2))
    assert compact.parser_result.raw_artifact_sha256 != (
        pretty.parser_result.raw_artifact_sha256
    )
    assert compact.parser_result.parsed_value_identity_sha256 == (
        pretty.parser_result.parsed_value_identity_sha256
    )
    assert compact.artifact_validation_identity_sha256 != (
        pretty.artifact_validation_identity_sha256
    )
    assert compact.artifact_validation_identity_sha256 == (
        _binding_identity_oracle(compact)[1]
    )
    assert pretty.artifact_validation_identity_sha256 == (
        _binding_identity_oracle(pretty)[1]
    )


def test_binding_identity_changes_for_every_identity_bearing_field() -> None:
    result = _validate(_raw_approval())
    record = _binding_record_oracle(result)
    original = hashlib.sha256(
        _ARTIFACT_DOMAIN + _canonical_json(record)
    ).hexdigest()
    mutations: tuple[tuple[tuple[str, ...], Any], ...] = (
        (("result_version",), "different_result_v2"),
        (("composition_version",), "different_composition_v2"),
        (("composition_state",), "object_contract_invalid"),
        (("composition_validation_performed",), False),
        (("artifact_contract_valid",), False),
        (
            ("composition_diagnostics",),
            ["approval_artifact_object_contract_invalid"],
        ),
        (("object_contract_validation_performed",), False),
        (("root_object", "root_object_check_performed"), False),
        (("root_object", "root_object_valid"), False),
        (
            ("conversion", "exact_builtin_conversion_performed"),
            False,
        ),
        (("conversion", "exact_builtin_conversion_valid"), False),
        (
            (
                "parsed_identity_binding",
                "parsed_identity_recheck_performed",
            ),
            False,
        ),
        (
            (
                "parsed_identity_binding",
                "parsed_identity_recheck_matches",
            ),
            False,
        ),
        (("parser_result", "result_version"), "different_parse_result_v2"),
        (("parser_result", "parser_version"), "different_parser_v2"),
        (("parser_result", "parse_state"), "json_grammar_invalid"),
        (("parser_result", "parse_valid"), False),
        (("parser_result", "parsing_performed"), False),
        (("parser_result", "parsed_value_available"), False),
        (("parser_result", "raw_artifact_size_bytes"), 1),
        (("parser_result", "raw_artifact_sha256"), "0" * 64),
        (
            ("parser_result", "parsed_value_identity_sha256"),
            "0" * 64,
        ),
        (("parser_result", "diagnostics"), ["raw_approval_json_invalid"]),
        (
            ("approval_object_result", "result_version"),
            "different_object_result_v2",
        ),
        (
            ("approval_object_result", "approval_schema_version"),
            "different_schema_v2",
        ),
        (
            ("approval_object_result", "approval_policy_version"),
            "policy_v2",
        ),
        (
            (
                "approval_object_result",
                "declared_operator_approved_source_policy_sha256",
            ),
            "0" * 64,
        ),
        (
            (
                "approval_object_result",
                "canonical_approval_content_sha256",
            ),
            "0" * 64,
        ),
        (
            ("approval_object_result", "approval_identity_matches"),
            False,
        ),
        (("approval_object_result", "object_structure_valid"), False),
        (
            ("approval_object_result", "object_validation_performed"),
            False,
        ),
        (
            (
                "approval_object_result",
                "semantic_validation_performed",
            ),
            False,
        ),
        (
            ("approval_object_result", "approval_state"),
            "semantically_invalid",
        ),
        (("approval_object_result", "approval_object_valid"), False),
        (("approval_object_result", "source_count"), 1),
        (
            ("approval_object_result", "diagnostics"),
            ["approval_identity_mismatch"],
        ),
    )
    for path, value in mutations:
        changed = json.loads(json.dumps(record))
        target = changed
        for key in path[:-1]:
            target = target[key]
        assert target[path[-1]] != value
        target[path[-1]] = value
        assert hashlib.sha256(
            _ARTIFACT_DOMAIN + _canonical_json(changed)
        ).hexdigest() != original

    null_record = _binding_record_oracle(_validate(b"{}"))
    null_original = hashlib.sha256(
        _ARTIFACT_DOMAIN + _canonical_json(null_record)
    ).hexdigest()
    null_mutations = (
        ("approval_schema_version", APPROVAL_SCHEMA_VERSION),
        ("approval_policy_version", "policy_v1"),
        ("declared_operator_approved_source_policy_sha256", "1" * 64),
        ("canonical_approval_content_sha256", "2" * 64),
        ("approval_identity_matches", False),
        ("approval_object_valid", False),
        ("source_count", 0),
    )
    for key, value in null_mutations:
        changed = json.loads(json.dumps(null_record))
        assert changed["approval_object_result"][key] is None
        changed["approval_object_result"][key] = value
        assert hashlib.sha256(
            _ARTIFACT_DOMAIN + _canonical_json(changed)
        ).hexdigest() != null_original


def test_structural_object_result_binds_json_nulls() -> None:
    result = _validate(b"{}")
    record = _binding_record_oracle(result)
    nested = record["approval_object_result"]
    assert nested["approval_schema_version"] is None
    assert nested["approval_policy_version"] is None
    assert nested["canonical_approval_content_sha256"] is None
    encoded, expected = _binding_identity_oracle(result)
    assert b'"approval_schema_version":null' in encoded
    assert result.artifact_validation_identity_sha256 == expected


def test_binding_record_is_bounded_far_below_frozen_limit() -> None:
    result = _validate(_raw_approval(sources=[_source()]))
    encoded, _ = _binding_identity_oracle(result)
    assert len(encoded) < 5_071
    assert len(encoded) < MAX_ARTIFACT_VALIDATION_BINDING_BYTES


def test_forced_binding_overflow_is_invariant_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval()
    calls = 0

    def counting_sha256(value: bytes = b"") -> Any:
        nonlocal calls
        calls += 1
        return hashlib.sha256(value)

    monkeypatch.setattr(
        artifact_contract,
        "MAX_ARTIFACT_VALIDATION_BINDING_BYTES",
        1,
    )
    monkeypatch.setattr(artifact_contract, "sha256", counting_sha256)
    _assert_invariant(lambda: _validate(raw))
    assert calls == 2


@pytest.mark.parametrize(
    "raw",
    [None, "{}", b"", b"[]"],
)
def test_artifact_identity_is_absent_before_object_result(raw: object) -> None:
    result = _validate(raw)
    assert result.object_contract_validation_performed is False
    assert result.approval_object_validation_result is None
    assert result.artifact_validation_identity_sha256 is None


def test_result_is_frozen_slotted_and_rejects_boolean_coercion() -> None:
    result = _validate(_raw_approval())
    assert type(result) is Step2MarketSourcePolicyApprovalArtifactValidationResult
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.artifact_contract_valid = False  # type: ignore[misc]
    _assert_boolean_rejected(result)


def test_result_owns_explicit_blocking_reconstruction_methods() -> None:
    result_type = Step2MarketSourcePolicyApprovalArtifactValidationResult
    methods = {
        name: result_type.__dict__[name]
        for name in ("__reduce__", "__reduce_ex__", "__setstate__")
    }
    assert methods["__reduce__"] is result_type.__reduce__
    assert methods["__reduce_ex__"] is result_type.__reduce_ex__
    assert methods["__setstate__"] is result_type.__setstate__
    assert methods["__reduce__"] is not object.__reduce__
    assert methods["__reduce_ex__"] is not object.__reduce_ex__
    assert methods["__reduce__"] is not copyreg._reconstructor
    assert methods["__setstate__"] is not dataclasses._dataclass_setstate
    for method in methods.values():
        assert method.__module__ == artifact_contract.__name__
        assert Path(method.__code__.co_filename).resolve() == Path(
            artifact_contract.__file__
        ).resolve()

    result = _validate(_raw_approval())
    before = tuple(getattr(result, item.name) for item in fields(type(result)))
    before_bytes = pickle.dumps(before)
    _assert_construction_rejected(lambda: result.__setstate__(object()))
    after = tuple(getattr(result, item.name) for item in fields(type(result)))
    assert after == before
    assert pickle.dumps(after) == before_bytes


def test_manual_reduction_entrypoints_are_blocked_without_mutation() -> None:
    result = _validate(_raw_approval())
    before = tuple(getattr(result, item.name) for item in fields(type(result)))
    before_bytes = pickle.dumps(before)
    original_identity = result.artifact_validation_identity_sha256
    original_parser = result.parser_result
    original_object = result.approval_object_validation_result
    original_state = result.composition_state
    original_validity = result.artifact_contract_valid

    _assert_construction_rejected(result.__reduce__)
    for protocol in range(6):
        _assert_construction_rejected(
            lambda protocol=protocol: result.__reduce_ex__(protocol)
        )

    after = tuple(getattr(result, item.name) for item in fields(type(result)))
    assert after == before
    assert pickle.dumps(after) == before_bytes
    assert result.artifact_validation_identity_sha256 == original_identity
    assert result.parser_result is original_parser
    assert result.approval_object_validation_result is original_object
    assert result.composition_state is original_state
    assert result.artifact_contract_valid is original_validity


def test_reduce_ex_rejects_an_unreadable_protocol_without_inspection() -> None:
    operations: list[str] = []

    class UnreadableProtocol:
        def __getattribute__(self, name: str) -> Any:
            operations.append(f"attribute:{name}")
            raise AssertionError("protocol attribute access")

        def __int__(self) -> int:
            operations.append("int")
            raise AssertionError("protocol integer conversion")

        def __index__(self) -> int:
            operations.append("index")
            raise AssertionError("protocol index conversion")

        def __bool__(self) -> bool:
            operations.append("bool")
            raise AssertionError("protocol Boolean conversion")

        def __eq__(self, other: object) -> bool:
            operations.append("equal")
            raise AssertionError("protocol comparison")

        def __lt__(self, other: object) -> bool:
            operations.append("less-than")
            raise AssertionError("protocol comparison")

        def __iter__(self) -> Any:
            operations.append("iterate")
            raise AssertionError("protocol iteration")

        def __getitem__(self, key: object) -> Any:
            operations.append("getitem")
            raise AssertionError("protocol indexing")

        def __len__(self) -> int:
            operations.append("length")
            raise AssertionError("protocol length")

        def __repr__(self) -> str:
            operations.append("representation")
            raise AssertionError("protocol representation")

    result = _validate(_raw_approval())
    before = tuple(getattr(result, item.name) for item in fields(type(result)))
    _assert_construction_rejected(
        lambda: result.__reduce_ex__(UnreadableProtocol())
    )
    assert operations == []
    assert tuple(
        getattr(result, item.name) for item in fields(type(result))
    ) == before


def test_getstate_cannot_restore_initialized_or_uninitialized_result() -> None:
    result = _validate(_raw_approval())
    assert hasattr(result, "__getstate__")
    before = tuple(getattr(result, item.name) for item in fields(type(result)))
    before_bytes = pickle.dumps(before)
    state = result.__getstate__()
    assert type(state) is list
    assert len(state) == len(fields(type(result)))

    _assert_construction_rejected(lambda: result.__setstate__(state))
    after = tuple(getattr(result, item.name) for item in fields(type(result)))
    assert after == before
    assert pickle.dumps(after) == before_bytes

    uninitialized = object.__new__(
        Step2MarketSourcePolicyApprovalArtifactValidationResult
    )
    assert all(
        not hasattr(uninitialized, item.name)
        for item in fields(type(result))
    )
    _assert_construction_rejected(
        lambda: uninitialized.__setstate__(state)
    )
    assert all(
        not hasattr(uninitialized, item.name)
        for item in fields(type(result))
    )


def test_forged_state_cannot_mutate_a_public_result() -> None:
    result = _validate(_raw_approval())
    other = _validate(_raw_approval(sources=[_source()]))
    before = tuple(getattr(result, item.name) for item in fields(type(result)))
    before_bytes = pickle.dumps(before)
    names = tuple(item.name for item in fields(type(result)))
    forged_state = list(before)
    replacements = {
        "artifact_validation_identity_sha256": "0" * 64,
        "parser_result": other.parser_result,
        "approval_object_validation_result": (
            other.approval_object_validation_result
        ),
        "composition_state": State.OBJECT_CONTRACT_INVALID,
        "artifact_contract_valid": False,
    }
    for name, replacement in replacements.items():
        index = names.index(name)
        assert forged_state[index] != replacement
        forged_state[index] = replacement

    original_identity = result.artifact_validation_identity_sha256
    original_parser = result.parser_result
    original_object = result.approval_object_validation_result
    original_state = result.composition_state
    original_validity = result.artifact_contract_valid
    _assert_construction_rejected(
        lambda: result.__setstate__(forged_state)
    )

    after = tuple(getattr(result, item.name) for item in fields(type(result)))
    assert after == before
    assert pickle.dumps(after) == before_bytes
    assert result.artifact_validation_identity_sha256 == original_identity
    assert result.parser_result is original_parser
    assert result.approval_object_validation_result is original_object
    assert result.composition_state is original_state
    assert result.artifact_contract_valid is original_validity


def test_direct_result_construction_is_sealed() -> None:
    result = _validate(_raw_approval())
    supplied_fields = {
        item.name: getattr(result, item.name)
        for item in fields(type(result))
    }
    _assert_construction_rejected(
        lambda: Step2MarketSourcePolicyApprovalArtifactValidationResult()
    )
    _assert_construction_rejected(
        lambda: Step2MarketSourcePolicyApprovalArtifactValidationResult(
            **supplied_fields
        )
    )


def test_dataclasses_replace_cannot_forge_digest_or_nested_results() -> None:
    result = _validate(_raw_approval())
    other = _validate(_raw_approval(indent=2))
    assert result.parser_result.raw_artifact_sha256 != (
        other.parser_result.raw_artifact_sha256
    )
    _assert_construction_rejected(
        lambda: replace(
            result,
            artifact_validation_identity_sha256="0" * 64,
        )
    )
    _assert_construction_rejected(
        lambda: replace(result, parser_result=other.parser_result)
    )
    _assert_construction_rejected(
        lambda: replace(
            result,
            approval_object_validation_result=(
                other.approval_object_validation_result
            ),
        )
    )


def test_copy_deepcopy_and_pickle_reconstruction_are_blocked() -> None:
    result = _validate(_raw_approval())
    reconstructors: list[Callable[[], Any]] = [
        lambda: copy.copy(result),
        lambda: copy.deepcopy(result),
    ]
    reconstructors.extend(
        lambda protocol=protocol: pickle.loads(
            pickle.dumps(result, protocol=protocol)
        )
        for protocol in range(6)
    )
    for reconstruct in reconstructors:
        _assert_construction_rejected(reconstruct)


def test_nested_results_retain_their_existing_boolean_errors() -> None:
    result = _validate(_raw_approval())
    with pytest.raises(TypeError) as parser_error:
        bool(result.parser_result)
    assert "inspect parse_valid explicitly" in str(parser_error.value)
    object_result = result.approval_object_validation_result
    assert object_result is not None
    with pytest.raises(TypeError) as object_error:
        bool(object_result)
    assert "inspect approval_object_valid explicitly" in str(object_error.value)


@pytest.mark.parametrize(
    ("changes", "raw"),
    [
        ({"parser_version": "wrong"}, b"{}"),
        ({"result_version": "wrong"}, b"{}"),
        ({"raw_artifact_sha256": "A" * 64}, b"{}"),
        ({"parsed_value_available": False}, b"{}"),
        ({"diagnostics": ()}, b"{"),
    ],
)
def test_impossible_parser_result_invariants_raise_fixed_exception(
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, Any],
    raw: bytes,
) -> None:
    genuine = parse_step2_market_source_policy_approvals_bytes(raw)
    altered = _replace_frozen_fields(genuine, **changes)
    monkeypatch.setattr(
        artifact_contract,
        "parse_step2_market_source_policy_approvals_bytes",
        lambda value: altered,
    )
    _assert_invariant(lambda: _validate(raw))


def test_wrong_parser_result_exact_class_is_invariant_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        artifact_contract,
        "parse_step2_market_source_policy_approvals_bytes",
        lambda value: object(),
    )
    _assert_invariant(lambda: _validate(b"{}"))


@pytest.mark.parametrize("kind", ["unknown", "duplicate", "cycle"])
def test_impossible_frozen_tree_is_invariant_failure(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    raw = b"{}"
    if kind == "cycle":
        child = FrozenJsonArray(())
        root = FrozenJsonObject((("child", child),))
        object.__setattr__(child, "items", (child,))
    else:
        root = FrozenJsonObject(())
        items: object = (
            (("child", 1),)
            if kind == "unknown"
            else (("same", None), ("same", None))
        )
        object.__setattr__(root, "items", items)
    altered = _patched_parser_result(raw, immutable_parsed_value=root)
    monkeypatch.setattr(
        artifact_contract,
        "parse_step2_market_source_policy_approvals_bytes",
        lambda value: altered,
    )
    _assert_invariant(lambda: _validate(raw))


@pytest.mark.parametrize("kind", ["depth", "nodes", "string", "members", "items"])
def test_defensive_frozen_tree_bounds_are_invariant_failures(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    if kind == "depth":
        child: Any = None
        for _ in range(MAX_JSON_NESTING_DEPTH + 1):
            child = FrozenJsonArray((child,))
        root = FrozenJsonObject((("child", child),))
    elif kind == "nodes":
        block = FrozenJsonArray((None,) * MAX_ARRAY_ITEM_COUNT)
        root = FrozenJsonObject(
            (("child", FrozenJsonArray((block,) * 4)),)
        )
    elif kind == "string":
        root = FrozenJsonObject(
            (("child", "x" * (MAX_DECODED_STRING_CODE_POINTS + 1)),)
        )
    elif kind == "members":
        root = FrozenJsonObject(
            tuple(
                (f"k{index}", None)
                for index in range(MAX_OBJECT_MEMBER_COUNT + 1)
            )
        )
    else:
        root = FrozenJsonObject(
            (("child", FrozenJsonArray((None,) * (MAX_ARRAY_ITEM_COUNT + 1))),)
        )
    altered = _patched_parser_result(b"{}", immutable_parsed_value=root)
    monkeypatch.setattr(
        artifact_contract,
        "parse_step2_market_source_policy_approvals_bytes",
        lambda value: altered,
    )
    _assert_invariant(lambda: _validate(b"{}"))


def test_exact_defensive_depth_and_repeated_node_occurrences_pass() -> None:
    child: Any = None
    for _ in range(MAX_JSON_NESTING_DEPTH - 1):
        child = FrozenJsonArray((child,))
    shared = FrozenJsonArray(("same",))
    root = FrozenJsonObject((("deep", child), ("a", shared), ("b", shared)))
    converted = artifact_contract._convert_frozen_root_to_exact_builtins(root)
    assert converted["a"] == ["same"]
    assert converted["b"] == ["same"]
    assert converted["a"] is not converted["b"]


def test_exact_defensive_node_string_member_and_item_bounds_pass() -> None:
    blocks = (
        FrozenJsonArray((None,) * 1024),
        FrozenJsonArray((None,) * 1024),
        FrozenJsonArray((None,) * 1024),
        FrozenJsonArray((None,) * 1018),
    )
    exact_nodes = FrozenJsonObject(
        (("values", FrozenJsonArray(blocks)),)
    )
    converted_nodes = artifact_contract._convert_frozen_root_to_exact_builtins(
        exact_nodes
    )
    assert sum(len(block) for block in converted_nodes["values"]) == 4090

    exact_string = FrozenJsonObject(
        (("value", "x" * MAX_DECODED_STRING_CODE_POINTS),)
    )
    assert len(
        artifact_contract._convert_frozen_root_to_exact_builtins(
            exact_string
        )["value"]
    ) == MAX_DECODED_STRING_CODE_POINTS

    exact_members = FrozenJsonObject(
        tuple((f"k{index}", None) for index in range(MAX_OBJECT_MEMBER_COUNT))
    )
    assert len(
        artifact_contract._convert_frozen_root_to_exact_builtins(
            exact_members
        )
    ) == MAX_OBJECT_MEMBER_COUNT

    exact_items = FrozenJsonObject(
        (("values", FrozenJsonArray((None,) * MAX_ARRAY_ITEM_COUNT)),)
    )
    assert len(
        artifact_contract._convert_frozen_root_to_exact_builtins(
            exact_items
        )["values"]
    ) == MAX_ARRAY_ITEM_COUNT


def test_canonical_full_root_overflow_is_invariant_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    large = "😀" * MAX_DECODED_STRING_CODE_POINTS
    root = FrozenJsonObject(
        tuple((f"k{index}", large) for index in range(5))
    )
    altered = _patched_parser_result(b"{}", immutable_parsed_value=root)
    monkeypatch.setattr(
        artifact_contract,
        "parse_step2_market_source_policy_approvals_bytes",
        lambda value: altered,
    )
    _assert_invariant(lambda: _validate(b"{}"))


@pytest.mark.parametrize(
    "changes",
    [
        {"result_version": "wrong"},
        {"object_validation_performed": False},
        {"approval_state": ObjectState.INPUT_ABSENT},
        {"canonical_approval_content_sha256": "A" * 64},
        {"source_count": 1},
    ],
)
def test_impossible_object_result_invariants_raise_fixed_exception(
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, Any],
) -> None:
    raw = _raw_approval()
    genuine = validate_step2_market_source_policy_approvals_object(_approval())
    altered = _replace_frozen_fields(genuine, **changes)
    monkeypatch.setattr(
        artifact_contract,
        "validate_step2_market_source_policy_approvals_object",
        lambda value: altered,
    )
    _assert_invariant(lambda: _validate(raw))


def test_wrong_object_result_exact_class_is_invariant_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        artifact_contract,
        "validate_step2_market_source_policy_approvals_object",
        lambda value: object(),
    )
    _assert_invariant(lambda: _validate(_raw_approval()))


def test_structurally_capable_object_diagnostic_mapping_is_exact() -> None:
    structurally_capable = {
        ObjectDiagnostic.APPROVAL_INPUT_INVALID,
        ObjectDiagnostic.APPROVAL_SCHEMA_VERSION_UNSUPPORTED,
        ObjectDiagnostic.APPROVAL_DECLARED_IDENTITY_INVALID,
        ObjectDiagnostic.APPROVAL_POLICY_VERSION_INVALID,
        ObjectDiagnostic.APPROVAL_PROVENANCE_INVALID,
        ObjectDiagnostic.CANONICAL_SOURCE_ID_INVALID,
        ObjectDiagnostic.SOURCE_VERSION_INVALID,
        ObjectDiagnostic.ALIAS_INVALID,
        ObjectDiagnostic.PERMISSION_FIELD_INVALID,
    }
    expected = {
        diagnostic: diagnostic in structurally_capable
        for diagnostic in ObjectDiagnostic
    }
    actual = {
        diagnostic: (
            diagnostic
            in artifact_contract._STRUCTURALLY_CAPABLE_OBJECT_DIAGNOSTICS
        )
        for diagnostic in ObjectDiagnostic
    }
    assert actual == expected


@pytest.mark.parametrize(
    "diagnostic",
    [
        ObjectDiagnostic.DUPLICATE_CANONICAL_SOURCE_ID,
        ObjectDiagnostic.DUPLICATE_ALIAS,
        ObjectDiagnostic.ALIAS_CANONICAL_COLLISION,
        ObjectDiagnostic.UNKNOWN_SOURCE_ROLE,
        ObjectDiagnostic.IMPLICIT_OR_WILDCARD_PERMISSION,
        ObjectDiagnostic.DUPLICATE_PERMISSION_TUPLE,
        ObjectDiagnostic.APPROVAL_IDENTITY_MISMATCH,
    ],
)
def test_structurally_impossible_object_diagnostics_are_invariant_failures(
    monkeypatch: pytest.MonkeyPatch,
    diagnostic: ObjectDiagnostic,
) -> None:
    raw = b"{}"
    genuine = validate_step2_market_source_policy_approvals_object({})
    assert genuine.approval_state is ObjectState.STRUCTURALLY_INVALID
    altered = replace(genuine, diagnostics=(diagnostic,))
    monkeypatch.setattr(
        artifact_contract,
        "validate_step2_market_source_policy_approvals_object",
        lambda value: altered,
    )
    _assert_invariant(lambda: _validate(raw))


@pytest.mark.parametrize(
    "kind",
    [
        "both_hashes",
        "canonical_hash",
        "declared_hash",
        "schema_version",
        "policy_version",
        "source_count",
    ],
)
def test_object_result_identity_and_metadata_must_match_local_root(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    sources = [_source()]
    raw = _raw_approval(sources=sources)
    approval = _approval(sources=[_source()])
    genuine = validate_step2_market_source_policy_approvals_object(approval)
    assert genuine.approval_state is ObjectState.VALID_NONEMPTY
    wrong_hash = "0" * 64
    assert wrong_hash != genuine.canonical_approval_content_sha256
    changes: dict[str, Any]
    if kind == "both_hashes":
        changes = {
            "declared_operator_approved_source_policy_sha256": wrong_hash,
            "canonical_approval_content_sha256": wrong_hash,
        }
    elif kind == "canonical_hash":
        changes = {"canonical_approval_content_sha256": wrong_hash}
    elif kind == "declared_hash":
        changes = {
            "declared_operator_approved_source_policy_sha256": wrong_hash
        }
    elif kind == "schema_version":
        changes = {"approval_schema_version": "substituted_schema_v2"}
    elif kind == "policy_version":
        changes = {"approval_policy_version": "policy_v2"}
    else:
        changes = {"source_count": 2}
    altered = replace(genuine, **changes)
    monkeypatch.setattr(
        artifact_contract,
        "validate_step2_market_source_policy_approvals_object",
        lambda value: altered,
    )
    _assert_invariant(lambda: _validate(raw))


def test_cross_artifact_object_result_pairing_is_invariant_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_a_raw = _raw_approval()
    artifact_b = _approval(sources=[_source()])
    artifact_b_result = validate_step2_market_source_policy_approvals_object(
        artifact_b
    )
    assert artifact_b_result.approval_state is ObjectState.VALID_NONEMPTY
    monkeypatch.setattr(
        artifact_contract,
        "validate_step2_market_source_policy_approvals_object",
        lambda value: artifact_b_result,
    )
    _assert_invariant(lambda: _validate(artifact_a_raw))


def test_parser_dependency_exception_propagates_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval()
    error = ValueError("distinct parser failure")

    def fail(value: object) -> Step2MarketSourcePolicyApprovalParseResult:
        raise error

    monkeypatch.setattr(
        artifact_contract,
        "parse_step2_market_source_policy_approvals_bytes",
        fail,
    )
    with pytest.raises(ValueError) as exc_info:
        _validate(raw)
    assert exc_info.value is error


def test_identity_hash_dependency_exception_propagates_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval()
    error = ValueError("distinct identity failure")

    def fail(value: object = b"") -> Any:
        raise error

    monkeypatch.setattr(artifact_contract, "sha256", fail)
    with pytest.raises(ValueError) as exc_info:
        _validate(raw)
    assert exc_info.value is error


def test_binding_hash_dependency_exception_propagates_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval()
    error = ValueError("distinct binding hash failure")
    calls = 0

    def fail_third(value: bytes = b"") -> Any:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise error
        return hashlib.sha256(value)

    monkeypatch.setattr(artifact_contract, "sha256", fail_third)
    with pytest.raises(ValueError) as exc_info:
        _validate(raw)
    assert exc_info.value is error
    assert calls == 3


def test_invalid_factory_computed_binding_digest_is_invariant_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval()
    calls = 0

    class InvalidDigest:
        def hexdigest(self) -> str:
            return "not-a-sha256"

    def invalid_third(value: bytes = b"") -> Any:
        nonlocal calls
        calls += 1
        if calls == 3:
            return InvalidDigest()
        return hashlib.sha256(value)

    monkeypatch.setattr(artifact_contract, "sha256", invalid_third)
    _assert_invariant(lambda: _validate(raw))
    assert calls == 3


def test_local_approval_content_hash_exception_propagates_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval()
    error = ValueError("distinct local approval-content hash failure")
    calls = 0

    def fail_second(value: bytes = b"") -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error
        return hashlib.sha256(value)

    monkeypatch.setattr(artifact_contract, "sha256", fail_second)
    with pytest.raises(ValueError) as exc_info:
        _validate(raw)
    assert exc_info.value is error
    assert calls == 2


def test_binding_json_dependency_exception_propagates_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval()
    error = ValueError("distinct binding serialization failure")

    def fail(*args: Any, **kwargs: Any) -> str:
        raise error

    monkeypatch.setattr(artifact_contract.json, "dumps", fail)
    with pytest.raises(ValueError) as exc_info:
        _validate(raw)
    assert exc_info.value is error


def test_object_validator_dependency_exception_propagates_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_approval()
    error = ValueError("distinct object validation failure")

    def fail(value: object) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
        raise error

    monkeypatch.setattr(
        artifact_contract,
        "validate_step2_market_source_policy_approvals_object",
        fail,
    )
    with pytest.raises(ValueError) as exc_info:
        _validate(raw)
    assert exc_info.value is error


def test_production_module_has_no_try_trystar_or_assert_statement() -> None:
    source = Path(artifact_contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(
        isinstance(node, (ast.Try, ast.TryStar, ast.Assert))
        for node in ast.walk(tree)
    )


def test_tests_do_not_patch_private_result_or_binding_helpers() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    patched_names = {
        node.args[1].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and type(node.args[1].value) is str
    }
    assert not patched_names & {
        "_create_result",
        "_convert_frozen_root_to_exact_builtins",
        "_canonical_complete_root_identity",
        "_canonical_approval_content_identity",
        "_artifact_validation_identity",
        "_binding_record",
        "Step2MarketSourcePolicyApprovalArtifactValidationResult",
    }


def test_public_dependency_calls_are_exactly_once_or_at_most_once_in_ast() -> None:
    source = Path(artifact_contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    public_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "validate_step2_market_source_policy_approval_artifact_bytes"
    )
    called = [
        node.func.id
        for node in ast.walk(public_function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert called.count(
        "parse_step2_market_source_policy_approvals_bytes"
    ) == 1
    assert called.count(
        "validate_step2_market_source_policy_approvals_object"
    ) == 1


def test_composition_module_imports_only_public_dependency_symbols() -> None:
    source = Path(artifact_contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    dependency_imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").endswith(
            (
                "parse_step2_market_source_policy_approvals",
                "validate_step2_market_source_policy_approvals",
            )
        )
        for alias in node.names
    ]
    assert dependency_imports
    assert all(not name.startswith("_") for name in dependency_imports)


_MODULE = artifact_contract.__name__
_BASENAME = _MODULE.rsplit(".", 1)[-1]
_RELATIVE_PATH = (
    Path("src/investment_orchestrator/validators") / f"{_BASENAME}.py"
)
_ALLOWED_EXPECTED_IDENTITY_BINDER = Path(
    "src/investment_orchestrator/validators/"
    "bind_step2_market_source_policy_approval_expected_identity.py"
)
_PUBLIC_SYMBOLS = frozenset(
    {
        "validate_step2_market_source_policy_approval_artifact_bytes",
        "Step2MarketSourcePolicyApprovalArtifactValidationResult",
        "Step2MarketSourcePolicyApprovalArtifactState",
        "Step2MarketSourcePolicyApprovalArtifactDiagnostic",
    }
)


def _reference_findings(relative_path: str, source: str) -> list[str]:
    findings: list[str] = []
    tree = ast.parse(source, filename=relative_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _MODULE or alias.name.startswith(f"{_MODULE}."):
                    findings.append(f"{relative_path}: import")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == _MODULE or module.endswith(f".{_BASENAME}"):
                findings.append(f"{relative_path}: from-import")
            if any(alias.name in _PUBLIC_SYMBOLS for alias in node.names):
                findings.append(f"{relative_path}: symbol-import")
        elif isinstance(node, ast.Name) and node.id in _PUBLIC_SYMBOLS:
            findings.append(f"{relative_path}: symbol")
        elif isinstance(node, ast.Attribute) and node.attr in _PUBLIC_SYMBOLS:
            findings.append(f"{relative_path}: attribute")
        elif isinstance(node, ast.Call):
            dynamic = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
            ) or (
                isinstance(node.func, ast.Name)
                and node.func.id == "__import__"
            )
            if dynamic and any(
                isinstance(argument, ast.Constant)
                and argument.value == _MODULE
                for argument in node.args
            ):
                findings.append(f"{relative_path}: dynamic-import")
    for marker in (_MODULE, *_PUBLIC_SYMBOLS, RESULT_VERSION, COMPOSITION_VERSION):
        if marker in source:
            findings.append(f"{relative_path}: text")
    return sorted(set(findings))


def test_reference_detector_covers_import_symbol_alias_dynamic_and_literal_forms() -> None:
    cases = (
        f"import {_MODULE}\n",
        f"import {_MODULE} as contract\n",
        f"from {_MODULE} import Step2MarketSourcePolicyApprovalArtifactState\n",
        "handler = Step2MarketSourcePolicyApprovalArtifactValidationResult\n",
        f"import importlib\nimportlib.import_module({_MODULE!r})\n",
        f"contract = __import__({_MODULE!r})\n",
        f"VERSION = {COMPOSITION_VERSION!r}\n",
    )
    assert all(
        _reference_findings(f"synthetic/{index}.py", source)
        for index, source in enumerate(cases)
    )


def test_composition_module_has_zero_production_consumers() -> None:
    root = Path(__file__).resolve().parents[2]
    production_root = root / "src" / "investment_orchestrator"
    contract_path = root / _RELATIVE_PATH
    allowed_consumer_path = root / _ALLOWED_EXPECTED_IDENTITY_BINDER
    findings: list[str] = []
    for path in sorted(production_root.rglob("*.py")):
        if path in {contract_path, allowed_consumer_path}:
            continue
        relative = path.relative_to(root).as_posix()
        findings.extend(
            _reference_findings(relative, path.read_text(encoding="utf-8"))
        )
    assert sorted(set(findings)) == [], "\n".join(sorted(set(findings)))


def test_composition_module_has_no_external_or_authority_capability() -> None:
    source = Path(artifact_contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert imported_roots <= {
        "__future__",
        "dataclasses",
        "enum",
        "hashlib",
        "investment_orchestrator",
        "json",
        "re",
        "typing",
    }
    forbidden_calls = {
        "open",
        "getenv",
        "system",
        "popen",
        "run",
        "request",
        "urlopen",
        "publish",
        "materialize",
        "compile_orders",
        "submit",
        "transmit",
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not (called_names | called_attributes) & forbidden_calls
    forbidden_text = (
        "portfolio",
        "workflow",
        "step3",
        "step4",
        "final_safety",
        "broker",
        "live_execution",
    )
    imported_modules = "\n".join(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert all(marker not in imported_modules.lower() for marker in forbidden_text)


def test_authority_and_state_action_surface_is_absent() -> None:
    result = _validate(_raw_approval(sources=[_source()]))
    assert result.artifact_contract_valid is True
    assert result.not_trade_authorization is True
    assert result.trade_permission_effect == "none"
    forbidden = {
        "approved",
        "authorized",
        "active",
        "ready_for_trading",
        "permission_granted",
        "hold_effect",
        "sell_effect",
        "new_buy_effect",
        "order_compilation_performed",
        "step3_reachable",
        "step4_reachable",
        "broker_execution_performed",
    }
    assert not forbidden & {item.name for item in fields(type(result))}
