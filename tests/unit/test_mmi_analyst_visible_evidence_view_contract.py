from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.mmi import (
    canonical,
    contracts,
    evidence_bundle,
    policy_projection,
    portfolio_projection,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES,
    _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_IDENTITY_DOMAIN,
    MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN,
    MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
    MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN,
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
    canonical_json_bytes,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    MMI_ANALYST_VIEW_LIMITATION_TRANSLATIONS,
    MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_ARTIFACT_KIND,
    MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_SCHEMA_VERSION,
    mmi_analyst_visible_evidence_view_identity_sha256,
)


SCHEMA_NAME = "mmi_analyst_visible_evidence_view_v1.schema.json"
SCHEMA_PATH = (
    repo_root()
    / "schemas"
    / "mmi_analyst_visible_evidence_view_v1.schema.json"
)
TIMESTAMP = "2026-07-28T12:34:56.123456Z"
POLICY_DATE = "2026-07-25"
PORTFOLIO_DATE = "2026-07-27"
SHA_A = "1" * 64
SHA_B = "2" * 64
NOT_SUPPLIED = "NOT_SUPPLIED"
SOURCE_ABSENT = "PRESENT_VALIDATED_SOURCE_ABSENT"
SOURCE_BOUND = "PRESENT_SOURCE_BOUND_VALIDATED"
PORTFOLIO_BRANCHES = (NOT_SUPPLIED, SOURCE_ABSENT, SOURCE_BOUND)
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
IDENTITY_FIELD = "analyst_visible_evidence_view_identity_sha256"
OUTSIDE_LIMITATION_CODE = (
    "VIEW_PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE"
)

EXPECTED_TRANSLATIONS = (
    (
        "POLICY_PROJECTION",
        "POLICY_CASH_MODEL_UNAVAILABLE",
        "VIEW_POLICY_CASH_MODEL_UNAVAILABLE",
    ),
    (
        "POLICY_PROJECTION",
        "POLICY_EXTENDED_ACTIVATION_CONSTRAINTS_UNAVAILABLE",
        "VIEW_POLICY_EXTENDED_ACTIVATION_CONSTRAINTS_UNAVAILABLE",
    ),
    (
        "POLICY_PROJECTION",
        "POLICY_LOOKTHROUGH_EXPOSURE_UNAVAILABLE",
        "VIEW_POLICY_LOOKTHROUGH_EXPOSURE_UNAVAILABLE",
    ),
    (
        "POLICY_PROJECTION",
        "POLICY_MAX_NEW_TICKER_RULE_UNAVAILABLE",
        "VIEW_POLICY_MAX_NEW_TICKER_RULE_UNAVAILABLE",
    ),
    (
        "POLICY_PROJECTION",
        "POLICY_MINIMUM_HOLDING_ENFORCEMENT_INCOMPLETE",
        "VIEW_POLICY_MINIMUM_HOLDING_ENFORCEMENT_INCOMPLETE",
    ),
    (
        "POLICY_PROJECTION",
        "POLICY_PER_RUN_BUDGET_APPLICABILITY_UNVERIFIED",
        "VIEW_POLICY_PER_RUN_BUDGET_APPLICABILITY_UNVERIFIED",
    ),
    (
        "POLICY_PROJECTION",
        "POLICY_PER_RUN_NEW_BUY_BUDGET_UNAVAILABLE",
        "VIEW_POLICY_PER_RUN_NEW_BUY_BUDGET_UNAVAILABLE",
    ),
    (
        "POLICY_PROJECTION",
        "POLICY_PORTFOLIO_NAV_CONCENTRATION_UNAVAILABLE",
        "VIEW_POLICY_PORTFOLIO_NAV_CONCENTRATION_UNAVAILABLE",
    ),
    (
        "POLICY_PROJECTION",
        "POLICY_SELL_ELIGIBILITY_INCOMPLETE",
        "VIEW_POLICY_SELL_ELIGIBILITY_INCOMPLETE",
    ),
    (
        "POLICY_PROJECTION",
        "POLICY_TAX_LOT_ENFORCEMENT_UNAVAILABLE",
        "VIEW_POLICY_TAX_LOT_ENFORCEMENT_UNAVAILABLE",
    ),
    (
        "POLICY_PROJECTION",
        "POLICY_TURNOVER_ENFORCEMENT_INCOMPLETE",
        "VIEW_POLICY_TURNOVER_ENFORCEMENT_INCOMPLETE",
    ),
    (
        "EVIDENCE_BUNDLE",
        "EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED",
        "VIEW_EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED",
    ),
    (
        "PORTFOLIO_PROJECTION",
        "PORTFOLIO_SOURCE_MISSING",
        "VIEW_PORTFOLIO_SOURCE_MISSING",
    ),
    (
        "PORTFOLIO_PROJECTION",
        "PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE",
        "VIEW_PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE",
    ),
    (
        "PORTFOLIO_PROJECTION",
        "PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED",
        "VIEW_PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED",
    ),
    (
        "PORTFOLIO_PROJECTION",
        "PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE",
        OUTSIDE_LIMITATION_CODE,
    ),
)
LIMITATION_BY_CODE = {
    output_code: (index, owner)
    for index, (owner, _upstream_code, output_code) in enumerate(
        EXPECTED_TRANSLATIONS
    )
}


def _coverage() -> dict[str, object]:
    return {
        "holdings": "UNSTRUCTURED_NOT_PROJECTED",
        "cash": "UNAVAILABLE_NOT_PROJECTED",
        "deployable_cash": "UNAVAILABLE_NOT_PROJECTED",
        "open_sells": "UNSTRUCTURED_NOT_PROJECTED",
        "tax_lots": "UNSTRUCTURED_NOT_PROJECTED",
        "holding_dates": "UNAVAILABLE_NOT_PROJECTED",
        "gains_losses": "UNAVAILABLE_NOT_PROJECTED",
        "weights": "UNAVAILABLE_NOT_PROJECTED",
        "nav_concentration": "UNAVAILABLE_NOT_PROJECTED",
        "look_through_exposure": "UNAVAILABLE_NOT_PROJECTED",
    }


def _policy_view() -> dict[str, object]:
    return {
        "policy_as_of_date": POLICY_DATE,
        "policy_method": (
            "BUDGET_SHORTLIST_ROTATION_WITHOUT_TARGET_WEIGHTS"
        ),
        "benchmark_reference_instruments": ["VOO"],
        "analysis_instruments": [
            {"ticker": "VOO", "policy_role": "CORE"},
            {"ticker": "QQQ", "policy_role": "CORE"},
            {"ticker": "SMH", "policy_role": "SATELLITE"},
            {"ticker": "QUAL", "policy_role": "APPROVED_EXTENDED"},
        ],
        "extended_activation_status": "NOT_EVALUATED_REPORT_ONLY",
        "instrument_availability_observation_status": (
            "NOT_DETERMINISTICALLY_AVAILABLE"
        ),
        "target_weights_absence_reason": (
            "POLICY_METHOD_HAS_NO_TARGET_WEIGHTS"
        ),
    }


def _portfolio_view(branch: str) -> dict[str, object]:
    if branch == NOT_SUPPLIED:
        return {"presence_status": NOT_SUPPLIED}
    if branch == SOURCE_ABSENT:
        return {
            "presence_status": SOURCE_ABSENT,
            "portfolio_source_date": None,
            "open_buy_status": "SOURCE_ABSENT",
            "open_buy_observations": [],
            "fact_coverage_statuses": _coverage(),
        }
    if branch == SOURCE_BOUND:
        return {
            "presence_status": SOURCE_BOUND,
            "portfolio_source_date": PORTFOLIO_DATE,
            "open_buy_status": "SOURCE_VALIDATED",
            "open_buy_observations": [
                {
                    "ticker": "QQQ",
                    "policy_membership_classification": "CORE",
                },
                {
                    "ticker": "XYZ",
                    "policy_membership_classification": (
                        "OUTSIDE_POLICY_UNIVERSE"
                    ),
                },
            ],
            "fact_coverage_statuses": _coverage(),
        }
    raise AssertionError(branch)


def _limitation(
    code: str,
    *,
    affected_tickers: list[str] | None = None,
) -> dict[str, object]:
    _rank, owner = LIMITATION_BY_CODE[code]
    if affected_tickers is None:
        affected_tickers = ["XYZ"] if code == OUTSIDE_LIMITATION_CODE else []
    return {
        "owner": owner,
        "code": code,
        "affected_tickers": list(affected_tickers),
    }


