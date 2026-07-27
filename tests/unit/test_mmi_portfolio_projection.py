from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import (
    Clamped,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    ROUND_DOWN,
    Rounded,
    Underflow,
    localcontext,
)
import hashlib
import inspect
import json
from pathlib import Path
import struct
from types import MappingProxyType

import pytest
import yaml

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi import portfolio_projection
from investment_orchestrator.mmi.canonical import (
    MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
    MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN,
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
    validate_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.mmi.source_capture import (
    _capture_mmi_source_at_root,
)


EVALUATION_TIME = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
PORTFOLIO_SECTION_START = (
    "(2a) existing_buy_open_orders_summary"
    "（optional, ticker-level summary; buy-side existing open orders SSOT）"
)
PORTFOLIO_SECTION_END = (
    "(2b) sell_open_orders"
    "（optional, lot-aware open sell orders summary）"
)
OPEN_BUY_HEADER = (
    "TICKER | budget | compiled_open_order_notional(optional) | "
    "residual_cash_not_allocated(optional) | template_id | "
    "anchor_baseline_last_close | anchor_price_asof | "
    "last_refresh_date_et(optional) | highest_live_limit(optional) | "
    "lowest_live_limit(optional) | live_step_count(optional) | "
    "live_order_steps_summary(optional) | "
    "live_order_qtys_summary(optional)"
)
STATIC_GAP_CODES = (
    "PORTFOLIO_HOLDINGS_UNSTRUCTURED",
    "PORTFOLIO_OPEN_SELL_ORDERS_UNSTRUCTURED",
    "PORTFOLIO_TAX_LOTS_UNSTRUCTURED",
    "PORTFOLIO_DEPLOYABLE_CASH_UNAVAILABLE",
    "PORTFOLIO_WEIGHTS_UNAVAILABLE",
    "PORTFOLIO_NAV_CONCENTRATION_UNAVAILABLE",
    "PORTFOLIO_LOOKTHROUGH_EXPOSURE_UNAVAILABLE",
)


class _FixedClock:
    def __init__(self, observed: datetime = EVALUATION_TIME) -> None:
        self.observed = observed

    def now_utc(self) -> datetime:
        return self.observed


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
        "user_approved_extended_etf_static_list": [
            "QUAL",
            "CIBR",
        ],
        "user_approved_extended_etf_theme_map": {
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
        },
    }


