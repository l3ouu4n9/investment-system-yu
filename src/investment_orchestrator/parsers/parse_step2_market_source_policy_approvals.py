"""Strict raw-byte parsing for Step 2 source-policy approval artifacts.

This module parses one exact immutable byte string into a frozen JSON-subset
tree.  It does not read artifacts, validate the decoded approval-object
contract, authenticate an operator, activate or select a policy, resolve a
source, affect a workflow, publish an artifact, compile an order, or grant
trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
from typing import TypeAlias


MAX_RAW_ARTIFACT_BYTES = 2_097_152
MAX_JSON_NESTING_DEPTH = 8
MAX_JSON_NODE_COUNT = 4096
MAX_DECODED_STRING_CODE_POINTS = 262_144
MAX_OBJECT_MEMBER_COUNT = 1024
MAX_ARRAY_ITEM_COUNT = 1024
MAX_PARSED_VALUE_CANONICAL_BYTES = 12_582_912
MAX_RAW_APPROVAL_PARSE_DIAGNOSTICS = 1

PARSE_RESULT_VERSION = "step2_market_source_policy_approval_parse_result_v1"
PARSER_VERSION = "step2_market_source_policy_approval_parser_v1"
AUTHORITY_SCOPE = "strict_raw_artifact_parsing_only"
NOT_TRADE_AUTHORIZATION = True
TRADE_PERMISSION_EFFECT = "none"
OPERATOR_AUTHENTICATION_PERFORMED = False
OBJECT_CONTRACT_VALIDATION_PERFORMED = False
SOURCE_RESOLUTION_PERFORMED = False
FRESHNESS_EVALUATION_PERFORMED = False
ACTIVATION_EVALUATION_PERFORMED = False
PUBLICATION_EVALUATION_PERFORMED = False
WORKFLOW_PERMISSION_EVALUATED = False
ORDER_COMPILATION_EVALUATED = False

PARSE_RESULT_BOOLEAN_COERCION_ERROR = (
    "inspect parse_valid explicitly; Step 2 source-policy approval parse "
    "results have no truth value"
)
FROZEN_JSON_BOOLEAN_COERCION_ERROR = (
    "inspect items explicitly; frozen JSON containers have no truth value"
)

_UTF8_BOM = b"\xef\xbb\xbf"
_JSON_WHITESPACE = frozenset(" \t\n\r")
_PARSED_VALUE_IDENTITY_DOMAIN = (
    b"step2_market_source_policy_approval_parsed_value_v1\0"
)


class Step2MarketSourcePolicyApprovalParseState(str, Enum):
    INPUT_ABSENT = "input_absent"
    INPUT_TYPE_INVALID = "input_type_invalid"
    RAW_SIZE_INVALID = "raw_size_invalid"
    ENCODING_INVALID = "encoding_invalid"
    JSON_GRAMMAR_INVALID = "json_grammar_invalid"
    DUPLICATE_KEY_INVALID = "duplicate_key_invalid"
    RESOURCE_LIMIT_INVALID = "resource_limit_invalid"
    UNSUPPORTED_SCALAR_INVALID = "unsupported_scalar_invalid"
    UNICODE_SCALAR_INVALID = "unicode_scalar_invalid"
    VALID = "valid"


class Step2MarketSourcePolicyApprovalParseDiagnostic(str, Enum):
    RAW_APPROVAL_INPUT_MISSING = "raw_approval_input_missing"
    RAW_APPROVAL_INPUT_TYPE_INVALID = "raw_approval_input_type_invalid"
    RAW_APPROVAL_SIZE_INVALID = "raw_approval_size_invalid"
    RAW_APPROVAL_BOM_UNSUPPORTED = "raw_approval_bom_unsupported"
    RAW_APPROVAL_UTF8_INVALID = "raw_approval_utf8_invalid"
    RAW_APPROVAL_JSON_INVALID = "raw_approval_json_invalid"
    RAW_APPROVAL_DUPLICATE_KEY = "raw_approval_duplicate_key"
    RAW_APPROVAL_DEPTH_LIMIT_EXCEEDED = (
        "raw_approval_depth_limit_exceeded"
    )
    RAW_APPROVAL_NODE_LIMIT_EXCEEDED = "raw_approval_node_limit_exceeded"
    RAW_APPROVAL_STRING_LIMIT_EXCEEDED = (
        "raw_approval_string_limit_exceeded"
    )
    RAW_APPROVAL_OBJECT_MEMBER_LIMIT_EXCEEDED = (
        "raw_approval_object_member_limit_exceeded"
    )
    RAW_APPROVAL_ARRAY_ITEM_LIMIT_EXCEEDED = (
        "raw_approval_array_item_limit_exceeded"
    )
    RAW_APPROVAL_UNSUPPORTED_SCALAR = "raw_approval_unsupported_scalar"
    RAW_APPROVAL_SURROGATE_INVALID = "raw_approval_surrogate_invalid"
    RAW_APPROVAL_TRAILING_CONTENT = "raw_approval_trailing_content"


@dataclass(frozen=True, slots=True)
class FrozenJsonObject:
    """One immutable JSON object retaining decoded source-member order."""

    items: tuple[tuple[str, FrozenJsonValue], ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple:
            raise TypeError
        keys: set[str] = set()
        for item in self.items:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError
            key, value = item
            if type(key) is not str or not _is_frozen_json_value(value):
                raise TypeError
            if key in keys:
                raise ValueError
            keys.add(key)

    def __bool__(self) -> bool:
        raise TypeError(FROZEN_JSON_BOOLEAN_COERCION_ERROR)


@dataclass(frozen=True, slots=True)
class FrozenJsonArray:
    """One immutable JSON array retaining source-item order."""

    items: tuple[FrozenJsonValue, ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or any(
            not _is_frozen_json_value(value) for value in self.items
        ):
            raise TypeError

    def __bool__(self) -> bool:
        raise TypeError(FROZEN_JSON_BOOLEAN_COERCION_ERROR)


FrozenJsonValue: TypeAlias = FrozenJsonObject | FrozenJsonArray | str | None


def _is_frozen_json_value(value: object) -> bool:
    return value is None or type(value) in {
        str,
        FrozenJsonObject,
        FrozenJsonArray,
    }


@dataclass(frozen=True, slots=True)
class Step2MarketSourcePolicyApprovalParseResult:
    """Frozen strict-parse result with no object or activation authority."""

    result_version: str = field(default=PARSE_RESULT_VERSION, init=False)
    parser_version: str = field(default=PARSER_VERSION, init=False)
    parse_state: Step2MarketSourcePolicyApprovalParseState
    raw_artifact_size_bytes: int | None
    raw_artifact_sha256: str | None
    parsed_value_identity_sha256: str | None
    parsing_performed: bool
    parse_valid: bool | None
    parsed_value_available: bool
    immutable_parsed_value: FrozenJsonValue | None
    diagnostics: tuple[Step2MarketSourcePolicyApprovalParseDiagnostic, ...]
    authority_scope: str = field(default=AUTHORITY_SCOPE, init=False)
    not_trade_authorization: bool = field(
        default=NOT_TRADE_AUTHORIZATION,
        init=False,
    )
    trade_permission_effect: str = field(
        default=TRADE_PERMISSION_EFFECT,
        init=False,
    )
    operator_authentication_performed: bool = field(
        default=OPERATOR_AUTHENTICATION_PERFORMED,
        init=False,
    )
    object_contract_validation_performed: bool = field(
        default=OBJECT_CONTRACT_VALIDATION_PERFORMED,
        init=False,
    )
    source_resolution_performed: bool = field(
        default=SOURCE_RESOLUTION_PERFORMED,
        init=False,
    )
    freshness_evaluation_performed: bool = field(
        default=FRESHNESS_EVALUATION_PERFORMED,
        init=False,
    )
    activation_evaluation_performed: bool = field(
        default=ACTIVATION_EVALUATION_PERFORMED,
        init=False,
    )
    publication_evaluation_performed: bool = field(
        default=PUBLICATION_EVALUATION_PERFORMED,
        init=False,
    )
    workflow_permission_evaluated: bool = field(
        default=WORKFLOW_PERMISSION_EVALUATED,
        init=False,
    )
    order_compilation_evaluated: bool = field(
        default=ORDER_COMPILATION_EVALUATED,
        init=False,
    )

    def __bool__(self) -> bool:
        raise TypeError(PARSE_RESULT_BOOLEAN_COERCION_ERROR)


_State = Step2MarketSourcePolicyApprovalParseState
_Diagnostic = Step2MarketSourcePolicyApprovalParseDiagnostic


@dataclass(frozen=True, slots=True)
class _ParseFailure:
    state: Step2MarketSourcePolicyApprovalParseState
    diagnostic: Step2MarketSourcePolicyApprovalParseDiagnostic


@dataclass(frozen=True, slots=True)
class _SubsetParseOutcome:
    value: FrozenJsonValue | None
    value_available: bool
    failure: _ParseFailure | None


@dataclass(frozen=True, slots=True)
class _StringOutcome:
    value: str | None
    next_index: int
    failure: _ParseFailure | None


@dataclass(frozen=True, slots=True)
class _CanonicalIdentityOutcome:
    identity_sha256: str | None
    canonical_size_bytes: int
    size_exceeded: bool


@dataclass(slots=True)
class _ObjectFrame:
    depth: int
    items: list[tuple[str, FrozenJsonValue]]
    keys: set[str]
    state: str
    pending_key: str | None


@dataclass(slots=True)
class _ArrayFrame:
    depth: int
    items: list[FrozenJsonValue]
    state: str


_Frame: TypeAlias = _ObjectFrame | _ArrayFrame


def parse_step2_market_source_policy_approvals_bytes(
    value: object,
) -> Step2MarketSourcePolicyApprovalParseResult:
    """Strictly parse exact approval bytes without validating their object contract."""
    if value is None:
        return _invalid_result(
            state=_State.INPUT_ABSENT,
            diagnostic=_Diagnostic.RAW_APPROVAL_INPUT_MISSING,
            size=None,
            raw_hash=None,
            parsing_performed=False,
            parse_valid=None,
        )
    if type(value) is not bytes:
        return _invalid_result(
            state=_State.INPUT_TYPE_INVALID,
            diagnostic=_Diagnostic.RAW_APPROVAL_INPUT_TYPE_INVALID,
            size=None,
            raw_hash=None,
            parsing_performed=False,
            parse_valid=None,
        )

    size = len(value)
    if size > MAX_RAW_ARTIFACT_BYTES:
        return _invalid_result(
            state=_State.RAW_SIZE_INVALID,
            diagnostic=_Diagnostic.RAW_APPROVAL_SIZE_INVALID,
            size=size,
            raw_hash=None,
            parsing_performed=False,
            parse_valid=None,
        )

    raw_hash = hashlib.sha256(value).hexdigest()
    if size == 0:
        return _invalid_result(
            state=_State.RAW_SIZE_INVALID,
            diagnostic=_Diagnostic.RAW_APPROVAL_SIZE_INVALID,
            size=0,
            raw_hash=raw_hash,
            parsing_performed=False,
            parse_valid=None,
        )
    if value.startswith(_UTF8_BOM):
        return _invalid_result(
            state=_State.ENCODING_INVALID,
            diagnostic=_Diagnostic.RAW_APPROVAL_BOM_UNSUPPORTED,
            size=size,
            raw_hash=raw_hash,
            parsing_performed=True,
            parse_valid=False,
        )

    try:
        text = value.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return _invalid_result(
            state=_State.ENCODING_INVALID,
            diagnostic=_Diagnostic.RAW_APPROVAL_UTF8_INVALID,
            size=size,
            raw_hash=raw_hash,
            parsing_performed=True,
            parse_valid=False,
        )

    parsed = _IterativeJsonSubsetParser(text).parse()
    if parsed.failure is not None:
        return _invalid_result(
            state=parsed.failure.state,
            diagnostic=parsed.failure.diagnostic,
            size=size,
            raw_hash=raw_hash,
            parsing_performed=True,
            parse_valid=False,
        )
    if not parsed.value_available:
        raise AssertionError

    canonical = _canonical_parsed_value_identity(parsed.value)
    if canonical.size_exceeded:
        return _invalid_result(
            state=_State.RESOURCE_LIMIT_INVALID,
            diagnostic=_Diagnostic.RAW_APPROVAL_SIZE_INVALID,
            size=size,
            raw_hash=raw_hash,
            parsing_performed=True,
            parse_valid=False,
        )
    if canonical.identity_sha256 is None:
        raise AssertionError

    return Step2MarketSourcePolicyApprovalParseResult(
        parse_state=_State.VALID,
        raw_artifact_size_bytes=size,
        raw_artifact_sha256=raw_hash,
        parsed_value_identity_sha256=canonical.identity_sha256,
        parsing_performed=True,
        parse_valid=True,
        parsed_value_available=True,
        immutable_parsed_value=parsed.value,
        diagnostics=(),
    )


def _invalid_result(
    *,
    state: Step2MarketSourcePolicyApprovalParseState,
    diagnostic: Step2MarketSourcePolicyApprovalParseDiagnostic,
    size: int | None,
    raw_hash: str | None,
    parsing_performed: bool,
    parse_valid: bool | None,
) -> Step2MarketSourcePolicyApprovalParseResult:
    diagnostics = (diagnostic,)
    if len(diagnostics) > MAX_RAW_APPROVAL_PARSE_DIAGNOSTICS:
        raise AssertionError
    return Step2MarketSourcePolicyApprovalParseResult(
        parse_state=state,
        raw_artifact_size_bytes=size,
        raw_artifact_sha256=raw_hash,
        parsed_value_identity_sha256=None,
        parsing_performed=parsing_performed,
        parse_valid=parse_valid,
        parsed_value_available=False,
        immutable_parsed_value=None,
        diagnostics=diagnostics,
    )


class _IterativeJsonSubsetParser:
    """Explicit-stack JSON-subset parser with fixed resource bounds."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._index = 0
        self._node_count = 0
        self._frames: list[_Frame] = []
        self._root: FrozenJsonValue | None = None
        self._root_available = False

    def parse(self) -> _SubsetParseOutcome:
        self._skip_whitespace()
        if self._index == len(self._text):
            return self._failed(_json_failure())

        failure = self._start_value(depth=0, local_limit=None)
        if failure is not None:
            return self._failed(failure)

        while True:
            if self._root_available and not self._frames:
                self._skip_whitespace()
                if self._index != len(self._text):
                    return self._failed(
                        _ParseFailure(
                            _State.JSON_GRAMMAR_INVALID,
                            _Diagnostic.RAW_APPROVAL_TRAILING_CONTENT,
                        )
                    )
                return _SubsetParseOutcome(
                    value=self._root,
                    value_available=True,
                    failure=None,
                )

            if not self._frames:
                raise AssertionError
            frame = self._frames[-1]
            if type(frame) is _ObjectFrame:
                failure = self._advance_object(frame)
            elif type(frame) is _ArrayFrame:
                failure = self._advance_array(frame)
            else:
                raise AssertionError
            if failure is not None:
                return self._failed(failure)

    def _advance_object(self, frame: _ObjectFrame) -> _ParseFailure | None:
        self._skip_whitespace()
        if frame.state in {"key_or_end", "key"}:
            if self._at_end():
                return _json_failure()
            current = self._text[self._index]
            if current == "}":
                if frame.state != "key_or_end":
                    return _json_failure()
                self._index += 1
                return self._close_object(frame)
            if current != '"':
                return _json_failure()
            key_outcome = _parse_json_string(self._text, self._index)
            if key_outcome.failure is not None:
                return key_outcome.failure
            key = key_outcome.value
            if type(key) is not str:
                raise AssertionError
            self._index = key_outcome.next_index
            if key in frame.keys:
                return _ParseFailure(
                    _State.DUPLICATE_KEY_INVALID,
                    _Diagnostic.RAW_APPROVAL_DUPLICATE_KEY,
                )
            frame.keys.add(key)
            frame.pending_key = key
            frame.state = "colon"
            return None

        if frame.state == "colon":
            if self._at_end() or self._text[self._index] != ":":
                return _json_failure()
            self._index += 1
            frame.state = "value"
            return None

        if frame.state == "value":
            self._skip_whitespace()
            local_limit = (
                _ParseFailure(
                    _State.RESOURCE_LIMIT_INVALID,
                    _Diagnostic.RAW_APPROVAL_OBJECT_MEMBER_LIMIT_EXCEEDED,
                )
                if len(frame.items) >= MAX_OBJECT_MEMBER_COUNT
                else None
            )
            return self._start_value(
                depth=frame.depth + 1,
                local_limit=local_limit,
            )

        if frame.state == "comma_or_end":
            if self._at_end():
                return _json_failure()
            current = self._text[self._index]
            if current == ",":
                self._index += 1
                frame.state = "key"
                return None
            if current == "}":
                self._index += 1
                return self._close_object(frame)
            return _json_failure()

        raise AssertionError

    def _advance_array(self, frame: _ArrayFrame) -> _ParseFailure | None:
        self._skip_whitespace()
        if frame.state in {"value_or_end", "value"}:
            if self._at_end():
                return _json_failure()
            if self._text[self._index] == "]":
                if frame.state != "value_or_end":
                    return _json_failure()
                self._index += 1
                return self._close_array(frame)
            local_limit = (
                _ParseFailure(
                    _State.RESOURCE_LIMIT_INVALID,
                    _Diagnostic.RAW_APPROVAL_ARRAY_ITEM_LIMIT_EXCEEDED,
                )
                if len(frame.items) >= MAX_ARRAY_ITEM_COUNT
                else None
            )
            return self._start_value(
                depth=frame.depth + 1,
                local_limit=local_limit,
            )

        if frame.state == "comma_or_end":
            if self._at_end():
                return _json_failure()
            current = self._text[self._index]
            if current == ",":
                self._index += 1
                frame.state = "value"
                return None
            if current == "]":
                self._index += 1
                return self._close_array(frame)
            return _json_failure()

        raise AssertionError

    def _start_value(
        self,
        *,
        depth: int,
        local_limit: _ParseFailure | None,
    ) -> _ParseFailure | None:
        if depth > MAX_JSON_NESTING_DEPTH:
            return _ParseFailure(
                _State.RESOURCE_LIMIT_INVALID,
                _Diagnostic.RAW_APPROVAL_DEPTH_LIMIT_EXCEEDED,
            )
        if self._node_count + 1 > MAX_JSON_NODE_COUNT:
            return _ParseFailure(
                _State.RESOURCE_LIMIT_INVALID,
                _Diagnostic.RAW_APPROVAL_NODE_LIMIT_EXCEEDED,
            )
        if local_limit is not None:
            return local_limit
        self._node_count += 1

        if self._at_end():
            return _json_failure()
        current = self._text[self._index]
        if current == "{":
            self._index += 1
            self._frames.append(
                _ObjectFrame(
                    depth=depth,
                    items=[],
                    keys=set(),
                    state="key_or_end",
                    pending_key=None,
                )
            )
            return None
        if current == "[":
            self._index += 1
            self._frames.append(
                _ArrayFrame(depth=depth, items=[], state="value_or_end")
            )
            return None
        if current == '"':
            outcome = _parse_json_string(self._text, self._index)
            if outcome.failure is not None:
                return outcome.failure
            if type(outcome.value) is not str:
                raise AssertionError
            self._index = outcome.next_index
            self._attach_value(outcome.value)
            return None
        if current == "n":
            if self._text[self._index : self._index + 4] != "null":
                return _json_failure()
            self._index += 4
            self._attach_value(None)
            return None
        if current == "t":
            return self._unsupported_word("true")
        if current == "f":
            return self._unsupported_word("false")
        if current == "-" or _is_ascii_digit(current):
            number_end = _scan_json_number(self._text, self._index)
            if number_end is None or not _is_value_delimiter(
                self._text,
                number_end,
            ):
                return _json_failure()
            return _ParseFailure(
                _State.UNSUPPORTED_SCALAR_INVALID,
                _Diagnostic.RAW_APPROVAL_UNSUPPORTED_SCALAR,
            )
        return _json_failure()

    def _unsupported_word(self, token: str) -> _ParseFailure:
        if not _matches_delimited_token(self._text, self._index, token):
            return _json_failure()
        return _ParseFailure(
            _State.UNSUPPORTED_SCALAR_INVALID,
            _Diagnostic.RAW_APPROVAL_UNSUPPORTED_SCALAR,
        )

    def _attach_value(self, value: FrozenJsonValue) -> None:
        if not self._frames:
            if self._root_available:
                raise AssertionError
            self._root = value
            self._root_available = True
            return

        frame = self._frames[-1]
        if type(frame) is _ObjectFrame:
            if frame.state != "value" or frame.pending_key is None:
                raise AssertionError
            frame.items.append((frame.pending_key, value))
            frame.pending_key = None
            frame.state = "comma_or_end"
            return
        if type(frame) is _ArrayFrame:
            if frame.state not in {"value_or_end", "value"}:
                raise AssertionError
            frame.items.append(value)
            frame.state = "comma_or_end"
            return
        raise AssertionError

    def _close_object(self, frame: _ObjectFrame) -> None:
        if self._frames[-1] is not frame:
            raise AssertionError
        self._frames.pop()
        self._attach_value(FrozenJsonObject(tuple(frame.items)))

    def _close_array(self, frame: _ArrayFrame) -> None:
        if self._frames[-1] is not frame:
            raise AssertionError
        self._frames.pop()
        self._attach_value(FrozenJsonArray(tuple(frame.items)))

    def _skip_whitespace(self) -> None:
        while (
            self._index < len(self._text)
            and self._text[self._index] in _JSON_WHITESPACE
        ):
            self._index += 1

    def _at_end(self) -> bool:
        return self._index >= len(self._text)

    @staticmethod
    def _failed(failure: _ParseFailure) -> _SubsetParseOutcome:
        return _SubsetParseOutcome(
            value=None,
            value_available=False,
            failure=failure,
        )


