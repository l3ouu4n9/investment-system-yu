from __future__ import annotations

import ast
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator
import pytest

import investment_orchestrator.validators.validate_step2_market_observations as market_observations
from investment_orchestrator.validators.validate_step2_market_observations import (
    FRESHNESS_EVALUATION_PERFORMED,
    IDENTITY_ONLY,
    MARKET_OBSERVATIONS_SCHEMA_FILENAME,
    MARKET_OBSERVATIONS_SCHEMA_VERSION,
    MAX_CANONICAL_BYTES,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODE_COUNT,
    NOT_AUTHORIZATION,
    PERMISSION_EFFECT_NONE,
    REPORTED_ISSUE_CODES,
    SEMANTIC_VALIDATION_PERFORMED,
    UNIVERSE_RESOLUTION_PERFORMED,
    VALIDATION_BOOLEAN_COERCION_ERROR,
    Step2MarketObservationsDiagnostic,
    validate_step2_market_observations,
)


ROW_FIELDS = (
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

NULLABLE_FIELDS = (
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
)

STRICTLY_POSITIVE_FIELDS = (
    "last_close",
    "ma50",
    "ma200",
    "week_52_low",
    "week_52_high",
)

NONNEGATIVE_FIELDS = (
    "atr_20_abs",
    "atr_20_30d_pct",
)

FORBIDDEN_AUTHORITY_FIELDS = (
    "freshness_ok",
    "data_gap",
    "data_gap_reason",
    "same_day_close_required",
    "holiday_resolution_ok",
    "market_data_target_close_date_et",
    "execution_date_et",
    "run_timestamp_et",
    "primary_source",
    "fallback_source",
    "market_data_usable",
    "candidate_valid",
    "ready",
    "permission",
    "allowed_actions",
    "gate_result",
    "publication_eligible",
    "order_compilation_allowed",
    "broker",
    "broker_route",
    "executable_order",
    "order_type",
    "order_quantity",
    "submit_order",
    "time_in_force",
)


def _valid_row(*, ticker: str = "QQQ") -> dict[str, Any]:
    return {
        "ticker": ticker,
        "last_close": 500.25,
        "reported_price_asof": "2026-07-13",
        "atr_20_abs": 7.5,
        "atr_20_30d_pct": 1.5,
        "ma50": 490.0,
        "ma200": 450.0,
        "avg_volume_3m": 12_345_678,
        "week_52_low": 400.0,
        "week_52_high": 550.0,
        "reported_last_close_source": "reported-primary-source",
        "reported_price_source": "reported-primary-source",
        "reported_technicals_source": "reported-primary-source",
        "reported_retrieved_at_utc": "2026-07-14T01:02:03.123456Z",
        "source_evidence_refs": ["observation:QQQ:close"],
        "reported_issue_codes": [],
        "observation_notes": ["Reported observation only."],
    }


def _valid_value(*, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": MARKET_OBSERVATIONS_SCHEMA_VERSION,
        "observations": rows if rows is not None else [_valid_row()],
    }


def _canonical_oracle(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _schema_from_file() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    return json.loads(
        (repo_root / "schemas" / MARKET_OBSERVATIONS_SCHEMA_FILENAME).read_text(
            encoding="utf-8"
        )
    )


def _external_schema_valid(value: Any) -> bool:
    return Draft202012Validator(
        _schema_from_file(),
        format_checker=market_observations._FORMAT_CHECKER,
    ).is_valid(value)


def _assert_valid(value: Any) -> None:
    result = validate_step2_market_observations(value)
    assert result.structure_valid is True
    assert result.schema_valid is True
    assert result.diagnostics == ()
    assert result.canonical_identity_sha256 == hashlib.sha256(
        _canonical_oracle(value)
    ).hexdigest()
    assert result.canonical_size_bytes == len(_canonical_oracle(value))


def _assert_diagnostic(
    value: Any,
    diagnostic: Step2MarketObservationsDiagnostic,
    *,
    structure_valid: bool | None = None,
) -> None:
    result = validate_step2_market_observations(value)
    assert result.schema_valid is False
    assert result.diagnostics == (diagnostic,)
    if structure_valid is not None:
        assert result.structure_valid is structure_valid


def _mapping_chain(depth: int) -> dict[str, Any]:
    root: dict[str, Any] = {}
    cursor = root
    for _ in range(depth):
        child: dict[str, Any] = {}
        cursor["child"] = child
        cursor = child
    return root


def _list_chain(depth: int) -> list[Any]:
    root: list[Any] = []
    cursor = root
    for _ in range(depth):
        child: list[Any] = []
        cursor.append(child)
        cursor = child
    return root


def test_schema_file_is_draft_2020_12_and_matches_code_owned_schema() -> None:
    schema = _schema_from_file()
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema == market_observations._STEP2_MARKET_OBSERVATIONS_SCHEMA
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["observation"]["additionalProperties"] is False


def test_complete_observation_is_structurally_valid_and_non_authorizing() -> None:
    _assert_valid(_valid_value())


def test_nullable_observation_claims_and_metrics_may_all_be_null() -> None:
    row = _valid_row()
    for field_name in NULLABLE_FIELDS:
        row[field_name] = None
    row["source_evidence_refs"] = []
    row["reported_issue_codes"] = []
    row["observation_notes"] = []
    _assert_valid(_valid_value(rows=[row]))


def test_multiple_rows_are_structurally_valid() -> None:
    _assert_valid(_valid_value(rows=[_valid_row(ticker="QQQ"), _valid_row(ticker="VOO")]))


def test_unicode_sources_references_and_notes_are_preserved() -> None:
    row = _valid_row()
    row["reported_last_close_source"] = "一次資料"
    row["reported_price_source"] = "marché — clôture"
    row["source_evidence_refs"] = ["証拠:終値"]
    row["observation_notes"] = ['Résumé: café, 東京, and "quoted" text.']
    _assert_valid(_valid_value(rows=[row]))


@pytest.mark.parametrize("field_name", ["schema_version", "observations"])
def test_every_required_top_level_field_is_enforced(field_name: str) -> None:
    value = _valid_value()
    del value[field_name]
    expected = (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_VERSION_INVALID
        if field_name == "schema_version"
        else Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID
    )
    _assert_diagnostic(value, expected, structure_valid=True)


@pytest.mark.parametrize("field_name", ROW_FIELDS)
def test_every_required_observation_field_is_enforced(field_name: str) -> None:
    value = _valid_value()
    del value["observations"][0][field_name]
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_unknown_top_level_field_is_rejected() -> None:
    value = _valid_value()
    value["unknown"] = "closed"
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_unknown_observation_field_is_rejected() -> None:
    value = _valid_value()
    value["observations"][0]["unknown"] = "closed"
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize("value", [[], "value", 1, True])
def test_wrong_root_type_is_schema_invalid(value: Any) -> None:
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize("row", [[], "row", 1, True, None])
def test_wrong_row_type_is_schema_invalid(row: Any) -> None:
    value = _valid_value(rows=[row])
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_none_root_uses_the_closed_missing_diagnostic() -> None:
    _assert_diagnostic(
        None,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_MISSING,
        structure_valid=False,
    )


def test_exact_builtin_dict_and_list_are_accepted() -> None:
    value = _valid_value()
    assert type(value) is dict
    assert type(value["observations"]) is list
    assert type(value["observations"][0]) is dict
    _assert_valid(value)


def test_dict_subclass_is_rejected() -> None:
    class DictSubclass(dict[str, Any]):
        pass

    value = DictSubclass(_valid_value())
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_list_subclass_is_rejected() -> None:
    class ListSubclass(list[Any]):
        pass

    value = _valid_value()
    value["observations"] = ListSubclass(value["observations"])
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_custom_mapping_is_rejected_without_invoking_hostile_methods() -> None:
    class HostileMapping(Mapping[str, Any]):
        touched = False

        def __getitem__(self, key: str) -> Any:
            self.touched = True
            raise AssertionError("must not read custom mapping")

        def __iter__(self) -> Iterator[str]:
            self.touched = True
            raise AssertionError("must not iterate custom mapping")

        def __len__(self) -> int:
            self.touched = True
            raise AssertionError("must not size custom mapping")

    hostile = HostileMapping()
    _assert_diagnostic(
        hostile,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )
    assert hostile.touched is False


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ("note",),
        lambda: {"note"},
        lambda: frozenset({"note"}),
        lambda: (item for item in ["note"]),
        lambda: b"note",
        lambda: bytearray(b"note"),
        object,
    ],
    ids=("tuple", "set", "frozenset", "generator", "bytes", "bytearray", "object"),
)
def test_unsupported_nested_types_are_rejected_without_coercion(factory: Any) -> None:
    value = _valid_value()
    value["observations"][0]["observation_notes"] = factory()
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_nested_tuple_is_rejected_without_tuple_to_list_coercion() -> None:
    value = _valid_value()
    value["observations"][0]["observation_notes"] = [[("nested",)]]
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_non_string_mapping_key_is_rejected_without_stringification() -> None:
    value = _valid_value()
    value["observations"][0][1] = "not-a-string-key"
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_string_subclass_is_rejected_without_coercion() -> None:
    class StringSubclass(str):
        pass

    value = _valid_value()
    value["observations"][0]["ticker"] = StringSubclass("QQQ")
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )


