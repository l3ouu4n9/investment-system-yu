from __future__ import annotations

import ast
import copy
import dataclasses
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import inspect
import json
import math
import pickle
from pathlib import Path
from typing import Any, get_type_hints

import pytest

from investment_orchestrator.parsers import (
    parse_step2_market_source_policy_operator_approval_intent_statement as parser_contract,
)
from investment_orchestrator.parsers.parse_step2_market_source_policy_operator_approval_intent_statement import (
    AUTHENTICATION_EVALUATION_PERFORMED,
    APPROVAL_INTENT_STATEMENT_PARSER_REVISION,
    APPROVAL_INTENT_STATEMENT_RESULT_REVISION,
    AUTHORITY_SCOPE,
    FRESHNESS_EVALUATION_PERFORMED,
    FROZEN_JSON_BOOLEAN_COERCION_ERROR,
    FrozenApprovalIntentJsonArray,
    FrozenApprovalIntentJsonObject,
    INTENT_EVALUATION_PERFORMED,
    LIFECYCLE_EVALUATION_PERFORMED,
    MAX_APPROVAL_INTENT_STATEMENT_PARSE_DIAGNOSTICS,
    MAX_ARRAY_ITEM_COUNT,
    MAX_CUMULATIVE_STRING_CODE_POINTS,
    MAX_INDIVIDUAL_STRING_CODE_POINTS,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODE_COUNT,
    MAX_JSON_NUMBER_TOKEN_CODE_POINTS,
    MAX_OBJECT_MEMBER_COUNT,
    MAX_PARSED_VALUE_CANONICAL_BYTES,
    MAX_RAW_STATEMENT_BYTES,
    MIN_RAW_STATEMENT_BYTES,
    NOT_ACTIVATION_AUTHORIZATION,
    NOT_APPROVAL_AUTHORIZATION,
    NOT_AUTHENTICATION,
    NOT_TRADE_AUTHORIZATION,
    ORDER_COMPILATION_EVALUATED,
    PARSED_VALUE_IDENTITY_DOMAIN,
    PARSE_RESULT_BOOLEAN_COERCION_ERROR,
    REPLAY_EVALUATION_PERFORMED,
    STATEMENT_CONTRACT_VALIDATION_PERFORMED,
    STATEMENT_SEMANTIC_IDENTITY_COMPUTED,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementParseDiagnostic,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementParseState,
    TRADE_PERMISSION_EFFECT,
    WORKFLOW_PERMISSION_EVALUATED,
    parse_step2_market_source_policy_operator_approval_intent_statement_bytes,
)


Diagnostic = Step2MarketSourcePolicyOperatorApprovalIntentStatementParseDiagnostic
Result = Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult
State = Step2MarketSourcePolicyOperatorApprovalIntentStatementParseState


def _parse(value: object) -> Result:
    return parse_step2_market_source_policy_operator_approval_intent_statement_bytes(
        value
    )


def _thaw(value: Any) -> Any:
    if type(value) is FrozenApprovalIntentJsonObject:
        return {key: _thaw(child) for key, child in value.items}
    if type(value) is FrozenApprovalIntentJsonArray:
        return [_thaw(child) for child in value.items]
    return value


