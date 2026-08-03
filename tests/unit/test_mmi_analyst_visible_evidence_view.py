from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from investment_orchestrator.mmi import analyst_visible_evidence_view
from investment_orchestrator.mmi.analyst_visible_evidence_view import (
    build_mmi_analyst_visible_evidence_view,
    validate_mmi_analyst_visible_evidence_view,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiPolicyProjectionBuildResult,
    MmiPolicyProjectionValidationResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
    mmi_analyst_visible_evidence_view_identity_sha256,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.mmi.source_capture import (
    _capture_mmi_source_at_root,
)


EVALUATION_TIME = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
VIEW_SCHEMA_NAME = "mmi_analyst_visible_evidence_view_v1.schema.json"
SHA_F = "f" * 64
VIEW_IDENTITY_DOMAIN = b"mmi_analyst_visible_evidence_view_v1\0"
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
POLICY_LIMITATION_CODES = (
    "VIEW_POLICY_CASH_MODEL_UNAVAILABLE",
    "VIEW_POLICY_LOOKTHROUGH_EXPOSURE_UNAVAILABLE",
    "VIEW_POLICY_MINIMUM_HOLDING_ENFORCEMENT_INCOMPLETE",
    "VIEW_POLICY_PER_RUN_BUDGET_APPLICABILITY_UNVERIFIED",
    "VIEW_POLICY_PORTFOLIO_NAV_CONCENTRATION_UNAVAILABLE",
    "VIEW_POLICY_SELL_ELIGIBILITY_INCOMPLETE",
    "VIEW_POLICY_TAX_LOT_ENFORCEMENT_UNAVAILABLE",
    "VIEW_POLICY_TURNOVER_ENFORCEMENT_INCOMPLETE",
)


class _FixedClock:
    def __init__(self, observed: datetime = EVALUATION_TIME) -> None:
        self.observed = observed

    def now_utc(self) -> datetime:
        return self.observed


class _OneSnapshotMapping(Mapping[str, object]):
    def __init__(self, value: Mapping[str, object]) -> None:
        self._value = dict(value)
        self.iterations = 0
        self.length_reads = 0
        self.emitted_keys: list[str] = []
        self.lookup_counts: dict[str, int] = {}

    def __iter__(self) -> Iterator[str]:
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("caller mapping was read more than once")
        for key in self._value:
            self.emitted_keys.append(key)
            yield key

    def __len__(self) -> int:
        self.length_reads += 1
        return len(self._value)

    def __getitem__(self, key: str) -> object:
        self.lookup_counts[key] = self.lookup_counts.get(key, 0) + 1
        if self.lookup_counts[key] > 1:
            raise AssertionError("caller key was looked up more than once")
        return self._value[key]

    def assert_single_read(self) -> None:
        assert self.iterations == 1
        assert self.length_reads == 0
        assert self.emitted_keys == list(self._value)
        assert self.lookup_counts == dict.fromkeys(self._value, 1)


class _DuplicateKeyMapping(Mapping[str, object]):
    def __init__(
        self,
        value: Mapping[str, object],
        *,
        duplicate_key: str,
        first_value: object,
        second_value: object,
        distinct_equal_key: bool = False,
    ) -> None:
        self._value = dict(value)
        assert duplicate_key in self._value
        second_key = duplicate_key
        if distinct_equal_key:
            second_key = ("_" + duplicate_key)[1:]
            assert second_key == duplicate_key
            assert second_key is not duplicate_key
            assert type(second_key) is str
        emitted_keys: list[str] = []
        for key in self._value:
            emitted_keys.append(key)
            if key == duplicate_key:
                emitted_keys.append(second_key)
        self._emitted_keys = tuple(emitted_keys)
        self._duplicate_key = duplicate_key
        self._duplicate_values = (first_value, second_value)
        self.iterations = 0
        self.length_reads = 0
        self.lookup_counts: dict[str, int] = {}

    def __iter__(self) -> Iterator[str]:
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("duplicate mapping received a second iterator")
        return iter(self._emitted_keys)

    def __len__(self) -> int:
        self.length_reads += 1
        return len(self._emitted_keys)

    def __getitem__(self, key: str) -> object:
        self.lookup_counts[key] = self.lookup_counts.get(key, 0) + 1
        if key == self._duplicate_key:
            lookup_index = self.lookup_counts[key] - 1
            if lookup_index >= len(self._duplicate_values):
                raise AssertionError("duplicate key received an extra lookup")
            return self._duplicate_values[lookup_index]
        return self._value[key]

    def assert_duplicate_rejected_before_second_lookup(self) -> None:
        assert self.iterations == 1
        assert self.length_reads == 0
        assert self.lookup_counts[self._duplicate_key] == 1


class _CopyHookTrap:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __copy__(self) -> object:
        self.calls.append("copy")
        raise AssertionError("copy hook must not be invoked")

    def __deepcopy__(self, _memo: object) -> object:
        self.calls.append("deepcopy")
        raise AssertionError("deepcopy hook must not be invoked")

    def __reduce__(self) -> object:
        self.calls.append("reduce")
        raise AssertionError("serialization hook must not be invoked")


@dataclass(frozen=True, slots=True)
class _TrustedInputs:
    policy: dict[str, object]
    policy_source: MmiCapturedSource
    run_context: MmiProjectionRunContext
    omitted_bundle: dict[str, object]
    source_absent_portfolio: dict[str, object]
    source_absent_bundle: dict[str, object]
    source_bound_portfolio: dict[str, object]
    source_bound_bundle: dict[str, object]
    portfolio_source: MmiCapturedSource
    parse_failed_portfolio: dict[str, object]
    parse_failed_bundle: dict[str, object]
    parse_failed_source: MmiCapturedSource
    alternate_run_context: MmiProjectionRunContext


def _settings() -> dict[str, object]:
    return {
        "as_of": "2026-07-26",
        "run_timestamp_et": "2026-07-26 10:00 ET",
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
            "single_extended_etf_budget_cap_pct_of_total_open_orders": (
                0.20
            ),
            "activation_minimum_effective_budget_pct_of_total_open_orders": (
                0.04
            ),
            "max_same_theme_extended_etf_count": 1,
            "max_same_theme_budget_pct_of_total_open_orders": 0.25,
            "require_distinct_theme_buckets_when_multiple_extended_etfs": (
                True
            ),
        },
    }


