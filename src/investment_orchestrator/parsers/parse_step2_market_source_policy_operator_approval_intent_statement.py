"""Strict raw JSON parsing for operator approval-intent statements.

This module deliberately stops at raw JSON parsing.  It does not validate the
five-field statement contract, compute a statement semantic identity,
authenticate a subject, interpret an approval, activate a policy, or grant
workflow or trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
import math
from typing import NoReturn, TypeAlias


MIN_RAW_STATEMENT_BYTES = 1
MAX_RAW_STATEMENT_BYTES = 4_096
MAX_JSON_NESTING_DEPTH = 8
MAX_JSON_NODE_COUNT = 256
MAX_CUMULATIVE_STRING_CODE_POINTS = 2_048
MAX_INDIVIDUAL_STRING_CODE_POINTS = 1_024
MAX_OBJECT_MEMBER_COUNT = 32
MAX_ARRAY_ITEM_COUNT = 64
MAX_JSON_NUMBER_TOKEN_CODE_POINTS = 256
MAX_PARSED_VALUE_CANONICAL_BYTES = 32_768
MAX_APPROVAL_INTENT_STATEMENT_PARSE_DIAGNOSTICS = 1

APPROVAL_INTENT_STATEMENT_RESULT_REVISION = (
    "step2_market_source_policy_operator_approval_intent_statement_"
    "parse_result_v1"
)
APPROVAL_INTENT_STATEMENT_PARSER_REVISION = (
    "step2_market_source_policy_operator_approval_intent_statement_"
    "parser_v1"
)
AUTHORITY_SCOPE = "raw_json_parsing_only"
NOT_AUTHENTICATION = True
NOT_APPROVAL_AUTHORIZATION = True
NOT_ACTIVATION_AUTHORIZATION = True
NOT_TRADE_AUTHORIZATION = True
TRADE_PERMISSION_EFFECT = "none"
STATEMENT_CONTRACT_VALIDATION_PERFORMED = False
STATEMENT_SEMANTIC_IDENTITY_COMPUTED = False
AUTHENTICATION_EVALUATION_PERFORMED = False
INTENT_EVALUATION_PERFORMED = False
FRESHNESS_EVALUATION_PERFORMED = False
REPLAY_EVALUATION_PERFORMED = False
LIFECYCLE_EVALUATION_PERFORMED = False
WORKFLOW_PERMISSION_EVALUATED = False
ORDER_COMPILATION_EVALUATED = False

PARSED_VALUE_IDENTITY_DOMAIN = (
    b"step2_market_source_policy_operator_"
    b"approval_intent_statement_parsed_value_v1\0"
)
PARSE_RESULT_BOOLEAN_COERCION_ERROR = (
    "inspect parse_valid explicitly; operator approval-intent statement parse "
    "results have no truth value"
)
FROZEN_JSON_BOOLEAN_COERCION_ERROR = (
    "inspect items explicitly; frozen approval-intent JSON containers have no "
    "truth value"
)
_FROZEN_JSON_STATE_RESTORATION_ERROR = (
    "operator approval-intent JSON containers do not support state restoration"
)
_RESULT_CONSTRUCTION_ERROR = (
    "operator approval-intent statement parse results are created only by the "
    "public parser"
)
_INVARIANT_ERROR = (
    "Step 2 operator approval-intent statement parser invariant violated"
)
_UTF8_BOM = b"\xef\xbb\xbf"
_JSON_WHITESPACE = frozenset(" \t\n\r")


class Step2MarketSourcePolicyOperatorApprovalIntentStatementParseState(
    str,
    Enum,
):
    INPUT_ABSENT = "input_absent"
    INPUT_TYPE_INVALID = "input_type_invalid"
    RAW_SIZE_INVALID = "raw_size_invalid"
    ENCODING_INVALID = "encoding_invalid"
    JSON_GRAMMAR_INVALID = "json_grammar_invalid"
    DUPLICATE_KEY_INVALID = "duplicate_key_invalid"
    UNICODE_SCALAR_INVALID = "unicode_scalar_invalid"
    RESOURCE_LIMIT_INVALID = "resource_limit_invalid"
    VALID = "valid"


class Step2MarketSourcePolicyOperatorApprovalIntentStatementParseDiagnostic(
    str,
    Enum,
):
    STATEMENT_INPUT_MISSING = "statement_input_missing"
    STATEMENT_INPUT_TYPE_INVALID = "statement_input_type_invalid"
    STATEMENT_RAW_SIZE_INVALID = "statement_raw_size_invalid"
    STATEMENT_UTF8_BOM_UNSUPPORTED = "statement_utf8_bom_unsupported"
    STATEMENT_UTF8_INVALID = "statement_utf8_invalid"
    STATEMENT_JSON_INVALID = "statement_json_invalid"
    STATEMENT_TRAILING_CONTENT = "statement_trailing_content"
    STATEMENT_DUPLICATE_KEY = "statement_duplicate_key"
    STATEMENT_SURROGATE_INVALID = "statement_surrogate_invalid"
    STATEMENT_DEPTH_LIMIT_EXCEEDED = "statement_depth_limit_exceeded"
    STATEMENT_NODE_LIMIT_EXCEEDED = "statement_node_limit_exceeded"
    STATEMENT_CUMULATIVE_STRING_LIMIT_EXCEEDED = (
        "statement_cumulative_string_limit_exceeded"
    )
    STATEMENT_STRING_LIMIT_EXCEEDED = "statement_string_limit_exceeded"
    STATEMENT_OBJECT_MEMBER_LIMIT_EXCEEDED = (
        "statement_object_member_limit_exceeded"
    )
    STATEMENT_ARRAY_ITEM_LIMIT_EXCEEDED = "statement_array_item_limit_exceeded"
    STATEMENT_NUMBER_LIMIT_EXCEEDED = "statement_number_limit_exceeded"


class _ParserInvariantError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FrozenApprovalIntentJsonObject:
    """An immutable JSON object retaining source member order."""

    items: tuple[tuple[str, FrozenApprovalIntentJsonValue], ...]

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

    def __setstate__(self, state: object) -> NoReturn:
        del state
        raise TypeError(_FROZEN_JSON_STATE_RESTORATION_ERROR)


@dataclass(frozen=True, slots=True)
class FrozenApprovalIntentJsonArray:
    """An immutable JSON array retaining source item order."""

    items: tuple[FrozenApprovalIntentJsonValue, ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or any(
            not _is_frozen_json_value(value) for value in self.items
        ):
            raise TypeError

    def __bool__(self) -> bool:
        raise TypeError(FROZEN_JSON_BOOLEAN_COERCION_ERROR)

    def __setstate__(self, state: object) -> NoReturn:
        del state
        raise TypeError(_FROZEN_JSON_STATE_RESTORATION_ERROR)


FrozenApprovalIntentJsonValue: TypeAlias = (
    FrozenApprovalIntentJsonObject
    | FrozenApprovalIntentJsonArray
    | str
    | int
    | float
    | bool
    | None
)


def _is_frozen_json_value(value: object) -> bool:
    return value is None or type(value) in {
        FrozenApprovalIntentJsonObject,
        FrozenApprovalIntentJsonArray,
        str,
        int,
        float,
        bool,
    }


@dataclass(frozen=True, slots=True, init=False)
class Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult:
    """A sealed raw-parse report with no statement or authority decision."""

    result_version: str
    parser_version: str
    parse_state: Step2MarketSourcePolicyOperatorApprovalIntentStatementParseState
    raw_statement_size_bytes: int | None
    raw_statement_sha256: str | None
    parsed_value_identity_sha256: str | None
    text_decoding_performed: bool
    text_decoding_valid: bool | None
    json_syntax_validation_performed: bool
    json_syntax_valid: bool | None
    duplicate_key_validation_performed: bool
    duplicate_keys_valid: bool | None
    unicode_scalar_validation_performed: bool
    unicode_scalars_valid: bool | None
    structural_bound_validation_performed: bool
    structural_bounds_valid: bool | None
    parse_valid: bool | None
    parsed_value_available: bool
    immutable_parsed_value: FrozenApprovalIntentJsonValue | None
    diagnostics: tuple[
        Step2MarketSourcePolicyOperatorApprovalIntentStatementParseDiagnostic,
        ...,
    ]
    authority_scope: str
    not_authentication: bool
    not_approval_authorization: bool
    not_activation_authorization: bool
    not_trade_authorization: bool
    trade_permission_effect: str
    statement_contract_validation_performed: bool
    statement_semantic_identity_computed: bool
    authentication_evaluation_performed: bool
    intent_evaluation_performed: bool
    freshness_evaluation_performed: bool
    replay_evaluation_performed: bool
    lifecycle_evaluation_performed: bool
    workflow_permission_evaluated: bool
    order_compilation_evaluated: bool

    def __new__(cls, *args: object, **kwargs: object) -> NoReturn:
        del cls, args, kwargs
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __setstate__(self, state: object) -> NoReturn:
        del state
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __reduce__(self) -> NoReturn:
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __bool__(self) -> bool:
        raise TypeError(PARSE_RESULT_BOOLEAN_COERCION_ERROR)


_State = Step2MarketSourcePolicyOperatorApprovalIntentStatementParseState
_Diagnostic = Step2MarketSourcePolicyOperatorApprovalIntentStatementParseDiagnostic


class _JsonGrammarFailure(Exception):
    pass


class _TrailingContentFailure(Exception):
    pass


class _DuplicateKeyFailure(Exception):
    pass


class _UnicodeScalarFailure(Exception):
    pass


class _ResourceLimitFailure(Exception):
    def __init__(self, diagnostic: _Diagnostic) -> None:
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class _SyntaxNumber:
    token: str


@dataclass(frozen=True, slots=True)
class _SyntaxObject:
    items: tuple[tuple[str, _SyntaxValue], ...]


@dataclass(frozen=True, slots=True)
class _SyntaxArray:
    items: tuple[_SyntaxValue, ...]


_SyntaxValue: TypeAlias = (
    _SyntaxObject | _SyntaxArray | _SyntaxNumber | str | bool | None
)


@dataclass(slots=True)
class _ObjectParseFrame:
    depth: int
    items: list[tuple[str, _SyntaxValue]]
    state: str
    retain: bool
    member_count: int
    pending_key: str | None
    pending_member_retained: bool
    key_spans: list[tuple[int, int]]


@dataclass(slots=True)
class _ArrayParseFrame:
    depth: int
    items: list[_SyntaxValue]
    state: str
    retain: bool
    item_count: int
    pending_item_retained: bool


_ParseFrame: TypeAlias = _ObjectParseFrame | _ArrayParseFrame
_NO_VALUE = object()
_DISCARDED_VALUE = object()


@dataclass(slots=True)
class _ParseControl:
    """Bounded checks recorded in left-to-right parser encounter order.

    Values record depth/node limits as they begin; container member/item limits
    record before their excess child starts; strings and numbers record while
    their token is scanned.  Grammar, duplicate-key, and Unicode ownership is
    resolved only after the complete JSON grammar has been consumed.
    """

    first_resource_diagnostic: _Diagnostic | None = None
    duplicate_key_found: bool = False
    unicode_scalar_invalid: bool = False
    node_count: int = 0
    cumulative_string_code_points: int = 0

    def record_resource(self, diagnostic: _Diagnostic) -> None:
        if self.first_resource_diagnostic is None:
            self.first_resource_diagnostic = diagnostic

    def begin_value(self, *, depth: int, retain: bool) -> bool:
        if depth > MAX_JSON_NESTING_DEPTH:
            self.record_resource(_Diagnostic.STATEMENT_DEPTH_LIMIT_EXCEEDED)
            retain = False
        if self.node_count >= MAX_JSON_NODE_COUNT:
            self.record_resource(_Diagnostic.STATEMENT_NODE_LIMIT_EXCEEDED)
            retain = False
        if self.node_count <= MAX_JSON_NODE_COUNT:
            self.node_count += 1
        return retain


@dataclass(frozen=True, slots=True)
class _ParseOutcome:
    syntax_value: _SyntaxValue | None
    syntax_value_available: bool
    resource_diagnostic: _Diagnostic | None
    duplicate_key_found: bool
    unicode_scalar_invalid: bool


@dataclass(frozen=True, slots=True)
class _ParsedJsonString:
    value: str | None
    end: int
    span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _RawStatementBoundary:
    raw_statement: bytes
    size_bytes: int


def parse_step2_market_source_policy_operator_approval_intent_statement_bytes(
    value: object,
) -> Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult:
    """Parse one exact JSON value without validating statement semantics."""
    if value is None:
        return _create_result(
            state=_State.INPUT_ABSENT,
            diagnostic=_Diagnostic.STATEMENT_INPUT_MISSING,
            raw_boundary=None,
            immutable_parsed_value=None,
        )
    if type(value) is not bytes:
        return _create_result(
            state=_State.INPUT_TYPE_INVALID,
            diagnostic=_Diagnostic.STATEMENT_INPUT_TYPE_INVALID,
            raw_boundary=None,
            immutable_parsed_value=None,
        )

    size = len(value)
    if size < MIN_RAW_STATEMENT_BYTES or size > MAX_RAW_STATEMENT_BYTES:
        return _create_result(
            state=_State.RAW_SIZE_INVALID,
            diagnostic=_Diagnostic.STATEMENT_RAW_SIZE_INVALID,
            raw_boundary=_RawStatementBoundary(value, size),
            immutable_parsed_value=None,
        )
    # Run the parser-owned raw identity stage before any decoding decision.
    # The sealed factory recomputes the value it stores from this exact raw
    # boundary so no result-construction path can accept a supplied identity.
    hashlib.sha256(value).hexdigest()
    raw_boundary = _RawStatementBoundary(value, size)
    if value.startswith(_UTF8_BOM):
        return _create_result(
            state=_State.ENCODING_INVALID,
            diagnostic=_Diagnostic.STATEMENT_UTF8_BOM_UNSUPPORTED,
            raw_boundary=raw_boundary,
            immutable_parsed_value=None,
        )

    try:
        text = value.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return _create_result(
            state=_State.ENCODING_INVALID,
            diagnostic=_Diagnostic.STATEMENT_UTF8_INVALID,
            raw_boundary=raw_boundary,
            immutable_parsed_value=None,
        )

    try:
        parse_outcome = _IterativeJsonParser(text).parse()
    except _TrailingContentFailure:
        return _create_result(
            state=_State.JSON_GRAMMAR_INVALID,
            diagnostic=_Diagnostic.STATEMENT_TRAILING_CONTENT,
            raw_boundary=raw_boundary,
            immutable_parsed_value=None,
        )
    except _JsonGrammarFailure:
        return _create_result(
            state=_State.JSON_GRAMMAR_INVALID,
            diagnostic=_Diagnostic.STATEMENT_JSON_INVALID,
            raw_boundary=raw_boundary,
            immutable_parsed_value=None,
        )

    if parse_outcome.duplicate_key_found:
        return _create_result(
            state=_State.DUPLICATE_KEY_INVALID,
            diagnostic=_Diagnostic.STATEMENT_DUPLICATE_KEY,
            raw_boundary=raw_boundary,
            immutable_parsed_value=None,
        )

    syntax_value = parse_outcome.syntax_value
    if parse_outcome.syntax_value_available:
        if not _is_syntax_value(syntax_value):
            _invariant_failure()
        try:
            _validate_no_duplicate_keys(syntax_value)
        except _DuplicateKeyFailure:
            return _create_result(
                state=_State.DUPLICATE_KEY_INVALID,
                diagnostic=_Diagnostic.STATEMENT_DUPLICATE_KEY,
                raw_boundary=raw_boundary,
                immutable_parsed_value=None,
            )

    if parse_outcome.unicode_scalar_invalid:
        return _create_result(
            state=_State.UNICODE_SCALAR_INVALID,
            diagnostic=_Diagnostic.STATEMENT_SURROGATE_INVALID,
            raw_boundary=raw_boundary,
            immutable_parsed_value=None,
        )

    if parse_outcome.resource_diagnostic is not None:
        return _create_result(
            state=_State.RESOURCE_LIMIT_INVALID,
            diagnostic=parse_outcome.resource_diagnostic,
            raw_boundary=raw_boundary,
            immutable_parsed_value=None,
        )

    if not parse_outcome.syntax_value_available:
        _invariant_failure()

    try:
        scalar_value = _normalize_unicode_scalars(syntax_value)
        immutable_value = _convert_with_resource_bounds(scalar_value)
    except _ResourceLimitFailure as failure:
        return _create_result(
            state=_State.RESOURCE_LIMIT_INVALID,
            diagnostic=failure.diagnostic,
            raw_boundary=raw_boundary,
            immutable_parsed_value=None,
        )

    return _create_result(
        state=_State.VALID,
        diagnostic=None,
        raw_boundary=raw_boundary,
        immutable_parsed_value=immutable_value,
    )


class _IterativeJsonParser:
    """An explicit-stack RFC 8259 parser with bounded syntax retention."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._index = 0
        self._frames: list[_ParseFrame] = []
        self._root: _SyntaxValue | object = _NO_VALUE
        self._control = _ParseControl()

    def parse(self) -> _ParseOutcome:
        self._skip_whitespace()
        if self._at_end():
            raise _JsonGrammarFailure
        self._start_value(depth=0, retain=True)

        while True:
            if self._root is not _NO_VALUE and not self._frames:
                self._skip_whitespace()
                if not self._at_end():
                    raise _TrailingContentFailure
                root = self._root
                if root is _DISCARDED_VALUE:
                    syntax_value: _SyntaxValue | None = None
                    syntax_value_available = False
                elif _is_syntax_value(root):
                    syntax_value = root
                    syntax_value_available = True
                else:
                    _invariant_failure()
                return _ParseOutcome(
                    syntax_value=syntax_value,
                    syntax_value_available=syntax_value_available,
                    resource_diagnostic=self._control.first_resource_diagnostic,
                    duplicate_key_found=self._control.duplicate_key_found,
                    unicode_scalar_invalid=self._control.unicode_scalar_invalid,
                )
            if not self._frames:
                _invariant_failure()
            frame = self._frames[-1]
            if type(frame) is _ObjectParseFrame:
                self._advance_object(frame)
            elif type(frame) is _ArrayParseFrame:
                self._advance_array(frame)
            else:
                _invariant_failure()

    def _advance_object(self, frame: _ObjectParseFrame) -> None:
        self._skip_whitespace()
        if frame.state in {"key_or_end", "key"}:
            if self._at_end():
                raise _JsonGrammarFailure
            if self._text[self._index] == "}":
                if frame.state != "key_or_end":
                    raise _JsonGrammarFailure
                self._index += 1
                self._close_object(frame)
                return
            if self._text[self._index] != '"':
                raise _JsonGrammarFailure
            member_retained = (
                frame.retain and frame.member_count < MAX_OBJECT_MEMBER_COUNT
            )
            if frame.member_count >= MAX_OBJECT_MEMBER_COUNT:
                self._control.record_resource(
                    _Diagnostic.STATEMENT_OBJECT_MEMBER_LIMIT_EXCEEDED
                )
            parsed = _parse_json_string(
                self._text,
                self._index,
                self._control,
                retain=member_retained,
            )
            self._index = parsed.end
            self._record_object_key(frame, parsed.span)
            if frame.member_count <= MAX_OBJECT_MEMBER_COUNT:
                frame.member_count += 1
            frame.pending_key = parsed.value if member_retained else None
            frame.pending_member_retained = (
                member_retained and type(parsed.value) is str
            )
            frame.state = "colon"
            return
        if frame.state == "colon":
            self._skip_whitespace()
            if self._at_end() or self._text[self._index] != ":":
                raise _JsonGrammarFailure
            self._index += 1
            frame.state = "value"
            return
        if frame.state == "value":
            self._skip_whitespace()
            self._start_value(
                depth=frame.depth + 1,
                retain=frame.pending_member_retained,
            )
            return
        if frame.state == "comma_or_end":
            self._skip_whitespace()
            if self._at_end():
                raise _JsonGrammarFailure
            current = self._text[self._index]
            if current == ",":
                self._index += 1
                frame.state = "key"
                return
            if current == "}":
                self._index += 1
                self._close_object(frame)
                return
            raise _JsonGrammarFailure
        _invariant_failure()

    def _advance_array(self, frame: _ArrayParseFrame) -> None:
        self._skip_whitespace()
        if frame.state in {"value_or_end", "value"}:
            if self._at_end():
                raise _JsonGrammarFailure
            if self._text[self._index] == "]":
                if frame.state != "value_or_end":
                    raise _JsonGrammarFailure
                self._index += 1
                self._close_array(frame)
                return
            item_retained = frame.retain and frame.item_count < MAX_ARRAY_ITEM_COUNT
            if frame.item_count >= MAX_ARRAY_ITEM_COUNT:
                self._control.record_resource(
                    _Diagnostic.STATEMENT_ARRAY_ITEM_LIMIT_EXCEEDED
                )
            if frame.item_count <= MAX_ARRAY_ITEM_COUNT:
                frame.item_count += 1
            frame.pending_item_retained = item_retained
            self._start_value(depth=frame.depth + 1, retain=item_retained)
            return
        if frame.state == "comma_or_end":
            self._skip_whitespace()
            if self._at_end():
                raise _JsonGrammarFailure
            current = self._text[self._index]
            if current == ",":
                self._index += 1
                frame.state = "value"
                return
            if current == "]":
                self._index += 1
                self._close_array(frame)
                return
            raise _JsonGrammarFailure
        _invariant_failure()

    def _start_value(self, *, depth: int, retain: bool) -> None:
        if self._at_end():
            raise _JsonGrammarFailure
        retain = self._control.begin_value(depth=depth, retain=retain)
        current = self._text[self._index]
        if current == "{":
            self._index += 1
            self._frames.append(
                _ObjectParseFrame(
                    depth=depth,
                    items=[],
                    state="key_or_end",
                    retain=retain,
                    member_count=0,
                    pending_key=None,
                    pending_member_retained=False,
                    key_spans=[],
                )
            )
            return
        if current == "[":
            self._index += 1
            self._frames.append(
                _ArrayParseFrame(
                    depth=depth,
                    items=[],
                    state="value_or_end",
                    retain=retain,
                    item_count=0,
                    pending_item_retained=False,
                )
            )
            return
        if current == '"':
            parsed = _parse_json_string(
                self._text,
                self._index,
                self._control,
                retain=retain,
            )
            self._index = parsed.end
            self._attach_value(
                parsed.value if parsed.value is not None else _DISCARDED_VALUE
            )
            return
        if current == "n":
            self._consume_keyword("null", None, retain=retain)
            return
        if current == "t":
            self._consume_keyword("true", True, retain=retain)
            return
        if current == "f":
            self._consume_keyword("false", False, retain=retain)
            return
        if current == "-" or _is_ascii_digit(current):
            parsed, next_index = _parse_json_number(
                self._text,
                self._index,
                self._control,
                retain=retain,
            )
            self._index = next_index
            self._attach_value(
                parsed if parsed is not None else _DISCARDED_VALUE
            )
            return
        raise _JsonGrammarFailure

    def _consume_keyword(
        self,
        token: str,
        value: bool | None,
        *,
        retain: bool,
    ) -> None:
        end = self._index + len(token)
        if self._text[self._index:end] != token:
            raise _JsonGrammarFailure
        self._index = end
        self._attach_value(value if retain else _DISCARDED_VALUE)

    def _record_object_key(
        self,
        frame: _ObjectParseFrame,
        span: tuple[int, int],
    ) -> None:
        for previous in frame.key_spans:
            if _json_string_spans_equal(self._text, previous, span):
                self._control.duplicate_key_found = True
                break
        frame.key_spans.append(span)

    def _attach_value(self, value: _SyntaxValue | object) -> None:
        if not self._frames:
            if self._root is not _NO_VALUE:
                _invariant_failure()
            self._root = value
            return
        frame = self._frames[-1]
        if type(frame) is _ObjectParseFrame:
            if frame.state != "value":
                _invariant_failure()
            if frame.pending_member_retained and value is not _DISCARDED_VALUE:
                if type(frame.pending_key) is not str or not _is_syntax_value(value):
                    _invariant_failure()
                frame.items.append((frame.pending_key, value))
            frame.pending_key = None
            frame.pending_member_retained = False
            frame.state = "comma_or_end"
            return
        if type(frame) is _ArrayParseFrame:
            if frame.state not in {"value_or_end", "value"}:
                _invariant_failure()
            if frame.pending_item_retained and value is not _DISCARDED_VALUE:
                if not _is_syntax_value(value):
                    _invariant_failure()
                frame.items.append(value)
            frame.pending_item_retained = False
            frame.state = "comma_or_end"
            return
        _invariant_failure()

    def _close_object(self, frame: _ObjectParseFrame) -> None:
        if not self._frames or self._frames[-1] is not frame:
            _invariant_failure()
        self._frames.pop()
        self._attach_value(
            _SyntaxObject(tuple(frame.items))
            if frame.retain
            else _DISCARDED_VALUE
        )

    def _close_array(self, frame: _ArrayParseFrame) -> None:
        if not self._frames or self._frames[-1] is not frame:
            _invariant_failure()
        self._frames.pop()
        self._attach_value(
            _SyntaxArray(tuple(frame.items))
            if frame.retain
            else _DISCARDED_VALUE
        )

    def _skip_whitespace(self) -> None:
        while (
            self._index < len(self._text)
            and self._text[self._index] in _JSON_WHITESPACE
        ):
            self._index += 1

    def _at_end(self) -> bool:
        return self._index >= len(self._text)