@pytest.mark.parametrize("ticker", ["A", "QQQ", "BRK.B", "BRK-B", "A1", "ABCDEFGHIJ"])
def test_valid_ticker_contract(ticker: str) -> None:
    _assert_valid(_valid_value(rows=[_valid_row(ticker=ticker)]))


@pytest.mark.parametrize(
    "ticker",
    ["", "qqq", "1QQQ", " QQQ", "QQQ ", "ABCDEFGHIJK", "BRK_B", "$QQQ"],
)
def test_invalid_ticker_contract(ticker: str) -> None:
    value = _valid_value(rows=[_valid_row(ticker=ticker)])
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize(
    ("reported_date", "expected_valid"),
    [
        ("2026-02-28", True),
        ("2024-02-29", True),
        ("2026-02-29", False),
        ("2026-02-30", False),
        ("2026-00-01", False),
        ("2026-13-01", False),
        ("2026-01-00", False),
        ("2026-1-01", False),
        ("2026/01/01", False),
        ("2026-01-01T00:00:00", False),
        ("2026-01-01Z", False),
        (" 2026-01-01", False),
        ("2026-01-01 ", False),
    ],
)
def test_reported_date_semantics_and_schema_python_parity(
    reported_date: str,
    expected_valid: bool,
) -> None:
    value = _valid_value()
    value["observations"][0]["reported_price_asof"] = reported_date
    result = validate_step2_market_observations(value)
    assert result.schema_valid is expected_valid
    assert _external_schema_valid(value) is expected_valid


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-07-14T01:02:03Z",
        "2026-07-14T01:02:03.1Z",
        "2026-07-14T01:02:03.123456Z",
        "2024-02-29T23:59:59Z",
    ],
)
def test_valid_reported_utc_timestamp_contract(timestamp: str) -> None:
    value = _valid_value()
    value["observations"][0]["reported_retrieved_at_utc"] = timestamp
    assert _external_schema_valid(value) is True
    _assert_valid(value)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-02-30T01:02:03Z",
        "2026-07-14T24:00:00Z",
        "2026-07-14T01:02:60Z",
        "2026-07-14T01:02:03.1234567Z",
        "2026-07-14T01:02:03+00:00",
        "2026-07-14T01:02:03-07:00",
        "2026-07-14T01:02:03",
        "2026-07-14T01:02:03z",
        "2026-07-14 01:02:03Z",
        " 2026-07-14T01:02:03Z",
        "2026-07-14T01:02:03Z ",
    ],
)
def test_invalid_reported_utc_timestamp_contract(timestamp: str) -> None:
    value = _valid_value()
    value["observations"][0]["reported_retrieved_at_utc"] = timestamp
    assert _external_schema_valid(value) is False
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize("field_name", STRICTLY_POSITIVE_FIELDS)
@pytest.mark.parametrize("number", [1, 1.25, 10**100])
def test_strictly_positive_numeric_fields_accept_finite_ints_and_floats(
    field_name: str,
    number: int | float,
) -> None:
    value = _valid_value()
    value["observations"][0][field_name] = number
    _assert_valid(value)


