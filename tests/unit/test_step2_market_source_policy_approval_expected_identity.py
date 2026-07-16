from __future__ import annotations

import ast
import copy
import dataclasses
from dataclasses import fields, is_dataclass, replace
import hashlib
import hmac
import inspect
import json
from pathlib import Path
import pickle
from typing import Any

import pytest

import investment_orchestrator.validators.bind_step2_market_source_policy_approval_expected_identity as binding_contract
import investment_orchestrator.validators.validate_step2_market_source_policy_approval_artifact as artifact_contract
from investment_orchestrator.parsers.parse_step2_market_source_policy_approvals import (
    parse_step2_market_source_policy_approvals_bytes,
)
from investment_orchestrator.validators.bind_step2_market_source_policy_approval_expected_identity import (
    ACTIVATION_EVALUATION_PERFORMED,
    ACTIVE_POLICY_MATERIALIZATION_PERFORMED,
    AUTHORITY_SCOPE,
    BINDING_RESULT_BOOLEAN_COERCION_ERROR,
    BINDING_VERSION,
    FRESHNESS_EVALUATION_PERFORMED,
    MAX_EXPECTED_IDENTITY_BINDING_BYTES,
    MAX_EXPECTED_IDENTITY_BINDING_DIAGNOSTICS,
    NOT_TRADE_AUTHORIZATION,
    OPERATOR_APPROVAL_INFERRED,
    OPERATOR_AUTHENTICATION_PERFORMED,
    ORDER_COMPILATION_EVALUATED,
    PUBLICATION_EVALUATION_PERFORMED,
    RESULT_VERSION,
    SOURCE_RESOLUTION_PERFORMED,
    Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic,
    Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult,
    Step2MarketSourcePolicyApprovalExpectedIdentityBindingState,
    TRADE_PERMISSION_EFFECT,
    WORKFLOW_PERMISSION_EVALUATED,
    bind_step2_market_source_policy_approval_expected_identity,
)
from investment_orchestrator.validators.validate_step2_market_source_policy_approval_artifact import (
    COMPOSITION_VERSION,
    MAX_ARTIFACT_VALIDATION_BINDING_BYTES,
    Step2MarketSourcePolicyApprovalArtifactState,
    Step2MarketSourcePolicyApprovalArtifactValidationResult,
    validate_step2_market_source_policy_approval_artifact_bytes,
)


_State = Step2MarketSourcePolicyApprovalExpectedIdentityBindingState
_Diagnostic = Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic
_ArtifactState = Step2MarketSourcePolicyApprovalArtifactState
_APPROVAL_DOMAIN = b"step2_market_source_policy_approvals_v1\0"
_ARTIFACT_DOMAIN = (
    b"step2_market_source_policy_approval_artifact_validation_v1\0"
)
_BINDING_DOMAIN = (
    b"step2_market_source_policy_approval_expected_identity_binding_v1\0"
)
_INVARIANT_MESSAGE = (
    "Step 2 source-policy approval expected-identity binding invariant "
    "violated"
)
_CONSTRUCTION_ERROR = (
    "Step 2 source-policy approval expected-identity binding results are "
    "created only by the public binder"
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
        "approval_reason": "Approved for expected-identity binder testing.",
    }