def _parse_json_string(
    text: str,
    start: int,
    control: _ParseControl,
    *,
    retain: bool,
) -> _ParsedJsonString:
    if start >= len(text) or text[start] != '"':
        _invariant_failure()
    pieces: list[str] = []
    index = start + 1
    pending_high_surrogate: int | None = None
    individual_count = 0
    value_retained = retain
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

    def retain_scalar(value: str) -> None:
        nonlocal individual_count, value_retained
        individual_allowed = individual_count < MAX_INDIVIDUAL_STRING_CODE_POINTS
        cumulative_allowed = (
            control.cumulative_string_code_points
            < MAX_CUMULATIVE_STRING_CODE_POINTS
        )
        if not individual_allowed:
            control.record_resource(_Diagnostic.STATEMENT_STRING_LIMIT_EXCEEDED)
        if not cumulative_allowed:
            control.record_resource(
                _Diagnostic.STATEMENT_CUMULATIVE_STRING_LIMIT_EXCEEDED
            )
        individual_count += 1
        if cumulative_allowed:
            control.cumulative_string_code_points += 1
        if value_retained and individual_allowed and cumulative_allowed:
            pieces.append(value)
        else:
            value_retained = False

    def consume_character(value: str) -> None:
        nonlocal pending_high_surrogate, value_retained
        codepoint = ord(value)
        if pending_high_surrogate is not None:
            if 0xDC00 <= codepoint <= 0xDFFF:
                scalar = 0x10000 + (
                    (pending_high_surrogate - 0xD800) << 10
                ) + (codepoint - 0xDC00)
                pending_high_surrogate = None
                retain_scalar(chr(scalar))
                return
            control.unicode_scalar_invalid = True
            pending_high_surrogate = None
            value_retained = False
        if 0xD800 <= codepoint <= 0xDBFF:
            pending_high_surrogate = codepoint
            return
        if 0xDC00 <= codepoint <= 0xDFFF:
            control.unicode_scalar_invalid = True
            value_retained = False
            return
        retain_scalar(value)

    while index < len(text):
        current = text[index]
        if current == '"':
            if pending_high_surrogate is not None:
                control.unicode_scalar_invalid = True
                value_retained = False
            return _ParsedJsonString(
                value="".join(pieces) if value_retained else None,
                end=index + 1,
                span=(start, index + 1),
            )
        if ord(current) < 0x20:
            raise _JsonGrammarFailure
        if current != "\\":
            consume_character(current)
            index += 1
            continue
        index += 1
        if index >= len(text):
            raise _JsonGrammarFailure
        escape = text[index]
        if escape in simple_escapes:
            consume_character(simple_escapes[escape])
            index += 1
            continue
        if escape != "u":
            raise _JsonGrammarFailure
        code_unit = _read_hex_code_unit(text, index + 1)
        if code_unit is None:
            raise _JsonGrammarFailure
        consume_character(chr(code_unit))
        index += 5
    raise _JsonGrammarFailure


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


