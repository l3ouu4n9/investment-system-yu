"""Tests for the sealed decoded approval-intent statement validator."""

from __future__ import annotations

import ast
import copy
from dataclasses import fields, replace
from enum import IntEnum
import hashlib
import inspect
import json
from pathlib import Path
import pickle
import types
from collections.abc import Mapping

import pytest

from investment_orchestrator.parsers.parse_step2_market_source_policy_operator_approval_intent_statement import (
    parse_step2_market_source_policy_operator_approval_intent_statement_bytes,
)
from investment_orchestrator.validators import (
    validate_step2_market_source_policy_operator_approval_intent_statement as module,
)
from investment_orchestrator.validators.validate_step2_market_source_policy_operator_approval_intent_statement import (
    AUTHORITY_SCOPE,
    MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_CANONICAL_BYTES,
    MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_VALIDATION_DIAGNOSTICS,
    OPERATOR_APPROVAL_INTENT_STATEMENT_IDENTITY_DOMAIN,
    STATEMENT_SCHEMA_VERSION,
    TRADE_PERMISSION_EFFECT,
    Step2MarketSourcePolicyOperatorApprovalIntentAction as IntentAction,
    Step2MarketSourcePolicyOperatorApprovalIntentStatement as Statement,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic as Diagnostic,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult as Result,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState as State,
    VALIDATION_RESULT_VERSION,
    VALIDATOR_VERSION,
    validate_step2_market_source_policy_operator_approval_intent_statement as validate,
)


_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _valid_value(
    *,
    context: str = "v1",
    expected: str = _DIGEST_A,
    intent: str = "APPROVE",
    provenance: str = _DIGEST_B,
) -> dict[str, object]:
    return {
        "statement_schema_version": STATEMENT_SCHEMA_VERSION,
        "authentication_context_version": context,
        "expected_identity_binding_sha256": expected,
        "intent_action": intent,
        "provenance_identity_sha256": provenance,
    }


def _assert_invalid(
    result: Result,
    state: State,
    diagnostic: Diagnostic,
) -> None:
    assert result.validation_state is state
    assert result.diagnostics == (diagnostic,)
    assert result.validated_statement is None
    assert result.operator_approval_intent_statement_identity_sha256 is None
    assert result.semantic_identity_computed is False
    assert result.intent_evaluation_performed is False
    assert result.intent_action is None
    assert result.intent_is_approval is None


def _oracle(value: dict[str, object]) -> tuple[bytes, str]:
    record = {
        "authentication_context_version": value["authentication_context_version"],
        "expected_identity_binding_sha256": value["expected_identity_binding_sha256"],
        "intent_action": value["intent_action"],
        "provenance_identity_sha256": value["provenance_identity_sha256"],
        "statement_schema_version": STATEMENT_SCHEMA_VERSION,
    }
    canonical = json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    identity = hashlib.sha256(
        OPERATOR_APPROVAL_INTENT_STATEMENT_IDENTITY_DOMAIN + canonical
    ).hexdigest()
    return canonical, identity


