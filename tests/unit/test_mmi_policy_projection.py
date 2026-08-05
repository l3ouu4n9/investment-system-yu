from __future__ import annotations

import copy
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext
import hashlib
import inspect
import json
from pathlib import Path
import struct
from types import MappingProxyType

import pytest
import yaml

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiProjectionRunContext,
    MmiProjectionResultCategory,
    MmiSourceRole,
    begin_mmi_projection_run,
    _begin_mmi_projection_run_with_clock,
    _create_mmi_captured_source,
)
from investment_orchestrator.mmi.policy_projection import (
    MAXIMUM_POLICY_CANONICAL_BYTES,
    MAXIMUM_UNIVERSE_CANONICAL_BYTES,
    POLICY_METHOD,
    _ProjectionContractFailure,
    _parse_strict_strategy_settings,
    _validate_policy_semantics,
    _validate_universe_semantics,
    build_mmi_policy_projection,
    validate_mmi_policy_projection,
)
from investment_orchestrator.mmi.source_capture import (
    _capture_mmi_source_at_root,
    capture_current_mmi_source,
)


EVALUATION_TIME = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)


class _FixedClock:
    def now_utc(self) -> datetime:
        return EVALUATION_TIME


def _valid_settings() -> dict[str, object]:
    return {
        "as_of": "2026-07-24",
        "run_timestamp_et": "2026-07-24 10:00 ET",
        "benchmark": "QQQ",
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.00,
        "relative_rotation_enabled": True,
        "relative_rotation_guardrails": {
            "require_same_role_for_rotation": True,
            "min_score_gap_to_rotate": 2,
            "do_not_rotate_if_current_holding_still_role_valid": True,
            "no_rotation_on_one_rank_change_only": True,
        },
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
        "user_approved_extended_etf_static_list": ["QUAL", "CIBR"],
        "user_approved_extended_etf_theme_map": {
            "QUAL": {"theme_bucket": "quality_factor"},
            "CIBR": {"theme_bucket": "cybersecurity"},
        },
        "active_shortlist_size_rule": {
            "benchmark_carrier": 1,
            "diversified_core_buffer_max": 1,
            "sector_alpha_tilt_max": 1,
            "extended_etf_minority_sleeve_max": 2,
        },
        "max_new_tickers_per_week": {
            "base_universe_new_tickers_per_week": 0,
            "extended_etf_sleeve_new_tickers_per_week": 2,
        },
        "extended_etf_constraints": {
            "sleeve_budget_cap_pct_of_total_open_orders": 0.35,
            "single_extended_etf_budget_cap_pct_of_total_open_orders": 0.20,
            "activation_minimum_effective_budget_pct_of_total_open_orders": 0.04,
            "max_same_theme_extended_etf_count": 1,
            "max_same_theme_budget_pct_of_total_open_orders": 0.25,
            "require_distinct_theme_buckets_when_multiple_extended_etfs": True,
            "ignored_free_text": "not projected",
        },
    }


