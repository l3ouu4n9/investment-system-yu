"""Tests for sealed raw/object approval-intent statement composition."""

from __future__ import annotations

import ast
import copy
from dataclasses import fields, replace
import hashlib
import inspect
import json
from pathlib import Path
import pickle

import pytest

from investment_orchestrator.parsers.parse_step2_market_source_policy_operator_approval_intent_statement import (
    FrozenApprovalIntentJsonArray,
    FrozenApprovalIntentJsonObject,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementParseState as ParseState,
)
from investment_orchestrator.validators import (
    validate_step2_market_source_policy_operator_approval_intent_statement_artifact as module,
)
from investment_orchestrator.validators.validate_step2_market_source_policy_operator_approval_intent_statement import (
    Step2MarketSourcePolicyOperatorApprovalIntentAction as IntentAction,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic as StatementDiagnostic,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState as StatementState,
)
from investment_orchestrator.validators.validate_step2_market_source_policy_operator_approval_intent_statement_artifact import (
    AUTHORITY_SCOPE,
    MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_CANONICAL_BYTES,
    MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_DIAGNOSTICS,
    OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_IDENTITY_DOMAIN,
    RESULT_VERSION,
    TRADE_PERMISSION_EFFECT,
    VALIDATOR_VERSION,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationDiagnostic as Diagnostic,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationResult as Result,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationState as State,
    validate_step2_market_source_policy_operator_approval_intent_statement_artifact_bytes as validate,
)


_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_SCHEMA = "step2_market_source_policy_operator_approval_intent_statement_v1"
_INVARIANT_ERROR = (
    "operator approval-intent statement artifact validator invariant violated"
)


def _value(
    *,
    context: str = "v1",
    expected: object = _DIGEST_A,
    intent: object = "APPROVE",
    provenance: object = _DIGEST_B,
) -> dict[str, object]:
    return {
        "statement_schema_version": _SCHEMA,
        "authentication_context_version": context,
        "expected_identity_binding_sha256": expected,
        "intent_action": intent,
        "provenance_identity_sha256": provenance,
    }


def _raw(value: object, *, padded_to: int | None = None) -> bytes:
    encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
    if padded_to is None:
        return encoded
    assert len(encoded) <= padded_to
    return b" " * (padded_to - len(encoded)) + encoded


def _sealed_field_values(value: object) -> tuple[object, ...]:
    return tuple(getattr(value, item.name) for item in fields(type(value)))


def _artifact_branch_values(result: Result) -> tuple[object, ...]:
    nested = {"parser_result", "statement_validation_result"}
    return tuple(
        getattr(result, item.name)
        for item in fields(Result)
        if item.name not in nested
    )


def _oracle(result: Result) -> tuple[bytes, str]:
    parser_result = result.parser_result
    statement_result = result.statement_validation_result
    assert parser_result is not None
    assert statement_result is not None
    record = {
        "artifact_contract_valid": result.artifact_contract_valid,
        "composition_checks": {
            "literal_intent_evaluation_performed": (
                result.literal_intent_evaluation_performed
            ),
            "parser_result_integrity_check_performed": True,
            "parser_result_integrity_valid": True,
            "parsed_value_conversion_performed": True,
            "parsed_value_conversion_valid": True,
            "parsed_value_identity_recheck_performed": True,
            "parsed_value_identity_recheck_valid": True,
            "statement_semantic_identity_recheck_performed": (
                result.statement_semantic_identity_recheck_performed
            ),
            "statement_semantic_identity_recheck_valid": (
                result.statement_semantic_identity_recheck_valid
            ),
            "statement_validation_performed": True,
            "statement_validation_result_integrity_check_performed": True,
            "statement_validation_result_integrity_valid": True,
            "statement_validation_valid": result.statement_validation_valid,
        },
        "composition_state": result.composition_state.value,
        "literal_intent_action": (
            None
            if result.literal_intent_action is None
            else result.literal_intent_action.value
        ),
        "literal_intent_is_approval": result.literal_intent_is_approval,
        "parser_result": {
            "parse_state": parser_result.parse_state.value,
            "parsed_value_identity_sha256": (
                parser_result.parsed_value_identity_sha256
            ),
            "parser_version": parser_result.parser_version,
            "raw_statement_sha256": parser_result.raw_statement_sha256,
            "raw_statement_size_bytes": parser_result.raw_statement_size_bytes,
            "result_version": parser_result.result_version,
        },
        "result_version": RESULT_VERSION,
        "statement_validation_result": {
            "diagnostics": [
                diagnostic.value for diagnostic in statement_result.diagnostics
            ],
            "operator_approval_intent_statement_identity_sha256": (
                statement_result
                .operator_approval_intent_statement_identity_sha256
            ),
            "result_version": statement_result.result_version,
            "statement_contract_valid": (
                statement_result.statement_contract_valid
            ),
            "validation_state": statement_result.validation_state.value,
            "validator_version": statement_result.validator_version,
        },
        "validator_version": VALIDATOR_VERSION,
    }
    canonical = json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return canonical, hashlib.sha256(
        OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_IDENTITY_DOMAIN
        + canonical
    ).hexdigest()


def _assert_authority_markers(result: Result) -> None:
    assert result.authority_scope == AUTHORITY_SCOPE
    assert result.not_authentication is True
    assert result.not_approval_authorization is True
    assert result.not_activation_authorization is True
    assert result.not_trade_authorization is True
    assert result.trade_permission_effect == TRADE_PERMISSION_EFFECT == "none"
    assert result.authentication_evaluation_performed is False
    assert result.authorship_evaluation_performed is False
    assert result.freshness_evaluation_performed is False
    assert result.replay_evaluation_performed is False
    assert result.lifecycle_evaluation_performed is False
    assert result.activation_evaluation_performed is False
    assert result.workflow_permission_evaluated is False
    assert result.order_compilation_evaluated is False


def test_public_api_signature_and_exact_enums_are_frozen() -> None:
    signature = inspect.signature(validate)
    assert tuple(signature.parameters) == ("value",)
    parameter = signature.parameters["value"]
    assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        validate()
    assert list(State) == [
        State.RAW_PARSE_INVALID,
        State.PARSER_RESULT_INTEGRITY_INVALID,
        State.PARSED_VALUE_IDENTITY_BINDING_INVALID,
        State.STATEMENT_CONTRACT_INVALID,
        State.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID,
        State.STATEMENT_SEMANTIC_IDENTITY_BINDING_INVALID,
        State.VALID_APPROVE,
        State.VALID_REJECT,
    ]
    assert [item.name for item in Diagnostic] == [
        "RAW_STATEMENT_PARSE_INVALID",
        "PARSER_RESULT_INTEGRITY_INVALID",
        "PARSED_VALUE_IDENTITY_BINDING_INVALID",
        "STATEMENT_CONTRACT_INVALID",
        "STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID",
        "STATEMENT_SEMANTIC_IDENTITY_BINDING_INVALID",
    ]
    assert [item.value for item in Diagnostic] == [item.name.lower() for item in Diagnostic]
    assert MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_DIAGNOSTICS == 1