def _parsed_identity_oracle(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(PARSED_VALUE_IDENTITY_DOMAIN + canonical).hexdigest()


def _assert_authority_markers(result: Result) -> None:
    assert (
        result.result_version
        == APPROVAL_INTENT_STATEMENT_RESULT_REVISION
    )
    assert result.parser_version == APPROVAL_INTENT_STATEMENT_PARSER_REVISION
    assert result.authority_scope == AUTHORITY_SCOPE == "raw_json_parsing_only"
    assert result.not_authentication is NOT_AUTHENTICATION is True
    assert (
        result.not_approval_authorization
        is NOT_APPROVAL_AUTHORIZATION
        is True
    )
    assert (
        result.not_activation_authorization
        is NOT_ACTIVATION_AUTHORIZATION
        is True
    )
    assert result.not_trade_authorization is NOT_TRADE_AUTHORIZATION is True
    assert result.trade_permission_effect == TRADE_PERMISSION_EFFECT == "none"
    assert (
        result.statement_contract_validation_performed
        is STATEMENT_CONTRACT_VALIDATION_PERFORMED
        is False
    )
    assert (
        result.statement_semantic_identity_computed
        is STATEMENT_SEMANTIC_IDENTITY_COMPUTED
        is False
    )
    assert (
        result.authentication_evaluation_performed
        is AUTHENTICATION_EVALUATION_PERFORMED
        is False
    )
    assert result.intent_evaluation_performed is INTENT_EVALUATION_PERFORMED is False
    assert (
        result.freshness_evaluation_performed
        is FRESHNESS_EVALUATION_PERFORMED
        is False
    )
    assert result.replay_evaluation_performed is REPLAY_EVALUATION_PERFORMED is False
    assert (
        result.lifecycle_evaluation_performed
        is LIFECYCLE_EVALUATION_PERFORMED
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


def _assert_no_truth_value(result: Result) -> None:
    with pytest.raises(TypeError) as exc_info:
        bool(result)
    assert str(exc_info.value) == PARSE_RESULT_BOOLEAN_COERCION_ERROR


def _assert_stage_matrix(
    result: Result,
    *,
    decoding: tuple[bool, bool | None],
    syntax: tuple[bool, bool | None],
    duplicate: tuple[bool, bool | None],
    unicode: tuple[bool, bool | None],
    bounds: tuple[bool, bool | None],
    parse_valid: bool | None,
) -> None:
    assert (
        result.text_decoding_performed,
        result.text_decoding_valid,
    ) == decoding
    assert (
        result.json_syntax_validation_performed,
        result.json_syntax_valid,
    ) == syntax
    assert (
        result.duplicate_key_validation_performed,
        result.duplicate_keys_valid,
    ) == duplicate
    assert (
        result.unicode_scalar_validation_performed,
        result.unicode_scalars_valid,
    ) == unicode
    assert (
        result.structural_bound_validation_performed,
        result.structural_bounds_valid,
    ) == bounds
    assert result.parse_valid is parse_valid


def _assert_invalid(
    raw: object,
    state: State,
    diagnostic: Diagnostic,
    *,
    size: int | None,
    raw_hash: str | None,
    decoding: tuple[bool, bool | None],
    syntax: tuple[bool, bool | None],
    duplicate: tuple[bool, bool | None],
    unicode: tuple[bool, bool | None],
    bounds: tuple[bool, bool | None],
    parse_valid: bool | None,
) -> Result:
    result = _parse(raw)
    assert type(result) is Result
    assert result.parse_state is state
    assert result.raw_statement_size_bytes == size
    assert result.raw_statement_sha256 == raw_hash
    assert result.parsed_value_identity_sha256 is None
    _assert_stage_matrix(
        result,
        decoding=decoding,
        syntax=syntax,
        duplicate=duplicate,
        unicode=unicode,
        bounds=bounds,
        parse_valid=parse_valid,
    )
    assert result.parsed_value_available is False
    assert result.immutable_parsed_value is None
    assert result.diagnostics == (diagnostic,)
    _assert_authority_markers(result)
    _assert_no_truth_value(result)
    return result


def _assert_valid(raw: bytes, expected: Any) -> Result:
    result = _parse(raw)
    assert type(result) is Result
    assert result.parse_state is State.VALID
    assert result.raw_statement_size_bytes == len(raw)
    assert result.raw_statement_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.parsed_value_identity_sha256 == _parsed_identity_oracle(expected)
    _assert_stage_matrix(
        result,
        decoding=(True, True),
        syntax=(True, True),
        duplicate=(True, True),
        unicode=(True, True),
        bounds=(True, True),
        parse_valid=True,
    )
    assert result.parsed_value_available is True
    assert _thaw(result.immutable_parsed_value) == expected
    assert result.diagnostics == ()
    _assert_authority_markers(result)
    _assert_no_truth_value(result)
    return result


def _nested_arrays(depth: int) -> bytes:
    return b"[" * depth + b"null" + b"]" * depth


def _node_fixture(null_count: int) -> bytes:
    groups: list[bytes] = []
    remaining = null_count
    while remaining:
        group_size = min(MAX_ARRAY_ITEM_COUNT, remaining)
        groups.append(b"[" + b",".join([b"null"] * group_size) + b"]")
        remaining -= group_size
    return b"[" + b",".join(groups) + b"]"


def _object(count: int) -> bytes:
    return b"{" + b",".join(
        f'"k{index}":null'.encode("ascii") for index in range(count)
    ) + b"}"


def _array(count: int) -> bytes:
    return b"[" + b",".join([b"null"] * count) + b"]"


def _syntax_node_count(value: object) -> int:
    count = 0
    stack = [value]
    while stack:
        current = stack.pop()
        count += 1
        if type(current) is parser_contract._SyntaxObject:
            stack.extend(child for _, child in current.items)
        elif type(current) is parser_contract._SyntaxArray:
            stack.extend(current.items)
    return count


def _syntax_max_depth(value: object) -> int:
    maximum = 0
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        maximum = max(maximum, depth)
        if type(current) is parser_contract._SyntaxObject:
            stack.extend((child, depth + 1) for _, child in current.items)
        elif type(current) is parser_contract._SyntaxArray:
            stack.extend((child, depth + 1) for child in current.items)
    return maximum


def test_public_signature_limits_versions_and_enum_orders_are_exact() -> None:
    signature = inspect.signature(
        parse_step2_market_source_policy_operator_approval_intent_statement_bytes
    )
    assert tuple(signature.parameters) == ("value",)
    assert signature.parameters["value"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert get_type_hints(
        parse_step2_market_source_policy_operator_approval_intent_statement_bytes
    )["return"] is Result
    assert MIN_RAW_STATEMENT_BYTES == 1
    assert MAX_RAW_STATEMENT_BYTES == 4_096
    assert MAX_JSON_NESTING_DEPTH == 8
    assert MAX_JSON_NODE_COUNT == 256
    assert MAX_CUMULATIVE_STRING_CODE_POINTS == 2_048
    assert MAX_INDIVIDUAL_STRING_CODE_POINTS == 1_024
    assert MAX_OBJECT_MEMBER_COUNT == 32
    assert MAX_ARRAY_ITEM_COUNT == 64
    assert MAX_JSON_NUMBER_TOKEN_CODE_POINTS == 256
    assert MAX_PARSED_VALUE_CANONICAL_BYTES == 32_768
    assert MAX_APPROVAL_INTENT_STATEMENT_PARSE_DIAGNOSTICS == 1
    assert [state.name for state in State] == [
        "INPUT_ABSENT",
        "INPUT_TYPE_INVALID",
        "RAW_SIZE_INVALID",
        "ENCODING_INVALID",
        "JSON_GRAMMAR_INVALID",
        "DUPLICATE_KEY_INVALID",
        "UNICODE_SCALAR_INVALID",
        "RESOURCE_LIMIT_INVALID",
        "VALID",
    ]
    assert [diagnostic.name for diagnostic in Diagnostic] == [
        "STATEMENT_INPUT_MISSING",
        "STATEMENT_INPUT_TYPE_INVALID",
        "STATEMENT_RAW_SIZE_INVALID",
        "STATEMENT_UTF8_BOM_UNSUPPORTED",
        "STATEMENT_UTF8_INVALID",
        "STATEMENT_JSON_INVALID",
        "STATEMENT_TRAILING_CONTENT",
        "STATEMENT_DUPLICATE_KEY",
        "STATEMENT_SURROGATE_INVALID",
        "STATEMENT_DEPTH_LIMIT_EXCEEDED",
        "STATEMENT_NODE_LIMIT_EXCEEDED",
        "STATEMENT_CUMULATIVE_STRING_LIMIT_EXCEEDED",
        "STATEMENT_STRING_LIMIT_EXCEEDED",
        "STATEMENT_OBJECT_MEMBER_LIMIT_EXCEEDED",
        "STATEMENT_ARRAY_ITEM_LIMIT_EXCEEDED",
        "STATEMENT_NUMBER_LIMIT_EXCEEDED",
    ]
    assert [diagnostic.value for diagnostic in Diagnostic] == [
        name.lower() for name in [diagnostic.name for diagnostic in Diagnostic]
    ]


def test_absent_and_invalid_inputs_are_unread_and_unretained() -> None:
    absent = _assert_invalid(
        None,
        State.INPUT_ABSENT,
        Diagnostic.STATEMENT_INPUT_MISSING,
        size=None,
        raw_hash=None,
        decoding=(False, None),
        syntax=(False, None),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=None,
    )
    assert absent.immutable_parsed_value is None

    class Unread:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(name)

        def __iter__(self) -> object:
            raise AssertionError

        def __repr__(self) -> str:
            raise AssertionError

        def __bool__(self) -> bool:
            raise AssertionError

    invalid = _assert_invalid(
        Unread(),
        State.INPUT_TYPE_INVALID,
        Diagnostic.STATEMENT_INPUT_TYPE_INVALID,
        size=None,
        raw_hash=None,
        decoding=(False, None),
        syntax=(False, None),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=None,
    )
    assert invalid.raw_statement_sha256 is None


@pytest.mark.parametrize(
    "value",
    ["null", bytearray(b"null"), memoryview(b"null"), [110, 117, 108, 108]],
)
def test_only_exact_bytes_are_accepted(value: object) -> None:
    _assert_invalid(
        value,
        State.INPUT_TYPE_INVALID,
        Diagnostic.STATEMENT_INPUT_TYPE_INVALID,
        size=None,
        raw_hash=None,
        decoding=(False, None),
        syntax=(False, None),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=None,
    )


def test_bytes_subclass_and_path_like_are_rejected_without_coercion() -> None:
    class BytesSubclass(bytes):
        pass

    for value in (BytesSubclass(b"null"), Path("statement.json")):
        _assert_invalid(
            value,
            State.INPUT_TYPE_INVALID,
            Diagnostic.STATEMENT_INPUT_TYPE_INVALID,
            size=None,
            raw_hash=None,
            decoding=(False, None),
            syntax=(False, None),
            duplicate=(False, None),
            unicode=(False, None),
            bounds=(False, None),
            parse_valid=None,
        )


@pytest.mark.parametrize("raw", [b"", b"x" * (MAX_RAW_STATEMENT_BYTES + 1)])
def test_raw_size_invalid_has_size_but_no_hash_or_later_stage(raw: bytes) -> None:
    _assert_invalid(
        raw,
        State.RAW_SIZE_INVALID,
        Diagnostic.STATEMENT_RAW_SIZE_INVALID,
        size=len(raw),
        raw_hash=None,
        decoding=(False, None),
        syntax=(False, None),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=None,
    )


def test_exact_minimum_and_maximum_raw_sizes_are_processed_without_truncation() -> None:
    _assert_valid(b"0", 0)
    raw = b" " * (MAX_RAW_STATEMENT_BYTES - 1) + b"0"
    result = _assert_valid(raw, 0)
    assert result.raw_statement_size_bytes == MAX_RAW_STATEMENT_BYTES


@pytest.mark.parametrize(
    ("raw", "diagnostic"),
    [
        (b"\xef\xbb\xbfnull", Diagnostic.STATEMENT_UTF8_BOM_UNSUPPORTED),
        (b'"\xc3"', Diagnostic.STATEMENT_UTF8_INVALID),
        (b"\xf0\x9f\x92", Diagnostic.STATEMENT_UTF8_INVALID),
    ],
)
def test_encoding_failures_have_raw_identity_but_no_json_parse(
    raw: bytes,
    diagnostic: Diagnostic,
) -> None:
    _assert_invalid(
        raw,
        State.ENCODING_INVALID,
        diagnostic,
        size=len(raw),
        raw_hash=hashlib.sha256(raw).hexdigest(),
        decoding=(True, False),
        syntax=(False, None),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=False,
    )


def test_utf8_non_ascii_nul_and_bom_inside_a_string_are_parser_valid() -> None:
    _assert_valid('"é"'.encode("utf-8"), "é")
    _assert_valid(b'"\\u0000"', "\x00")
    _assert_valid('"\ufeff"'.encode("utf-8"), "\ufeff")
    raw_nul = b'"\x00"'
    _assert_invalid(
        raw_nul,
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.STATEMENT_JSON_INVALID,
        size=len(raw_nul),
        raw_hash=hashlib.sha256(raw_nul).hexdigest(),
        decoding=(True, True),
        syntax=(True, False),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=False,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"null", None),
        (b"true", True),
        (b"false", False),
        (b"-0", 0),
        (b"1.5", 1.5),
        (b'"text"', "text"),
        (b"[]", []),
        (b"{}", {}),
        (b" \t\r\n [1, false, null] \n", [1, False, None]),
    ],
)
def test_all_json_primitive_and_container_roots_are_parser_valid(
    raw: bytes,
    expected: Any,
) -> None:
    _assert_valid(raw, expected)


@pytest.mark.parametrize(
    "raw",
    [
        b"// comment\nnull",
        b"[1,]",
        b'{"a":1,}',
        b"NaN",
        b"Infinity",
        b"-Infinity",
        b"01",
        b"1.",
        b"1e",
        b'"\\x"',
        b'"unterminated',
    ],
)
def test_general_json_grammar_failures_are_classified_deterministically(raw: bytes) -> None:
    _assert_invalid(
        raw,
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.STATEMENT_JSON_INVALID,
        size=len(raw),
        raw_hash=hashlib.sha256(raw).hexdigest(),
        decoding=(True, True),
        syntax=(True, False),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=False,
    )


def test_trailing_non_whitespace_has_its_own_diagnostic() -> None:
    raw = b"null x"
    _assert_invalid(
        raw,
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.STATEMENT_TRAILING_CONTENT,
        size=len(raw),
        raw_hash=hashlib.sha256(raw).hexdigest(),
        decoding=(True, True),
        syntax=(True, False),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=False,
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"truex",
        b"falsex",
        b"nullx",
        b"1x",
        b"-1x",
        b"1.0x",
        b"1e0x",
        b'"x"x',
        b"[]x",
        b"{}x",
    ],
)
def test_complete_root_values_followed_by_content_are_trailing_content(
    raw: bytes,
) -> None:
    _assert_invalid(
        raw,
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.STATEMENT_TRAILING_CONTENT,
        size=len(raw),
        raw_hash=hashlib.sha256(raw).hexdigest(),
        decoding=(True, True),
        syntax=(True, False),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=False,
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"tru",
        b"fals",
        b"nul",
        b"-",
        b"01",
        b"-01",
        b"1.",
        b"1e",
        b"1e+",
        b"1e-",
        b".1",
        b"+",
        b"NaN",
        b"Infinity",
        b"-Infinity",
    ],
)
def test_malformed_root_tokens_remain_general_json_grammar_failures(
    raw: bytes,
) -> None:
    _assert_invalid(
        raw,
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.STATEMENT_JSON_INVALID,
        size=len(raw),
        raw_hash=hashlib.sha256(raw).hexdigest(),
        decoding=(True, True),
        syntax=(True, False),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=False,
    )