def _raw_settings(settings: dict[str, object]) -> bytes:
    return yaml.safe_dump(
        settings,
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")


def _install_and_capture(
    root: Path,
    raw: bytes,
) -> MmiCapturedSource:
    path = root / "inputs/current/strategy_settings.yaml"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    result = _capture_mmi_source_at_root(
        root,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert result.valid, result.reason_codes
    assert result.source is not None
    return result.source


def _build(
    root: Path,
    settings: dict[str, object] | None = None,
    *,
    raw: bytes | None = None,
):
    source, run_context = _source_and_run_context(
        root,
        settings,
        raw=raw,
    )
    return build_mmi_policy_projection(
        source,
        run_context=run_context,
    )


def _source_and_run_context(
    root: Path,
    settings: dict[str, object] | None = None,
    *,
    raw: bytes | None = None,
) -> tuple[MmiCapturedSource, MmiProjectionRunContext]:
    source = _install_and_capture(
        root,
        raw
        if raw is not None
        else _raw_settings(
            settings if settings is not None else _valid_settings()
        ),
    )
    return (
        source,
        _begin_mmi_projection_run_with_clock(_FixedClock()),
    )


def _projection_with_contract(
    root: Path,
    settings: dict[str, object] | None = None,
    *,
    raw: bytes | None = None,
) -> tuple[
    dict[str, object],
    MmiCapturedSource,
    MmiProjectionRunContext,
]:
    source, run_context = _source_and_run_context(
        root,
        settings,
        raw=raw,
    )
    result = build_mmi_policy_projection(
        source,
        run_context=run_context,
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    return dict(result.projection), source, run_context


def _projection(root: Path) -> dict[str, object]:
    projection, _source, _run_context = _projection_with_contract(root)
    return projection


def _independent_record_identity(
    record: dict[str, object],
    *,
    identity_field: str,
    domain: bytes,
) -> str:
    preimage = deepcopy(record)
    preimage.pop(identity_field, None)
    canonical = json.dumps(
        preimage,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(
        domain + struct.pack(">Q", len(canonical)) + canonical
    ).hexdigest()


def _reseal_projection(projection: dict[str, object]) -> None:
    universe = projection["universe_projection"]
    assert isinstance(universe, dict)
    universe["universe_projection_identity_sha256"] = (
        _independent_record_identity(
            universe,
            identity_field="universe_projection_identity_sha256",
            domain=MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
        )
    )
    projection["universe_projection_identity_sha256"] = universe[
        "universe_projection_identity_sha256"
    ]
    projection["policy_projection_identity_sha256"] = (
        _independent_record_identity(
            projection,
            identity_field="policy_projection_identity_sha256",
            domain=MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
        )
    )


def _assert_resealed_policy_semantic_rejection(
    projection: dict[str, object],
    *,
    reason_code: str,
    source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> None:
    _reseal_projection(projection)
    with pytest.raises(_ProjectionContractFailure) as exc_info:
        _validate_policy_semantics(projection)
    assert exc_info.value.code == reason_code

    validation = validate_mmi_policy_projection(
        projection,
        source=source,
        run_context=run_context,
    )
    assert not validation.valid
    assert validation.authority_effect == "NONE"
    assert validation.reason_codes in {
        (reason_code,),
        ("MMI_POLICY_PROJECTION_SCHEMA_INVALID",),
    }


def _assert_resealed_universe_semantic_rejection(
    projection: dict[str, object],
    *,
    reason_code: str,
    validation_reason_code: str,
    source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> None:
    _reseal_projection(projection)
    universe = projection["universe_projection"]
    assert isinstance(universe, dict)
    with pytest.raises(_ProjectionContractFailure) as exc_info:
        _validate_universe_semantics(universe)
    assert exc_info.value.code == reason_code

    validation = validate_mmi_policy_projection(
        projection,
        source=source,
        run_context=run_context,
    )
    assert not validation.valid
    assert validation.authority_effect == "NONE"
    assert validation.reason_codes == (validation_reason_code,)


def _forged_source(
    source: MmiCapturedSource,
    *,
    raw_bytes: bytes,
    source_record: dict[str, object],
    seal: bytes,
) -> MmiCapturedSource:
    forged = object.__new__(MmiCapturedSource)
    object.__setattr__(forged, "role", MmiSourceRole.STRATEGY_SETTINGS)
    object.__setattr__(forged, "raw_bytes", raw_bytes)
    object.__setattr__(
        forged,
        "source_record",
        MappingProxyType(dict(source_record)),
    )
    object.__setattr__(forged, "_provenance_seal", seal)
    return forged


def test_current_repository_settings_build_a_valid_report_only_projection() -> None:
    # Live-current repository sentinel.  It validates the actual tracked
    # strategy settings through the public production owners: the repository
    # root locator, the code-owned clock, and the public source capture.  It
    # deliberately freezes no operational scalar, so a routine operator input
    # refresh cannot make it stale, while a genuinely future-dated or
    # malformed current input still fails closed through production codes.
    settings_path = repo_root() / "inputs/current/strategy_settings.yaml"
    raw = settings_path.read_bytes()
    expected_digest = hashlib.sha256(raw).hexdigest()

    capture = capture_current_mmi_source(
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=expected_digest,
    )
    assert capture.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    ), capture.reason_codes
    assert capture.authority_effect == "NONE"
    assert capture.source is not None
    source = capture.source
    assert source.role is MmiSourceRole.STRATEGY_SETTINGS
    assert source.raw_bytes == raw

    record = dict(source.source_record)
    assert record["repository_relative_locator"] == (
        "inputs/current/strategy_settings.yaml"
    )
    assert record["expected_sha256"] == expected_digest
    assert record["observed_sha256"] == expected_digest
    assert record["observed_size_bytes"] == len(raw)
    assert record["content_binding_status"] == "EXPECTED_SHA256_MATCHED"
    assert record["stable_read_status"] == "STABLE_BEFORE_AND_AFTER"
    assert record["regular_file_status"] == "REGULAR_FILE"
    assert record["operator_origin_authentication"] == "NOT_ESTABLISHED"
    assert record["authority_effect"] == "NONE"
    assert record["source_record_identity_sha256"] == (
        _independent_record_identity(
            record,
            identity_field="source_record_identity_sha256",
            domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        )
    )

    run_context = begin_mmi_projection_run()
    assert run_context.authority_effect == "NONE"
    assert run_context.evaluation_time_utc.utcoffset().total_seconds() == 0
    assert run_context.evaluation_timestamp_utc.endswith("Z")

    result = build_mmi_policy_projection(
        source,
        run_context=run_context,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    ), result.reason_codes
    assert result.authority_effect == "NONE"
    assert result.projection is not None
    projection = dict(result.projection)
    assert projection["policy_method"] == POLICY_METHOD
    assert projection["report_only"] is True
    assert projection["authority_effect"] == "NONE"
    assert projection["target_weights_present"] is False
    assert projection["target_weights"] == []
    assert (
        projection["target_weights_absence_reason"]
        == "POLICY_METHOD_HAS_NO_TARGET_WEIGHTS"
    )
    assert projection["source_record_identity_sha256"] == (
        record["source_record_identity_sha256"]
    )

    # Future-date protection, asserted against the real code-owned clock
    # rather than a frozen literal.  A future-dated current input is rejected
    # upstream by MMI_POLICY_AS_OF_FUTURE before this point is reached.
    policy_as_of = date.fromisoformat(projection["policy_as_of_date"])
    assert policy_as_of <= run_context.evaluation_time_utc.date()

    # Neither budget scalar below is frozen here: both legitimately change on
    # every operator input refresh.  The expected values are re-derived from
    # the same captured current bytes through the strict settings parser,
    # which is a separate production path from the projection builder under
    # test.  Both comparisons are exact Decimal comparisons; no float is
    # involved anywhere.
    parsed_settings = _parse_strict_strategy_settings(raw)

    cap = projection["hard_open_orders_budget_cap"]
    assert set(cap) == {
        "currency",
        "amount_decimal",
        "validation_status",
        "authority_effect",
    }
    assert cap["currency"] == "USD"
    assert cap["validation_status"] == "SOURCE_VALIDATED"
    assert cap["authority_effect"] == "NONE"
    assert type(cap["amount_decimal"]) is str
    cap_amount = Decimal(cap["amount_decimal"])
    assert cap_amount.is_finite()
    assert cap_amount >= 0
    assert cap_amount == parsed_settings["hard_cap_open_orders_budget"]
    assert "HARD_OPEN_ORDERS_BUDGET_CAP_VALIDATED" in (
        projection["policy_completeness_statuses"]
    )

    per_run = projection["per_run_new_buy_budget"]
    assert set(per_run) == {
        "status",
        "currency",
        "amount_decimal",
        "authority_effect",
    }
    assert per_run["status"] == "VALUE_PRESENT_APPLICABILITY_UNVERIFIED"
    assert per_run["currency"] == "USD"
    assert per_run["authority_effect"] == "NONE"
    assert type(per_run["amount_decimal"]) is str
    per_run_amount = Decimal(per_run["amount_decimal"])
    assert per_run_amount.is_finite()
    assert per_run_amount >= 0
    assert per_run_amount == parsed_settings["target_new_buy_budget_this_run"]
    # The unverified applicability of this budget is recorded consistently in
    # all three places the contract owns, and the completeness statuses claim
    # no validated per-run budget.
    assert "POLICY_PER_RUN_BUDGET_APPLICABILITY_UNVERIFIED" in (
        result.reason_codes
    )
    assert "POLICY_PER_RUN_BUDGET_APPLICABILITY_UNVERIFIED" in tuple(
        gap["code"] for gap in projection["known_policy_gaps"]
    )
    assert not any(
        "PER_RUN" in status
        for status in projection["policy_completeness_statuses"]
    )

    serialized = json.dumps(projection).casefold()
    for forbidden in (
        "order_compilation",
        "execution_authority",
        "publication_authority",
        "permission",
        "allowed_actions",
        "blocked_actions",
    ):
        assert forbidden not in serialized

    repeat = build_mmi_policy_projection(
        source,
        run_context=run_context,
    )
    assert repeat.projection is not None
    assert dict(repeat.projection) == projection
    assert projection["policy_projection_identity_sha256"] == (
        _independent_record_identity(
            projection,
            identity_field="policy_projection_identity_sha256",
            domain=MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
        )
    )
    validate_artifact_schema(
        projection,
        schema_name="mmi_policy_projection_v1.schema.json",
    )

    validation = validate_mmi_policy_projection(
        dict(projection),
        source=source,
        run_context=run_context,
    )
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert validation.authority_effect == "NONE"


def test_policy_and_universe_identities_are_independent_and_stable(
    tmp_path: Path,
) -> None:
    first = _projection(tmp_path / "first")
    second = _projection(tmp_path / "second")
    assert first == second
    universe = first["universe_projection"]
    assert (
        universe["universe_projection_identity_sha256"]
        == record_identity_sha256(
            universe,
            identity_field="universe_projection_identity_sha256",
            domain=MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
            maximum_bytes=MAXIMUM_UNIVERSE_CANONICAL_BYTES,
        )
        == _independent_record_identity(
            universe,
            identity_field="universe_projection_identity_sha256",
            domain=MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
        )
    )
    assert (
        first["policy_projection_identity_sha256"]
        == record_identity_sha256(
            first,
            identity_field="policy_projection_identity_sha256",
            domain=MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
            maximum_bytes=MAXIMUM_POLICY_CANONICAL_BYTES,
        )
        == _independent_record_identity(
            first,
            identity_field="policy_projection_identity_sha256",
            domain=MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
        )
    )
    assert (
        first["policy_projection_identity_sha256"]
        != universe["universe_projection_identity_sha256"]
    )
    assert not {
        "portfolio_projection_identity_sha256",
        "evidence_bundle_identity_sha256",
        "contract_catalog_identity_sha256",
        "source_generation_identity_sha256",
        "analyst_visible_view_identity_sha256",
        "input_package_identity_sha256",
        "rendered_prompt_identity_sha256",
    } & set(first)


def test_projection_decimal_values_and_identities_ignore_ambient_context(
    tmp_path: Path,
) -> None:
    raw = _raw_settings(_valid_settings())
    marker = b"hard_cap_open_orders_budget: 38211.29"
    exact = (
        b"hard_cap_open_orders_budget: "
        b"12345678901234567890123456789.1234567890123456789"
    )
    assert raw.count(marker) == 1
    raw = raw.replace(marker, exact)
    source = _install_and_capture(tmp_path, raw)
    projections: list[dict[str, object]] = []
    for precision, rounding in (
        (3, ROUND_DOWN),
        (6, ROUND_UP),
        (28, ROUND_DOWN),
        (50, ROUND_UP),
    ):
        with localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            result = build_mmi_policy_projection(
                source,
                run_context=_begin_mmi_projection_run_with_clock(
                    _FixedClock()
                ),
            )
        assert result.valid, result.reason_codes
        projections.append(dict(result.projection))
    assert all(value == projections[0] for value in projections[1:])
    assert projections[0]["hard_open_orders_budget_cap"][
        "amount_decimal"
    ] == (
        "12345678901234567890123456789."
        "1234567890123456789"
    )


def test_universe_projection_preserves_source_order_and_analysis_scope(
    tmp_path: Path,
) -> None:
    settings = _valid_settings()
    settings["core_universe"] = ["VT", "QQQ", "VOO"]
    settings["satellite_universe"] = ["IGV", "SMH"]
    settings["user_approved_extended_etf_static_list"] = ["CIBR", "QUAL"]
    result = _build(tmp_path, settings)
    assert result.valid
    universe = result.projection["universe_projection"]
    assert universe["analysis_scope_instruments"] == [
        "VT",
        "QQQ",
        "VOO",
        "IGV",
        "SMH",
        "CIBR",
        "QUAL",
    ]
    assert universe["role_by_ticker"] == {
        "VT": "CORE",
        "QQQ": "CORE",
        "VOO": "CORE",
        "IGV": "SATELLITE",
        "SMH": "SATELLITE",
        "CIBR": "APPROVED_EXTENDED",
        "QUAL": "APPROVED_EXTENDED",
    }
    assert universe["extended_activation_status"] == (
        "NOT_EVALUATED_REPORT_ONLY"
    )
    assert universe["instrument_availability_observation_status"] == (
        "NOT_DETERMINISTICALLY_AVAILABLE"
    )


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda value: value.pop("core_universe"),
            "MMI_UNIVERSE_CORE_MISSING",
        ),
        (
            lambda value: value.update({"core_universe": []}),
            "MMI_UNIVERSE_CORE_EMPTY",
        ),
        (
            lambda value: value.update({"core_universe": "QQQ"}),
            "MMI_UNIVERSE_CORE_INVALID",
        ),
        (
            lambda value: value.update({"core_universe": ["QQQ", "QQQ"]}),
            "MMI_UNIVERSE_CORE_INVALID_DUPLICATE",
        ),
        (
            lambda value: value.pop("satellite_universe"),
            "MMI_UNIVERSE_SATELLITE_MISSING",
        ),
        (
            lambda value: value.update({"satellite_universe": []}),
            "MMI_UNIVERSE_SATELLITE_EMPTY",
        ),
        (
            lambda value: value.update({"satellite_universe": [123]}),
            "MMI_UNIVERSE_SATELLITE_INVALID",
        ),
        (
            lambda value: value.update({"satellite_universe": ["QQQ"]}),
            "MMI_UNIVERSE_CORE_SATELLITE_OVERLAP",
        ),
        (
            lambda value: value.pop(
                "user_approved_extended_etf_static_list"
            ),
            "MMI_UNIVERSE_APPROVED_EXTENDED_MISSING",
        ),
        (
            lambda value: value.update(
                {"user_approved_extended_etf_static_list": "QUAL"}
            ),
            "MMI_UNIVERSE_APPROVED_EXTENDED_INVALID",
        ),
        (
            lambda value: value.update(
                {
                    "user_approved_extended_etf_static_list": [
                        "QUAL",
                        "QUAL",
                    ]
                }
            ),
            "MMI_UNIVERSE_APPROVED_EXTENDED_INVALID_DUPLICATE",
        ),
        (
            lambda value: value.update(
                {"user_approved_extended_etf_static_list": ["QQQ"]}
            ),
            "MMI_UNIVERSE_EXTENDED_BASE_OVERLAP",
        ),
        (
            lambda value: value.update({"core_universe": ["qqq"]}),
            "MMI_UNIVERSE_CORE_INVALID",
        ),
        (
            lambda value: value.update({"benchmark": "SPY"}),
            "MMI_UNIVERSE_BENCHMARK_NOT_CORE",
        ),
    ],
)
def test_invalid_deterministic_universe_is_blocking(
    tmp_path: Path,
    mutation,
    code: str,
) -> None:
    settings = _valid_settings()
    mutation(settings)
    result = _build(tmp_path, settings)
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (code,)
    assert result.projection is None
    assert result.authority_effect == "NONE"


def test_explicit_empty_approved_extended_list_is_valid_zero_members(
    tmp_path: Path,
) -> None:
    settings = _valid_settings()
    settings["user_approved_extended_etf_static_list"] = []
    settings["user_approved_extended_etf_theme_map"] = {}
    result = _build(tmp_path, settings)
    assert result.valid
    universe = result.projection["universe_projection"]
    assert universe["approved_extended_universe"] == []
    assert universe["extended_membership_status"] == (
        "APPROVED_STATIC_MEMBERS_EMPTY"
    )
    assert universe["analysis_scope_instruments"] == [
        "QQQ",
        "VOO",
        "VTI",
        "VT",
        "SMH",
        "IGV",
    ]


def test_missing_and_partial_theme_maps_are_bounded_gaps_not_membership_changes(
    tmp_path: Path,
) -> None:
    missing = _valid_settings()
    missing.pop("user_approved_extended_etf_theme_map")
    missing_result = _build(tmp_path / "missing", missing)
    assert missing_result.valid
    missing_universe = missing_result.projection["universe_projection"]
    assert missing_universe["approved_extended_universe"] == ["QUAL", "CIBR"]
    assert missing_universe["approved_extended_members_without_theme"] == [
        "QUAL",
        "CIBR",
    ]
    assert [gap["code"] for gap in missing_universe["known_universe_gaps"]] == [
        "EXTENDED_THEME_MAP_UNAVAILABLE"
    ]

    partial = _valid_settings()
    partial["user_approved_extended_etf_theme_map"].pop("CIBR")
    partial_result = _build(tmp_path / "partial", partial)
    assert partial_result.valid
    partial_universe = partial_result.projection["universe_projection"]
    assert partial_universe["analysis_scope_instruments"][-2:] == [
        "QUAL",
        "CIBR",
    ]
    assert partial_universe["approved_extended_members_without_theme"] == [
        "CIBR"
    ]
    assert [gap["code"] for gap in partial_universe["known_universe_gaps"]] == [
        "EXTENDED_ETF_THEME_MAPPING_INCOMPLETE"
    ]


@pytest.mark.parametrize(
    "theme_map",
    [
        "not-a-map",
        {"QUAL": "quality_factor"},
        {"QUAL": {"theme_bucket": ""}},
        {"QUAL": {"theme_bucket": "quality_factor", "extra": True}},
        {"PAVE": {"theme_bucket": "infrastructure"}},
    ],
)
def test_malformed_or_outside_theme_mapping_is_blocking(
    tmp_path: Path,
    theme_map: object,
) -> None:
    settings = _valid_settings()
    settings["user_approved_extended_etf_theme_map"] = theme_map
    result = _build(tmp_path, settings)
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    if isinstance(theme_map, dict) and "PAVE" in theme_map:
        assert result.reason_codes == (
            "MMI_UNIVERSE_THEME_KEY_OUTSIDE_APPROVED_EXTENDED",
        )
    else:
        assert result.reason_codes == ("MMI_UNIVERSE_THEME_MAP_INVALID",)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda value: value.pop("hard_cap_open_orders_budget"),
            "MMI_POLICY_HARD_OPEN_ORDERS_BUDGET_CAP_INVALID",
        ),
        (
            lambda value: value.update(
                {"hard_cap_open_orders_budget": 0}
            ),
            "MMI_POLICY_HARD_OPEN_ORDERS_BUDGET_CAP_INVALID",
        ),
        (
            lambda value: value.pop("active_shortlist_size_rule"),
            "MMI_POLICY_SHORTLIST_RULES_INVALID",
        ),
        (
            lambda value: value["active_shortlist_size_rule"].pop(
                "benchmark_carrier"
            ),
            "MMI_POLICY_SHORTLIST_RULES_INVALID",
        ),
        (
            lambda value: value.update(
                {"relative_rotation_enabled": "true"}
            ),
            "MMI_POLICY_ROTATION_ENABLED_INVALID",
        ),
        (
            lambda value: value.pop("relative_rotation_guardrails"),
            "MMI_POLICY_ROTATION_GUARDRAILS_INVALID",
        ),
        (
            lambda value: value["relative_rotation_guardrails"].pop(
                "min_score_gap_to_rotate"
            ),
            "MMI_POLICY_ROTATION_GUARDRAILS_INVALID",
        ),
        (
            lambda value: value.update(
                {"target_new_buy_budget_this_run": "12000"}
            ),
            "MMI_POLICY_PER_RUN_NEW_BUY_BUDGET_INVALID",
        ),
        (
            lambda value: value.update(
                {"target_new_buy_budget_this_run": -1}
            ),
            "MMI_POLICY_PER_RUN_NEW_BUY_BUDGET_INVALID",
        ),
        (
            lambda value: value.update({"target_weights": []}),
            "MMI_POLICY_TARGET_WEIGHTS_PROHIBITED",
        ),
    ],
)
def test_required_method_fields_and_malformed_optional_values_block(
    tmp_path: Path,
    mutation,
    code: str,
) -> None:
    settings = _valid_settings()
    mutation(settings)
    result = _build(tmp_path, settings)
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (code,)
    assert result.projection is None