@pytest.mark.parametrize(
    ("raw", "parser_state"),
    [
        (None, ParseState.INPUT_ABSENT),
        (object(), ParseState.INPUT_TYPE_INVALID),
        (b"", ParseState.RAW_SIZE_INVALID),
        (b"\xff", ParseState.ENCODING_INVALID),
        (b"{", ParseState.JSON_GRAMMAR_INVALID),
        (b'{"a":1,"a":2}', ParseState.DUPLICATE_KEY_INVALID),
        (b'"\\ud800"', ParseState.UNICODE_SCALAR_INVALID),
        (b"[" + b",".join(b"0" for _ in range(65)) + b"]", ParseState.RESOURCE_LIMIT_INVALID),
    ],
)
def test_normal_parser_invalid_branches_are_delegated(
    raw: object,
    parser_state: ParseState,
) -> None:
    result = validate(raw)
    assert result.composition_state is State.RAW_PARSE_INVALID
    assert result.diagnostics == (Diagnostic.RAW_STATEMENT_PARSE_INVALID,)
    assert result.parser_result is not None
    assert result.parser_result.parse_state is parser_state
    assert result.statement_validation_result is None
    assert result.parsed_value_conversion_performed is False
    assert result.parsed_value_identity_recheck_performed is False
    assert result.statement_validation_performed is False
    assert result.artifact_contract_valid is False
    assert result.operator_approval_intent_statement_artifact_validation_identity_sha256 is None
    _assert_authority_markers(result)


class _UnreadUnsupportedRawInput:
    def __getattribute__(self, name: str) -> object:
        raise AssertionError(f"unsupported raw input was read through {name}")

    def __iter__(self) -> object:
        raise AssertionError("unsupported raw input was iterated")

    def __len__(self) -> int:
        raise AssertionError("unsupported raw input length was read")

    def __bool__(self) -> bool:
        raise AssertionError("unsupported raw input truth value was read")

    def __repr__(self) -> str:
        raise AssertionError("unsupported raw input representation was read")


def test_unsupported_raw_input_remains_unread_beyond_exact_type_classification() -> None:
    result = validate(_UnreadUnsupportedRawInput())
    assert result.composition_state is State.RAW_PARSE_INVALID
    assert result.parser_result is not None
    assert result.parser_result.parse_state is ParseState.INPUT_TYPE_INVALID
    assert result.statement_validation_result is None


class _HostileString(str):
    def __new__(cls, value: str, calls: list[str]) -> _HostileString:
        instance = super().__new__(cls, value)
        instance._calls = calls
        return instance

    def _explode(self, operation: str) -> object:
        self._calls.append(operation)
        raise RuntimeError(f"hostile string {operation}")

    def __eq__(self, value: object) -> object:
        del value
        return self._explode("__eq__")

    def __ne__(self, value: object) -> object:
        del value
        return self._explode("__ne__")

    def __bool__(self) -> bool:
        return self._explode("__bool__")  # type: ignore[return-value]

    def __str__(self) -> str:
        return self._explode("__str__")  # type: ignore[return-value]

    def __repr__(self) -> str:
        return self._explode("__repr__")  # type: ignore[return-value]

    def __hash__(self) -> int:
        return self._explode("__hash__")  # type: ignore[return-value]

    def __iter__(self) -> object:
        return self._explode("__iter__")

    def __len__(self) -> int:
        return self._explode("__len__")  # type: ignore[return-value]

    def __getitem__(self, value: object) -> object:
        del value
        return self._explode("__getitem__")


class _HostileInteger(int):
    def __new__(cls, value: int, calls: list[str]) -> _HostileInteger:
        instance = super().__new__(cls, value)
        instance._calls = calls
        return instance

    def _explode(self, operation: str) -> object:
        self._calls.append(operation)
        raise RuntimeError(f"hostile integer {operation}")

    def __eq__(self, value: object) -> object:
        del value
        return self._explode("__eq__")

    def __ne__(self, value: object) -> object:
        del value
        return self._explode("__ne__")

    def __lt__(self, value: object) -> object:
        del value
        return self._explode("__lt__")

    def __le__(self, value: object) -> object:
        del value
        return self._explode("__le__")

    def __gt__(self, value: object) -> object:
        del value
        return self._explode("__gt__")

    def __ge__(self, value: object) -> object:
        del value
        return self._explode("__ge__")

    def __bool__(self) -> bool:
        return self._explode("__bool__")  # type: ignore[return-value]


class _HostileObject:
    def __init__(self, calls: list[str]) -> None:
        object.__setattr__(self, "_calls", calls)

    def _explode(self, operation: str) -> object:
        object.__getattribute__(self, "_calls").append(operation)
        raise RuntimeError(f"hostile object {operation}")

    def __getattribute__(self, name: str) -> object:
        if name in {"_calls", "_explode", "__class__"}:
            return object.__getattribute__(self, name)
        return self._explode(f"attribute:{name}")

    def __eq__(self, value: object) -> object:
        del value
        return self._explode("__eq__")

    def __ne__(self, value: object) -> object:
        del value
        return self._explode("__ne__")

    def __bool__(self) -> bool:
        return self._explode("__bool__")  # type: ignore[return-value]

    def __str__(self) -> str:
        return self._explode("__str__")  # type: ignore[return-value]

    def __repr__(self) -> str:
        return self._explode("__repr__")  # type: ignore[return-value]

    def __hash__(self) -> int:
        return self._explode("__hash__")  # type: ignore[return-value]

    def __iter__(self) -> object:
        return self._explode("__iter__")

    def __len__(self) -> int:
        return self._explode("__len__")  # type: ignore[return-value]

    def __getitem__(self, value: object) -> object:
        del value
        return self._explode("__getitem__")


class _HostileTuple(tuple[object, ...]):
    def __new__(cls, calls: list[str]) -> _HostileTuple:
        instance = super().__new__(cls, ())
        instance._calls = calls
        return instance

    def _explode(self, operation: str) -> object:
        self._calls.append(operation)
        raise RuntimeError(f"hostile tuple {operation}")

    def __eq__(self, value: object) -> object:
        del value
        return self._explode("__eq__")

    def __ne__(self, value: object) -> object:
        del value
        return self._explode("__ne__")

    def __bool__(self) -> bool:
        return self._explode("__bool__")  # type: ignore[return-value]

    def __str__(self) -> str:
        return self._explode("__str__")  # type: ignore[return-value]

    def __repr__(self) -> str:
        return self._explode("__repr__")  # type: ignore[return-value]

    def __hash__(self) -> int:
        return self._explode("__hash__")  # type: ignore[return-value]

    def __iter__(self) -> object:
        return self._explode("__iter__")

    def __len__(self) -> int:
        return self._explode("__len__")  # type: ignore[return-value]

    def __getitem__(self, value: object) -> object:
        del value
        return self._explode("__getitem__")