def test_public_api_signature_requires_one_argument() -> None:
    signature = inspect.signature(validate)
    assert list(signature.parameters) == ["value"]
    parameter = signature.parameters["value"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    with pytest.raises(TypeError):
        validate()


def test_exact_json_non_object_roots_are_root_invalid() -> None:
    for value in (None, [], "", 0, 1.5, True, False):
        result = validate(value)
        _assert_invalid(result, State.ROOT_TYPE_INVALID, Diagnostic.STATEMENT_ROOT_TYPE_INVALID)
        assert result.input_type_check_performed is True
        assert result.input_type_valid is True
        assert result.root_type_check_performed is True
        assert result.root_type_valid is False
        assert result.key_set_validation_performed is False
        assert result.key_set_valid is None
        assert result.field_validation_performed is False
        assert result.field_validation_valid is None
        assert result.statement_contract_valid is None


class _DictSubclass(dict):
    pass


class _ListSubclass(list):
    pass


class _StrSubclass(str):
    pass


class _IntSubclass(int):
    pass


class _FloatSubclass(float):
    pass


class _StringKey(str):
    pass


class _IntEnumValue(IntEnum):
    ONE = 1


class _CustomMapping(Mapping[object, object]):
    def __getitem__(self, key: object) -> object:
        raise AssertionError("custom mapping was read")

    def __iter__(self) -> object:
        raise AssertionError("custom mapping was iterated")

    def __len__(self) -> int:
        raise AssertionError("custom mapping length was read")


class _Unreadable:
    def __getattribute__(self, name: str) -> object:
        if name in {"__class__", "__dict__"}:
            return object.__getattribute__(self, name)
        raise AssertionError("unsupported input was read")

    def __repr__(self) -> str:
        raise AssertionError("unsupported input was represented")

    def __bool__(self) -> bool:
        raise AssertionError("unsupported input was truth-tested")

    def __iter__(self) -> object:
        raise AssertionError("unsupported input was iterated")

    def __len__(self) -> int:
        raise AssertionError("unsupported input length was read")


def test_unsupported_values_are_classified_without_reading_them() -> None:
    parser_result = parse_step2_market_source_policy_operator_approval_intent_statement_bytes(
        b'{"a":[1]}'
    )
    array_parser_result = (
        parse_step2_market_source_policy_operator_approval_intent_statement_bytes(b"[1]")
    )
    unsupported_values = (
        _DictSubclass(),
        _ListSubclass(),
        _StrSubclass("x"),
        _IntSubclass(1),
        _FloatSubclass(1.0),
        _IntEnumValue.ONE,
        types.MappingProxyType({}),
        _CustomMapping(),
        (("a", 1),),
        b"{}",
        Path("statement.json"),
        parser_result,
        parser_result.immutable_parsed_value,
        array_parser_result.immutable_parsed_value,
        _Unreadable(),
    )
    for value in unsupported_values:
        result = validate(value)
        _assert_invalid(
            result,
            State.INPUT_TYPE_INVALID,
            Diagnostic.STATEMENT_INPUT_TYPE_INVALID,
        )
        assert result.input_type_check_performed is True
        assert result.input_type_valid is False
        assert result.root_type_check_performed is False
        assert result.root_type_valid is None
        assert result.key_set_validation_performed is False
        assert result.key_set_valid is None
        assert result.field_validation_performed is False
        assert result.field_validation_valid is None
        assert result.statement_contract_valid is None


def test_exact_dict_reaches_key_set_validation() -> None:
    result = validate({})
    _assert_invalid(result, State.KEY_SET_INVALID, Diagnostic.STATEMENT_KEY_SET_INVALID)
    assert result.input_type_valid is True
    assert result.root_type_valid is True
    assert result.key_set_validation_performed is True
    assert result.key_set_valid is False
    assert result.statement_contract_valid is False


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"unknown": "value"},
        {"statement_schema_version": STATEMENT_SCHEMA_VERSION},
        {
            **_valid_value(),
            "unknown": "value",
        },
        {
            key: item
            for key, item in _valid_value().items()
            if key != "intent_action"
        },
        {
            key: item
            for key, item in _valid_value().items()
            if key not in {"intent_action", "provenance_identity_sha256"}
        },
        {0: "value", **_valid_value()},
        {_StringKey("statement_schema_version"): STATEMENT_SCHEMA_VERSION,
         "authentication_context_version": "v1",
         "expected_identity_binding_sha256": _DIGEST_A,
         "intent_action": "APPROVE",
         "provenance_identity_sha256": _DIGEST_B},
    ],
)
def test_invalid_key_sets_have_one_key_set_diagnostic(value: dict[object, object]) -> None:
    result = validate(value)
    _assert_invalid(result, State.KEY_SET_INVALID, Diagnostic.STATEMENT_KEY_SET_INVALID)
    assert result.key_set_validation_performed is True
    assert result.key_set_valid is False
    assert result.field_validation_performed is False
    assert result.statement_contract_valid is False