def test_optional_policy_absence_is_explicitly_gapped(
    tmp_path: Path,
) -> None:
    settings = _valid_settings()
    settings.pop("target_new_buy_budget_this_run")
    settings.pop("max_new_tickers_per_week")
    settings.pop("extended_etf_constraints")
    result = _build(tmp_path, settings)
    assert result.valid
    projection = result.projection
    assert projection["per_run_new_buy_budget"] == {
        "status": "VALUE_UNAVAILABLE",
        "currency": None,
        "amount_decimal": None,
        "authority_effect": "NONE",
    }
    assert projection["maximum_new_ticker_rules"]["status"] == "UNAVAILABLE"
    assert projection["extended_sleeve_constraints"]["status"] == "UNAVAILABLE"
    gap_codes = [gap["code"] for gap in projection["known_policy_gaps"]]
    assert "POLICY_PER_RUN_NEW_BUY_BUDGET_UNAVAILABLE" in gap_codes
    assert "POLICY_MAX_NEW_TICKER_RULE_UNAVAILABLE" in gap_codes
    assert "POLICY_EXTENDED_ACTIVATION_CONSTRAINTS_UNAVAILABLE" in gap_codes


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("as_of", "2026-07-26", "MMI_POLICY_AS_OF_FUTURE"),
        ("as_of", "2026-02-30", "MMI_POLICY_AS_OF_INVALID"),
        (
            "run_timestamp_et",
            "2026-07-25 09:00 ET",
            "MMI_POLICY_SOURCE_TIMESTAMP_FUTURE",
        ),
        (
            "run_timestamp_et",
            "2026-11-01 01:30 ET",
            "MMI_POLICY_SOURCE_TIMESTAMP_AMBIGUOUS",
        ),
    ],
)
def test_future_or_invalid_source_time_is_blocking(
    tmp_path: Path,
    field: str,
    value: str,
    code: str,
) -> None:
    settings = _valid_settings()
    settings[field] = value
    result = _build(tmp_path, settings)
    assert result.reason_codes == (code,)
    assert result.projection is None