@pytest.mark.parametrize("field_name", STRICTLY_POSITIVE_FIELDS)
@pytest.mark.parametrize("number", [0, 0.0, -1, -0.5])
def test_strictly_positive_numeric_fields_reject_zero_and_negative_values(
    field_name: str,
    number: int | float,
) -> None:
    value = _valid_value()
    value["observations"][0][field_name] = number
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize("field_name", NONNEGATIVE_FIELDS)
@pytest.mark.parametrize("number", [0, 0.0, 1, 1.25])
def test_nonnegative_numeric_fields_accept_zero_and_positive_values(
    field_name: str,
    number: int | float,
) -> None:
    value = _valid_value()
    value["observations"][0][field_name] = number
    _assert_valid(value)


@pytest.mark.parametrize("field_name", NONNEGATIVE_FIELDS)
@pytest.mark.parametrize("number", [-1, -0.5])
def test_nonnegative_numeric_fields_reject_negative_values(
    field_name: str,
    number: int | float,
) -> None:
    value = _valid_value()
    value["observations"][0][field_name] = number
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize("field_name", STRICTLY_POSITIVE_FIELDS + NONNEGATIVE_FIELDS)
def test_boolean_is_not_a_number(field_name: str) -> None:
    value = _valid_value()
    value["observations"][0][field_name] = True
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_structurally_rejected(non_finite: float) -> None:
    value = _valid_value()
    value["observations"][0]["last_close"] = non_finite
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )


@pytest.mark.parametrize("volume", [0, 1, 9_007_199_254_740_991, None])
def test_volume_integer_and_safe_integer_boundaries_are_accepted(
    volume: int | None,
) -> None:
    value = _valid_value()
    value["observations"][0]["avg_volume_3m"] = volume
    _assert_valid(value)