def test_key_set_validity_is_insertion_order_neutral() -> None:
    normal = _valid_value()
    reverse = dict(reversed(tuple(normal.items())))
    normal_result = validate(normal)
    reverse_result = validate(reverse)
    assert normal_result.validation_state is State.VALID_APPROVE
    assert reverse_result.validation_state is State.VALID_APPROVE
    assert (
        normal_result.operator_approval_intent_statement_identity_sha256
        == reverse_result.operator_approval_intent_statement_identity_sha256
    )


@pytest.mark.parametrize("missing_key", sorted(_valid_value()))
def test_each_required_key_is_mandatory(missing_key: str) -> None:
    value = _valid_value()
    del value[missing_key]
    _assert_invalid(
        validate(value),
        State.KEY_SET_INVALID,
        Diagnostic.STATEMENT_KEY_SET_INVALID,
    )


@pytest.mark.parametrize("replaced_key", sorted(_valid_value()))
def test_each_unknown_key_and_mixed_missing_unknown_key_set_is_rejected(
    replaced_key: str,
) -> None:
    value = _valid_value()
    value["unknown_key"] = value.pop(replaced_key)
    _assert_invalid(
        validate(value),
        State.KEY_SET_INVALID,
        Diagnostic.STATEMENT_KEY_SET_INVALID,
    )


@pytest.mark.parametrize(
    ("field", "value", "state", "diagnostic"),
    [
        ("statement_schema_version", None, State.FIELD_TYPE_INVALID, Diagnostic.STATEMENT_SCHEMA_VERSION_INVALID),
        ("statement_schema_version", "wrong", State.FIELD_VALUE_INVALID, Diagnostic.STATEMENT_SCHEMA_VERSION_INVALID),
        ("authentication_context_version", None, State.FIELD_TYPE_INVALID, Diagnostic.AUTHENTICATION_CONTEXT_TYPE_INVALID),
        ("authentication_context_version", "", State.FIELD_VALUE_INVALID, Diagnostic.AUTHENTICATION_CONTEXT_SYNTAX_INVALID),
        ("authentication_context_version", "a" * 65, State.FIELD_VALUE_INVALID, Diagnostic.AUTHENTICATION_CONTEXT_SYNTAX_INVALID),
        ("authentication_context_version", "v\u00e9", State.FIELD_VALUE_INVALID, Diagnostic.AUTHENTICATION_CONTEXT_SYNTAX_INVALID),
        ("authentication_context_version", "bad__segment", State.FIELD_VALUE_INVALID, Diagnostic.AUTHENTICATION_CONTEXT_SYNTAX_INVALID),
        ("authentication_context_version", "latest", State.FIELD_VALUE_INVALID, Diagnostic.AUTHENTICATION_CONTEXT_RESERVED),
        ("authentication_context_version", "current", State.FIELD_VALUE_INVALID, Diagnostic.AUTHENTICATION_CONTEXT_RESERVED),
        ("authentication_context_version", "default", State.FIELD_VALUE_INVALID, Diagnostic.AUTHENTICATION_CONTEXT_RESERVED),
        ("authentication_context_version", "*", State.FIELD_VALUE_INVALID, Diagnostic.AUTHENTICATION_CONTEXT_RESERVED),
        ("expected_identity_binding_sha256", None, State.FIELD_TYPE_INVALID, Diagnostic.EXPECTED_IDENTITY_BINDING_TYPE_INVALID),
        ("expected_identity_binding_sha256", "A" * 64, State.FIELD_VALUE_INVALID, Diagnostic.EXPECTED_IDENTITY_BINDING_SYNTAX_INVALID),
        ("expected_identity_binding_sha256", "a" * 63, State.FIELD_VALUE_INVALID, Diagnostic.EXPECTED_IDENTITY_BINDING_SYNTAX_INVALID),
        ("expected_identity_binding_sha256", "0x" + "a" * 62, State.FIELD_VALUE_INVALID, Diagnostic.EXPECTED_IDENTITY_BINDING_SYNTAX_INVALID),
        ("intent_action", None, State.FIELD_TYPE_INVALID, Diagnostic.INTENT_ACTION_TYPE_INVALID),
        ("intent_action", "approve", State.FIELD_VALUE_INVALID, Diagnostic.INTENT_ACTION_VALUE_INVALID),
        ("intent_action", " APPROVE", State.FIELD_VALUE_INVALID, Diagnostic.INTENT_ACTION_VALUE_INVALID),
        ("provenance_identity_sha256", None, State.FIELD_TYPE_INVALID, Diagnostic.PROVENANCE_IDENTITY_TYPE_INVALID),
        ("provenance_identity_sha256", "b" * 63, State.FIELD_VALUE_INVALID, Diagnostic.PROVENANCE_IDENTITY_SYNTAX_INVALID),
        ("provenance_identity_sha256", "b" * 63 + "\uff42", State.FIELD_VALUE_INVALID, Diagnostic.PROVENANCE_IDENTITY_SYNTAX_INVALID),
    ],
)
def test_field_type_and_value_diagnostics(
    field: str,
    value: object,
    state: State,
    diagnostic: Diagnostic,
) -> None:
    statement = _valid_value()
    statement[field] = value
    result = validate(statement)
    _assert_invalid(result, state, diagnostic)
    assert result.field_validation_performed is True
    assert result.field_validation_valid is False
    assert result.statement_contract_valid is False