def test_no_source_age_threshold_is_invented(tmp_path: Path) -> None:
    settings = _valid_settings()
    settings["as_of"] = "2001-01-01"
    settings.pop("run_timestamp_et")
    result = _build(tmp_path, settings)
    assert result.valid
    assert result.projection["policy_as_of_date"] == "2001-01-01"
    assert result.projection["source_run_timestamp_utc"] is None
    assert not any("STALE" in code for code in result.reason_codes)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (
            b"a: 1\na: 2\n",
            "MMI_POLICY_YAML_DUPLICATE_KEY",
        ),
        (
            b"a: &anchor 1\nb: *anchor\n",
            "MMI_POLICY_YAML_ANCHOR_PROHIBITED",
        ),
        (
            b"base: &base\n  a: 1\nmerged:\n  <<: *base\n",
            "MMI_POLICY_YAML_ANCHOR_PROHIBITED",
        ),
        (
            b"value: !custom tagged\n",
            "MMI_POLICY_YAML_TAG_PROHIBITED",
        ),
        (
            b"value: !!null null\n",
            "MMI_POLICY_YAML_TAG_PROHIBITED",
        ),
        (
            b"value: !!bool YES\n",
            "MMI_POLICY_YAML_BOOLEAN_INVALID",
        ),
        (
            b"value: !!int 0x10\n",
            "MMI_POLICY_YAML_NUMERIC_INVALID",
        ),
        (
            b"a: 1\n---\nb: 2\n",
            "MMI_POLICY_YAML_MULTIPLE_DOCUMENTS",
        ),
        (
            b"value: .nan\n",
            "MMI_POLICY_YAML_NONFINITE_NUMBER",
        ),
        (
            b"value: [unterminated\n",
            "MMI_POLICY_YAML_PARSE_FAILED",
        ),
        (
            b"\xef\xbb\xbfas_of: '2026-07-24'\n",
            "MMI_POLICY_SOURCE_BOM_PROHIBITED",
        ),
        (
            b"value: \xff\n",
            "MMI_POLICY_SOURCE_UTF8_INVALID",
        ),
    ],
)
def test_strict_yaml_rejects_ambiguous_or_unsupported_constructs(
    tmp_path: Path,
    raw: bytes,
    code: str,
) -> None:
    result = _build(tmp_path, raw=raw)
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (code,)
    assert result.projection is None


@pytest.mark.parametrize(
    ("plain_scalar", "expected_type", "expected_value"),
    [
        ("yes", str, "yes"),
        ("Yes", str, "Yes"),
        ("YES", str, "YES"),
        ("no", str, "no"),
        ("ON", str, "ON"),
        ("OFF", str, "OFF"),
        ("~", str, "~"),
        ("null", str, "null"),
        ("Null", str, "Null"),
        ("0x10", str, "0x10"),
        ("0o10", str, "0o10"),
        ("012", str, "012"),
        ("1:20", str, "1:20"),
        ("1e3", str, "1e3"),
        ("1.0e3", str, "1.0e3"),
        (".true", str, ".true"),
        ("true", bool, True),
        ("false", bool, False),
        ("0", int, 0),
        ("-12", int, -12),
        ("1.2300", Decimal, Decimal("1.2300")),
    ],
)
def test_strict_yaml_plain_scalar_grammar_is_mmi_owned(
    plain_scalar: str,
    expected_type: type[object],
    expected_value: object,
) -> None:
    parsed = _parse_strict_strategy_settings(
        f"value: {plain_scalar}\n".encode("ascii")
    )
    assert type(parsed["value"]) is expected_type
    assert parsed["value"] == expected_value


