"""Pure structural validation for Step 2 market-observations v2 values.

This module validates only the closed shape and exact JSON-native types of
reported observations.  It deliberately performs no freshness evaluation,
source verification, universe resolution, readiness decision, permission
lookup, publication decision, workflow transition, or order action.

The optional fractional-second rule is canonical and explicit: reported UTC
timestamps may omit a fraction or contain one through six decimal digits, and
must otherwise be an RFC 3339 timestamp ending in uppercase ``Z``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, validators


MARKET_OBSERVATIONS_SCHEMA_VERSION = "step2_market_observations_v2"
MARKET_OBSERVATIONS_VALIDATION_RESULT_VERSION = (
    "step2_market_observations_validation_result_v1"
)
MARKET_OBSERVATIONS_SCHEMA_FILENAME = "step2_market_observations.schema.json"

MAX_CANONICAL_BYTES = 262144
MAX_JSON_NESTING_DEPTH = 32
MAX_JSON_NODE_COUNT = 4096

IDENTITY_ONLY = True
NOT_AUTHORIZATION = True
PERMISSION_EFFECT_NONE = "none"
SEMANTIC_VALIDATION_PERFORMED = False
FRESHNESS_EVALUATION_PERFORMED = False
UNIVERSE_RESOLUTION_PERFORMED = False

VALIDATION_BOOLEAN_COERCION_ERROR = (
    "inspect structure_valid and schema_valid explicitly; "
    "market-observations validation results have no truth value"
)

REPORTED_ISSUE_CODES = (
    "MISSING_LAST_CLOSE_CLAIM",
    "MISSING_PRICE_DATE_CLAIM",
    "MISSING_CLOSE_SOURCE_CLAIM",
    "MISSING_TECHNICALS_CLAIM",
    "MISSING_TECHNICAL_SOURCE_CLAIM",
    "MISSING_RETRIEVAL_TIMESTAMP_CLAIM",
    "STALE_DATA_CLAIM",
    "FUTURE_DATED_DATA_CLAIM",
    "SOURCE_CONFLICT_CLAIM",
    "OTHER_REPORTED_ISSUE",
)

_STRICT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STRICT_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)

_ROW_REQUIRED_FIELDS = (
    "ticker",
    "last_close",
    "reported_price_asof",
    "atr_20_abs",
    "atr_20_30d_pct",
    "ma50",
    "ma200",
    "avg_volume_3m",
    "week_52_low",
    "week_52_high",
    "reported_last_close_source",
    "reported_price_source",
    "reported_technicals_source",
    "reported_retrieved_at_utc",
    "source_evidence_refs",
    "reported_issue_codes",
    "observation_notes",
)

_NULLABLE_STRICTLY_POSITIVE_NUMBER_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "number", "exclusiveMinimum": 0},
        {"type": "null"},
    ]
}
_NULLABLE_NONNEGATIVE_NUMBER_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "number", "minimum": 0},
        {"type": "null"},
    ]
}
_NULLABLE_REPORTED_SOURCE_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "string", "minLength": 1, "maxLength": 64},
        {"type": "null"},
    ]
}
_NULLABLE_REPORTED_DATE_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {
            "type": "string",
            "pattern": r"^\d{4}-\d{2}-\d{2}$",
            "format": "date",
        },
        {"type": "null"},
    ]
}
_NULLABLE_REPORTED_UTC_TIMESTAMP_SCHEMA: dict[str, Any] = {
    "description": (
        "UTC RFC 3339 claim with uppercase T/Z and either no fraction or "
        "1-6 fractional-second digits."
    ),
    "anyOf": [
        {
            "type": "string",
            "pattern": (
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
                r"(?:\.\d{1,6})?Z$"
            ),
            "format": "date-time",
        },
        {"type": "null"},
    ],
}

_STEP2_MARKET_OBSERVATIONS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": (
        "https://investment-system.local/schemas/"
        "step2_market_observations.schema.json"
    ),
    "title": "Step 2 Market Observations v2",
    "description": (
        "Closed structural contract for reported market observations. "
        "Structural validity is identity-only evidence and does not establish "
        "freshness, usability, universe membership, readiness, permission, "
        "publication eligibility, order eligibility, or execution safety."
    ),
    "type": "object",
    "required": ["schema_version", "observations"],
    "properties": {
        "schema_version": {"const": MARKET_OBSERVATIONS_SCHEMA_VERSION},
        "observations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 128,
            "items": {"$ref": "#/$defs/observation"},
        },
    },
    "additionalProperties": False,
    "$defs": {
        "nullable_strictly_positive_number": (
            _NULLABLE_STRICTLY_POSITIVE_NUMBER_SCHEMA
        ),
        "nullable_nonnegative_number": _NULLABLE_NONNEGATIVE_NUMBER_SCHEMA,
        "nullable_reported_source": _NULLABLE_REPORTED_SOURCE_SCHEMA,
        "nullable_reported_date": _NULLABLE_REPORTED_DATE_SCHEMA,
        "nullable_reported_utc_timestamp": (
            _NULLABLE_REPORTED_UTC_TIMESTAMP_SCHEMA
        ),
        "observation": {
            "type": "object",
            "required": list(_ROW_REQUIRED_FIELDS),
            "properties": {
                "ticker": {
                    "type": "string",
                    "pattern": r"^[A-Z][A-Z0-9.-]{0,9}$",
                },
                "last_close": {
                    "$ref": "#/$defs/nullable_strictly_positive_number"
                },
                "reported_price_asof": {
                    "$ref": "#/$defs/nullable_reported_date"
                },
                "atr_20_abs": {
                    "$ref": "#/$defs/nullable_nonnegative_number"
                },
                "atr_20_30d_pct": {
                    "$ref": "#/$defs/nullable_nonnegative_number"
                },
                "ma50": {
                    "$ref": "#/$defs/nullable_strictly_positive_number"
                },
                "ma200": {
                    "$ref": "#/$defs/nullable_strictly_positive_number"
                },
                "avg_volume_3m": {
                    "anyOf": [
                        {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 9007199254740991,
                        },
                        {"type": "null"},
                    ]
                },
                "week_52_low": {
                    "$ref": "#/$defs/nullable_strictly_positive_number"
                },
                "week_52_high": {
                    "$ref": "#/$defs/nullable_strictly_positive_number"
                },
                "reported_last_close_source": {
                    "$ref": "#/$defs/nullable_reported_source"
                },
                "reported_price_source": {
                    "$ref": "#/$defs/nullable_reported_source"
                },
                "reported_technicals_source": {
                    "$ref": "#/$defs/nullable_reported_source"
                },
                "reported_retrieved_at_utc": {
                    "$ref": "#/$defs/nullable_reported_utc_timestamp"
                },
                "source_evidence_refs": {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": 64,
                    "uniqueItems": True,
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                    },
                },
                "reported_issue_codes": {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": 16,
                    "uniqueItems": True,
                    "items": {
                        "type": "string",
                        "enum": list(REPORTED_ISSUE_CODES),
                    },
                },
                "observation_notes": {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": 64,
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4096,
                    },
                },
            },
            "additionalProperties": False,
        },
    },
}


class Step2MarketObservationsDiagnostic(str, Enum):
    """Closed diagnostics for the pure structural contract."""

    MARKET_OBSERVATIONS_MISSING = "market_observations_missing"
    MARKET_OBSERVATIONS_STRUCTURE_INVALID = (
        "market_observations_structure_invalid"
    )
    MARKET_OBSERVATIONS_SIZE_EXCEEDED = "market_observations_size_exceeded"
    MARKET_OBSERVATIONS_VERSION_INVALID = "market_observations_version_invalid"
    MARKET_OBSERVATIONS_SCHEMA_INVALID = "market_observations_schema_invalid"


@dataclass(frozen=True, slots=True)
class Step2MarketObservationsValidationResult:
    """Immutable, non-authorizing result of structural validation."""

    structure_valid: bool
    schema_valid: bool
    canonical_identity_sha256: str | None
    canonical_size_bytes: int | None
    diagnostics: tuple[Step2MarketObservationsDiagnostic, ...]
    result_version: str = field(
        default=MARKET_OBSERVATIONS_VALIDATION_RESULT_VERSION,
        init=False,
    )
    schema_version: str = field(
        default=MARKET_OBSERVATIONS_SCHEMA_VERSION,
        init=False,
    )
    identity_only: bool = field(default=IDENTITY_ONLY, init=False)
    not_authorization: bool = field(default=NOT_AUTHORIZATION, init=False)
    permission_effect: str = field(default=PERMISSION_EFFECT_NONE, init=False)
    semantic_validation_performed: bool = field(
        default=SEMANTIC_VALIDATION_PERFORMED,
        init=False,
    )
    freshness_evaluation_performed: bool = field(
        default=FRESHNESS_EVALUATION_PERFORMED,
        init=False,
    )
    universe_resolution_performed: bool = field(
        default=UNIVERSE_RESOLUTION_PERFORMED,
        init=False,
    )

    def __bool__(self) -> bool:
        """Reject ambiguous success/failure coercion by every caller."""
        raise TypeError(VALIDATION_BOOLEAN_COERCION_ERROR)


class _StructureInvalid(ValueError):
    """Internal bounded signal for unsupported or excessive JSON structure."""


class _CanonicalSizeExceeded(ValueError):
    """Internal bounded signal for canonical output beyond the byte contract."""


def _is_exact_object(_: Any, instance: Any) -> bool:
    return type(instance) is dict


def _is_exact_array(_: Any, instance: Any) -> bool:
    return type(instance) is list


def _is_exact_string(_: Any, instance: Any) -> bool:
    return type(instance) is str


def _is_exact_boolean(_: Any, instance: Any) -> bool:
    return type(instance) is bool


def _is_exact_integer(_: Any, instance: Any) -> bool:
    return type(instance) is int


def _is_exact_finite_number(_: Any, instance: Any) -> bool:
    return type(instance) is int or (
        type(instance) is float and math.isfinite(instance)
    )


def _is_exact_null(_: Any, instance: Any) -> bool:
    return instance is None


_EXACT_TYPE_CHECKER = Draft202012Validator.TYPE_CHECKER.redefine_many(
    {
        "object": _is_exact_object,
        "array": _is_exact_array,
        "string": _is_exact_string,
        "boolean": _is_exact_boolean,
        "integer": _is_exact_integer,
        "number": _is_exact_finite_number,
        "null": _is_exact_null,
    }
)
_ExactDraft202012Validator = validators.extend(
    Draft202012Validator,
    type_checker=_EXACT_TYPE_CHECKER,
)
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date")
def _is_strict_calendar_date(value: Any) -> bool:
    if type(value) is not str or _STRICT_DATE_RE.fullmatch(value) is None:
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value


@_FORMAT_CHECKER.checks("date-time")
def _is_strict_utc_timestamp(value: Any) -> bool:
    if type(value) is not str or _STRICT_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(None)


_ExactDraft202012Validator.check_schema(_STEP2_MARKET_OBSERVATIONS_SCHEMA)
_SCHEMA_VALIDATOR = _ExactDraft202012Validator(
    _STEP2_MARKET_OBSERVATIONS_SCHEMA,
    format_checker=_FORMAT_CHECKER,
)


def validate_step2_market_observations(
    value: object,
) -> Step2MarketObservationsValidationResult:
    """Validate one caller value under the pure v2 structural contract.

    Diagnostic priority is missing, structural/type/depth/node/cycle, canonical
    size, schema version, then all remaining schema defects.  No caller text or
    dependency exception detail is returned.
    """
    if value is None:
        return _failure(
            Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_MISSING,
            structure_valid=False,
        )

    try:
        snapshot = _snapshot_json_value(value)
    except _StructureInvalid:
        return _failure(
            Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
            structure_valid=False,
        )

    try:
        canonical_bytes = _iterative_canonical_json_bytes(snapshot)
    except _CanonicalSizeExceeded:
        return _failure(
            Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SIZE_EXCEEDED,
            structure_valid=True,
            canonical_size_bytes=MAX_CANONICAL_BYTES + 1,
        )
    except (UnicodeEncodeError, ValueError):
        return _failure(
            Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
            structure_valid=False,
        )

    canonical_size = len(canonical_bytes)
    canonical_identity = hashlib.sha256(canonical_bytes).hexdigest()

    if (
        type(snapshot) is dict
        and snapshot.get("schema_version") != MARKET_OBSERVATIONS_SCHEMA_VERSION
    ):
        return _failure(
            Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_VERSION_INVALID,
            structure_valid=True,
            canonical_identity_sha256=canonical_identity,
            canonical_size_bytes=canonical_size,
        )

    try:
        schema_valid = _SCHEMA_VALIDATOR.is_valid(snapshot)
    except (TypeError, ValueError, RecursionError):
        schema_valid = False
    if not schema_valid:
        return _failure(
            Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
            structure_valid=True,
            canonical_identity_sha256=canonical_identity,
            canonical_size_bytes=canonical_size,
        )

    return Step2MarketObservationsValidationResult(
        structure_valid=True,
        schema_valid=True,
        canonical_identity_sha256=canonical_identity,
        canonical_size_bytes=canonical_size,
        diagnostics=(),
    )


def _failure(
    diagnostic: Step2MarketObservationsDiagnostic,
    *,
    structure_valid: bool,
    canonical_identity_sha256: str | None = None,
    canonical_size_bytes: int | None = None,
) -> Step2MarketObservationsValidationResult:
    return Step2MarketObservationsValidationResult(
        structure_valid=structure_valid,
        schema_valid=False,
        canonical_identity_sha256=canonical_identity_sha256,
        canonical_size_bytes=canonical_size_bytes,
        diagnostics=(diagnostic,),
    )


def _snapshot_json_value(value: Any) -> Any:
    """Iteratively copy one exact JSON-native value under contract bounds."""
    root: list[Any] = [None]
    active_container_ids: set[int] = set()
    node_count = 0
    # Frames are operation, source value, destination container, slot, depth.
    stack: list[tuple[str, Any, Any, Any, int]] = [
        ("visit", value, root, 0, 0)
    ]

    while stack:
        operation, source, destination, slot, depth = stack.pop()
        if operation == "leave":
            active_container_ids.remove(id(source))
            continue

        node_count += 1
        if node_count > MAX_JSON_NODE_COUNT or depth > MAX_JSON_NESTING_DEPTH:
            raise _StructureInvalid from None

        if source is None or type(source) in {bool, str, int}:
            destination[slot] = source
            continue
        if type(source) is float:
            if not math.isfinite(source):
                raise _StructureInvalid from None
            destination[slot] = source
            continue
        if type(source) not in {dict, list}:
            raise _StructureInvalid from None

        source_id = id(source)
        if source_id in active_container_ids:
            raise _StructureInvalid from None
        active_container_ids.add(source_id)
        stack.append(("leave", source, None, None, depth))

        if node_count + len(source) > MAX_JSON_NODE_COUNT:
            raise _StructureInvalid from None

        if type(source) is dict:
            shallow_source = source.copy()
            if any(type(key) is not str for key in shallow_source):
                raise _StructureInvalid from None
            snapshot_object: dict[str, Any] = {}
            destination[slot] = snapshot_object
            keys = list(shallow_source)
            for key in reversed(keys):
                stack.append(
                    (
                        "visit",
                        shallow_source[key],
                        snapshot_object,
                        key,
                        depth + 1,
                    )
                )
            continue

        shallow_array = source.copy()
        snapshot_array: list[Any] = [None] * len(shallow_array)
        destination[slot] = snapshot_array
        for index in range(len(shallow_array) - 1, -1, -1):
            stack.append(
                (
                    "visit",
                    shallow_array[index],
                    snapshot_array,
                    index,
                    depth + 1,
                )
            )

    return root[0]


def _iterative_canonical_json_bytes(value: Any) -> bytes:
    """Serialize a validated strict JSON tree without container recursion."""
    output = bytearray()
    stack: list[tuple[str, Any]] = [("value", value)]

    def append(fragment: bytes) -> None:
        if len(output) + len(fragment) > MAX_CANONICAL_BYTES:
            raise _CanonicalSizeExceeded from None
        output.extend(fragment)

    while stack:
        operation, current = stack.pop()
        if operation == "raw":
            append(current)
            continue

        if current is None:
            append(b"null")
            continue
        if type(current) is bool:
            append(b"true" if current else b"false")
            continue
        if type(current) is int:
            append(_integer_json_bytes(current))
            continue
        if type(current) is float:
            if not math.isfinite(current):
                raise ValueError from None
            append(json.dumps(current, allow_nan=False).encode("ascii"))
            continue
        if type(current) is str:
            if len(current) > MAX_CANONICAL_BYTES:
                raise _CanonicalSizeExceeded from None
            append(
                json.dumps(
                    current,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            continue

        if type(current) is list:
            append(b"[")
            stack.append(("raw", b"]"))
            for index in range(len(current) - 1, -1, -1):
                stack.append(("value", current[index]))
                if index != 0:
                    stack.append(("raw", b","))
            continue

        if type(current) is dict:
            append(b"{")
            stack.append(("raw", b"}"))
            keys = sorted(current)
            for index in range(len(keys) - 1, -1, -1):
                key = keys[index]
                if type(key) is not str:
                    raise ValueError from None
                stack.append(("value", current[key]))
                stack.append(("raw", b":"))
                stack.append(("value", key))
                if index != 0:
                    stack.append(("raw", b","))
            continue

        raise ValueError from None

    return bytes(output)


def _integer_json_bytes(value: int) -> bytes:
    """Encode an arbitrary Python integer without the interpreter digit cap."""
    if value == 0:
        return b"0"

    negative = value < 0
    magnitude = -value if negative else value
    # This conservative lower bound prevents expensive conversion of an
    # integer that cannot possibly fit in the canonical-byte contract.
    lower_digit_bound = (
        ((magnitude.bit_length() - 1) * 30102) // 100000
    ) + 1
    if lower_digit_bound + int(negative) > MAX_CANONICAL_BYTES:
        raise _CanonicalSizeExceeded from None

    chunks: list[int] = []
    while magnitude:
        magnitude, remainder = divmod(magnitude, 1_000_000_000)
        chunks.append(remainder)

    encoded = bytearray(b"-" if negative else b"")
    encoded.extend(str(chunks.pop()).encode("ascii"))
    while chunks:
        encoded.extend(f"{chunks.pop():09d}".encode("ascii"))
        if len(encoded) > MAX_CANONICAL_BYTES:
            raise _CanonicalSizeExceeded from None
    return bytes(encoded)


__all__ = [
    "FRESHNESS_EVALUATION_PERFORMED",
    "IDENTITY_ONLY",
    "MARKET_OBSERVATIONS_SCHEMA_FILENAME",
    "MARKET_OBSERVATIONS_SCHEMA_VERSION",
    "MARKET_OBSERVATIONS_VALIDATION_RESULT_VERSION",
    "MAX_CANONICAL_BYTES",
    "MAX_JSON_NESTING_DEPTH",
    "MAX_JSON_NODE_COUNT",
    "NOT_AUTHORIZATION",
    "PERMISSION_EFFECT_NONE",
    "REPORTED_ISSUE_CODES",
    "SEMANTIC_VALIDATION_PERFORMED",
    "UNIVERSE_RESOLUTION_PERFORMED",
    "VALIDATION_BOOLEAN_COERCION_ERROR",
    "Step2MarketObservationsDiagnostic",
    "Step2MarketObservationsValidationResult",
    "validate_step2_market_observations",
]