def _json_string_spans_equal(
    text: str,
    left: tuple[int, int],
    right: tuple[int, int],
) -> bool:
    left_values = _iter_json_string_comparison_scalars(text, *left)
    right_values = _iter_json_string_comparison_scalars(text, *right)
    sentinel = object()
    while True:
        left_value = next(left_values, sentinel)
        right_value = next(right_values, sentinel)
        if left_value is sentinel or right_value is sentinel:
            return left_value is sentinel and right_value is sentinel
        if left_value != right_value:
            return False


def _iter_json_string_comparison_scalars(
    text: str,
    start: int,
    end: int,
) -> object:
    """Yield decoded key scalars without retaining a second key string."""
    if start >= end or text[start] != '"' or text[end - 1] != '"':
        _invariant_failure()
    index = start + 1
    pending_high_surrogate: int | None = None
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
    while index < end - 1:
        current = text[index]
        if current == "\\":
            index += 1
            if index >= end - 1:
                _invariant_failure()
            escape = text[index]
            if escape in simple_escapes:
                current = simple_escapes[escape]
                index += 1
            elif escape == "u":
                code_unit = _read_hex_code_unit(text, index + 1)
                if code_unit is None:
                    _invariant_failure()
                current = chr(code_unit)
                index += 5
            else:
                _invariant_failure()
        else:
            index += 1
        codepoint = ord(current)
        if pending_high_surrogate is not None:
            if 0xDC00 <= codepoint <= 0xDFFF:
                scalar = 0x10000 + (
                    (pending_high_surrogate - 0xD800) << 10
                ) + (codepoint - 0xDC00)
                pending_high_surrogate = None
                yield chr(scalar)
                continue
            yield chr(pending_high_surrogate)
            pending_high_surrogate = None
        if 0xD800 <= codepoint <= 0xDBFF:
            pending_high_surrogate = codepoint
        else:
            yield current
    if pending_high_surrogate is not None:
        yield chr(pending_high_surrogate)