@pytest.mark.parametrize("token", [b"truex", b"nullx", b"1x"])
def test_primitive_extra_content_is_trailing_only_at_the_root(token: bytes) -> None:
    root = _parse(token)
    assert root.diagnostics == (Diagnostic.STATEMENT_TRAILING_CONTENT,)
    for raw in (b"[" + token + b"]", b'{"a":' + token + b"}"):
        nested = _parse(raw)
        assert nested.parse_state is State.JSON_GRAMMAR_INVALID
        assert nested.diagnostics == (Diagnostic.STATEMENT_JSON_INVALID,)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":1,"\\u0061":2}',
        b'{"outer":{"x":1,"x":2}}',
        b'{"' + "😀".encode("utf-8") + b'":1,"\\ud83d\\ude00":2}',
    ],
)
def test_duplicate_decoded_keys_at_every_depth_are_rejected(raw: bytes) -> None:
    _assert_invalid(
        raw,
        State.DUPLICATE_KEY_INVALID,
        Diagnostic.STATEMENT_DUPLICATE_KEY,
        size=len(raw),
        raw_hash=hashlib.sha256(raw).hexdigest(),
        decoding=(True, True),
        syntax=(True, True),
        duplicate=(True, False),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=False,
    )


def test_equal_keys_in_distinct_sibling_objects_are_valid() -> None:
    _assert_valid(b'[{"a":1},{"a":2}]', [{"a": 1}, {"a": 2}])