def _branch_limitations(branch: str) -> list[dict[str, object]]:
    codes = ["VIEW_POLICY_CASH_MODEL_UNAVAILABLE"]
    if branch == NOT_SUPPLIED:
        codes.append("VIEW_EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED")
    elif branch == SOURCE_ABSENT:
        codes.append("VIEW_PORTFOLIO_SOURCE_MISSING")
    elif branch == SOURCE_BOUND:
        codes.append(OUTSIDE_LIMITATION_CODE)
    return [_limitation(code) for code in codes]


def _independent_identity(value: dict[str, object]) -> str:
    preimage = deepcopy(value)
    preimage.pop(IDENTITY_FIELD, None)
    encoded = json.dumps(
        preimage,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    material = (
        b"mmi_analyst_visible_evidence_view_v1\0"
        + struct.pack(">Q", len(encoded))
        + encoded
    )
    return hashlib.sha256(material).hexdigest()


def _view(branch: str = SOURCE_BOUND) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "mmi_analyst_visible_evidence_view_v1",
        "artifact_kind": "MMI_ANALYST_VISIBLE_EVIDENCE_VIEW",
        "report_only": True,
        "authority_effect": "NONE",
        "evaluation_timestamp_utc": TIMESTAMP,
        "evidence_bundle_identity_sha256": SHA_A,
        "policy_view": _policy_view(),
        "portfolio_view": _portfolio_view(branch),
        "known_view_limitations": _branch_limitations(branch),
        "view_completeness_status": "PROJECTION_VALID_WITH_GAPS",
        IDENTITY_FIELD: "0" * 64,
    }
    value[IDENTITY_FIELD] = _independent_identity(value)
    return value


def _schema() -> dict[str, object]:
    value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _assert_schema_rejected(value: object) -> None:
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(value, schema_name=SCHEMA_NAME)


def _assert_identity_rejected(value: object) -> None:
    with pytest.raises(
        MmiCanonicalizationError,
        match="MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_CONTRACT_INVALID",
    ):
        mmi_analyst_visible_evidence_view_identity_sha256(  # type: ignore[arg-type]
            value
        )


def _object_schemas(value: object):
    if type(value) is dict:
        if value.get("type") == "object":
            yield value
        for child in value.values():
            yield from _object_schemas(child)
    elif type(value) is list:
        for child in value:
            yield from _object_schemas(child)


def _reverse_mapping_order(value: object) -> object:
    if type(value) is dict:
        return {
            key: _reverse_mapping_order(child)
            for key, child in reversed(tuple(value.items()))
        }
    if type(value) is list:
        return [_reverse_mapping_order(child) for child in value]
    return value


def _leaf_paths(
    value: object,
    prefix: tuple[object, ...] = (),
) -> list[tuple[object, ...]]:
    if type(value) is dict:
        return [
            path
            for key, child in value.items()
            for path in _leaf_paths(child, (*prefix, key))
        ]
    if type(value) is list:
        return [
            path
            for index, child in enumerate(value)
            for path in _leaf_paths(child, (*prefix, index))
        ]
    return [prefix]


def _value_at_path(value: object, path: tuple[object, ...]) -> object:
    current = value
    for part in path:
        current = current[part]  # type: ignore[index]
    return current


def _set_path(
    value: object,
    path: tuple[object, ...],
    replacement: object,
) -> None:
    current = value
    for part in path[:-1]:
        current = current[part]  # type: ignore[index]
    current[path[-1]] = replacement  # type: ignore[index]


def _maximum_view() -> dict[str, object]:
    # This fills the structural schema maximum for the byte-ceiling proof.
    # Source-bound reachability is independently proven below to stop at 12.
    tickers = [f"A{index:015d}" for index in range(256)]
    limitation_rows = []
    for rank, (owner, _upstream_code, output_code) in enumerate(
        EXPECTED_TRANSLATIONS
    ):
        affected = tickers if output_code == OUTSIDE_LIMITATION_CODE else []
        row = {
            "owner": owner,
            "code": output_code,
            "affected_tickers": affected,
        }
        row_size = len(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        limitation_rows.append((row_size, rank, row))
    selected_ranks = {
        rank
        for _size, rank, _row in sorted(
            limitation_rows,
            reverse=True,
        )[:14]
    }
    limitations = [
        row
        for _size, rank, row in limitation_rows
        if rank in selected_ranks
    ]
    value: dict[str, object] = {
        "schema_version": "mmi_analyst_visible_evidence_view_v1",
        "artifact_kind": "MMI_ANALYST_VISIBLE_EVIDENCE_VIEW",
        "report_only": True,
        "authority_effect": "NONE",
        "evaluation_timestamp_utc": "9999-12-31T23:59:59.999999Z",
        "evidence_bundle_identity_sha256": "f" * 64,
        "policy_view": {
            "policy_as_of_date": "9999-12-31",
            "policy_method": (
                "BUDGET_SHORTLIST_ROTATION_WITHOUT_TARGET_WEIGHTS"
            ),
            "benchmark_reference_instruments": [tickers[0]],
            "analysis_instruments": [
                {
                    "ticker": ticker,
                    "policy_role": (
                        "CORE"
                        if index == 0
                        else (
                            "SATELLITE"
                            if index == 1
                            else "APPROVED_EXTENDED"
                        )
                    ),
                }
                for index, ticker in enumerate(tickers)
            ],
            "extended_activation_status": (
                "NOT_EVALUATED_REPORT_ONLY"
            ),
            "instrument_availability_observation_status": (
                "NOT_DETERMINISTICALLY_AVAILABLE"
            ),
            "target_weights_absence_reason": (
                "POLICY_METHOD_HAS_NO_TARGET_WEIGHTS"
            ),
        },
        "portfolio_view": {
            "presence_status": SOURCE_BOUND,
            "portfolio_source_date": "9999-12-31",
            "open_buy_status": "SOURCE_VALIDATED",
            "open_buy_observations": [
                {
                    "ticker": ticker,
                    "policy_membership_classification": (
                        "OUTSIDE_POLICY_UNIVERSE"
                    ),
                }
                for ticker in tickers
            ],
            "fact_coverage_statuses": _coverage(),
        },
        "known_view_limitations": limitations,
        "view_completeness_status": "PROJECTION_VALID_WITH_GAPS",
        IDENTITY_FIELD: "f" * 64,
    }
    value[IDENTITY_FIELD] = _independent_identity(value)
    return value


def test_schema_is_closed_draft_2020_12_with_exact_top_level() -> None:
    schema = _schema()
    assert schema["$schema"] == (
        "https://json-schema.org/draft/2020-12/schema"
    )
    assert schema["additionalProperties"] is False
    expected = {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "evaluation_timestamp_utc",
        "evidence_bundle_identity_sha256",
        "policy_view",
        "portfolio_view",
        "known_view_limitations",
        "view_completeness_status",
        IDENTITY_FIELD,
    }
    assert set(schema["required"]) == expected
    assert set(schema["properties"]) == expected
    assert all(
        object_schema.get("additionalProperties") is False
        for object_schema in _object_schemas(schema)
    )
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("branch", PORTFOLIO_BRANCHES)
def test_each_portfolio_branch_is_valid_and_identity_bound(
    branch: str,
) -> None:
    value = _view(branch)
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(value)
        == value[IDENTITY_FIELD]
        == _independent_identity(value)
    )


@pytest.mark.parametrize(
    "field",
    (
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "evaluation_timestamp_utc",
        "evidence_bundle_identity_sha256",
        "policy_view",
        "portfolio_view",
        "known_view_limitations",
        "view_completeness_status",
        IDENTITY_FIELD,
    ),
)
def test_every_top_level_field_is_required(field: str) -> None:
    value = _view()
    value.pop(field)
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_top_level_and_every_nested_object_are_closed() -> None:
    candidates = []
    top = _view()
    top["unexpected"] = "closed"
    candidates.append(top)

    policy = _view()
    policy_view = policy["policy_view"]
    assert type(policy_view) is dict
    policy_view["unexpected"] = "closed"
    candidates.append(policy)

    instrument = _view()
    policy_view = instrument["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list and type(instruments[0]) is dict
    instruments[0]["unexpected"] = "closed"
    candidates.append(instrument)

    portfolio = _view()
    portfolio_view = portfolio["portfolio_view"]
    assert type(portfolio_view) is dict
    portfolio_view["unexpected"] = "closed"
    candidates.append(portfolio)

    observation = _view()
    portfolio_view = observation["portfolio_view"]
    assert type(portfolio_view) is dict
    observations = portfolio_view["open_buy_observations"]
    assert type(observations) is list and type(observations[0]) is dict
    observations[0]["unexpected"] = "closed"
    candidates.append(observation)

    coverage = _view()
    portfolio_view = coverage["portfolio_view"]
    assert type(portfolio_view) is dict
    coverage_value = portfolio_view["fact_coverage_statuses"]
    assert type(coverage_value) is dict
    coverage_value["unexpected"] = "closed"
    candidates.append(coverage)

    limitation = _view()
    limitations = limitation["known_view_limitations"]
    assert type(limitations) is list and type(limitations[0]) is dict
    limitations[0]["unexpected"] = "closed"
    candidates.append(limitation)

    for candidate in candidates:
        _assert_schema_rejected(candidate)
        _assert_identity_rejected(candidate)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("schema_version", "mmi_analyst_visible_evidence_view_v2"),
        ("artifact_kind", "MMI_ANALYST_EVIDENCE"),
        ("report_only", False),
        ("authority_effect", "READY"),
        ("view_completeness_status", "PROJECTION_VALID_COMPLETE"),
    ),
)
def test_fixed_top_level_constants_are_exact(
    field: str,
    replacement: object,
) -> None:
    value = _view()
    value[field] = replacement
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