def _parse_json_number(
    text: str,
    start: int,
    control: _ParseControl,
    *,
    retain: bool,
) -> tuple[_SyntaxNumber | None, int]:
    index = start
    if text[index] == "-":
        index += 1
        if index >= len(text):
            raise _JsonGrammarFailure
    if text[index] == "0":
        index += 1
        if index < len(text) and _is_ascii_digit(text[index]):
            raise _JsonGrammarFailure
    elif "1" <= text[index] <= "9":
        index += 1
        while index < len(text) and _is_ascii_digit(text[index]):
            index += 1
    else:
        raise _JsonGrammarFailure
    if index < len(text) and text[index] == ".":
        index += 1
        fraction_start = index
        while index < len(text) and _is_ascii_digit(text[index]):
            index += 1
        if index == fraction_start:
            raise _JsonGrammarFailure
    if index < len(text) and text[index] in {"e", "E"}:
        index += 1
        if index < len(text) and text[index] in {"+", "-"}:
            index += 1
        exponent_start = index
        while index < len(text) and _is_ascii_digit(text[index]):
            index += 1
        if index == exponent_start:
            raise _JsonGrammarFailure
    if index - start > MAX_JSON_NUMBER_TOKEN_CODE_POINTS:
        control.record_resource(_Diagnostic.STATEMENT_NUMBER_LIMIT_EXCEEDED)
        return None, index
    if not retain:
        return None, index
    return _SyntaxNumber(text[start:index]), index