def _settings_bytes() -> bytes:
    return yaml.safe_dump(
        _settings(),
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")


def _portfolio_row(ticker: str, budget: str) -> str:
    return " | ".join(
        (
            ticker,
            budget,
            "",
            "",
            "T4-E",
            "700.00",
            "2026-07-20",
            "",
            "",
            "",
            "",
            "",
            "",
        )
    )


def _portfolio_bytes(
    *,
    rows: tuple[tuple[str, str], ...] = (
        ("QQQ", "100.00"),
        ("ARKK", "200.00"),
    ),
    valid_header: bool = True,
) -> bytes:
    header = OPEN_BUY_HEADER if valid_header else "TICKER | malformed"
    return (
        "\n".join(
            (
                "【Portfolio Snapshot】",
                "# updated 2026-07-26",
                "(1) current_holdings_base",
                "PRIVATE_BROKER | QQQ | 9 | 123.45",
                PORTFOLIO_SECTION_START,
                "- exact code-owned explanatory line",
                header,
                *(_portfolio_row(*row) for row in rows),
                "",
                PORTFOLIO_SECTION_END,
                "PRIVATE_ACCOUNT | QQQ | raw sell instruction",
                "(3) LTCG_ELIGIBLE_SELLABLE",
                "QQQ | 9 | 2020-01-01 | private tax lot",
            )
        )
        + "\n"
    ).encode("utf-8")


def _capture(
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


def _build_portfolio(
    source: MmiCapturedSource | None,
    *,
    policy: dict[str, object],
    policy_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    result = build_mmi_portfolio_snapshot_projection(
        source,
        policy_projection=deepcopy(policy),
        policy_source=policy_source,
        run_context=run_context,
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    return dict(result.projection)


def _build_bundle(
    *,
    policy: dict[str, object],
    policy_source: MmiCapturedSource,
    portfolio: dict[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    result = build_mmi_authenticated_evidence_bundle(
        policy_projection=deepcopy(policy),
        policy_source=policy_source,
        portfolio_projection=(
            None if portfolio is None else deepcopy(portfolio)
        ),
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    return dict(result.projection)


@pytest.fixture(scope="module")
def trusted_inputs(
    tmp_path_factory: pytest.TempPathFactory,
) -> _TrustedInputs:
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    policy_source = _capture(
        tmp_path_factory.mktemp("v1c-policy"),
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=_settings_bytes(),
    )
    policy_result = build_mmi_policy_projection(
        policy_source,
        run_context=run_context,
    )
    assert policy_result.valid, policy_result.reason_codes
    assert policy_result.projection is not None
    policy = dict(policy_result.projection)

    source_absent_portfolio = _build_portfolio(
        None,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    portfolio_source = _capture(
        tmp_path_factory.mktemp("v1c-portfolio"),
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        raw=_portfolio_bytes(),
    )
    source_bound_portfolio = _build_portfolio(
        portfolio_source,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    parse_failed_source = _capture(
        tmp_path_factory.mktemp("v1c-parse-failed"),
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        raw=_portfolio_bytes(valid_header=False),
    )
    parse_failed_portfolio = _build_portfolio(
        parse_failed_source,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    return _TrustedInputs(
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
        omitted_bundle=_build_bundle(
            policy=policy,
            policy_source=policy_source,
            portfolio=None,
            portfolio_source=None,
            run_context=run_context,
        ),
        source_absent_portfolio=source_absent_portfolio,
        source_absent_bundle=_build_bundle(
            policy=policy,
            policy_source=policy_source,
            portfolio=source_absent_portfolio,
            portfolio_source=None,
            run_context=run_context,
        ),
        source_bound_portfolio=source_bound_portfolio,
        source_bound_bundle=_build_bundle(
            policy=policy,
            policy_source=policy_source,
            portfolio=source_bound_portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        ),
        portfolio_source=portfolio_source,
        parse_failed_portfolio=parse_failed_portfolio,
        parse_failed_bundle=_build_bundle(
            policy=policy,
            policy_source=policy_source,
            portfolio=parse_failed_portfolio,
            portfolio_source=parse_failed_source,
            run_context=run_context,
        ),
        parse_failed_source=parse_failed_source,
        alternate_run_context=_begin_mmi_projection_run_with_clock(
            _FixedClock(EVALUATION_TIME + timedelta(hours=1))
        ),
    )


def _branch_inputs(
    inputs: _TrustedInputs,
    branch: str,
) -> tuple[
    dict[str, object],
    dict[str, object] | None,
    MmiCapturedSource | None,
]:
    if branch == "NOT_SUPPLIED":
        return inputs.omitted_bundle, None, None
    if branch == "PRESENT_VALIDATED_SOURCE_ABSENT":
        return (
            inputs.source_absent_bundle,
            inputs.source_absent_portfolio,
            None,
        )
    if branch == "PRESENT_SOURCE_BOUND_VALIDATED":
        return (
            inputs.source_bound_bundle,
            inputs.source_bound_portfolio,
            inputs.portfolio_source,
        )
    if branch == "PARSE_FAILED":
        return (
            inputs.parse_failed_bundle,
            inputs.parse_failed_portfolio,
            inputs.parse_failed_source,
        )
    raise AssertionError(branch)


def _build_view(
    inputs: _TrustedInputs,
    branch: str = "PRESENT_SOURCE_BOUND_VALIDATED",
) -> MmiPolicyProjectionBuildResult:
    bundle, portfolio, portfolio_source = _branch_inputs(inputs, branch)
    return build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(bundle),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=(
            None if portfolio is None else deepcopy(portfolio)
        ),
        portfolio_source=portfolio_source,
        run_context=inputs.run_context,
    )


def _valid_view(
    inputs: _TrustedInputs,
    branch: str = "PRESENT_SOURCE_BOUND_VALIDATED",
) -> dict[str, object]:
    result = _build_view(inputs, branch)
    assert result.valid, result.reason_codes
    assert result.projection is not None
    return dict(result.projection)


def _validate_view(
    candidate: object,
    inputs: _TrustedInputs,
    branch: str = "PRESENT_SOURCE_BOUND_VALIDATED",
    *,
    evidence_bundle: dict[str, object] | None = None,
    policy_projection: dict[str, object] | None = None,
    policy_source: MmiCapturedSource | None = None,
    run_context: MmiProjectionRunContext | None = None,
) -> MmiPolicyProjectionValidationResult:
    bundle, portfolio, portfolio_source = _branch_inputs(inputs, branch)
    return validate_mmi_analyst_visible_evidence_view(
        value=candidate,  # type: ignore[arg-type]
        evidence_bundle=deepcopy(
            bundle if evidence_bundle is None else evidence_bundle
        ),
        policy_projection=deepcopy(
            inputs.policy
            if policy_projection is None
            else policy_projection
        ),
        policy_source=(
            inputs.policy_source
            if policy_source is None
            else policy_source
        ),
        portfolio_projection=(
            None if portfolio is None else deepcopy(portfolio)
        ),
        portfolio_source=portfolio_source,
        run_context=(
            inputs.run_context if run_context is None else run_context
        ),
    )


def _independent_view_identity(value: dict[str, object]) -> str:
    preimage = deepcopy(value)
    preimage.pop(
        "analyst_visible_evidence_view_identity_sha256",
        None,
    )
    canonical = json.dumps(
        preimage,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(
        VIEW_IDENTITY_DOMAIN
        + struct.pack(">Q", len(canonical))
        + canonical
    ).hexdigest()


def _reseal_view(value: dict[str, object]) -> None:
    value["analyst_visible_evidence_view_identity_sha256"] = (
        _independent_view_identity(value)
    )


def _container_ids(value: object) -> set[int]:
    if type(value) is dict:
        return {id(value)} | {
            nested_id
            for item in value.values()
            for nested_id in _container_ids(item)
        }
    if type(value) in {list, tuple}:
        return {id(value)} | {
            nested_id
            for item in value
            for nested_id in _container_ids(item)
        }
    return set()


def _assert_blocked_without_view(
    result: (
        MmiPolicyProjectionBuildResult
        | MmiPolicyProjectionValidationResult
    ),
    *,
    status: MmiProjectionResultCategory,
    reason: str,
) -> None:
    assert result.status is status
    assert result.authority_effect == "NONE"
    assert result.reason_codes == (reason,)
    if isinstance(result, MmiPolicyProjectionBuildResult):
        assert result.projection is None


def test_public_surfaces_are_exact_keyword_only_and_not_reexported() -> None:
    expected_build = (
        "evidence_bundle",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    )
    expected_validate = ("value", *expected_build)
    build_signature = inspect.signature(
        build_mmi_analyst_visible_evidence_view
    )
    validate_signature = inspect.signature(
        validate_mmi_analyst_visible_evidence_view
    )
    assert tuple(build_signature.parameters) == expected_build
    assert tuple(validate_signature.parameters) == expected_validate
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in build_signature.parameters.values()
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in validate_signature.parameters.values()
    )
    assert analyst_visible_evidence_view.__all__ == (
        "build_mmi_analyst_visible_evidence_view",
        "validate_mmi_analyst_visible_evidence_view",
    )
    assert not any(
        name
        for name, value in vars(analyst_visible_evidence_view).items()
        if not name.startswith("_")
        and inspect.isclass(value)
        and value.__module__ == analyst_visible_evidence_view.__name__
    )
    import investment_orchestrator.mmi as mmi

    assert mmi.__all__ == ()
    assert not hasattr(
        mmi,
        "build_mmi_analyst_visible_evidence_view",
    )


@pytest.mark.parametrize(
    "branch",
    (
        "NOT_SUPPLIED",
        "PRESENT_VALIDATED_SOURCE_ABSENT",
        "PRESENT_SOURCE_BOUND_VALIDATED",
        "PARSE_FAILED",
    ),
)
def test_every_valid_branch_builds_and_round_trips(
    trusted_inputs: _TrustedInputs,
    branch: str,
) -> None:
    result = _build_view(trusted_inputs, branch)
    assert type(result) is MmiPolicyProjectionBuildResult
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert result.authority_effect == "NONE"
    assert result.projection is not None
    view = dict(result.projection)
    validate_artifact_schema(view, schema_name=VIEW_SCHEMA_NAME)
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(view)
        == view["analyst_visible_evidence_view_identity_sha256"]
        == _independent_view_identity(view)
    )
    validation = _validate_view(view, trusted_inputs, branch)
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert validation.reason_codes == ()
    assert validation.authority_effect == "NONE"


def test_policy_view_is_exact_ordered_and_theme_free(
    trusted_inputs: _TrustedInputs,
) -> None:
    view = _valid_view(trusted_inputs)
    policy_view = view["policy_view"]
    assert type(policy_view) is dict
    assert policy_view == {
        "policy_as_of_date": "2026-07-26",
        "policy_method": (
            "BUDGET_SHORTLIST_ROTATION_WITHOUT_TARGET_WEIGHTS"
        ),
        "benchmark_reference_instruments": ["QQQ"],
        "analysis_instruments": [
            {"ticker": "QQQ", "policy_role": "CORE"},
            {"ticker": "VOO", "policy_role": "CORE"},
            {"ticker": "VTI", "policy_role": "CORE"},
            {"ticker": "VT", "policy_role": "CORE"},
            {"ticker": "SMH", "policy_role": "SATELLITE"},
            {"ticker": "IGV", "policy_role": "SATELLITE"},
            {"ticker": "QUAL", "policy_role": "APPROVED_EXTENDED"},
            {"ticker": "CIBR", "policy_role": "APPROVED_EXTENDED"},
        ],
        "extended_activation_status": "NOT_EVALUATED_REPORT_ONLY",
        "instrument_availability_observation_status": (
            "NOT_DETERMINISTICALLY_AVAILABLE"
        ),
        "target_weights_absence_reason": (
            "POLICY_METHOD_HAS_NO_TARGET_WEIGHTS"
        ),
    }
    serialized = json.dumps(policy_view).casefold()
    assert not {
        "theme",
        "sector",
        "category",
        "description",
        "rank",
        "allocation",
        "recommendation",
        "permission",
    } & set(serialized.replace('"', " ").split())


def test_source_owned_theme_gap_is_validated_but_not_exposed(
    tmp_path: Path,
) -> None:
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    settings = _settings()
    settings.pop("user_approved_extended_etf_theme_map")
    raw = yaml.safe_dump(
        settings,
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    policy_source = _capture(
        tmp_path,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=raw,
    )
    policy_result = build_mmi_policy_projection(
        policy_source,
        run_context=run_context,
    )
    assert policy_result.valid, policy_result.reason_codes
    assert policy_result.projection is not None
    policy = dict(policy_result.projection)
    assert "EXTENDED_THEME_MAP_UNAVAILABLE" in policy_result.reason_codes
    bundle = _build_bundle(
        policy=policy,
        policy_source=policy_source,
        portfolio=None,
        portfolio_source=None,
        run_context=run_context,
    )
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=bundle,
        policy_projection=policy,
        policy_source=policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    serialized = json.dumps(result.projection).casefold()
    assert "theme" not in serialized
    assert "quality_factor" not in serialized
    assert "cybersecurity" not in serialized


def test_portfolio_omission_is_distinct_from_source_absence(
    trusted_inputs: _TrustedInputs,
) -> None:
    omitted = _valid_view(trusted_inputs, "NOT_SUPPLIED")
    absent = _valid_view(
        trusted_inputs,
        "PRESENT_VALIDATED_SOURCE_ABSENT",
    )
    assert omitted["portfolio_view"] == {
        "presence_status": "NOT_SUPPLIED"
    }
    assert absent["portfolio_view"] == {
        "presence_status": "PRESENT_VALIDATED_SOURCE_ABSENT",
        "portfolio_source_date": None,
        "open_buy_status": "SOURCE_ABSENT",
        "open_buy_observations": [],
        "fact_coverage_statuses": {
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
        },
    }
    assert (
        "VIEW_EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED"
        in {
            item["code"]
            for item in omitted["known_view_limitations"]
            if type(item) is dict
        }
    )
    assert (
        "VIEW_PORTFOLIO_SOURCE_MISSING"
        in {
            item["code"]
            for item in absent["known_view_limitations"]
            if type(item) is dict
        }
    )


def test_source_bound_observations_are_qualitative_unique_and_ordered(
    trusted_inputs: _TrustedInputs,
) -> None:
    view = _valid_view(trusted_inputs)
    portfolio = view["portfolio_view"]
    assert type(portfolio) is dict
    assert portfolio["portfolio_source_date"] == "2026-07-26"
    assert portfolio["open_buy_status"] == "SOURCE_VALIDATED"
    assert portfolio["open_buy_observations"] == [
        {
            "ticker": "QQQ",
            "policy_membership_classification": "CORE",
        },
        {
            "ticker": "ARKK",
            "policy_membership_classification": (
                "OUTSIDE_POLICY_UNIVERSE"
            ),
        },
    ]
    assert all(
        set(item) == {
            "ticker",
            "policy_membership_classification",
        }
        for item in portfolio["open_buy_observations"]
    )
    assert (
        "VIEW_PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE",
        ["ARKK"],
    ) in tuple(
        (item["code"], item["affected_tickers"])
        for item in view["known_view_limitations"]
        if type(item) is dict
    )


def test_parse_failure_exposes_no_rows_or_parser_text(
    trusted_inputs: _TrustedInputs,
) -> None:
    view = _valid_view(trusted_inputs, "PARSE_FAILED")
    portfolio = view["portfolio_view"]
    assert type(portfolio) is dict
    assert portfolio["open_buy_status"] == "PARSE_FAILED"
    assert portfolio["open_buy_observations"] == []
    assert (
        "VIEW_PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED"
        in {
            item["code"]
            for item in view["known_view_limitations"]
            if type(item) is dict
        }
    )
    assert "malformed" not in json.dumps(view).casefold()


@pytest.mark.parametrize(
    ("component", "status"),
    (
        ("policy", MmiProjectionResultCategory.PROJECTION_BLOCKED),
        (
            "policy",
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        ("evidence", MmiProjectionResultCategory.PROJECTION_BLOCKED),
        (
            "evidence",
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        ("portfolio", MmiProjectionResultCategory.PROJECTION_BLOCKED),
        (
            "portfolio",
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
    ),
)
def test_upstream_blocked_and_contract_failure_remain_distinct(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
    component: str,
    status: MmiProjectionResultCategory,
) -> None:
    valid_result = MmiPolicyProjectionValidationResult(
        status=MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        authority_effect="NONE",
        reason_codes=(),
    )
    forced_result = MmiPolicyProjectionValidationResult(
        status=status,
        authority_effect="NONE",
        reason_codes=("UPSTREAM_PRIVATE_REASON",),
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_policy_projection",
        lambda *_args, **_kwargs: (
            forced_result if component == "policy" else valid_result
        ),
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view._evidence_bundle,
        "validate_mmi_authenticated_evidence_bundle",
        lambda *_args, **_kwargs: (
            forced_result if component == "evidence" else valid_result
        ),
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_portfolio_snapshot_projection",
        lambda *_args, **_kwargs: (
            forced_result if component == "portfolio" else valid_result
        ),
    )
    result = _build_view(trusted_inputs)
    expected_reason = (
        "MMI_ANALYST_VIEW_UPSTREAM_COMPONENT_BLOCKED"
        if status is MmiProjectionResultCategory.PROJECTION_BLOCKED
        else "MMI_ANALYST_VIEW_UPSTREAM_COMPONENT_CONTRACT_FAILURE"
    )
    _assert_blocked_without_view(
        result,
        status=status,
        reason=expected_reason,
    )
    assert "UPSTREAM_PRIVATE_REASON" not in result.reason_codes


def test_required_validation_order_precedes_fact_derivation(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    original_run = (
        analyst_visible_evidence_view
        ._mmi_projection_run_context_provenance_is_valid
    )
    original_source = (
        analyst_visible_evidence_view
        ._mmi_captured_source_provenance_is_valid
    )
    original_policy = (
        analyst_visible_evidence_view.validate_mmi_policy_projection
    )
    original_evidence = (
        analyst_visible_evidence_view._evidence_bundle
        .validate_mmi_authenticated_evidence_bundle
    )
    original_portfolio = (
        analyst_visible_evidence_view
        .validate_mmi_portfolio_snapshot_projection
    )
    original_derive = (
        analyst_visible_evidence_view._derive_expected_view
    )

    def run_wrapper(value: object) -> bool:
        events.append("run-provenance")
        return original_run(value)

    def source_wrapper(value: object) -> bool:
        role = getattr(value, "role", None)
        events.append(f"source:{getattr(role, 'value', role)}")
        return original_source(value)

    def policy_wrapper(*args: object, **kwargs: object):
        events.append("policy-validation")
        return original_policy(*args, **kwargs)

    def evidence_wrapper(*args: object, **kwargs: object):
        events.append("evidence-validation")
        return original_evidence(*args, **kwargs)

    def portfolio_wrapper(*args: object, **kwargs: object):
        events.append("portfolio-validation")
        return original_portfolio(*args, **kwargs)

    def derive_wrapper(*args: object, **kwargs: object):
        events.append("view-derivation")
        return original_derive(*args, **kwargs)

    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "_mmi_projection_run_context_provenance_is_valid",
        run_wrapper,
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "_mmi_captured_source_provenance_is_valid",
        source_wrapper,
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_policy_projection",
        policy_wrapper,
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view._evidence_bundle,
        "validate_mmi_authenticated_evidence_bundle",
        evidence_wrapper,
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_portfolio_snapshot_projection",
        portfolio_wrapper,
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "_derive_expected_view",
        derive_wrapper,
    )
    result = _build_view(trusted_inputs)
    assert result.valid, result.reason_codes
    assert events == [
        "run-provenance",
        "source:STRATEGY_SETTINGS",
        "policy-validation",
        "evidence-validation",
        "source:PORTFOLIO_SNAPSHOT",
        "portfolio-validation",
        "view-derivation",
    ]


def test_each_caller_mapping_is_snapshotted_once(
    trusted_inputs: _TrustedInputs,
) -> None:
    evidence = _OneSnapshotMapping(trusted_inputs.source_bound_bundle)
    policy = _OneSnapshotMapping(trusted_inputs.policy)
    portfolio = _OneSnapshotMapping(
        trusted_inputs.source_bound_portfolio
    )
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    assert result.valid, result.reason_codes
    evidence.assert_single_read()
    policy.assert_single_read()
    portfolio.assert_single_read()

    assert result.projection is not None
    candidate = _OneSnapshotMapping(result.projection)
    evidence = _OneSnapshotMapping(trusted_inputs.source_bound_bundle)
    policy = _OneSnapshotMapping(trusted_inputs.policy)
    portfolio = _OneSnapshotMapping(
        trusted_inputs.source_bound_portfolio
    )
    validation = validate_mmi_analyst_visible_evidence_view(
        value=candidate,
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    assert validation.valid, validation.reason_codes
    candidate.assert_single_read()
    evidence.assert_single_read()
    policy.assert_single_read()
    portfolio.assert_single_read()


@pytest.mark.parametrize(
    ("first_value", "second_value", "distinct_equal_key"),
    (
        ("same", "same", False),
        ("first", "second", False),
        (
            {"nested": {"ticker": "SPY"}},
            {"nested": {"ticker": "VOO"}},
            False,
        ),
        ("same", "same", True),
    ),
)
def test_snapshot_rejects_every_duplicate_key_before_second_lookup(
    first_value: object,
    second_value: object,
    distinct_equal_key: bool,
) -> None:
    hostile = _DuplicateKeyMapping(
        {"duplicate": "base"},
        duplicate_key="duplicate",
        first_value=first_value,
        second_value=second_value,
        distinct_equal_key=distinct_equal_key,
    )
    with pytest.raises(
        analyst_visible_evidence_view._ViewBlocked
    ) as exc_info:
        analyst_visible_evidence_view._snapshot_mapping(hostile)
    assert (
        exc_info.value.code
        == "MMI_ANALYST_VIEW_UPSTREAM_COMPONENT_BLOCKED"
    )
    hostile.assert_duplicate_rejected_before_second_lookup()


def test_snapshot_rejects_non_string_and_string_subclass_keys() -> None:
    class _StringSubclass(str):
        pass

    for invalid_key in (1, _StringSubclass("ticker")):
        with pytest.raises(
            analyst_visible_evidence_view._ViewBlocked
        ) as exc_info:
            analyst_visible_evidence_view._snapshot_mapping(
                {invalid_key: "VOO"}
            )
        assert (
            exc_info.value.code
            == "MMI_ANALYST_VIEW_UPSTREAM_COMPONENT_BLOCKED"
        )


def test_builder_rejects_duplicate_policy_key_without_second_lookup(
    trusted_inputs: _TrustedInputs,
) -> None:
    policy = deepcopy(trusted_inputs.policy)
    valid_universe = deepcopy(policy["universe_projection"])
    altered_universe = deepcopy(valid_universe)
    assert type(altered_universe) is dict
    core = altered_universe["core_universe"]
    assert type(core) is list
    assert core[1] == "VOO"
    core[1] = "SPY"
    hostile_policy = _DuplicateKeyMapping(
        policy,
        duplicate_key="universe_projection",
        first_value=altered_universe,
        second_value=valid_universe,
        distinct_equal_key=True,
    )
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=hostile_policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=deepcopy(
            trusted_inputs.source_bound_portfolio
        ),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
        reason="MMI_ANALYST_VIEW_UPSTREAM_COMPONENT_BLOCKED",
    )
    hostile_policy.assert_duplicate_rejected_before_second_lookup()
    diagnostic = repr(result)
    assert "universe_projection" not in diagnostic
    assert "SPY" not in diagnostic
    assert "VOO" not in diagnostic


def test_validator_rejects_duplicate_candidate_key_as_candidate_invalid(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_view(trusted_inputs)
    valid_policy_view = deepcopy(candidate["policy_view"])
    altered_policy_view = deepcopy(valid_policy_view)
    assert type(altered_policy_view) is dict
    instruments = altered_policy_view["analysis_instruments"]
    assert type(instruments) is list
    instrument = instruments[1]
    assert type(instrument) is dict
    instrument["ticker"] = "SPY"
    hostile_candidate = _DuplicateKeyMapping(
        candidate,
        duplicate_key="policy_view",
        first_value=altered_policy_view,
        second_value=valid_policy_view,
    )
    validation = validate_mmi_analyst_visible_evidence_view(
        value=hostile_candidate,
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=deepcopy(trusted_inputs.policy),
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=deepcopy(
            trusted_inputs.source_bound_portfolio
        ),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    _assert_blocked_without_view(
        validation,
        status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
        reason="MMI_ANALYST_VIEW_CANDIDATE_SCHEMA_INVALID",
    )
    hostile_candidate.assert_duplicate_rejected_before_second_lookup()
    diagnostic = repr(validation)
    assert "policy_view" not in diagnostic
    assert "SPY" not in diagnostic
    assert "VOO" not in diagnostic


def test_validator_rejects_duplicate_upstream_key_as_upstream_failure(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_view(trusted_inputs)
    policy = deepcopy(trusted_inputs.policy)
    valid_universe = deepcopy(policy["universe_projection"])
    altered_universe = deepcopy(valid_universe)
    assert type(altered_universe) is dict
    core = altered_universe["core_universe"]
    assert type(core) is list
    core[1] = "SPY"
    hostile_policy = _DuplicateKeyMapping(
        policy,
        duplicate_key="universe_projection",
        first_value=altered_universe,
        second_value=valid_universe,
    )
    validation = validate_mmi_analyst_visible_evidence_view(
        value=candidate,
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=hostile_policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=deepcopy(
            trusted_inputs.source_bound_portfolio
        ),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    _assert_blocked_without_view(
        validation,
        status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
        reason="MMI_ANALYST_VIEW_UPSTREAM_COMPONENT_BLOCKED",
    )
    hostile_policy.assert_duplicate_rejected_before_second_lookup()
    diagnostic = repr(validation)
    assert "universe_projection" not in diagnostic
    assert "SPY" not in diagnostic
    assert "VOO" not in diagnostic


@pytest.mark.parametrize("_iteration", range(10))
def test_nested_policy_mutation_after_validation_cannot_change_view(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
    _iteration: int,
) -> None:
    baseline = _valid_view(trusted_inputs)
    policy = deepcopy(trusted_inputs.policy)
    original_validator = (
        analyst_visible_evidence_view
        .validate_mmi_portfolio_snapshot_projection
    )
    mutation_count = 0

    def mutate_after_validation(*args: object, **kwargs: object):
        nonlocal mutation_count
        result = original_validator(*args, **kwargs)
        universe = policy["universe_projection"]
        assert type(universe) is dict
        core = universe["core_universe"]
        assert type(core) is list
        assert core[1] == "VOO"
        core[1] = "SPY"
        mutation_count += 1
        return result

    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_portfolio_snapshot_projection",
        mutate_after_validation,
    )
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=deepcopy(
            trusted_inputs.source_bound_portfolio
        ),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    assert mutation_count == 1
    assert result.valid, result.reason_codes
    assert result.projection is not None
    policy_view = result.projection["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    tickers = [item["ticker"] for item in instruments]
    assert "VOO" in tickers
    assert "SPY" not in tickers
    assert (
        result.projection[
            "analyst_visible_evidence_view_identity_sha256"
        ]
        == baseline["analyst_visible_evidence_view_identity_sha256"]
    )


def test_nested_evidence_mutation_after_validation_cannot_change_view(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _valid_view(trusted_inputs)
    bundle = deepcopy(trusted_inputs.source_bound_bundle)
    original_validator = (
        analyst_visible_evidence_view._evidence_bundle
        .validate_mmi_authenticated_evidence_bundle
    )
    mutation_count = 0

    def mutate_after_validation(*args: object, **kwargs: object):
        nonlocal mutation_count
        result = original_validator(*args, **kwargs)
        component = bundle["portfolio_component"]
        assert type(component) is dict
        component["presence_status"] = "NOT_SUPPLIED"
        mutation_count += 1
        return result

    monkeypatch.setattr(
        analyst_visible_evidence_view._evidence_bundle,
        "validate_mmi_authenticated_evidence_bundle",
        mutate_after_validation,
    )
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=bundle,
        policy_projection=deepcopy(trusted_inputs.policy),
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=deepcopy(
            trusted_inputs.source_bound_portfolio
        ),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    assert mutation_count == 1
    assert result.valid, result.reason_codes
    assert result.projection == baseline
    portfolio_view = result.projection["portfolio_view"]
    assert type(portfolio_view) is dict
    assert (
        portfolio_view["presence_status"]
        == "PRESENT_SOURCE_BOUND_VALIDATED"
    )


def test_nested_portfolio_mutation_after_validation_cannot_change_view(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _valid_view(trusted_inputs)
    portfolio = deepcopy(trusted_inputs.source_bound_portfolio)
    original_validator = (
        analyst_visible_evidence_view
        .validate_mmi_portfolio_snapshot_projection
    )
    mutation_count = 0

    def mutate_after_validation(*args: object, **kwargs: object):
        nonlocal mutation_count
        result = original_validator(*args, **kwargs)
        open_buy = portfolio["open_buy_orders"]
        gaps = portfolio["known_gaps"]
        assert type(open_buy) is dict
        assert type(gaps) is list
        records = open_buy["records"]
        assert type(records) is list
        outside_record = records[1]
        assert type(outside_record) is dict
        assert outside_record["ticker"] == "ARKK"
        outside_record["ticker"] = "XBI"
        outside_gap = next(
            gap
            for gap in gaps
            if type(gap) is dict
            and gap.get("code")
            == "PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE"
        )
        outside_gap["affected_tickers"] = ["XBI"]
        mutation_count += 1
        return result

    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_portfolio_snapshot_projection",
        mutate_after_validation,
    )
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=deepcopy(trusted_inputs.policy),
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    assert mutation_count == 1
    assert result.valid, result.reason_codes
    assert result.projection == baseline
    serialized = json.dumps(result.projection)
    assert "ARKK" in serialized
    assert "XBI" not in serialized


def test_valid_candidate_nested_mutation_after_snapshot_cannot_reject(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _valid_view(trusted_inputs)
    original_validator = (
        analyst_visible_evidence_view
        .validate_mmi_portfolio_snapshot_projection
    )

    def mutate_after_validation(*args: object, **kwargs: object):
        result = original_validator(*args, **kwargs)
        policy_view = candidate["policy_view"]
        assert type(policy_view) is dict
        instruments = policy_view["analysis_instruments"]
        assert type(instruments) is list
        instrument = instruments[1]
        assert type(instrument) is dict
        instrument["ticker"] = "SPY"
        return result

    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_portfolio_snapshot_projection",
        mutate_after_validation,
    )
    validation = _validate_view(candidate, trusted_inputs)
    assert validation.valid, validation.reason_codes
    policy_view = candidate["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    assert instruments[1] == {"ticker": "SPY", "policy_role": "CORE"}


def test_invalid_candidate_nested_mutation_to_valid_after_snapshot_rejects(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_view = _valid_view(trusted_inputs)
    candidate = deepcopy(valid_view)
    policy_view = candidate["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    instrument = instruments[1]
    assert type(instrument) is dict
    instrument["ticker"] = "SPY"
    original_validator = (
        analyst_visible_evidence_view
        .validate_mmi_portfolio_snapshot_projection
    )

    def mutate_after_validation(*args: object, **kwargs: object):
        result = original_validator(*args, **kwargs)
        instrument["ticker"] = "VOO"
        return result

    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_portfolio_snapshot_projection",
        mutate_after_validation,
    )
    validation = _validate_view(candidate, trusted_inputs)
    assert candidate == valid_view
    _assert_blocked_without_view(
        validation,
        status=MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        reason="MMI_ANALYST_VIEW_IDENTITY_INVALID",
    )


def test_nested_mapping_is_materialized_once_during_snapshot(
    trusted_inputs: _TrustedInputs,
) -> None:
    policy = deepcopy(trusted_inputs.policy)
    universe = policy["universe_projection"]
    assert type(universe) is dict
    nested = _OneSnapshotMapping(universe)
    policy["universe_projection"] = nested
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=deepcopy(
            trusted_inputs.source_bound_portfolio
        ),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    assert result.valid, result.reason_codes
    nested.assert_single_read()


@pytest.mark.parametrize(
    "unsupported",
    ("cycle", "set", "bytearray"),
)
def test_cycle_and_unsupported_nested_container_fail_closed(
    trusted_inputs: _TrustedInputs,
    unsupported: str,
) -> None:
    policy = deepcopy(trusted_inputs.policy)
    if unsupported == "cycle":
        policy["unsupported"] = policy
    elif unsupported == "bytearray":
        policy["unsupported"] = bytearray(b"SPY")
    else:
        policy["unsupported"] = {"SPY"}
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=deepcopy(
            trusted_inputs.source_bound_portfolio
        ),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
        reason="MMI_ANALYST_VIEW_UPSTREAM_COMPONENT_BLOCKED",
    )


@pytest.mark.parametrize(
    "unsupported",
    ("cycle", "set", "bytearray"),
)
def test_candidate_cycle_and_unsupported_container_are_schema_blocked(
    trusted_inputs: _TrustedInputs,
    unsupported: str,
) -> None:
    candidate = _valid_view(trusted_inputs)
    if unsupported == "cycle":
        candidate["unsupported"] = candidate
    elif unsupported == "bytearray":
        candidate["unsupported"] = bytearray(b"SPY")
    else:
        candidate["unsupported"] = {"SPY"}
    validation = _validate_view(candidate, trusted_inputs)
    _assert_blocked_without_view(
        validation,
        status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
        reason="MMI_ANALYST_VIEW_CANDIDATE_SCHEMA_INVALID",
    )


def test_builder_rejects_nested_empty_tuple_as_upstream_failure(
    trusted_inputs: _TrustedInputs,
) -> None:
    policy = deepcopy(trusted_inputs.policy)
    universe = policy["universe_projection"]
    assert type(universe) is dict
    universe["unsupported"] = ()
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=deepcopy(
            trusted_inputs.source_bound_portfolio
        ),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
        reason="MMI_ANALYST_VIEW_UPSTREAM_COMPONENT_BLOCKED",
    )
    assert "unsupported" not in repr(result)


def test_validator_rejects_nested_candidate_empty_tuple_as_invalid(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_view(trusted_inputs)
    policy_view = candidate["policy_view"]
    assert type(policy_view) is dict
    policy_view["unsupported"] = ()
    validation = _validate_view(candidate, trusted_inputs)
    _assert_blocked_without_view(
        validation,
        status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
        reason="MMI_ANALYST_VIEW_CANDIDATE_SCHEMA_INVALID",
    )
    assert "unsupported" not in repr(validation)


def test_validator_rejects_nested_upstream_empty_tuple_as_upstream_failure(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_view(trusted_inputs)
    policy = deepcopy(trusted_inputs.policy)
    universe = policy["universe_projection"]
    assert type(universe) is dict
    universe["unsupported"] = ()
    validation = _validate_view(
        candidate,
        trusted_inputs,
        policy_projection=policy,
    )
    _assert_blocked_without_view(
        validation,
        status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
        reason="MMI_ANALYST_VIEW_UPSTREAM_COMPONENT_BLOCKED",
    )
    assert "unsupported" not in repr(validation)


def test_snapshot_rejects_object_without_invoking_copy_hooks(
    trusted_inputs: _TrustedInputs,
) -> None:
    policy = deepcopy(trusted_inputs.policy)
    trap = _CopyHookTrap()
    policy["unsupported"] = trap
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=deepcopy(
            trusted_inputs.source_bound_portfolio
        ),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
        reason="MMI_ANALYST_VIEW_UPSTREAM_COMPONENT_BLOCKED",
    )
    assert trap.calls == []


def test_recursive_snapshot_detaches_containers_without_mutating_input() -> None:
    caller = {
        "mapping": {"list": [1, {"leaf": "value"}]},
        "tuple": ({"nested": [True, None]},),
    }
    before = deepcopy(caller)
    snapshot = analyst_visible_evidence_view._snapshot_mapping(caller)
    assert snapshot == caller == before
    assert not _container_ids(snapshot) & _container_ids(caller)

    caller_mapping = caller["mapping"]
    caller_tuple = caller["tuple"]
    assert type(caller_mapping) is dict
    assert type(caller_tuple) is tuple
    snapshot_tuple = snapshot["tuple"]
    assert type(snapshot_tuple) is tuple
    assert snapshot_tuple is not caller_tuple
    caller_mapping["list"].append("caller-only")  # type: ignore[union-attr]
    caller_tuple[0]["nested"].append(False)
    assert snapshot == before


def test_result_contains_no_caller_owned_container_alias(
    trusted_inputs: _TrustedInputs,
) -> None:
    policy = deepcopy(trusted_inputs.policy)
    bundle = deepcopy(trusted_inputs.source_bound_bundle)
    portfolio = deepcopy(trusted_inputs.source_bound_portfolio)
    caller_container_ids = (
        _container_ids(policy)
        | _container_ids(bundle)
        | _container_ids(portfolio)
    )
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=bundle,
        policy_projection=policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    assert not (
        _container_ids(result.projection) & caller_container_ids
    )


@pytest.mark.parametrize(
    "correlation",
    (
        "omitted-bundle-with-portfolio",
        "source-absent-bundle-with-source-bound-portfolio",
        "source-bound-bundle-with-source-absent-portfolio",
        "wrong-run-context",
        "wrong-evidence-policy-identity",
        "source-without-projection",
    ),
)
def test_component_correlations_fail_closed(
    trusted_inputs: _TrustedInputs,
    correlation: str,
) -> None:
    bundle = deepcopy(trusted_inputs.source_bound_bundle)
    portfolio: dict[str, object] | None = deepcopy(
        trusted_inputs.source_bound_portfolio
    )
    portfolio_source: MmiCapturedSource | None = (
        trusted_inputs.portfolio_source
    )
    run_context = trusted_inputs.run_context
    if correlation == "omitted-bundle-with-portfolio":
        bundle = deepcopy(trusted_inputs.omitted_bundle)
    elif correlation == "source-absent-bundle-with-source-bound-portfolio":
        bundle = deepcopy(trusted_inputs.source_absent_bundle)
    elif correlation == "source-bound-bundle-with-source-absent-portfolio":
        portfolio = deepcopy(trusted_inputs.source_absent_portfolio)
        portfolio_source = None
    elif correlation == "wrong-run-context":
        run_context = trusted_inputs.alternate_run_context
    elif correlation == "wrong-evidence-policy-identity":
        component = bundle["policy_component"]
        assert type(component) is dict
        component["policy_projection_identity_sha256"] = SHA_F
    else:
        portfolio = None

    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=bundle,
        policy_projection=deepcopy(trusted_inputs.policy),
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    assert result.status in {
        MmiProjectionResultCategory.PROJECTION_BLOCKED,
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
    }
    assert result.projection is None
    assert result.authority_effect == "NONE"


def test_unknown_gap_and_limitation_overflow_fail_without_truncation(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_result = MmiPolicyProjectionValidationResult(
        status=MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        authority_effect="NONE",
        reason_codes=(),
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_policy_projection",
        lambda *_args, **_kwargs: valid_result,
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view._evidence_bundle,
        "validate_mmi_authenticated_evidence_bundle",
        lambda *_args, **_kwargs: valid_result,
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_portfolio_snapshot_projection",
        lambda *_args, **_kwargs: valid_result,
    )

    policy = deepcopy(trusted_inputs.policy)
    gaps = policy["known_policy_gaps"]
    assert type(gaps) is list
    unknown = deepcopy(gaps[0])
    assert type(unknown) is dict
    unknown["code"] = "POLICY_NEW_UNREVIEWED_GAP"
    gaps.append(unknown)
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=deepcopy(
            trusted_inputs.source_bound_portfolio
        ),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        reason="MMI_ANALYST_VIEW_UNKNOWN_UPSTREAM_LIMITATION",
    )

    policy = deepcopy(trusted_inputs.policy)
    gaps = policy["known_policy_gaps"]
    assert type(gaps) is list
    existing = {
        item["code"]
        for item in gaps
        if type(item) is dict and type(item.get("code")) is str
    }
    for code in (
        "POLICY_EXTENDED_ACTIVATION_CONSTRAINTS_UNAVAILABLE",
        "POLICY_MAX_NEW_TICKER_RULE_UNAVAILABLE",
        "POLICY_PER_RUN_NEW_BUY_BUDGET_UNAVAILABLE",
    ):
        if code in existing:
            continue
        template = deepcopy(gaps[0])
        assert type(template) is dict
        template["code"] = code
        template["affected_tickers"] = []
        gaps.append(template)
    gaps.sort(key=lambda item: item["code"])  # type: ignore[index]
    portfolio = deepcopy(trusted_inputs.source_bound_portfolio)
    portfolio_gaps = portfolio["known_gaps"]
    assert type(portfolio_gaps) is list
    portfolio_order = (
        "PORTFOLIO_SOURCE_MISSING",
        "PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE",
        "PORTFOLIO_HOLDINGS_UNSTRUCTURED",
        "PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED",
        "PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE",
        "PORTFOLIO_OPEN_SELL_ORDERS_UNSTRUCTURED",
        "PORTFOLIO_TAX_LOTS_UNSTRUCTURED",
        "PORTFOLIO_DEPLOYABLE_CASH_UNAVAILABLE",
        "PORTFOLIO_WEIGHTS_UNAVAILABLE",
        "PORTFOLIO_NAV_CONCENTRATION_UNAVAILABLE",
        "PORTFOLIO_LOOKTHROUGH_EXPOSURE_UNAVAILABLE",
    )
    existing_portfolio_codes = {
        item["code"]
        for item in portfolio_gaps
        if type(item) is dict and type(item.get("code")) is str
    }
    for code in (
        "PORTFOLIO_SOURCE_MISSING",
        "PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE",
        "PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED",
    ):
        if code in existing_portfolio_codes:
            continue
        template = deepcopy(portfolio_gaps[0])
        assert type(template) is dict
        template["code"] = code
        template["affected_tickers"] = []
        portfolio_gaps.append(template)
    rank = {code: index for index, code in enumerate(portfolio_order)}
    portfolio_gaps.sort(
        key=lambda item: rank[item["code"]]  # type: ignore[index]
    )
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=policy,
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        reason="MMI_ANALYST_VIEW_COMPONENT_CORRELATION_INVALID",
    )
    assert len(gaps) == 11
    assert sum(
        1
        for item in portfolio_gaps
        if type(item) is dict
        and (
            "PORTFOLIO_PROJECTION",
            item.get("code"),
        )
        in analyst_visible_evidence_view._TRANSLATION_BY_UPSTREAM
    ) == 4


def test_conflicting_duplicate_classification_is_contract_failure(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_result = MmiPolicyProjectionValidationResult(
        status=MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        authority_effect="NONE",
        reason_codes=(),
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_policy_projection",
        lambda *_args, **_kwargs: valid_result,
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view._evidence_bundle,
        "validate_mmi_authenticated_evidence_bundle",
        lambda *_args, **_kwargs: valid_result,
    )
    monkeypatch.setattr(
        analyst_visible_evidence_view,
        "validate_mmi_portfolio_snapshot_projection",
        lambda *_args, **_kwargs: valid_result,
    )
    portfolio = deepcopy(trusted_inputs.source_bound_portfolio)
    open_buy = portfolio["open_buy_orders"]
    assert type(open_buy) is dict
    records = open_buy["records"]
    assert type(records) is list and records
    duplicate = deepcopy(records[0])
    assert type(duplicate) is dict
    duplicate["policy_membership_classification"] = "SATELLITE"
    records.append(duplicate)
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(trusted_inputs.source_bound_bundle),
        policy_projection=deepcopy(trusted_inputs.policy),
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        reason=(
            "MMI_ANALYST_VIEW_OBSERVATION_CLASSIFICATION_CONFLICT"
        ),
    )


def test_candidate_schema_identity_and_expected_equality_are_distinct(
    trusted_inputs: _TrustedInputs,
) -> None:
    view = _valid_view(trusted_inputs)

    schema_invalid = deepcopy(view)
    schema_invalid["unexpected"] = "closed"
    result = _validate_view(schema_invalid, trusted_inputs)
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
        reason="MMI_ANALYST_VIEW_CANDIDATE_SCHEMA_INVALID",
    )

    stale_identity = deepcopy(view)
    stale_identity[
        "analyst_visible_evidence_view_identity_sha256"
    ] = SHA_F
    result = _validate_view(stale_identity, trusted_inputs)
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        reason="MMI_ANALYST_VIEW_IDENTITY_INVALID",
    )

    resealed = deepcopy(view)
    resealed["evidence_bundle_identity_sha256"] = SHA_F
    _reseal_view(resealed)
    validate_artifact_schema(resealed, schema_name=VIEW_SCHEMA_NAME)
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(resealed)
        == resealed["analyst_visible_evidence_view_identity_sha256"]
        == _independent_view_identity(resealed)
    )
    result = _validate_view(resealed, trusted_inputs)
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        reason="MMI_ANALYST_VIEW_SOURCE_FIDELITY_MISMATCH",
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "evidence_identity",
        "evaluation_timestamp",
        "policy_date",
        "analysis_ticker",
        "benchmark_and_core_ticker",
        "portfolio_date",
        "observation_ticker_and_limitation",
        "observation_classification",
        "observation_order",
        "affected_visible_ticker",
        "remove_outside_limitation",
    ),
)
def test_structurally_valid_resealed_mutations_fail_source_equality(
    trusted_inputs: _TrustedInputs,
    mutation: str,
) -> None:
    candidate = _valid_view(trusted_inputs)
    policy = candidate["policy_view"]
    portfolio = candidate["portfolio_view"]
    limitations = candidate["known_view_limitations"]
    assert type(policy) is dict
    assert type(portfolio) is dict
    assert type(limitations) is list
    instruments = policy["analysis_instruments"]
    observations = portfolio["open_buy_observations"]
    assert type(instruments) is list
    assert type(observations) is list
    if mutation == "evidence_identity":
        candidate["evidence_bundle_identity_sha256"] = SHA_F
    elif mutation == "evaluation_timestamp":
        candidate["evaluation_timestamp_utc"] = (
            "2026-07-27T12:00:00.000001Z"
        )
    elif mutation == "policy_date":
        policy["policy_as_of_date"] = "2026-07-25"
    elif mutation == "analysis_ticker":
        assert type(instruments[1]) is dict
        instruments[1]["ticker"] = "ABC"
    elif mutation == "benchmark_and_core_ticker":
        assert type(instruments[0]) is dict
        instruments[0]["ticker"] = "SPY"
        policy["benchmark_reference_instruments"] = ["SPY"]
    elif mutation == "portfolio_date":
        portfolio["portfolio_source_date"] = "2026-07-25"
    elif mutation == "observation_ticker_and_limitation":
        assert type(observations[1]) is dict
        observations[1]["ticker"] = "XBI"
        outside = limitations[-1]
        assert type(outside) is dict
        outside["affected_tickers"] = ["XBI"]
    elif mutation == "observation_classification":
        assert type(observations[1]) is dict
        observations[1]["policy_membership_classification"] = "CORE"
    elif mutation == "observation_order":
        observations.reverse()
    elif mutation == "affected_visible_ticker":
        outside = limitations[-1]
        assert type(outside) is dict
        outside["affected_tickers"] = ["QQQ"]
    else:
        limitations.pop()
    _reseal_view(candidate)
    validate_artifact_schema(candidate, schema_name=VIEW_SCHEMA_NAME)
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(candidate)
        == candidate["analyst_visible_evidence_view_identity_sha256"]
        == _independent_view_identity(candidate)
    )
    result = _validate_view(candidate, trusted_inputs)
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        reason="MMI_ANALYST_VIEW_SOURCE_FIDELITY_MISMATCH",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("report_only", False),
        ("authority_effect", "READY"),
        ("view_completeness_status", "PROJECTION_VALID_COMPLETE"),
        ("schema_version", "mmi_analyst_visible_evidence_view_v2"),
        ("artifact_kind", "MMI_ANALYST_ACTION_VIEW"),
    ),
)
def test_fixed_contract_mutations_are_schema_blocked(
    trusted_inputs: _TrustedInputs,
    field: str,
    value: object,
) -> None:
    candidate = _valid_view(trusted_inputs)
    candidate[field] = value
    _reseal_view(candidate)
    result = _validate_view(candidate, trusted_inputs)
    _assert_blocked_without_view(
        result,
        status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
        reason="MMI_ANALYST_VIEW_CANDIDATE_SCHEMA_INVALID",
    )


def test_structural_identity_never_establishes_source_bound_authority(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_view(trusted_inputs)
    candidate["evidence_bundle_identity_sha256"] = SHA_F
    policy = candidate["policy_view"]
    assert type(policy) is dict
    policy["policy_as_of_date"] = "2026-07-25"
    _reseal_view(candidate)
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(candidate)
        == candidate["analyst_visible_evidence_view_identity_sha256"]
    )
    validation = _validate_view(candidate, trusted_inputs)
    assert not validation.valid
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )


def test_all_outputs_recursively_preserve_privacy(
    trusted_inputs: _TrustedInputs,
) -> None:
    outputs: list[object] = []
    for branch in (
        "NOT_SUPPLIED",
        "PRESENT_VALIDATED_SOURCE_ABSENT",
        "PRESENT_SOURCE_BOUND_VALIDATED",
        "PARSE_FAILED",
    ):
        result = _build_view(trusted_inputs, branch)
        outputs.extend(
            (
                result.reason_codes,
                result.projection,
                _validate_view(result.projection, trusted_inputs, branch),
            )
        )
    serialized = json.dumps(
        outputs,
        default=lambda value: {
            "status": getattr(value, "status", None).value,
            "authority_effect": getattr(
                value,
                "authority_effect",
                None,
            ),
            "reason_codes": getattr(value, "reason_codes", ()),
        },
    ).casefold()
    for forbidden in (
        "private_broker",
        "private_account",
        "reserved_budget",
        "notional",
        "quantity",
        "price",
        "order_id",
        "instruction",
        "theme_bucket",
        "quality_factor",
        "cybersecurity",
        "tax lot",
        "cost_basis",
        "raw_bytes",
        "source_record",
        "provenance",
        "prompt",
        "response",
        "provider",
        "persona",
    ):
        assert forbidden not in serialized
    prohibited_metadata_fields = {
        "model",
        "model_name",
        "provider",
        "provider_name",
        "prompt",
        "response",
        "token_budget",
    }

    def keys(value: object) -> set[str]:
        if type(value) is dict:
            return set(value) | {
                nested
                for item in value.values()
                for nested in keys(item)
            }
        if type(value) in {list, tuple}:
            return {
                nested
                for item in value
                for nested in keys(item)
            }
        return set()

    assert not keys(outputs) & prohibited_metadata_fields


def test_module_has_no_filesystem_capture_network_or_side_effect_surface() -> None:
    path = (
        repo_root()
        / "src/investment_orchestrator/mmi/"
        "analyst_visible_evidence_view.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
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
        "pathlib",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "openai",
        "anthropic",
    } & imported_modules
    for forbidden in (
        "capture_current_mmi_source",
        "_capture_mmi_source_at_root",
        "write_validated_json",
        "write_json",
        "Path(",
        "open(",
        "publish",
        "pointer",
        "prompt",
        "response",
        "broker",
        "order_compilation",
    ):
        assert forbidden not in source.casefold()


def test_no_source_capture_is_reachable_for_any_branch_or_failure(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from investment_orchestrator.mmi import source_capture

    def fail_capture(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("source capture became reachable")

    monkeypatch.setattr(
        source_capture,
        "capture_current_mmi_source",
        fail_capture,
    )
    monkeypatch.setattr(
        source_capture,
        "_capture_mmi_source_at_root",
        fail_capture,
    )
    for branch in (
        "NOT_SUPPLIED",
        "PRESENT_VALIDATED_SOURCE_ABSENT",
        "PRESENT_SOURCE_BOUND_VALIDATED",
        "PARSE_FAILED",
    ):
        assert _build_view(trusted_inputs, branch).valid
    invalid = build_mmi_analyst_visible_evidence_view(
        evidence_bundle={},
        policy_projection=deepcopy(trusted_inputs.policy),
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=trusted_inputs.run_context,
    )
    assert not invalid.valid


def test_view_module_has_exact_grounded_prompt_consumer() -> None:
    root = repo_root()
    production_paths = tuple(
        sorted((root / "src/investment_orchestrator").rglob("*.py"))
    )
    module_path = (
        root
        / "src/investment_orchestrator/mmi/"
        "analyst_visible_evidence_view.py"
    )
    module_name = (
        "investment_orchestrator.mmi.analyst_visible_evidence_view"
    )
    importers: list[str] = []
    for path in production_paths:
        if path == module_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported = None
            if isinstance(node, ast.Import):
                if any(
                    alias.name == module_name
                    or alias.name.startswith(f"{module_name}.")
                    for alias in node.names
                ):
                    imported = module_name
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and (
                    node.module == module_name
                    or node.module.startswith(f"{module_name}.")
                )
            ):
                imported = node.module
            if imported is not None:
                importers.append(path.relative_to(root).as_posix())
                break
    assert importers == [
        "src/investment_orchestrator/mmi/grounded_prompt.py",
    ]
    assert len(production_paths) == 141


def test_normal_build_and_validation_do_not_change_upstream_inputs(
    trusted_inputs: _TrustedInputs,
) -> None:
    policy = deepcopy(trusted_inputs.policy)
    bundle = deepcopy(trusted_inputs.source_bound_bundle)
    portfolio = deepcopy(trusted_inputs.source_bound_portfolio)
    before = deepcopy((policy, bundle, portfolio))
    result = build_mmi_analyst_visible_evidence_view(
        evidence_bundle=MappingProxyType(bundle),
        policy_projection=MappingProxyType(policy),
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=MappingProxyType(portfolio),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    assert result.valid, result.reason_codes
    assert (policy, bundle, portfolio) == before
    assert result.projection is not None
    validation = validate_mmi_analyst_visible_evidence_view(
        value=MappingProxyType(dict(result.projection)),
        evidence_bundle=MappingProxyType(bundle),
        policy_projection=MappingProxyType(policy),
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=MappingProxyType(portfolio),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    assert validation.valid, validation.reason_codes
    assert (policy, bundle, portfolio) == before


def test_reason_codes_are_closed_and_do_not_leak_values(
    trusted_inputs: _TrustedInputs,
) -> None:
    success = _build_view(trusted_inputs)
    assert success.reason_codes == (
        *POLICY_LIMITATION_CODES,
        "VIEW_PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE",
    )
    blocked = build_mmi_analyst_visible_evidence_view(
        evidence_bundle={},
        policy_projection=deepcopy(trusted_inputs.policy),
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=trusted_inputs.run_context,
    )
    assert blocked.projection is None
    assert all(
        type(code) is str
        and code.startswith("MMI_ANALYST_VIEW_")
        and "/" not in code
        and "\\" not in code
        for code in blocked.reason_codes
    )