def test_grammar_failure_precedes_duplicate_key_detection() -> None:
    raw = b'{"a":1,"a":}'
    _assert_invalid(
        raw,
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.STATEMENT_JSON_INVALID,
        size=len(raw),
        raw_hash=hashlib.sha256(raw).hexdigest(),
        decoding=(True, True),
        syntax=(True, False),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=False,
    )


@pytest.mark.parametrize(
    "raw",
    [b'"\\ud800"', b'"\\udc00"', b'"\\udc00\\ud800"', b'"\\ud800x"'],
)
def test_invalid_unicode_surrogate_sequences_are_a_later_distinct_failure(
    raw: bytes,
) -> None:
    _assert_invalid(
        raw,
        State.UNICODE_SCALAR_INVALID,
        Diagnostic.STATEMENT_SURROGATE_INVALID,
        size=len(raw),
        raw_hash=hashlib.sha256(raw).hexdigest(),
        decoding=(True, True),
        syntax=(True, True),
        duplicate=(True, True),
        unicode=(True, False),
        bounds=(False, None),
        parse_valid=False,
    )


def test_valid_surrogates_and_direct_supplementary_unicode_normalize_equally() -> None:
    escaped = _assert_valid(b'"\\ud83d\\ude00"', "😀")
    direct = _assert_valid('"😀"'.encode("utf-8"), "😀")
    assert escaped.parsed_value_identity_sha256 == direct.parsed_value_identity_sha256
    assert escaped.raw_statement_sha256 != direct.raw_statement_sha256


def test_json_number_conversion_type_and_finite_rules() -> None:
    integer = _assert_valid(b"1", 1)
    negative_zero = _assert_valid(b"-0", 0)
    decimal = _assert_valid(b"1.0", 1.0)
    exponent = _assert_valid(b"1e0", 1.0)
    negative_float_zero = _assert_valid(b"-0.0", -0.0)
    assert type(integer.immutable_parsed_value) is int
    assert type(negative_zero.immutable_parsed_value) is int
    assert type(decimal.immutable_parsed_value) is float
    assert type(negative_float_zero.immutable_parsed_value) is float
    assert math.copysign(1.0, negative_float_zero.immutable_parsed_value) == -1.0
    assert integer.parsed_value_identity_sha256 != decimal.parsed_value_identity_sha256
    assert decimal.parsed_value_identity_sha256 == exponent.parsed_value_identity_sha256
    overflow = b"1e999"
    _assert_invalid(
        overflow,
        State.RESOURCE_LIMIT_INVALID,
        Diagnostic.STATEMENT_NUMBER_LIMIT_EXCEEDED,
        size=len(overflow),
        raw_hash=hashlib.sha256(overflow).hexdigest(),
        decoding=(True, True),
        syntax=(True, True),
        duplicate=(True, True),
        unicode=(True, True),
        bounds=(True, False),
        parse_valid=False,
    )


