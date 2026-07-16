from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
import hashlib
from io import BytesIO
import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.parsers import (
    parse_step2_market_source_policy_approvals as parser_contract,
)
from investment_orchestrator.parsers.parse_step2_market_source_policy_approvals import (
    ACTIVATION_EVALUATION_PERFORMED,
    AUTHORITY_SCOPE,
    FRESHNESS_EVALUATION_PERFORMED,
    FROZEN_JSON_BOOLEAN_COERCION_ERROR,
    FrozenJsonArray,
    FrozenJsonObject,
    MAX_ARRAY_ITEM_COUNT,
    MAX_DECODED_STRING_CODE_POINTS,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODE_COUNT,
    MAX_OBJECT_MEMBER_COUNT,
    MAX_PARSED_VALUE_CANONICAL_BYTES,
    MAX_RAW_APPROVAL_PARSE_DIAGNOSTICS,
    MAX_RAW_ARTIFACT_BYTES,
    NOT_TRADE_AUTHORIZATION,
    OBJECT_CONTRACT_VALIDATION_PERFORMED,
    OPERATOR_AUTHENTICATION_PERFORMED,
    ORDER_COMPILATION_EVALUATED,
    PARSER_VERSION,
    PARSE_RESULT_BOOLEAN_COERCION_ERROR,
    PARSE_RESULT_VERSION,
    PUBLICATION_EVALUATION_PERFORMED,
    SOURCE_RESOLUTION_PERFORMED,
    Step2MarketSourcePolicyApprovalParseDiagnostic,
    Step2MarketSourcePolicyApprovalParseResult,
    Step2MarketSourcePolicyApprovalParseState,
    TRADE_PERMISSION_EFFECT,
    WORKFLOW_PERMISSION_EVALUATED,
    parse_step2_market_source_policy_approvals_bytes,
)
from investment_orchestrator.validators.validate_step2_market_source_policy_approvals import (
    validate_step2_market_source_policy_approvals_object,
)


Diagnostic = Step2MarketSourcePolicyApprovalParseDiagnostic
State = Step2MarketSourcePolicyApprovalParseState
_PARSED_DOMAIN = b"step2_market_source_policy_approval_parsed_value_v1\0"
_APPROVAL_CONTENT_DOMAIN = b"step2_market_source_policy_approvals_v1\0"


def _parse(value: object) -> Step2MarketSourcePolicyApprovalParseResult:
    return parse_step2_market_source_policy_approvals_bytes(value)