@pytest.mark.parametrize("raw", [b"null", b"[]", b'"x"', b"1", b"-0.0", b"true"])
def test_every_valid_non_object_root_reaches_p4a2_root_invalid(raw: bytes) -> None:
    result = validate(raw)
    assert result.composition_state is State.STATEMENT_CONTRACT_INVALID
    assert result.parser_result is not None
    assert result.parser_result.parse_state is ParseState.VALID
    assert result.statement_validation_result is not None
    assert result.statement_validation_result.validation_state is StatementState.ROOT_TYPE_INVALID
    assert result.statement_validation_valid is False
    assert result.artifact_contract_valid is False
    assert result.operator_approval_intent_statement_artifact_validation_identity_sha256 is not None


@pytest.mark.parametrize(
    ("intent", "state", "is_approval"),
    [
        ("APPROVE", State.VALID_APPROVE, True),
        ("REJECT", State.VALID_REJECT, False),
    ],
)
def test_valid_actions_bind_all_identities_and_remain_non_authorizing(
    intent: str,
    state: State,
    is_approval: bool,
) -> None:
    result = validate(_raw(_value(intent=intent)))
    assert result.composition_state is state
    assert result.parser_result is not None
    assert result.statement_validation_result is not None
    assert result.artifact_contract_valid is True
    assert result.literal_intent_evaluation_performed is True
    assert result.literal_intent_action is (
        IntentAction.APPROVE if is_approval else IntentAction.REJECT
    )
    assert result.literal_intent_is_approval is is_approval
    assert result.diagnostics == ()
    assert result.parsed_value_conversion_valid is True
    assert result.parsed_value_identity_recheck_valid is True
    assert result.statement_validation_result_integrity_valid is True
    assert result.statement_semantic_identity_recheck_valid is True
    canonical, identity = _oracle(result)
    assert len(canonical) <= MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_CANONICAL_BYTES
    assert result.operator_approval_intent_statement_artifact_validation_identity_sha256 == identity
    _assert_authority_markers(result)


@pytest.mark.parametrize(
    ("field", "bad_value", "expected_diagnostic"),
    [
        ("statement_schema_version", "wrong", StatementDiagnostic.STATEMENT_SCHEMA_VERSION_INVALID),
        ("authentication_context_version", "*", StatementDiagnostic.AUTHENTICATION_CONTEXT_RESERVED),
        ("expected_identity_binding_sha256", "A" * 64, StatementDiagnostic.EXPECTED_IDENTITY_BINDING_SYNTAX_INVALID),
        ("intent_action", "approve", StatementDiagnostic.INTENT_ACTION_VALUE_INVALID),
        ("provenance_identity_sha256", "b" * 63, StatementDiagnostic.PROVENANCE_IDENTITY_SYNTAX_INVALID),
    ],
)
def test_statement_contract_failures_retain_verified_nested_results(
    field: str,
    bad_value: object,
    expected_diagnostic: StatementDiagnostic,
) -> None:
    value = _value()
    value[field] = bad_value
    result = validate(_raw(value))
    assert result.composition_state is State.STATEMENT_CONTRACT_INVALID
    assert result.diagnostics == (Diagnostic.STATEMENT_CONTRACT_INVALID,)
    assert result.parser_result is not None
    assert result.statement_validation_result is not None
    assert result.statement_validation_result.diagnostics == (expected_diagnostic,)
    assert result.statement_validation_valid is False
    assert result.statement_semantic_identity_recheck_performed is False
    assert result.artifact_contract_valid is False
    assert result.operator_approval_intent_statement_artifact_validation_identity_sha256 is not None
    canonical, identity = _oracle(result)
    assert len(canonical) <= MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_CANONICAL_BYTES
    assert result.operator_approval_intent_statement_artifact_validation_identity_sha256 == identity


def test_exact_dependency_call_counts_and_parser_invalid_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser_calls = 0
    validator_calls = 0
    parser = module.parse_step2_market_source_policy_operator_approval_intent_statement_bytes
    statement_validator = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement
    )

    def count_parser(value: object) -> object:
        nonlocal parser_calls
        parser_calls += 1
        return parser(value)

    def count_validator(value: object) -> object:
        nonlocal validator_calls
        validator_calls += 1
        return statement_validator(value)

    monkeypatch.setattr(module, "parse_step2_market_source_policy_operator_approval_intent_statement_bytes", count_parser)
    monkeypatch.setattr(module, "validate_step2_market_source_policy_operator_approval_intent_statement", count_validator)
    valid = validate(_raw(_value()))
    assert valid.composition_state is State.VALID_APPROVE
    assert (parser_calls, validator_calls) == (1, 1)
    invalid = validate(b"{")
    assert invalid.composition_state is State.RAW_PARSE_INVALID
    assert (parser_calls, validator_calls) == (2, 1)


def test_wrong_parser_result_type_fails_closed_without_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module,
        "parse_step2_market_source_policy_operator_approval_intent_statement_bytes",
        lambda value: object(),
    )
    result = validate(_raw(_value()))
    assert result.composition_state is State.PARSER_RESULT_INTEGRITY_INVALID
    assert result.parser_result is None
    assert result.statement_validation_result is None
    assert result.artifact_contract_valid is None


@pytest.mark.parametrize(
    ("attribute", "replacement"),
    [
        ("result_version", "wrong-result-version"),
        ("parser_version", "wrong-parser-version"),
        ("not_authentication", False),
        ("raw_statement_size_bytes", 999),
        ("raw_statement_sha256", "0" * 64),
        ("parsed_value_identity_sha256", "not-a-sha256"),
        ("parsed_value_available", False),
    ],
)
def test_completed_parser_result_contract_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    replacement: object,
) -> None:
    raw = _raw(_value())
    parser = module.parse_step2_market_source_policy_operator_approval_intent_statement_bytes
    parser_result = parser(raw)
    object.__setattr__(parser_result, attribute, replacement)
    p4a2_called = False

    def return_drifted(value: object) -> object:
        del value
        return parser_result

    def must_not_run(value: object) -> object:
        del value
        nonlocal p4a2_called
        p4a2_called = True
        raise AssertionError("p4a2 must be suppressed after parser integrity drift")

    monkeypatch.setattr(
        module,
        "parse_step2_market_source_policy_operator_approval_intent_statement_bytes",
        return_drifted,
    )
    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        must_not_run,
    )
    result = validate(raw)
    assert result.composition_state is State.PARSER_RESULT_INTEGRITY_INVALID
    assert result.parser_result is None
    assert result.statement_validation_result is None
    assert result.artifact_contract_valid is None
    assert p4a2_called is False