@pytest.mark.parametrize(
    "value",
    (
        "2026-13-01T00:00:00.000000Z",
        "2026-02-30T00:00:00.000000Z",
        "2026-01-01T24:00:00.000000Z",
        "2026-01-01T00:60:00.000000Z",
        "2026-01-01T00:00:60.000000Z",
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:00Z",
    ),
)
def test_timestamp_semantics_reject_invalid_or_noncanonical_values(
    value: str,
) -> None:
    candidate = _view()
    candidate["evaluation_timestamp_utc"] = value
    _assert_identity_rejected(candidate)
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z",
        value,
    ):
        _assert_schema_rejected(candidate)


@pytest.mark.parametrize(
    "value",
    (
        "2026-13-01",
        "2026-02-30",
        "2026-01-00",
        "2026-1-01",
        "2026-01-01T00:00:00Z",
    ),
)
def test_policy_date_semantics_reject_invalid_or_noncanonical_values(
    value: str,
) -> None:
    candidate = _view()
    policy = candidate["policy_view"]
    assert type(policy) is dict
    policy["policy_as_of_date"] = value
    _assert_identity_rejected(candidate)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _assert_schema_rejected(candidate)


@pytest.mark.parametrize(
    "value",
    (
        "2026-13-01",
        "2026-02-30",
        "2026-01-00",
        "2026-1-01",
        "2026-01-01T00:00:00Z",
    ),
)
def test_portfolio_date_semantics_reject_invalid_values(
    value: str,
) -> None:
    candidate = _view()
    portfolio = candidate["portfolio_view"]
    assert type(portfolio) is dict
    portfolio["portfolio_source_date"] = value
    _assert_identity_rejected(candidate)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _assert_schema_rejected(candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("evidence_bundle_identity_sha256", "A" * 64),
        ("evidence_bundle_identity_sha256", "0" * 63),
        (IDENTITY_FIELD, "g" * 64),
        (IDENTITY_FIELD, 0),
    ),
)
def test_hashes_are_exact_lowercase_sha256(
    field: str,
    value: object,
) -> None:
    candidate = _view()
    candidate[field] = value
    _assert_schema_rejected(candidate)
    _assert_identity_rejected(candidate)


def test_policy_view_has_exact_approved_fact_set_and_constants() -> None:
    value = _view()
    policy = value["policy_view"]
    assert type(policy) is dict
    assert set(policy) == {
        "policy_as_of_date",
        "policy_method",
        "benchmark_reference_instruments",
        "analysis_instruments",
        "extended_activation_status",
        "instrument_availability_observation_status",
        "target_weights_absence_reason",
    }
    assert policy["policy_method"] == (
        "BUDGET_SHORTLIST_ROTATION_WITHOUT_TARGET_WEIGHTS"
    )
    assert policy["extended_activation_status"] == (
        "NOT_EVALUATED_REPORT_ONLY"
    )
    assert policy["instrument_availability_observation_status"] == (
        "NOT_DETERMINISTICALLY_AVAILABLE"
    )
    assert policy["target_weights_absence_reason"] == (
        "POLICY_METHOD_HAS_NO_TARGET_WEIGHTS"
    )


@pytest.mark.parametrize(
    "field",
    (
        "theme",
        "theme_slug",
        "theme_name",
        "category",
        "sector",
        "industry",
        "description",
        "metadata",
    ),
)
def test_policy_instruments_reject_theme_or_category_fields(
    field: str,
) -> None:
    value = _view()
    policy = value["policy_view"]
    assert type(policy) is dict
    instruments = policy["analysis_instruments"]
    assert type(instruments) is list and type(instruments[-1]) is dict
    instruments[-1][field] = "source_owned_value"
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


@pytest.mark.parametrize(
    "role",
    (
        "OUTSIDE_POLICY_UNIVERSE",
        "CORE_AND_SATELLITE",
        "",
        None,
    ),
)
def test_analysis_instrument_role_vocabulary_is_closed(
    role: object,
) -> None:
    value = _view()
    policy = value["policy_view"]
    assert type(policy) is dict
    instruments = policy["analysis_instruments"]
    assert type(instruments) is list and type(instruments[-1]) is dict
    instruments[-1]["policy_role"] = role
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_duplicate_analysis_ticker_is_rejected_even_with_different_role() -> None:
    value = _view()
    policy = value["policy_view"]
    assert type(policy) is dict
    instruments = policy["analysis_instruments"]
    assert type(instruments) is list
    instruments.append(
        {"ticker": "VOO", "policy_role": "APPROVED_EXTENDED"}
    )
    _assert_identity_rejected(value)


def test_policy_role_group_order_is_structurally_enforced() -> None:
    value = _view()
    policy = value["policy_view"]
    assert type(policy) is dict
    instruments = policy["analysis_instruments"]
    assert type(instruments) is list
    instruments[1], instruments[2] = instruments[2], instruments[1]
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    _assert_identity_rejected(value)


def test_policy_requires_core_and_satellite_members() -> None:
    for missing_role in ("CORE", "SATELLITE"):
        value = _view()
        policy = value["policy_view"]
        assert type(policy) is dict
        instruments = policy["analysis_instruments"]
        assert type(instruments) is list
        policy["analysis_instruments"] = [
            item
            for item in instruments
            if type(item) is dict
            and item.get("policy_role") != missing_role
        ]
        _assert_schema_rejected(value)
        _assert_identity_rejected(value)


def test_benchmark_must_exist_once_as_core() -> None:
    absent = _view()
    policy = absent["policy_view"]
    assert type(policy) is dict
    policy["benchmark_reference_instruments"] = ["SPY"]
    validate_artifact_schema(absent, schema_name=SCHEMA_NAME)
    _assert_identity_rejected(absent)

    noncore = _view()
    policy = noncore["policy_view"]
    assert type(policy) is dict
    policy["benchmark_reference_instruments"] = ["SMH"]
    validate_artifact_schema(noncore, schema_name=SCHEMA_NAME)
    _assert_identity_rejected(noncore)


def test_policy_order_does_not_add_rank_or_recommendation_fields() -> None:
    value = _view()
    policy = value["policy_view"]
    assert type(policy) is dict
    instruments = policy["analysis_instruments"]
    assert type(instruments) is list
    for field in (
        "rank",
        "priority",
        "recommendation",
        "allocation",
        "availability",
        "permission",
    ):
        candidate = deepcopy(value)
        candidate_policy = candidate["policy_view"]
        assert type(candidate_policy) is dict
        candidate_instruments = candidate_policy["analysis_instruments"]
        assert type(candidate_instruments) is list
        assert type(candidate_instruments[0]) is dict
        candidate_instruments[0][field] = "forbidden"
        _assert_schema_rejected(candidate)
        _assert_identity_rejected(candidate)


def test_not_supplied_portfolio_contains_only_presence() -> None:
    value = _view(NOT_SUPPLIED)
    portfolio = value["portfolio_view"]
    assert portfolio == {"presence_status": NOT_SUPPLIED}
    for field in (
        "portfolio_source_date",
        "open_buy_status",
        "open_buy_observations",
        "fact_coverage_statuses",
        "source_identity",
    ):
        candidate = _view(NOT_SUPPLIED)
        candidate_portfolio = candidate["portfolio_view"]
        assert type(candidate_portfolio) is dict
        candidate_portfolio[field] = None
        _assert_schema_rejected(candidate)
        _assert_identity_rejected(candidate)