def _is_ascii_digit(value: str) -> bool:
    return "0" <= value <= "9"


def _is_syntax_value(value: object) -> bool:
    return value is None or type(value) in {
        _SyntaxObject,
        _SyntaxArray,
        _SyntaxNumber,
        str,
        bool,
    }


def _validate_no_duplicate_keys(value: _SyntaxValue) -> None:
    stack: list[_SyntaxValue] = [value]
    while stack:
        current = stack.pop()
        if type(current) is _SyntaxObject:
            keys: set[str] = set()
            for key, child in current.items:
                comparable_key = _collapse_valid_surrogate_pairs(key)
                if comparable_key in keys:
                    raise _DuplicateKeyFailure
                keys.add(comparable_key)
                stack.append(child)
        elif type(current) is _SyntaxArray:
            stack.extend(current.items)
        elif not _is_syntax_value(current):
            _invariant_failure()


def _collapse_valid_surrogate_pairs(value: str) -> str:
    pieces: list[str] = []
    index = 0
    while index < len(value):
        codepoint = ord(value[index])
        if (
            0xD800 <= codepoint <= 0xDBFF
            and index + 1 < len(value)
            and 0xDC00 <= ord(value[index + 1]) <= 0xDFFF
        ):
            scalar = 0x10000 + ((codepoint - 0xD800) << 10) + (
                ord(value[index + 1]) - 0xDC00
            )
            pieces.append(chr(scalar))
            index += 2
            continue
        pieces.append(value[index])
        index += 1
    return "".join(pieces)


def _normalize_unicode_scalars(value: _SyntaxValue) -> _SyntaxValue:
    results: list[_SyntaxValue | str] = []
    stack: list[tuple[str, object]] = [("value", value)]
    while stack:
        operation, current = stack.pop()
        if operation == "string":
            if type(current) is not str:
                _invariant_failure()
            results.append(_normalize_unicode_string(current))
            continue
        if operation == "object":
            if type(current) is not _SyntaxObject:
                _invariant_failure()
            item_count = len(current.items)
            start = len(results) - (item_count * 2)
            if start < 0:
                _invariant_failure()
            members = results[start:]
            del results[start:]
            if any(type(member) is not str for member in members[::2]):
                _invariant_failure()
            items = tuple(
                (members[index], members[index + 1])
                for index in range(0, len(members), 2)
            )
            results.append(_SyntaxObject(items))
            continue
        if operation == "array":
            if type(current) is not _SyntaxArray:
                _invariant_failure()
            item_count = len(current.items)
            start = len(results) - item_count
            if start < 0:
                _invariant_failure()
            items = tuple(results[start:])
            del results[start:]
            results.append(_SyntaxArray(items))
            continue
        if operation != "value":
            _invariant_failure()
        if type(current) is _SyntaxObject:
            stack.append(("object", current))
            for key, child in reversed(current.items):
                stack.append(("value", child))
                stack.append(("string", key))
            continue
        if type(current) is _SyntaxArray:
            stack.append(("array", current))
            for child in reversed(current.items):
                stack.append(("value", child))
            continue
        if type(current) is str:
            stack.append(("string", current))
            continue
        if current is None or type(current) in {_SyntaxNumber, bool}:
            results.append(current)
            continue
        _invariant_failure()
    if len(results) != 1 or not _is_syntax_value(results[0]):
        _invariant_failure()
    return results[0]