@pytest.mark.parametrize(
    ("attribute", "replacement_factory"),
    [
        ("result_version", lambda calls, raw: _HostileString("version", calls)),
        ("parser_version", lambda calls, raw: _HostileString("parser", calls)),
        ("authority_scope", lambda calls, raw: _HostileString("scope", calls)),
        (
            "trade_permission_effect",
            lambda calls, raw: _HostileString("none", calls),
        ),
        (
            "raw_statement_sha256",
            lambda calls, raw: _HostileString("a" * 64, calls),
        ),
        (
            "parsed_value_identity_sha256",
            lambda calls, raw: _HostileString("a" * 64, calls),
        ),
        ("parse_state", lambda calls, raw: _HostileObject(calls)),
        ("diagnostics", lambda calls, raw: _HostileTuple(calls)),
        (
            "text_decoding_performed",
            lambda calls, raw: _HostileObject(calls),
        ),
        ("not_authentication", lambda calls, raw: _HostileObject(calls)),
        (
            "raw_statement_size_bytes",
            lambda calls, raw: _HostileInteger(len(raw), calls),
        ),
        (
            "immutable_parsed_value",
            lambda calls, raw: _HostileObject(calls),
        ),
    ],
)
def test_hostile_completed_p4a1_fields_fail_closed_without_executing_hooks(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    replacement_factory: object,
) -> None:
    raw = _raw(_value())
    parser = module.parse_step2_market_source_policy_operator_approval_intent_statement_bytes
    parser_result = parser(raw)
    calls: list[str] = []
    object.__setattr__(parser_result, attribute, replacement_factory(calls, raw))
    p4a2_called = False

    def return_forged(value: object) -> object:
        del value
        return parser_result

    def must_not_run(value: object) -> object:
        del value
        nonlocal p4a2_called
        p4a2_called = True
        raise AssertionError("p4a2 must be suppressed for hostile p4a1 output")

    monkeypatch.setattr(
        module,
        "parse_step2_market_source_policy_operator_approval_intent_statement_bytes",
        return_forged,
    )
    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        must_not_run,
    )
    result = validate(raw)
    assert result.composition_state is State.PARSER_RESULT_INTEGRITY_INVALID
    assert result.parser_result is None
    assert result.statement_validation_result is None
    assert result.artifact_contract_valid is None
    assert p4a2_called is False
    assert calls == []


@pytest.mark.parametrize(
    "forged_tree",
    [
        lambda: _forged_object((("a", 1), ("a", 2))),
        lambda: _forged_array_with_cycle(),
        lambda: _shared_array_tree(),
        lambda: float("nan"),
        lambda: FrozenApprovalIntentJsonArray(tuple(range(65))),
        lambda: "a" * 1025,
    ],
)
def test_deep_frozen_tree_integrity_defects_fail_before_conversion(
    monkeypatch: pytest.MonkeyPatch,
    forged_tree: object,
) -> None:
    raw = _raw(_value())
    parser = module.parse_step2_market_source_policy_operator_approval_intent_statement_bytes
    parser_result = parser(raw)
    tree = forged_tree()
    object.__setattr__(parser_result, "immutable_parsed_value", tree)
    p4a2_called = False

    def return_forged(value: object) -> object:
        del value
        return parser_result

    def must_not_run(value: object) -> object:
        del value
        nonlocal p4a2_called
        p4a2_called = True
        raise AssertionError("p4a2 must be suppressed after malformed frozen tree")

    monkeypatch.setattr(
        module,
        "parse_step2_market_source_policy_operator_approval_intent_statement_bytes",
        return_forged,
    )
    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        must_not_run,
    )
    result = validate(raw)
    assert result.composition_state is State.PARSER_RESULT_INTEGRITY_INVALID
    assert result.parser_result is None
    assert result.statement_validation_result is None
    assert p4a2_called is False


def _forged_object(items: object) -> FrozenApprovalIntentJsonObject:
    forged = object.__new__(FrozenApprovalIntentJsonObject)
    object.__setattr__(forged, "items", items)
    return forged


def _forged_array_with_cycle() -> FrozenApprovalIntentJsonArray:
    forged = object.__new__(FrozenApprovalIntentJsonArray)
    object.__setattr__(forged, "items", (forged,))
    return forged


def _shared_array_tree() -> FrozenApprovalIntentJsonArray:
    child = FrozenApprovalIntentJsonArray((1,))
    return FrozenApprovalIntentJsonArray((child, child))


def test_malformed_frozen_tree_fails_closed_without_partial_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = module.parse_step2_market_source_policy_operator_approval_intent_statement_bytes

    def malformed(value: object) -> object:
        result = parser(value)
        broken = object.__new__(FrozenApprovalIntentJsonArray)
        object.__setattr__(broken, "items", [])
        object.__setattr__(result, "immutable_parsed_value", broken)
        return result

    monkeypatch.setattr(module, "parse_step2_market_source_policy_operator_approval_intent_statement_bytes", malformed)
    result = validate(_raw(_value()))
    assert result.composition_state is State.PARSER_RESULT_INTEGRITY_INVALID
    assert result.parser_result is None
    assert result.statement_validation_result is None


def test_parsed_identity_mismatch_discards_stale_parser_and_suppresses_p4a2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = module.parse_step2_market_source_policy_operator_approval_intent_statement_bytes
    called = False

    def stale(value: object) -> object:
        result = parser(value)
        object.__setattr__(result, "parsed_value_identity_sha256", "0" * 64)
        return result

    def must_not_run(value: object) -> object:
        del value
        nonlocal called
        called = True
        raise AssertionError("p4a2 must be suppressed")

    monkeypatch.setattr(module, "parse_step2_market_source_policy_operator_approval_intent_statement_bytes", stale)
    monkeypatch.setattr(module, "validate_step2_market_source_policy_operator_approval_intent_statement", must_not_run)
    result = validate(_raw(_value()))
    assert result.composition_state is State.PARSED_VALUE_IDENTITY_BINDING_INVALID
    assert result.parser_result is None
    assert result.statement_validation_result is None
    assert result.artifact_contract_valid is False
    assert called is False


def test_wrong_or_input_incompatible_p4a2_result_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statement_validator = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement
    )

    def incompatible(value: object) -> object:
        del value
        return statement_validator(_value(intent="REJECT"))

    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        incompatible,
    )
    result = validate(_raw(_value(intent="APPROVE")))
    assert result.composition_state is State.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID
    assert result.parser_result is not None
    assert result.statement_validation_result is None
    assert result.artifact_contract_valid is None


def test_wrong_p4a2_result_type_fails_closed_and_retains_only_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        lambda value: object(),
    )
    result = validate(_raw(_value()))
    assert result.composition_state is State.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID
    assert result.parser_result is not None
    assert result.statement_validation_result is None
    assert result.artifact_contract_valid is None