def _assert_fixed_markers(
    result: Step2MarketSourcePolicyApprovalParseResult,
) -> None:
    assert result.result_version == PARSE_RESULT_VERSION
    assert result.parser_version == PARSER_VERSION
    assert result.authority_scope == AUTHORITY_SCOPE
    assert result.authority_scope == "strict_raw_artifact_parsing_only"
    assert result.not_trade_authorization is NOT_TRADE_AUTHORIZATION is True
    assert result.trade_permission_effect == TRADE_PERMISSION_EFFECT == "none"
    assert (
        result.operator_authentication_performed
        is OPERATOR_AUTHENTICATION_PERFORMED
        is False
    )
    assert (
        result.object_contract_validation_performed
        is OBJECT_CONTRACT_VALIDATION_PERFORMED
        is False
    )
    assert result.source_resolution_performed is SOURCE_RESOLUTION_PERFORMED is False
    assert (
        result.freshness_evaluation_performed
        is FRESHNESS_EVALUATION_PERFORMED
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


def _assert_bool_rejected(result: Step2MarketSourcePolicyApprovalParseResult) -> None:
    with pytest.raises(TypeError) as exc_info:
        bool(result)
    assert str(exc_info.value) == PARSE_RESULT_BOOLEAN_COERCION_ERROR


def _assert_failure(
    value: object,
    state: State,
    diagnostic: Diagnostic,
    *,
    expected_size: int | None,
    expected_hash: str | None,
    parsing_performed: bool,
    parse_valid: bool | None,
) -> Step2MarketSourcePolicyApprovalParseResult:
    result = _parse(value)
    assert result.parse_state is state
    assert result.raw_artifact_size_bytes == expected_size
    assert result.raw_artifact_sha256 == expected_hash
    assert result.parsed_value_identity_sha256 is None
    assert result.parsing_performed is parsing_performed
    assert result.parse_valid is parse_valid
    assert result.parsed_value_available is False
    assert result.immutable_parsed_value is None
    assert result.diagnostics == (diagnostic,)
    _assert_fixed_markers(result)
    _assert_bool_rejected(result)
    return result


def _assert_bounded_failure(
    raw: bytes,
    state: State,
    diagnostic: Diagnostic,
) -> Step2MarketSourcePolicyApprovalParseResult:
    return _assert_failure(
        raw,
        state,
        diagnostic,
        expected_size=len(raw),
        expected_hash=hashlib.sha256(raw).hexdigest(),
        parsing_performed=True,
        parse_valid=False,
    )


def _assert_valid(raw: bytes) -> Step2MarketSourcePolicyApprovalParseResult:
    result = _parse(raw)
    assert result.parse_state is State.VALID
    assert result.raw_artifact_size_bytes == len(raw)
    assert result.raw_artifact_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.parsed_value_identity_sha256 is not None
    assert len(result.parsed_value_identity_sha256) == 64
    assert result.parsing_performed is True
    assert result.parse_valid is True
    assert result.parsed_value_available is True
    assert result.diagnostics == ()
    _assert_fixed_markers(result)
    _assert_bool_rejected(result)
    return result


def _thaw(value: Any) -> Any:
    if type(value) is FrozenJsonObject:
        return {key: _thaw(child) for key, child in value.items}
    if type(value) is FrozenJsonArray:
        return [_thaw(child) for child in value.items]
    return value


def _canonical_oracle(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _identity_oracle(value: Any) -> str:
    return hashlib.sha256(_PARSED_DOMAIN + _canonical_oracle(value)).hexdigest()


def _nested_arrays(container_count: int) -> bytes:
    return b"[" * container_count + b"]" * container_count


def _node_fixture(null_count: int) -> bytes:
    lengths: list[int] = []
    remaining = null_count
    while remaining:
        length = min(MAX_ARRAY_ITEM_COUNT, remaining)
        lengths.append(length)
        remaining -= length
    children = [b"[" + b",".join([b"null"] * length) + b"]" for length in lengths]
    return b"[" + b",".join(children) + b"]"


def _object_with_members(count: int) -> bytes:
    members = [f'"k{index}":null'.encode("ascii") for index in range(count)]
    return b"{" + b",".join(members) + b"}"


def _array_with_items(count: int, item: bytes = b"null") -> bytes:
    return b"[" + b",".join([item] * count) + b"]"


def test_fixed_limits_and_versions_are_exact() -> None:
    assert MAX_RAW_ARTIFACT_BYTES == 2_097_152
    assert MAX_JSON_NESTING_DEPTH == 8
    assert MAX_JSON_NODE_COUNT == 4096
    assert MAX_DECODED_STRING_CODE_POINTS == 262_144
    assert MAX_OBJECT_MEMBER_COUNT == 1024
    assert MAX_ARRAY_ITEM_COUNT == 1024
    assert MAX_PARSED_VALUE_CANONICAL_BYTES == 12_582_912
    assert MAX_RAW_APPROVAL_PARSE_DIAGNOSTICS == 1
    assert PARSE_RESULT_VERSION == "step2_market_source_policy_approval_parse_result_v1"
    assert PARSER_VERSION == "step2_market_source_policy_approval_parser_v1"


def test_parse_state_enum_order_is_exact() -> None:
    assert tuple(Step2MarketSourcePolicyApprovalParseState) == (
        State.INPUT_ABSENT,
        State.INPUT_TYPE_INVALID,
        State.RAW_SIZE_INVALID,
        State.ENCODING_INVALID,
        State.JSON_GRAMMAR_INVALID,
        State.DUPLICATE_KEY_INVALID,
        State.RESOURCE_LIMIT_INVALID,
        State.UNSUPPORTED_SCALAR_INVALID,
        State.UNICODE_SCALAR_INVALID,
        State.VALID,
    )


def test_public_result_fields_are_exact() -> None:
    assert tuple(field.name for field in fields(Step2MarketSourcePolicyApprovalParseResult)) == (
        "result_version",
        "parser_version",
        "parse_state",
        "raw_artifact_size_bytes",
        "raw_artifact_sha256",
        "parsed_value_identity_sha256",
        "parsing_performed",
        "parse_valid",
        "parsed_value_available",
        "immutable_parsed_value",
        "diagnostics",
        "authority_scope",
        "not_trade_authorization",
        "trade_permission_effect",
        "operator_authentication_performed",
        "object_contract_validation_performed",
        "source_resolution_performed",
        "freshness_evaluation_performed",
        "activation_evaluation_performed",
        "publication_evaluation_performed",
        "workflow_permission_evaluated",
        "order_compilation_evaluated",
    )


def test_input_absent_complete_branch() -> None:
    _assert_failure(
        None,
        State.INPUT_ABSENT,
        Diagnostic.RAW_APPROVAL_INPUT_MISSING,
        expected_size=None,
        expected_hash=None,
        parsing_performed=False,
        parse_valid=None,
    )


class _BytesSubclass(bytes):
    pass


@pytest.mark.parametrize(
    "value",
    [
        _BytesSubclass(b"null"),
        bytearray(b"null"),
        memoryview(b"null"),
        "null",
        Path("approval.json"),
        BytesIO(b"null"),
        object(),
        True,
        1,
    ],
)
def test_non_exact_bytes_input_complete_branch(value: object) -> None:
    _assert_failure(
        value,
        State.INPUT_TYPE_INVALID,
        Diagnostic.RAW_APPROVAL_INPUT_TYPE_INVALID,
        expected_size=None,
        expected_hash=None,
        parsing_performed=False,
        parse_valid=None,
    )


def test_zero_bytes_retains_size_and_empty_hash() -> None:
    _assert_failure(
        b"",
        State.RAW_SIZE_INVALID,
        Diagnostic.RAW_APPROVAL_SIZE_INVALID,
        expected_size=0,
        expected_hash=hashlib.sha256(b"").hexdigest(),
        parsing_performed=False,
        parse_valid=None,
    )


def test_exact_raw_size_limit_passes() -> None:
    raw = b" " * (MAX_RAW_ARTIFACT_BYTES - 4) + b"null"
    result = _assert_valid(raw)
    assert result.immutable_parsed_value is None


def test_raw_size_limit_plus_one_retains_no_hash() -> None:
    raw = b" " * (MAX_RAW_ARTIFACT_BYTES + 1)
    _assert_failure(
        raw,
        State.RAW_SIZE_INVALID,
        Diagnostic.RAW_APPROVAL_SIZE_INVALID,
        expected_size=MAX_RAW_ARTIFACT_BYTES + 1,
        expected_hash=None,
        parsing_performed=False,
        parse_valid=None,
    )


def test_whitespace_only_is_json_invalid() -> None:
    _assert_bounded_failure(
        b" \t\n\r ",
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.RAW_APPROVAL_JSON_INVALID,
    )


def test_initial_utf8_bom_is_distinct() -> None:
    _assert_bounded_failure(
        b"\xef\xbb\xbfnull",
        State.ENCODING_INVALID,
        Diagnostic.RAW_APPROVAL_BOM_UNSUPPORTED,
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'"\x80"',
        b'"\xc3"',
        b'"\xc0\xaf"',
        b'"\xe2\x28\xa1"',
        b"\xff\xfenull",
        b'"\xed\xa0\x80"',
    ],
)
def test_invalid_utf8_forms_are_rejected(raw: bytes) -> None:
    _assert_bounded_failure(
        raw,
        State.ENCODING_INVALID,
        Diagnostic.RAW_APPROVAL_UTF8_INVALID,
    )


def test_ascii_and_non_ascii_utf8_are_valid() -> None:
    ascii_result = _assert_valid(b'"ascii"')
    unicode_result = _assert_valid('"caf\u00e9 \U0001f680"'.encode())
    assert ascii_result.immutable_parsed_value == "ascii"
    assert unicode_result.immutable_parsed_value == "caf\u00e9 \U0001f680"


def test_standard_external_whitespace_only_is_accepted() -> None:
    result = _assert_valid(b" \t\n\r { \"a\" : null } \r\n")
    assert _thaw(result.immutable_parsed_value) == {"a": None}


def test_feff_inside_string_is_data_but_outside_is_invalid() -> None:
    inside = _assert_valid('"\ufeff"'.encode())
    assert inside.immutable_parsed_value == "\ufeff"
    _assert_bounded_failure(
        b" \xef\xbb\xbfnull",
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.RAW_APPROVAL_JSON_INVALID,
    )


def test_embedded_nul_outside_string_is_json_invalid() -> None:
    _assert_bounded_failure(
        b'{"a"\x00:null}',
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.RAW_APPROVAL_JSON_INVALID,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"{}", {}),
        (b"[]", []),
        (b'"value"', "value"),
        (b"null", None),
        (b'{"a":[null,"x"]}', {"a": [None, "x"]}),
    ],
)
def test_all_accepted_root_and_nested_value_kinds(raw: bytes, expected: Any) -> None:
    result = _assert_valid(raw)
    assert _thaw(result.immutable_parsed_value) == expected


