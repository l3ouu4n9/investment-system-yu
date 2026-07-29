"""MMI-only canonical JSON and domain-separated identity helpers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import struct
from typing import Final


MMI_SOURCE_RECORD_IDENTITY_DOMAIN: Final = b"mmi_source_record_v1\0"
MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN: Final = (
    b"mmi_universe_projection_v1\0"
)
MMI_POLICY_PROJECTION_IDENTITY_DOMAIN: Final = b"mmi_policy_projection_v1\0"
MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN: Final = (
    b"mmi_portfolio_snapshot_projection_v1\0"
)
MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN: Final = (
    b"mmi_authenticated_evidence_bundle_v1\0"
)
_MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_IDENTITY_DOMAIN: Final = (
    b"mmi_analyst_visible_evidence_view_v1\0"
)
_MMI_GROUNDED_PROMPT_CONTEXT_BINDING_DOMAIN: Final = (
    b"mmi_grounded_prompt_context_binding_v1\0"
)
_MMI_GROUNDED_PROMPT_ARTIFACT_IDENTITY_DOMAIN: Final = (
    b"mmi_grounded_prompt_artifact_v1\0"
)
_MMI_RAW_RESPONSE_ENVELOPE_IDENTITY_DOMAIN: Final = (
    b"mmi_raw_response_envelope_v1\0"
)

MAXIMUM_CANONICAL_JSON_BYTES: Final = 1_048_576
MAXIMUM_AUTHENTICATED_EVIDENCE_BUNDLE_CANONICAL_BYTES: Final = 2_048
MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES: Final = 47_584
MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES: Final = 65_536
MAXIMUM_MMI_RAW_RESPONSE_BYTES: Final = 262_144
_MAXIMUM_GROUNDED_PROMPT_CANONICAL_BYTES: Final = 65_536
MAXIMUM_CANONICAL_DEPTH: Final = 32
MAXIMUM_CANONICAL_NODES: Final = 16_384
MAXIMUM_DECIMAL_INTEGRAL_DIGITS: Final = 48
MAXIMUM_DECIMAL_FRACTIONAL_DIGITS: Final = 24
MAXIMUM_DECIMAL_TOTAL_DIGITS: Final = 56
MAXIMUM_DECIMAL_RENDERED_CHARACTERS: Final = 64

_DECIMAL_TEXT_RE: Final = re.compile(
    r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
)


class MmiCanonicalizationError(ValueError):
    """Raised when a value cannot enter an MMI identity preimage."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_decimal_string(value: Decimal | int | str) -> str:
    """Render a bounded decimal exactly without ambient Decimal context."""
    if type(value) is bool:
        raise MmiCanonicalizationError("MMI_DECIMAL_TYPE_INVALID")
    if type(value) is int:
        if abs(value) >= 10**MAXIMUM_DECIMAL_INTEGRAL_DIGITS:
            raise MmiCanonicalizationError(
                "MMI_DECIMAL_DIGIT_LIMIT_EXCEEDED"
            )
        decimal_value = Decimal(value)
    elif type(value) is Decimal:
        decimal_value = value
    elif type(value) is str:
        if not _DECIMAL_TEXT_RE.fullmatch(value):
            raise MmiCanonicalizationError("MMI_DECIMAL_TEXT_INVALID")
        try:
            decimal_value = Decimal(value)
        except InvalidOperation:
            raise MmiCanonicalizationError("MMI_DECIMAL_TEXT_INVALID") from None
    else:
        raise MmiCanonicalizationError("MMI_DECIMAL_TYPE_INVALID")
    if not decimal_value.is_finite():
        raise MmiCanonicalizationError("MMI_DECIMAL_NONFINITE")

    sign, coefficient, exponent = decimal_value.as_tuple()
    if type(exponent) is not int:
        raise MmiCanonicalizationError("MMI_DECIMAL_NONFINITE")
    if not coefficient or all(digit == 0 for digit in coefficient):
        return "0"

    significant_end = len(coefficient)
    while coefficient[significant_end - 1] == 0:
        significant_end -= 1
        exponent += 1
    if significant_end > MAXIMUM_DECIMAL_TOTAL_DIGITS:
        raise MmiCanonicalizationError("MMI_DECIMAL_DIGIT_LIMIT_EXCEEDED")

    if exponent >= 0:
        integral_digits = significant_end + exponent
        fractional_digits = 0
    else:
        decimal_position = significant_end + exponent
        if decimal_position > 0:
            integral_digits = decimal_position
            fractional_digits = -exponent
        else:
            integral_digits = 1
            fractional_digits = -exponent
    if (
        integral_digits > MAXIMUM_DECIMAL_INTEGRAL_DIGITS
        or fractional_digits > MAXIMUM_DECIMAL_FRACTIONAL_DIGITS
        or integral_digits + fractional_digits
        > MAXIMUM_DECIMAL_TOTAL_DIGITS
    ):
        raise MmiCanonicalizationError("MMI_DECIMAL_DIGIT_LIMIT_EXCEEDED")

    coefficient_text = "".join(
        str(digit) for digit in coefficient[:significant_end]
    )
    if exponent >= 0:
        integral = coefficient_text + ("0" * exponent)
        fractional = ""
    else:
        decimal_position = len(coefficient_text) + exponent
        if decimal_position > 0:
            integral = coefficient_text[:decimal_position]
            fractional = coefficient_text[decimal_position:]
        else:
            integral = "0"
            fractional = ("0" * (-decimal_position)) + coefficient_text

    integral = integral.lstrip("0") or "0"
    fractional = fractional.rstrip("0")
    rendered_integral_digits = len(integral)
    rendered_fractional_digits = len(fractional)
    total_digits = rendered_integral_digits + rendered_fractional_digits
    if (
        rendered_integral_digits > MAXIMUM_DECIMAL_INTEGRAL_DIGITS
        or rendered_fractional_digits
        > MAXIMUM_DECIMAL_FRACTIONAL_DIGITS
        or total_digits > MAXIMUM_DECIMAL_TOTAL_DIGITS
    ):
        raise MmiCanonicalizationError("MMI_DECIMAL_DIGIT_LIMIT_EXCEEDED")

    normalized = integral
    if fractional:
        normalized += f".{fractional}"
    if sign:
        normalized = f"-{normalized}"
    if (
        len(normalized) > MAXIMUM_DECIMAL_RENDERED_CHARACTERS
        or not _DECIMAL_TEXT_RE.fullmatch(normalized)
    ):
        raise MmiCanonicalizationError("MMI_DECIMAL_NORMALIZATION_FAILED")
    return normalized