def _parse_json_string(text: str, start: int) -> _StringOutcome:
    if start >= len(text) or text[start] != '"':
        raise AssertionError
    pieces: list[str] = []
    count = 0
    index = start + 1

    while index < len(text):
        current = text[index]
        codepoint = ord(current)
        if current == '"':
            return _StringOutcome("".join(pieces), index + 1, None)
        if codepoint < 0x20:
            return _string_failure(_json_failure())
        if current != "\\":
            if 0xD800 <= codepoint <= 0xDFFF:
                return _string_failure(_surrogate_failure())
            if count >= MAX_DECODED_STRING_CODE_POINTS:
                return _string_failure(_string_limit_failure())
            pieces.append(current)
            count += 1
            index += 1
            continue

        index += 1
        if index >= len(text):
            return _string_failure(_json_failure())
        escape = text[index]
        simple_escapes = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        if escape in simple_escapes:
            decoded = simple_escapes[escape]
            index += 1
        elif escape == "u":
            code_unit = _read_hex_code_unit(text, index + 1)
            if code_unit is None:
                return _string_failure(_json_failure())
            index += 5
            if 0xD800 <= code_unit <= 0xDBFF:
                if index + 6 > len(text) or text[index : index + 2] != "\\u":
                    return _string_failure(_surrogate_failure())
                low = _read_hex_code_unit(text, index + 2)
                if low is None or not 0xDC00 <= low <= 0xDFFF:
                    return _string_failure(_surrogate_failure())
                scalar = 0x10000 + ((code_unit - 0xD800) << 10) + (
                    low - 0xDC00
                )
                decoded = chr(scalar)
                index += 6
            elif 0xDC00 <= code_unit <= 0xDFFF:
                return _string_failure(_surrogate_failure())
            else:
                decoded = chr(code_unit)
        else:
            return _string_failure(_json_failure())

        if count >= MAX_DECODED_STRING_CODE_POINTS:
            return _string_failure(_string_limit_failure())
        pieces.append(decoded)
        count += 1

    return _string_failure(_json_failure())