def test_number_token_limit_has_exact_boundary() -> None:
    _assert_valid(b"1" * MAX_JSON_NUMBER_TOKEN_CODE_POINTS, int("1" * 256))
    raw = b"1" * (MAX_JSON_NUMBER_TOKEN_CODE_POINTS + 1)
    _assert_invalid(
        raw,
        State.RESOURCE_LIMIT_INVALID,
        Diagnostic.STATEMENT_NUMBER_LIMIT_EXCEEDED,
        size=len(raw),
        raw_hash=hashlib.sha256(raw).hexdigest(),
        decoding=(True, True),
        syntax=(True, True),
        duplicate=(True, True),
        unicode=(True, True),
        bounds=(True, False),
        parse_valid=False,
    )


def test_depth_node_member_array_and_string_resource_limits_are_exact() -> None:
    _assert_valid(_nested_arrays(MAX_JSON_NESTING_DEPTH), [[[[[[[[None]]]]]]]])
    too_deep = _nested_arrays(MAX_JSON_NESTING_DEPTH + 1)
    _assert_resource_failure(too_deep, Diagnostic.STATEMENT_DEPTH_LIMIT_EXCEEDED)

    exact_nodes = _node_fixture(251)
    assert _parse(exact_nodes).parse_state is State.VALID
    _assert_resource_failure(
        _node_fixture(252), Diagnostic.STATEMENT_NODE_LIMIT_EXCEEDED
    )

    assert _parse(_object(MAX_OBJECT_MEMBER_COUNT)).parse_state is State.VALID
    _assert_resource_failure(
        _object(MAX_OBJECT_MEMBER_COUNT + 1),
        Diagnostic.STATEMENT_OBJECT_MEMBER_LIMIT_EXCEEDED,
    )
    assert _parse(_array(MAX_ARRAY_ITEM_COUNT)).parse_state is State.VALID
    _assert_resource_failure(
        _array(MAX_ARRAY_ITEM_COUNT + 1),
        Diagnostic.STATEMENT_ARRAY_ITEM_LIMIT_EXCEEDED,
    )

    assert _parse(b'"' + b"a" * MAX_INDIVIDUAL_STRING_CODE_POINTS + b'"').parse_state is State.VALID
    _assert_resource_failure(
        b'"' + b"a" * (MAX_INDIVIDUAL_STRING_CODE_POINTS + 1) + b'"',
        Diagnostic.STATEMENT_STRING_LIMIT_EXCEEDED,
    )
    cumulative_valid = b"[\"" + b"a" * 1024 + b'","' + b"b" * 1024 + b'\"]'
    assert _parse(cumulative_valid).parse_state is State.VALID
    cumulative_invalid = (
        b"[\""
        + b"a" * 1024
        + b'","'
        + b"b" * 1024
        + b'","'
        + b"c"
        + b'\"]'
    )
    _assert_resource_failure(
        cumulative_invalid,
        Diagnostic.STATEMENT_CUMULATIVE_STRING_LIMIT_EXCEEDED,
    )


def test_resource_controls_discard_excess_syntax_before_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, int]] = []
    original = parser_contract._validate_no_duplicate_keys

    def inspect_retained(value: object) -> None:
        if type(value) is parser_contract._SyntaxArray:
            observed.append(("array", len(value.items)))
        if type(value) is parser_contract._SyntaxObject:
            observed.append(("object", len(value.items)))
        original(value)

    monkeypatch.setattr(
        parser_contract,
        "_validate_no_duplicate_keys",
        inspect_retained,
    )
    _assert_resource_failure(
        _array(MAX_ARRAY_ITEM_COUNT + 1),
        Diagnostic.STATEMENT_ARRAY_ITEM_LIMIT_EXCEEDED,
    )
    _assert_resource_failure(
        _object(MAX_OBJECT_MEMBER_COUNT + 1),
        Diagnostic.STATEMENT_OBJECT_MEMBER_LIMIT_EXCEEDED,
    )
    assert ("array", MAX_ARRAY_ITEM_COUNT) in observed
    assert ("object", MAX_OBJECT_MEMBER_COUNT) in observed
    assert all(
        count <= (
            MAX_ARRAY_ITEM_COUNT if kind == "array" else MAX_OBJECT_MEMBER_COUNT
        )
        for kind, count in observed
    )


def test_string_number_depth_and_node_controls_never_expose_overlimit_syntax() -> None:
    string_control = parser_contract._ParseControl()
    parsed_string = parser_contract._parse_json_string(
        '"' + ("a" * (MAX_INDIVIDUAL_STRING_CODE_POINTS + 1)) + '"',
        0,
        string_control,
        retain=True,
    )
    assert parsed_string.value is None
    assert (
        string_control.first_resource_diagnostic
        is Diagnostic.STATEMENT_STRING_LIMIT_EXCEEDED
    )

    key_control = parser_contract._ParseControl()
    parsed_key = parser_contract._parse_json_string(
        '"' + ("k" * (MAX_INDIVIDUAL_STRING_CODE_POINTS + 1)) + '"',
        0,
        key_control,
        retain=True,
    )
    assert parsed_key.value is None
    assert (
        key_control.first_resource_diagnostic
        is Diagnostic.STATEMENT_STRING_LIMIT_EXCEEDED
    )

    cumulative_control = parser_contract._ParseControl()
    parser_contract._parse_json_string(
        '"' + ("a" * MAX_INDIVIDUAL_STRING_CODE_POINTS) + '"',
        0,
        cumulative_control,
        retain=True,
    )
    cumulative_string = parser_contract._parse_json_string(
        '"' + ("b" * MAX_INDIVIDUAL_STRING_CODE_POINTS) + '"',
        0,
        cumulative_control,
        retain=True,
    )
    overflow_string = parser_contract._parse_json_string(
        '"c"',
        0,
        cumulative_control,
        retain=True,
    )
    assert cumulative_string.value == "b" * MAX_INDIVIDUAL_STRING_CODE_POINTS
    assert overflow_string.value is None
    assert (
        cumulative_control.first_resource_diagnostic
        is Diagnostic.STATEMENT_CUMULATIVE_STRING_LIMIT_EXCEEDED
    )

    number_outcome = parser_contract._IterativeJsonParser(
        "1" * (MAX_JSON_NUMBER_TOKEN_CODE_POINTS + 1)
    ).parse()
    assert number_outcome.syntax_value_available is False
    assert (
        number_outcome.resource_diagnostic
        is Diagnostic.STATEMENT_NUMBER_LIMIT_EXCEEDED
    )

    node_outcome = parser_contract._IterativeJsonParser(
        _node_fixture(252).decode("ascii")
    ).parse()
    assert node_outcome.syntax_value_available is True
    assert node_outcome.syntax_value is not None
    assert _syntax_node_count(node_outcome.syntax_value) <= MAX_JSON_NODE_COUNT
    assert (
        node_outcome.resource_diagnostic
        is Diagnostic.STATEMENT_NODE_LIMIT_EXCEEDED
    )

    depth_outcome = parser_contract._IterativeJsonParser(
        _nested_arrays(MAX_JSON_NESTING_DEPTH + 1).decode("ascii")
    ).parse()
    assert depth_outcome.syntax_value_available is True
    assert depth_outcome.syntax_value is not None
    assert _syntax_node_count(depth_outcome.syntax_value) <= MAX_JSON_NODE_COUNT
    assert _syntax_max_depth(depth_outcome.syntax_value) <= MAX_JSON_NESTING_DEPTH
    assert (
        depth_outcome.resource_diagnostic
        is Diagnostic.STATEMENT_DEPTH_LIMIT_EXCEEDED
    )


