from __future__ import annotations

import ast
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
from investment_orchestrator.mmi import evidence_bundle
from investment_orchestrator.mmi.canonical import (
    MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiPolicyProjectionBuildResult,
    MmiPolicyProjectionValidationResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
    mmi_authenticated_evidence_bundle_identity_sha256,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
    validate_mmi_authenticated_evidence_bundle,
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


EVALUATION_TIME = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
SCHEMA_NAME = "mmi_authenticated_evidence_bundle_v1.schema.json"
SHA_F = "f" * 64
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


class _FixedClock:
    def __init__(self, observed: datetime = EVALUATION_TIME) -> None:
        self.observed = observed

    def now_utc(self) -> datetime:
        return self.observed


@dataclass(frozen=True, slots=True)
class _TrustedInputs:
    policy: dict[str, object]
    policy_source: MmiCapturedSource
    run_context: MmiProjectionRunContext
    source_absent_portfolio: dict[str, object]
    source_bound_portfolio: dict[str, object]
    portfolio_source: MmiCapturedSource
    alternate_policy: dict[str, object]
    alternate_policy_source: MmiCapturedSource
    alternate_policy_portfolio: dict[str, object]
    alternate_portfolio: dict[str, object]
    alternate_portfolio_source: MmiCapturedSource
    alternate_run_context: MmiProjectionRunContext


def _settings(*, benchmark: str = "QQQ") -> dict[str, object]:
    return {
        "as_of": "2026-07-24",
        "run_timestamp_et": "2026-07-24 10:00 ET",
        "benchmark": benchmark,
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


def _settings_bytes(*, benchmark: str = "QQQ") -> bytes:
    return yaml.safe_dump(
        _settings(benchmark=benchmark),
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")


def _portfolio_bytes(
    *,
    ticker: str = "QQQ",
    budget: str = "100.00",
) -> bytes:
    row = " | ".join(
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
    return (
        "\n".join(
            (
                "【Portfolio Snapshot】",
                "# updated 2026-07-24",
                "(1) current_holdings_base",
                "PRIVATE_BROKER | QQQ | 9 | 123.45",
                PORTFOLIO_SECTION_START,
                "- exact code-owned explanatory line",
                OPEN_BUY_HEADER,
                row,
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


def _build_policy(
    root: Path,
    *,
    run_context: MmiProjectionRunContext,
    benchmark: str = "QQQ",
) -> tuple[dict[str, object], MmiCapturedSource]:
    source = _capture(
        root,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=_settings_bytes(benchmark=benchmark),
    )
    result = build_mmi_policy_projection(
        source,
        run_context=run_context,
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    return dict(result.projection), source


def _build_portfolio(
    source: MmiCapturedSource | None,
    *,
    policy: dict[str, object],
    policy_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    result = build_mmi_portfolio_snapshot_projection(
        source,
        policy_projection=policy,
        policy_source=policy_source,
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
    policy, policy_source = _build_policy(
        tmp_path_factory.mktemp("e1c-policy"),
        run_context=run_context,
    )
    source_absent_portfolio = _build_portfolio(
        None,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    portfolio_source = _capture(
        tmp_path_factory.mktemp("e1c-portfolio"),
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        raw=_portfolio_bytes(),
    )
    source_bound_portfolio = _build_portfolio(
        portfolio_source,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )

    alternate_policy, alternate_policy_source = _build_policy(
        tmp_path_factory.mktemp("e1c-alternate-policy"),
        run_context=run_context,
        benchmark="VOO",
    )
    alternate_policy_portfolio = _build_portfolio(
        None,
        policy=alternate_policy,
        policy_source=alternate_policy_source,
        run_context=run_context,
    )
    alternate_portfolio_source = _capture(
        tmp_path_factory.mktemp("e1c-alternate-portfolio"),
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        raw=_portfolio_bytes(ticker="VOO", budget="200.00"),
    )
    alternate_portfolio = _build_portfolio(
        alternate_portfolio_source,
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    alternate_run_context = _begin_mmi_projection_run_with_clock(
        _FixedClock(EVALUATION_TIME + timedelta(hours=1))
    )
    return _TrustedInputs(
        policy=policy,
        policy_source=policy_source,
        run_context=run_context,
        source_absent_portfolio=source_absent_portfolio,
        source_bound_portfolio=source_bound_portfolio,
        portfolio_source=portfolio_source,
        alternate_policy=alternate_policy,
        alternate_policy_source=alternate_policy_source,
        alternate_policy_portfolio=alternate_policy_portfolio,
        alternate_portfolio=alternate_portfolio,
        alternate_portfolio_source=alternate_portfolio_source,
        alternate_run_context=alternate_run_context,
    )


def _build_bundle(
    inputs: _TrustedInputs,
    *,
    portfolio_projection: dict[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    policy_projection: dict[str, object] | None = None,
    policy_source: MmiCapturedSource | None = None,
    run_context: MmiProjectionRunContext | None = None,
) -> MmiPolicyProjectionBuildResult:
    return build_mmi_authenticated_evidence_bundle(
        policy_projection=(
            deepcopy(inputs.policy)
            if policy_projection is None
            else policy_projection
        ),
        policy_source=(
            inputs.policy_source
            if policy_source is None
            else policy_source
        ),
        portfolio_projection=(
            None
            if portfolio_projection is None
            else deepcopy(portfolio_projection)
        ),
        portfolio_source=portfolio_source,
        run_context=(
            inputs.run_context if run_context is None else run_context
        ),
    )


def _valid_bundle(
    inputs: _TrustedInputs,
    *,
    portfolio_projection: dict[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
) -> dict[str, object]:
    result = _build_bundle(
        inputs,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    return dict(result.projection)


def _validate_bundle(
    candidate: object,
    inputs: _TrustedInputs,
    *,
    portfolio_projection: dict[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
) -> MmiPolicyProjectionValidationResult:
    return validate_mmi_authenticated_evidence_bundle(
        candidate,  # type: ignore[arg-type]
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=(
            None
            if portfolio_projection is None
            else deepcopy(portfolio_projection)
        ),
        portfolio_source=portfolio_source,
        run_context=inputs.run_context,
    )


def _independent_bundle_identity(value: dict[str, object]) -> str:
    preimage = deepcopy(value)
    preimage.pop("evidence_bundle_identity_sha256", None)
    canonical = json.dumps(
        preimage,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(
        MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN
        + struct.pack(">Q", len(canonical))
        + canonical
    ).hexdigest()


def _reseal_bundle(value: dict[str, object]) -> None:
    value["evidence_bundle_identity_sha256"] = (
        _independent_bundle_identity(value)
    )


def _reseal_policy(value: dict[str, object]) -> None:
    universe = value["universe_projection"]
    assert type(universe) is dict
    universe_preimage = deepcopy(universe)
    universe_preimage.pop(
        "universe_projection_identity_sha256",
        None,
    )
    universe_bytes = json.dumps(
        universe_preimage,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    universe_identity = hashlib.sha256(
        b"mmi_universe_projection_v1\0"
        + struct.pack(">Q", len(universe_bytes))
        + universe_bytes
    ).hexdigest()
    universe["universe_projection_identity_sha256"] = universe_identity
    value["universe_projection_identity_sha256"] = universe_identity
    policy_preimage = deepcopy(value)
    policy_preimage.pop("policy_projection_identity_sha256", None)
    policy_bytes = json.dumps(
        policy_preimage,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    value["policy_projection_identity_sha256"] = hashlib.sha256(
        b"mmi_policy_projection_v1\0"
        + struct.pack(">Q", len(policy_bytes))
        + policy_bytes
    ).hexdigest()


def test_public_surfaces_are_exact_keyword_only_and_not_reexported() -> None:
    build_signature = inspect.signature(
        build_mmi_authenticated_evidence_bundle
    )
    validation_signature = inspect.signature(
        validate_mmi_authenticated_evidence_bundle
    )
    assert tuple(build_signature.parameters) == (
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    )
    assert tuple(validation_signature.parameters) == (
        "value",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in build_signature.parameters.values()
    )
    assert all(
        validation_signature.parameters[name].kind
        is inspect.Parameter.KEYWORD_ONLY
        for name in tuple(validation_signature.parameters)[1:]
    )
    assert evidence_bundle.__all__ == (
        "build_mmi_authenticated_evidence_bundle",
        "validate_mmi_authenticated_evidence_bundle",
    )
    with pytest.raises(TypeError):
        build_mmi_authenticated_evidence_bundle(  # type: ignore[call-arg]
            policy_projection={},
            policy_source=object(),
            run_context=object(),
        )
    with pytest.raises(TypeError):
        validate_mmi_authenticated_evidence_bundle({})  # type: ignore[call-arg]
    import investment_orchestrator.mmi as mmi

    assert mmi.__all__ == ()
    assert not hasattr(
        mmi,
        "build_mmi_authenticated_evidence_bundle",
    )


def test_portfolio_omission_builds_exact_gap_manifest_and_round_trips(
    trusted_inputs: _TrustedInputs,
) -> None:
    result = _build_bundle(
        trusted_inputs,
        portfolio_projection=None,
        portfolio_source=None,
    )
    assert type(result) is MmiPolicyProjectionBuildResult
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert result.authority_effect == "NONE"
    assert result.reason_codes == (
        "EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED",
    )
    assert result.projection is not None
    manifest = dict(result.projection)
    assert manifest["portfolio_component"] == {
        "presence_status": "NOT_SUPPLIED"
    }
    assert manifest["known_evidence_gaps"] == [
        {
            "code": "EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED",
            "scope": "EVIDENCE_ASSEMBLY",
            "component": "PORTFOLIO_PROJECTION",
        }
    ]
    assert set(manifest) == {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "evaluation_timestamp_utc",
        "policy_component",
        "portfolio_component",
        "known_evidence_gaps",
        "evidence_completeness_status",
        "evidence_bundle_identity_sha256",
    }
    validation = _validate_bundle(
        manifest,
        trusted_inputs,
        portfolio_projection=None,
        portfolio_source=None,
    )
    assert type(validation) is MmiPolicyProjectionValidationResult
    assert validation.valid
    assert validation.reason_codes == ()
    assert validation.authority_effect == "NONE"


def test_source_absent_portfolio_is_present_without_assembly_gap(
    trusted_inputs: _TrustedInputs,
) -> None:
    manifest = _valid_bundle(
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_absent_portfolio,
        portfolio_source=None,
    )
    component = manifest["portfolio_component"]
    assert type(component) is dict
    assert component == {
        "presence_status": "PRESENT_VALIDATED_SOURCE_ABSENT",
        "portfolio_schema_version": (
            "mmi_portfolio_snapshot_projection_v1"
        ),
        "portfolio_artifact_kind": (
            "MMI_PORTFOLIO_SNAPSHOT_PROJECTION"
        ),
        "portfolio_projection_identity_sha256": (
            trusted_inputs.source_absent_portfolio[
                "portfolio_projection_identity_sha256"
            ]
        ),
        "policy_projection_identity_sha256": (
            trusted_inputs.policy["policy_projection_identity_sha256"]
        ),
        "portfolio_source_status": "SOURCE_ABSENT",
        "validation_result_category": "PROJECTION_VALID_WITH_GAPS",
    }
    assert manifest["known_evidence_gaps"] == []
    assert "portfolio_source_record_identity_sha256" not in component
    assert _validate_bundle(
        manifest,
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_absent_portfolio,
        portfolio_source=None,
    ).valid


def test_source_bound_portfolio_uses_only_validated_component_identities(
    trusted_inputs: _TrustedInputs,
) -> None:
    manifest = _valid_bundle(
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    policy_component = manifest["policy_component"]
    portfolio_component = manifest["portfolio_component"]
    assert type(policy_component) is dict
    assert type(portfolio_component) is dict
    assert policy_component[
        "strategy_source_record_identity_sha256"
    ] == trusted_inputs.policy_source.source_record[
        "source_record_identity_sha256"
    ]
    assert policy_component[
        "universe_projection_identity_sha256"
    ] == trusted_inputs.policy["universe_projection_identity_sha256"]
    assert policy_component[
        "policy_projection_identity_sha256"
    ] == trusted_inputs.policy["policy_projection_identity_sha256"]
    assert portfolio_component["presence_status"] == (
        "PRESENT_SOURCE_BOUND_VALIDATED"
    )
    assert portfolio_component[
        "portfolio_source_record_identity_sha256"
    ] == trusted_inputs.portfolio_source.source_record[
        "source_record_identity_sha256"
    ]
    assert manifest["known_evidence_gaps"] == []
    validate_artifact_schema(manifest, schema_name=SCHEMA_NAME)
    assert (
        manifest["evidence_bundle_identity_sha256"]
        == _independent_bundle_identity(manifest)
        == mmi_authenticated_evidence_bundle_identity_sha256(
            manifest
        )
    )


def test_validation_order_is_provenance_then_components_then_derivation(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    original_run = (
        evidence_bundle._mmi_projection_run_context_provenance_is_valid
    )
    original_source = (
        evidence_bundle._mmi_captured_source_provenance_is_valid
    )
    original_policy = evidence_bundle.validate_mmi_policy_projection
    original_portfolio = (
        evidence_bundle.validate_mmi_portfolio_snapshot_projection
    )
    original_derive = evidence_bundle._derive_expected_manifest

    def run_wrapper(value: object) -> bool:
        events.append("run-provenance")
        return original_run(value)

    def source_wrapper(value: object) -> bool:
        role = getattr(value, "role", None)
        events.append(f"source-provenance:{getattr(role, 'value', role)}")
        return original_source(value)

    def policy_wrapper(*args: object, **kwargs: object):
        events.append("policy-validation")
        return original_policy(*args, **kwargs)

    def portfolio_wrapper(*args: object, **kwargs: object):
        events.append("portfolio-validation")
        return original_portfolio(*args, **kwargs)

    def derive_wrapper(*args: object, **kwargs: object):
        events.append("manifest-derivation")
        return original_derive(*args, **kwargs)

    monkeypatch.setattr(
        evidence_bundle,
        "_mmi_projection_run_context_provenance_is_valid",
        run_wrapper,
    )
    monkeypatch.setattr(
        evidence_bundle,
        "_mmi_captured_source_provenance_is_valid",
        source_wrapper,
    )
    monkeypatch.setattr(
        evidence_bundle,
        "validate_mmi_policy_projection",
        policy_wrapper,
    )
    monkeypatch.setattr(
        evidence_bundle,
        "validate_mmi_portfolio_snapshot_projection",
        portfolio_wrapper,
    )
    monkeypatch.setattr(
        evidence_bundle,
        "_derive_expected_manifest",
        derive_wrapper,
    )
    result = _build_bundle(
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    assert result.valid, result.reason_codes
    assert events == [
        "run-provenance",
        "source-provenance:STRATEGY_SETTINGS",
        "policy-validation",
        "portfolio-validation",
        "source-provenance:PORTFOLIO_SNAPSHOT",
        "manifest-derivation",
    ]


def test_mapping_inputs_are_snapshotted_then_source_bound_validated(
    trusted_inputs: _TrustedInputs,
) -> None:
    result = build_mmi_authenticated_evidence_bundle(
        policy_projection=MappingProxyType(deepcopy(trusted_inputs.policy)),
        policy_source=trusted_inputs.policy_source,
        portfolio_projection=MappingProxyType(
            deepcopy(trusted_inputs.source_bound_portfolio)
        ),
        portfolio_source=trusted_inputs.portfolio_source,
        run_context=trusted_inputs.run_context,
    )
    assert result.valid, result.reason_codes


def test_source_without_projection_is_blocked_with_no_bundle(
    trusted_inputs: _TrustedInputs,
) -> None:
    result = _build_bundle(
        trusted_inputs,
        portfolio_projection=None,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.authority_effect == "NONE"
    assert result.reason_codes == (
        "MMI_EVIDENCE_PORTFOLIO_PROJECTION_REQUIRED",
    )
    assert result.projection is None


def test_forged_policy_source_run_context_and_portfolio_source_fail_closed(
    trusted_inputs: _TrustedInputs,
) -> None:
    forged_policy_source = object.__new__(MmiCapturedSource)
    object.__setattr__(
        forged_policy_source,
        "role",
        MmiSourceRole.STRATEGY_SETTINGS,
    )
    object.__setattr__(forged_policy_source, "raw_bytes", b"forged")
    object.__setattr__(
        forged_policy_source,
        "source_record",
        MappingProxyType(dict(trusted_inputs.policy_source.source_record)),
    )

    forged_run_context = object.__new__(MmiProjectionRunContext)
    object.__setattr__(
        forged_run_context,
        "evaluation_time_utc",
        EVALUATION_TIME,
    )
    object.__setattr__(
        forged_run_context,
        "evaluation_timestamp_utc",
        EVALUATION_TIME.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )
    object.__setattr__(forged_run_context, "authority_effect", "NONE")

    forged_portfolio_source = object.__new__(MmiCapturedSource)
    object.__setattr__(
        forged_portfolio_source,
        "role",
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    )
    object.__setattr__(
        forged_portfolio_source,
        "raw_bytes",
        b"forged",
    )
    object.__setattr__(
        forged_portfolio_source,
        "source_record",
        MappingProxyType(
            dict(trusted_inputs.portfolio_source.source_record)
        ),
    )

    cases = (
        {
            "policy_source": forged_policy_source,
            "run_context": trusted_inputs.run_context,
            "portfolio_projection": None,
            "portfolio_source": None,
        },
        {
            "policy_source": trusted_inputs.policy_source,
            "run_context": forged_run_context,
            "portfolio_projection": None,
            "portfolio_source": None,
        },
        {
            "policy_source": trusted_inputs.policy_source,
            "run_context": trusted_inputs.run_context,
            "portfolio_projection": (
                trusted_inputs.source_bound_portfolio
            ),
            "portfolio_source": forged_portfolio_source,
        },
    )
    for case in cases:
        result = build_mmi_authenticated_evidence_bundle(
            policy_projection=deepcopy(trusted_inputs.policy),
            policy_source=case["policy_source"],  # type: ignore[arg-type]
            portfolio_projection=deepcopy(
                case["portfolio_projection"]
            ),
            portfolio_source=case["portfolio_source"],  # type: ignore[arg-type]
            run_context=case["run_context"],  # type: ignore[arg-type]
        )
        assert result.status is (
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
        )
        assert result.authority_effect == "NONE"
        assert result.projection is None
        assert result.reason_codes == (
            "MMI_EVIDENCE_COMPONENT_VALIDATION_CONTRACT_FAILURE",
        )


@pytest.mark.parametrize(
    ("component", "expected_status"),
    (
        ("policy-blocked", MmiProjectionResultCategory.PROJECTION_BLOCKED),
        (
            "policy-contract",
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        (
            "portfolio-blocked",
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
        ),
        (
            "portfolio-contract",
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
    ),
)
def test_component_failures_preserve_blocked_vs_contract_classification(
    trusted_inputs: _TrustedInputs,
    component: str,
    expected_status: MmiProjectionResultCategory,
) -> None:
    policy = deepcopy(trusted_inputs.policy)
    portfolio = deepcopy(trusted_inputs.source_bound_portfolio)
    if component == "policy-blocked":
        policy.pop("schema_version")
    elif component == "policy-contract":
        policy["policy_projection_identity_sha256"] = SHA_F
    elif component == "portfolio-blocked":
        portfolio.pop("schema_version")
    else:
        portfolio["portfolio_projection_identity_sha256"] = SHA_F

    result = _build_bundle(
        trusted_inputs,
        policy_projection=policy,
        portfolio_projection=portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    assert result.status is expected_status
    assert result.authority_effect == "NONE"
    assert result.projection is None
    assert result.reason_codes == (
        (
            "MMI_EVIDENCE_COMPONENT_VALIDATION_BLOCKED"
            if expected_status
            is MmiProjectionResultCategory.PROJECTION_BLOCKED
            else "MMI_EVIDENCE_COMPONENT_VALIDATION_CONTRACT_FAILURE"
        ),
    )


def test_candidate_schema_identity_and_expected_equality_are_distinct(
    trusted_inputs: _TrustedInputs,
) -> None:
    manifest = _valid_bundle(
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )

    schema_invalid = deepcopy(manifest)
    schema_invalid["unexpected"] = "closed"
    result = _validate_bundle(
        schema_invalid,
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (
        "MMI_EVIDENCE_CANDIDATE_BUNDLE_SCHEMA_INVALID",
    )

    stale_identity = deepcopy(manifest)
    stale_identity["evidence_bundle_identity_sha256"] = SHA_F
    result = _validate_bundle(
        stale_identity,
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.reason_codes == (
        "MMI_EVIDENCE_BUNDLE_IDENTITY_INVALID",
    )

    resealed = deepcopy(manifest)
    resealed["evaluation_timestamp_utc"] = (
        "2026-07-25T12:00:00.000001Z"
    )
    _reseal_bundle(resealed)
    validate_artifact_schema(resealed, schema_name=SCHEMA_NAME)
    assert (
        mmi_authenticated_evidence_bundle_identity_sha256(resealed)
        == resealed["evidence_bundle_identity_sha256"]
    )
    result = _validate_bundle(
        resealed,
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.reason_codes == (
        "MMI_EVIDENCE_BUNDLE_SOURCE_FIDELITY_MISMATCH",
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "evaluation_timestamp",
        "strategy_source_identity",
        "universe_identity",
        "policy_identity_and_reference",
        "portfolio_identity",
        "portfolio_source_identity",
        "source_absent_branch",
        "not_supplied_branch_and_gap",
    ),
)
def test_every_schema_valid_resealed_substitution_fails_source_binding(
    trusted_inputs: _TrustedInputs,
    mutation: str,
) -> None:
    candidate = _valid_bundle(
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    policy = candidate["policy_component"]
    portfolio = candidate["portfolio_component"]
    assert type(policy) is dict and type(portfolio) is dict
    if mutation == "evaluation_timestamp":
        candidate["evaluation_timestamp_utc"] = (
            "2026-07-25T12:00:00.000001Z"
        )
    elif mutation == "strategy_source_identity":
        policy["strategy_source_record_identity_sha256"] = SHA_F
    elif mutation == "universe_identity":
        policy["universe_projection_identity_sha256"] = SHA_F
    elif mutation == "policy_identity_and_reference":
        policy["policy_projection_identity_sha256"] = SHA_F
        portfolio["policy_projection_identity_sha256"] = SHA_F
    elif mutation == "portfolio_identity":
        portfolio["portfolio_projection_identity_sha256"] = SHA_F
    elif mutation == "portfolio_source_identity":
        portfolio["portfolio_source_record_identity_sha256"] = SHA_F
    elif mutation == "source_absent_branch":
        candidate["portfolio_component"] = {
            "presence_status": "PRESENT_VALIDATED_SOURCE_ABSENT",
            "portfolio_schema_version": (
                "mmi_portfolio_snapshot_projection_v1"
            ),
            "portfolio_artifact_kind": (
                "MMI_PORTFOLIO_SNAPSHOT_PROJECTION"
            ),
            "portfolio_projection_identity_sha256": (
                portfolio["portfolio_projection_identity_sha256"]
            ),
            "policy_projection_identity_sha256": (
                policy["policy_projection_identity_sha256"]
            ),
            "portfolio_source_status": "SOURCE_ABSENT",
            "validation_result_category": "PROJECTION_VALID_WITH_GAPS",
        }
    else:
        candidate["portfolio_component"] = {
            "presence_status": "NOT_SUPPLIED"
        }
        candidate["known_evidence_gaps"] = [
            {
                "code": "EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED",
                "scope": "EVIDENCE_ASSEMBLY",
                "component": "PORTFOLIO_PROJECTION",
            }
        ]
    _reseal_bundle(candidate)
    validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    assert (
        candidate["evidence_bundle_identity_sha256"]
        == _independent_bundle_identity(candidate)
        == mmi_authenticated_evidence_bundle_identity_sha256(
            candidate
        )
    )
    result = _validate_bundle(
        candidate,
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.reason_codes == (
        "MMI_EVIDENCE_BUNDLE_SOURCE_FIDELITY_MISMATCH",
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "policy_result",
        "portfolio_source_status",
        "portfolio_result",
        "completeness",
        "report_only",
        "authority",
        "gap_code",
    ),
)
def test_constant_or_gap_mutations_are_schema_blocked(
    trusted_inputs: _TrustedInputs,
    mutation: str,
) -> None:
    if mutation == "gap_code":
        candidate = _valid_bundle(
            trusted_inputs,
            portfolio_projection=None,
            portfolio_source=None,
        )
    else:
        candidate = _valid_bundle(
            trusted_inputs,
            portfolio_projection=trusted_inputs.source_bound_portfolio,
            portfolio_source=trusted_inputs.portfolio_source,
        )
    policy = candidate["policy_component"]
    portfolio = candidate["portfolio_component"]
    assert type(policy) is dict and type(portfolio) is dict
    if mutation == "policy_result":
        policy["validation_result_category"] = "PROJECTION_VALID_COMPLETE"
    elif mutation == "portfolio_source_status":
        portfolio["portfolio_source_status"] = "SOURCE_ABSENT"
    elif mutation == "portfolio_result":
        portfolio["validation_result_category"] = (
            "PROJECTION_VALID_COMPLETE"
        )
    elif mutation == "completeness":
        candidate["evidence_completeness_status"] = (
            "PROJECTION_VALID_COMPLETE"
        )
    elif mutation == "report_only":
        candidate["report_only"] = False
    elif mutation == "authority":
        candidate["authority_effect"] = "READY"
    else:
        gaps = candidate["known_evidence_gaps"]
        assert type(gaps) is list and type(gaps[0]) is dict
        gaps[0]["code"] = "PORTFOLIO_SOURCE_MISSING"
    _reseal_bundle(candidate)
    result = _validate_bundle(
        candidate,
        trusted_inputs,
        portfolio_projection=(
            None
            if mutation == "gap_code"
            else trusted_inputs.source_bound_portfolio
        ),
        portfolio_source=(
            None if mutation == "gap_code" else trusted_inputs.portfolio_source
        ),
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (
        "MMI_EVIDENCE_CANDIDATE_BUNDLE_SCHEMA_INVALID",
    )


def test_structural_identity_never_establishes_source_bound_validity(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_bundle(
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    policy = candidate["policy_component"]
    portfolio = candidate["portfolio_component"]
    assert type(policy) is dict and type(portfolio) is dict
    policy["strategy_source_record_identity_sha256"] = SHA_F
    policy["policy_projection_identity_sha256"] = SHA_F
    portfolio["policy_projection_identity_sha256"] = SHA_F
    _reseal_bundle(candidate)
    assert (
        mmi_authenticated_evidence_bundle_identity_sha256(candidate)
        == candidate["evidence_bundle_identity_sha256"]
    )
    validation = _validate_bundle(
        candidate,
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    assert not validation.valid
    assert validation.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )


def test_component_substitutions_all_fail_closed(
    trusted_inputs: _TrustedInputs,
) -> None:
    forged_policy = deepcopy(trusted_inputs.policy)
    universe = forged_policy["universe_projection"]
    assert type(universe) is dict
    core = universe["core_universe"]
    assert type(core) is list
    core.reverse()
    _reseal_policy(forged_policy)

    cases = (
        (
            "other-policy-projection",
            trusted_inputs.alternate_policy,
            trusted_inputs.policy_source,
            None,
            None,
            trusted_inputs.run_context,
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        (
            "wrong-policy-source",
            trusted_inputs.policy,
            trusted_inputs.alternate_policy_source,
            None,
            None,
            trusted_inputs.run_context,
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        (
            "wrong-policy-source-role",
            trusted_inputs.policy,
            trusted_inputs.portfolio_source,
            None,
            None,
            trusted_inputs.run_context,
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        (
            "resealed-policy",
            forged_policy,
            trusted_inputs.policy_source,
            None,
            None,
            trusted_inputs.run_context,
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        (
            "run-context-mismatch",
            trusted_inputs.policy,
            trusted_inputs.policy_source,
            None,
            None,
            trusted_inputs.alternate_run_context,
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        (
            "portfolio-from-other-policy",
            trusted_inputs.policy,
            trusted_inputs.policy_source,
            trusted_inputs.alternate_policy_portfolio,
            None,
            trusted_inputs.run_context,
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        (
            "portfolio-from-other-source",
            trusted_inputs.policy,
            trusted_inputs.policy_source,
            trusted_inputs.alternate_portfolio,
            trusted_inputs.portfolio_source,
            trusted_inputs.run_context,
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        (
            "source-present-without-source",
            trusted_inputs.policy,
            trusted_inputs.policy_source,
            trusted_inputs.source_bound_portfolio,
            None,
            trusted_inputs.run_context,
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        (
            "source-absent-with-source",
            trusted_inputs.policy,
            trusted_inputs.policy_source,
            trusted_inputs.source_absent_portfolio,
            trusted_inputs.portfolio_source,
            trusted_inputs.run_context,
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
        (
            "source-without-projection",
            trusted_inputs.policy,
            trusted_inputs.policy_source,
            None,
            trusted_inputs.portfolio_source,
            trusted_inputs.run_context,
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
        ),
    )
    for (
        name,
        policy,
        policy_source,
        portfolio,
        portfolio_source,
        run_context,
        expected_status,
    ) in cases:
        result = build_mmi_authenticated_evidence_bundle(
            policy_projection=deepcopy(policy),
            policy_source=policy_source,
            portfolio_projection=(
                None if portfolio is None else deepcopy(portfolio)
            ),
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
        assert result.status is expected_status, name
        assert result.authority_effect == "NONE", name
        assert result.projection is None, name


def test_builder_and_validator_use_one_shared_manifest_derivation(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = evidence_bundle._derive_expected_manifest

    def wrapper(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        evidence_bundle,
        "_derive_expected_manifest",
        wrapper,
    )
    result = _build_bundle(
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_absent_portfolio,
        portfolio_source=None,
    )
    assert result.valid
    assert result.projection is not None
    validation = _validate_bundle(
        result.projection,
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_absent_portfolio,
        portfolio_source=None,
    )
    assert validation.valid
    assert calls == 2


def test_internal_schema_identity_and_size_failures_are_contract_failures(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_schema(*_args: object, **_kwargs: object) -> None:
        raise ValueError("private schema detail")

    monkeypatch.setattr(
        evidence_bundle,
        "validate_artifact_schema",
        fail_schema,
    )
    result = _build_bundle(
        trusted_inputs,
        portfolio_projection=None,
        portfolio_source=None,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.reason_codes == (
        "MMI_EVIDENCE_DERIVED_BUNDLE_SCHEMA_INVALID",
    )
    assert result.projection is None


def test_identity_and_size_failures_do_not_emit_a_bundle(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_identity(_value: object) -> str:
        raise MmiCanonicalizationError("private identity detail")

    monkeypatch.setattr(
        evidence_bundle,
        "mmi_authenticated_evidence_bundle_identity_sha256",
        fail_identity,
    )
    result = _build_bundle(
        trusted_inputs,
        portfolio_projection=None,
        portfolio_source=None,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.reason_codes == (
        "MMI_EVIDENCE_BUNDLE_IDENTITY_INVALID",
    )
    assert result.projection is None


def test_no_source_capture_or_source_path_operation_occurs(
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
    for portfolio_projection, portfolio_source in (
        (None, None),
        (
            trusted_inputs.source_bound_portfolio,
            trusted_inputs.portfolio_source,
        ),
    ):
        result = _build_bundle(
            trusted_inputs,
            portfolio_projection=portfolio_projection,
            portfolio_source=portfolio_source,
        )
        assert result.valid, result.reason_codes

    source = Path(evidence_bundle.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not imported_roots & {
        "os",
        "pathlib",
        "stat",
        "subprocess",
        "tempfile",
    }
    assert not any(
        module.endswith(".source_capture") for module in imported_modules
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr
        in {
            "open",
            "read_bytes",
            "read_text",
            "write_bytes",
            "write_text",
        }
        for node in ast.walk(tree)
    )


def test_success_and_failure_outputs_preserve_privacy_boundary(
    trusted_inputs: _TrustedInputs,
) -> None:
    success = _build_bundle(
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    blocked = _build_bundle(
        trusted_inputs,
        portfolio_projection=None,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    serialized = json.dumps(
        {
            "success_projection": success.projection,
            "success_reasons": success.reason_codes,
            "blocked_projection": blocked.projection,
            "blocked_reasons": blocked.reason_codes,
        },
        sort_keys=True,
    ).casefold()
    for forbidden in (
        "private_broker",
        "private_account",
        "raw sell instruction",
        "private tax lot",
        "38211.29",
        "12000",
        "qqq",
        "voo",
        "holdings",
        "tax_lot",
        "quantity",
        "price",
        "inputs/current",
        "_provenance",
        "prompt",
        "response",
        "provider",
        "model",
        "permission",
        "gate",
        "readiness",
        "publication",
    ):
        assert forbidden not in serialized


def test_e1c_has_no_export_consumer_workflow_or_later_phase_surface() -> None:
    root = repo_root()
    module_path = (
        root / "src/investment_orchestrator/mmi/evidence_bundle.py"
    )
    production_sources = {
        path: path.read_text(encoding="utf-8")
        for path in (root / "src/investment_orchestrator").rglob("*.py")
    }
    for path, source in production_sources.items():
        if path == module_path:
            continue
        tree = ast.parse(source)
        assert not any(
            isinstance(node, ast.ImportFrom)
            and node.module
            == "investment_orchestrator.mmi.evidence_bundle"
            for node in ast.walk(tree)
        )
        assert not any(
            isinstance(node, ast.Import)
            and any(
                alias.name
                == "investment_orchestrator.mmi.evidence_bundle"
                for alias in node.names
            )
            for node in ast.walk(tree)
        )

    source = production_sources[module_path].casefold()
    for forbidden in (
        "analyst_view",
        "prompt",
        "response",
        "weekly",
        "publisher",
        "publication",
        "pointer",
        "broker",
        "provider",
        "model",
        "network",
        "poll",
        "retry",
        "schedule",
        "order_compilation",
        "new_buy",
        "no_trade",
    ):
        assert forbidden not in source


def test_result_gap_matrix_is_exact(
    trusted_inputs: _TrustedInputs,
) -> None:
    valid_cases = (
        (None, None, ("EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED",)),
        (trusted_inputs.source_absent_portfolio, None, ()),
        (
            trusted_inputs.source_bound_portfolio,
            trusted_inputs.portfolio_source,
            (),
        ),
    )
    for portfolio, source, expected_reasons in valid_cases:
        result = _build_bundle(
            trusted_inputs,
            portfolio_projection=portfolio,
            portfolio_source=source,
        )
        assert result.status is (
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
        )
        assert result.reason_codes == expected_reasons
        assert result.authority_effect == "NONE"
        assert result.projection is not None


def test_e1c_adds_no_identity_domain_and_preserves_report_only_authority(
    trusted_inputs: _TrustedInputs,
) -> None:
    from investment_orchestrator.mmi import canonical

    domains = {
        name: value
        for name, value in canonical.__dict__.items()
        if name.startswith("MMI_")
        and name.endswith("_IDENTITY_DOMAIN")
    }
    assert len(domains) == 5
    assert set(domains.values()) == {
        b"mmi_source_record_v1\0",
        b"mmi_universe_projection_v1\0",
        b"mmi_policy_projection_v1\0",
        b"mmi_portfolio_snapshot_projection_v1\0",
        b"mmi_authenticated_evidence_bundle_v1\0",
    }
    manifest = _valid_bundle(
        trusted_inputs,
        portfolio_projection=trusted_inputs.source_bound_portfolio,
        portfolio_source=trusted_inputs.portfolio_source,
    )
    assert manifest["report_only"] is True
    assert manifest["authority_effect"] == "NONE"
    assert manifest["evidence_completeness_status"] == (
        "PROJECTION_VALID_WITH_GAPS"
    )