def test_source_absent_portfolio_has_exact_unknown_shape() -> None:
    value = _view(SOURCE_ABSENT)
    portfolio = value["portfolio_view"]
    assert type(portfolio) is dict
    assert portfolio["portfolio_source_date"] is None
    assert portfolio["open_buy_status"] == "SOURCE_ABSENT"
    assert portfolio["open_buy_observations"] == []
    assert portfolio["fact_coverage_statuses"] == _coverage()


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("portfolio_source_date", PORTFOLIO_DATE),
        ("open_buy_status", "SOURCE_VALIDATED"),
        (
            "open_buy_observations",
            [
                {
                    "ticker": "QQQ",
                    "policy_membership_classification": "CORE",
                }
            ],
        ),
    ),
)
def test_source_absent_branch_rejects_source_present_hybrids(
    field: str,
    replacement: object,
) -> None:
    value = _view(SOURCE_ABSENT)
    portfolio = value["portfolio_view"]
    assert type(portfolio) is dict
    portfolio[field] = replacement
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_source_bound_parse_failure_requires_empty_observations() -> None:
    valid = _view(SOURCE_BOUND)
    portfolio = valid["portfolio_view"]
    assert type(portfolio) is dict
    portfolio["open_buy_status"] = "PARSE_FAILED"
    portfolio["open_buy_observations"] = []
    valid["known_view_limitations"] = [
        _limitation("VIEW_POLICY_CASH_MODEL_UNAVAILABLE"),
        _limitation("VIEW_PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED"),
    ]
    validate_artifact_schema(valid, schema_name=SCHEMA_NAME)
    mmi_analyst_visible_evidence_view_identity_sha256(valid)

    invalid = _view(SOURCE_BOUND)
    portfolio = invalid["portfolio_view"]
    assert type(portfolio) is dict
    portfolio["open_buy_status"] = "PARSE_FAILED"
    _assert_schema_rejected(invalid)
    _assert_identity_rejected(invalid)


def test_source_validated_may_have_empty_observations_without_zero_inference() -> None:
    value = _view(SOURCE_BOUND)
    portfolio = value["portfolio_view"]
    assert type(portfolio) is dict
    portfolio["open_buy_observations"] = []
    value["known_view_limitations"] = [
        _limitation("VIEW_POLICY_CASH_MODEL_UNAVAILABLE")
    ]
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    mmi_analyst_visible_evidence_view_identity_sha256(value)
    assert "no_open_orders" not in json.dumps(value).casefold()


@pytest.mark.parametrize(
    "classification",
    (
        "CORE",
        "SATELLITE",
        "APPROVED_EXTENDED",
        "OUTSIDE_POLICY_UNIVERSE",
    ),
)
def test_open_buy_classification_vocabulary_is_exact(
    classification: str,
) -> None:
    value = _view(SOURCE_BOUND)
    portfolio = value["portfolio_view"]
    assert type(portfolio) is dict
    observations = portfolio["open_buy_observations"]
    assert type(observations) is list and type(observations[0]) is dict
    observations[0]["policy_membership_classification"] = classification
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    mmi_analyst_visible_evidence_view_identity_sha256(value)


def test_duplicate_observation_ticker_is_rejected_without_overwrite() -> None:
    value = _view(SOURCE_BOUND)
    portfolio = value["portfolio_view"]
    assert type(portfolio) is dict
    observations = portfolio["open_buy_observations"]
    assert type(observations) is list
    observations.append(
        {
            "ticker": "QQQ",
            "policy_membership_classification": "SATELLITE",
        }
    )
    _assert_identity_rejected(value)


@pytest.mark.parametrize(
    "field",
    (
        "policy_role",
        "outside_policy_universe",
        "reserved_budget_decimal",
        "quantity",
        "price",
        "order_id",
        "instruction",
        "theme",
        "category",
    ),
)
def test_observations_reject_role_money_order_and_theme_fields(
    field: str,
) -> None:
    value = _view(SOURCE_BOUND)
    portfolio = value["portfolio_view"]
    assert type(portfolio) is dict
    observations = portfolio["open_buy_observations"]
    assert type(observations) is list and type(observations[0]) is dict
    observations[0][field] = "forbidden"
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_coverage_statuses_are_exact_and_absent_when_not_supplied() -> None:
    not_supplied = _view(NOT_SUPPLIED)
    portfolio = not_supplied["portfolio_view"]
    assert type(portfolio) is dict
    assert "fact_coverage_statuses" not in portfolio

    for branch in (SOURCE_ABSENT, SOURCE_BOUND):
        value = _view(branch)
        portfolio = value["portfolio_view"]
        assert type(portfolio) is dict
        assert portfolio["fact_coverage_statuses"] == _coverage()
        missing = deepcopy(value)
        missing_portfolio = missing["portfolio_view"]
        assert type(missing_portfolio) is dict
        coverage = missing_portfolio["fact_coverage_statuses"]
        assert type(coverage) is dict
        coverage.pop("weights")
        _assert_schema_rejected(missing)
        _assert_identity_rejected(missing)

        changed = deepcopy(value)
        changed_portfolio = changed["portfolio_view"]
        assert type(changed_portfolio) is dict
        coverage = changed_portfolio["fact_coverage_statuses"]
        assert type(coverage) is dict
        coverage["weights"] = "ZERO"
        _assert_schema_rejected(changed)
        _assert_identity_rejected(changed)


def test_closed_limitation_translation_contract_is_exact_and_theme_free() -> None:
    assert MMI_ANALYST_VIEW_LIMITATION_TRANSLATIONS == (
        EXPECTED_TRANSLATIONS
    )
    assert len(EXPECTED_TRANSLATIONS) == 16
    assert len(
        {
            output_code
            for _owner, _upstream_code, output_code
            in EXPECTED_TRANSLATIONS
        }
    ) == 16
    serialized = json.dumps(EXPECTED_TRANSLATIONS).casefold()
    assert "theme" not in serialized
    assert "unknown" not in serialized
    assert "fallback" not in serialized
    assert "generic" not in serialized