def _approval_content(
    *,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "policy_version": "policy_v1",
        "supersedes_policy_version": None,
        "policy_change_reason": "Initial expected-identity fixture.",
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


def _raw_approval(
    *,
    sources: list[dict[str, Any]] | None = None,
    reason: str = "Initial expected-identity fixture.",
) -> bytes:
    content = _approval_content(sources=sources)
    content["policy_change_reason"] = reason
    declared = hashlib.sha256(
        _APPROVAL_DOMAIN + _canonical_json(content)
    ).hexdigest()
    return json.dumps(
        {
            "schema_version": "step2_market_source_policy_approvals_v1",
            "operator_approved_source_policy_sha256": declared,
            "approval_content": content,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _artifact(
    raw: object,
) -> Step2MarketSourcePolicyApprovalArtifactValidationResult:
    return validate_step2_market_source_policy_approval_artifact_bytes(raw)


def _valid_empty_artifact() -> Step2MarketSourcePolicyApprovalArtifactValidationResult:
    return _artifact(_raw_approval())


def _valid_nonempty_artifact() -> Step2MarketSourcePolicyApprovalArtifactValidationResult:
    return _artifact(_raw_approval(sources=[_source()]))


def _bind(
    artifact: object,
    expected: object,
) -> Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult:
    return bind_step2_market_source_policy_approval_expected_identity(
        artifact_validation_result=artifact,
        expected_artifact_validation_identity_sha256=expected,
    )


def _forge(instance: Any, **changes: Any) -> Any:
    forged = object.__new__(type(instance))
    for item in fields(type(instance)):
        object.__setattr__(
            forged,
            item.name,
            changes.get(item.name, getattr(instance, item.name)),
        )
    return forged


def _parsed_identity_binding_invalid_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> Step2MarketSourcePolicyApprovalArtifactValidationResult:
    raw = _raw_approval()
    parser_result = parse_step2_market_source_policy_approvals_bytes(raw)
    altered = "0" * 64
    if parser_result.parsed_value_identity_sha256 == altered:
        altered = "1" * 64
    forged_parser_result = _forge(
        parser_result,
        parsed_value_identity_sha256=altered,
    )
    monkeypatch.setattr(
        artifact_contract,
        "parse_step2_market_source_policy_approvals_bytes",
        lambda value: forged_parser_result,
    )
    result = artifact_contract.validate_step2_market_source_policy_approval_artifact_bytes(
        raw
    )
    assert result.composition_state is _ArtifactState.PARSED_IDENTITY_BINDING_INVALID
    return result


def _artifact_binding_record(
    artifact: Step2MarketSourcePolicyApprovalArtifactValidationResult,
) -> dict[str, Any]:
    parser_result = artifact.parser_result
    object_result = artifact.approval_object_validation_result
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
            "object_validation_performed": (
                object_result.object_validation_performed
            ),
            "result_version": object_result.result_version,
            "semantic_validation_performed": (
                object_result.semantic_validation_performed
            ),
            "source_count": object_result.source_count,
        },
        "artifact_contract_valid": artifact.artifact_contract_valid,
        "composition_diagnostics": [
            diagnostic.value for diagnostic in artifact.diagnostics
        ],
        "composition_state": artifact.composition_state.value,
        "composition_validation_performed": True,
        "composition_version": artifact.composition_version,
        "conversion": {
            "exact_builtin_conversion_performed": True,
            "exact_builtin_conversion_valid": True,
        },
        "object_contract_validation_performed": True,
        "parsed_identity_binding": {
            "parsed_identity_recheck_matches": True,
            "parsed_identity_recheck_performed": True,
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
        "result_version": artifact.result_version,
        "root_object": {
            "root_object_check_performed": True,
            "root_object_valid": True,
        },
    }


def _binding_record(
    result: Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult,
) -> dict[str, Any]:
    artifact = result.artifact_validation_result
    assert artifact is not None
    assert result.expected_artifact_validation_identity_sha256 is not None
    assert result.actual_artifact_validation_identity_sha256 is not None
    return {
        "artifact_identity_recheck": {
            "artifact_identity_recheck_matches": True,
            "artifact_identity_recheck_performed": True,
        },
        "artifact_result_binding_eligible": True,
        "artifact_result_check_performed": True,
        "artifact_result_invariant_validation_performed": True,
        "artifact_validation_result": {
            "artifact_contract_valid": True,
            "artifact_validation_identity_sha256": (
                result.actual_artifact_validation_identity_sha256
            ),
            "composition_state": artifact.composition_state.value,
            "composition_version": artifact.composition_version,
            "result_version": artifact.result_version,
        },
        "binding_diagnostics": [
            diagnostic.value for diagnostic in result.diagnostics
        ],
        "binding_state": result.binding_state.value,
        "binding_valid": result.binding_valid,
        "binding_version": BINDING_VERSION,
        "expected_artifact_validation_identity_sha256": (
            result.expected_artifact_validation_identity_sha256
        ),
        "identity_comparison": {
            "identity_comparison_performed": True,
            "identity_matches": result.identity_matches,
        },
        "result_version": RESULT_VERSION,
    }


def _digest(domain: bytes, record: dict[str, Any]) -> str:
    return hashlib.sha256(domain + _canonical_json(record)).hexdigest()


def _assert_authority_markers(
    result: Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult,
) -> None:
    assert result.result_version == RESULT_VERSION
    assert result.binding_version == BINDING_VERSION
    assert result.authority_scope == AUTHORITY_SCOPE
    assert result.not_trade_authorization is NOT_TRADE_AUTHORIZATION
    assert result.trade_permission_effect == TRADE_PERMISSION_EFFECT
    assert result.operator_authentication_performed is OPERATOR_AUTHENTICATION_PERFORMED
    assert result.operator_approval_inferred is OPERATOR_APPROVAL_INFERRED
    assert result.activation_evaluation_performed is ACTIVATION_EVALUATION_PERFORMED
    assert (
        result.active_policy_materialization_performed
        is ACTIVE_POLICY_MATERIALIZATION_PERFORMED
    )
    assert result.source_resolution_performed is SOURCE_RESOLUTION_PERFORMED
    assert result.freshness_evaluation_performed is FRESHNESS_EVALUATION_PERFORMED
    assert result.publication_evaluation_performed is PUBLICATION_EVALUATION_PERFORMED
    assert result.workflow_permission_evaluated is WORKFLOW_PERMISSION_EVALUATED
    assert result.order_compilation_evaluated is ORDER_COMPILATION_EVALUATED


@pytest.mark.parametrize(
    ("expected", "state", "diagnostic"),
    [
        (
            None,
            _State.EXPECTED_IDENTITY_INPUT_ABSENT,
            _Diagnostic.EXPECTED_IDENTITY_INPUT_MISSING,
        ),
        (
            b"0" * 64,
            _State.EXPECTED_IDENTITY_INPUT_TYPE_INVALID,
            _Diagnostic.EXPECTED_IDENTITY_INPUT_TYPE_INVALID,
        ),
        (
            "bad",
            _State.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID,
            _Diagnostic.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID,
        ),
    ],
)
def test_expected_input_precedes_artifact_inspection(
    expected: object,
    state: Step2MarketSourcePolicyApprovalExpectedIdentityBindingState,
    diagnostic: Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic,
) -> None:
    class UninspectableArtifact:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(name)

    result = _bind(UninspectableArtifact(), expected)
    assert result.binding_state is state
    assert result.artifact_validation_result is None
    assert result.expected_artifact_validation_identity_sha256 is None
    assert result.actual_artifact_validation_identity_sha256 is None
    assert result.expected_identity_binding_sha256 is None
    assert result.artifact_result_check_performed is False
    assert result.artifact_result_invariant_validation_performed is False
    assert result.artifact_result_binding_eligible is None
    assert result.artifact_identity_recheck_performed is False
    assert result.artifact_identity_recheck_matches is None
    assert result.identity_comparison_performed is False
    assert result.identity_matches is None
    assert result.binding_valid is None
    assert result.diagnostics == (diagnostic,)
    _assert_authority_markers(result)


@pytest.mark.parametrize(
    "expected",
    [
        "A" * 64,
        "0" * 63,
        "0" * 65,
        " 0" + "0" * 62,
        "0" * 63 + " ",
        "0x" + "0" * 62,
        "00-" + "0" * 61,
        "０" * 64,
        "g" * 64,
    ],
)
def test_expected_identity_syntax_is_exact(expected: str) -> None:
    result = _bind(object(), expected)
    assert result.binding_state is _State.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID
    assert result.expected_artifact_validation_identity_sha256 is None


def test_exact_string_subclass_is_type_invalid() -> None:
    class DigestSubclass(str):
        pass

    result = _bind(object(), DigestSubclass("0" * 64))
    assert result.binding_state is _State.EXPECTED_IDENTITY_INPUT_TYPE_INVALID


@pytest.mark.parametrize(
    "expected",
    [bytearray(b"0" * 64), memoryview(b"0" * 64), [], 0, True],
)
def test_non_string_expected_identity_types_are_rejected(
    expected: object,
) -> None:
    result = _bind(object(), expected)
    assert result.binding_state is _State.EXPECTED_IDENTITY_INPUT_TYPE_INVALID
    assert result.expected_artifact_validation_identity_sha256 is None


@pytest.mark.parametrize("artifact", [None, b"{}", {}, [], "artifact", 1, True])
def test_artifact_type_boundary_is_exact(artifact: object) -> None:
    expected = "0" * 64
    result = _bind(artifact, expected)
    assert result.binding_state is _State.ARTIFACT_VALIDATION_RESULT_TYPE_INVALID
    assert result.artifact_validation_result is None
    assert result.expected_artifact_validation_identity_sha256 == expected
    assert result.actual_artifact_validation_identity_sha256 is None
    assert result.expected_identity_binding_sha256 is None
    assert result.artifact_result_check_performed is True
    assert result.artifact_result_invariant_validation_performed is False
    assert result.artifact_result_binding_eligible is False
    assert result.artifact_identity_recheck_performed is False
    assert result.artifact_identity_recheck_matches is None
    assert result.identity_comparison_performed is False
    assert result.identity_matches is None
    assert result.binding_valid is None
    assert result.diagnostics == (
        _Diagnostic.APPROVAL_ARTIFACT_VALIDATION_RESULT_TYPE_INVALID,
    )


def test_artifact_result_subclass_is_rejected_without_field_access() -> None:
    class ArtifactSubclass(
        Step2MarketSourcePolicyApprovalArtifactValidationResult
    ):
        pass

    artifact = object.__new__(ArtifactSubclass)
    result = _bind(artifact, "0" * 64)
    assert result.binding_state is _State.ARTIFACT_VALIDATION_RESULT_TYPE_INVALID


@pytest.mark.parametrize(
    "artifact_factory",
    [
        lambda: _artifact(None),
        lambda: _artifact({}),
        lambda: _artifact(b""),
        lambda: _artifact(b"[]"),
    ],
)
def test_category_a_states_skip_identity_hash_and_comparison(
    artifact_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = artifact_factory()

    def forbidden_sha256(value: object) -> object:
        raise AssertionError(value)

    def forbidden_compare(left: object, right: object) -> bool:
        raise AssertionError((left, right))

    monkeypatch.setattr(binding_contract, "sha256", forbidden_sha256)
    monkeypatch.setattr(binding_contract.hmac, "compare_digest", forbidden_compare)
    result = _bind(artifact, "0" * 64)
    assert result.binding_state is _State.ARTIFACT_CONTRACT_NOT_VALID
    assert result.artifact_validation_result is artifact
    assert result.artifact_result_check_performed is True
    assert result.artifact_result_invariant_validation_performed is True
    assert result.artifact_result_binding_eligible is False
    assert result.artifact_identity_recheck_performed is False
    assert result.artifact_identity_recheck_matches is None
    assert result.identity_comparison_performed is False
    assert result.identity_matches is None
    assert result.binding_valid is False
    assert result.expected_identity_binding_sha256 is None


def test_parsed_identity_binding_invalid_is_category_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _parsed_identity_binding_invalid_artifact(monkeypatch)
    monkeypatch.setattr(
        binding_contract,
        "sha256",
        lambda value: (_ for _ in ()).throw(AssertionError(value)),
    )
    result = _bind(artifact, "0" * 64)
    assert result.binding_state is _State.ARTIFACT_CONTRACT_NOT_VALID
    assert result.artifact_identity_recheck_performed is False
    assert result.artifact_identity_recheck_matches is None


def test_object_contract_invalid_rechecks_once_but_remains_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(b"{}")
    original_sha256 = binding_contract.sha256
    original_compare = binding_contract.hmac.compare_digest
    hash_inputs: list[bytes] = []
    comparisons: list[tuple[str, str]] = []

    def recording_sha256(value: bytes) -> Any:
        hash_inputs.append(value)
        return original_sha256(value)

    def recording_compare(left: str, right: str) -> bool:
        comparisons.append((left, right))
        return original_compare(left, right)

    monkeypatch.setattr(binding_contract, "sha256", recording_sha256)
    monkeypatch.setattr(binding_contract.hmac, "compare_digest", recording_compare)
    result = _bind(artifact, "0" * 64)
    assert result.binding_state is _State.ARTIFACT_CONTRACT_NOT_VALID
    assert result.artifact_result_check_performed is True
    assert result.artifact_result_invariant_validation_performed is True
    assert result.artifact_result_binding_eligible is False
    assert result.artifact_identity_recheck_performed is True
    assert result.artifact_identity_recheck_matches is True
    assert result.actual_artifact_validation_identity_sha256 is None
    assert result.identity_comparison_performed is False
    assert result.identity_matches is None
    assert result.binding_valid is False
    assert result.expected_identity_binding_sha256 is None
    assert len(hash_inputs) == 1
    assert hash_inputs[0].startswith(_ARTIFACT_DOMAIN)
    assert comparisons == [
        (
            hashlib.sha256(hash_inputs[0]).hexdigest(),
            artifact.artifact_validation_identity_sha256,
        )
    ]


@pytest.mark.parametrize("artifact_factory", [_valid_empty_artifact, _valid_nonempty_artifact])
def test_valid_artifact_exact_match_is_completed_and_non_authorizing(
    artifact_factory: Any,
) -> None:
    artifact = artifact_factory()
    expected = artifact.artifact_validation_identity_sha256
    result = _bind(artifact, expected)
    assert result.binding_state is _State.EXPECTED_IDENTITY_MATCH
    assert result.artifact_validation_result is artifact
    assert result.expected_artifact_validation_identity_sha256 == expected
    assert result.actual_artifact_validation_identity_sha256 == expected
    assert result.artifact_result_check_performed is True
    assert result.artifact_result_invariant_validation_performed is True
    assert result.artifact_result_binding_eligible is True
    assert result.artifact_identity_recheck_performed is True
    assert result.artifact_identity_recheck_matches is True
    assert result.identity_comparison_performed is True
    assert result.identity_matches is True
    assert result.binding_valid is True
    assert result.diagnostics == ()
    assert result.expected_identity_binding_sha256 is not None
    _assert_authority_markers(result)


@pytest.mark.parametrize(
    "expected_factory",
    [
        lambda actual: ("0" if actual[0] != "0" else "1") + actual[1:],
        lambda actual: "0" * 64 if actual != "0" * 64 else "1" * 64,
    ],
)
def test_expected_identity_mismatch_is_a_completed_audit_result(
    expected_factory: Any,
) -> None:
    artifact = _valid_nonempty_artifact()
    expected = expected_factory(artifact.artifact_validation_identity_sha256)
    result = _bind(artifact, expected)
    assert result.binding_state is _State.EXPECTED_IDENTITY_MISMATCH
    assert result.actual_artifact_validation_identity_sha256 == (
        artifact.artifact_validation_identity_sha256
    )
    assert result.identity_comparison_performed is True
    assert result.identity_matches is False
    assert result.binding_valid is False
    assert result.diagnostics == (_Diagnostic.EXPECTED_IDENTITY_MISMATCH,)
    assert result.expected_identity_binding_sha256 is not None


def test_expected_identity_from_another_artifact_mismatches() -> None:
    artifact_a = _artifact(_raw_approval(reason="Artifact A."))
    artifact_b = _artifact(_raw_approval(reason="Artifact B."))
    assert artifact_a.artifact_validation_identity_sha256 != (
        artifact_b.artifact_validation_identity_sha256
    )
    result = _bind(
        artifact_a,
        artifact_b.artifact_validation_identity_sha256,
    )
    assert result.binding_state is _State.EXPECTED_IDENTITY_MISMATCH


def test_all_seven_public_branch_matrices_are_exact() -> None:
    valid = _valid_empty_artifact()
    actual = valid.artifact_validation_identity_sha256
    mismatch = ("0" if actual[0] != "0" else "1") + actual[1:]
    results = {
        _State.EXPECTED_IDENTITY_INPUT_ABSENT: _bind(object(), None),
        _State.EXPECTED_IDENTITY_INPUT_TYPE_INVALID: _bind(object(), b"0" * 64),
        _State.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID: _bind(object(), "bad"),
        _State.ARTIFACT_VALIDATION_RESULT_TYPE_INVALID: _bind(object(), "0" * 64),
        _State.ARTIFACT_CONTRACT_NOT_VALID: _bind(_artifact(None), "0" * 64),
        _State.EXPECTED_IDENTITY_MISMATCH: _bind(valid, mismatch),
        _State.EXPECTED_IDENTITY_MATCH: _bind(valid, actual),
    }
    expected = {
        _State.EXPECTED_IDENTITY_INPUT_ABSENT: (
            False, False, None, False, None, False, None, None
        ),
        _State.EXPECTED_IDENTITY_INPUT_TYPE_INVALID: (
            False, False, None, False, None, False, None, None
        ),
        _State.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID: (
            False, False, None, False, None, False, None, None
        ),
        _State.ARTIFACT_VALIDATION_RESULT_TYPE_INVALID: (
            True, False, False, False, None, False, None, None
        ),
        _State.ARTIFACT_CONTRACT_NOT_VALID: (
            True, True, False, False, None, False, None, False
        ),
        _State.EXPECTED_IDENTITY_MISMATCH: (
            True, True, True, True, True, True, False, False
        ),
        _State.EXPECTED_IDENTITY_MATCH: (
            True, True, True, True, True, True, True, True
        ),
    }
    assert set(results) == set(_State)
    for state, result in results.items():
        assert result.binding_state is state
        assert (
            result.artifact_result_check_performed,
            result.artifact_result_invariant_validation_performed,
            result.artifact_result_binding_eligible,
            result.artifact_identity_recheck_performed,
            result.artifact_identity_recheck_matches,
            result.identity_comparison_performed,
            result.identity_matches,
            result.binding_valid,
        ) == expected[state]


@pytest.mark.parametrize(
    "artifact_factory",
    [lambda: _artifact(b"{}"), _valid_empty_artifact, _valid_nonempty_artifact],
)
def test_stored_artifact_identity_substitution_is_invariant_failure(
    artifact_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = artifact_factory()
    wrong = "0" * 64
    if artifact.artifact_validation_identity_sha256 == wrong:
        wrong = "1" * 64
    forged = _forge(artifact, artifact_validation_identity_sha256=wrong)
    comparisons: list[tuple[str, str]] = []
    hash_inputs: list[bytes] = []
    original_compare = binding_contract.hmac.compare_digest
    original_sha256 = binding_contract.sha256

    def recording_sha256(value: bytes) -> Any:
        hash_inputs.append(value)
        return original_sha256(value)

    def recording_compare(left: str, right: str) -> bool:
        comparisons.append((left, right))
        return original_compare(left, right)

    monkeypatch.setattr(binding_contract, "sha256", recording_sha256)
    monkeypatch.setattr(binding_contract.hmac, "compare_digest", recording_compare)
    with pytest.raises(binding_contract._ExpectedIdentityBindingInvariantError) as exc_info:
        _bind(forged, wrong)
    assert str(exc_info.value) == _INVARIANT_MESSAGE
    assert len(hash_inputs) == 1
    assert hash_inputs[0].startswith(_ARTIFACT_DOMAIN)
    assert len(comparisons) == 1
    assert comparisons[0][1] == wrong


def test_identity_recheck_precedes_expected_comparison_and_audit_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _valid_empty_artifact()
    expected = artifact.artifact_validation_identity_sha256
    events: list[tuple[str, object, object | None]] = []
    original_sha256 = binding_contract.sha256
    original_compare = binding_contract.hmac.compare_digest

    def recording_sha256(value: bytes) -> Any:
        kind = "artifact" if value.startswith(_ARTIFACT_DOMAIN) else "audit"
        events.append(("sha256", kind, None))
        return original_sha256(value)

    def recording_compare(left: str, right: str) -> bool:
        events.append(("compare", left, right))
        return original_compare(left, right)

    monkeypatch.setattr(binding_contract, "sha256", recording_sha256)
    monkeypatch.setattr(binding_contract.hmac, "compare_digest", recording_compare)
    result = _bind(artifact, expected)
    assert result.binding_state is _State.EXPECTED_IDENTITY_MATCH
    assert events == [
        ("sha256", "artifact", None),
        (
            "compare",
            artifact.artifact_validation_identity_sha256,
            artifact.artifact_validation_identity_sha256,
        ),
        ("compare", expected, artifact.artifact_validation_identity_sha256),
        ("sha256", "audit", None),
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"result_version": "wrong"},
        {"composition_version": "wrong"},
        {"authority_scope": "wrong"},
        {"artifact_contract_valid": None},
        {"artifact_validation_identity_sha256": None},
    ],
)
def test_impossible_exact_artifact_results_raise_fixed_invariant(
    changes: dict[str, Any],
) -> None:
    artifact = _valid_empty_artifact()
    forged = _forge(artifact, **changes)
    with pytest.raises(binding_contract._ExpectedIdentityBindingInvariantError) as exc_info:
        _bind(forged, "0" * 64)
    assert str(exc_info.value) == _INVARIANT_MESSAGE


def test_impossible_nested_parser_and_object_results_raise() -> None:
    artifact = _valid_empty_artifact()
    forged_parser = _forge(artifact.parser_result, result_version="wrong")
    forged_parser_artifact = _forge(artifact, parser_result=forged_parser)
    with pytest.raises(binding_contract._ExpectedIdentityBindingInvariantError):
        _bind(forged_parser_artifact, "0" * 64)

    object_result = artifact.approval_object_validation_result
    assert object_result is not None
    forged_object = _forge(object_result, source_count=1)
    forged_object_artifact = _forge(
        artifact,
        approval_object_validation_result=forged_object,
    )
    with pytest.raises(binding_contract._ExpectedIdentityBindingInvariantError):
        _bind(forged_object_artifact, "0" * 64)


@pytest.mark.parametrize(
    "artifact_factory",
    [lambda: _artifact(b"{}"), _valid_empty_artifact, _valid_nonempty_artifact],
)
def test_b1p2_identity_matches_independent_twelve_key_oracle(
    artifact_factory: Any,
) -> None:
    artifact = artifact_factory()
    record = _artifact_binding_record(artifact)
    assert len(record) == 12
    encoded = _canonical_json(record)
    assert len(encoded) <= MAX_ARTIFACT_VALIDATION_BINDING_BYTES
    assert artifact.artifact_validation_identity_sha256 == _digest(
        _ARTIFACT_DOMAIN,
        record,
    )


@pytest.mark.parametrize("matches", [False, True])
def test_binding_identity_matches_independent_twelve_key_oracle(
    matches: bool,
) -> None:
    artifact = _valid_nonempty_artifact()
    actual = artifact.artifact_validation_identity_sha256
    expected = actual if matches else ("0" if actual[0] != "0" else "1") + actual[1:]
    result = _bind(artifact, expected)
    record = _binding_record(result)
    assert len(record) == 12
    encoded = _canonical_json(record)
    assert result.expected_identity_binding_sha256 == _digest(
        _BINDING_DOMAIN,
        record,
    )
    assert result.expected_identity_binding_sha256 != hashlib.sha256(encoded).hexdigest()


def test_exact_completed_record_maximum_is_1117_bytes() -> None:
    artifact = _valid_nonempty_artifact()
    actual = artifact.artifact_validation_identity_sha256
    mismatch_expected = ("0" if actual[0] != "0" else "1") + actual[1:]
    match = _bind(artifact, actual)
    mismatch = _bind(artifact, mismatch_expected)
    assert len(_canonical_json(_binding_record(match))) == 1084
    assert len(_canonical_json(_binding_record(mismatch))) == 1117
    assert 1117 < MAX_EXPECTED_IDENTITY_BINDING_BYTES == 2048


def test_every_binding_record_leaf_is_identity_sensitive() -> None:
    artifact = _valid_nonempty_artifact()
    result = _bind(artifact, artifact.artifact_validation_identity_sha256)
    original = _binding_record(result)
    original_digest = _digest(_BINDING_DOMAIN, original)
    mutations: tuple[tuple[tuple[str, ...], Any], ...] = (
        (("artifact_identity_recheck", "artifact_identity_recheck_matches"), False),
        (("artifact_identity_recheck", "artifact_identity_recheck_performed"), False),
        (("artifact_result_binding_eligible",), False),
        (("artifact_result_check_performed",), False),
        (("artifact_result_invariant_validation_performed",), False),
        (("artifact_validation_result", "artifact_contract_valid"), False),
        (("artifact_validation_result", "artifact_validation_identity_sha256"), "0" * 64),
        (("artifact_validation_result", "composition_state"), "valid_empty"),
        (("artifact_validation_result", "composition_version"), "other"),
        (("artifact_validation_result", "result_version"), "other"),
        (("binding_diagnostics",), ["expected_identity_mismatch"]),
        (("binding_state",), "expected_identity_mismatch"),
        (("binding_valid",), False),
        (("binding_version",), "other"),
        (("expected_artifact_validation_identity_sha256",), "0" * 64),
        (("identity_comparison", "identity_comparison_performed"), False),
        (("identity_comparison", "identity_matches"), False),
        (("result_version",), "other"),
    )
    for path, replacement in mutations:
        mutated = copy.deepcopy(original)
        target: Any = mutated
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = replacement
        assert _digest(_BINDING_DOMAIN, mutated) != original_digest, path


def test_state_and_diagnostic_enums_are_exact_and_ordered() -> None:
    assert tuple(_State) == (
        _State.EXPECTED_IDENTITY_INPUT_ABSENT,
        _State.EXPECTED_IDENTITY_INPUT_TYPE_INVALID,
        _State.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID,
        _State.ARTIFACT_VALIDATION_RESULT_TYPE_INVALID,
        _State.ARTIFACT_CONTRACT_NOT_VALID,
        _State.EXPECTED_IDENTITY_MISMATCH,
        _State.EXPECTED_IDENTITY_MATCH,
    )
    assert tuple(_Diagnostic) == (
        _Diagnostic.EXPECTED_IDENTITY_INPUT_MISSING,
        _Diagnostic.EXPECTED_IDENTITY_INPUT_TYPE_INVALID,
        _Diagnostic.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID,
        _Diagnostic.APPROVAL_ARTIFACT_VALIDATION_RESULT_TYPE_INVALID,
        _Diagnostic.APPROVAL_ARTIFACT_CONTRACT_NOT_VALID,
        _Diagnostic.EXPECTED_IDENTITY_MISMATCH,
    )
    assert MAX_EXPECTED_IDENTITY_BINDING_DIAGNOSTICS == 1


def test_result_field_contract_is_exact() -> None:
    expected = (
        "result_version",
        "binding_version",
        "binding_state",
        "artifact_validation_result",
        "expected_artifact_validation_identity_sha256",
        "actual_artifact_validation_identity_sha256",
        "expected_identity_binding_sha256",
        "artifact_result_check_performed",
        "artifact_result_invariant_validation_performed",
        "artifact_result_binding_eligible",
        "artifact_identity_recheck_performed",
        "artifact_identity_recheck_matches",
        "identity_comparison_performed",
        "identity_matches",
        "binding_valid",
        "diagnostics",
        "authority_scope",
        "not_trade_authorization",
        "trade_permission_effect",
        "operator_authentication_performed",
        "operator_approval_inferred",
        "activation_evaluation_performed",
        "active_policy_materialization_performed",
        "source_resolution_performed",
        "freshness_evaluation_performed",
        "publication_evaluation_performed",
        "workflow_permission_evaluated",
        "order_compilation_evaluated",
    )
    assert tuple(item.name for item in fields(Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult)) == expected
    assert is_dataclass(Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult)
    assert Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult.__dataclass_params__.frozen is True
    assert tuple(Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult.__slots__) == expected


def test_construction_blockers_are_class_owned_and_module_defined() -> None:
    result_type = Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult
    for name in ("__new__", "__setstate__", "__reduce__", "__reduce_ex__"):
        method = result_type.__dict__[name]
        if isinstance(method, staticmethod):
            method = method.__func__
        assert method.__code__.co_filename == binding_contract.__file__
    assert result_type.__reduce__ is not object.__reduce__
    assert result_type.__reduce_ex__ is not object.__reduce_ex__
    assert result_type.__setstate__ is not dataclasses._dataclass_setstate


def test_result_boolean_coercion_is_blocked_for_every_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [
        _bind(object(), None),
        _bind(object(), b"0" * 64),
        _bind(object(), "bad"),
        _bind(object(), "0" * 64),
        _bind(_artifact(None), "0" * 64),
    ]
    artifact = _valid_empty_artifact()
    results.extend(
        [
            _bind(artifact, "0" * 64),
            _bind(artifact, artifact.artifact_validation_identity_sha256),
        ]
    )
    for result in results:
        with pytest.raises(TypeError) as exc_info:
            bool(result)
        assert str(exc_info.value) == BINDING_RESULT_BOOLEAN_COERCION_ERROR


def test_direct_construction_and_dataclasses_replace_are_blocked() -> None:
    artifact = _valid_empty_artifact()
    result = _bind(artifact, artifact.artifact_validation_identity_sha256)
    with pytest.raises(TypeError, match="created only by the public binder"):
        Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult()
    all_fields = {item.name: getattr(result, item.name) for item in fields(type(result))}
    with pytest.raises(TypeError, match="created only by the public binder"):
        Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult(**all_fields)
    with pytest.raises(TypeError, match="created only by the public binder"):
        replace(result, expected_identity_binding_sha256="0" * 64)
    with pytest.raises(TypeError, match="created only by the public binder"):
        replace(result, artifact_validation_result=_valid_nonempty_artifact())


def test_reduction_state_copy_and_pickle_reconstruction_are_blocked() -> None:
    artifact = _valid_empty_artifact()
    result = _bind(artifact, artifact.artifact_validation_identity_sha256)
    before = tuple(getattr(result, item.name) for item in fields(type(result)))
    operations = [result.__reduce__]
    operations.extend(
        lambda protocol=protocol: result.__reduce_ex__(protocol)
        for protocol in range(6)
    )
    operations.extend([lambda: copy.copy(result), lambda: copy.deepcopy(result)])
    operations.extend(
        lambda protocol=protocol: pickle.dumps(result, protocol=protocol)
        for protocol in range(6)
    )
    for operation in operations:
        with pytest.raises(TypeError) as exc_info:
            operation()
        assert str(exc_info.value) == _CONSTRUCTION_ERROR
    state = result.__getstate__()
    with pytest.raises(TypeError, match="created only by the public binder"):
        result.__setstate__(state)
    uninitialized = object.__new__(type(result))
    with pytest.raises(TypeError, match="created only by the public binder"):
        uninitialized.__setstate__(state)
    for item in fields(type(result)):
        with pytest.raises(AttributeError):
            getattr(uninitialized, item.name)
    assert tuple(getattr(result, item.name) for item in fields(type(result))) == before


def test_reduce_ex_does_not_inspect_protocol() -> None:
    artifact = _valid_empty_artifact()
    result = _bind(artifact, artifact.artifact_validation_identity_sha256)
    operations: list[str] = []

    class Protocol:
        def _fail(self, name: str) -> Any:
            operations.append(name)
            raise AssertionError(name)

        def __getattribute__(self, name: str) -> Any:
            if name == "_fail":
                return object.__getattribute__(self, name)
            return self._fail("attribute")

        def __int__(self) -> Any:
            return self._fail("int")

        def __index__(self) -> Any:
            return self._fail("index")

        def __bool__(self) -> Any:
            return self._fail("bool")

        def __eq__(self, other: object) -> Any:
            return self._fail("equality")

        def __lt__(self, other: object) -> Any:
            return self._fail("comparison")

        def __iter__(self) -> Any:
            return self._fail("iteration")

        def __getitem__(self, key: object) -> Any:
            return self._fail("item")

        def __len__(self) -> Any:
            return self._fail("length")

        def __hash__(self) -> Any:
            return self._fail("hash")

        def __repr__(self) -> str:
            return self._fail("repr")

    with pytest.raises(TypeError) as exc_info:
        result.__reduce_ex__(Protocol())
    assert str(exc_info.value) == _CONSTRUCTION_ERROR
    assert operations == []


def test_factory_is_sole_result_allocation_site_and_accepts_no_digest() -> None:
    source = Path(binding_contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    factory = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_create_result"
    )
    factory_nodes = set(ast.walk(factory))
    allocations = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "object"
        and node.func.attr == "__new__"
    ]
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "object"
        and node.func.attr == "__setattr__"
    ]
    assert len(allocations) == 1 and allocations[0] in factory_nodes
    assert assignments and all(node in factory_nodes for node in assignments)
    assert "expected_identity_binding_sha256" not in {
        argument.arg for argument in factory.args.args + factory.args.kwonlyargs
    }
    assert "copyreg" not in source
    assert "pickle" not in source
    assert "_reconstructor" not in source