@pytest.mark.parametrize("volume", [-1, 9_007_199_254_740_992, 1.0, True, "1"])
def test_volume_rejects_out_of_range_and_non_exact_integer_values(
    volume: Any,
) -> None:
    value = _valid_value()
    value["observations"][0]["avg_volume_3m"] = volume
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_all_nullable_fields_accept_explicit_null_without_defaulting() -> None:
    for field_name in NULLABLE_FIELDS:
        value = _valid_value()
        value["observations"][0][field_name] = None
        _assert_valid(value)


def test_observation_row_count_boundary_and_plus_one() -> None:
    at_limit = [_valid_row() for _ in range(128)]
    _assert_valid(_valid_value(rows=at_limit))

    over_limit = [_valid_row() for _ in range(129)]
    _assert_diagnostic(
        _valid_value(rows=over_limit),
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_observations_must_not_be_empty() -> None:
    _assert_diagnostic(
        _valid_value(rows=[]),
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_source_evidence_reference_count_boundary_and_plus_one() -> None:
    value = _valid_value()
    value["observations"][0]["source_evidence_refs"] = [
        f"ref-{index}" for index in range(64)
    ]
    _assert_valid(value)

    value["observations"][0]["source_evidence_refs"].append("ref-64")
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_note_count_boundary_and_plus_one() -> None:
    value = _valid_value()
    value["observations"][0]["observation_notes"] = [
        f"note-{index}" for index in range(64)
    ]
    _assert_valid(value)

    value["observations"][0]["observation_notes"].append("note-64")
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_all_reported_issue_codes_are_closed_and_accepted_once() -> None:
    value = _valid_value()
    value["observations"][0]["reported_issue_codes"] = list(REPORTED_ISSUE_CODES)
    _assert_valid(value)


def test_issue_code_max_items_keyword_boundary_and_plus_one() -> None:
    issue_schema = _schema_from_file()["$defs"]["observation"]["properties"][
        "reported_issue_codes"
    ]
    assert issue_schema["maxItems"] == 16
    validator = Draft202012Validator(issue_schema)
    at_limit = ["OTHER_REPORTED_ISSUE"] * 16
    over_limit = ["OTHER_REPORTED_ISSUE"] * 17
    assert not any(error.validator == "maxItems" for error in validator.iter_errors(at_limit))
    assert any(error.validator == "maxItems" for error in validator.iter_errors(over_limit))


@pytest.mark.parametrize(
    "issue_codes",
    [
        ["OTHER_REPORTED_ISSUE", "OTHER_REPORTED_ISSUE"],
        ["UNKNOWN_REPORTED_ISSUE"],
        ["stale_data_claim"],
    ],
)
def test_issue_codes_reject_duplicates_unknown_values_and_case_changes(
    issue_codes: list[str],
) -> None:
    value = _valid_value()
    value["observations"][0]["reported_issue_codes"] = issue_codes
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_duplicate_source_evidence_references_are_rejected() -> None:
    value = _valid_value()
    value["observations"][0]["source_evidence_refs"] = ["ref", "ref"]
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


@pytest.mark.parametrize(
    ("field_name", "valid_value", "invalid_values"),
    [
        ("reported_last_close_source", "s" * 64, ["", "s" * 65]),
        ("reported_price_source", "s" * 64, ["", "s" * 65]),
        ("reported_technicals_source", "s" * 64, ["", "s" * 65]),
    ],
)
def test_reported_source_string_length_boundaries(
    field_name: str,
    valid_value: str,
    invalid_values: list[str],
) -> None:
    value = _valid_value()
    value["observations"][0][field_name] = valid_value
    _assert_valid(value)
    for invalid in invalid_values:
        rejected = _valid_value()
        rejected["observations"][0][field_name] = invalid
        _assert_diagnostic(
            rejected,
            Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
            structure_valid=True,
        )


def test_reference_string_length_boundary_and_plus_one() -> None:
    value = _valid_value()
    value["observations"][0]["source_evidence_refs"] = ["r" * 128]
    _assert_valid(value)

    for invalid in ("", "r" * 129):
        rejected = _valid_value()
        rejected["observations"][0]["source_evidence_refs"] = [invalid]
        _assert_diagnostic(
            rejected,
            Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
            structure_valid=True,
        )


def test_note_string_length_boundary_and_plus_one() -> None:
    value = _valid_value()
    value["observations"][0]["observation_notes"] = ["n" * 4096]
    _assert_valid(value)

    for invalid in ("", "n" * 4097):
        rejected = _valid_value()
        rejected["observations"][0]["observation_notes"] = [invalid]
        _assert_diagnostic(
            rejected,
            Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
            structure_valid=True,
        )


def test_strings_are_not_trimmed_or_normalized() -> None:
    value = _valid_value()
    value["observations"][0]["reported_price_source"] = " source claim "
    value["observations"][0]["source_evidence_refs"] = [" reference "]
    value["observations"][0]["observation_notes"] = [" note "]
    _assert_valid(value)


def test_mapping_depth_32_is_structurally_accepted_and_33_is_rejected() -> None:
    at_limit = validate_step2_market_observations(
        _mapping_chain(MAX_JSON_NESTING_DEPTH)
    )
    assert at_limit.structure_valid is True
    assert at_limit.diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_VERSION_INVALID,
    )

    _assert_diagnostic(
        _mapping_chain(MAX_JSON_NESTING_DEPTH + 1),
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_list_depth_32_is_structurally_accepted_and_33_is_rejected() -> None:
    at_limit = validate_step2_market_observations(
        _list_chain(MAX_JSON_NESTING_DEPTH)
    )
    assert at_limit.structure_valid is True
    assert at_limit.diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
    )

    _assert_diagnostic(
        _list_chain(MAX_JSON_NESTING_DEPTH + 1),
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_node_count_4096_is_structurally_accepted_and_4097_is_rejected() -> None:
    at_limit = {"items": [None] * (MAX_JSON_NODE_COUNT - 2)}
    result = validate_step2_market_observations(at_limit)
    assert result.structure_valid is True
    assert result.diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_VERSION_INVALID,
    )

    over_limit = {"items": [None] * (MAX_JSON_NODE_COUNT - 1)}
    _assert_diagnostic(
        over_limit,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )


def _self_referential_dict() -> dict[str, Any]:
    value: dict[str, Any] = {}
    value["cycle"] = value
    return value


def _self_referential_list() -> list[Any]:
    value: list[Any] = []
    value.append(value)
    return value


def _dictionary_list_mutual_cycle() -> dict[str, Any]:
    value: dict[str, Any] = {}
    child: list[Any] = [value]
    value["child"] = child
    return value


def _nested_mixed_cycle() -> dict[str, Any]:
    root: dict[str, Any] = {"outer": [{"inner": []}]}
    root["outer"][0]["inner"].append(root["outer"])
    return root


def _nested_dictionary_cycle() -> dict[str, Any]:
    root: dict[str, Any] = {"outer": {"inner": {}}}
    root["outer"]["inner"]["back"] = root["outer"]
    return root


@pytest.mark.parametrize(
    "factory",
    [
        _self_referential_dict,
        _self_referential_list,
        _dictionary_list_mutual_cycle,
        _nested_mixed_cycle,
        _nested_dictionary_cycle,
    ],
    ids=("dict", "list", "dict-list", "nested-mixed", "nested-dict"),
)
def test_direct_and_nested_cycles_fail_with_one_bounded_diagnostic(factory: Any) -> None:
    _assert_diagnostic(
        factory(),
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
        structure_valid=False,
    )


def test_shared_acyclic_dictionary_and_list_aliases_are_not_cycles() -> None:
    shared_dictionary = {"value": [1, 2]}
    shared_list = ["same"]
    value = {
        "first_dict": shared_dictionary,
        "second_dict": shared_dictionary,
        "first_list": shared_list,
        "second_list": shared_list,
    }
    result = validate_step2_market_observations(value)
    assert result.structure_valid is True
    assert result.diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_VERSION_INVALID,
    )
    assert result.canonical_identity_sha256 == hashlib.sha256(
        _canonical_oracle(value)
    ).hexdigest()