def test_resource_discard_preserves_grammar_duplicate_and_unicode_precedence() -> None:
    array_overflow = _array(MAX_ARRAY_ITEM_COUNT + 1)
    _assert_resource_failure(
        array_overflow,
        Diagnostic.STATEMENT_ARRAY_ITEM_LIMIT_EXCEEDED,
    )
    _assert_invalid(
        array_overflow[:-1] + b",]",
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.STATEMENT_JSON_INVALID,
        size=len(array_overflow) + 1,
        raw_hash=hashlib.sha256(array_overflow[:-1] + b",]").hexdigest(),
        decoding=(True, True),
        syntax=(True, False),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=False,
    )

    object_overflow_then_grammar = (
        _object(MAX_OBJECT_MEMBER_COUNT + 1)[:-1] + b',"later":}'
    )
    _assert_invalid(
        object_overflow_then_grammar,
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.STATEMENT_JSON_INVALID,
        size=len(object_overflow_then_grammar),
        raw_hash=hashlib.sha256(object_overflow_then_grammar).hexdigest(),
        decoding=(True, True),
        syntax=(True, False),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=False,
    )

    unique_members = [f'"k{index}":null'.encode("ascii") for index in range(32)]
    duplicate_overflow = b"{" + b",".join(unique_members + [b'"k0":null']) + b"}"
    escaped_duplicate_overflow = (
        b"{" + b",".join([b'"a":null'] + unique_members[1:] + [b'"\\u0061":null']) + b"}"
    )
    for raw in (duplicate_overflow, escaped_duplicate_overflow):
        _assert_invalid(
            raw,
            State.DUPLICATE_KEY_INVALID,
            Diagnostic.STATEMENT_DUPLICATE_KEY,
            size=len(raw),
            raw_hash=hashlib.sha256(raw).hexdigest(),
            decoding=(True, True),
            syntax=(True, True),
            duplicate=(True, False),
            unicode=(False, None),
            bounds=(False, None),
            parse_valid=False,
        )

    duplicate_then_grammar = b'{"a":null,"a":}'
    _assert_invalid(
        duplicate_then_grammar,
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.STATEMENT_JSON_INVALID,
        size=len(duplicate_then_grammar),
        raw_hash=hashlib.sha256(duplicate_then_grammar).hexdigest(),
        decoding=(True, True),
        syntax=(True, False),
        duplicate=(False, None),
        unicode=(False, None),
        bounds=(False, None),
        parse_valid=False,
    )

    unicode_overflow = (
        b"[" + b",".join([b"null"] * MAX_ARRAY_ITEM_COUNT + [b'"\\ud800"']) + b"]"
    )
    _assert_invalid(
        unicode_overflow,
        State.UNICODE_SCALAR_INVALID,
        Diagnostic.STATEMENT_SURROGATE_INVALID,
        size=len(unicode_overflow),
        raw_hash=hashlib.sha256(unicode_overflow).hexdigest(),
        decoding=(True, True),
        syntax=(True, True),
        duplicate=(True, True),
        unicode=(True, False),
        bounds=(False, None),
        parse_valid=False,
    )

    overlong_string_with_bad_escape = (
        b'"' + (b"a" * (MAX_INDIVIDUAL_STRING_CODE_POINTS + 1)) + b"\\x\""
    )
    overlong_number_with_incomplete_exponent = (
        (b"1" * MAX_JSON_NUMBER_TOKEN_CODE_POINTS) + b"e"
    )
    for raw in (
        overlong_string_with_bad_escape,
        overlong_number_with_incomplete_exponent,
    ):
        _assert_invalid(
            raw,
            State.JSON_GRAMMAR_INVALID,
            Diagnostic.STATEMENT_JSON_INVALID,
            size=len(raw),
            raw_hash=hashlib.sha256(raw).hexdigest(),
            decoding=(True, True),
            syntax=(True, False),
            duplicate=(False, None),
            unicode=(False, None),
            bounds=(False, None),
            parse_valid=False,
        )


def test_first_resource_violation_follows_left_to_right_parser_order() -> None:
    raw = (
        b"["
        + b",".join([b"null"] * MAX_ARRAY_ITEM_COUNT)
        + b',"'
        + (b"a" * (MAX_INDIVIDUAL_STRING_CODE_POINTS + 1))
        + b'"]'
    )
    _assert_resource_failure(
        raw,
        Diagnostic.STATEMENT_ARRAY_ITEM_LIMIT_EXCEEDED,
    )