def test_public_api_is_required_keyword_only_and_calls_no_validator() -> None:
    signature = inspect.signature(
        bind_step2_market_source_policy_approval_expected_identity
    )
    assert tuple(signature.parameters) == (
        "artifact_validation_result",
        "expected_artifact_validation_identity_sha256",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is inspect.Parameter.empty
        for parameter in signature.parameters.values()
    )
    source = Path(binding_contract.__file__).read_text(encoding="utf-8")
    assert "validate_step2_market_source_policy_approval_artifact_bytes" not in source
    assert "parse_step2_market_source_policy_approvals_bytes" not in source
    assert "validate_step2_market_source_policy_approvals_object" not in source


def test_dependency_exceptions_propagate_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _valid_empty_artifact()
    error = ValueError("distinct hash failure")

    def fail(value: object) -> object:
        raise error

    monkeypatch.setattr(binding_contract, "sha256", fail)
    with pytest.raises(ValueError) as exc_info:
        _bind(artifact, artifact.artifact_validation_identity_sha256)
    assert exc_info.value is error


def test_audit_hash_dependency_failure_propagates_after_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _valid_empty_artifact()
    original = binding_contract.sha256
    error = ValueError("distinct audit hash failure")
    calls = 0

    def fail_second(value: bytes) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error
        return original(value)

    monkeypatch.setattr(binding_contract, "sha256", fail_second)
    with pytest.raises(ValueError) as exc_info:
        _bind(artifact, artifact.artifact_validation_identity_sha256)
    assert exc_info.value is error
    assert calls == 2