def test_valid_null_distinguishes_available_value_from_failure() -> None:
    result = _assert_valid(b"null")
    assert result.immutable_parsed_value is None
    assert result.parsed_value_available is True


@pytest.mark.parametrize(
    "raw",
    [
        b"/* comment */ null",
        b"// comment\nnull",
        b'{"a":null,}',
        b"[null,]",
        b"{'a':null}",
        b"{a:null}",
        b'"\\x"',
        b'"line\nfeed"',
        b"nul",
        b"truex",
        b"01",
        b"1.",
        b"1e",
        b"--1",
        b"NaN",
        b"Infinity",
        b"-Infinity",
    ],
)
def test_invalid_json_grammar_forms(raw: bytes) -> None:
    _assert_bounded_failure(
        raw,
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.RAW_APPROVAL_JSON_INVALID,
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"0",
        b"-1",
        b"1.25",
        b"1e2",
        b"-0.25E-2",
        b"true",
        b"false",
        b"[true]",
        b'{"a":0}',
    ],
)
def test_valid_number_and_boolean_tokens_are_unsupported(raw: bytes) -> None:
    _assert_bounded_failure(
        raw,
        State.UNSUPPORTED_SCALAR_INVALID,
        Diagnostic.RAW_APPROVAL_UNSUPPORTED_SCALAR,
    )


@pytest.mark.parametrize(
    "raw",
    [b"null null", b"nullx", b"null[]", b"{}[]", b'"a"x'],
)
def test_non_whitespace_after_root_is_trailing_content(raw: bytes) -> None:
    _assert_bounded_failure(
        raw,
        State.JSON_GRAMMAR_INVALID,
        Diagnostic.RAW_APPROVAL_TRAILING_CONTENT,
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":null,"a":null}',
        b'{"outer":{"a":null,"a":null}}',
        b'{"a":null,"\\u0061":null}',
        b'{"unknown":null,"unknown":null}',
        b'{"a":null,"a":0}',
    ],
)
def test_duplicate_keys_fail_after_decoding_at_every_depth(raw: bytes) -> None:
    _assert_bounded_failure(
        raw,
        State.DUPLICATE_KEY_INVALID,
        Diagnostic.RAW_APPROVAL_DUPLICATE_KEY,
    )


def test_case_distinct_keys_are_accepted_and_source_order_is_retained() -> None:
    result = _assert_valid(b'{"a":null,"A":null}')
    value = result.immutable_parsed_value
    assert type(value) is FrozenJsonObject
    assert tuple(key for key, _ in value.items) == ("a", "A")


def test_normalization_equivalent_keys_remain_distinct() -> None:
    raw = '{"\u00e9":null,"e\u0301":null}'.encode()
    result = _assert_valid(raw)
    value = result.immutable_parsed_value
    assert type(value) is FrozenJsonObject
    assert tuple(key for key, _ in value.items) == ("\u00e9", "e\u0301")


