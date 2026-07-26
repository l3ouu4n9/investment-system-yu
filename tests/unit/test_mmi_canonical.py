from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext
import hashlib
import inspect
import struct

import pytest

from investment_orchestrator.mmi.canonical import (
    MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
    canonical_json_bytes,
    domain_separated_sha256,
    normalize_decimal_string,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    MmiClockContractError,
    MmiProjectionRunContext,
    _begin_mmi_projection_run_with_clock,
    _mmi_projection_run_context_provenance_is_valid,
    begin_mmi_projection_run,
)


class _CountingClock:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls = 0

    def now_utc(self) -> object:
        self.calls += 1
        return self.value


def test_canonical_object_order_is_irrelevant_and_list_order_is_material() -> None:
    assert canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert canonical_json_bytes({"a": 1, "b": 2}) == b'{"a":1,"b":2}'
    assert canonical_json_bytes({"values": [1, 2]}) != canonical_json_bytes(
        {"values": [2, 1]}
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("12000.00"), "12000"),
        (Decimal("0.2500"), "0.25"),
        (Decimal("-0.000"), "0"),
        (0, "0"),
        ("38211.2900", "38211.29"),
    ],
)
def test_decimal_normalization_is_non_exponent_and_has_canonical_zero(
    value: Decimal | int | str,
    expected: str,
) -> None:
    assert normalize_decimal_string(value) == expected
    assert "e" not in expected.casefold()
    assert not expected.startswith("+")


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        "+1",
        "01",
        "1e2",
        1.5,
        True,
    ],
)
def test_decimal_normalization_rejects_noncanonical_or_binary_values(
    value: object,
) -> None:
    with pytest.raises(MmiCanonicalizationError):
        normalize_decimal_string(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0"), "0"),
        (Decimal("-0"), "0"),
        (Decimal("0.0"), "0"),
        (Decimal("-0.000"), "0"),
        (Decimal("1.2300"), "1.23"),
        (
            Decimal(
                "12345678901234567890123456789."
                "1234567890123456789"
            ),
            (
                "12345678901234567890123456789."
                "1234567890123456789"
            ),
        ),
        (
            Decimal("0.0000000000000000001234000"),
            "0.0000000000000000001234",
        ),
        (Decimal("1E+47"), "1" + ("0" * 47)),
        (Decimal("1E-24"), "0." + ("0" * 23) + "1"),
    ],
)
def test_decimal_normalization_is_context_independent_and_exact(
    value: Decimal,
    expected: str,
) -> None:
    observed: set[str] = set()
    for precision, rounding in (
        (3, ROUND_DOWN),
        (5, ROUND_UP),
        (28, ROUND_DOWN),
        (50, ROUND_UP),
    ):
        with localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            observed.add(normalize_decimal_string(value))
    assert observed == {expected}


@pytest.mark.parametrize(
    "value",
    [
        Decimal("1E+48"),
        Decimal("1E-25"),
        Decimal(
            "1234567890123456789012345678901234567890123456789"
            ".12345678"
        ),
    ],
)
def test_decimal_normalization_rejects_values_outside_frozen_digit_bounds(
    value: Decimal,
) -> None:
    with pytest.raises(
        MmiCanonicalizationError,
        match="MMI_DECIMAL_DIGIT_LIMIT_EXCEEDED",
    ):
        normalize_decimal_string(value)


@pytest.mark.parametrize(
    "value",
    [
        {"value": 1.5},
        {"value": float("nan")},
        {"value": Decimal("1")},
        {"value": (1, 2)},
        {"value": object()},
        {1: "not-a-string-key"},
    ],
)
def test_canonical_json_rejects_unsupported_types(value: object) -> None:
    with pytest.raises(MmiCanonicalizationError):
        canonical_json_bytes(value)


def test_canonical_json_rejects_cycles_and_enforces_exact_byte_bound() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(
        MmiCanonicalizationError,
        match="MMI_CANONICAL_CYCLE_PROHIBITED",
    ):
        canonical_json_bytes(cyclic)

    encoded = canonical_json_bytes({"padding": "x" * 20})
    assert canonical_json_bytes(
        {"padding": "x" * 20},
        maximum_bytes=len(encoded),
    ) == encoded
    with pytest.raises(
        MmiCanonicalizationError,
        match="MMI_CANONICAL_SIZE_EXCEEDED",
    ):
        canonical_json_bytes(
            {"padding": "x" * 20},
            maximum_bytes=len(encoded) - 1,
        )


def test_domain_hash_is_ascii_domain_length_and_canonical_payload() -> None:
    value = {"b": [2, 1], "a": "value"}
    canonical = canonical_json_bytes(value)
    expected = hashlib.sha256(
        MMI_SOURCE_RECORD_IDENTITY_DOMAIN
        + struct.pack(">Q", len(canonical))
        + canonical
    ).hexdigest()
    assert (
        domain_separated_sha256(
            MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
            value,
        )
        == expected
    )


def test_all_p1a_domains_are_unique_ascii_and_nul_terminated() -> None:
    domains = (
        MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
        MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
    )
    assert len(domains) == len(set(domains)) == 3
    assert all(
        domain.endswith(b"\0")
        and b"\0" not in domain[:-1]
        and domain.decode("ascii")
        for domain in domains
    )
    value = {"same": "payload"}
    assert len({domain_separated_sha256(domain, value) for domain in domains}) == 3