def test_reachable_limitation_maximum_is_proven_from_upstream_contracts() -> None:
    translation_by_upstream = {
        (owner, upstream_code): output_code
        for owner, upstream_code, output_code in EXPECTED_TRANSLATIONS
    }
    assert len(translation_by_upstream) == len(EXPECTED_TRANSLATIONS) == 16
    assert len(
        {
            (owner, output_code)
            for owner, _upstream_code, output_code in EXPECTED_TRANSLATIONS
        }
    ) == 16

    policy_owner = "POLICY_PROJECTION"
    policy_always = frozenset(
        {
            "POLICY_CASH_MODEL_UNAVAILABLE",
            "POLICY_PORTFOLIO_NAV_CONCENTRATION_UNAVAILABLE",
            "POLICY_LOOKTHROUGH_EXPOSURE_UNAVAILABLE",
            "POLICY_TURNOVER_ENFORCEMENT_INCOMPLETE",
            "POLICY_MINIMUM_HOLDING_ENFORCEMENT_INCOMPLETE",
            "POLICY_TAX_LOT_ENFORCEMENT_UNAVAILABLE",
            "POLICY_SELL_ELIGIBILITY_INCOMPLETE",
        }
    )
    policy_per_run = frozenset(
        {
            "POLICY_PER_RUN_NEW_BUY_BUDGET_UNAVAILABLE",
            "POLICY_PER_RUN_BUDGET_APPLICABILITY_UNVERIFIED",
        }
    )
    policy_optional = frozenset(
        {
            "POLICY_MAX_NEW_TICKER_RULE_UNAVAILABLE",
            "POLICY_EXTENDED_ACTIVATION_CONSTRAINTS_UNAVAILABLE",
        }
    )
    policy_selected = policy_always | policy_per_run | policy_optional

    # policy_projection._derive_expected_policy_gaps has seven unconditional
    # _ALWAYS_UNAVAILABLE_POLICY_GAPS, one exclusive per-run status branch,
    # and two independently optional SOURCE_VALIDATED/UNAVAILABLE branches.
    assert frozenset(
        code
        for code, _question_class in (
            policy_projection._ALWAYS_UNAVAILABLE_POLICY_GAPS
        )
    ) == policy_always
    assert {
        upstream_code
        for owner, upstream_code in translation_by_upstream
        if owner == policy_owner
    } == policy_selected

    reachable_policy_upstream: set[frozenset[str]] = set()
    for per_run_status in (
        "VALUE_UNAVAILABLE",
        "VALUE_PRESENT_APPLICABILITY_UNVERIFIED",
    ):
        for maximum_new_status in ("SOURCE_VALIDATED", "UNAVAILABLE"):
            for constraints_status in ("SOURCE_VALIDATED", "UNAVAILABLE"):
                gaps = policy_projection._derive_expected_policy_gaps(
                    {
                        "source_record_identity_sha256": SHA_A,
                        "universe_projection": {
                            "known_universe_gaps": [],
                        },
                        "per_run_new_buy_budget": {
                            "status": per_run_status,
                        },
                        "maximum_new_ticker_rules": {
                            "status": maximum_new_status,
                        },
                        "extended_sleeve_constraints": {
                            "status": constraints_status,
                        },
                    }
                )
                codes = tuple(gap["code"] for gap in gaps)
                assert len(codes) == len(set(codes))
                assert set(codes) <= policy_selected, (
                    "P1a added an unmodeled selected limitation"
                )
                reachable_policy_upstream.add(frozenset(codes))

    assert len(reachable_policy_upstream) == 8
    assert {
        code
        for state in reachable_policy_upstream
        for code in state
    } == policy_selected
    assert all(
        len(state & policy_per_run) == 1
        for state in reachable_policy_upstream
    ), "P1a per-run budget limitation states must remain exclusive"
    assert max(map(len, reachable_policy_upstream)) == 10
    reachable_policy = {
        frozenset(
            translation_by_upstream[(policy_owner, code)]
            for code in state
        )
        for state in reachable_policy_upstream
    }

    validated_policy = evidence_bundle._ValidatedPolicyComponent(
        source_record_identity_sha256=SHA_A,
        universe_projection_identity_sha256=SHA_B,
        policy_projection_identity_sha256="3" * 64,
        validation_result_category="PROJECTION_VALID_WITH_GAPS",
    )
    portfolio_components = {
        NOT_SUPPLIED: evidence_bundle._ValidatedPortfolioComponent(
            presence_status=NOT_SUPPLIED,
            portfolio_projection_identity_sha256=None,
            portfolio_source_record_identity_sha256=None,
            validation_result_category=None,
        ),
        SOURCE_ABSENT: evidence_bundle._ValidatedPortfolioComponent(
            presence_status=SOURCE_ABSENT,
            portfolio_projection_identity_sha256="4" * 64,
            portfolio_source_record_identity_sha256=None,
            validation_result_category="PROJECTION_VALID_WITH_GAPS",
        ),
        SOURCE_BOUND: evidence_bundle._ValidatedPortfolioComponent(
            presence_status=SOURCE_BOUND,
            portfolio_projection_identity_sha256="4" * 64,
            portfolio_source_record_identity_sha256="5" * 64,
            validation_result_category="PROJECTION_VALID_WITH_GAPS",
        ),
    }
    e1_gap_codes: dict[str, tuple[str, ...]] = {}
    # evidence_bundle._derive_expected_manifest emits the assembly omission
    # only for NOT_SUPPLIED; both supplied P1b branches must have no E1 gap.
    for branch, component in portfolio_components.items():
        manifest = evidence_bundle._derive_expected_manifest(
            evaluation_timestamp_utc=TIMESTAMP,
            policy=validated_policy,
            portfolio=component,
        )
        gaps = manifest["known_evidence_gaps"]
        assert type(gaps) is list
        e1_gap_codes[branch] = tuple(gap["code"] for gap in gaps)
    assert e1_gap_codes == {
        NOT_SUPPLIED: (
            "EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED",
        ),
        SOURCE_ABSENT: (),
        SOURCE_BOUND: (),
    }, "E1 omission is exclusive to the NOT_SUPPLIED branch"

    p1b_owner = "PORTFOLIO_PROJECTION"
    p1b_selected = frozenset(
        {
            "PORTFOLIO_SOURCE_MISSING",
            "PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE",
            "PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED",
            "PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE",
        }
    )
    p1b_static = frozenset(
        {
            "PORTFOLIO_HOLDINGS_UNSTRUCTURED",
            "PORTFOLIO_OPEN_SELL_ORDERS_UNSTRUCTURED",
            "PORTFOLIO_TAX_LOTS_UNSTRUCTURED",
            "PORTFOLIO_DEPLOYABLE_CASH_UNAVAILABLE",
            "PORTFOLIO_WEIGHTS_UNAVAILABLE",
            "PORTFOLIO_NAV_CONCENTRATION_UNAVAILABLE",
            "PORTFOLIO_LOOKTHROUGH_EXPOSURE_UNAVAILABLE",
        }
    )
    assert frozenset(portfolio_projection._STATIC_GAP_CODES) == p1b_static
    assert portfolio_projection._GAP_CODE_SET == p1b_static | p1b_selected
    assert {
        upstream_code
        for owner, upstream_code in translation_by_upstream
        if owner == p1b_owner
    } == p1b_selected

    outside_record = {
        "ticker": "XYZ",
        "reserved_budget_decimal": "1",
        "stated_compiled_notional_decimal": None,
        "policy_membership_classification": (
            "OUTSIDE_POLICY_UNIVERSE"
        ),
        "policy_role_annotation": None,
        "outside_policy_universe": True,
    }
    run_context = SimpleNamespace(
        evaluation_timestamp_utc=TIMESTAMP,
        evaluation_time_utc=datetime(
            2026,
            7,
            28,
            12,
            34,
            56,
            123456,
            tzinfo=timezone.utc,
        ),
    )

    def valid_p1b_state(
        *,
        source_status: str,
        source_date: str | None,
        open_status: str,
        records: list[dict[str, object]],
    ) -> frozenset[str]:
        projection = portfolio_projection._base_projection(
            evaluation_timestamp_utc=TIMESTAMP,
            policy_projection_identity_sha256=SHA_B,
            portfolio_source_status=source_status,
            portfolio_source_record_identity_sha256=(
                None if source_status == "SOURCE_ABSENT" else SHA_A
            ),
            portfolio_source_date=source_date,
            open_buy_status=open_status,
            open_buy_records=records,
            total_reserved_budget_decimal=(
                None
                if open_status != "SOURCE_VALIDATED"
                else ("1" if records else "0")
            ),
        )
        portfolio_projection._validate_portfolio_semantics(
            projection,
            policy_projection_identity_sha256=SHA_B,
            policy_roles={},
            run_context=run_context,
        )
        gaps = projection["known_gaps"]
        assert type(gaps) is list
        codes = tuple(gap["code"] for gap in gaps)
        assert len(codes) == len(set(codes))
        assert set(codes) <= p1b_static | p1b_selected, (
            "P1b added an unmodeled gap"
        )
        return frozenset(set(codes) & p1b_selected)

    # portfolio_projection._validate_portfolio_semantics permits SOURCE_ABSENT
    # only with SOURCE_ABSENT open buys and no date. A source-bound projection
    # permits SOURCE_VALIDATED records or PARSE_FAILED with records cleared.
    p1b_states = {
        "source_absent": valid_p1b_state(
            source_status="SOURCE_ABSENT",
            source_date=None,
            open_status="SOURCE_ABSENT",
            records=[],
        ),
    }
    for timestamp_state, source_date in (
        ("dated", PORTFOLIO_DATE),
        ("undated", None),
    ):
        p1b_states[f"{timestamp_state}_validated_empty"] = (
            valid_p1b_state(
                source_status="SOURCE_PRESENT_CONTENT_BOUND",
                source_date=source_date,
                open_status="SOURCE_VALIDATED",
                records=[],
            )
        )
        p1b_states[f"{timestamp_state}_validated_outside"] = (
            valid_p1b_state(
                source_status="SOURCE_PRESENT_CONTENT_BOUND",
                source_date=source_date,
                open_status="SOURCE_VALIDATED",
                records=[outside_record],
            )
        )
        p1b_states[f"{timestamp_state}_parse_failed"] = (
            valid_p1b_state(
                source_status="SOURCE_PRESENT_CONTENT_BOUND",
                source_date=source_date,
                open_status="PARSE_FAILED",
                records=[],
            )
        )

    assert p1b_states == {
        "source_absent": frozenset({"PORTFOLIO_SOURCE_MISSING"}),
        "dated_validated_empty": frozenset(),
        "dated_validated_outside": frozenset(
            {"PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE"}
        ),
        "dated_parse_failed": frozenset(
            {"PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED"}
        ),
        "undated_validated_empty": frozenset(
            {"PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE"}
        ),
        "undated_validated_outside": frozenset(
            {
                "PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE",
                "PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE",
            }
        ),
        "undated_parse_failed": frozenset(
            {
                "PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE",
                "PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED",
            }
        ),
    }
    assert {
        code for state in p1b_states.values() for code in state
    } == p1b_selected
    parse_code = "PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED"
    outside_code = "PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE"
    assert all(
        not {parse_code, outside_code} <= state
        for state in p1b_states.values()
    ), "P1b PARSE_FAILED clears records, so outside observations cannot coexist"
    assert max(map(len, p1b_states.values())) == 2

    invalid_parse_outside = portfolio_projection._base_projection(
        evaluation_timestamp_utc=TIMESTAMP,
        policy_projection_identity_sha256=SHA_B,
        portfolio_source_status="SOURCE_PRESENT_CONTENT_BOUND",
        portfolio_source_record_identity_sha256=SHA_A,
        portfolio_source_date=None,
        open_buy_status="PARSE_FAILED",
        open_buy_records=[outside_record],
        total_reserved_budget_decimal=None,
    )
    with pytest.raises(
        portfolio_projection._PortfolioContractFailure,
        match="MMI_PORTFOLIO_PROJECTION_SEMANTIC_INVALID",
    ):
        portfolio_projection._validate_portfolio_semantics(
            invalid_parse_outside,
            policy_projection_identity_sha256=SHA_B,
            policy_roles={},
            run_context=run_context,
        )

    invalid_absent_parse = portfolio_projection._base_projection(
        evaluation_timestamp_utc=TIMESTAMP,
        policy_projection_identity_sha256=SHA_B,
        portfolio_source_status="SOURCE_ABSENT",
        portfolio_source_record_identity_sha256=None,
        portfolio_source_date=None,
        open_buy_status="PARSE_FAILED",
        open_buy_records=[],
        total_reserved_budget_decimal=None,
    )
    with pytest.raises(
        portfolio_projection._PortfolioContractFailure,
        match="MMI_PORTFOLIO_PROJECTION_SEMANTIC_INVALID",
    ):
        portfolio_projection._validate_portfolio_semantics(
            invalid_absent_parse,
            policy_projection_identity_sha256=SHA_B,
            policy_roles={},
            run_context=run_context,
        )

    invalid_absent_outside = portfolio_projection._base_projection(
        evaluation_timestamp_utc=TIMESTAMP,
        policy_projection_identity_sha256=SHA_B,
        portfolio_source_status="SOURCE_ABSENT",
        portfolio_source_record_identity_sha256=None,
        portfolio_source_date=None,
        open_buy_status="SOURCE_VALIDATED",
        open_buy_records=[outside_record],
        total_reserved_budget_decimal="1",
    )
    with pytest.raises(
        portfolio_projection._PortfolioContractFailure,
        match="MMI_PORTFOLIO_PROJECTION_SEMANTIC_INVALID",
    ):
        portfolio_projection._validate_portfolio_semantics(
            invalid_absent_outside,
            policy_projection_identity_sha256=SHA_B,
            policy_roles={},
            run_context=run_context,
        )

    invalid_absent_timestamp = portfolio_projection._base_projection(
        evaluation_timestamp_utc=TIMESTAMP,
        policy_projection_identity_sha256=SHA_B,
        portfolio_source_status="SOURCE_ABSENT",
        portfolio_source_record_identity_sha256=None,
        portfolio_source_date=PORTFOLIO_DATE,
        open_buy_status="SOURCE_ABSENT",
        open_buy_records=[],
        total_reserved_budget_decimal=None,
    )
    with pytest.raises(
        portfolio_projection._PortfolioContractFailure,
        match="MMI_PORTFOLIO_PROJECTION_SEMANTIC_INVALID",
    ):
        portfolio_projection._validate_portfolio_semantics(
            invalid_absent_timestamp,
            policy_projection_identity_sha256=SHA_B,
            policy_roles={},
            run_context=run_context,
        )

    invalid_bound_source_absent = portfolio_projection._base_projection(
        evaluation_timestamp_utc=TIMESTAMP,
        policy_projection_identity_sha256=SHA_B,
        portfolio_source_status="SOURCE_PRESENT_CONTENT_BOUND",
        portfolio_source_record_identity_sha256=SHA_A,
        portfolio_source_date=PORTFOLIO_DATE,
        open_buy_status="SOURCE_ABSENT",
        open_buy_records=[],
        total_reserved_budget_decimal=None,
    )
    with pytest.raises(
        portfolio_projection._PortfolioContractFailure,
        match="MMI_PORTFOLIO_PROJECTION_SEMANTIC_INVALID",
    ):
        portfolio_projection._validate_portfolio_semantics(
            invalid_bound_source_absent,
            policy_projection_identity_sha256=SHA_B,
            policy_roles={},
            run_context=run_context,
        )

    e1_owner = "EVIDENCE_BUNDLE"
    e1_upstream = "EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED"
    reachable_e1_p1b_upstream: set[
        frozenset[tuple[str, str]]
    ] = {
        frozenset({(e1_owner, e1_upstream)}),
        frozenset(
            (p1b_owner, code)
            for code in p1b_states["source_absent"]
        ),
        *(
            frozenset((p1b_owner, code) for code in state)
            for name, state in p1b_states.items()
            if name != "source_absent"
        ),
    }
    assert all(
        not (
            (e1_owner, e1_upstream) in state
            and any(owner == p1b_owner for owner, _code in state)
        )
        for state in reachable_e1_p1b_upstream
    )
    assert max(map(len, reachable_e1_p1b_upstream)) == 2
    reachable_e1_p1b = {
        frozenset(
            translation_by_upstream[(owner, code)]
            for owner, code in state
        )
        for state in reachable_e1_p1b_upstream
    }

    reachable_translation_keys = {
        *((policy_owner, code) for code in policy_selected),
        (e1_owner, e1_upstream),
        *((p1b_owner, code) for code in p1b_selected),
    }
    assert reachable_translation_keys == set(translation_by_upstream), (
        "the reachability proof must cover every translation key"
    )

    combined_states = {
        policy_state | portfolio_state
        for policy_state in reachable_policy
        for portfolio_state in reachable_e1_p1b
    }
    exact_reachable_maximum = max(map(len, combined_states))
    assert exact_reachable_maximum == 12
    assert any(len(state) == 12 for state in combined_states)

    schema = _schema()
    properties = schema["properties"]
    assert type(properties) is dict
    limitation_schema = properties["known_view_limitations"]
    assert type(limitation_schema) is dict
    structural_schema_maximum = limitation_schema["maxItems"]
    assert structural_schema_maximum == 14
    assert exact_reachable_maximum <= structural_schema_maximum