def test_strict_yaml_dates_remain_strings_for_semantic_validation() -> None:
    parsed = _parse_strict_strategy_settings(
        b"as_of: 2026-07-24\n"
    )
    assert type(parsed["as_of"]) is str
    assert parsed["as_of"] == "2026-07-24"


def test_strict_yaml_depth_and_node_bounds(tmp_path: Path) -> None:
    too_deep = ("value: " + "[" * 17 + "0" + "]" * 17 + "\n").encode()
    deep_result = _build(tmp_path / "deep", raw=too_deep)
    assert deep_result.reason_codes == ("MMI_POLICY_YAML_DEPTH_EXCEEDED",)

    too_many_nodes = (
        "values:\n" + "".join("  - x\n" for _ in range(4_097))
    ).encode()
    node_result = _build(tmp_path / "nodes", raw=too_many_nodes)
    assert node_result.reason_codes == (
        "MMI_POLICY_YAML_NODE_COUNT_EXCEEDED",
    )


def test_builder_rejects_forged_source_role_schema_identity_and_binding(
    tmp_path: Path,
) -> None:
    raw = _raw_settings(_valid_settings())
    source = _install_and_capture(tmp_path, raw)
    context = _begin_mmi_projection_run_with_clock(_FixedClock())

    with pytest.raises(TypeError):
        MmiCapturedSource(
            role=MmiSourceRole.STRATEGY_SETTINGS,
            raw_bytes=raw,
            source_record=source.source_record,
        )
    with pytest.raises(TypeError):
        replace(source, raw_bytes=raw + b"changed: true\n")

    legitimate_copy = copy.copy(source)
    accepted = build_mmi_policy_projection(
        legitimate_copy,
        run_context=context,
    )
    assert accepted.valid

    altered_bytes = _forged_source(
        source,
        raw_bytes=raw + b"changed: true\n",
        source_record=dict(source.source_record),
        seal=source._provenance_seal,
    )
    altered_bytes_result = build_mmi_policy_projection(
        altered_bytes,
        run_context=context,
    )
    assert altered_bytes_result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert altered_bytes_result.reason_codes == (
        "MMI_POLICY_CAPTURE_PROVENANCE_INVALID",
    )

    altered_record = dict(source.source_record)
    altered_record["observed_size_bytes"] = len(raw) + 1
    altered_record["source_record_identity_sha256"] = (
        _independent_record_identity(
            altered_record,
            identity_field="source_record_identity_sha256",
            domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        )
    )
    altered_record_source = _forged_source(
        source,
        raw_bytes=raw,
        source_record=altered_record,
        seal=source._provenance_seal,
    )
    altered_record_result = build_mmi_policy_projection(
        altered_record_source,
        run_context=context,
    )
    assert altered_record_result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert altered_record_result.reason_codes == (
        "MMI_POLICY_CAPTURE_PROVENANCE_INVALID",
    )

    arbitrary_raw = b"arbitrary: attacker-produced\n"
    arbitrary_hash = hashlib.sha256(arbitrary_raw).hexdigest()
    arbitrary_record = dict(source.source_record)
    arbitrary_record.update(
        {
            "observed_size_bytes": len(arbitrary_raw),
            "expected_sha256": arbitrary_hash,
            "observed_sha256": arbitrary_hash,
        }
    )
    arbitrary_record["source_record_identity_sha256"] = (
        _independent_record_identity(
            arbitrary_record,
            identity_field="source_record_identity_sha256",
            domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        )
    )
    validate_artifact_schema(
        arbitrary_record,
        schema_name="mmi_source_record_v1.schema.json",
    )
    fake = _forged_source(
        source,
        raw_bytes=arbitrary_raw,
        source_record=arbitrary_record,
        seal=b"\x00" * 32,
    )
    fake_result = build_mmi_policy_projection(
        fake,
        run_context=context,
    )
    assert fake_result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert fake_result.reason_codes == (
        "MMI_POLICY_CAPTURE_PROVENANCE_INVALID",
    )