def _normalize_unicode_string(value: str) -> str:
    pieces: list[str] = []
    index = 0
    while index < len(value):
        codepoint = ord(value[index])
        if 0xD800 <= codepoint <= 0xDBFF:
            if (
                index + 1 >= len(value)
                or not 0xDC00 <= ord(value[index + 1]) <= 0xDFFF
            ):
                raise _UnicodeScalarFailure
            scalar = 0x10000 + ((codepoint - 0xD800) << 10) + (
                ord(value[index + 1]) - 0xDC00
            )
            pieces.append(chr(scalar))
            index += 2
            continue
        if 0xDC00 <= codepoint <= 0xDFFF:
            raise _UnicodeScalarFailure
        pieces.append(value[index])
        index += 1
    return "".join(pieces)


def _convert_with_resource_bounds(
    value: _SyntaxValue,
) -> FrozenApprovalIntentJsonValue:
    node_count = 0
    string_count = 0
    results: list[FrozenApprovalIntentJsonValue | str] = []
    stack: list[tuple[str, object, int]] = [("value", value, 0)]
    while stack:
        operation, current, depth = stack.pop()
        if operation == "string":
            if type(current) is not str:
                _invariant_failure()
            string_count = _check_string_bound(current, string_count)
            results.append(current)
            continue
        if operation == "object":
            if type(current) is not _SyntaxObject:
                _invariant_failure()
            count = len(current.items)
            start = len(results) - (count * 2)
            if start < 0:
                _invariant_failure()
            members = results[start:]
            del results[start:]
            if any(type(member) is not str for member in members[::2]):
                _invariant_failure()
            results.append(
                FrozenApprovalIntentJsonObject(
                    tuple(
                        (members[index], members[index + 1])
                        for index in range(0, len(members), 2)
                    )
                )
            )
            continue
        if operation == "array":
            if type(current) is not _SyntaxArray:
                _invariant_failure()
            count = len(current.items)
            start = len(results) - count
            if start < 0:
                _invariant_failure()
            items = tuple(results[start:])
            del results[start:]
            results.append(FrozenApprovalIntentJsonArray(items))
            continue
        if operation != "value":
            _invariant_failure()
        if depth > MAX_JSON_NESTING_DEPTH:
            raise _ResourceLimitFailure(
                _Diagnostic.STATEMENT_DEPTH_LIMIT_EXCEEDED
            )
        if node_count >= MAX_JSON_NODE_COUNT:
            raise _ResourceLimitFailure(
                _Diagnostic.STATEMENT_NODE_LIMIT_EXCEEDED
            )
        node_count += 1
        if type(current) is _SyntaxObject:
            if len(current.items) > MAX_OBJECT_MEMBER_COUNT:
                raise _ResourceLimitFailure(
                    _Diagnostic.STATEMENT_OBJECT_MEMBER_LIMIT_EXCEEDED
                )
            stack.append(("object", current, depth))
            for key, child in reversed(current.items):
                stack.append(("value", child, depth + 1))
                stack.append(("string", key, depth))
            continue
        if type(current) is _SyntaxArray:
            if len(current.items) > MAX_ARRAY_ITEM_COUNT:
                raise _ResourceLimitFailure(
                    _Diagnostic.STATEMENT_ARRAY_ITEM_LIMIT_EXCEEDED
                )
            stack.append(("array", current, depth))
            for child in reversed(current.items):
                stack.append(("value", child, depth + 1))
            continue
        if type(current) is str:
            stack.append(("string", current, depth))
            continue
        if type(current) is _SyntaxNumber:
            results.append(_convert_json_number(current.token))
            continue
        if current is None or type(current) is bool:
            results.append(current)
            continue
        _invariant_failure()
    if len(results) != 1 or not _is_frozen_json_value(results[0]):
        _invariant_failure()
    return results[0]


def _check_string_bound(value: str, current_total: int) -> int:
    length = len(value)
    if length > MAX_INDIVIDUAL_STRING_CODE_POINTS:
        raise _ResourceLimitFailure(_Diagnostic.STATEMENT_STRING_LIMIT_EXCEEDED)
    if current_total + length > MAX_CUMULATIVE_STRING_CODE_POINTS:
        raise _ResourceLimitFailure(
            _Diagnostic.STATEMENT_CUMULATIVE_STRING_LIMIT_EXCEEDED
        )
    return current_total + length


def _convert_json_number(token: str) -> int | float:
    if len(token) > MAX_JSON_NUMBER_TOKEN_CODE_POINTS:
        raise _ResourceLimitFailure(_Diagnostic.STATEMENT_NUMBER_LIMIT_EXCEEDED)
    if "." not in token and "e" not in token and "E" not in token:
        return int(token)
    value = float(token)
    if not math.isfinite(value):
        raise _ResourceLimitFailure(_Diagnostic.STATEMENT_NUMBER_LIMIT_EXCEEDED)
    return value