def _read_hex_code_unit(text: str, start: int) -> int | None:
    end = start + 4
    if end > len(text):
        return None
    value = 0
    for character in text[start:end]:
        if "0" <= character <= "9":
            digit = ord(character) - ord("0")
        elif "a" <= character <= "f":
            digit = ord(character) - ord("a") + 10
        elif "A" <= character <= "F":
            digit = ord(character) - ord("A") + 10
        else:
            return None
        value = (value << 4) | digit
    return value


def _scan_json_number(text: str, start: int) -> int | None:
    index = start
    if text[index] == "-":
        index += 1
        if index >= len(text):
            return None

    if text[index] == "0":
        index += 1
        if index < len(text) and _is_ascii_digit(text[index]):
            return None
    elif "1" <= text[index] <= "9":
        index += 1
        while index < len(text) and _is_ascii_digit(text[index]):
            index += 1
    else:
        return None

    if index < len(text) and text[index] == ".":
        index += 1
        fraction_start = index
        while index < len(text) and _is_ascii_digit(text[index]):
            index += 1
        if index == fraction_start:
            return None

    if index < len(text) and text[index] in {"e", "E"}:
        index += 1
        if index < len(text) and text[index] in {"+", "-"}:
            index += 1
        exponent_start = index
        while index < len(text) and _is_ascii_digit(text[index]):
            index += 1
        if index == exponent_start:
            return None
    return index