def test_shared_acyclic_list_alias_is_copied_by_value_in_valid_contract() -> None:
    shared = ["shared reported text"]
    value = _valid_value()
    value["observations"][0]["source_evidence_refs"] = shared
    value["observations"][0]["observation_notes"] = shared
    result = validate_step2_market_observations(value)
    assert result.schema_valid is True
    captured_identity = result.canonical_identity_sha256

    shared.append("later mutation")
    assert result.canonical_identity_sha256 == captured_identity
    assert validate_step2_market_observations(value).canonical_identity_sha256 != captured_identity


def test_canonical_size_exact_boundary_and_plus_one() -> None:
    at_limit = _valid_value()
    at_limit["padding"] = ""
    base_size = len(_canonical_oracle(at_limit))
    at_limit["padding"] = "x" * (MAX_CANONICAL_BYTES - base_size)
    assert len(_canonical_oracle(at_limit)) == MAX_CANONICAL_BYTES

    boundary_result = validate_step2_market_observations(at_limit)
    assert boundary_result.structure_valid is True
    assert boundary_result.canonical_size_bytes == MAX_CANONICAL_BYTES
    assert boundary_result.diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
    )

    over_limit = deepcopy(at_limit)
    over_limit["padding"] += "x"
    over_result = validate_step2_market_observations(over_limit)
    assert over_result.structure_valid is True
    assert over_result.schema_valid is False
    assert over_result.canonical_identity_sha256 is None
    assert over_result.canonical_size_bytes == MAX_CANONICAL_BYTES + 1
    assert over_result.diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SIZE_EXCEEDED,
    )