@pytest.mark.parametrize(
    "context",
    ["a", "a0", "v1", "alpha.beta", "alpha_beta", "alpha-beta", "a0.b1_c2-d3", "a" * 64],
)
def test_authentication_context_accepts_only_the_frozen_machine_id_grammar(
    context: str,
) -> None:
    result = validate(_valid_value(context=context))
    assert result.validation_state is State.VALID_APPROVE


@pytest.mark.parametrize(
    "context",
    ["0a", "A", "a.", "a_", "a-", "a..b", "a.-b", "a__b", "a--b", "a/b", "a b"],
)
def test_authentication_context_rejects_invalid_grammar(context: str) -> None:
    result = validate(_valid_value(context=context))
    _assert_invalid(
        result,
        State.FIELD_VALUE_INVALID,
        Diagnostic.AUTHENTICATION_CONTEXT_SYNTAX_INVALID,
    )


def test_reserved_context_check_precedes_grammar_check() -> None:
    result = validate(_valid_value(context="*"))
    _assert_invalid(
        result,
        State.FIELD_VALUE_INVALID,
        Diagnostic.AUTHENTICATION_CONTEXT_RESERVED,
    )


@pytest.mark.parametrize("field", ["expected_identity_binding_sha256", "provenance_identity_sha256"])
@pytest.mark.parametrize(
    "bad_value",
    [
        _StrSubclass("a" * 64),
        b"a" * 64,
        "a" * 63,
        "a" * 65,
        "a" * 63 + "A",
        "a" * 63 + " ",
        "aa" + ":" + "a" * 61,
        "0x" + "a" * 62,
        "a" * 63 + "\uff41",
    ],
)
def test_digest_fields_require_exact_lowercase_hex_strings(
    field: str,
    bad_value: object,
) -> None:
    statement = _valid_value()
    statement[field] = bad_value
    result = validate(statement)
    expected_diagnostic = (
        Diagnostic.EXPECTED_IDENTITY_BINDING_TYPE_INVALID
        if field == "expected_identity_binding_sha256" and type(bad_value) is not str
        else Diagnostic.PROVENANCE_IDENTITY_TYPE_INVALID
        if field == "provenance_identity_sha256" and type(bad_value) is not str
        else Diagnostic.EXPECTED_IDENTITY_BINDING_SYNTAX_INVALID
        if field == "expected_identity_binding_sha256"
        else Diagnostic.PROVENANCE_IDENTITY_SYNTAX_INVALID
    )
    expected_state = (
        State.FIELD_TYPE_INVALID
        if expected_diagnostic in {
            Diagnostic.EXPECTED_IDENTITY_BINDING_TYPE_INVALID,
            Diagnostic.PROVENANCE_IDENTITY_TYPE_INVALID,
        }
        else State.FIELD_VALUE_INVALID
    )
    _assert_invalid(result, expected_state, expected_diagnostic)