def _snapshot_canonical_value(
    value: object,
    *,
    depth: int,
    node_counter: list[int],
    active_containers: set[int],
) -> object:
    if depth > MAXIMUM_CANONICAL_DEPTH:
        raise MmiCanonicalizationError("MMI_CANONICAL_DEPTH_EXCEEDED")
    node_counter[0] += 1
    if node_counter[0] > MAXIMUM_CANONICAL_NODES:
        raise MmiCanonicalizationError("MMI_CANONICAL_NODE_COUNT_EXCEEDED")
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        raise MmiCanonicalizationError("MMI_CANONICAL_FLOAT_PROHIBITED")
    if type(value) is list:
        identity = id(value)
        if identity in active_containers:
            raise MmiCanonicalizationError("MMI_CANONICAL_CYCLE_PROHIBITED")
        active_containers.add(identity)
        try:
            return [
                _snapshot_canonical_value(
                    item,
                    depth=depth + 1,
                    node_counter=node_counter,
                    active_containers=active_containers,
                )
                for item in value
            ]
        finally:
            active_containers.remove(identity)
    if type(value) is dict:
        identity = id(value)
        if identity in active_containers:
            raise MmiCanonicalizationError("MMI_CANONICAL_CYCLE_PROHIBITED")
        active_containers.add(identity)
        try:
            if any(type(key) is not str for key in value):
                raise MmiCanonicalizationError(
                    "MMI_CANONICAL_OBJECT_KEY_INVALID"
                )
            return {
                key: _snapshot_canonical_value(
                    item,
                    depth=depth + 1,
                    node_counter=node_counter,
                    active_containers=active_containers,
                )
                for key, item in value.items()
            }
        finally:
            active_containers.remove(identity)
    raise MmiCanonicalizationError("MMI_CANONICAL_TYPE_UNSUPPORTED")


def canonical_json_bytes(
    value: object,
    *,
    maximum_bytes: int = MAXIMUM_CANONICAL_JSON_BYTES,
) -> bytes:
    """Encode one bounded native JSON value under the frozen MMI profile."""
    if type(maximum_bytes) is not int or maximum_bytes < 1:
        raise MmiCanonicalizationError("MMI_CANONICAL_BOUND_INVALID")
    snapshot = _snapshot_canonical_value(
        value,
        depth=0,
        node_counter=[0],
        active_containers=set(),
    )
    try:
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (UnicodeEncodeError, ValueError):
        raise MmiCanonicalizationError("MMI_CANONICAL_ENCODING_FAILED") from None
    if len(encoded) > maximum_bytes:
        raise MmiCanonicalizationError("MMI_CANONICAL_SIZE_EXCEEDED")
    return encoded


def domain_separated_sha256(
    domain: bytes,
    value: object,
    *,
    maximum_bytes: int = MAXIMUM_CANONICAL_JSON_BYTES,
) -> str:
    """Hash a length-delimited canonical JSON payload in one ASCII domain."""
    if (
        type(domain) is not bytes
        or not domain
        or not domain.endswith(b"\0")
        or b"\0" in domain[:-1]
    ):
        raise MmiCanonicalizationError("MMI_IDENTITY_DOMAIN_INVALID")
    try:
        domain.decode("ascii")
    except UnicodeDecodeError:
        raise MmiCanonicalizationError("MMI_IDENTITY_DOMAIN_INVALID") from None
    canonical = canonical_json_bytes(value, maximum_bytes=maximum_bytes)
    material = domain + struct.pack(">Q", len(canonical)) + canonical
    return hashlib.sha256(material).hexdigest()


def record_identity_sha256(
    record: dict[str, object],
    *,
    identity_field: str,
    domain: bytes,
    maximum_bytes: int = MAXIMUM_CANONICAL_JSON_BYTES,
) -> str:
    """Hash a record after excluding only its own self-identity field."""
    if type(record) is not dict or type(identity_field) is not str:
        raise MmiCanonicalizationError("MMI_IDENTITY_RECORD_INVALID")
    preimage = dict(record)
    preimage.pop(identity_field, None)
    return domain_separated_sha256(
        domain,
        preimage,
        maximum_bytes=maximum_bytes,
    )