def _create_result(
    *,
    state: _State,
    diagnostic: _Diagnostic | None,
    raw_boundary: _RawStatementBoundary | None,
    immutable_parsed_value: FrozenApprovalIntentJsonValue | None,
) -> Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult:
    """Create every public result and internally derive all identities."""
    if type(state) is not _State:
        _invariant_failure()
    matrix = _branch_matrix(state)
    expected_diagnostics = _state_diagnostics(state)
    if diagnostic not in expected_diagnostics:
        _invariant_failure()
    diagnostics = () if diagnostic is None else (diagnostic,)
    if len(diagnostics) > MAX_APPROVAL_INTENT_STATEMENT_PARSE_DIAGNOSTICS:
        _invariant_failure()

    if state in {_State.INPUT_ABSENT, _State.INPUT_TYPE_INVALID}:
        if raw_boundary is not None or immutable_parsed_value is not None:
            _invariant_failure()
        raw_size: int | None = None
        raw_identity: str | None = None
    else:
        if type(raw_boundary) is not _RawStatementBoundary:
            _invariant_failure()
        raw_size = raw_boundary.size_bytes
        if type(raw_boundary.raw_statement) is not bytes:
            _invariant_failure()
        if raw_size != len(raw_boundary.raw_statement):
            _invariant_failure()
        size_valid = (
            MIN_RAW_STATEMENT_BYTES
            <= raw_size
            <= MAX_RAW_STATEMENT_BYTES
        )
        if (state is _State.RAW_SIZE_INVALID) is not (not size_valid):
            _invariant_failure()
        if state is _State.RAW_SIZE_INVALID:
            raw_identity = None
        else:
            if not size_valid:
                _invariant_failure()
            raw_identity = hashlib.sha256(raw_boundary.raw_statement).hexdigest()

    if state is _State.VALID:
        _validate_immutable_tree(immutable_parsed_value)
        parsed_identity = _parsed_value_identity(immutable_parsed_value)
    else:
        if immutable_parsed_value is not None:
            _invariant_failure()
        parsed_identity = None

    (
        text_performed,
        text_valid,
        syntax_performed,
        syntax_valid,
        duplicate_performed,
        duplicate_valid,
        unicode_performed,
        unicode_valid,
        bound_performed,
        bound_valid,
        parse_valid,
    ) = matrix
    parsed_available = state is _State.VALID
    if not parsed_available and immutable_parsed_value is not None:
        _invariant_failure()
    if parsed_available is not (parsed_identity is not None):
        _invariant_failure()

    result = object.__new__(
        Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult
    )
    values = {
        "result_version": APPROVAL_INTENT_STATEMENT_RESULT_REVISION,
        "parser_version": APPROVAL_INTENT_STATEMENT_PARSER_REVISION,
        "parse_state": state,
        "raw_statement_size_bytes": raw_size,
        "raw_statement_sha256": raw_identity,
        "parsed_value_identity_sha256": parsed_identity,
        "text_decoding_performed": text_performed,
        "text_decoding_valid": text_valid,
        "json_syntax_validation_performed": syntax_performed,
        "json_syntax_valid": syntax_valid,
        "duplicate_key_validation_performed": duplicate_performed,
        "duplicate_keys_valid": duplicate_valid,
        "unicode_scalar_validation_performed": unicode_performed,
        "unicode_scalars_valid": unicode_valid,
        "structural_bound_validation_performed": bound_performed,
        "structural_bounds_valid": bound_valid,
        "parse_valid": parse_valid,
        "parsed_value_available": parsed_available,
        "immutable_parsed_value": immutable_parsed_value,
        "diagnostics": diagnostics,
        "authority_scope": AUTHORITY_SCOPE,
        "not_authentication": NOT_AUTHENTICATION,
        "not_approval_authorization": NOT_APPROVAL_AUTHORIZATION,
        "not_activation_authorization": NOT_ACTIVATION_AUTHORIZATION,
        "not_trade_authorization": NOT_TRADE_AUTHORIZATION,
        "trade_permission_effect": TRADE_PERMISSION_EFFECT,
        "statement_contract_validation_performed": (
            STATEMENT_CONTRACT_VALIDATION_PERFORMED
        ),
        "statement_semantic_identity_computed": (
            STATEMENT_SEMANTIC_IDENTITY_COMPUTED
        ),
        "authentication_evaluation_performed": (
            AUTHENTICATION_EVALUATION_PERFORMED
        ),
        "intent_evaluation_performed": INTENT_EVALUATION_PERFORMED,
        "freshness_evaluation_performed": FRESHNESS_EVALUATION_PERFORMED,
        "replay_evaluation_performed": REPLAY_EVALUATION_PERFORMED,
        "lifecycle_evaluation_performed": LIFECYCLE_EVALUATION_PERFORMED,
        "workflow_permission_evaluated": WORKFLOW_PERMISSION_EVALUATED,
        "order_compilation_evaluated": ORDER_COMPILATION_EVALUATED,
    }
    if tuple(values) != tuple(field.name for field in fields(type(result))):
        _invariant_failure()
    for name, field_value in values.items():
        object.__setattr__(result, name, field_value)
    return result


def _branch_matrix(
    state: _State,
) -> tuple[
    bool,
    bool | None,
    bool,
    bool | None,
    bool,
    bool | None,
    bool,
    bool | None,
    bool,
    bool | None,
    bool | None,
]:
    matrices = {
        _State.INPUT_ABSENT: (
            False,
            None,
            False,
            None,
            False,
            None,
            False,
            None,
            False,
            None,
            None,
        ),
        _State.INPUT_TYPE_INVALID: (
            False,
            None,
            False,
            None,
            False,
            None,
            False,
            None,
            False,
            None,
            None,
        ),
        _State.RAW_SIZE_INVALID: (
            False,
            None,
            False,
            None,
            False,
            None,
            False,
            None,
            False,
            None,
            None,
        ),
        _State.ENCODING_INVALID: (
            True,
            False,
            False,
            None,
            False,
            None,
            False,
            None,
            False,
            None,
            False,
        ),
        _State.JSON_GRAMMAR_INVALID: (
            True,
            True,
            True,
            False,
            False,
            None,
            False,
            None,
            False,
            None,
            False,
        ),
        _State.DUPLICATE_KEY_INVALID: (
            True,
            True,
            True,
            True,
            True,
            False,
            False,
            None,
            False,
            None,
            False,
        ),
        _State.UNICODE_SCALAR_INVALID: (
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            False,
            False,
            None,
            False,
        ),
        _State.RESOURCE_LIMIT_INVALID: (
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            False,
            False,
        ),
        _State.VALID: (
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
        ),
    }
    try:
        return matrices[state]
    except KeyError:
        _invariant_failure()


def _state_diagnostics(state: _State) -> tuple[_Diagnostic | None, ...]:
    diagnostics = {
        _State.INPUT_ABSENT: (_Diagnostic.STATEMENT_INPUT_MISSING,),
        _State.INPUT_TYPE_INVALID: (_Diagnostic.STATEMENT_INPUT_TYPE_INVALID,),
        _State.RAW_SIZE_INVALID: (_Diagnostic.STATEMENT_RAW_SIZE_INVALID,),
        _State.ENCODING_INVALID: (
            _Diagnostic.STATEMENT_UTF8_BOM_UNSUPPORTED,
            _Diagnostic.STATEMENT_UTF8_INVALID,
        ),
        _State.JSON_GRAMMAR_INVALID: (
            _Diagnostic.STATEMENT_JSON_INVALID,
            _Diagnostic.STATEMENT_TRAILING_CONTENT,
        ),
        _State.DUPLICATE_KEY_INVALID: (_Diagnostic.STATEMENT_DUPLICATE_KEY,),
        _State.UNICODE_SCALAR_INVALID: (_Diagnostic.STATEMENT_SURROGATE_INVALID,),
        _State.RESOURCE_LIMIT_INVALID: (
            _Diagnostic.STATEMENT_DEPTH_LIMIT_EXCEEDED,
            _Diagnostic.STATEMENT_NODE_LIMIT_EXCEEDED,
            _Diagnostic.STATEMENT_CUMULATIVE_STRING_LIMIT_EXCEEDED,
            _Diagnostic.STATEMENT_STRING_LIMIT_EXCEEDED,
            _Diagnostic.STATEMENT_OBJECT_MEMBER_LIMIT_EXCEEDED,
            _Diagnostic.STATEMENT_ARRAY_ITEM_LIMIT_EXCEEDED,
            _Diagnostic.STATEMENT_NUMBER_LIMIT_EXCEEDED,
        ),
        _State.VALID: (None,),
    }
    try:
        return diagnostics[state]
    except KeyError:
        _invariant_failure()