def _matches_delimited_token(text: str, start: int, token: str) -> bool:
    end = start + len(token)
    return text[start:end] == token and _is_value_delimiter(text, end)


def _is_value_delimiter(text: str, index: int) -> bool:
    return index == len(text) or text[index] in _JSON_WHITESPACE or text[index] in {
        ",",
        "]",
        "}",
    }


def _is_ascii_digit(value: str) -> bool:
    return "0" <= value <= "9"


def _json_failure() -> _ParseFailure:
    return _ParseFailure(
        _State.JSON_GRAMMAR_INVALID,
        _Diagnostic.RAW_APPROVAL_JSON_INVALID,
    )


def _surrogate_failure() -> _ParseFailure:
    return _ParseFailure(
        _State.UNICODE_SCALAR_INVALID,
        _Diagnostic.RAW_APPROVAL_SURROGATE_INVALID,
    )


def _string_limit_failure() -> _ParseFailure:
    return _ParseFailure(
        _State.RESOURCE_LIMIT_INVALID,
        _Diagnostic.RAW_APPROVAL_STRING_LIMIT_EXCEEDED,
    )


def _string_failure(failure: _ParseFailure) -> _StringOutcome:
    return _StringOutcome(value=None, next_index=0, failure=failure)


def _canonical_parsed_value_identity(
    value: FrozenJsonValue,
) -> _CanonicalIdentityOutcome:
    """Stream canonical full-root JSON into a domain-separated SHA-256."""
    hasher = hashlib.sha256()
    hasher.update(_PARSED_VALUE_IDENTITY_DOMAIN)
    canonical_size = 0
    stack: list[tuple[str, object]] = [("value", value)]

    while stack:
        operation, current = stack.pop()
        if operation == "raw":
            fragment = current
            if type(fragment) is not bytes:
                raise AssertionError
            canonical_size, exceeded = _update_canonical_hash(
                hasher,
                canonical_size,
                fragment,
            )
            if exceeded:
                return _CanonicalIdentityOutcome(None, canonical_size, True)
            continue
        if operation == "string":
            if type(current) is not str:
                raise AssertionError
            canonical_size, exceeded = _update_canonical_string(
                hasher,
                canonical_size,
                current,
            )
            if exceeded:
                return _CanonicalIdentityOutcome(None, canonical_size, True)
            continue
        if operation != "value":
            raise AssertionError

        if current is None:
            canonical_size, exceeded = _update_canonical_hash(
                hasher,
                canonical_size,
                b"null",
            )
            if exceeded:
                return _CanonicalIdentityOutcome(None, canonical_size, True)
            continue
        if type(current) is str:
            stack.append(("string", current))
            continue
        if type(current) is FrozenJsonArray:
            canonical_size, exceeded = _update_canonical_hash(
                hasher,
                canonical_size,
                b"[",
            )
            if exceeded:
                return _CanonicalIdentityOutcome(None, canonical_size, True)
            stack.append(("raw", b"]"))
            for index in range(len(current.items) - 1, -1, -1):
                stack.append(("value", current.items[index]))
                if index != 0:
                    stack.append(("raw", b","))
            continue
        if type(current) is FrozenJsonObject:
            canonical_size, exceeded = _update_canonical_hash(
                hasher,
                canonical_size,
                b"{",
            )
            if exceeded:
                return _CanonicalIdentityOutcome(None, canonical_size, True)
            sorted_items = sorted(current.items, key=lambda item: item[0])
            stack.append(("raw", b"}"))
            for index in range(len(sorted_items) - 1, -1, -1):
                key, child = sorted_items[index]
                stack.append(("value", child))
                stack.append(("raw", b":"))
                stack.append(("string", key))
                if index != 0:
                    stack.append(("raw", b","))
            continue
        raise AssertionError

    return _CanonicalIdentityOutcome(
        identity_sha256=hasher.hexdigest(),
        canonical_size_bytes=canonical_size,
        size_exceeded=False,
    )