def test_valid_bmp_escape_surrogate_pair_and_noncharacter() -> None:
    result = _assert_valid(b'["\\u20ac","\\ud83d\\ude80","\\ufdd0"]')
    assert _thaw(result.immutable_parsed_value) == ["\u20ac", "\U0001f680", "\ufdd0"]


@pytest.mark.parametrize(
    "raw",
    [
        b'"\\ud800"',
        b'"\\udc00"',
        b'"\\ud800\\u0041"',
        b'"\\ud800x"',
        b'"\\ud800\\uxxxx"',
    ],
)
def test_invalid_surrogate_forms_are_distinct(raw: bytes) -> None:
    _assert_bounded_failure(
        raw,
        State.UNICODE_SCALAR_INVALID,
        Diagnostic.RAW_APPROVAL_SURROGATE_INVALID,
    )


def test_surrogate_pair_counts_as_one_decoded_code_point() -> None:
    raw = (
        b'"'
        + b"a" * (MAX_DECODED_STRING_CODE_POINTS - 1)
        + b"\\ud83d\\ude80"
        + b'"'
    )
    result = _assert_valid(raw)
    assert type(result.immutable_parsed_value) is str
    assert len(result.immutable_parsed_value) == MAX_DECODED_STRING_CODE_POINTS


def test_depth_exact_limit_passes_and_plus_one_fails() -> None:
    valid = _nested_arrays(MAX_JSON_NESTING_DEPTH + 1)
    invalid = _nested_arrays(MAX_JSON_NESTING_DEPTH + 2)
    _assert_valid(valid)
    _assert_bounded_failure(
        invalid,
        State.RESOURCE_LIMIT_INVALID,
        Diagnostic.RAW_APPROVAL_DEPTH_LIMIT_EXCEEDED,
    )


def test_hostile_nesting_fails_without_recursion() -> None:
    raw = _nested_arrays(10_000)
    _assert_bounded_failure(
        raw,
        State.RESOURCE_LIMIT_INVALID,
        Diagnostic.RAW_APPROVAL_DEPTH_LIMIT_EXCEEDED,
    )


def test_node_exact_limit_passes_and_plus_one_fails() -> None:
    valid = _node_fixture(4091)
    invalid = _node_fixture(4092)
    _assert_valid(valid)
    _assert_bounded_failure(
        invalid,
        State.RESOURCE_LIMIT_INVALID,
        Diagnostic.RAW_APPROVAL_NODE_LIMIT_EXCEEDED,
    )


def test_decoded_string_exact_limit_passes_and_plus_one_fails() -> None:
    valid = b'"' + b"a" * MAX_DECODED_STRING_CODE_POINTS + b'"'
    invalid = b'"' + b"a" * (MAX_DECODED_STRING_CODE_POINTS + 1) + b'"'
    result = _assert_valid(valid)
    assert len(result.immutable_parsed_value) == MAX_DECODED_STRING_CODE_POINTS
    _assert_bounded_failure(
        invalid,
        State.RESOURCE_LIMIT_INVALID,
        Diagnostic.RAW_APPROVAL_STRING_LIMIT_EXCEEDED,
    )


def test_decoded_object_key_uses_the_same_string_limit() -> None:
    valid = b'{"' + b"k" * MAX_DECODED_STRING_CODE_POINTS + b'":null}'
    invalid = b'{"' + b"k" * (MAX_DECODED_STRING_CODE_POINTS + 1) + b'":null}'
    _assert_valid(valid)
    _assert_bounded_failure(
        invalid,
        State.RESOURCE_LIMIT_INVALID,
        Diagnostic.RAW_APPROVAL_STRING_LIMIT_EXCEEDED,
    )


def test_object_member_exact_limit_passes_and_plus_one_fails() -> None:
    _assert_valid(_object_with_members(MAX_OBJECT_MEMBER_COUNT))
    _assert_bounded_failure(
        _object_with_members(MAX_OBJECT_MEMBER_COUNT + 1),
        State.RESOURCE_LIMIT_INVALID,
        Diagnostic.RAW_APPROVAL_OBJECT_MEMBER_LIMIT_EXCEEDED,
    )


def test_array_item_exact_limit_passes_and_plus_one_fails() -> None:
    _assert_valid(_array_with_items(MAX_ARRAY_ITEM_COUNT))
    _assert_bounded_failure(
        _array_with_items(MAX_ARRAY_ITEM_COUNT + 1),
        State.RESOURCE_LIMIT_INVALID,
        Diagnostic.RAW_APPROVAL_ARRAY_ITEM_LIMIT_EXCEEDED,
    )


def test_local_container_limit_precedes_unsupported_extra_value() -> None:
    array = _array_with_items(MAX_ARRAY_ITEM_COUNT)[:-1] + b",0]"
    _assert_bounded_failure(
        array,
        State.RESOURCE_LIMIT_INVALID,
        Diagnostic.RAW_APPROVAL_ARRAY_ITEM_LIMIT_EXCEEDED,
    )
    object_value = (
        _object_with_members(MAX_OBJECT_MEMBER_COUNT)[:-1] + b',"extra":0}'
    )
    _assert_bounded_failure(
        object_value,
        State.RESOURCE_LIMIT_INVALID,
        Diagnostic.RAW_APPROVAL_OBJECT_MEMBER_LIMIT_EXCEEDED,
    )