def _validate_immutable_tree(value: FrozenApprovalIntentJsonValue) -> None:
    active: set[int] = set()
    node_count = 0
    string_count = 0
    stack: list[tuple[str, object, int]] = [("value", value, 0)]
    while stack:
        operation, current, depth = stack.pop()
        if operation == "leave":
            active.remove(id(current))
            continue
        if operation != "value":
            _invariant_failure()
        if depth > MAX_JSON_NESTING_DEPTH or node_count >= MAX_JSON_NODE_COUNT:
            _invariant_failure()
        node_count += 1
        if current is None or type(current) in {int, bool}:
            continue
        if type(current) is str:
            string_count = _validate_frozen_string(current, string_count)
            continue
        if type(current) is float:
            if not math.isfinite(current):
                _invariant_failure()
            continue
        if type(current) is FrozenApprovalIntentJsonArray:
            identifier = id(current)
            if (
                identifier in active
                or type(current.items) is not tuple
                or len(current.items) > MAX_ARRAY_ITEM_COUNT
            ):
                _invariant_failure()
            active.add(identifier)
            stack.append(("leave", current, depth))
            for child in reversed(current.items):
                stack.append(("value", child, depth + 1))
            continue
        if type(current) is FrozenApprovalIntentJsonObject:
            identifier = id(current)
            if (
                identifier in active
                or type(current.items) is not tuple
                or len(current.items) > MAX_OBJECT_MEMBER_COUNT
            ):
                _invariant_failure()
            active.add(identifier)
            keys: set[str] = set()
            for item in current.items:
                if type(item) is not tuple or len(item) != 2:
                    _invariant_failure()
                key, child = item
                if type(key) is not str or key in keys:
                    _invariant_failure()
                keys.add(key)
                string_count = _validate_frozen_string(key, string_count)
                if not _is_frozen_json_value(child):
                    _invariant_failure()
            stack.append(("leave", current, depth))
            for _, child in reversed(current.items):
                stack.append(("value", child, depth + 1))
            continue
        _invariant_failure()


def _validate_frozen_string(value: str, current_total: int) -> int:
    if (
        len(value) > MAX_INDIVIDUAL_STRING_CODE_POINTS
        or current_total + len(value) > MAX_CUMULATIVE_STRING_CODE_POINTS
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        _invariant_failure()
    return current_total + len(value)


def _parsed_value_identity(value: FrozenApprovalIntentJsonValue) -> str:
    built_in_value = _to_exact_builtin(value)
    canonical = json.dumps(
        built_in_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(canonical) > MAX_PARSED_VALUE_CANONICAL_BYTES:
        _invariant_failure()
    return hashlib.sha256(PARSED_VALUE_IDENTITY_DOMAIN + canonical).hexdigest()


def _to_exact_builtin(value: FrozenApprovalIntentJsonValue) -> object:
    root: object = _NO_VALUE
    stack: list[tuple[object, object | None, object | None]] = [
        (value, None, None)
    ]
    while stack:
        current, parent, destination = stack.pop()
        converted: object
        if current is None or type(current) in {str, int, float, bool}:
            converted = current
        elif type(current) is FrozenApprovalIntentJsonArray:
            converted = [None] * len(current.items)
            _assign_converted(root_holder := [root], parent, destination, converted)
            root = root_holder[0]
            for index in range(len(current.items) - 1, -1, -1):
                stack.append((current.items[index], converted, index))
            continue
        elif type(current) is FrozenApprovalIntentJsonObject:
            converted = {key: None for key, _ in current.items}
            _assign_converted(root_holder := [root], parent, destination, converted)
            root = root_holder[0]
            for key, child in reversed(current.items):
                stack.append((child, converted, key))
            continue
        else:
            _invariant_failure()
        root_holder = [root]
        _assign_converted(root_holder, parent, destination, converted)
        root = root_holder[0]
    if root is _NO_VALUE:
        _invariant_failure()
    return root


def _assign_converted(
    root_holder: list[object],
    parent: object | None,
    destination: object | None,
    value: object,
) -> None:
    if parent is None:
        if root_holder[0] is not _NO_VALUE:
            _invariant_failure()
        root_holder[0] = value
        return
    if type(parent) is list and type(destination) is int:
        parent[destination] = value
        return
    if type(parent) is dict and type(destination) is str:
        parent[destination] = value
        return
    _invariant_failure()


def _invariant_failure() -> NoReturn:
    raise _ParserInvariantError(_INVARIANT_ERROR)


__all__ = [
    "AUTHENTICATION_EVALUATION_PERFORMED",
    "AUTHORITY_SCOPE",
    "FRESHNESS_EVALUATION_PERFORMED",
    "FROZEN_JSON_BOOLEAN_COERCION_ERROR",
    "FrozenApprovalIntentJsonArray",
    "FrozenApprovalIntentJsonObject",
    "FrozenApprovalIntentJsonValue",
    "INTENT_EVALUATION_PERFORMED",
    "LIFECYCLE_EVALUATION_PERFORMED",
    "MAX_APPROVAL_INTENT_STATEMENT_PARSE_DIAGNOSTICS",
    "MAX_ARRAY_ITEM_COUNT",
    "MAX_CUMULATIVE_STRING_CODE_POINTS",
    "MAX_INDIVIDUAL_STRING_CODE_POINTS",
    "MAX_JSON_NESTING_DEPTH",
    "MAX_JSON_NODE_COUNT",
    "MAX_JSON_NUMBER_TOKEN_CODE_POINTS",
    "MAX_OBJECT_MEMBER_COUNT",
    "MAX_PARSED_VALUE_CANONICAL_BYTES",
    "MAX_RAW_STATEMENT_BYTES",
    "MIN_RAW_STATEMENT_BYTES",
    "NOT_ACTIVATION_AUTHORIZATION",
    "NOT_APPROVAL_AUTHORIZATION",
    "NOT_AUTHENTICATION",
    "NOT_TRADE_AUTHORIZATION",
    "ORDER_COMPILATION_EVALUATED",
    "APPROVAL_INTENT_STATEMENT_PARSER_REVISION",
    "PARSED_VALUE_IDENTITY_DOMAIN",
    "PARSE_RESULT_BOOLEAN_COERCION_ERROR",
    "APPROVAL_INTENT_STATEMENT_RESULT_REVISION",
    "REPLAY_EVALUATION_PERFORMED",
    "STATEMENT_CONTRACT_VALIDATION_PERFORMED",
    "STATEMENT_SEMANTIC_IDENTITY_COMPUTED",
    "Step2MarketSourcePolicyOperatorApprovalIntentStatementParseDiagnostic",
    "Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult",
    "Step2MarketSourcePolicyOperatorApprovalIntentStatementParseState",
    "TRADE_PERMISSION_EFFECT",
    "WORKFLOW_PERMISSION_EVALUATED",
    "parse_step2_market_source_policy_operator_approval_intent_statement_bytes",
]