@pytest.mark.parametrize(
    "value",
    [
        {},
        [],
        {"empty_dict": {}, "empty_list": []},
        {"nested": [{"null": None, "bools": [True, False]}]},
        {"unicode": "東京 — café"},
        {"escaping": "quote=\" slash=\\ control=\n\t\u0001"},
        {"integers": [-1, 0, 1, 2**63, 10**100]},
        {"numbers": [1.25, -0.0, 1e100]},
        {"z": 1, "a": 2, "middle": [3, {"b": 4, "a": 5}]},
    ],
)
def test_iterative_canonical_serializer_is_byte_identical_to_json_oracle(
    value: Any,
) -> None:
    snapshot = market_observations._snapshot_json_value(value)
    actual = market_observations._iterative_canonical_json_bytes(snapshot)
    assert actual == _canonical_oracle(value)


def test_iterative_canonical_serializer_matches_oracle_for_shared_aliases() -> None:
    shared_dict = {"nested": [1, 2, None]}
    shared_list = ["α", True, -7]
    value = {
        "dict_first": shared_dict,
        "dict_second": shared_dict,
        "list_first": shared_list,
        "list_second": shared_list,
    }
    snapshot = market_observations._snapshot_json_value(value)
    assert market_observations._iterative_canonical_json_bytes(
        snapshot
    ) == _canonical_oracle(value)


def test_repeated_validation_is_deterministic() -> None:
    value = _valid_value()
    first = validate_step2_market_observations(value)
    second = validate_step2_market_observations(value)
    assert first == second


def test_mapping_insertion_order_does_not_affect_identity() -> None:
    first = _valid_value()
    second_row = dict(reversed(list(first["observations"][0].items())))
    second = {
        "observations": [second_row],
        "schema_version": MARKET_OBSERVATIONS_SCHEMA_VERSION,
    }
    first_result = validate_step2_market_observations(first)
    second_result = validate_step2_market_observations(second)
    assert first_result.schema_valid is True
    assert second_result.schema_valid is True
    assert first_result.canonical_identity_sha256 == second_result.canonical_identity_sha256
    assert first_result.canonical_size_bytes == second_result.canonical_size_bytes


def test_list_order_is_identity_significant() -> None:
    first = _valid_value()
    first["observations"][0]["source_evidence_refs"] = ["a", "b"]
    second = deepcopy(first)
    second["observations"][0]["source_evidence_refs"] = ["b", "a"]
    first_result = validate_step2_market_observations(first)
    second_result = validate_step2_market_observations(second)
    assert first_result.schema_valid is True
    assert second_result.schema_valid is True
    assert first_result.canonical_identity_sha256 != second_result.canonical_identity_sha256


def test_caller_mutation_after_validation_cannot_change_returned_identity() -> None:
    value = _valid_value()
    result = validate_step2_market_observations(value)
    captured_identity = result.canonical_identity_sha256
    captured_size = result.canonical_size_bytes

    value["observations"][0]["last_close"] = 999.0
    value["observations"][0]["observation_notes"].append("mutated later")

    assert result.canonical_identity_sha256 == captured_identity
    assert result.canonical_size_bytes == captured_size
    assert validate_step2_market_observations(value).canonical_identity_sha256 != captured_identity


@pytest.mark.parametrize("location", ["top", "row"])
@pytest.mark.parametrize("field_name", FORBIDDEN_AUTHORITY_FIELDS)
def test_every_authority_and_order_field_is_rejected(
    field_name: str,
    location: str,
) -> None:
    value = _valid_value()
    destination = value if location == "top" else value["observations"][0]
    destination[field_name] = True
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
        structure_valid=True,
    )


def test_result_markers_are_fixed_and_make_no_semantic_or_authority_claim() -> None:
    result = validate_step2_market_observations(_valid_value())
    assert result.identity_only is IDENTITY_ONLY is True
    assert result.not_authorization is NOT_AUTHORIZATION is True
    assert result.permission_effect == PERMISSION_EFFECT_NONE == "none"
    assert (
        result.semantic_validation_performed
        is SEMANTIC_VALIDATION_PERFORMED
        is False
    )
    assert (
        result.freshness_evaluation_performed
        is FRESHNESS_EVALUATION_PERFORMED
        is False
    )
    assert (
        result.universe_resolution_performed
        is UNIVERSE_RESOLUTION_PERFORMED
        is False
    )
    for prohibited_field in (
        "candidate_valid",
        "market_data_usable",
        "resolved_universe",
        "ready",
        "publication_eligible",
        "allowed_actions",
        "step3_allowed",
        "step4_allowed",
        "order_authority",
    ):
        assert not hasattr(result, prohibited_field)