def test_builder_accepts_only_run_context_not_raw_datetime(
    tmp_path: Path,
) -> None:
    signature = inspect.signature(build_mmi_policy_projection)
    assert tuple(signature.parameters) == ("source", "run_context")
    assert signature.parameters["run_context"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )
    source = _install_and_capture(tmp_path, _raw_settings(_valid_settings()))
    result = build_mmi_policy_projection(
        source,
        run_context=EVALUATION_TIME,  # type: ignore[arg-type]
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.reason_codes == (
        "MMI_PROJECTION_RUN_CONTEXT_PROVENANCE_INVALID",
    )
    assert result.projection is None


def test_validator_requires_provenance_source_and_run_context(
    tmp_path: Path,
) -> None:
    projection, source, run_context = _projection_with_contract(tmp_path)
    signature = inspect.signature(validate_mmi_policy_projection)
    assert tuple(signature.parameters) == (
        "value",
        "source",
        "run_context",
    )
    assert signature.parameters["source"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )
    assert signature.parameters["run_context"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )

    with pytest.raises(TypeError):
        validate_mmi_policy_projection(projection)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        validate_mmi_policy_projection(  # type: ignore[call-arg]
            projection,
            source=source,
        )
    with pytest.raises(TypeError):
        validate_mmi_policy_projection(  # type: ignore[call-arg]
            projection,
            run_context=run_context,
        )

    missing_source = validate_mmi_policy_projection(
        projection,
        source=None,  # type: ignore[arg-type]
        run_context=run_context,
    )
    assert missing_source.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert missing_source.reason_codes == (
        "MMI_POLICY_CAPTURE_PROVENANCE_INVALID",
    )
    missing_context = validate_mmi_policy_projection(
        projection,
        source=source,
        run_context=None,  # type: ignore[arg-type]
    )
    assert missing_context.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert missing_context.reason_codes == (
        "MMI_PROJECTION_RUN_CONTEXT_PROVENANCE_INVALID",
    )
    assert missing_source.authority_effect == "NONE"
    assert missing_context.authority_effect == "NONE"


def test_validator_rejects_forged_source_and_wrong_source_role(
    tmp_path: Path,
) -> None:
    projection, source, run_context = _projection_with_contract(tmp_path)
    forged = _forged_source(
        source,
        raw_bytes=source.raw_bytes,
        source_record=dict(source.source_record),
        seal=b"\x00" * 32,
    )
    forged_result = validate_mmi_policy_projection(
        projection,
        source=forged,
        run_context=run_context,
    )
    assert forged_result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert forged_result.reason_codes == (
        "MMI_POLICY_CAPTURE_PROVENANCE_INVALID",
    )

    wrong_role = _create_mmi_captured_source(
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        raw_bytes=source.raw_bytes,
        source_record=source.source_record,
    )
    wrong_role_result = validate_mmi_policy_projection(
        projection,
        source=wrong_role,
        run_context=run_context,
    )
    assert wrong_role_result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert wrong_role_result.reason_codes == (
        "MMI_POLICY_CAPTURE_ROLE_INVALID",
    )
    assert forged_result.authority_effect == "NONE"
    assert wrong_role_result.authority_effect == "NONE"


@pytest.mark.parametrize(
    ("evaluation_time", "evaluation_timestamp", "seal"),
    [
        (
            datetime(2026, 7, 25, 12, 0, 0, 1, tzinfo=timezone.utc),
            "2026-07-25T12:00:00.000000Z",
            None,
        ),
        (
            EVALUATION_TIME,
            "2026-07-25T12:00:00.000001Z",
            None,
        ),
        (
            datetime(2026, 7, 25, 12, 0, 0, 1, tzinfo=timezone.utc),
            "2026-07-25T12:00:00.000001Z",
            None,
        ),
        (
            EVALUATION_TIME,
            "2026-07-25T12:00:00.000000Z",
            b"\x00" * 32,
        ),
    ],
)
def test_builder_rejects_forged_or_content_changed_run_context(
    tmp_path: Path,
    evaluation_time: datetime,
    evaluation_timestamp: str,
    seal: bytes | None,
) -> None:
    source = _install_and_capture(
        tmp_path,
        _raw_settings(_valid_settings()),
    )
    legitimate = _begin_mmi_projection_run_with_clock(_FixedClock())
    built = build_mmi_policy_projection(
        source,
        run_context=legitimate,
    )
    assert built.valid
    assert built.projection is not None
    forged = object.__new__(MmiProjectionRunContext)
    object.__setattr__(forged, "evaluation_time_utc", evaluation_time)
    object.__setattr__(
        forged,
        "evaluation_timestamp_utc",
        evaluation_timestamp,
    )
    object.__setattr__(forged, "authority_effect", "NONE")
    object.__setattr__(
        forged,
        "_provenance_seal",
        legitimate._provenance_seal if seal is None else seal,
    )
    result = build_mmi_policy_projection(
        source,
        run_context=forged,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.reason_codes == (
        "MMI_PROJECTION_RUN_CONTEXT_PROVENANCE_INVALID",
    )
    assert result.projection is None
    validation = validate_mmi_policy_projection(
        dict(built.projection),
        source=source,
        run_context=forged,
    )
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert validation.reason_codes == (
        "MMI_PROJECTION_RUN_CONTEXT_PROVENANCE_INVALID",
    )
    assert validation.authority_effect == "NONE"


def test_independent_valid_contexts_with_same_clock_are_reproducible(
    tmp_path: Path,
) -> None:
    source = _install_and_capture(
        tmp_path,
        _raw_settings(_valid_settings()),
    )
    first_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    second_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    assert first_context is not second_context
    first = build_mmi_policy_projection(
        source,
        run_context=first_context,
    )
    second = build_mmi_policy_projection(
        source,
        run_context=second_context,
    )
    assert first.valid and second.valid
    assert dict(first.projection) == dict(second.projection)
    assert first.projection["policy_projection_identity_sha256"] == (
        second.projection["policy_projection_identity_sha256"]
    )


def test_validator_rejects_projection_source_record_mismatch(
    tmp_path: Path,
) -> None:
    projection, first_source, run_context = _projection_with_contract(
        tmp_path / "first"
    )
    second_settings = _valid_settings()
    second_settings["source_only_note"] = "changes source identity only"
    second_projection, second_source, _second_context = (
        _projection_with_contract(
            tmp_path / "second",
            second_settings,
        )
    )
    assert projection["source_record_identity_sha256"] != (
        second_projection["source_record_identity_sha256"]
    )

    validation = validate_mmi_policy_projection(
        projection,
        source=second_source,
        run_context=run_context,
    )
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert validation.reason_codes == (
        "MMI_POLICY_SOURCE_FIDELITY_MISMATCH",
    )
    assert validation.authority_effect == "NONE"
    assert first_source is not second_source


def test_source_bound_validator_rejects_resealed_valid_core_benchmark_change(
    tmp_path: Path,
) -> None:
    projection, source, run_context = _projection_with_contract(tmp_path)
    universe = projection["universe_projection"]
    assert universe["core_universe"] == ["QQQ", "VOO", "VTI", "VT"]
    assert universe["benchmark_reference_instruments"] == ["QQQ"]
    universe["benchmark_reference_instruments"] = ["VOO"]
    _reseal_projection(projection)

    validation = validate_mmi_policy_projection(
        projection,
        source=source,
        run_context=run_context,
    )
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert validation.reason_codes == (
        "MMI_POLICY_SOURCE_FIDELITY_MISMATCH",
    )
    assert validation.authority_effect == "NONE"


def test_validator_accepts_matching_legitimate_nondefault_benchmark_source(
    tmp_path: Path,
) -> None:
    original, original_source, run_context = _projection_with_contract(
        tmp_path / "original"
    )
    alternate_settings = _valid_settings()
    alternate_settings["benchmark"] = "VOO"
    alternate, alternate_source, alternate_context = (
        _projection_with_contract(
            tmp_path / "alternate",
            alternate_settings,
        )
    )
    assert alternate["universe_projection"][
        "benchmark_reference_instruments"
    ] == ["VOO"]
    accepted = validate_mmi_policy_projection(
        alternate,
        source=alternate_source,
        run_context=alternate_context,
    )
    assert accepted.valid
    assert accepted.authority_effect == "NONE"

    mismatched = validate_mmi_policy_projection(
        alternate,
        source=original_source,
        run_context=run_context,
    )
    assert mismatched.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert mismatched.reason_codes == (
        "MMI_POLICY_SOURCE_FIDELITY_MISMATCH",
    )
    reverse_mismatch = validate_mmi_policy_projection(
        original,
        source=alternate_source,
        run_context=alternate_context,
    )
    assert reverse_mismatch.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert reverse_mismatch.reason_codes == (
        "MMI_POLICY_SOURCE_FIDELITY_MISMATCH",
    )
    assert original["universe_projection"][
        "benchmark_reference_instruments"
    ] == ["QQQ"]


def test_schema_closure_and_semantic_validation_fail_closed(
    tmp_path: Path,
) -> None:
    projection, source, run_context = _projection_with_contract(tmp_path)
    validate_artifact_schema(
        projection,
        schema_name="mmi_policy_projection_v1.schema.json",
    )
    validate_artifact_schema(
        projection["universe_projection"],
        schema_name="mmi_universe_projection_v1.schema.json",
    )
    assert validate_mmi_policy_projection(
        projection,
        source=source,
        run_context=run_context,
    ).valid

    extra = deepcopy(projection)
    extra["unexpected"] = True
    schema_invalid = validate_mmi_policy_projection(
        extra,
        source=source,
        run_context=run_context,
    )
    assert schema_invalid.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert schema_invalid.reason_codes == (
        "MMI_POLICY_PROJECTION_SCHEMA_INVALID",
    )

    forged_identity = deepcopy(projection)
    forged_identity["policy_projection_identity_sha256"] = "0" * 64
    identity_invalid = validate_mmi_policy_projection(
        forged_identity,
        source=source,
        run_context=run_context,
    )
    assert identity_invalid.reason_codes == (
        "MMI_POLICY_PROJECTION_IDENTITY_INVALID",
    )

    forged_scope = deepcopy(projection)
    forged_scope["universe_projection"]["analysis_scope_instruments"].reverse()
    universe = forged_scope["universe_projection"]
    universe["universe_projection_identity_sha256"] = (
        _independent_record_identity(
            universe,
            identity_field="universe_projection_identity_sha256",
            domain=MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
        )
    )
    forged_scope["universe_projection_identity_sha256"] = universe[
        "universe_projection_identity_sha256"
    ]
    forged_scope["policy_projection_identity_sha256"] = (
        _independent_record_identity(
            forged_scope,
            identity_field="policy_projection_identity_sha256",
            domain=MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
        )
    )
    semantic_invalid = validate_mmi_policy_projection(
        forged_scope,
        source=source,
        run_context=run_context,
    )
    assert semantic_invalid.reason_codes == (
        "MMI_UNIVERSE_PROJECTION_SEMANTIC_INVALID",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda gaps: gaps.clear(),
        lambda gaps: gaps[0].update(
            {"affected_tickers": ["QUAL"]}
        ),
        lambda gaps: gaps.append(
            {
                "code": "EXTENDED_ETF_THEME_MAPPING_INCOMPLETE",
                "scope": "UNIVERSE",
                "affected_question_class": (
                    "UNIVERSE_THEME_INTERPRETATION"
                ),
                "affected_tickers": ["QUAL"],
                "source_record_identity_sha256": gaps[0][
                    "source_record_identity_sha256"
                ],
            }
        ),
    ],
)
def test_universe_gaps_are_rederived_after_public_identities_are_resealed(
    tmp_path: Path,
    mutation,
) -> None:
    settings = _valid_settings()
    settings.pop("user_approved_extended_etf_theme_map")
    projection, source, run_context = _projection_with_contract(
        tmp_path,
        settings,
    )
    forged = deepcopy(projection)
    gaps = forged["universe_projection"]["known_universe_gaps"]
    mutation(gaps)
    _reseal_projection(forged)

    validation = validate_mmi_policy_projection(
        forged,
        source=source,
        run_context=run_context,
    )
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert validation.reason_codes == (
        "MMI_UNIVERSE_GAP_CONTRACT_MISMATCH",
    )


def test_complete_theme_map_rejects_an_untriggered_universe_gap(
    tmp_path: Path,
) -> None:
    projection, source, run_context = _projection_with_contract(tmp_path)
    forged = deepcopy(projection)
    universe = forged["universe_projection"]
    universe["known_universe_gaps"].append(
        {
            "code": "EXTENDED_THEME_MAP_UNAVAILABLE",
            "scope": "UNIVERSE",
            "affected_question_class": (
                "UNIVERSE_THEME_INTERPRETATION"
            ),
            "affected_tickers": ["QUAL", "CIBR"],
            "source_record_identity_sha256": forged[
                "source_record_identity_sha256"
            ],
        }
    )
    _reseal_projection(forged)
    validation = validate_mmi_policy_projection(
        forged,
        source=source,
        run_context=run_context,
    )
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert validation.reason_codes == (
        "MMI_UNIVERSE_GAP_CONTRACT_MISMATCH",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["known_policy_gaps"].__setitem__(
            slice(None),
            [
                gap
                for gap in value["known_policy_gaps"]
                if gap["code"]
                != "POLICY_CASH_MODEL_UNAVAILABLE"
            ],
        ),
        lambda value: value["known_policy_gaps"].append(
            {
                "code": "POLICY_MAX_NEW_TICKER_RULE_UNAVAILABLE",
                "scope": "POLICY",
                "affected_question_class": "MAXIMUM_NEW_TICKERS",
                "affected_tickers": [],
                "source_record_identity_sha256": value[
                    "source_record_identity_sha256"
                ],
            }
        ),
        lambda value: next(
            gap
            for gap in value["known_policy_gaps"]
            if gap["code"] == "POLICY_SELL_ELIGIBILITY_INCOMPLETE"
        ).update({"affected_tickers": ["QQQ"]}),
        lambda value: value["known_policy_gaps"].clear(),
    ],
)
def test_policy_gaps_are_rederived_after_public_identity_is_resealed(
    tmp_path: Path,
    mutation,
) -> None:
    projection, source, run_context = _projection_with_contract(tmp_path)
    forged = deepcopy(projection)
    mutation(forged)
    forged["known_policy_gaps"].sort(
        key=lambda gap: (
            gap["code"],
            gap["scope"],
            tuple(gap["affected_tickers"]),
        )
    )
    _reseal_projection(forged)

    validation = validate_mmi_policy_projection(
        forged,
        source=source,
        run_context=run_context,
    )
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert validation.reason_codes == (
        "MMI_POLICY_GAP_CONTRACT_MISMATCH",
    )