def test_canonical_streaming_exact_limit_and_limit_guard() -> None:
    full = "\x7f" * MAX_DECODED_STRING_CODE_POINTS
    tail = "\x7f" * (MAX_DECODED_STRING_CODE_POINTS - 5) + "a" * 5
    at_limit = FrozenJsonArray((full,) * 7 + (tail,))
    outcome = parser_contract._canonical_parsed_value_identity(at_limit)
    assert outcome.size_exceeded is False
    assert outcome.canonical_size_bytes == MAX_PARSED_VALUE_CANONICAL_BYTES
    assert outcome.identity_sha256 is not None

    over_tail = "\x7f" * (MAX_DECODED_STRING_CODE_POINTS - 5) + '"' + "a" * 4
    over_limit = FrozenJsonArray((full,) * 7 + (over_tail,))
    excessive = parser_contract._canonical_parsed_value_identity(over_limit)
    assert excessive.size_exceeded is True
    assert excessive.identity_sha256 is None
    assert excessive.canonical_size_bytes <= MAX_PARSED_VALUE_CANONICAL_BYTES


def test_canonical_identity_matches_standard_json_oracle() -> None:
    raw = (
        b'{"z":"\\u0000\\b\\f\\n\\r\\t\\\"\\\\",'
        b'"a":["\\ud83d\\ude80","caf\\u00e9",null]}'
    )
    result = _assert_valid(raw)
    value = _thaw(result.immutable_parsed_value)
    assert result.parsed_value_identity_sha256 == _identity_oracle(value)
    canonical = _canonical_oracle(value)
    assert b"\\u0000" in canonical
    assert b"\\b" in canonical
    assert b"\\f" in canonical
    assert b"\\n" in canonical
    assert b"\\r" in canonical
    assert b"\\t" in canonical
    assert b'\\"' in canonical
    assert b"\\\\" in canonical