def test_p4a2_input_type_invalid_is_impossible_for_converted_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statement_validator = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement
    )

    def impossible(value: object) -> object:
        del value
        return statement_validator(object())

    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        impossible,
    )
    result = validate(_raw(_value()))
    assert result.composition_state is State.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID
    assert result.parser_result is not None
    assert result.statement_validation_result is None
    assert result.artifact_contract_valid is None


@pytest.mark.parametrize(
    ("mutate_result", "mutate_snapshot"),
    [
        (
            lambda result: object.__setattr__(result, "validator_version", "wrong"),
            None,
        ),
        (
            lambda result: object.__setattr__(result, "not_authentication", False),
            None,
        ),
        (
            lambda result: object.__setattr__(result, "field_validation_valid", False),
            None,
        ),
        (
            lambda result: object.__setattr__(
                result,
                "diagnostics",
                (StatementDiagnostic.INTENT_ACTION_VALUE_INVALID,),
            ),
            None,
        ),
        (
            lambda result: None,
            lambda snapshot: object.__setattr__(
                snapshot,
                "authentication_context_version",
                "v2",
            ),
        ),
    ],
)
def test_completed_p4a2_contract_or_input_consistency_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    mutate_result: object,
    mutate_snapshot: object,
) -> None:
    statement_validator = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement
    )

    def drifted(value: object) -> object:
        result = statement_validator(value)
        mutate_result(result)
        if mutate_snapshot is not None:
            snapshot = result.validated_statement
            assert snapshot is not None
            mutate_snapshot(snapshot)
        return result

    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        drifted,
    )
    result = validate(_raw(_value()))
    assert result.composition_state is State.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID
    assert result.parser_result is not None
    assert result.statement_validation_result is None
    assert result.artifact_contract_valid is None


@pytest.mark.parametrize(
    ("target", "attribute", "replacement_factory"),
    [
        ("result", "result_version", lambda calls: _HostileString("version", calls)),
        ("result", "validator_version", lambda calls: _HostileString("validator", calls)),
        ("result", "authority_scope", lambda calls: _HostileString("scope", calls)),
        (
            "result",
            "trade_permission_effect",
            lambda calls: _HostileString("none", calls),
        ),
        (
            "result",
            "operator_approval_intent_statement_identity_sha256",
            lambda calls: _HostileString("a" * 64, calls),
        ),
        ("result", "validation_state", lambda calls: _HostileObject(calls)),
        ("result", "diagnostics", lambda calls: _HostileTuple(calls)),
        (
            "result",
            "input_type_check_performed",
            lambda calls: _HostileObject(calls),
        ),
        ("result", "not_authentication", lambda calls: _HostileObject(calls)),
        (
            "result",
            "statement_contract_valid",
            lambda calls: _HostileObject(calls),
        ),
        ("result", "validated_statement", lambda calls: _HostileObject(calls)),
        (
            "snapshot",
            "authentication_context_version",
            lambda calls: _HostileString("v1", calls),
        ),
        ("snapshot", "intent_action", lambda calls: _HostileObject(calls)),
    ],
)
def test_hostile_completed_p4a2_fields_fail_closed_without_executing_hooks(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    attribute: str,
    replacement_factory: object,
) -> None:
    statement_validator = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement
    )
    forged_result = statement_validator(_value())
    calls: list[str] = []
    replacement = replacement_factory(calls)
    if target == "result":
        object.__setattr__(forged_result, attribute, replacement)
    else:
        snapshot = forged_result.validated_statement
        assert snapshot is not None
        object.__setattr__(snapshot, attribute, replacement)

    def return_forged(value: object) -> object:
        del value
        return forged_result

    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        return_forged,
    )
    result = validate(_raw(_value()))
    assert (
        result.composition_state
        is State.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID
    )
    assert result.parser_result is not None
    assert result.statement_validation_result is None
    assert result.artifact_contract_valid is None
    assert calls == []


def test_hostile_trade_permission_effect_regression_is_classified_not_propagated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statement_validator = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement
    )
    forged_result = statement_validator(_value())
    calls: list[str] = []
    object.__setattr__(
        forged_result,
        "trade_permission_effect",
        _HostileString("none", calls),
    )

    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        lambda value: forged_result,
    )
    result = validate(_raw(_value()))
    assert (
        result.composition_state
        is State.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID
    )
    assert calls == []


def test_converted_value_is_fresh_exact_builtin_and_not_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statement_validator = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement
    )
    received: list[object] = []

    def inspect_converted(value: object) -> object:
        received.append(value)
        assert type(value) is dict
        assert all(type(key) is str for key in value)
        return statement_validator(value)

    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        inspect_converted,
    )
    result = validate(_raw(_value()))
    assert result.composition_state is State.VALID_APPROVE
    assert len(received) == 1
    converted = received[0]
    assert type(converted) is dict
    assert result.parser_result is not None
    assert type(result.parser_result.immutable_parsed_value) is not type(converted)
    converted["intent_action"] = "REJECT"
    assert result.literal_intent_action is IntentAction.APPROVE
    assert result.statement_validation_result is not None
    assert result.statement_validation_result.intent_action is IntentAction.APPROVE


@pytest.mark.parametrize(
    ("raw", "expected_type"),
    [
        (b"null", type(None)),
        (b"[]", list),
        (b'"x"', str),
        (b"1", int),
        (b"-0.0", float),
        (b"true", bool),
    ],
)
def test_conversion_preserves_exact_json_scalar_and_container_types(
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    expected_type: type[object],
) -> None:
    statement_validator = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement
    )
    received: list[object] = []

    def capture(value: object) -> object:
        received.append(value)
        return statement_validator(value)

    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        capture,
    )
    result = validate(raw)
    assert result.composition_state is State.STATEMENT_CONTRACT_INVALID
    assert len(received) == 1
    converted = received[0]
    assert type(converted) is expected_type
    if type(converted) is float:
        assert json.dumps(converted, separators=(",", ":")) == "-0.0"


def test_nested_conversion_uses_only_fresh_exact_builtin_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statement_validator = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement
    )
    received: list[object] = []

    def capture(value: object) -> object:
        received.append(value)
        return statement_validator(value)

    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        capture,
    )
    result = validate(b'{"outer":[1,{"inner":false},null]}')
    assert result.composition_state is State.STATEMENT_CONTRACT_INVALID
    assert received == [{"outer": [1, {"inner": False}, None]}]
    converted = received[0]
    assert type(converted) is dict
    outer = converted["outer"]
    assert type(outer) is list
    assert type(outer[1]) is dict
    assert not isinstance(converted, FrozenApprovalIntentJsonObject)
    assert not isinstance(outer, FrozenApprovalIntentJsonArray)