def test_field_validation_precedence_suppresses_later_fields() -> None:
    statement = _valid_value(
        context="*",
        expected="not-a-digest",
        intent="wrong",
        provenance="also-not-a-digest",
    )
    result = validate(statement)
    _assert_invalid(
        result,
        State.FIELD_VALUE_INVALID,
        Diagnostic.AUTHENTICATION_CONTEXT_RESERVED,
    )


@pytest.mark.parametrize(
    ("field", "diagnostic"),
    [
        ("statement_schema_version", Diagnostic.STATEMENT_SCHEMA_VERSION_INVALID),
        ("authentication_context_version", Diagnostic.AUTHENTICATION_CONTEXT_TYPE_INVALID),
        ("expected_identity_binding_sha256", Diagnostic.EXPECTED_IDENTITY_BINDING_TYPE_INVALID),
        ("intent_action", Diagnostic.INTENT_ACTION_TYPE_INVALID),
        ("provenance_identity_sha256", Diagnostic.PROVENANCE_IDENTITY_TYPE_INVALID),
    ],
)
def test_every_string_field_rejects_a_string_subclass(
    field: str,
    diagnostic: Diagnostic,
) -> None:
    value = _valid_value()
    value[field] = _StrSubclass(str(value[field]))
    _assert_invalid(validate(value), State.FIELD_TYPE_INVALID, diagnostic)


def test_schema_failure_suppresses_adversarial_later_field() -> None:
    class _UnreadValue:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError("later value was inspected")

    statement = _valid_value()
    statement["statement_schema_version"] = "wrong"
    statement["authentication_context_version"] = _UnreadValue()
    result = validate(statement)
    _assert_invalid(
        result,
        State.FIELD_VALUE_INVALID,
        Diagnostic.STATEMENT_SCHEMA_VERSION_INVALID,
    )


def test_valid_approve_and_reject_have_complete_semantics_and_snapshot() -> None:
    for literal, state, is_approval in (
        ("APPROVE", State.VALID_APPROVE, True),
        ("REJECT", State.VALID_REJECT, False),
    ):
        value = _valid_value(intent=literal)
        result = validate(value)
        canonical, identity = _oracle(value)
        assert result.validation_state is state
        assert result.validated_statement is not None
        assert type(result.validated_statement) is Statement
        assert result.validated_statement.statement_schema_version == STATEMENT_SCHEMA_VERSION
        assert result.validated_statement.authentication_context_version == "v1"
        assert result.validated_statement.expected_identity_binding_sha256 == _DIGEST_A
        assert result.validated_statement.provenance_identity_sha256 == _DIGEST_B
        assert result.validated_statement.intent_action.value == literal
        assert result.operator_approval_intent_statement_identity_sha256 == identity
        assert result.semantic_identity_computed is True
        assert result.input_type_check_performed is True
        assert result.input_type_valid is True
        assert result.root_type_check_performed is True
        assert result.root_type_valid is True
        assert result.key_set_validation_performed is True
        assert result.key_set_valid is True
        assert result.field_validation_performed is True
        assert result.field_validation_valid is True
        assert result.statement_contract_valid is True
        assert result.intent_evaluation_performed is True
        assert result.intent_action.value == literal
        assert result.intent_is_approval is is_approval
        assert result.diagnostics == ()
        assert len(canonical) <= MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_CANONICAL_BYTES