def test_record_identity_excludes_only_its_own_identity_field() -> None:
    record = {
        "schema_version": "mmi_source_record_v1",
        "value": 1,
        "source_record_identity_sha256": "0" * 64,
    }
    identity = record_identity_sha256(
        record,
        identity_field="source_record_identity_sha256",
        domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    )
    record["source_record_identity_sha256"] = "f" * 64
    assert (
        record_identity_sha256(
            record,
            identity_field="source_record_identity_sha256",
            domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        )
        == identity
    )
    record["value"] = 2
    assert (
        record_identity_sha256(
            record,
            identity_field="source_record_identity_sha256",
            domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        )
        != identity
    )


def test_projection_run_reads_clock_once_and_freezes_microsecond_utc() -> None:
    observed = datetime(2026, 7, 25, 12, 34, 56, 123, tzinfo=timezone.utc)
    clock = _CountingClock(observed)
    context = _begin_mmi_projection_run_with_clock(clock)
    assert clock.calls == 1
    assert context.evaluation_time_utc == observed
    assert context.evaluation_timestamp_utc == "2026-07-25T12:34:56.000123Z"
    assert context.authority_effect == "NONE"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (
            datetime(2026, 7, 25, 12, 0),
            "MMI_CLOCK_TIMESTAMP_NAIVE",
        ),
        (
            datetime(
                2026,
                7,
                25,
                12,
                0,
                tzinfo=timezone(timedelta(hours=-7)),
            ),
            "MMI_CLOCK_TIMESTAMP_NOT_UTC",
        ),
        ("not-a-datetime", "MMI_CLOCK_RESULT_INVALID"),
    ],
)
def test_projection_run_rejects_invalid_clock_results(
    value: object,
    code: str,
) -> None:
    clock = _CountingClock(value)
    with pytest.raises(MmiClockContractError, match=code):
        _begin_mmi_projection_run_with_clock(clock)
    assert clock.calls == 1


def test_projection_run_clock_failure_creates_no_context() -> None:
    class FailingClock:
        def now_utc(self) -> datetime:
            raise RuntimeError("raw clock error must not escape")

    with pytest.raises(MmiClockContractError, match="MMI_CLOCK_READ_FAILED"):
        _begin_mmi_projection_run_with_clock(FailingClock())


def test_production_clock_surface_has_no_operator_timestamp_override() -> None:
    assert tuple(inspect.signature(begin_mmi_projection_run).parameters) == ()
    with pytest.raises(TypeError):
        MmiProjectionRunContext(
            evaluation_time_utc=datetime(
                2026,
                7,
                25,
                12,
                tzinfo=timezone.utc,
            ),
            evaluation_timestamp_utc="2026-07-25T12:00:00.000000Z",
        )
    context = _begin_mmi_projection_run_with_clock(
        _CountingClock(
            datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
        )
    )
    assert _mmi_projection_run_context_provenance_is_valid(context)


def test_projection_run_context_provenance_rejects_replace_and_forgery() -> None:
    observed = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    context = _begin_mmi_projection_run_with_clock(_CountingClock(observed))

    replacements = (
        {
            "evaluation_time_utc": observed
            + timedelta(microseconds=1),
        },
        {
            "evaluation_timestamp_utc": (
                "2026-07-25T12:00:00.000001Z"
            ),
        },
        {
            "evaluation_time_utc": observed
            + timedelta(microseconds=1),
            "evaluation_timestamp_utc": (
                "2026-07-25T12:00:00.000001Z"
            ),
        },
    )
    for changes in replacements:
        with pytest.raises(TypeError):
            replace(context, **changes)

    forged = object.__new__(MmiProjectionRunContext)
    object.__setattr__(forged, "evaluation_time_utc", observed)
    object.__setattr__(
        forged,
        "evaluation_timestamp_utc",
        "2026-07-25T12:00:00.000000Z",
    )
    object.__setattr__(forged, "authority_effect", "NONE")
    object.__setattr__(forged, "_provenance_seal", b"\x00" * 32)
    assert not _mmi_projection_run_context_provenance_is_valid(forged)

    reconstructed = object.__new__(MmiProjectionRunContext)
    object.__setattr__(
        reconstructed,
        "evaluation_time_utc",
        context.evaluation_time_utc,
    )
    object.__setattr__(
        reconstructed,
        "evaluation_timestamp_utc",
        context.evaluation_timestamp_utc,
    )
    object.__setattr__(
        reconstructed,
        "authority_effect",
        context.authority_effect,
    )
    object.__setattr__(
        reconstructed,
        "_provenance_token",
        context._provenance_token,
    )
    object.__setattr__(
        reconstructed,
        "_provenance_seal",
        context._provenance_seal,
    )
    assert not _mmi_projection_run_context_provenance_is_valid(
        reconstructed
    )


def test_unchanged_projection_run_context_copy_preserves_provenance() -> None:
    context = _begin_mmi_projection_run_with_clock(
        _CountingClock(
            datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
        )
    )
    shallow = copy.copy(context)
    deep = copy.deepcopy(context)
    assert shallow is context
    assert deep is context
    assert _mmi_projection_run_context_provenance_is_valid(shallow)
    assert _mmi_projection_run_context_provenance_is_valid(deep)