def test_policy_status_and_gap_must_change_together(
    tmp_path: Path,
) -> None:
    projection, source, run_context = _projection_with_contract(tmp_path)
    forged = deepcopy(projection)
    forged["per_run_new_buy_budget"] = {
        "status": "VALUE_UNAVAILABLE",
        "currency": None,
        "amount_decimal": None,
        "authority_effect": "NONE",
    }
    _reseal_projection(forged)
    validation = validate_mmi_policy_projection(
        forged,
        source=source,
        run_context=run_context,
    )
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert validation.reason_codes == (
        "MMI_POLICY_GAP_CONTRACT_MISMATCH",
    )


def test_duplicate_policy_gap_and_false_complete_result_are_rejected(
    tmp_path: Path,
) -> None:
    projection, source, run_context = _projection_with_contract(tmp_path)
    duplicate = deepcopy(projection)
    duplicate["known_policy_gaps"].append(
        deepcopy(duplicate["known_policy_gaps"][0])
    )
    _reseal_projection(duplicate)
    duplicate_result = validate_mmi_policy_projection(
        duplicate,
        source=source,
        run_context=run_context,
    )
    assert not duplicate_result.valid
    assert duplicate_result.reason_codes == (
        "MMI_POLICY_PROJECTION_SCHEMA_INVALID",
    )

    no_gaps = deepcopy(projection)
    no_gaps["known_policy_gaps"] = []
    _reseal_projection(no_gaps)
    no_gaps_result = validate_mmi_policy_projection(
        no_gaps,
        source=source,
        run_context=run_context,
    )
    assert no_gaps_result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert no_gaps_result.status is not (
        MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    )
    assert no_gaps_result.reason_codes == (
        "MMI_POLICY_GAP_CONTRACT_MISMATCH",
    )


def test_projection_contains_no_runtime_authority_fields(tmp_path: Path) -> None:
    projection = _projection(tmp_path)
    prohibited = {
        "availability",
        "allowed_actions",
        "permission",
        "ready",
        "gate",
        "order_ready",
    }

    def visit(value: object) -> None:
        if isinstance(value, dict):
            assert not prohibited & set(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(projection)
    assert projection["authority_effect"] == "NONE"
    assert projection["universe_projection"]["authority_effect"] == "NONE"
    assert projection["hard_open_orders_budget_cap"]["authority_effect"] == (
        "NONE"
    )
    assert projection["per_run_new_buy_budget"]["authority_effect"] == "NONE"


@pytest.mark.parametrize(
    ("benchmarks", "validation_reason_code"),
    [
        (["QQQ", "VOO"], "MMI_POLICY_PROJECTION_SCHEMA_INVALID"),
        ([], "MMI_POLICY_PROJECTION_SCHEMA_INVALID"),
        (["QQQ", "QQQ"], "MMI_POLICY_PROJECTION_SCHEMA_INVALID"),
        (["SPY"], "MMI_UNIVERSE_BENCHMARK_CONTRACT_MISMATCH"),
    ],
)
def test_resealed_benchmark_contract_mutations_are_rejected(
    tmp_path: Path,
    benchmarks: list[str],
    validation_reason_code: str,
) -> None:
    forged, source, run_context = _projection_with_contract(tmp_path)
    forged["universe_projection"][
        "benchmark_reference_instruments"
    ] = benchmarks
    _assert_resealed_universe_semantic_rejection(
        forged,
        reason_code="MMI_UNIVERSE_BENCHMARK_CONTRACT_MISMATCH",
        validation_reason_code=validation_reason_code,
        source=source,
        run_context=run_context,
    )


@pytest.mark.parametrize("benchmark_carrier", [0, -1, 257, True, None])
def test_resealed_invalid_benchmark_carrier_is_rejected(
    tmp_path: Path,
    benchmark_carrier: object,
) -> None:
    forged, source, run_context = _projection_with_contract(tmp_path)
    forged["shortlist_size_rules"][
        "benchmark_carrier"
    ] = benchmark_carrier
    _assert_resealed_policy_semantic_rejection(
        forged,
        reason_code="MMI_POLICY_SHORTLIST_CONTRACT_MISMATCH",
        source=source,
        run_context=run_context,
    )


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        (("enabled",), "true"),
        (("guardrails", "minimum_score_gap_to_rotate"), True),
        (("guardrails", "minimum_score_gap_to_rotate"), -1),
        (("guardrails", "minimum_score_gap_to_rotate"), 257),
        (
            ("guardrails", "require_same_role_for_rotation"),
            1,
        ),
    ],
)
def test_resealed_invalid_rotation_policy_is_rejected(
    tmp_path: Path,
    field_path: tuple[str, ...],
    value: object,
) -> None:
    forged, source, run_context = _projection_with_contract(tmp_path)
    target = forged["rotation_policy"]
    for component in field_path[:-1]:
        target = target[component]
    target[field_path[-1]] = value
    _assert_resealed_policy_semantic_rejection(
        forged,
        reason_code="MMI_POLICY_ROTATION_CONTRACT_MISMATCH",
        source=source,
        run_context=run_context,
    )