def test_canonical_maxima_and_bound_are_exact() -> None:
    approve = _valid_value(context="a" * 64, intent="APPROVE")
    reject = _valid_value(context="a" * 64, intent="REJECT")
    approve_canonical, _ = _oracle(approve)
    reject_canonical, _ = _oracle(reject)
    assert len(approve_canonical) == 419
    assert len(reject_canonical) == 418
    assert MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_CANONICAL_BYTES == 512
    assert len(approve_canonical) < MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_CANONICAL_BYTES


def test_identity_is_domain_separated_and_sensitive_to_every_variable_field() -> None:
    base = _valid_value()
    base_result = validate(base)
    assert base_result.operator_approval_intent_statement_identity_sha256 is not None
    assert base_result.operator_approval_intent_statement_identity_sha256 != hashlib.sha256(
        _oracle(base)[0]
    ).hexdigest()
    variants = (
        _valid_value(context="v2"),
        _valid_value(expected="c" * 64),
        _valid_value(intent="REJECT"),
        _valid_value(provenance="d" * 64),
    )
    for variant in variants:
        assert (
            validate(variant).operator_approval_intent_statement_identity_sha256
            != base_result.operator_approval_intent_statement_identity_sha256
        )


def test_fixed_schema_key_is_included_in_the_canonical_identity_record() -> None:
    value = _valid_value()
    result = validate(value)
    canonical, identity = _oracle(value)
    counterfactual = json.dumps(
        {
            "authentication_context_version": value["authentication_context_version"],
            "expected_identity_binding_sha256": value["expected_identity_binding_sha256"],
            "intent_action": value["intent_action"],
            "provenance_identity_sha256": value["provenance_identity_sha256"],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert result.operator_approval_intent_statement_identity_sha256 == identity
    assert b'"statement_schema_version"' in canonical
    assert identity != hashlib.sha256(
        OPERATOR_APPROVAL_INTENT_STATEMENT_IDENTITY_DOMAIN + counterfactual
    ).hexdigest()


def test_result_and_snapshot_field_order_is_frozen() -> None:
    assert [item.name for item in fields(Statement)] == [
        "statement_schema_version",
        "authentication_context_version",
        "expected_identity_binding_sha256",
        "intent_action",
        "provenance_identity_sha256",
    ]
    assert [item.name for item in fields(Result)] == [
        "result_version",
        "validator_version",
        "validation_state",
        "validated_statement",
        "operator_approval_intent_statement_identity_sha256",
        "input_type_check_performed",
        "input_type_valid",
        "root_type_check_performed",
        "root_type_valid",
        "key_set_validation_performed",
        "key_set_valid",
        "field_validation_performed",
        "field_validation_valid",
        "semantic_identity_computed",
        "statement_contract_valid",
        "intent_evaluation_performed",
        "intent_action",
        "intent_is_approval",
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


def test_enum_orders_and_diagnostic_bound_are_exact() -> None:
    assert list(State) == [
        State.INPUT_TYPE_INVALID,
        State.ROOT_TYPE_INVALID,
        State.KEY_SET_INVALID,
        State.FIELD_TYPE_INVALID,
        State.FIELD_VALUE_INVALID,
        State.VALID_APPROVE,
        State.VALID_REJECT,
    ]
    assert [item.name for item in Diagnostic] == [
        "STATEMENT_INPUT_TYPE_INVALID",
        "STATEMENT_ROOT_TYPE_INVALID",
        "STATEMENT_KEY_SET_INVALID",
        "STATEMENT_SCHEMA_VERSION_INVALID",
        "AUTHENTICATION_CONTEXT_TYPE_INVALID",
        "AUTHENTICATION_CONTEXT_SYNTAX_INVALID",
        "AUTHENTICATION_CONTEXT_RESERVED",
        "EXPECTED_IDENTITY_BINDING_TYPE_INVALID",
        "EXPECTED_IDENTITY_BINDING_SYNTAX_INVALID",
        "INTENT_ACTION_TYPE_INVALID",
        "INTENT_ACTION_VALUE_INVALID",
        "PROVENANCE_IDENTITY_TYPE_INVALID",
        "PROVENANCE_IDENTITY_SYNTAX_INVALID",
    ]
    assert [item.value for item in Diagnostic] == [item.name.lower() for item in Diagnostic]
    assert MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_VALIDATION_DIAGNOSTICS == 1


@pytest.mark.parametrize(
    "value,state,checks,contract",
    [
        (b"{}", State.INPUT_TYPE_INVALID, (False, None, False, None, False, None), None),
        (None, State.ROOT_TYPE_INVALID, (True, False, False, None, False, None), None),
        ({}, State.KEY_SET_INVALID, (True, True, True, False, False, None), False),
        (
            {**_valid_value(), "intent_action": None},
            State.FIELD_TYPE_INVALID,
            (True, True, True, True, True, False),
            False,
        ),
        (
            {**_valid_value(), "intent_action": "wrong"},
            State.FIELD_VALUE_INVALID,
            (True, True, True, True, True, False),
            False,
        ),
        (_valid_value(), State.VALID_APPROVE, (True, True, True, True, True, True), True),
        (
            _valid_value(intent="REJECT"),
            State.VALID_REJECT,
            (True, True, True, True, True, True),
            True,
        ),
    ],
)
def test_complete_branch_matrix(
    value: object,
    state: State,
    checks: tuple[object, ...],
    contract: bool | None,
) -> None:
    result = validate(value)
    assert result.validation_state is state
    assert (
        result.input_type_valid,
        result.root_type_valid,
        result.key_set_validation_performed,
        result.key_set_valid,
        result.field_validation_performed,
        result.field_validation_valid,
    ) == checks
    assert result.statement_contract_valid is contract
    if state in {State.VALID_APPROVE, State.VALID_REJECT}:
        assert result.validated_statement is not None
        assert result.operator_approval_intent_statement_identity_sha256 is not None
    else:
        assert result.validated_statement is None
        assert result.operator_approval_intent_statement_identity_sha256 is None


def test_authority_markers_are_fixed_and_non_authorizing() -> None:
    result = validate(_valid_value())
    assert result.result_version == VALIDATION_RESULT_VERSION
    assert result.validator_version == VALIDATOR_VERSION
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


@pytest.mark.parametrize("result", [validate(b"{}"), validate(_valid_value())])
def test_results_have_no_truth_value(result: Result) -> None:
    with pytest.raises(TypeError, match="^inspect statement_contract_valid explicitly; operator approval-intent statement validation results have no truth value$"):
        bool(result)


class _UnreadState:
    def __getattribute__(self, name: str) -> object:
        raise AssertionError("state input was inspected")


class _UnreadProtocol:
    def __getattribute__(self, name: str) -> object:
        raise AssertionError("protocol input was inspected")


def test_snapshot_and_result_construction_and_reconstruction_are_sealed() -> None:
    result = validate(_valid_value())
    assert result.validated_statement is not None
    snapshot = result.validated_statement
    for cls, error in (
        (Result, "operator approval-intent statement validation results are created only by the public validator"),
        (Statement, "operator approval-intent statement snapshots are created only by the public validator"),
    ):
        with pytest.raises(TypeError, match=f"^{error}$"):
            cls()
        with pytest.raises(TypeError, match=f"^{error}$"):
            cls(*([None] * len(fields(cls))))
    for value, error in (
        (result, "operator approval-intent statement validation results are created only by the public validator"),
        (snapshot, "operator approval-intent statement snapshots are created only by the public validator"),
    ):
        with pytest.raises(TypeError, match=f"^{error}$"):
            replace(value)
        with pytest.raises(TypeError, match=f"^{error}$"):
            value.__setstate__(_UnreadState())
        with pytest.raises(TypeError, match=f"^{error}$"):
            value.__reduce__()
        with pytest.raises(TypeError, match=f"^{error}$"):
            value.__reduce_ex__(_UnreadProtocol())
        with pytest.raises(TypeError, match=f"^{error}$"):
            copy.copy(value)
        with pytest.raises(TypeError, match=f"^{error}$"):
            copy.deepcopy(value)
        for protocol in range(6):
            with pytest.raises(TypeError, match=f"^{error}$"):
                pickle.dumps(value, protocol=protocol)
        uninitialized = object.__new__(type(value))
        with pytest.raises(TypeError, match=f"^{error}$"):
            uninitialized.__setstate__(_UnreadState())
    assert result.validated_statement is snapshot
    assert snapshot.intent_action is IntentAction.APPROVE


def test_state_restoration_is_class_owned_not_dataclass_generated() -> None:
    assert "__setstate__" in Result.__dict__
    assert "__setstate__" in Statement.__dict__
    assert Result.__dict__["__setstate__"].__name__ == "__setstate__"
    assert Statement.__dict__["__setstate__"].__name__ == "__setstate__"


def test_allocation_sites_are_only_inside_the_private_factory() -> None:
    source = inspect.getsource(module)
    tree = ast.parse(source)
    locations = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "object"
            and node.func.attr == "__new__"
        ):
            locations.append(node)
    assert len(locations) == 2
    for node in locations:
        containing_functions = [
            candidate.name
            for candidate in ast.walk(tree)
            if isinstance(candidate, ast.FunctionDef)
            and candidate.lineno <= node.lineno <= candidate.end_lineno
        ]
        assert containing_functions[-1] == "_create_result"


def test_dependency_failures_propagate_and_canonical_bound_precedes_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _valid_value()

    def fail_json(*args: object, **kwargs: object) -> str:
        raise RuntimeError("json failure")

    monkeypatch.setattr(module.json, "dumps", fail_json)
    with pytest.raises(RuntimeError, match="json failure"):
        validate(ready)

    monkeypatch.undo()

    def fail_utf8(
        statement: Statement,
    ) -> bytes:
        del statement
        raise UnicodeError("utf8 failure")

    monkeypatch.setattr(module, "_canonical_statement_utf8", fail_utf8)
    with pytest.raises(UnicodeError, match="utf8 failure"):
        validate(ready)

    monkeypatch.undo()
    original_sha256 = module.hashlib.sha256

    def fail_hash(*args: object, **kwargs: object) -> object:
        raise RuntimeError("hash failure")

    monkeypatch.setattr(module.hashlib, "sha256", fail_hash)
    with pytest.raises(RuntimeError, match="hash failure"):
        validate(ready)

    monkeypatch.undo()
    monkeypatch.setattr(module, "MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_CANONICAL_BYTES", 1)
    called = False

    def track_hash(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        return original_sha256(*args, **kwargs)

    monkeypatch.setattr(module.hashlib, "sha256", track_hash)
    with pytest.raises(RuntimeError, match="validator invariant violated"):
        validate(ready)
    assert called is False


def test_module_is_pure_and_has_no_production_consumers() -> None:
    source = inspect.getsource(module)
    imports = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    assert set(imports) <= {"__future__", "dataclasses", "enum", "hashlib", "json", "re", "typing"}
    root = Path(__file__).resolve().parents[2]
    module_path = Path(module.__file__).resolve()
    module_name = (
        "investment_orchestrator.validators."
        "validate_step2_market_source_policy_operator_approval_intent_statement"
    )
    for source_path in (root / "src").rglob("*.py"):
        if source_path.resolve() == module_path:
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                imported_modules.append(node.module)
        assert module_name not in imported_modules, source_path