def test_parsed_identity_directly_uses_the_required_domain_separator() -> None:
    raw = b'{"z":[null,{}],"a":"value"}'
    decoded = {"z": [None, {}], "a": "value"}
    result = parse_step2_market_source_policy_approvals_bytes(raw)
    canonical_root = json.dumps(
        decoded,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    unprefixed_digest = hashlib.sha256(canonical_root).hexdigest()
    prefixed_digest = hashlib.sha256(
        b"step2_market_source_policy_approval_parsed_value_v1\0"
        + canonical_root
    ).hexdigest()

    assert result.parse_valid is True
    assert result.parsed_value_identity_sha256 == prefixed_digest
    assert result.parsed_value_identity_sha256 != unprefixed_digest
    assert prefixed_digest != unprefixed_digest


def test_canonical_identity_oracle_covers_nested_empty_containers() -> None:
    raw = (
        b'{"empty_object":{},"empty_array":[],'
        b'"nested_object":{"nested":{}},'
        b'"nested_array":{"nested":[]},'
        b'"array_containers":[{},[]]}'
    )
    decoded = {
        "empty_object": {},
        "empty_array": [],
        "nested_object": {"nested": {}},
        "nested_array": {"nested": []},
        "array_containers": [{}, []],
    }
    result = parse_step2_market_source_policy_approvals_bytes(raw)
    canonical_root = json.dumps(
        decoded,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected_digest = hashlib.sha256(
        b"step2_market_source_policy_approval_parsed_value_v1\0"
        + canonical_root
    ).hexdigest()

    assert result.parse_valid is True
    assert result.parsed_value_identity_sha256 == expected_digest
    root = result.immutable_parsed_value
    assert type(root) is FrozenJsonObject
    assert tuple(key for key, _ in root.items) == (
        "empty_object",
        "empty_array",
        "nested_object",
        "nested_array",
        "array_containers",
    )
    assert type(root.items[0][1]) is FrozenJsonObject
    assert root.items[0][1].items == ()
    assert type(root.items[1][1]) is FrozenJsonArray
    assert root.items[1][1].items == ()
    nested_object = root.items[2][1]
    assert type(nested_object) is FrozenJsonObject
    assert type(nested_object.items[0][1]) is FrozenJsonObject
    assert nested_object.items[0][1].items == ()
    nested_array = root.items[3][1]
    assert type(nested_array) is FrozenJsonObject
    assert type(nested_array.items[0][1]) is FrozenJsonArray
    assert nested_array.items[0][1].items == ()
    array_containers = root.items[4][1]
    assert type(array_containers) is FrozenJsonArray
    assert type(array_containers.items[0]) is FrozenJsonObject
    assert array_containers.items[0].items == ()
    assert type(array_containers.items[1]) is FrozenJsonArray
    assert array_containers.items[1].items == ()


def test_ordinary_and_escaped_slash_each_match_independent_oracles() -> None:
    ordinary_raw = b'{"path":"a/b"}'
    escaped_raw = b'{"path":"a\\/b"}'
    decoded = {"path": "a/b"}
    canonical_root = json.dumps(
        decoded,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected_digest = hashlib.sha256(
        b"step2_market_source_policy_approval_parsed_value_v1\0"
        + canonical_root
    ).hexdigest()
    ordinary = parse_step2_market_source_policy_approvals_bytes(ordinary_raw)
    escaped = parse_step2_market_source_policy_approvals_bytes(escaped_raw)

    assert ordinary.parse_valid is True
    assert escaped.parse_valid is True
    assert ordinary.parsed_value_identity_sha256 == expected_digest
    assert escaped.parsed_value_identity_sha256 == expected_digest
    assert ordinary.raw_artifact_sha256 != escaped.raw_artifact_sha256
    assert ordinary.parsed_value_identity_sha256 == escaped.parsed_value_identity_sha256
    assert ordinary.immutable_parsed_value == escaped.immutable_parsed_value


def test_raw_whitespace_escape_and_key_order_change_only_raw_identity() -> None:
    first_raw = b'{"b":null,"a":"x"}'
    second_raw = b' { "a" : "\\u0078", "b" : null } '
    first = _assert_valid(first_raw)
    second = _assert_valid(second_raw)
    assert first.raw_artifact_sha256 != second.raw_artifact_sha256
    assert first.parsed_value_identity_sha256 == second.parsed_value_identity_sha256
    first_value = first.immutable_parsed_value
    second_value = second.immutable_parsed_value
    assert type(first_value) is FrozenJsonObject
    assert type(second_value) is FrozenJsonObject
    assert tuple(key for key, _ in first_value.items) == ("b", "a")
    assert tuple(key for key, _ in second_value.items) == ("a", "b")


def test_array_order_changes_parsed_identity() -> None:
    first = _assert_valid(b'["a","b"]')
    second = _assert_valid(b'["b","a"]')
    assert first.parsed_value_identity_sha256 != second.parsed_value_identity_sha256


def test_unicode_normalization_changes_parsed_identity() -> None:
    first = _assert_valid('"\u00e9"'.encode())
    second = _assert_valid('"e\u0301"'.encode())
    assert first.parsed_value_identity_sha256 != second.parsed_value_identity_sha256


def test_parsed_identity_is_domain_separated_from_approval_content_identity() -> None:
    content = {
        "policy_version": "policy.v1",
        "supersedes_policy_version": None,
        "policy_change_reason": "Initial empty policy.",
        "approved_by": "operator.one",
        "approved_at_utc": "2026-07-15T00:00:00Z",
        "sources": [],
    }
    content_bytes = _canonical_oracle(content)
    content_hash = hashlib.sha256(_APPROVAL_CONTENT_DOMAIN + content_bytes).hexdigest()
    approval = {
        "schema_version": "step2_market_source_policy_approvals_v1",
        "operator_approved_source_policy_sha256": content_hash,
        "approval_content": content,
    }
    raw = _canonical_oracle(approval)
    parsed = _assert_valid(raw)
    object_result = validate_step2_market_source_policy_approvals_object(approval)
    assert object_result.approval_object_valid is True
    assert object_result.canonical_approval_content_sha256 == content_hash
    assert parsed.parsed_value_identity_sha256 != content_hash


def test_frozen_tree_retains_orders_and_has_no_mutable_container() -> None:
    result = _assert_valid(b'{"b":[null,"x"],"a":{}}')
    root = result.immutable_parsed_value
    assert type(root) is FrozenJsonObject
    assert tuple(key for key, _ in root.items) == ("b", "a")
    array = root.items[0][1]
    assert type(array) is FrozenJsonArray
    assert array.items == (None, "x")
    assert not hasattr(root, "__dict__")
    assert not hasattr(array, "__dict__")
    with pytest.raises(FrozenInstanceError):
        root.items = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        array.items = ()  # type: ignore[misc]
    assert not any(type(value) in {dict, list} for value in (root, array, *array.items))


@pytest.mark.parametrize(
    "factory",
    [
        lambda: FrozenJsonArray([]),
        lambda: FrozenJsonArray(([],)),
        lambda: FrozenJsonObject([]),
        lambda: FrozenJsonObject((("a", []),)),
        lambda: FrozenJsonObject((("a", None), ("a", None))),
    ],
)
def test_public_frozen_container_construction_rejects_mutability_and_duplicates(
    factory: Any,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_frozen_container_boolean_coercion_is_rejected_exactly() -> None:
    for value in (FrozenJsonObject(()), FrozenJsonArray(())):
        with pytest.raises(TypeError) as exc_info:
            bool(value)
        assert str(exc_info.value) == FROZEN_JSON_BOOLEAN_COERCION_ERROR


@pytest.mark.parametrize(
    "value",
    [
        None,
        "null",
        b"",
        b"\xef\xbb\xbfnull",
        b"\x80",
        b"?",
        b'{"a":null,"a":null}',
        _nested_arrays(MAX_JSON_NESTING_DEPTH + 2),
        b"0",
        b'"\\ud800"',
        b"null null",
        b"null",
    ],
)
def test_every_parse_state_result_rejects_boolean_coercion(value: object) -> None:
    _assert_bool_rejected(_parse(value))


def test_result_is_frozen_slotted_and_retains_no_raw_bytes() -> None:
    result = _assert_valid(b'{"a":null}')
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.parse_valid = False  # type: ignore[misc]
    assert all(field.name != "raw_artifact_bytes" for field in fields(result))


def test_diagnostic_enum_order_and_singleton_bound_are_exact() -> None:
    assert tuple(Step2MarketSourcePolicyApprovalParseDiagnostic) == (
        Diagnostic.RAW_APPROVAL_INPUT_MISSING,
        Diagnostic.RAW_APPROVAL_INPUT_TYPE_INVALID,
        Diagnostic.RAW_APPROVAL_SIZE_INVALID,
        Diagnostic.RAW_APPROVAL_BOM_UNSUPPORTED,
        Diagnostic.RAW_APPROVAL_UTF8_INVALID,
        Diagnostic.RAW_APPROVAL_JSON_INVALID,
        Diagnostic.RAW_APPROVAL_DUPLICATE_KEY,
        Diagnostic.RAW_APPROVAL_DEPTH_LIMIT_EXCEEDED,
        Diagnostic.RAW_APPROVAL_NODE_LIMIT_EXCEEDED,
        Diagnostic.RAW_APPROVAL_STRING_LIMIT_EXCEEDED,
        Diagnostic.RAW_APPROVAL_OBJECT_MEMBER_LIMIT_EXCEEDED,
        Diagnostic.RAW_APPROVAL_ARRAY_ITEM_LIMIT_EXCEEDED,
        Diagnostic.RAW_APPROVAL_UNSUPPORTED_SCALAR,
        Diagnostic.RAW_APPROVAL_SURROGATE_INVALID,
        Diagnostic.RAW_APPROVAL_TRAILING_CONTENT,
    )
    assert MAX_RAW_APPROVAL_PARSE_DIAGNOSTICS == 1


def test_every_diagnostic_is_reachable_through_public_api() -> None:
    cases: tuple[tuple[object, Diagnostic], ...] = (
        (None, Diagnostic.RAW_APPROVAL_INPUT_MISSING),
        ("null", Diagnostic.RAW_APPROVAL_INPUT_TYPE_INVALID),
        (b"", Diagnostic.RAW_APPROVAL_SIZE_INVALID),
        (b"\xef\xbb\xbfnull", Diagnostic.RAW_APPROVAL_BOM_UNSUPPORTED),
        (b"\x80", Diagnostic.RAW_APPROVAL_UTF8_INVALID),
        (b"?", Diagnostic.RAW_APPROVAL_JSON_INVALID),
        (b'{"a":null,"a":null}', Diagnostic.RAW_APPROVAL_DUPLICATE_KEY),
        (
            _nested_arrays(MAX_JSON_NESTING_DEPTH + 2),
            Diagnostic.RAW_APPROVAL_DEPTH_LIMIT_EXCEEDED,
        ),
        (_node_fixture(4092), Diagnostic.RAW_APPROVAL_NODE_LIMIT_EXCEEDED),
        (
            b'"' + b"a" * (MAX_DECODED_STRING_CODE_POINTS + 1) + b'"',
            Diagnostic.RAW_APPROVAL_STRING_LIMIT_EXCEEDED,
        ),
        (
            _object_with_members(MAX_OBJECT_MEMBER_COUNT + 1),
            Diagnostic.RAW_APPROVAL_OBJECT_MEMBER_LIMIT_EXCEEDED,
        ),
        (
            _array_with_items(MAX_ARRAY_ITEM_COUNT + 1),
            Diagnostic.RAW_APPROVAL_ARRAY_ITEM_LIMIT_EXCEEDED,
        ),
        (b"0", Diagnostic.RAW_APPROVAL_UNSUPPORTED_SCALAR),
        (b'"\\ud800"', Diagnostic.RAW_APPROVAL_SURROGATE_INVALID),
        (b"null null", Diagnostic.RAW_APPROVAL_TRAILING_CONTENT),
    )
    reached = {_parse(value).diagnostics[0] for value, _ in cases}
    assert reached == set(Step2MarketSourcePolicyApprovalParseDiagnostic)
    assert all(_parse(value).diagnostics == (expected,) for value, expected in cases)


def test_failures_never_mix_diagnostics_or_retain_parsed_values() -> None:
    values: tuple[object, ...] = (
        None,
        "null",
        b"",
        b" " * (MAX_RAW_ARTIFACT_BYTES + 1),
        b"\xef\xbb\xbf\x80",
        b"\x80?",
        b'{"a":null,"a":0}',
        _nested_arrays(MAX_JSON_NESTING_DEPTH + 2),
    )
    for value in values:
        result = _parse(value)
        assert len(result.diagnostics) == 1
        assert result.parsed_value_identity_sha256 is None
        assert result.parsed_value_available is False
        assert result.immutable_parsed_value is None


def test_failure_precedence_is_deterministic() -> None:
    oversized_bom = b"\xef\xbb\xbf" + b" " * MAX_RAW_ARTIFACT_BYTES
    assert _parse(oversized_bom).diagnostics == (
        Diagnostic.RAW_APPROVAL_SIZE_INVALID,
    )
    assert _parse(b"\xef\xbb\xbf\x80").diagnostics == (
        Diagnostic.RAW_APPROVAL_BOM_UNSUPPORTED,
    )
    assert _parse(b"?\x80").diagnostics == (Diagnostic.RAW_APPROVAL_UTF8_INVALID,)
    assert _parse(b'{"a":null,"a":0}').diagnostics == (
        Diagnostic.RAW_APPROVAL_DUPLICATE_KEY,
    )


def test_upper_size_check_precedes_hash_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b" " * (MAX_RAW_ARTIFACT_BYTES + 1)

    def fail(value: bytes = b"") -> Any:
        raise AssertionError

    monkeypatch.setattr(parser_contract.hashlib, "sha256", fail)
    result = parse_step2_market_source_policy_approvals_bytes(raw)
    assert result.parse_state is State.RAW_SIZE_INVALID
    assert result.raw_artifact_sha256 is None


def test_public_raw_hash_dependency_exception_propagates_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"null"
    expected = ValueError("distinctive raw hash dependency failure")

    def fail(value: bytes = b"") -> Any:
        raise expected

    monkeypatch.setattr(parser_contract.hashlib, "sha256", fail)
    with pytest.raises(ValueError) as exc_info:
        parse_step2_market_source_policy_approvals_bytes(raw)
    assert exc_info.value is expected


def test_public_parsed_identity_dependency_exception_propagates_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"null"
    expected = ValueError("distinctive parsed identity dependency failure")
    original = hashlib.sha256
    calls = 0

    def fail_second(value: bytes = b"") -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise expected
        return original(value)

    monkeypatch.setattr(parser_contract.hashlib, "sha256", fail_second)
    with pytest.raises(ValueError) as exc_info:
        parse_step2_market_source_policy_approvals_bytes(raw)
    assert exc_info.value is expected
    assert calls == 2


def test_exception_handlers_are_narrow_and_no_trystar_exists() -> None:
    path = Path(parser_contract.__file__).resolve()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert not any(isinstance(node, ast.TryStar) for node in ast.walk(tree))
    tries = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
    assert len(tries) == 1
    handlers = tries[0].handlers
    assert len(handlers) == 1
    assert isinstance(handlers[0].type, ast.Name)
    assert handlers[0].type.id == "UnicodeDecodeError"


def test_parser_uses_no_forbidden_decoder_or_recursive_function() -> None:
    path = Path(parser_contract.__file__).resolve()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    forbidden_symbols = {
        "loads",
        "load",
        "raw_decode",
        "object_pairs_hook",
        "literal_eval",
        "safe_load",
    }
    assert not {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } & forbidden_symbols
    assert "object_pairs_hook" not in source
    function_names = {
        node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for function in (
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        assert not any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == function.name
            for call in ast.walk(function)
        )
    assert "_parse_json_string" in function_names


_MODULE = parser_contract.__name__
_BASENAME = _MODULE.rsplit(".", 1)[-1]
_RELATIVE_PATH = Path("src/investment_orchestrator/parsers") / f"{_BASENAME}.py"
_PUBLIC_SYMBOLS = frozenset(
    {
        "parse_step2_market_source_policy_approvals_bytes",
        "Step2MarketSourcePolicyApprovalParseResult",
        "Step2MarketSourcePolicyApprovalParseState",
        "Step2MarketSourcePolicyApprovalParseDiagnostic",
        "FrozenJsonObject",
        "FrozenJsonArray",
        "PARSE_RESULT_VERSION",
        "PARSER_VERSION",
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
            ) or (isinstance(node.func, ast.Name) and node.func.id == "__import__")
            if dynamic and any(
                isinstance(argument, ast.Constant) and argument.value == _MODULE
                for argument in node.args
            ):
                findings.append(f"{relative_path}: dynamic-import")
    for marker in (_MODULE, *_PUBLIC_SYMBOLS, PARSE_RESULT_VERSION, PARSER_VERSION):
        if marker in source:
            findings.append(f"{relative_path}: text")
    return sorted(set(findings))


def test_reference_detector_covers_import_symbol_alias_dynamic_and_literal_forms() -> None:
    cases = (
        f"import {_MODULE}\n",
        f"import {_MODULE} as parser\nparser.parse_step2_market_source_policy_approvals_bytes(b'null')\n",
        f"from {_MODULE} import Step2MarketSourcePolicyApprovalParseResult\n",
        "handler = FrozenJsonObject\n",
        f"import importlib\nimportlib.import_module({_MODULE!r})\n",
        f"contract = __import__({_MODULE!r})\n",
        f"VERSION = {PARSER_VERSION!r}\n",
    )
    assert all(
        _reference_findings(f"synthetic/{index}.py", source)
        for index, source in enumerate(cases)
    )


def test_no_production_consumer_references_strict_parser_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    production_root = root / "src" / "investment_orchestrator"
    parser_path = root / _RELATIVE_PATH
    findings: list[str] = []
    for path in sorted(production_root.rglob("*.py")):
        if path == parser_path:
            continue
        relative = path.relative_to(root).as_posix()
        findings.extend(_reference_findings(relative, path.read_text(encoding="utf-8")))
    assert sorted(set(findings)) == [], "\n".join(sorted(set(findings)))


def test_production_parser_has_no_external_or_authority_capability() -> None:
    source = Path(parser_contract.__file__).read_text(encoding="utf-8")
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
    assert imported_roots <= {"__future__", "dataclasses", "enum", "hashlib", "typing"}
    forbidden_calls = {
        "open",
        "getenv",
        "system",
        "popen",
        "run",
        "request",
        "urlopen",
        "publish",
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
    assert not called_names & forbidden_calls
    assert not called_attributes & forbidden_calls
    assert "validate_step2_market_source_policy_approvals_object" not in source


def test_tests_do_not_patch_private_parse_or_result_helpers() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    patch_targets: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setattr"
        ):
            continue
        patch_targets.extend(ast.unparse(argument) for argument in node.args[:2])
    assert patch_targets == [
        "parser_contract.hashlib",
        "'sha256'",
        "parser_contract.hashlib",
        "'sha256'",
        "parser_contract.hashlib",
        "'sha256'",
    ]


def test_public_api_name_and_exports_are_byte_specific() -> None:
    assert parse_step2_market_source_policy_approvals_bytes.__name__.endswith("_bytes")
    assert "parse_step2_market_source_policy_approvals_bytes" in parser_contract.__all__
    assert "validate_step2_market_source_policy_approvals_object" not in parser_contract.__all__
    assert not hasattr(parser_contract, "parse_step2_market_source_policy_approvals_path")


def test_valid_parse_has_no_state_action_or_authority_fields() -> None:
    result = _assert_valid(b"{}")
    forbidden_fields = (
        "operator_authenticated",
        "source_role_eligible",
        "policy_activated",
        "candidate_valid",
        "publication_eligible",
        "workflow_allowed",
        "order_compilation_allowed",
        "broker_route",
        "hold_allowed",
        "sell_allowed",
        "new_buy_allowed",
    )
    assert all(not hasattr(result, field_name) for field_name in forbidden_fields)