@pytest.mark.parametrize(
    ("owner", "upstream_code", "output_code"),
    EXPECTED_TRANSLATIONS,
)
def test_each_closed_limitation_translation_is_structurally_representable(
    owner: str,
    upstream_code: str,
    output_code: str,
) -> None:
    assert type(upstream_code) is str
    value = _view(SOURCE_BOUND)
    value["known_view_limitations"] = [_limitation(output_code)]
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    mmi_analyst_visible_evidence_view_identity_sha256(value)
    limitations = value["known_view_limitations"]
    assert type(limitations) is list
    assert limitations[0]["owner"] == owner  # type: ignore[index]


@pytest.mark.parametrize(
    ("owner", "code"),
    (
        ("POLICY_PROJECTION", "POLICY_CASH_MODEL_UNAVAILABLE"),
        ("POLICY_PROJECTION", "EXTENDED_THEME_MAP_UNAVAILABLE"),
        ("POLICY_PROJECTION", "VIEW_UNKNOWN"),
        ("UNKNOWN", "VIEW_POLICY_CASH_MODEL_UNAVAILABLE"),
        ("PORTFOLIO_PROJECTION", "VIEW_POLICY_CASH_MODEL_UNAVAILABLE"),
    ),
)
def test_arbitrary_upstream_unknown_or_mismatched_limitations_fail(
    owner: str,
    code: str,
) -> None:
    value = _view()
    value["known_view_limitations"] = [
        {"owner": owner, "code": code, "affected_tickers": []}
    ]
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_duplicate_limitation_code_is_rejected() -> None:
    value = _view()
    value["known_view_limitations"] = [
        _limitation("VIEW_POLICY_CASH_MODEL_UNAVAILABLE"),
        _limitation("VIEW_POLICY_CASH_MODEL_UNAVAILABLE"),
    ]
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_limitation_order_is_structurally_canonical() -> None:
    value = _view()
    limitations = value["known_view_limitations"]
    assert type(limitations) is list
    limitations.reverse()
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    _assert_identity_rejected(value)


def test_more_than_fourteen_limitations_is_rejected() -> None:
    value = _view()
    value["known_view_limitations"] = [
        _limitation(output_code)
        for _owner, _upstream_code, output_code
        in EXPECTED_TRANSLATIONS[:15]
    ]
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_only_outside_policy_limitation_may_carry_tickers() -> None:
    non_ticker = _view()
    limitations = non_ticker["known_view_limitations"]
    assert type(limitations) is list and type(limitations[0]) is dict
    limitations[0]["affected_tickers"] = ["VOO"]
    _assert_schema_rejected(non_ticker)
    _assert_identity_rejected(non_ticker)

    outside_empty = _view()
    outside_empty["known_view_limitations"] = [
        _limitation(OUTSIDE_LIMITATION_CODE, affected_tickers=[])
    ]
    _assert_schema_rejected(outside_empty)
    _assert_identity_rejected(outside_empty)