def _assert_resource_failure(raw: bytes, diagnostic: Diagnostic) -> None:
    _assert_invalid(
        raw,
        State.RESOURCE_LIMIT_INVALID,
        diagnostic,
        size=len(raw),
        raw_hash=hashlib.sha256(raw).hexdigest(),
        decoding=(True, True),
        syntax=(True, True),
        duplicate=(True, True),
        unicode=(True, True),
        bounds=(True, False),
        parse_valid=False,
    )


def test_immutable_tree_is_frozen_ordered_and_exactly_typed() -> None:
    result = _assert_valid(
        b'{"z":[true,1,1.0,null,"x"],"a":false}',
        {"z": [True, 1, 1.0, None, "x"], "a": False},
    )
    root = result.immutable_parsed_value
    assert type(root) is FrozenApprovalIntentJsonObject
    assert tuple(key for key, _ in root.items) == ("z", "a")
    array = root.items[0][1]
    assert type(array) is FrozenApprovalIntentJsonArray
    assert tuple(type(item) for item in array.items) == (bool, int, float, type(None), str)
    with pytest.raises(TypeError) as exc_info:
        bool(root)
    assert str(exc_info.value) == FROZEN_JSON_BOOLEAN_COERCION_ERROR
    with pytest.raises(FrozenInstanceError):
        root.items = ()  # type: ignore[misc]
    with pytest.raises(ValueError):
        FrozenApprovalIntentJsonObject((("a", 1), ("a", 2)))