def test_unexpected_upstream_exceptions_propagate_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ParserFailure(RuntimeError):
        pass

    class ValidatorFailure(RuntimeError):
        pass

    def fail_parser(value: object) -> object:
        del value
        raise ParserFailure("parser failure")

    monkeypatch.setattr(
        module,
        "parse_step2_market_source_policy_operator_approval_intent_statement_bytes",
        fail_parser,
    )
    with pytest.raises(ParserFailure, match="parser failure"):
        validate(_raw(_value()))
    monkeypatch.undo()

    def fail_validator(value: object) -> object:
        del value
        raise ValidatorFailure("validator failure")

    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        fail_validator,
    )
    with pytest.raises(ValidatorFailure, match="validator failure"):
        validate(_raw(_value()))


def test_semantic_identity_mismatch_discards_stale_statement_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statement_validator = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement
    )

    def stale(value: object) -> object:
        result = statement_validator(value)
        object.__setattr__(
            result,
            "operator_approval_intent_statement_identity_sha256",
            "0" * 64,
        )
        return result

    monkeypatch.setattr(
        module,
        "validate_step2_market_source_policy_operator_approval_intent_statement",
        stale,
    )
    result = validate(_raw(_value()))
    assert result.composition_state is State.STATEMENT_SEMANTIC_IDENTITY_BINDING_INVALID
    assert result.parser_result is not None
    assert result.statement_validation_result is None
    assert result.literal_intent_evaluation_performed is False
    assert result.literal_intent_action is None
    assert result.artifact_contract_valid is False


def _semantic_oracle(result: Result) -> tuple[bytes, str]:
    statement_result = result.statement_validation_result
    assert statement_result is not None
    snapshot = statement_result.validated_statement
    assert snapshot is not None
    record = {
        "authentication_context_version": snapshot.authentication_context_version,
        "expected_identity_binding_sha256": snapshot.expected_identity_binding_sha256,
        "intent_action": snapshot.intent_action.value,
        "provenance_identity_sha256": snapshot.provenance_identity_sha256,
        "statement_schema_version": snapshot.statement_schema_version,
    }
    canonical = json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    domain = b"step2_market_source_policy_operator_approval_intent_statement_v1\0"
    return canonical, hashlib.sha256(domain + canonical).hexdigest()