def _raw_settings(settings: dict[str, object]) -> bytes:
    return yaml.safe_dump(
        settings,
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")


def _open_buy_row(
    ticker: str = "QQQ",
    *,
    budget: str = "100.00",
    stated_notional: str = "",
    residual: str = "",
    template_id: str = "T4-E",
    anchor_baseline: str = "700.00",
    anchor_date: str = "2026-07-20",
    refresh_date: str = "",
    highest_limit: str = "",
    lowest_limit: str = "",
    live_step_count: str = "",
    steps: str = "",
    quantities: str = "",
) -> str:
    return " | ".join(
        (
            ticker,
            budget,
            stated_notional,
            residual,
            template_id,
            anchor_baseline,
            anchor_date,
            refresh_date,
            highest_limit,
            lowest_limit,
            live_step_count,
            steps,
            quantities,
        )
    )


def _snapshot(
    rows: list[str] | None = None,
    *,
    header_date: str | None = "2026-07-24",
    header_marker: str | None = None,
    extra_header_markers: tuple[str, ...] = (),
    section_start: str = PORTFOLIO_SECTION_START,
    section_end: str = PORTFOLIO_SECTION_END,
    table_header: str = OPEN_BUY_HEADER,
    pre_section_text: tuple[str, ...] = (),
    section_prefix: tuple[str, ...] = (),
    post_section_text: tuple[str, ...] = (),
) -> bytes:
    if header_marker is None and header_date is not None:
        header_marker = f"# updated {header_date}"
    lines = ["【Portfolio Snapshot】"]
    if header_marker is not None:
        lines.append(header_marker)
    lines.extend(extra_header_markers)
    lines.extend(pre_section_text)
    lines.extend(
        (
            "(1) current_holdings_base",
            "SECRET_BROKER | QQQ | 9 | 123.45",
            section_start,
            "- exact code-owned explanatory line",
            table_header,
        )
    )
    lines.extend(section_prefix)
    lines.extend(rows if rows is not None else [_open_buy_row()])
    lines.extend(
        (
            "",
            section_end,
            "SECRET_ACCOUNT | QQQ | raw sell instruction",
            "(3) LTCG_ELIGIBLE_SELLABLE",
            "QQQ | 9 | 2020-01-01 | secret tax lot",
        )
    )
    lines.extend(post_section_text)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _capture_source(
    root: Path,
    *,
    role: MmiSourceRole,
    raw: bytes,
) -> MmiCapturedSource:
    relative = {
        MmiSourceRole.STRATEGY_SETTINGS: (
            "inputs/current/strategy_settings.yaml"
        ),
        MmiSourceRole.PORTFOLIO_SNAPSHOT: (
            "inputs/current/portfolio_snapshot.txt"
        ),
    }[role]
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    result = _capture_mmi_source_at_root(
        root,
        role=role,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert result.valid, result.reason_codes
    assert result.source is not None
    return result.source


def _policy_contract(
    root: Path,
    *,
    settings: dict[str, object] | None = None,
    raw: bytes | None = None,
    evaluation_time: datetime = EVALUATION_TIME,
) -> tuple[
    dict[str, object],
    MmiCapturedSource,
    MmiProjectionRunContext,
]:
    source = _capture_source(
        root,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=(
            raw
            if raw is not None
            else _raw_settings(
                settings if settings is not None else _valid_settings()
            )
        ),
    )
    run_context = _begin_mmi_projection_run_with_clock(
        _FixedClock(evaluation_time)
    )
    result = build_mmi_policy_projection(
        source,
        run_context=run_context,
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    return dict(result.projection), source, run_context


def _portfolio_source(root: Path, raw: bytes) -> MmiCapturedSource:
    return _capture_source(
        root,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        raw=raw,
    )


def _build(
    root: Path,
    *,
    portfolio_raw: bytes | None = None,
    settings: dict[str, object] | None = None,
) -> tuple[
    object,
    MmiCapturedSource | None,
    dict[str, object],
    MmiCapturedSource,
    MmiProjectionRunContext,
]:
    policy, policy_source, run_context = _policy_contract(
        root,
        settings=settings,
    )
    portfolio_source = (
        None
        if portfolio_raw is None
        else _portfolio_source(root, portfolio_raw)
    )
    result = build_mmi_portfolio_snapshot_projection(
        portfolio_source,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    return (
        result,
        portfolio_source,
        policy,
        policy_source,
        run_context,
    )


def _valid_projection(
    root: Path,
    *,
    rows: list[str] | None = None,
    settings: dict[str, object] | None = None,
    header_date: str | None = "2026-07-24",
) -> tuple[
    dict[str, object],
    MmiCapturedSource,
    dict[str, object],
    MmiCapturedSource,
    MmiProjectionRunContext,
]:
    result, portfolio_source, policy, policy_source, run_context = _build(
        root,
        portfolio_raw=_snapshot(rows, header_date=header_date),
        settings=settings,
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    assert portfolio_source is not None
    return (
        dict(result.projection),
        portfolio_source,
        policy,
        policy_source,
        run_context,
    )


def _independent_identity(
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


def _reseal_portfolio(projection: dict[str, object]) -> None:
    projection["portfolio_projection_identity_sha256"] = (
        _independent_identity(
            projection,
            identity_field="portfolio_projection_identity_sha256",
            domain=(
                MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN
            ),
        )
    )


def _validate_candidate(
    candidate: object,
    *,
    portfolio_source: MmiCapturedSource | None,
    policy: dict[str, object],
    policy_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
):
    return validate_mmi_portfolio_snapshot_projection(
        candidate,
        portfolio_source=portfolio_source,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )


def _gap_codes(projection: dict[str, object]) -> tuple[str, ...]:
    gaps = projection["known_gaps"]
    assert isinstance(gaps, list)
    return tuple(gap["code"] for gap in gaps)


def test_public_surfaces_are_keyword_source_bound_and_not_reexported() -> None:
    build_signature = inspect.signature(
        build_mmi_portfolio_snapshot_projection
    )
    validate_signature = inspect.signature(
        validate_mmi_portfolio_snapshot_projection
    )
    assert tuple(build_signature.parameters) == (
        "portfolio_source",
        "policy_projection",
        "policy_source",
        "run_context",
    )
    assert tuple(validate_signature.parameters) == (
        "value",
        "portfolio_source",
        "policy_projection",
        "policy_source",
        "run_context",
    )
    for signature in (build_signature, validate_signature):
        for name in (
            "policy_projection",
            "policy_source",
            "run_context",
        ):
            assert signature.parameters[name].kind is (
                inspect.Parameter.KEYWORD_ONLY
            )
    assert validate_signature.parameters[
        "portfolio_source"
    ].kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        validate_mmi_portfolio_snapshot_projection({})  # type: ignore[call-arg]

    import investment_orchestrator.mmi as mmi

    assert mmi.__all__ == ()
    assert not hasattr(
        mmi,
        "build_mmi_portfolio_snapshot_projection",
    )


def test_missing_source_is_exact_unknown_report_and_does_not_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, policy_source, run_context = _policy_contract(tmp_path)

    def fail_decode(_raw_bytes: bytes) -> str:
        raise AssertionError("missing-source path attempted a source read")

    monkeypatch.setattr(
        portfolio_projection,
        "_decode_portfolio_source",
        fail_decode,
    )
    result = build_mmi_portfolio_snapshot_projection(
        None,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert result.authority_effect == "NONE"
    assert result.projection is not None
    projection = dict(result.projection)
    assert projection["portfolio_source_status"] == "SOURCE_ABSENT"
    assert projection["portfolio_source_record_identity_sha256"] is None
    assert projection["portfolio_source_date"] is None
    assert projection["holdings"] == {
        "status": "UNSTRUCTURED_NOT_PROJECTED",
        "records": [],
    }
    assert projection["open_buy_orders"] == {
        "status": "SOURCE_ABSENT",
        "records": [],
        "total_reserved_budget_decimal": None,
    }
    assert projection["cash"] == {
        "status": "UNAVAILABLE_NOT_PROJECTED"
    }
    assert projection["deployable_cash"] == {
        "status": "UNAVAILABLE_NOT_PROJECTED"
    }
    assert _gap_codes(projection) == (
        "PORTFOLIO_SOURCE_MISSING",
        *STATIC_GAP_CODES,
    )
    assert result.reason_codes == _gap_codes(projection)
    validate_artifact_schema(
        projection,
        schema_name="mmi_portfolio_snapshot_projection_v1.schema.json",
    )
    validation = _validate_candidate(
        projection,
        portfolio_source=None,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert validation.reason_codes == ()
    assert validation.authority_effect == "NONE"


def test_missing_source_validator_requires_the_exact_missing_projection(
    tmp_path: Path,
) -> None:
    result, _, policy, policy_source, run_context = _build(tmp_path)
    assert result.projection is not None
    candidate = deepcopy(dict(result.projection))
    candidate["portfolio_source_status"] = (
        "SOURCE_PRESENT_CONTENT_BOUND"
    )
    candidate["portfolio_source_record_identity_sha256"] = "0" * 64
    _reseal_portfolio(candidate)
    validation = _validate_candidate(
        candidate,
        portfolio_source=None,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert not validation.valid
    assert validation.authority_effect == "NONE"


def test_policy_projection_mapping_surface_is_snapshotted_then_source_bound(
    tmp_path: Path,
) -> None:
    policy, policy_source, run_context = _policy_contract(tmp_path)
    read_only_policy = MappingProxyType(policy)
    result = build_mmi_portfolio_snapshot_projection(
        None,
        policy_projection=read_only_policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert result.valid
    assert result.projection is not None
    validation = validate_mmi_portfolio_snapshot_projection(
        dict(result.projection),
        portfolio_source=None,
        policy_projection=read_only_policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert validation.valid


@pytest.mark.parametrize(
    ("raw", "expected_status", "expected_code", "expected_date"),
    [
        (
            _snapshot(header_date="2026-07-24"),
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
            None,
            "2026-07-24",
        ),
        (
            _snapshot(header_date=None),
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
            "PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE",
            None,
        ),
        (
            _snapshot(
                header_date=None,
                header_marker=" # UPDATED 2026/07/24",
            ),
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_PORTFOLIO_SOURCE_TIMESTAMP_INVALID",
            None,
        ),
        (
            _snapshot(
                header_date="2026-07-24",
                extra_header_markers=("# updated 2026-07-23",),
            ),
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_PORTFOLIO_SOURCE_TIMESTAMP_AMBIGUOUS",
            None,
        ),
        (
            _snapshot(header_date="2026-07-26"),
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_PORTFOLIO_SOURCE_TIMESTAMP_FUTURE",
            None,
        ),
        (
            _snapshot(
                header_date=None,
                pre_section_text=(
                    "holding acquired 2026-07-24",
                    "operator note 2026-07-23",
                ),
                post_section_text=("comment 2026-07-22",),
            ),
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
            "PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE",
            None,
        ),
        (
            _snapshot(header_date="1900-01-01"),
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
            None,
            "1900-01-01",
        ),
    ],
)
def test_source_timestamp_contract_has_no_inferred_freshness(
    tmp_path: Path,
    raw: bytes,
    expected_status: MmiProjectionResultCategory,
    expected_code: str | None,
    expected_date: str | None,
) -> None:
    result, _, _, _, _ = _build(
        tmp_path,
        portfolio_raw=raw,
    )
    assert result.status is expected_status
    assert result.authority_effect == "NONE"
    if expected_status is MmiProjectionResultCategory.PROJECTION_BLOCKED:
        assert result.projection is None
        assert result.reason_codes == (expected_code,)
        return
    assert result.projection is not None
    projection = dict(result.projection)
    assert projection["portfolio_source_date"] == expected_date
    if expected_code is not None:
        assert expected_code in result.reason_codes
    assert not any(
        token in json.dumps(projection, sort_keys=True)
        for token in ("FRESH", "STALE", "8_DAY", "16_DAY")
    )


def test_dates_outside_the_source_header_never_override_unique_header(
    tmp_path: Path,
) -> None:
    raw = _snapshot(
        header_date="2026-07-20",
        pre_section_text=("holdings date 2099-01-01",),
        post_section_text=("# updated 2099-01-01",),
    )
    result, _, _, _, _ = _build(tmp_path, portfolio_raw=raw)
    assert result.valid
    assert result.projection is not None
    assert result.projection["portfolio_source_date"] == "2026-07-20"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (
            b"\xef\xbb\xbf" + _snapshot(),
            "MMI_PORTFOLIO_SOURCE_BOM_PROHIBITED",
        ),
        (
            b"# updated 2026-07-24\n\xff\n",
            "MMI_PORTFOLIO_SOURCE_UTF8_INVALID",
        ),
        (
            b"# updated 2026-07-24\n\x00\n",
            "MMI_PORTFOLIO_SOURCE_TEXT_INVALID",
        ),
    ],
)
def test_authenticated_source_text_still_fails_closed_on_invalid_encoding(
    tmp_path: Path,
    raw: bytes,
    code: str,
) -> None:
    result, _, _, _, _ = _build(
        tmp_path,
        portfolio_raw=raw,
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (code,)
    assert result.projection is None
    assert result.authority_effect == "NONE"


def _assert_parse_failed_projection(result: object) -> None:
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert result.authority_effect == "NONE"
    assert result.projection is not None
    assert result.projection["open_buy_orders"] == {
        "status": "PARSE_FAILED",
        "records": [],
        "total_reserved_budget_decimal": None,
    }
    assert "PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED" in (
        result.reason_codes
    )


@pytest.fixture(scope="module")
def strict_adapter_policy_contract(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[
    Path,
    bytes,
    MmiCapturedSource,
    MmiProjectionRunContext,
]:
    root = tmp_path_factory.mktemp("mmi-strict-adapter")
    policy, policy_source, run_context = _policy_contract(root)
    policy_snapshot = json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return root, policy_snapshot, policy_source, run_context


@pytest.mark.parametrize(
    "raw",
    [
        _snapshot(section_start="(2a) existing_buy_open_orders_summary"),
        _snapshot(section_end="(2b) sell_open_orders"),
        _snapshot(
            table_header=OPEN_BUY_HEADER.replace(
                "budget",
                "reserved_budget",
                1,
            )
        ),
        _snapshot(
            rows=[
                "QQQ | 100 | 50 | 50 | T4-E | 700 | "
                "2026-07-20 |  |  |  |  | "
            ]
        ),
        _snapshot(rows=[_open_buy_row(), _open_buy_row()]),
        _snapshot(rows=[_open_buy_row("")]),
        _snapshot(rows=[_open_buy_row("QQQ1")]),
        _snapshot(rows=[_open_buy_row("qqq")]),
        _snapshot(
            rows=[
                _open_buy_row(
                    steps="L1@50",
                    quantities="L2:1",
                )
            ]
        ),
        _snapshot(rows=[_open_buy_row(budget="-1")]),
        _snapshot(rows=[_open_buy_row(budget="NaN")]),
        _snapshot(rows=[_open_buy_row(budget="true")]),
        _snapshot(rows=[_open_buy_row(budget="1,000")]),
        _snapshot(rows=[_open_buy_row(budget="+1")]),
        _snapshot(rows=[_open_buy_row(budget="01")]),
        _snapshot(rows=[_open_buy_row(budget="1e2")]),
        _snapshot(rows=[_open_buy_row(residual="-1")]),
        _snapshot(
            rows=[
                _open_buy_row(
                    stated_notional="50",
                    residual="49",
                )
            ]
        ),
        _snapshot(
            rows=[_open_buy_row(budget="49", stated_notional="50")]
        ),
        _snapshot(rows=[_open_buy_row(template_id="")]),
        _snapshot(rows=[_open_buy_row(anchor_baseline="true")]),
        _snapshot(rows=[_open_buy_row(anchor_date="2026/07/20")]),
        _snapshot(rows=[_open_buy_row(refresh_date="2026-02-30")]),
        _snapshot(
            rows=[
                _open_buy_row(
                    highest_limit="10",
                    lowest_limit="11",
                )
            ]
        ),
        _snapshot(
            rows=[
                _open_buy_row(
                    live_step_count="2",
                    steps="L1@50",
                    quantities="L1:1",
                )
            ]
        ),
        _snapshot(
            rows=[
                _open_buy_row(
                    live_step_count="1",
                    steps="L1@50;L1@50",
                    quantities="L1:1",
                )
            ]
        ),
        _snapshot(
            rows=[
                _open_buy_row(
                    live_step_count="1",
                    steps="L1@-50",
                    quantities="L1:1",
                )
            ]
        ),
        _snapshot(
            rows=[
                _open_buy_row(
                    live_step_count="1",
                    steps="L1@50",
                    quantities="L1:true",
                )
            ]
        ),
        _snapshot(
            rows=[
                _open_buy_row(
                    stated_notional="49",
                    residual="51",
                    live_step_count="1",
                    steps="L1@50",
                    quantities="L1:1",
                )
            ]
        ),
        _snapshot(
            rows=[
                _open_buy_row(
                    live_step_count="",
                    steps="L1@50",
                    quantities="",
                )
            ]
        ),
        _snapshot(
            rows=[
                _open_buy_row(),
                _open_buy_row("VOO", budget="bad"),
            ]
        ),
        _snapshot(
            rows=[
                _open_buy_row(),
                "",
                _open_buy_row("VOO"),
            ]
        ),
        _snapshot(
            rows=[
                _open_buy_row(f"Q{index}", budget="1")
                for index in range(257)
            ]
        ),
    ],
)
def test_strict_adapter_rejects_every_defect_all_or_none(
    raw: bytes,
    strict_adapter_policy_contract: tuple[
        Path,
        bytes,
        MmiCapturedSource,
        MmiProjectionRunContext,
    ],
) -> None:
    root, policy_snapshot, policy_source, run_context = (
        strict_adapter_policy_contract
    )
    policy = json.loads(policy_snapshot.decode("utf-8"))
    assert type(policy) is dict
    source = _portfolio_source(root, raw)
    result = build_mmi_portfolio_snapshot_projection(
        source,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    _assert_parse_failed_projection(result)


def test_strict_adapter_accepts_exact_section_and_preserves_empty_optional(
    tmp_path: Path,
) -> None:
    rows = [
        _open_buy_row(
            "QQQ",
            budget="100.00",
            stated_notional="",
            residual="",
        ),
        _open_buy_row(
            "VOO",
            budget="20.5000",
            stated_notional="10.2500",
        ),
    ]
    projection, _, _, _, _ = _valid_projection(
        tmp_path,
        rows=rows,
    )
    open_buy = projection["open_buy_orders"]
    assert open_buy == {
        "status": "SOURCE_VALIDATED",
        "records": [
            {
                "ticker": "QQQ",
                "reserved_budget_decimal": "100",
                "stated_compiled_notional_decimal": None,
                "policy_membership_classification": "CORE",
                "policy_role_annotation": "CORE",
                "outside_policy_universe": False,
            },
            {
                "ticker": "VOO",
                "reserved_budget_decimal": "20.5",
                "stated_compiled_notional_decimal": "10.25",
                "policy_membership_classification": "CORE",
                "policy_role_annotation": "CORE",
                "outside_policy_universe": False,
            },
        ],
        "total_reserved_budget_decimal": "120.5",
    }
    assert "PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED" not in _gap_codes(
        projection
    )


def test_explicit_valid_empty_section_means_zero_open_buy_budget_only(
    tmp_path: Path,
) -> None:
    projection, _, _, _, _ = _valid_projection(
        tmp_path,
        rows=[],
    )
    assert projection["open_buy_orders"] == {
        "status": "SOURCE_VALIDATED",
        "records": [],
        "total_reserved_budget_decimal": "0",
    }
    assert projection["holdings"]["status"] == (
        "UNSTRUCTURED_NOT_PROJECTED"
    )
    assert projection["cash"]["status"] == "UNAVAILABLE_NOT_PROJECTED"


def test_exact_maximum_open_buy_record_count_is_accepted(
    tmp_path: Path,
) -> None:
    tickers = [
        f"{chr(65 + index // 26)}{chr(65 + index % 26)}"
        for index in range(256)
    ]
    projection, _, _, _, _ = _valid_projection(
        tmp_path,
        rows=[
            _open_buy_row(ticker, budget="1")
            for ticker in tickers
        ],
    )
    records = projection["open_buy_orders"]["records"]
    assert len(records) == 256
    assert [record["ticker"] for record in records] == tickers
    assert projection["open_buy_orders"][
        "total_reserved_budget_decimal"
    ] == "256"
    outside_gap = next(
        gap
        for gap in projection["known_gaps"]
        if gap["code"]
        == "PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE"
    )
    assert outside_gap["affected_tickers"] == tickers


def test_policy_annotations_use_only_source_bound_p1a_membership(
    tmp_path: Path,
) -> None:
    rows = [
        _open_buy_row("QQQ", budget="1"),
        _open_buy_row("SMH", budget="2"),
        _open_buy_row("CIBR", budget="3"),
        _open_buy_row("QUAL", budget="4"),
        _open_buy_row("ZZZ", budget="5"),
    ]
    projection, _, policy, _, _ = _valid_projection(
        tmp_path,
        rows=rows,
    )
    policy_before = deepcopy(policy)
    records = projection["open_buy_orders"]["records"]
    assert [
        (
            record["ticker"],
            record["policy_membership_classification"],
            record["policy_role_annotation"],
            record["outside_policy_universe"],
        )
        for record in records
    ] == [
        ("QQQ", "CORE", "CORE", False),
        ("SMH", "SATELLITE", "SATELLITE", False),
        ("CIBR", "APPROVED_EXTENDED", "APPROVED_EXTENDED", False),
        ("QUAL", "APPROVED_EXTENDED", "APPROVED_EXTENDED", False),
        ("ZZZ", "OUTSIDE_POLICY_UNIVERSE", None, True),
    ]
    outside_gap = next(
        gap
        for gap in projection["known_gaps"]
        if gap["code"]
        == "PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE"
    )
    assert outside_gap["affected_tickers"] == ["ZZZ"]
    assert policy == policy_before
    universe = policy["universe_projection"]
    assert "ZZZ" not in universe["role_by_ticker"]
    assert "ZZZ" not in universe["analysis_scope_instruments"]
    serialized = json.dumps(projection, sort_keys=True)
    assert "ELIGIBLE" not in serialized
    assert "AVAILABLE" in serialized
    assert "NOT_DETERMINISTICALLY_AVAILABLE" not in serialized
    assert "PERMISSION" not in serialized
    assert "GATE" not in serialized


def test_policy_projection_must_validate_against_same_source_and_time(
    tmp_path: Path,
) -> None:
    policy, policy_source, run_context = _policy_contract(
        tmp_path / "original"
    )
    portfolio_source = _portfolio_source(
        tmp_path / "portfolio",
        _snapshot(),
    )

    altered = deepcopy(policy)
    altered["policy_as_of_date"] = "2026-07-23"
    altered["policy_projection_identity_sha256"] = (
        _independent_identity(
            altered,
            identity_field="policy_projection_identity_sha256",
            domain=MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
        )
    )
    altered_result = build_mmi_portfolio_snapshot_projection(
        portfolio_source,
        policy_projection=altered,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert not altered_result.valid
    assert altered_result.authority_effect == "NONE"

    different_settings = _valid_settings()
    different_settings["benchmark"] = "VOO"
    different_source = _capture_source(
        tmp_path / "different-policy",
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=_raw_settings(different_settings),
    )
    wrong_source = build_mmi_portfolio_snapshot_projection(
        portfolio_source,
        policy_projection=policy,
        policy_source=different_source,
        run_context=run_context,
    )
    assert not wrong_source.valid

    wrong_role_source = build_mmi_portfolio_snapshot_projection(
        None,
        policy_projection=policy,
        policy_source=portfolio_source,
        run_context=run_context,
    )
    assert wrong_role_source.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert wrong_role_source.reason_codes == (
        "MMI_PORTFOLIO_POLICY_SOURCE_ROLE_INVALID",
    )

    other_context = _begin_mmi_projection_run_with_clock(
        _FixedClock(
            datetime(2026, 7, 25, 13, tzinfo=timezone.utc)
        )
    )
    wrong_time = build_mmi_portfolio_snapshot_projection(
        portfolio_source,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=other_context,
    )
    assert not wrong_time.valid


def test_forged_or_wrong_role_sources_fail_closed(
    tmp_path: Path,
) -> None:
    policy, policy_source, run_context = _policy_contract(tmp_path)
    raw = _snapshot()
    legitimate = _portfolio_source(tmp_path, raw)
    forged = object.__new__(MmiCapturedSource)
    object.__setattr__(
        forged,
        "role",
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    )
    object.__setattr__(forged, "raw_bytes", raw)
    object.__setattr__(
        forged,
        "source_record",
        MappingProxyType(dict(legitimate.source_record)),
    )
    object.__setattr__(forged, "_provenance_token", b"\x00" * 32)
    object.__setattr__(forged, "_provenance_seal", b"\x00" * 32)
    forged_result = build_mmi_portfolio_snapshot_projection(
        forged,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert forged_result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert forged_result.reason_codes == (
        "MMI_PORTFOLIO_CAPTURE_PROVENANCE_INVALID",
    )
    assert forged_result.authority_effect == "NONE"
    assert forged_result.projection is None

    wrong_role = build_mmi_portfolio_snapshot_projection(
        policy_source,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert wrong_role.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert wrong_role.reason_codes == (
        "MMI_PORTFOLIO_CAPTURE_ROLE_INVALID",
    )


def test_forged_policy_source_and_run_context_fail_before_use(
    tmp_path: Path,
) -> None:
    policy, policy_source, run_context = _policy_contract(tmp_path)
    forged_policy_source = object.__new__(MmiCapturedSource)
    object.__setattr__(
        forged_policy_source,
        "role",
        MmiSourceRole.STRATEGY_SETTINGS,
    )
    object.__setattr__(
        forged_policy_source,
        "raw_bytes",
        policy_source.raw_bytes,
    )
    object.__setattr__(
        forged_policy_source,
        "source_record",
        policy_source.source_record,
    )
    object.__setattr__(
        forged_policy_source,
        "_provenance_token",
        b"\x01" * 32,
    )
    object.__setattr__(
        forged_policy_source,
        "_provenance_seal",
        b"\x01" * 32,
    )
    policy_failure = build_mmi_portfolio_snapshot_projection(
        None,
        policy_projection=policy,
        policy_source=forged_policy_source,
        run_context=run_context,
    )
    assert policy_failure.reason_codes == (
        "MMI_PORTFOLIO_POLICY_SOURCE_PROVENANCE_INVALID",
    )

    forged_context = object.__new__(MmiProjectionRunContext)
    for name in (
        "evaluation_time_utc",
        "evaluation_timestamp_utc",
        "authority_effect",
    ):
        object.__setattr__(
            forged_context,
            name,
            getattr(run_context, name),
        )
    object.__setattr__(
        forged_context,
        "_provenance_token",
        b"\x02" * 32,
    )
    object.__setattr__(
        forged_context,
        "_provenance_seal",
        b"\x02" * 32,
    )
    context_failure = build_mmi_portfolio_snapshot_projection(
        None,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=forged_context,
    )
    assert context_failure.reason_codes == (
        "MMI_PROJECTION_RUN_CONTEXT_PROVENANCE_INVALID",
    )
    assert context_failure.authority_effect == "NONE"


def test_decimal_total_is_exact_canonical_and_context_independent(
    tmp_path: Path,
) -> None:
    rows = [
        _open_buy_row("QQQ", budget="0.1000", stated_notional=""),
        _open_buy_row("VOO", budget="0.20", stated_notional="0.00"),
        _open_buy_row(
            "SMH",
            budget="999999999999999999999999.123456789",
            stated_notional="1.2300",
        ),
    ]
    (
        policy,
        policy_source,
        run_context,
    ) = _policy_contract(tmp_path / "policy")
    source = _portfolio_source(
        tmp_path / "portfolio",
        _snapshot(rows),
    )
    with localcontext() as context:
        context.prec = 1
        context.rounding = ROUND_DOWN
        for signal in (
            Clamped,
            DivisionByZero,
            Inexact,
            InvalidOperation,
            Overflow,
            Rounded,
            Underflow,
        ):
            context.traps[signal] = True
        result = build_mmi_portfolio_snapshot_projection(
            source,
            policy_projection=policy,
            policy_source=policy_source,
            run_context=run_context,
        )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    open_buy = result.projection["open_buy_orders"]
    assert [
        record["reserved_budget_decimal"]
        for record in open_buy["records"]
    ] == [
        "0.1",
        "0.2",
        "999999999999999999999999.123456789",
    ]
    assert [
        record["stated_compiled_notional_decimal"]
        for record in open_buy["records"]
    ] == [None, "0", "1.23"]
    assert open_buy["total_reserved_budget_decimal"] == (
        "999999999999999999999999.423456789"
    )


def test_total_overflow_fails_the_entire_open_buy_projection(
    tmp_path: Path,
) -> None:
    maximum = "9" * 48
    result, _, _, _, _ = _build(
        tmp_path,
        portfolio_raw=_snapshot(
            [
                _open_buy_row("QQQ", budget=maximum),
                _open_buy_row("VOO", budget=maximum),
            ]
        ),
    )
    _assert_parse_failed_projection(result)


def test_source_order_is_preserved_and_identity_sensitive(
    tmp_path: Path,
) -> None:
    first_rows = [
        _open_buy_row("QQQ", budget="1"),
        _open_buy_row("VOO", budget="2"),
    ]
    second_rows = list(reversed(first_rows))
    first, _, _, _, _ = _valid_projection(
        tmp_path / "first",
        rows=first_rows,
    )
    second, _, _, _, _ = _valid_projection(
        tmp_path / "second",
        rows=second_rows,
    )
    assert [
        record["ticker"]
        for record in first["open_buy_orders"]["records"]
    ] == ["QQQ", "VOO"]
    assert [
        record["ticker"]
        for record in second["open_buy_orders"]["records"]
    ] == ["VOO", "QQQ"]
    assert first["portfolio_projection_identity_sha256"] != second[
        "portfolio_projection_identity_sha256"
    ]


def test_legitimate_different_portfolio_sources_each_validate(
    tmp_path: Path,
) -> None:
    policy, policy_source, run_context = _policy_contract(
        tmp_path / "policy"
    )
    projections: list[dict[str, object]] = []
    sources: list[MmiCapturedSource] = []
    for name, rows in (
        ("first", [_open_buy_row("QQQ", budget="1")]),
        ("second", [_open_buy_row("VOO", budget="2")]),
    ):
        source = _portfolio_source(
            tmp_path / name,
            _snapshot(rows, header_date="2026-07-23"),
        )
        result = build_mmi_portfolio_snapshot_projection(
            source,
            policy_projection=policy,
            policy_source=policy_source,
            run_context=run_context,
        )
        assert result.valid, result.reason_codes
        assert result.projection is not None
        projection = dict(result.projection)
        validation = _validate_candidate(
            projection,
            portfolio_source=source,
            policy=policy,
            policy_source=policy_source,
            run_context=run_context,
        )
        assert validation.valid
        projections.append(projection)
        sources.append(source)
    assert projections[0] != projections[1]
    cross_validation = _validate_candidate(
        projections[0],
        portfolio_source=sources[1],
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert not cross_validation.valid
    assert cross_validation.reason_codes == (
        "MMI_PORTFOLIO_SOURCE_FIDELITY_MISMATCH",
    )


def test_same_sources_and_run_context_produce_identical_persistent_output(
    tmp_path: Path,
) -> None:
    policy, policy_source, run_context = _policy_contract(tmp_path)
    source = _portfolio_source(tmp_path, _snapshot())
    first = build_mmi_portfolio_snapshot_projection(
        source,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    second = build_mmi_portfolio_snapshot_projection(
        source,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert first.valid and second.valid
    assert first.projection == second.projection
    serialized = json.dumps(first.projection, sort_keys=True)
    assert "provenance" not in serialized.casefold()
    assert "_seal" not in serialized.casefold()
    assert "_token" not in serialized.casefold()
    assert str(tmp_path) not in serialized


def test_independent_identity_reproduction_and_four_domains(
    tmp_path: Path,
) -> None:
    projection, source, policy, _, _ = _valid_projection(tmp_path)
    assert projection["portfolio_projection_identity_sha256"] == (
        _independent_identity(
            projection,
            identity_field="portfolio_projection_identity_sha256",
            domain=(
                MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN
            ),
        )
    )
    source_record = dict(source.source_record)
    assert source_record["source_record_identity_sha256"] == (
        _independent_identity(
            source_record,
            identity_field="source_record_identity_sha256",
            domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        )
    )
    universe = policy["universe_projection"]
    assert universe["universe_projection_identity_sha256"] == (
        _independent_identity(
            universe,
            identity_field="universe_projection_identity_sha256",
            domain=MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
        )
    )
    assert policy["policy_projection_identity_sha256"] == (
        _independent_identity(
            policy,
            identity_field="policy_projection_identity_sha256",
            domain=MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
        )
    )
    domains = (
        MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
        MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
        MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN,
    )
    assert len(domains) == len(set(domains)) == 4
    assert (
        MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN
        == b"mmi_portfolio_snapshot_projection_v1\0"
    )
    assert not any(
        token in portfolio_projection.__dict__
        for token in (
            "MMI_EVIDENCE_IDENTITY_DOMAIN",
            "MMI_ANALYST_VIEW_IDENTITY_DOMAIN",
            "MMI_INPUT_PACKAGE_IDENTITY_DOMAIN",
            "MMI_PROMPT_IDENTITY_DOMAIN",
            "MMI_RESPONSE_IDENTITY_DOMAIN",
        )
    )


def _assert_resealed_rejected(
    candidate: dict[str, object],
    *,
    portfolio_source: MmiCapturedSource,
    policy: dict[str, object],
    policy_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> None:
    _reseal_portfolio(candidate)
    validation = _validate_candidate(
        candidate,
        portfolio_source=portfolio_source,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert not validation.valid
    assert validation.authority_effect == "NONE"


def test_schema_valid_resealed_source_fidelity_mutations_are_rejected(
    tmp_path: Path,
) -> None:
    (
        projection,
        portfolio_source,
        policy,
        policy_source,
        run_context,
    ) = _valid_projection(
        tmp_path,
        rows=[_open_buy_row("QQQ", budget="100", stated_notional="50")],
    )
    candidates: dict[str, dict[str, object]] = {}

    changed_date = deepcopy(projection)
    changed_date["portfolio_source_date"] = "2026-07-23"
    candidates["source_date"] = changed_date

    changed_source_identity = deepcopy(projection)
    changed_source_identity[
        "portfolio_source_record_identity_sha256"
    ] = "1" * 64
    for gap in changed_source_identity["known_gaps"]:
        gap["portfolio_source_record_identity_sha256"] = "1" * 64
    candidates["source_identity"] = changed_source_identity

    changed_policy_identity = deepcopy(projection)
    changed_policy_identity["policy_projection_identity_sha256"] = (
        "2" * 64
    )
    for gap in changed_policy_identity["known_gaps"]:
        gap["policy_projection_identity_sha256"] = "2" * 64
    candidates["policy_identity"] = changed_policy_identity

    changed_ticker = deepcopy(projection)
    changed_ticker["open_buy_orders"]["records"][0]["ticker"] = "VOO"
    candidates["ticker"] = changed_ticker

    changed_budget = deepcopy(projection)
    changed_budget["open_buy_orders"]["records"][0][
        "reserved_budget_decimal"
    ] = "101"
    changed_budget["open_buy_orders"][
        "total_reserved_budget_decimal"
    ] = "101"
    candidates["reserved_budget"] = changed_budget

    changed_stated = deepcopy(projection)
    changed_stated["open_buy_orders"]["records"][0][
        "stated_compiled_notional_decimal"
    ] = "51"
    candidates["stated_notional"] = changed_stated

    parse_failed = deepcopy(projection)
    parse_failed["open_buy_orders"] = {
        "status": "PARSE_FAILED",
        "records": [],
        "total_reserved_budget_decimal": None,
    }
    parse_gap = {
        "code": "PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED",
        "scope": "PORTFOLIO_SNAPSHOT",
        "affected_tickers": [],
        "policy_projection_identity_sha256": projection[
            "policy_projection_identity_sha256"
        ],
        "portfolio_source_record_identity_sha256": projection[
            "portfolio_source_record_identity_sha256"
        ],
    }
    parse_failed["known_gaps"].insert(1, parse_gap)
    candidates["open_buy_status"] = parse_failed

    for name, candidate in candidates.items():
        _reseal_portfolio(candidate)
        validate_artifact_schema(
            candidate,
            schema_name=(
                "mmi_portfolio_snapshot_projection_v1.schema.json"
            ),
        )
        validation = _validate_candidate(
            candidate,
            portfolio_source=portfolio_source,
            policy=policy,
            policy_source=policy_source,
            run_context=run_context,
        )
        assert not validation.valid, name
        assert validation.authority_effect == "NONE", name
        if name == "policy_identity":
            assert validation.reason_codes == (
                "MMI_PORTFOLIO_PROJECTION_SEMANTIC_INVALID",
            )
        else:
            assert validation.reason_codes == (
                "MMI_PORTFOLIO_SOURCE_FIDELITY_MISMATCH",
            )


def test_every_persistent_projection_surface_rejects_resealed_mutation(
    tmp_path: Path,
) -> None:
    (
        projection,
        portfolio_source,
        policy,
        policy_source,
        run_context,
    ) = _valid_projection(tmp_path)
    mutated: list[dict[str, object]] = []

    for key, replacement in (
        ("schema_version", "mmi_portfolio_snapshot_projection_v2"),
        ("projection_kind", "OTHER"),
        ("report_only", False),
        ("authority_effect", "SOME"),
        ("evaluation_timestamp_utc", "2026-07-25T13:00:00.000000Z"),
        ("policy_projection_identity_sha256", "3" * 64),
        ("portfolio_source_status", "SOURCE_ABSENT"),
        ("portfolio_source_record_identity_sha256", "4" * 64),
        ("portfolio_source_date", "2026-07-23"),
        ("completeness_status", "PROJECTION_VALID_COMPLETE"),
    ):
        candidate = deepcopy(projection)
        candidate[key] = replacement
        mutated.append(candidate)

    for key in (
        "holdings",
        "cash",
        "deployable_cash",
        "open_sell_orders",
        "tax_lots",
        "holding_dates",
        "gains_losses",
        "weights",
        "nav_concentration",
        "lookthrough_exposure",
    ):
        candidate = deepcopy(projection)
        candidate[key]["status"] = "CHANGED"
        mutated.append(candidate)

    record_fields = {
        "ticker": "VOO",
        "reserved_budget_decimal": "101",
        "stated_compiled_notional_decimal": "51",
        "policy_membership_classification": "SATELLITE",
        "policy_role_annotation": "SATELLITE",
        "outside_policy_universe": True,
    }
    for key, replacement in record_fields.items():
        candidate = deepcopy(projection)
        candidate["open_buy_orders"]["records"][0][key] = replacement
        mutated.append(candidate)

    changed_total = deepcopy(projection)
    changed_total["open_buy_orders"][
        "total_reserved_budget_decimal"
    ] = "101"
    mutated.append(changed_total)

    changed_gap = deepcopy(projection)
    changed_gap["known_gaps"][0]["code"] = (
        "PORTFOLIO_WEIGHTS_UNAVAILABLE"
    )
    mutated.append(changed_gap)

    removed_gap = deepcopy(projection)
    removed_gap["known_gaps"].pop()
    mutated.append(removed_gap)

    changed_identity = deepcopy(projection)
    changed_identity["portfolio_projection_identity_sha256"] = "5" * 64

    for candidate in mutated:
        _assert_resealed_rejected(
            candidate,
            portfolio_source=portfolio_source,
            policy=policy,
            policy_source=policy_source,
            run_context=run_context,
        )
    identity_validation = _validate_candidate(
        changed_identity,
        portfolio_source=portfolio_source,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert not identity_validation.valid


def test_valid_projection_cannot_be_validated_against_source_absence(
    tmp_path: Path,
) -> None:
    (
        projection,
        _portfolio_source_value,
        policy,
        policy_source,
        run_context,
    ) = _valid_projection(tmp_path)
    validation = _validate_candidate(
        projection,
        portfolio_source=None,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert not validation.valid
    assert validation.reason_codes == (
        "MMI_PORTFOLIO_SOURCE_FIDELITY_MISMATCH",
    )


def test_privacy_boundary_excludes_raw_unstructured_and_execution_data(
    tmp_path: Path,
) -> None:
    raw = _snapshot(
        [_open_buy_row("QQQ", budget="100", stated_notional="50")],
        pre_section_text=(
            "BROKER_PRIVATE ACCT-1234 holdings 999 shares cost basis 321",
            "operator note: private prose",
        ),
        post_section_text=(
            "SELL 9 shares at LIMIT 999 for TAXLOT-SECRET",
            "/absolute/private/path/portfolio_snapshot.txt",
        ),
    )
    result, _, _, _, _ = _build(tmp_path, portfolio_raw=raw)
    assert result.valid
    assert result.projection is not None
    serialized = json.dumps(
        result.projection,
        ensure_ascii=False,
        sort_keys=True,
    )
    for forbidden in (
        "BROKER_PRIVATE",
        "ACCT-1234",
        "999 shares",
        "cost basis",
        "operator note",
        "SELL 9",
        "LIMIT 999",
        "TAXLOT-SECRET",
        "/absolute/private/path",
        "SECRET_BROKER",
        "SECRET_ACCOUNT",
        "raw sell instruction",
        "secret tax lot",
        "T4-E",
        "700.00",
    ):
        assert forbidden not in serialized
    prohibited_key_fragments = (
        "account",
        "broker",
        "quantity",
        "shares",
        "limit_price",
        "price_target",
        "order_id",
        "tax_lot_id",
        "cost_basis",
        "instruction",
        "raw",
        "path",
        "permission",
        "readiness",
        "gate",
    )

    def keys(value: object):
        if type(value) is dict:
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif type(value) is list:
            for child in value:
                yield from keys(child)

    all_keys = tuple(keys(result.projection))
    assert not any(
        fragment in key.casefold()
        for key in all_keys
        for fragment in prohibited_key_fragments
    )


def test_failure_diagnostics_never_expose_raw_parser_or_source_data(
    tmp_path: Path,
) -> None:
    raw_marker = "RAW_SECRET_ACCOUNT_987"
    malformed = _snapshot(
        [
            _open_buy_row(),
            f"{raw_marker} | bad | malformed",
        ]
    )
    result, _, _, _, _ = _build(
        tmp_path,
        portfolio_raw=malformed,
    )
    _assert_parse_failed_projection(result)
    assert raw_marker not in repr(result)
    assert "expected 13 columns" not in repr(result)


def test_schema_is_closed_draft_2020_12_and_authority_free(
    tmp_path: Path,
) -> None:
    schema_path = (
        Path("schemas")
        / "mmi_portfolio_snapshot_projection_v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$schema"] == (
        "https://json-schema.org/draft/2020-12/schema"
    )

    def object_schemas(value: object):
        if type(value) is dict:
            if value.get("type") == "object":
                yield value
            for child in value.values():
                yield from object_schemas(child)
        elif type(value) is list:
            for child in value:
                yield from object_schemas(child)

    assert all(
        item.get("additionalProperties") is False
        for item in object_schemas(schema)
    )
    schema_text = schema_path.read_text(encoding="utf-8").casefold()
    for forbidden in (
        "account_id",
        "broker_id",
        "permission",
        "allowed_action",
        "readiness",
        "quantity",
        "share_count",
        "order_compilation",
        "publication",
        "pointer",
        "prompt_identity",
        "response_identity",
        "evidence_identity",
    ):
        assert forbidden not in schema_text

    projection, _, _, _, _ = _valid_projection(tmp_path)
    validate_artifact_schema(
        projection,
        schema_name="mmi_portfolio_snapshot_projection_v1.schema.json",
    )
    candidate = deepcopy(projection)
    candidate["unexpected"] = "closed"
    with pytest.raises(Exception):
        validate_artifact_schema(
            candidate,
            schema_name=(
                "mmi_portfolio_snapshot_projection_v1.schema.json"
            ),
        )


def test_current_repository_sources_capture_build_and_validate(
    tmp_path: Path,
) -> None:
    strategy_raw = Path(
        "inputs/current/strategy_settings.yaml"
    ).read_bytes()
    portfolio_raw = Path(
        "inputs/current/portfolio_snapshot.txt"
    ).read_bytes()
    assert hashlib.sha256(strategy_raw).hexdigest() == (
        "fde678173e2d115dbdad3e73ad5ac74fb"
        "730ee4f40fb9c06c89fdefbcf732d26"
    )
    assert hashlib.sha256(portfolio_raw).hexdigest() == (
        "feabb3b03fa1022c6bc40c4214f7cb0d77"
        "1cc4e7844cf0fc0a738a265b260916"
    )
    policy, policy_source, run_context = _policy_contract(
        tmp_path,
        raw=strategy_raw,
    )
    policy_before = deepcopy(policy)
    portfolio_source = _portfolio_source(tmp_path, portfolio_raw)
    result = build_mmi_portfolio_snapshot_projection(
        portfolio_source,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert result.projection is not None
    projection = dict(result.projection)
    assert projection["portfolio_source_date"] == "2026-06-28"
    open_buy = projection["open_buy_orders"]
    assert open_buy["status"] == "SOURCE_VALIDATED"
    assert [
        record["ticker"] for record in open_buy["records"]
    ] == ["QQQ", "VOO", "SMH", "CIBR", "GRID"]
    assert open_buy["total_reserved_budget_decimal"] == "16078.45"
    assert policy == policy_before
    validation = _validate_candidate(
        projection,
        portfolio_source=portfolio_source,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert validation.valid
    assert projection["portfolio_projection_identity_sha256"] == (
        _independent_identity(
            projection,
            identity_field="portfolio_projection_identity_sha256",
            domain=(
                MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN
            ),
        )
    )
    production_source = Path(
        "src/investment_orchestrator/mmi/portfolio_projection.py"
    ).read_text(encoding="utf-8")
    for ticker in ("QQQ", "VOO", "SMH", "CIBR", "GRID"):
        assert f'"{ticker}"' not in production_source


def test_p1a_projection_and_identities_are_unchanged_by_p1b_build(
    tmp_path: Path,
) -> None:
    policy, policy_source, run_context = _policy_contract(tmp_path)
    before = deepcopy(policy)
    before_universe_identity = policy[
        "universe_projection_identity_sha256"
    ]
    before_policy_identity = policy[
        "policy_projection_identity_sha256"
    ]
    result = build_mmi_portfolio_snapshot_projection(
        None,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert result.valid
    assert policy == before
    assert policy["universe_projection_identity_sha256"] == (
        before_universe_identity
    )
    assert policy["policy_projection_identity_sha256"] == (
        before_policy_identity
    )


def test_result_categories_remain_closed_and_authority_none(
    tmp_path: Path,
) -> None:
    assert tuple(category.value for category in MmiProjectionResultCategory) == (
        "PROJECTION_VALID_COMPLETE",
        "PROJECTION_VALID_WITH_GAPS",
        "PROJECTION_BLOCKED",
        "PROJECTION_CONTRACT_FAILURE",
    )
    valid, _, _, _, _ = _build(tmp_path / "valid")
    blocked, _, _, _, _ = _build(
        tmp_path / "blocked",
        portfolio_raw=_snapshot(header_date="2026-07-26"),
    )
    policy, policy_source, run_context = _policy_contract(
        tmp_path / "contract"
    )
    forged = object.__new__(MmiCapturedSource)
    object.__setattr__(
        forged,
        "role",
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    )
    object.__setattr__(forged, "raw_bytes", b"")
    object.__setattr__(forged, "source_record", MappingProxyType({}))
    object.__setattr__(forged, "_provenance_token", b"")
    object.__setattr__(forged, "_provenance_seal", b"")
    contract = build_mmi_portfolio_snapshot_projection(
        forged,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert valid.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert blocked.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert contract.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert all(
        result.authority_effect == "NONE"
        for result in (valid, blocked, contract)
    )