def test_valid_and_invalid_results_both_reject_boolean_coercion() -> None:
    valid_result = validate_step2_market_observations(_valid_value())
    invalid_result = validate_step2_market_observations(None)
    with pytest.raises(TypeError, match=f"^{VALIDATION_BOOLEAN_COERCION_ERROR}$"):
        bool(valid_result)
    with pytest.raises(TypeError, match=f"^{VALIDATION_BOOLEAN_COERCION_ERROR}$"):
        bool(invalid_result)
    assert valid_result.schema_valid is True
    assert invalid_result.schema_valid is False


def test_result_is_frozen() -> None:
    result = validate_step2_market_observations(_valid_value())
    with pytest.raises(FrozenInstanceError):
        result.schema_valid = False  # type: ignore[misc]


def test_diagnostics_are_closed_bounded_and_do_not_echo_caller_content() -> None:
    secret = "CALLER-CONTROLLED-SECRET"
    value = _valid_value(rows=[_valid_row(ticker=secret)])
    result = validate_step2_market_observations(value)
    assert result.diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
    )
    assert secret not in repr(result)
    assert {diagnostic.value for diagnostic in Step2MarketObservationsDiagnostic} == {
        "market_observations_missing",
        "market_observations_structure_invalid",
        "market_observations_size_exceeded",
        "market_observations_version_invalid",
        "market_observations_schema_invalid",
    }


def test_diagnostic_priority_is_missing_then_structure_then_size_then_version_then_schema() -> None:
    assert validate_step2_market_observations(None).diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_MISSING,
    )

    unsupported = {
        "schema_version": "wrong",
        "observations": [],
        "unsupported": (1, 2),
    }
    assert validate_step2_market_observations(unsupported).diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_STRUCTURE_INVALID,
    )

    oversize = {
        "schema_version": "wrong",
        "observations": [],
        "padding": "x" * MAX_CANONICAL_BYTES,
    }
    assert validate_step2_market_observations(oversize).diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SIZE_EXCEEDED,
    )

    wrong_version = {
        "schema_version": "wrong",
        "observations": [],
        "unknown": True,
    }
    assert validate_step2_market_observations(wrong_version).diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_VERSION_INVALID,
    )

    schema_invalid = _valid_value()
    schema_invalid["unknown"] = True
    assert validate_step2_market_observations(schema_invalid).diagnostics == (
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_SCHEMA_INVALID,
    )


def test_wrong_schema_version_is_distinguished_from_remaining_schema_defects() -> None:
    value = _valid_value()
    value["schema_version"] = "step2_market_observations_v1"
    _assert_diagnostic(
        value,
        Step2MarketObservationsDiagnostic.MARKET_OBSERVATIONS_VERSION_INVALID,
        structure_valid=True,
    )


def test_duplicate_tickers_remain_deliberately_deferred_semantics() -> None:
    value = _valid_value(rows=[_valid_row(ticker="QQQ"), _valid_row(ticker="QQQ")])
    _assert_valid(value)


def test_low_greater_than_high_remains_deliberately_deferred_semantics() -> None:
    value = _valid_value()
    value["observations"][0]["week_52_low"] = 600.0
    value["observations"][0]["week_52_high"] = 500.0
    _assert_valid(value)


def test_source_and_metric_inconsistency_remains_deliberately_deferred_semantics() -> None:
    value = _valid_value()
    row = value["observations"][0]
    row["last_close"] = None
    row["reported_last_close_source"] = "claimed-source-without-close"
    row["reported_issue_codes"] = []
    _assert_valid(value)


def test_structural_success_does_not_claim_freshness_or_universe_resolution() -> None:
    value = _valid_value()
    value["observations"][0]["reported_price_asof"] = "2099-01-01"
    value["observations"][0]["reported_issue_codes"] = ["STALE_DATA_CLAIM"]
    result = validate_step2_market_observations(value)
    assert result.schema_valid is True
    assert result.semantic_validation_performed is False
    assert result.freshness_evaluation_performed is False
    assert result.universe_resolution_performed is False


_CONTRACT_MODULE = market_observations.__name__
_CONTRACT_MODULE_BASENAME = _CONTRACT_MODULE.rsplit(".", 1)[-1]
_CONTRACT_MODULE_RELATIVE_PATH = Path(
    "src/investment_orchestrator/validators"
) / f"{_CONTRACT_MODULE_BASENAME}.py"
_CONTRACT_SYMBOLS = frozenset(
    {
        validate_step2_market_observations.__name__,
        market_observations.Step2MarketObservationsValidationResult.__name__,
        market_observations.Step2MarketObservationsDiagnostic.__name__,
    }
)
_CONTRACT_TEXT_MARKERS = (
    _CONTRACT_MODULE,
    *sorted(_CONTRACT_SYMBOLS),
    MARKET_OBSERVATIONS_SCHEMA_VERSION,
    market_observations.MARKET_OBSERVATIONS_VALIDATION_RESULT_VERSION,
    MARKET_OBSERVATIONS_SCHEMA_FILENAME,
)