def test_affected_ticker_lexical_uniqueness_and_bounds_are_structural() -> None:
    invalid = _view()
    invalid["known_view_limitations"] = [
        _limitation(OUTSIDE_LIMITATION_CODE, affected_tickers=["bad"])
    ]
    _assert_schema_rejected(invalid)
    _assert_identity_rejected(invalid)

    duplicate = _view()
    duplicate["known_view_limitations"] = [
        _limitation(
            OUTSIDE_LIMITATION_CODE,
            affected_tickers=["XYZ", "XYZ"],
        )
    ]
    _assert_schema_rejected(duplicate)
    _assert_identity_rejected(duplicate)

    overbound = _view()
    overbound["known_view_limitations"] = [
        _limitation(
            OUTSIDE_LIMITATION_CODE,
            affected_tickers=[
                f"A{index:015d}" for index in range(257)
            ],
        )
    ]
    _assert_schema_rejected(overbound)
    _assert_identity_rejected(overbound)


def test_affected_ticker_cannot_create_new_view_disclosure() -> None:
    value = _view()
    value["known_view_limitations"] = [
        _limitation(
            OUTSIDE_LIMITATION_CODE,
            affected_tickers=["HIDDEN"],
        )
    ]
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    _assert_identity_rejected(value)
    visible = json.dumps(
        {
            "policy_view": value["policy_view"],
            "portfolio_view": value["portfolio_view"],
        }
    )
    assert "HIDDEN" not in visible


def test_internal_ticker_subset_check_is_not_source_authentication() -> None:
    value = _view()
    value["evidence_bundle_identity_sha256"] = SHA_B
    value[IDENTITY_FIELD] = _independent_identity(value)
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(value)
        == value[IDENTITY_FIELD]
    )
    assert not hasattr(
        contracts,
        "validate_mmi_analyst_visible_evidence_view",
    )


def test_identity_matches_independent_length_framed_oracle() -> None:
    for branch in PORTFOLIO_BRANCHES:
        value = _view(branch)
        assert (
            mmi_analyst_visible_evidence_view_identity_sha256(value)
            == _independent_identity(value)
            == value[IDENTITY_FIELD]
        )


def test_identity_is_mapping_insertion_order_independent() -> None:
    value = _view()
    reordered = _reverse_mapping_order(value)
    assert type(reordered) is dict
    assert _independent_identity(reordered) == _independent_identity(value)
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(reordered)
        == mmi_analyst_visible_evidence_view_identity_sha256(value)
    )


def test_identity_excludes_only_its_own_self_field() -> None:
    value = _view()
    expected = mmi_analyst_visible_evidence_view_identity_sha256(value)
    value[IDENTITY_FIELD] = "f" * 64
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(value)
        == expected
    )


def test_every_other_persistent_leaf_changes_identity_or_fails_contract() -> None:
    original = _view()
    original_identity = (
        mmi_analyst_visible_evidence_view_identity_sha256(original)
    )
    tested = 0
    for path in _leaf_paths(original):
        if path == (IDENTITY_FIELD,):
            continue
        value = deepcopy(original)
        observed = _value_at_path(value, path)
        if type(observed) is bool:
            replacement: object = not observed
        elif type(observed) is str and re.fullmatch(
            r"[0-9a-f]{64}",
            observed,
        ):
            replacement = (
                ("e" if observed[0] != "e" else "d") + observed[1:]
            )
        elif observed == TIMESTAMP:
            replacement = "2026-07-29T12:34:56.123456Z"
        elif observed in {POLICY_DATE, PORTFOLIO_DATE}:
            replacement = "2026-07-24"
        elif type(observed) is str and TICKER_RE.fullmatch(observed):
            replacement = "Z" * 16
        elif type(observed) is str:
            replacement = f"{observed}_MUTATED"
        else:
            replacement = "MUTATED"
        _set_path(value, path, replacement)
        try:
            identity = mmi_analyst_visible_evidence_view_identity_sha256(
                value
            )
        except MmiCanonicalizationError:
            pass
        else:
            assert identity != original_identity, path
        tested += 1
    assert tested > 40


def test_instrument_and_observation_order_are_identity_bound() -> None:
    instruments = _view()
    policy = instruments["policy_view"]
    assert type(policy) is dict
    items = policy["analysis_instruments"]
    assert type(items) is list
    items[0], items[1] = items[1], items[0]
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(instruments)
        != mmi_analyst_visible_evidence_view_identity_sha256(_view())
    )

    observations = _view()
    portfolio = observations["portfolio_view"]
    assert type(portfolio) is dict
    items = portfolio["open_buy_observations"]
    assert type(items) is list
    items[0], items[1] = items[1], items[0]
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(observations)
        != mmi_analyst_visible_evidence_view_identity_sha256(_view())
    )


def test_all_portfolio_branches_have_distinct_identities() -> None:
    identities = {
        mmi_analyst_visible_evidence_view_identity_sha256(_view(branch))
        for branch in PORTFOLIO_BRANCHES
    }
    assert len(identities) == 3


def test_resealed_structural_identity_is_not_source_authentication() -> None:
    original = _view()
    mutated = deepcopy(original)
    mutated["evidence_bundle_identity_sha256"] = SHA_B
    mutated[IDENTITY_FIELD] = _independent_identity(mutated)
    validate_artifact_schema(mutated, schema_name=SCHEMA_NAME)
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(mutated)
        == mutated[IDENTITY_FIELD]
    )
    assert mutated[IDENTITY_FIELD] != original[IDENTITY_FIELD]
    assert not hasattr(
        contracts,
        "validate_mmi_analyst_visible_evidence_view",
    )


def test_exactly_six_persistent_mmi_identity_domains_exist() -> None:
    domains_by_name = {
        name: value
        for name, value in canonical.__dict__.items()
        if name.startswith("MMI_")
        and name.endswith("_IDENTITY_DOMAIN")
    }
    assert domains_by_name == {
        "MMI_SOURCE_RECORD_IDENTITY_DOMAIN": (
            b"mmi_source_record_v1\0"
        ),
        "MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN": (
            b"mmi_universe_projection_v1\0"
        ),
        "MMI_POLICY_PROJECTION_IDENTITY_DOMAIN": (
            b"mmi_policy_projection_v1\0"
        ),
        "MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN": (
            b"mmi_portfolio_snapshot_projection_v1\0"
        ),
        "MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN": (
            b"mmi_authenticated_evidence_bundle_v1\0"
        ),
    }
    assert _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_IDENTITY_DOMAIN == (
        b"mmi_analyst_visible_evidence_view_v1\0"
    )
    domains = (
        *domains_by_name.values(),
        _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_IDENTITY_DOMAIN,
    )
    assert len(domains) == len(set(domains)) == 6


def test_first_five_domains_and_existing_fixed_identities_are_unchanged() -> None:
    assert MMI_SOURCE_RECORD_IDENTITY_DOMAIN == b"mmi_source_record_v1\0"
    assert MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN == (
        b"mmi_universe_projection_v1\0"
    )
    assert MMI_POLICY_PROJECTION_IDENTITY_DOMAIN == (
        b"mmi_policy_projection_v1\0"
    )
    assert MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN == (
        b"mmi_portfolio_snapshot_projection_v1\0"
    )
    assert MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN == (
        b"mmi_authenticated_evidence_bundle_v1\0"
    )
    assert _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_IDENTITY_DOMAIN == (
        b"mmi_analyst_visible_evidence_view_v1\0"
    )
    fixtures = (
        (
            MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
            "source_record_identity_sha256",
            "SOURCE",
            "5b1cc0a5ef02ecc271adcf21bd43db087"
            "e261a2f82f7bc873369d4ff5e1f435d",
        ),
        (
            MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
            "universe_projection_identity_sha256",
            "UNIVERSE",
            "fbf1729e36c909530cabc60a131d4838"
            "547ef78d8f9bf767c338868d63e7bbf5",
        ),
        (
            MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
            "policy_projection_identity_sha256",
            "POLICY",
            "cbf39ca850907a4db732a856eb1a1318"
            "b13384c4d6b3af97b935b148158ce233",
        ),
        (
            MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN,
            "portfolio_projection_identity_sha256",
            "PORTFOLIO",
            "371e25402d81be10369eb76b5d860587"
            "819030b912bd03dcef938f67ea66a9c1",
        ),
    )
    for domain, identity_field, kind, expected in fixtures:
        value = {
            "fixture_kind": kind,
            "fixture_version": 1,
            identity_field: "0" * 64,
        }
        assert (
            record_identity_sha256(
                value,
                identity_field=identity_field,
                domain=domain,
            )
            == expected
        )