def test_artifact_record_json_failure_propagates_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _valid_empty_artifact()
    error = ValueError("distinct artifact JSON failure")

    def fail(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(binding_contract.json, "dumps", fail)
    with pytest.raises(ValueError) as exc_info:
        _bind(artifact, artifact.artifact_validation_identity_sha256)
    assert exc_info.value is error


def test_audit_record_json_failure_propagates_after_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _valid_empty_artifact()
    original = binding_contract.json.dumps
    error = ValueError("distinct audit JSON failure")
    calls = 0

    def fail_second(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error
        return original(*args, **kwargs)

    monkeypatch.setattr(binding_contract.json, "dumps", fail_second)
    with pytest.raises(ValueError) as exc_info:
        _bind(artifact, artifact.artifact_validation_identity_sha256)
    assert exc_info.value is error
    assert calls == 2


@pytest.mark.parametrize("failure_call", [1, 2])
def test_compare_digest_failures_propagate_at_each_comparison(
    failure_call: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _valid_empty_artifact()
    original = binding_contract.hmac.compare_digest
    error = ValueError(f"distinct comparison failure {failure_call}")
    calls = 0

    def fail_selected(left: str, right: str) -> bool:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise error
        return original(left, right)

    monkeypatch.setattr(binding_contract.hmac, "compare_digest", fail_selected)
    with pytest.raises(ValueError) as exc_info:
        _bind(artifact, artifact.artifact_validation_identity_sha256)
    assert exc_info.value is error
    assert calls == failure_call


def test_artifact_binding_record_overflow_precedes_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _valid_empty_artifact()
    monkeypatch.setattr(
        binding_contract.json,
        "dumps",
        lambda *args, **kwargs: "x" * (MAX_ARTIFACT_VALIDATION_BINDING_BYTES + 1),
    )
    monkeypatch.setattr(
        binding_contract,
        "sha256",
        lambda value: (_ for _ in ()).throw(AssertionError(value)),
    )
    with pytest.raises(binding_contract._ExpectedIdentityBindingInvariantError) as exc_info:
        _bind(artifact, artifact.artifact_validation_identity_sha256)
    assert str(exc_info.value) == _INVARIANT_MESSAGE


def test_audit_binding_record_overflow_precedes_audit_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _valid_empty_artifact()
    original_dumps = binding_contract.json.dumps
    original_sha256 = binding_contract.sha256
    dump_calls = 0
    hash_inputs: list[bytes] = []

    def oversized_second(*args: object, **kwargs: object) -> str:
        nonlocal dump_calls
        dump_calls += 1
        if dump_calls == 2:
            return "x" * (MAX_EXPECTED_IDENTITY_BINDING_BYTES + 1)
        return original_dumps(*args, **kwargs)

    def recording_sha256(value: bytes) -> Any:
        hash_inputs.append(value)
        return original_sha256(value)

    monkeypatch.setattr(binding_contract.json, "dumps", oversized_second)
    monkeypatch.setattr(binding_contract, "sha256", recording_sha256)
    with pytest.raises(binding_contract._ExpectedIdentityBindingInvariantError) as exc_info:
        _bind(artifact, artifact.artifact_validation_identity_sha256)
    assert str(exc_info.value) == _INVARIANT_MESSAGE
    assert dump_calls == 2
    assert len(hash_inputs) == 1
    assert hash_inputs[0].startswith(_ARTIFACT_DOMAIN)


def test_production_module_has_no_try_trystar_or_assert() -> None:
    source = Path(binding_contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(
        isinstance(node, (ast.Try, ast.TryStar, ast.Assert))
        for node in ast.walk(tree)
    )


def test_binder_imports_only_public_b1p2_symbols() -> None:
    source = Path(binding_contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    b1p2_imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").endswith(
            "validate_step2_market_source_policy_approval_artifact"
        )
        for alias in node.names
    ]
    assert b1p2_imports
    assert set(b1p2_imports) <= set(artifact_contract.__all__)
    assert not any(name.startswith("_") for name in b1p2_imports)


_MODULE = binding_contract.__name__
_BASENAME = _MODULE.rsplit(".", 1)[-1]
_RELATIVE_PATH = Path("src/investment_orchestrator/validators") / f"{_BASENAME}.py"
_PUBLIC_SYMBOLS = frozenset(
    {
        "bind_step2_market_source_policy_approval_expected_identity",
        "Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult",
        "Step2MarketSourcePolicyApprovalExpectedIdentityBindingState",
        "Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic",
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
    for marker in (_MODULE, *_PUBLIC_SYMBOLS, RESULT_VERSION, BINDING_VERSION):
        if marker in source:
            findings.append(f"{relative_path}: text")
    return sorted(set(findings))


def test_reference_detector_covers_import_symbol_dynamic_and_literal_forms() -> None:
    cases = (
        f"import {_MODULE}\n",
        f"import {_MODULE} as contract\n",
        f"from {_MODULE} import {_State.__name__}\n",
        "handler = bind_step2_market_source_policy_approval_expected_identity\n",
        f"import importlib\nimportlib.import_module({_MODULE!r})\n",
        f"contract = __import__({_MODULE!r})\n",
        f"VERSION = {BINDING_VERSION!r}\n",
    )
    assert all(
        _reference_findings(f"synthetic/{index}.py", source)
        for index, source in enumerate(cases)
    )


def test_binder_has_zero_production_consumers() -> None:
    root = Path(__file__).resolve().parents[2]
    production_root = root / "src" / "investment_orchestrator"
    contract_path = root / _RELATIVE_PATH
    findings: list[str] = []
    for path in sorted(production_root.rglob("*.py")):
        if path == contract_path:
            continue
        relative = path.relative_to(root).as_posix()
        findings.extend(
            _reference_findings(relative, path.read_text(encoding="utf-8"))
        )
    assert sorted(set(findings)) == [], "\n".join(sorted(set(findings)))


def test_binder_has_no_external_or_downstream_capability() -> None:
    source = Path(binding_contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    allowed_roots = {
        "__future__",
        "dataclasses",
        "enum",
        "hashlib",
        "hmac",
        "investment_orchestrator",
        "json",
        "re",
        "types",
        "typing",
    }
    assert {name.split(".", 1)[0] for name in imported_modules} <= allowed_roots
    forbidden = (
        "workflow",
        "materializer",
        "publication",
        "step3",
        "step4",
        "compiler",
        "order",
        "broker",
        "network",
        "subprocess",
    )
    imported_text = "\n".join(sorted(imported_modules)).lower()
    assert all(marker not in imported_text for marker in forbidden)


def test_authority_surface_contains_no_approval_or_readiness_field() -> None:
    forbidden = {
        "approved",
        "operator_approved",
        "authorized",
        "activated",
        "active",
        "ready",
        "ready_for_trading",
        "permission_granted",
    }
    assert not forbidden & {
        item.name
        for item in fields(
            Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult
        )
    }