def _contract_reference_findings(
    relative_path: str,
    source_text: str,
) -> list[str]:
    """Return stable path/category/match findings for one production source."""
    findings: list[str] = []
    try:
        tree = ast.parse(source_text, filename=relative_path)
    except SyntaxError:
        return [f"{relative_path}: AST syntax error"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _CONTRACT_MODULE or alias.name.startswith(
                    f"{_CONTRACT_MODULE}."
                ):
                    findings.append(f"{relative_path}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            imported_module = node.module or ""
            if imported_module == _CONTRACT_MODULE or imported_module.endswith(
                f".{_CONTRACT_MODULE_BASENAME}"
            ):
                findings.append(f"{relative_path}: from-import {imported_module}")
            for alias in node.names:
                if alias.name in _CONTRACT_SYMBOLS:
                    findings.append(f"{relative_path}: symbol {alias.name}")
        elif isinstance(node, ast.Name) and node.id in _CONTRACT_SYMBOLS:
            findings.append(f"{relative_path}: symbol {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _CONTRACT_SYMBOLS:
            findings.append(f"{relative_path}: symbol {node.attr}")

    for marker in _CONTRACT_TEXT_MARKERS:
        if marker in source_text:
            findings.append(f"{relative_path}: text {marker}")
    return sorted(set(findings))


def _repository_root_for_contract_tests() -> Path:
    return Path(__file__).resolve().parents[2]


def test_contract_reference_detector_recognizes_all_forbidden_forms() -> None:
    synthetic_cases = {
        "direct-import": f"import {_CONTRACT_MODULE}\n",
        "from-import": (
            f"from {_CONTRACT_MODULE} import "
            f"{market_observations.Step2MarketObservationsValidationResult.__name__}\n"
        ),
        "aliased-import-and-call": (
            f"import {_CONTRACT_MODULE} as contract\n"
            f"contract.{validate_step2_market_observations.__name__}({{}})\n"
        ),
        "public-symbol-reference": (
            f"handler = {validate_step2_market_observations.__name__}\n"
        ),
        "schema-version": f"VERSION = {MARKET_OBSERVATIONS_SCHEMA_VERSION!r}\n",
        "result-version": (
            "RESULT_VERSION = "
            f"{market_observations.MARKET_OBSERVATIONS_VALIDATION_RESULT_VERSION!r}\n"
        ),
        "schema-filename": f"SCHEMA = {MARKET_OBSERVATIONS_SCHEMA_FILENAME!r}\n",
    }
    for case_name, source_text in synthetic_cases.items():
        findings = _contract_reference_findings(
            f"synthetic/{case_name}.py",
            source_text,
        )
        assert findings, case_name

    assert any(
        "import investment_orchestrator.validators.validate_step2_market_observations"
        in finding
        for finding in _contract_reference_findings(
            "synthetic/direct.py",
            synthetic_cases["direct-import"],
        )
    )
    assert any(
        "from-import investment_orchestrator.validators.validate_step2_market_observations"
        in finding
        for finding in _contract_reference_findings(
            "synthetic/from.py",
            synthetic_cases["from-import"],
        )
    )
    assert any(
        f"symbol {validate_step2_market_observations.__name__}" in finding
        for finding in _contract_reference_findings(
            "synthetic/call.py",
            synthetic_cases["aliased-import-and-call"],
        )
    )
    assert any(
        f"text {MARKET_OBSERVATIONS_SCHEMA_VERSION}" in finding
        for finding in _contract_reference_findings(
            "synthetic/version.py",
            synthetic_cases["schema-version"],
        )
    )
    assert any(
        f"text {market_observations.MARKET_OBSERVATIONS_VALIDATION_RESULT_VERSION}"
        in finding
        for finding in _contract_reference_findings(
            "synthetic/result-version.py",
            synthetic_cases["result-version"],
        )
    )
    assert any(
        f"text {MARKET_OBSERVATIONS_SCHEMA_FILENAME}" in finding
        for finding in _contract_reference_findings(
            "synthetic/schema.py",
            synthetic_cases["schema-filename"],
        )
    )


def test_no_production_consumer_references_market_observations_v2_contract() -> None:
    repo_root = _repository_root_for_contract_tests()
    production_root = repo_root / "src" / "investment_orchestrator"
    allowed_contract_path = repo_root / _CONTRACT_MODULE_RELATIVE_PATH
    findings: list[str] = []

    for path in sorted(production_root.rglob("*.py")):
        if path == allowed_contract_path:
            continue
        relative_path = path.relative_to(repo_root).as_posix()
        findings.extend(
            _contract_reference_findings(
                relative_path,
                path.read_text(encoding="utf-8"),
            )
        )

    assert sorted(set(findings)) == [], "\n".join(sorted(set(findings)))