def test_exact_structural_maximum_artifact_size_is_independently_proven() -> None:
    value = _maximum_view()
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert len(encoded) == 47_584
    assert (
        len(encoded)
        == MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES
    )
    assert len(canonical_json_bytes(value)) == len(encoded)
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(value)
        == value[IDENTITY_FIELD]
        == _independent_identity(value)
    )

    policy = value["policy_view"]
    assert type(policy) is dict
    instruments = policy["analysis_instruments"]
    assert type(instruments) is list and len(instruments) == 256
    portfolio = value["portfolio_view"]
    assert type(portfolio) is dict
    observations = portfolio["open_buy_observations"]
    assert type(observations) is list and len(observations) == 256
    limitations = value["known_view_limitations"]
    assert type(limitations) is list and len(limitations) == 14
    assert all(
        len(item["ticker"]) == 16  # type: ignore[index]
        for item in instruments
    )


def test_identity_helper_enforces_full_artifact_not_only_preimage_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _maximum_view()
    full_size = len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    preimage = deepcopy(value)
    preimage.pop(IDENTITY_FIELD)
    preimage_size = len(
        json.dumps(
            preimage,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert preimage_size < full_size
    monkeypatch.setattr(
        contracts,
        "MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES",
        full_size - 1,
    )
    with pytest.raises(
        MmiCanonicalizationError,
        match="MMI_CANONICAL_SIZE_EXCEEDED",
    ):
        mmi_analyst_visible_evidence_view_identity_sha256(value)


def test_schema_and_contract_have_no_theme_or_generic_string_channel() -> None:
    schema = _schema()
    schema_text = json.dumps(schema, sort_keys=True).casefold()
    for forbidden in (
        "theme",
        "theme_slug",
        "theme_name",
        "category",
        "sector",
        "industry",
        "description",
        "metadata",
        "notes",
        "extension",
    ):
        assert forbidden not in schema_text
    typed_strings = []

    def collect(value: object) -> None:
        if type(value) is dict:
            if value.get("type") == "string":
                typed_strings.append(value)
            for child in value.values():
                collect(child)
        elif type(value) is list:
            for child in value:
                collect(child)

    collect(schema)
    assert typed_strings
    assert all(
        "pattern" in item or "enum" in item or "const" in item
        for item in typed_strings
    )


@pytest.mark.parametrize(
    "field",
    (
        "raw_source",
        "raw_row",
        "account_id",
        "broker_id",
        "holding_text",
        "sell_instruction",
        "tax_lot_id",
        "cost_basis",
        "money",
        "amount",
        "reserved_budget_decimal",
        "cap",
        "quantity",
        "price",
        "order_id",
        "absolute_path",
        "parser_exception",
        "provenance_token",
        "prompt",
        "response",
        "provider",
        "model",
        "token_budget",
        "persona",
        "operator_workflow",
        "theme",
        "sector",
        "category",
    ),
)
def test_prohibited_private_or_later_phase_fields_fail_at_every_object_level(
    field: str,
) -> None:
    candidates: list[dict[str, object]] = []

    top = _view()
    top[field] = "forbidden"
    candidates.append(top)

    policy = _view()
    policy_value = policy["policy_view"]
    assert type(policy_value) is dict
    policy_value[field] = "forbidden"
    candidates.append(policy)

    instrument = _view()
    policy_value = instrument["policy_view"]
    assert type(policy_value) is dict
    instruments = policy_value["analysis_instruments"]
    assert type(instruments) is list and type(instruments[0]) is dict
    instruments[0][field] = "forbidden"
    candidates.append(instrument)

    portfolio = _view()
    portfolio_value = portfolio["portfolio_view"]
    assert type(portfolio_value) is dict
    portfolio_value[field] = "forbidden"
    candidates.append(portfolio)

    observation = _view()
    portfolio_value = observation["portfolio_view"]
    assert type(portfolio_value) is dict
    observations = portfolio_value["open_buy_observations"]
    assert type(observations) is list and type(observations[0]) is dict
    observations[0][field] = "forbidden"
    candidates.append(observation)

    coverage = _view()
    portfolio_value = coverage["portfolio_view"]
    assert type(portfolio_value) is dict
    coverage_value = portfolio_value["fact_coverage_statuses"]
    assert type(coverage_value) is dict
    coverage_value[field] = "forbidden"
    candidates.append(coverage)

    limitation = _view()
    limitations = limitation["known_view_limitations"]
    assert type(limitations) is list and type(limitations[0]) is dict
    limitations[0][field] = "forbidden"
    candidates.append(limitation)

    for candidate in candidates:
        _assert_schema_rejected(candidate)
        _assert_identity_rejected(candidate)


def test_exact_schema_property_ownership_has_no_private_value_channel() -> None:
    schema = _schema()
    properties = {
        key
        for object_schema in _object_schemas(schema)
        for key in object_schema.get("properties", {})
    }
    assert properties == {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "evaluation_timestamp_utc",
        "evidence_bundle_identity_sha256",
        "policy_view",
        "portfolio_view",
        "known_view_limitations",
        "view_completeness_status",
        IDENTITY_FIELD,
        "policy_as_of_date",
        "policy_method",
        "benchmark_reference_instruments",
        "analysis_instruments",
        "extended_activation_status",
        "instrument_availability_observation_status",
        "target_weights_absence_reason",
        "ticker",
        "policy_role",
        "presence_status",
        "portfolio_source_date",
        "open_buy_status",
        "open_buy_observations",
        "fact_coverage_statuses",
        "policy_membership_classification",
        "holdings",
        "cash",
        "deployable_cash",
        "open_sells",
        "tax_lots",
        "holding_dates",
        "gains_losses",
        "weights",
        "nav_concentration",
        "look_through_exposure",
        "owner",
        "code",
        "affected_tickers",
    }


def test_v1b_has_no_builder_validator_module_export_or_consumer() -> None:
    root = repo_root()
    production_root = root / "src/investment_orchestrator"
    production_paths = tuple(sorted(production_root.rglob("*.py")))
    assert len(production_paths) == 130
    relative = {
        path: path.relative_to(root).as_posix()
        for path in production_paths
    }
    trees = {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in production_paths
    }
    assert not tuple(
        relative[path]
        for path in production_paths
        if "analyst_visible_evidence_view" in path.stem
    )

    prohibited_surfaces = {
        "build_mmi_analyst_visible_evidence_view",
        "validate_mmi_analyst_visible_evidence_view",
    }
    defined = {
        node.name
        for tree in trees.values()
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not defined & prohibited_surfaces

    helper_name = "mmi_analyst_visible_evidence_view_identity_sha256"
    helper_definitions = tuple(
        relative[path]
        for path, tree in trees.items()
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == helper_name
            for node in tree.body
        )
    )
    assert helper_definitions == (
        "src/investment_orchestrator/mmi/contracts.py",
    )
    helper_consumers = tuple(
        relative[path]
        for path, tree in trees.items()
        if relative[path]
        != "src/investment_orchestrator/mmi/contracts.py"
        and any(
            (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id == helper_name
            )
            or (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and node.attr == helper_name
            )
            for node in ast.walk(tree)
        )
    )
    assert helper_consumers == ()

    schema_name_owners = tuple(
        relative[path]
        for path, tree in trees.items()
        if any(
            isinstance(node, ast.Constant)
            and node.value == SCHEMA_NAME
            for node in ast.walk(tree)
        )
    )
    assert schema_name_owners == ()

    init_path = root / "src/investment_orchestrator/mmi/__init__.py"
    init_tree = trees[init_path]
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        for node in ast.walk(init_tree)
    )
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"__getattr__", "__dir__"}
        for node in init_tree.body
    )


def test_contract_surface_is_structural_only_and_has_no_runtime_access() -> None:
    function = contracts.mmi_analyst_visible_evidence_view_identity_sha256
    assert function.__module__ == (
        "investment_orchestrator.mmi.contracts"
    )
    assert function.__doc__ == (
        "Calculate structural view identity without authenticating inputs."
    )
    source = (
        repo_root()
        / "src/investment_orchestrator/mmi/contracts.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not {
        "os",
        "glob",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "openai",
    } & imports
    assert "validate_artifact_schema" not in source
    assert "capture_current_mmi_source" not in source
    assert "build_mmi_authenticated_evidence_bundle" not in source


def test_contract_constants_are_exact_and_add_no_theme_taxonomy() -> None:
    assert MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_SCHEMA_VERSION == (
        "mmi_analyst_visible_evidence_view_v1"
    )
    assert MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_ARTIFACT_KIND == (
        "MMI_ANALYST_VISIBLE_EVIDENCE_VIEW"
    )
    assert MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES == (
        47_584
    )
    assert all(
        type(owner) is str
        and type(upstream_code) is str
        and type(output_code) is str
        for owner, upstream_code, output_code in (
            MMI_ANALYST_VIEW_LIMITATION_TRANSLATIONS
        )
    )
    assert "theme" not in json.dumps(
        (
            MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_SCHEMA_VERSION,
            MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_ARTIFACT_KIND,
            MMI_ANALYST_VIEW_LIMITATION_TRANSLATIONS,
        )
    ).casefold()