def test_semantic_identity_is_independently_bound_and_field_sensitive() -> None:
    baseline = validate(_raw(_value()))
    baseline_canonical, baseline_identity = _semantic_oracle(baseline)
    assert baseline.statement_validation_result is not None
    assert (
        baseline.statement_validation_result
        .operator_approval_intent_statement_identity_sha256
        == baseline_identity
    )
    assert len(baseline_canonical) <= 512
    variants = [
        _value(context="v2"),
        _value(expected="c" * 64),
        _value(intent="REJECT"),
        _value(provenance="d" * 64),
    ]
    for value in variants:
        result = validate(_raw(value))
        assert result.composition_state in {State.VALID_APPROVE, State.VALID_REJECT}
        _, identity = _semantic_oracle(result)
        assert identity != baseline_identity
    counterfactual = json.dumps(
        {
            "authentication_context_version": "v1",
            "expected_identity_binding_sha256": _DIGEST_A,
            "intent_action": "APPROVE",
            "provenance_identity_sha256": _DIGEST_B,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(
        b"step2_market_source_policy_operator_approval_intent_statement_v1\0"
        + counterfactual
    ).hexdigest() != baseline_identity


def test_parsed_and_artifact_identity_bind_raw_format_and_typed_json_value() -> None:
    compact = validate(_raw(_value()))
    spaced = validate(json.dumps(_value(), indent=1).encode("utf-8"))
    assert compact.parser_result is not None
    assert spaced.parser_result is not None
    assert compact.statement_validation_result is not None
    assert spaced.statement_validation_result is not None
    assert (
        compact.parser_result.parsed_value_identity_sha256
        == spaced.parser_result.parsed_value_identity_sha256
    )
    assert (
        compact.statement_validation_result
        .operator_approval_intent_statement_identity_sha256
        == spaced.statement_validation_result
        .operator_approval_intent_statement_identity_sha256
    )
    assert (
        compact.operator_approval_intent_statement_artifact_validation_identity_sha256
        != spaced.operator_approval_intent_statement_artifact_validation_identity_sha256
    )
    integer = validate(b"1")
    decimal = validate(b"1.0")
    negative_zero = validate(b"-0.0")
    assert integer.parser_result is not None
    assert decimal.parser_result is not None
    assert negative_zero.parser_result is not None
    assert (
        integer.parser_result.parsed_value_identity_sha256
        != decimal.parser_result.parsed_value_identity_sha256
    )
    assert (
        decimal.parser_result.parsed_value_identity_sha256
        != negative_zero.parser_result.parsed_value_identity_sha256
    )


def test_composition_identity_maxima_and_bound_are_exact() -> None:
    approve = validate(_raw(_value(context="a" * 64, intent="APPROVE"), padded_to=4096))
    reject = validate(_raw(_value(context="a" * 64, intent="REJECT"), padded_to=4096))
    invalid_value = _value(context="a" * 64, expected="A" * 64)
    invalid = validate(_raw(invalid_value, padded_to=4096))
    approve_canonical, _ = _oracle(approve)
    reject_canonical, _ = _oracle(reject)
    invalid_canonical, _ = _oracle(invalid)
    assert len(approve_canonical) == 1840
    assert len(reject_canonical) == 1838
    assert len(invalid_canonical) == 1839
    assert MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_CANONICAL_BYTES == 2048


def test_composition_canonical_bound_precedes_artifact_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_dumps = module.json.dumps
    original_hash = module._sha256_hex
    hashed_inputs: list[bytes] = []

    def bounded_dumps(value: object, *args: object, **kwargs: object) -> str:
        if type(value) is dict and "composition_checks" in value:
            return "x" * (
                MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_CANONICAL_BYTES
                + 1
            )
        return original_dumps(value, *args, **kwargs)

    def track_hash(value: bytes) -> str:
        hashed_inputs.append(value)
        return original_hash(value)

    monkeypatch.setattr(module.json, "dumps", bounded_dumps)
    monkeypatch.setattr(module, "_sha256_hex", track_hash)
    with pytest.raises(
        RuntimeError,
        match=(
            "^operator approval-intent statement artifact validator invariant "
            "violated$"
        ),
    ):
        validate(_raw(_value()))
    assert not [
        value
        for value in hashed_inputs
        if value.startswith(
            OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_IDENTITY_DOMAIN
        )
    ]


def test_factory_rejects_contradictory_retention_and_branch_combinations() -> None:
    with pytest.raises(RuntimeError, match=f"^{_INVARIANT_ERROR}$"):
        module._create_result(composition_state=State.RAW_PARSE_INVALID)
    with pytest.raises(RuntimeError, match=f"^{_INVARIANT_ERROR}$"):
        module._create_result(
            composition_state=State.PARSER_RESULT_INTEGRITY_INVALID,
            parser_result=(
                module.parse_step2_market_source_policy_operator_approval_intent_statement_bytes(
                    _raw(_value())
                )
            ),
        )
    with pytest.raises(RuntimeError, match=f"^{_INVARIANT_ERROR}$"):
        module._create_result(composition_state=State.VALID_APPROVE)


@pytest.mark.parametrize(
    ("name", "composition_state", "parser_value", "statement_value"),
    [
        (
            "valid-context",
            State.VALID_APPROVE,
            _value(context="v1"),
            _value(context="v2"),
        ),
        (
            "valid-expected-digest",
            State.VALID_APPROVE,
            _value(expected=_DIGEST_A),
            _value(expected="c" * 64),
        ),
        (
            "valid-provenance-digest",
            State.VALID_APPROVE,
            _value(provenance=_DIGEST_B),
            _value(provenance="d" * 64),
        ),
        (
            "approve-versus-reject",
            State.VALID_APPROVE,
            _value(intent="APPROVE"),
            _value(intent="REJECT"),
        ),
        (
            "reject-versus-approve",
            State.VALID_REJECT,
            _value(intent="REJECT"),
            _value(intent="APPROVE"),
        ),
        (
            "object-versus-root-invalid",
            State.STATEMENT_CONTRACT_INVALID,
            _value(),
            [],
        ),
        (
            "non-object-versus-key-invalid",
            State.STATEMENT_CONTRACT_INVALID,
            [],
            {},
        ),
        (
            "missing-key-versus-field-invalid",
            State.STATEMENT_CONTRACT_INVALID,
            {
                key: value
                for key, value in _value().items()
                if key != "intent_action"
            },
            _value(expected="A" * 64),
        ),
        (
            "field-type-versus-field-value",
            State.STATEMENT_CONTRACT_INVALID,
            _value(expected=0),
            _value(expected="A" * 64),
        ),
        (
            "schema-versus-context",
            State.STATEMENT_CONTRACT_INVALID,
            _value(**{"context": "v1"})
            | {"statement_schema_version": "wrong"},
            _value(context="*"),
        ),
        (
            "context-versus-expected-digest",
            State.STATEMENT_CONTRACT_INVALID,
            _value(context="*"),
            _value(expected="A" * 64),
        ),
    ],
)
def test_factory_rejects_detectable_genuine_cross_result_mismatches_before_allocation(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    composition_state: State,
    parser_value: object,
    statement_value: object,
) -> None:
    del name
    parser_result = (
        module.parse_step2_market_source_policy_operator_approval_intent_statement_bytes(
            _raw(parser_value)
        )
    )
    statement_result = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement(
            statement_value
        )
    )
    parser_before = _sealed_field_values(parser_result)
    statement_before = _sealed_field_values(statement_result)
    allocation_reached = False

    def refuse_allocation(values: dict[str, object]) -> None:
        del values
        nonlocal allocation_reached
        allocation_reached = True
        raise AssertionError("factory reached its result-allocation gate")

    monkeypatch.setattr(module, "_validate_result_values", refuse_allocation)
    with pytest.raises(RuntimeError, match=f"^{_INVARIANT_ERROR}$"):
        module._create_result(
            composition_state=composition_state,
            parser_result=parser_result,
            statement_validation_result=statement_result,
        )
    assert allocation_reached is False
    assert _sealed_field_values(parser_result) == parser_before
    assert _sealed_field_values(statement_result) == statement_before


def test_factory_accepts_observationally_equivalent_root_invalid_results() -> None:
    parser_result = (
        module.parse_step2_market_source_policy_operator_approval_intent_statement_bytes(
            _raw([])
        )
    )
    list_result = module.validate_step2_market_source_policy_operator_approval_intent_statement(
        []
    )
    null_result = module.validate_step2_market_source_policy_operator_approval_intent_statement(
        None
    )
    assert _sealed_field_values(list_result) == _sealed_field_values(null_result)

    list_artifact = module._create_result(
        composition_state=State.STATEMENT_CONTRACT_INVALID,
        parser_result=parser_result,
        statement_validation_result=list_result,
    )
    null_artifact = module._create_result(
        composition_state=State.STATEMENT_CONTRACT_INVALID,
        parser_result=parser_result,
        statement_validation_result=null_result,
    )

    for result in (list_artifact, null_artifact):
        assert result.composition_state is State.STATEMENT_CONTRACT_INVALID
        assert result.artifact_contract_valid is False
        assert result.literal_intent_evaluation_performed is False
        assert result.literal_intent_action is None
        assert result.literal_intent_is_approval is None
        _assert_authority_markers(result)
    assert _artifact_branch_values(list_artifact) == _artifact_branch_values(
        null_artifact
    )
    assert (
        list_artifact.operator_approval_intent_statement_artifact_validation_identity_sha256
        == null_artifact.operator_approval_intent_statement_artifact_validation_identity_sha256
    )


def test_factory_accepts_observationally_equivalent_field_invalid_results() -> None:
    parser_value = _value(context="!")
    parser_result = (
        module.parse_step2_market_source_policy_operator_approval_intent_statement_bytes(
            _raw(parser_value)
        )
    )
    first_result = module.validate_step2_market_source_policy_operator_approval_intent_statement(
        parser_value
    )
    equivalent_result = (
        module.validate_step2_market_source_policy_operator_approval_intent_statement(
            _value(context="@")
        )
    )
    assert _sealed_field_values(first_result) == _sealed_field_values(
        equivalent_result
    )

    first_artifact = module._create_result(
        composition_state=State.STATEMENT_CONTRACT_INVALID,
        parser_result=parser_result,
        statement_validation_result=first_result,
    )
    equivalent_artifact = module._create_result(
        composition_state=State.STATEMENT_CONTRACT_INVALID,
        parser_result=parser_result,
        statement_validation_result=equivalent_result,
    )
    assert _artifact_branch_values(first_artifact) == _artifact_branch_values(
        equivalent_artifact
    )
    assert (
        first_artifact.operator_approval_intent_statement_artifact_validation_identity_sha256
        == equivalent_artifact.operator_approval_intent_statement_artifact_validation_identity_sha256
    )


@pytest.mark.parametrize(
    ("value", "state"),
    [
        (_value(), State.VALID_APPROVE),
        (_value(intent="REJECT"), State.VALID_REJECT),
        (_value(context="*"), State.STATEMENT_CONTRACT_INVALID),
        ([], State.STATEMENT_CONTRACT_INVALID),
    ],
)
def test_matching_genuine_results_remain_accepted_by_the_public_path(
    value: object,
    state: State,
) -> None:
    result = validate(_raw(value))
    assert result.composition_state is state


@pytest.mark.parametrize(
    ("raw", "state", "contract"),
    [
        (b"{", State.RAW_PARSE_INVALID, False),
        (_raw(_value(expected="A" * 64)), State.STATEMENT_CONTRACT_INVALID, False),
        (_raw(_value()), State.VALID_APPROVE, True),
        (_raw(_value(intent="REJECT")), State.VALID_REJECT, True),
    ],
)
def test_complete_normal_branch_matrix(
    raw: bytes,
    state: State,
    contract: bool,
) -> None:
    result = validate(raw)
    assert result.composition_state is state
    assert result.artifact_contract_valid is contract
    assert result.parser_result_integrity_check_performed is True
    assert result.parser_result_integrity_valid is True
    if state is State.RAW_PARSE_INVALID:
        assert result.parsed_value_conversion_performed is False
        assert result.statement_validation_performed is False
    else:
        assert result.parsed_value_conversion_performed is True
        assert result.parsed_value_identity_recheck_valid is True
    if state in {State.VALID_APPROVE, State.VALID_REJECT}:
        assert result.literal_intent_evaluation_performed is True
        assert result.diagnostics == ()
    else:
        assert len(result.diagnostics) == 1


class _UnreadState:
    def __getattribute__(self, name: str) -> object:
        raise AssertionError("state was read")


class _UnreadProtocol:
    def __getattribute__(self, name: str) -> object:
        raise AssertionError("protocol was read")


def test_result_is_sealed_and_has_no_truth_value() -> None:
    result = validate(_raw(_value()))
    error = (
        "operator approval-intent statement artifact validation results are created "
        "only by the public validator"
    )
    with pytest.raises(TypeError, match=f"^{error}$"):
        Result()
    with pytest.raises(TypeError, match=f"^{error}$"):
        Result(*([None] * len(fields(Result))))
    with pytest.raises(TypeError, match=f"^{error}$"):
        replace(result)
    with pytest.raises(TypeError, match=f"^{error}$"):
        result.__setstate__(_UnreadState())
    with pytest.raises(TypeError, match=f"^{error}$"):
        result.__reduce__()
    with pytest.raises(TypeError, match=f"^{error}$"):
        result.__reduce_ex__(_UnreadProtocol())
    with pytest.raises(TypeError, match=f"^{error}$"):
        copy.copy(result)
    with pytest.raises(TypeError, match=f"^{error}$"):
        copy.deepcopy(result)
    for protocol in range(6):
        with pytest.raises(TypeError, match=f"^{error}$"):
            pickle.dumps(result, protocol=protocol)
    uninitialized = object.__new__(Result)
    with pytest.raises(TypeError, match=f"^{error}$"):
        uninitialized.__setstate__(_UnreadState())
    with pytest.raises(TypeError, match="^inspect artifact_contract_valid explicitly; operator approval-intent statement artifact validation results have no truth value$"):
        bool(result)


def test_result_field_order_and_single_allocation_site_are_frozen() -> None:
    assert [item.name for item in fields(Result)] == [
        "result_version",
        "validator_version",
        "composition_state",
        "parser_result",
        "statement_validation_result",
        "parser_result_integrity_check_performed",
        "parser_result_integrity_valid",
        "parsed_value_conversion_performed",
        "parsed_value_conversion_valid",
        "parsed_value_identity_recheck_performed",
        "parsed_value_identity_recheck_valid",
        "statement_validation_performed",
        "statement_validation_result_integrity_check_performed",
        "statement_validation_result_integrity_valid",
        "statement_validation_valid",
        "statement_semantic_identity_recheck_performed",
        "statement_semantic_identity_recheck_valid",
        "operator_approval_intent_statement_artifact_validation_identity_sha256",
        "artifact_validation_identity_computed",
        "artifact_contract_valid",
        "literal_intent_evaluation_performed",
        "literal_intent_action",
        "literal_intent_is_approval",
        "diagnostics",
        "authority_scope",
        "not_authentication",
        "not_approval_authorization",
        "not_activation_authorization",
        "not_trade_authorization",
        "trade_permission_effect",
        "authentication_evaluation_performed",
        "authorship_evaluation_performed",
        "freshness_evaluation_performed",
        "replay_evaluation_performed",
        "lifecycle_evaluation_performed",
        "activation_evaluation_performed",
        "workflow_permission_evaluated",
        "order_compilation_evaluated",
    ]
    source = inspect.getsource(module)
    tree = ast.parse(source)
    allocations = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "object"
        and node.func.attr == "__new__"
    ]
    assert len(allocations) == 1
    for allocation in allocations:
        containers = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.lineno <= allocation.lineno <= node.end_lineno
        ]
        assert containers[-1] == "_create_result"