def _update_canonical_hash(
    hasher: object,
    current_size: int,
    fragment: bytes,
) -> tuple[int, bool]:
    next_size = current_size + len(fragment)
    if next_size > MAX_PARSED_VALUE_CANONICAL_BYTES:
        return current_size, True
    hasher.update(fragment)
    return next_size, False


def _update_canonical_string(
    hasher: object,
    current_size: int,
    value: str,
) -> tuple[int, bool]:
    current_size, exceeded = _update_canonical_hash(
        hasher,
        current_size,
        b'"',
    )
    if exceeded:
        return current_size, True

    escapes = {
        0x08: b"\\b",
        0x09: b"\\t",
        0x0A: b"\\n",
        0x0C: b"\\f",
        0x0D: b"\\r",
        0x22: b'\\"',
        0x5C: b"\\\\",
    }
    for character in value:
        codepoint = ord(character)
        if codepoint in escapes:
            fragment = escapes[codepoint]
        elif 0x20 <= codepoint <= 0x7E:
            fragment = bytes((codepoint,))
        elif codepoint <= 0xFFFF:
            fragment = f"\\u{codepoint:04x}".encode("ascii")
        else:
            adjusted = codepoint - 0x10000
            high = 0xD800 + (adjusted >> 10)
            low = 0xDC00 + (adjusted & 0x3FF)
            fragment = f"\\u{high:04x}\\u{low:04x}".encode("ascii")
        current_size, exceeded = _update_canonical_hash(
            hasher,
            current_size,
            fragment,
        )
        if exceeded:
            return current_size, True

    return _update_canonical_hash(hasher, current_size, b'"')