@pytest.mark.parametrize(
    ("status", "base_count", "extended_count"),
    [
        ("SOURCE_VALIDATED", None, None),
        ("SOURCE_VALIDATED", 0, None),
        ("UNAVAILABLE", 0, 2),
        ("SOURCE_VALIDATED", True, 2),
        ("SOURCE_VALIDATED", -1, 2),
        ("SOURCE_VALIDATED", 0, 257),
    ],
)
def test_resealed_maximum_new_ticker_correlations_are_rejected(
    tmp_path: Path,
    status: str,
    base_count: object,
    extended_count: object,
) -> None:
    forged, source, run_context = _projection_with_contract(tmp_path)
    forged["maximum_new_ticker_rules"] = {
        "status": status,
        "base_universe_new_tickers_per_week": base_count,
        "extended_etf_sleeve_new_tickers_per_week": extended_count,
    }
    _assert_resealed_policy_semantic_rejection(
        forged,
        reason_code="MMI_POLICY_MAX_NEW_TICKER_CONTRACT_MISMATCH",
        source=source,
        run_context=run_context,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {
            "status": "SOURCE_VALIDATED",
            "sleeve_budget_cap_fraction": None,
            "single_etf_budget_cap_fraction": None,
            "activation_minimum_effective_budget_fraction": None,
            "maximum_same_theme_member_count": None,
            "maximum_same_theme_budget_fraction": None,
            "require_distinct_theme_buckets": None,
        },
        {
            "status": "SOURCE_VALIDATED",
            "sleeve_budget_cap_fraction": "0.35",
            "single_etf_budget_cap_fraction": None,
            "activation_minimum_effective_budget_fraction": "0.04",
            "maximum_same_theme_member_count": 1,
            "maximum_same_theme_budget_fraction": "0.25",
            "require_distinct_theme_buckets": True,
        },
        {
            "status": "UNAVAILABLE",
            "sleeve_budget_cap_fraction": "0.35",
            "single_etf_budget_cap_fraction": None,
            "activation_minimum_effective_budget_fraction": None,
            "maximum_same_theme_member_count": None,
            "maximum_same_theme_budget_fraction": None,
            "require_distinct_theme_buckets": None,
        },
        {
            "status": "SOURCE_VALIDATED",
            "sleeve_budget_cap_fraction": "1.1",
            "single_etf_budget_cap_fraction": "0.2",
            "activation_minimum_effective_budget_fraction": "0.04",
            "maximum_same_theme_member_count": 1,
            "maximum_same_theme_budget_fraction": "0.25",
            "require_distinct_theme_buckets": True,
        },
    ],
)
def test_resealed_extended_constraint_correlations_are_rejected(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    forged, source, run_context = _projection_with_contract(tmp_path)
    forged["extended_sleeve_constraints"] = mutation
    _assert_resealed_policy_semantic_rejection(
        forged,
        reason_code=(
            "MMI_POLICY_EXTENDED_SLEEVE_CONSTRAINTS_CONTRACT_MISMATCH"
        ),
        source=source,
        run_context=run_context,
    )


@pytest.mark.parametrize(
    "amount",
    [
        "38211.2900",
        "+1",
        "01",
        "-0",
        "1E3",
        "1e3",
        "1" * 49,
        f"0.{'0' * 24}1",
        f"{'1' * 40}.{'1' * 17}",
        "-1",
    ],
)
def test_resealed_hard_cap_requires_canonical_bounded_positive_decimal(
    tmp_path: Path,
    amount: str,
) -> None:
    forged, source, run_context = _projection_with_contract(tmp_path)
    forged["hard_open_orders_budget_cap"]["amount_decimal"] = amount
    _assert_resealed_policy_semantic_rejection(
        forged,
        reason_code=(
            "MMI_POLICY_HARD_OPEN_ORDERS_BUDGET_CAP_CONTRACT_MISMATCH"
        ),
        source=source,
        run_context=run_context,
    )


@pytest.mark.parametrize(
    "amount",
    [
        "12000.00",
        "+1",
        "01",
        "-0",
        "1E3",
        f"0.{'1' * 25}",
        "-1",
    ],
)
def test_resealed_per_run_budget_requires_canonical_bounded_decimal(
    tmp_path: Path,
    amount: str,
) -> None:
    forged, source, run_context = _projection_with_contract(tmp_path)
    forged["per_run_new_buy_budget"]["amount_decimal"] = amount
    _assert_resealed_policy_semantic_rejection(
        forged,
        reason_code="MMI_POLICY_PER_RUN_BUDGET_CONTRACT_MISMATCH",
        source=source,
        run_context=run_context,
    )


@pytest.mark.parametrize(
    "per_run_budget",
    [
        {
            "status": "VALUE_PRESENT_APPLICABILITY_UNVERIFIED",
            "currency": "USD",
            "amount_decimal": None,
            "authority_effect": "NONE",
        },
        {
            "status": "VALUE_UNAVAILABLE",
            "currency": "USD",
            "amount_decimal": "12000",
            "authority_effect": "NONE",
        },
    ],
)
def test_resealed_per_run_budget_status_value_mismatch_is_rejected(
    tmp_path: Path,
    per_run_budget: dict[str, object],
) -> None:
    forged, source, run_context = _projection_with_contract(tmp_path)
    forged["per_run_new_buy_budget"] = per_run_budget
    _assert_resealed_policy_semantic_rejection(
        forged,
        reason_code="MMI_POLICY_PER_RUN_BUDGET_CONTRACT_MISMATCH",
        source=source,
        run_context=run_context,
    )


@pytest.mark.parametrize(
    "ratio",
    [
        "0.350",
        "+1",
        "01",
        "-0",
        "1E3",
        f"0.{'1' * 25}",
        "-0.1",
        "1.0001",
    ],
)
def test_resealed_extended_ratio_requires_canonical_unit_interval(
    tmp_path: Path,
    ratio: str,
) -> None:
    forged, source, run_context = _projection_with_contract(tmp_path)
    forged["extended_sleeve_constraints"][
        "sleeve_budget_cap_fraction"
    ] = ratio
    _assert_resealed_policy_semantic_rejection(
        forged,
        reason_code=(
            "MMI_POLICY_EXTENDED_SLEEVE_CONSTRAINTS_CONTRACT_MISMATCH"
        ),
        source=source,
        run_context=run_context,
    )