def test_dependency_failures_propagate_and_bound_precedes_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw(_value())

    def fail_json(*args: object, **kwargs: object) -> str:
        raise RuntimeError("json failure")

    monkeypatch.setattr(module.json, "dumps", fail_json)
    with pytest.raises(RuntimeError, match="json failure"):
        validate(raw)
    monkeypatch.undo()

    def fail_hash(*args: object, **kwargs: object) -> object:
        raise RuntimeError("hash failure")

    monkeypatch.setattr(module.hashlib, "sha256", fail_hash)
    with pytest.raises(RuntimeError, match="hash failure"):
        validate(raw)
    monkeypatch.undo()

    original_hash = module._sha256_hex
    hashed_inputs: list[bytes] = []

    def track_hash(value: bytes) -> str:
        hashed_inputs.append(value)
        return original_hash(value)

    monkeypatch.setattr(
        module,
        "_canonical_json_utf8",
        lambda value: b"x" * (module.MAX_PARSED_VALUE_CANONICAL_BYTES + 1),
    )
    monkeypatch.setattr(module, "_sha256_hex", track_hash)
    result = validate(raw)
    assert result.composition_state is State.PARSER_RESULT_INTEGRITY_INVALID
    assert raw in hashed_inputs
    assert not [
        value
        for value in hashed_inputs
        if value.startswith(module.PARSED_VALUE_IDENTITY_DOMAIN)
    ]


def test_module_is_pure_has_exact_upstream_consumers_and_no_downstream_consumers() -> None:
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    assert imports <= {
        "__future__",
        "dataclasses",
        "enum",
        "hashlib",
        "json",
        "math",
        "re",
        "typing",
        "investment_orchestrator.parsers.parse_step2_market_source_policy_operator_approval_intent_statement",
        "investment_orchestrator.validators.validate_step2_market_source_policy_operator_approval_intent_statement",
    }
    assert "except Exception" not in source
    assert "except BaseException" not in source
    assert "assert " not in source
    assert "TryStar" not in source

    repository_root = Path(__file__).resolve().parents[2]
    module_path = Path(module.__file__).resolve()
    module_name = (
        "investment_orchestrator.validators."
        "validate_step2_market_source_policy_operator_approval_intent_statement_artifact"
    )
    consumers: set[str] = set()
    for source_path in (repository_root / "src").rglob("*.py"):
        if source_path.resolve() == module_path:
            continue
        source_tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(source_tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module
            for node in ast.walk(source_tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        if module_name in imported:
            consumers.add(source_path.relative_to(repository_root).as_posix())
    assert consumers == set()