__all__ = [
    "ACTIVATION_EVALUATION_PERFORMED",
    "AUTHORITY_SCOPE",
    "FRESHNESS_EVALUATION_PERFORMED",
    "FROZEN_JSON_BOOLEAN_COERCION_ERROR",
    "FrozenJsonArray",
    "FrozenJsonObject",
    "FrozenJsonValue",
    "MAX_ARRAY_ITEM_COUNT",
    "MAX_DECODED_STRING_CODE_POINTS",
    "MAX_JSON_NESTING_DEPTH",
    "MAX_JSON_NODE_COUNT",
    "MAX_OBJECT_MEMBER_COUNT",
    "MAX_PARSED_VALUE_CANONICAL_BYTES",
    "MAX_RAW_APPROVAL_PARSE_DIAGNOSTICS",
    "MAX_RAW_ARTIFACT_BYTES",
    "NOT_TRADE_AUTHORIZATION",
    "OBJECT_CONTRACT_VALIDATION_PERFORMED",
    "OPERATOR_AUTHENTICATION_PERFORMED",
    "ORDER_COMPILATION_EVALUATED",
    "PARSER_VERSION",
    "PARSE_RESULT_BOOLEAN_COERCION_ERROR",
    "PARSE_RESULT_VERSION",
    "PUBLICATION_EVALUATION_PERFORMED",
    "SOURCE_RESOLUTION_PERFORMED",
    "Step2MarketSourcePolicyApprovalParseDiagnostic",
    "Step2MarketSourcePolicyApprovalParseResult",
    "Step2MarketSourcePolicyApprovalParseState",
    "TRADE_PERMISSION_EFFECT",
    "WORKFLOW_PERMISSION_EVALUATED",
    "parse_step2_market_source_policy_approvals_bytes",
]