def test_genuine_result_owned_containers_block_state_restoration_without_mutation() -> None:
    result = _assert_valid(b'{"a":[1]}', {"a": [1]})
    root = result.immutable_parsed_value
    assert type(root) is FrozenApprovalIntentJsonObject
    nested = root.items[0][1]
    assert type(nested) is FrozenApprovalIntentJsonArray
    construction_error = (
        "operator approval-intent JSON containers do not support state restoration"
    )

    class UnreadState:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(name)

    for container, forged_state in (
        (root, ((("forged", 2),),)),
        (nested, ((2,),)),
    ):
        before_container = tuple(
            getattr(container, field.name) for field in fields(type(container))
        )
        before_result = tuple(getattr(result, field.name) for field in fields(Result))
        before_tree = json.dumps(
            _thaw(result.immutable_parsed_value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        before_identity = result.parsed_value_identity_sha256
        before_root_identity = id(root)
        before_nested_identity = id(nested)

        assert "__setstate__" in type(container).__dict__
        assert (
            type(container).__dict__["__setstate__"]
            is not dataclasses._dataclass_setstate
        )
        for state in (UnreadState(), forged_state):
            with pytest.raises(TypeError) as exc_info:
                container.__setstate__(state)
            assert str(exc_info.value) == construction_error

        assert tuple(
            getattr(container, field.name) for field in fields(type(container))
        ) == before_container
        assert tuple(getattr(result, field.name) for field in fields(Result)) == before_result
        assert json.dumps(
            _thaw(result.immutable_parsed_value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ) == before_tree
        assert result.parsed_value_identity_sha256 == before_identity
        assert result.parsed_value_identity_sha256 == _parsed_identity_oracle(
            _thaw(result.immutable_parsed_value)
        )
        assert id(root) == before_root_identity
        assert id(nested) == before_nested_identity
        assert result.parse_state is State.VALID
        assert result.parse_valid is True


def test_result_owned_container_copy_and_pickle_paths_fail_before_reconstruction() -> None:
    result = _assert_valid(b'{"a":[1]}', {"a": [1]})
    root = result.immutable_parsed_value
    assert type(root) is FrozenApprovalIntentJsonObject
    nested = root.items[0][1]
    assert type(nested) is FrozenApprovalIntentJsonArray
    construction_error = (
        "operator approval-intent JSON containers do not support state restoration"
    )
    before_tree = _thaw(root)
    before_identity = result.parsed_value_identity_sha256

    for container in (root, nested):
        for operation in (
            lambda container=container: copy.copy(container),
            lambda container=container: copy.deepcopy(container),
        ):
            with pytest.raises(TypeError) as exc_info:
                operation()
            assert str(exc_info.value) == construction_error
        for protocol in range(6):
            with pytest.raises(TypeError) as exc_info:
                pickle.loads(pickle.dumps(container, protocol=protocol))
            assert str(exc_info.value) == construction_error

    assert _thaw(root) == before_tree
    assert result.parsed_value_identity_sha256 == before_identity


def test_raw_and_parsed_identity_oracles_cover_spelling_order_and_domain() -> None:
    first = _assert_valid(b'{"a":1,"b":[2,3]}', {"a": 1, "b": [2, 3]})
    reordered = _assert_valid(b'{"b":[2,3],"a":1}', {"b": [2, 3], "a": 1})
    escaped = _assert_valid(b'"\\u0061"', "a")
    plain = _assert_valid(b'"a"', "a")
    arrays_a = _assert_valid(b"[1,2]", [1, 2])
    arrays_b = _assert_valid(b"[2,1]", [2, 1])
    assert first.raw_statement_sha256 != reordered.raw_statement_sha256
    assert first.parsed_value_identity_sha256 == reordered.parsed_value_identity_sha256
    assert escaped.raw_statement_sha256 != plain.raw_statement_sha256
    assert escaped.parsed_value_identity_sha256 == plain.parsed_value_identity_sha256
    assert arrays_a.parsed_value_identity_sha256 != arrays_b.parsed_value_identity_sha256
    canonical = b'{"a":1,"b":[2,3]}'
    assert first.parsed_value_identity_sha256 == hashlib.sha256(
        PARSED_VALUE_IDENTITY_DOMAIN + canonical
    ).hexdigest()
    assert first.parsed_value_identity_sha256 != hashlib.sha256(canonical).hexdigest()


def test_parsed_canonical_bound_is_checked_before_parsed_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    original_sha256 = parser_contract.hashlib.sha256

    def counting_sha256(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original_sha256(*args, **kwargs)

    monkeypatch.setattr(parser_contract, "MAX_PARSED_VALUE_CANONICAL_BYTES", 1)
    monkeypatch.setattr(parser_contract.hashlib, "sha256", counting_sha256)
    with pytest.raises(parser_contract._ParserInvariantError) as exc_info:
        _parse(b"null")
    assert str(exc_info.value) == (
        "Step 2 operator approval-intent statement parser invariant violated"
    )
    assert calls == 2


def test_result_is_sealed_frozen_slotted_and_has_one_private_allocation_site() -> None:
    result = _assert_valid(b"null", None)
    parameters = Result.__dataclass_params__
    assert parameters.frozen is True
    assert parameters.init is False
    assert tuple(Result.__slots__) == tuple(field.name for field in fields(Result))
    for name in ("__new__", "__setstate__", "__reduce__", "__reduce_ex__"):
        assert name in Result.__dict__
    construction_error = (
        "operator approval-intent statement parse results are created only by the "
        "public parser"
    )
    for operation in (
        lambda: Result(),
        lambda: Result(**{field.name: None for field in fields(Result)}),
        lambda: replace(result, parse_valid=False),
        lambda: result.__setstate__(()),
        lambda: result.__reduce__(),
        lambda: result.__reduce_ex__(0),
        lambda: copy.copy(result),
        lambda: copy.deepcopy(result),
        lambda: pickle.loads(pickle.dumps(result, protocol=5)),
    ):
        with pytest.raises(TypeError) as exc_info:
            operation()
        assert str(exc_info.value) == construction_error
    for protocol in range(6):
        with pytest.raises(TypeError) as exc_info:
            pickle.dumps(result, protocol=protocol)
        assert str(exc_info.value) == construction_error
    source = inspect.getsource(parser_contract)
    assert source.count("object.__new__(") == 1
    assert "def _create_result(" in source
    assert "raw_statement_sha256" not in inspect.signature(
        parser_contract._create_result
    ).parameters
    assert "parsed_value_identity_sha256" not in inspect.signature(
        parser_contract._create_result
    ).parameters


def test_result_reduction_protocol_is_unread_and_state_cannot_be_restored() -> None:
    result = _assert_valid(b'{"a":1}', {"a": 1})
    before = tuple(getattr(result, field.name) for field in fields(Result))

    class UnreadProtocol:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(name)

        def __int__(self) -> int:
            raise AssertionError

        def __bool__(self) -> bool:
            raise AssertionError

    with pytest.raises(TypeError):
        result.__reduce_ex__(UnreadProtocol())
    with pytest.raises(TypeError):
        result.__setstate__(("forged",))
    for protocol in range(6):
        with pytest.raises(TypeError):
            result.__reduce_ex__(protocol)
    state_getter = getattr(result, "__getstate__", None)
    if state_getter is not None:
        state = state_getter()
        with pytest.raises(TypeError):
            result.__setstate__(state)
        uninitialized = object.__new__(Result)
        with pytest.raises(TypeError):
            uninitialized.__setstate__(state)
        assert tuple(getattr(result, field.name) for field in fields(Result)) == before
    after = tuple(getattr(result, field.name) for field in fields(Result))
    assert after == before


def test_dependency_failures_propagate_without_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = b"null"

    def fail_sha256(*args: object, **kwargs: object) -> object:
        raise RuntimeError("sha failure")

    monkeypatch.setattr(parser_contract.hashlib, "sha256", fail_sha256)
    with pytest.raises(RuntimeError, match="sha failure"):
        _parse(raw)


def test_raw_hash_precedes_bom_and_parsed_hash_and_json_failures_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_raw_hash(*args: object, **kwargs: object) -> object:
        raise RuntimeError("raw hash failure")

    monkeypatch.setattr(parser_contract.hashlib, "sha256", fail_raw_hash)
    with pytest.raises(RuntimeError, match="raw hash failure"):
        _parse(b"\xef\xbb\xbfnull")


def test_parsed_hash_and_json_serialization_failures_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_sha256 = parser_contract.hashlib.sha256
    calls = 0

    def fail_second_hash(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("parsed hash failure")
        return original_sha256(*args, **kwargs)

    monkeypatch.setattr(parser_contract.hashlib, "sha256", fail_second_hash)
    with pytest.raises(RuntimeError, match="parsed hash failure"):
        _parse(b"null")

    monkeypatch.undo()

    def fail_json_dumps(*args: object, **kwargs: object) -> str:
        raise RuntimeError("json failure")

    monkeypatch.setattr(parser_contract.json, "dumps", fail_json_dumps)
    with pytest.raises(RuntimeError, match="json failure"):
        _parse(b"null")


def test_ast_proves_no_json_loads_recursion_or_broad_catches() -> None:
    source = inspect.getsource(parser_contract)
    tree = ast.parse(source)
    assert "json.loads" not in source
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.TryStar)]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            assert not (
                handler.type is None
                or (
                    isinstance(handler.type, ast.Name)
                    and handler.type.id in {"Exception", "BaseException"}
                )
            )
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]


def test_parser_has_no_production_consumers_or_external_capabilities() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    parser_path = Path(parser_contract.__file__).resolve()
    parser_relative = parser_path.relative_to(repository_root).as_posix()
    consumers: list[str] = []
    for path in (repository_root / "src").rglob("*.py"):
        if path.resolve() == parser_path:
            continue
        source = path.read_text(encoding="utf-8")
        if (
            "parse_step2_market_source_policy_operator_approval_intent_statement"
            in source
            or "FrozenApprovalIntentJson" in source
        ):
            consumers.append(path.relative_to(repository_root).as_posix())
    assert parser_relative == (
        "src/investment_orchestrator/parsers/"
        "parse_step2_market_source_policy_operator_approval_intent_statement.py"
    )
    assert consumers == []
    imports = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(ast.parse(parser_path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Import)
    }
    assert imports == {"hashlib", "json", "math"}
