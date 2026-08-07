"""Source-bound construction and validation of the dormant V2 MMI view."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES,
    MmiCanonicalizationError,
    _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_IDENTITY_DOMAIN,
    canonical_json_bytes,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MMI_ANALYST_VIEW_LIMITATION_TRANSLATIONS,
    MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_ARTIFACT_KIND,
    MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS,
    MMI_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_STATUS,
    MMI_EVIDENCE_PORTFOLIO_SOURCE_BOUND_STATUS,
    MmiCapturedSource,
    MmiPolicyProjectionBuildResult,
    MmiPolicyProjectionValidationResult,
    MmiPortfolioProjectionValidationResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    MmiSourceRole,
    _mmi_captured_source_provenance_is_valid,
    _mmi_projection_run_context_provenance_is_valid,
)
from . import evidence_bundle as _evidence_bundle
from investment_orchestrator.mmi.policy_projection import (
    validate_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    validate_mmi_portfolio_snapshot_projection,
)


__all__ = (
    "build_mmi_analyst_visible_evidence_view_v2",
    "validate_mmi_analyst_visible_evidence_view_v2",
)

MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_SCHEMA_VERSION: Final = (
    "mmi_analyst_visible_evidence_view_v2"
)
MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_ARTIFACT_KIND: Final = (
    MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_ARTIFACT_KIND
)
MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_RESEARCH_COMPONENT_STATUSES: Final = (
    MappingProxyType(
        {
            "per_instrument_research": "LIMITED_TO_VISIBLE_EVIDENCE",
            "anchor_associations": "UNAVAILABLE",
            "scheduled_events": "UNAVAILABLE",
            "regime_inputs": "UNAVAILABLE",
        }
    )
)

_SCHEMA_NAME: Final = (
    "mmi_analyst_visible_evidence_view_v2.schema.json"
)
_ZERO_SHA256: Final = "0" * 64
_MAXIMUM_SOURCE_BOUND_LIMITATIONS: Final = 12
_IDENTITY_FIELD: Final = (
    "analyst_visible_evidence_view_identity_sha256"
)
_V2_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "evaluation_timestamp_utc",
        "evidence_bundle_identity_sha256",
        "policy_view",
        "portfolio_view",
        "research_component_statuses",
        "known_view_limitations",
        "view_completeness_status",
        _IDENTITY_FIELD,
    }
)

_UPSTREAM_COMPONENT_BLOCKED: Final = (
    "MMI_ANALYST_VIEW_V2_UPSTREAM_COMPONENT_BLOCKED"
)
_UPSTREAM_COMPONENT_CONTRACT_FAILURE: Final = (
    "MMI_ANALYST_VIEW_V2_UPSTREAM_COMPONENT_CONTRACT_FAILURE"
)
_COMPONENT_CORRELATION_INVALID: Final = (
    "MMI_ANALYST_VIEW_V2_COMPONENT_CORRELATION_INVALID"
)
_UNKNOWN_UPSTREAM_LIMITATION: Final = (
    "MMI_ANALYST_VIEW_V2_UNKNOWN_UPSTREAM_LIMITATION"
)
_CONFLICTING_OBSERVATION_CLASSIFICATION: Final = (
    "MMI_ANALYST_VIEW_V2_OBSERVATION_CLASSIFICATION_CONFLICT"
)
_DERIVED_SCHEMA_FAILURE: Final = (
    "MMI_ANALYST_VIEW_V2_DERIVED_SCHEMA_INVALID"
)
_CANDIDATE_SCHEMA_FAILURE: Final = (
    "MMI_ANALYST_VIEW_V2_CANDIDATE_SCHEMA_INVALID"
)
_VIEW_IDENTITY_FAILURE: Final = (
    "MMI_ANALYST_VIEW_V2_IDENTITY_INVALID"
)
_SOURCE_FIDELITY_MISMATCH: Final = (
    "MMI_ANALYST_VIEW_V2_SOURCE_FIDELITY_MISMATCH"
)
_INTERNAL_CONTRACT_FAILURE: Final = (
    "MMI_ANALYST_VIEW_V2_INTERNAL_CONTRACT_FAILURE"
)

_POLICY_OWNER: Final = "POLICY_PROJECTION"
_EVIDENCE_OWNER: Final = "EVIDENCE_BUNDLE"
_PORTFOLIO_OWNER: Final = "PORTFOLIO_PROJECTION"
_OUTSIDE_POLICY_UPSTREAM_CODE: Final = (
    "PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE"
)

_TRANSLATION_BY_UPSTREAM: Final = {
    (owner, upstream_code): (owner, output_code)
    for owner, upstream_code, output_code in (
        MMI_ANALYST_VIEW_LIMITATION_TRANSLATIONS
    )
}
_NON_VISIBLE_POLICY_GAP_CODES: Final = frozenset(
    {
        "EXTENDED_THEME_MAP_UNAVAILABLE",
        "EXTENDED_ETF_THEME_MAPPING_INCOMPLETE",
    }
)
_COVERAGE_OWNED_PORTFOLIO_GAP_CODES: Final = frozenset(
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
_COVERAGE_SOURCE_FIELDS: Final = (
    ("holdings", "holdings", "UNSTRUCTURED_NOT_PROJECTED"),
    ("cash", "cash", "UNAVAILABLE_NOT_PROJECTED"),
    (
        "deployable_cash",
        "deployable_cash",
        "UNAVAILABLE_NOT_PROJECTED",
    ),
    (
        "open_sells",
        "open_sell_orders",
        "UNSTRUCTURED_NOT_PROJECTED",
    ),
    ("tax_lots", "tax_lots", "UNSTRUCTURED_NOT_PROJECTED"),
    (
        "holding_dates",
        "holding_dates",
        "UNAVAILABLE_NOT_PROJECTED",
    ),
    (
        "gains_losses",
        "gains_losses",
        "UNAVAILABLE_NOT_PROJECTED",
    ),
    ("weights", "weights", "UNAVAILABLE_NOT_PROJECTED"),
    (
        "nav_concentration",
        "nav_concentration",
        "UNAVAILABLE_NOT_PROJECTED",
    ),
    (
        "look_through_exposure",
        "lookthrough_exposure",
        "UNAVAILABLE_NOT_PROJECTED",
    ),
)


class _ViewBlocked(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ViewContractFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _build_result(
    status: MmiProjectionResultCategory,
    *reason_codes: str,
    projection: Mapping[str, object] | None = None,
) -> MmiPolicyProjectionBuildResult:
    return MmiPolicyProjectionBuildResult(
        status=status,
        authority_effect=AUTHORITY_EFFECT_NONE,
        reason_codes=tuple(reason_codes),
        projection=projection,
    )


def _validation_result(
    status: MmiProjectionResultCategory,
    *reason_codes: str,
) -> MmiPolicyProjectionValidationResult:
    return MmiPolicyProjectionValidationResult(
        status=status,
        authority_effect=AUTHORITY_EFFECT_NONE,
        reason_codes=tuple(reason_codes),
    )


def _snapshot_value(
    value: object,
    *,
    active_container_ids: set[int],
) -> object:
    if value is None or type(value) in {bool, int, float, str}:
        return value

    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_container_ids:
            raise _ViewBlocked(_UPSTREAM_COMPONENT_BLOCKED)
        active_container_ids.add(container_id)
        try:
            snapshot: dict[str, object] = {}
            seen_keys: set[str] = set()
            try:
                iterator = iter(value)
                while True:
                    try:
                        key = next(iterator)
                    except StopIteration:
                        break
                    if type(key) is not str or key in seen_keys:
                        raise _ViewBlocked(
                            _UPSTREAM_COMPONENT_BLOCKED
                        )
                    seen_keys.add(key)
                    item = value[key]
                    snapshot[key] = _snapshot_value(
                        item,
                        active_container_ids=active_container_ids,
                    )
            except _ViewBlocked:
                raise
            except Exception:
                raise _ViewBlocked(
                    _UPSTREAM_COMPONENT_BLOCKED
                ) from None
            return snapshot
        finally:
            active_container_ids.remove(container_id)

    if type(value) is list:
        container_id = id(value)
        if container_id in active_container_ids:
            raise _ViewBlocked(_UPSTREAM_COMPONENT_BLOCKED)
        active_container_ids.add(container_id)
        try:
            try:
                materialized = list(value)
            except Exception:
                raise _ViewBlocked(
                    _UPSTREAM_COMPONENT_BLOCKED
                ) from None
            return [
                _snapshot_value(
                    item,
                    active_container_ids=active_container_ids,
                )
                for item in materialized
            ]
        finally:
            active_container_ids.remove(container_id)

    if type(value) is tuple:
        if len(value) == 0:
            raise _ViewBlocked(_UPSTREAM_COMPONENT_BLOCKED)
        container_id = id(value)
        if container_id in active_container_ids:
            raise _ViewBlocked(_UPSTREAM_COMPONENT_BLOCKED)
        active_container_ids.add(container_id)
        try:
            return tuple(
                _snapshot_value(
                    item,
                    active_container_ids=active_container_ids,
                )
                for item in value
            )
        finally:
            active_container_ids.remove(container_id)

    raise _ViewBlocked(_UPSTREAM_COMPONENT_BLOCKED)


def _snapshot_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _ViewBlocked(_UPSTREAM_COMPONENT_BLOCKED)
    snapshot = _snapshot_value(value, active_container_ids=set())
    if type(snapshot) is not dict:
        raise _ViewBlocked(_UPSTREAM_COMPONENT_BLOCKED)
    return snapshot


def _require_dict(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
    return value


def _require_list(value: object) -> list[object]:
    if type(value) is not list:
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
    return value


def _require_string(value: object) -> str:
    if type(value) is not str:
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
    return value


def _raise_for_upstream_result(
    result: (
        MmiPolicyProjectionValidationResult
        | MmiPortfolioProjectionValidationResult
    ),
) -> None:
    if result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED:
        raise _ViewBlocked(_UPSTREAM_COMPONENT_BLOCKED)
    if (
        result.status
        is MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    ):
        raise _ViewContractFailure(
            _UPSTREAM_COMPONENT_CONTRACT_FAILURE
        )
    if (
        result.status
        is not MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
        or result.authority_effect != AUTHORITY_EFFECT_NONE
    ):
        raise _ViewContractFailure(
            _UPSTREAM_COMPONENT_CONTRACT_FAILURE
        )


def _require_initial_trust(
    *,
    policy_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> None:
    if not _mmi_projection_run_context_provenance_is_valid(run_context):
        raise _ViewContractFailure(
            _UPSTREAM_COMPONENT_CONTRACT_FAILURE
        )
    if (
        not _mmi_captured_source_provenance_is_valid(policy_source)
        or policy_source.role is not MmiSourceRole.STRATEGY_SETTINGS
    ):
        raise _ViewContractFailure(
            _UPSTREAM_COMPONENT_CONTRACT_FAILURE
        )


def _validate_live_provenance_and_extract_identities(
    *,
    evidence_bundle: dict[str, object],
    policy_projection: dict[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: dict[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> tuple[str, str | None]:
    _require_initial_trust(
        policy_source=policy_source,
        run_context=run_context,
    )
    try:
        policy_result = validate_mmi_policy_projection(
            policy_projection,
            source=policy_source,
            run_context=run_context,
        )
    except Exception:
        raise _ViewContractFailure(
            _INTERNAL_CONTRACT_FAILURE
        ) from None
    _raise_for_upstream_result(policy_result)

    try:
        evidence_result = (
            _evidence_bundle.validate_mmi_authenticated_evidence_bundle(
                evidence_bundle,
                policy_projection=policy_projection,
                policy_source=policy_source,
                portfolio_projection=portfolio_projection,
                portfolio_source=portfolio_source,
                run_context=run_context,
            )
        )
    except Exception:
        raise _ViewContractFailure(
            _INTERNAL_CONTRACT_FAILURE
        ) from None
    _raise_for_upstream_result(evidence_result)

    if portfolio_source is not None:
        if (
            not _mmi_captured_source_provenance_is_valid(
                portfolio_source
            )
            or portfolio_source.role
            is not MmiSourceRole.PORTFOLIO_SNAPSHOT
        ):
            raise _ViewContractFailure(
                _UPSTREAM_COMPONENT_CONTRACT_FAILURE
            )
        try:
            portfolio_result = validate_mmi_portfolio_snapshot_projection(
                portfolio_projection,
                portfolio_source=portfolio_source,
                policy_projection=policy_projection,
                policy_source=policy_source,
                run_context=run_context,
            )
        except Exception:
            raise _ViewContractFailure(
                _INTERNAL_CONTRACT_FAILURE
            ) from None
        _raise_for_upstream_result(portfolio_result)

    policy_source_identity = _require_dict(
        dict(policy_source.source_record)
    ).get("source_record_identity_sha256")
    if type(policy_source_identity) is not str:
        raise _ViewContractFailure(_INTERNAL_CONTRACT_FAILURE)

    portfolio_source_identity: str | None = None
    if portfolio_source is not None:
        portfolio_source_identity_val = _require_dict(
            dict(portfolio_source.source_record)
        ).get("source_record_identity_sha256")
        if type(portfolio_source_identity_val) is not str:
            raise _ViewContractFailure(_INTERNAL_CONTRACT_FAILURE)
        portfolio_source_identity = portfolio_source_identity_val

    return policy_source_identity, portfolio_source_identity


def _validate_deterministic_source_bound_correlation(
    *,
    evidence_bundle: dict[str, object],
    policy_projection: dict[str, object],
    policy_source_record_identity_sha256: str,
    portfolio_projection: dict[str, object] | None,
    portfolio_source_record_identity_sha256: str | None,
    run_context: MmiProjectionRunContext,
) -> None:
    universe = _require_dict(
        policy_projection.get("universe_projection")
    )
    policy_component = _require_dict(
        evidence_bundle.get("policy_component")
    )
    portfolio_component = _require_dict(
        evidence_bundle.get("portfolio_component")
    )
    if (
        evidence_bundle.get("evaluation_timestamp_utc")
        != run_context.evaluation_timestamp_utc
        or policy_projection.get("evaluation_timestamp_utc")
        != run_context.evaluation_timestamp_utc
        or policy_component.get(
            "strategy_source_record_identity_sha256"
        )
        != policy_source_record_identity_sha256
        or policy_component.get(
            "universe_projection_identity_sha256"
        )
        != universe.get("universe_projection_identity_sha256")
        or policy_component.get("policy_projection_identity_sha256")
        != policy_projection.get("policy_projection_identity_sha256")
    ):
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)

    presence_status = portfolio_component.get("presence_status")
    if portfolio_projection is None:
        if (
            portfolio_source_record_identity_sha256 is not None
            or presence_status
            != MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS
        ):
            raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
        return

    if (
        portfolio_projection.get("evaluation_timestamp_utc")
        != run_context.evaluation_timestamp_utc
        or portfolio_projection.get(
            "policy_projection_identity_sha256"
        )
        != policy_projection.get("policy_projection_identity_sha256")
        or portfolio_component.get(
            "portfolio_projection_identity_sha256"
        )
        != portfolio_projection.get(
            "portfolio_projection_identity_sha256"
        )
    ):
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)

    if portfolio_source_record_identity_sha256 is None:
        if (
            presence_status
            != MMI_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_STATUS
            or portfolio_projection.get("portfolio_source_status")
            != "SOURCE_ABSENT"
            or portfolio_projection.get(
                "portfolio_source_record_identity_sha256"
            )
            is not None
        ):
            raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
    else:
        source_identity = portfolio_source_record_identity_sha256
        if (
            presence_status
            != MMI_EVIDENCE_PORTFOLIO_SOURCE_BOUND_STATUS
            or portfolio_projection.get("portfolio_source_status")
            != "SOURCE_PRESENT_CONTENT_BOUND"
            or portfolio_projection.get(
                "portfolio_source_record_identity_sha256"
            )
            != source_identity
            or portfolio_component.get(
                "portfolio_source_record_identity_sha256"
            )
            != source_identity
        ):
            raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)


def _derive_policy_view(
    policy_projection: dict[str, object],
) -> tuple[dict[str, object], tuple[str, ...]]:
    universe = _require_dict(
        policy_projection.get("universe_projection")
    )
    role_groups = (
        ("core_universe", "CORE"),
        ("satellite_universe", "SATELLITE"),
        ("approved_extended_universe", "APPROVED_EXTENDED"),
    )
    analysis_instruments: list[dict[str, object]] = []
    ordered_tickers: list[str] = []
    for field, role in role_groups:
        for ticker_value in _require_list(universe.get(field)):
            ticker = _require_string(ticker_value)
            if ticker in ordered_tickers:
                raise _ViewContractFailure(
                    _COMPONENT_CORRELATION_INVALID
                )
            ordered_tickers.append(ticker)
            analysis_instruments.append(
                {
                    "ticker": ticker,
                    "policy_role": role,
                }
            )

    benchmark_values = _require_list(
        universe.get("benchmark_reference_instruments")
    )
    if len(benchmark_values) != 1:
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
    benchmark = _require_string(benchmark_values[0])
    role_by_ticker = _require_dict(universe.get("role_by_ticker"))
    if (
        role_by_ticker.get(benchmark) != "CORE"
        or benchmark not in ordered_tickers
    ):
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)

    return (
        {
            "policy_as_of_date": _require_string(
                policy_projection.get("policy_as_of_date")
            ),
            "policy_method": _require_string(
                policy_projection.get("policy_method")
            ),
            "benchmark_reference_instruments": [benchmark],
            "analysis_instruments": analysis_instruments,
            "extended_activation_status": _require_string(
                universe.get("extended_activation_status")
            ),
            "instrument_availability_observation_status": (
                _require_string(
                    universe.get(
                        "instrument_availability_observation_status"
                    )
                )
            ),
            "target_weights_absence_reason": _require_string(
                policy_projection.get(
                    "target_weights_absence_reason"
                )
            ),
        },
        tuple(ordered_tickers),
    )


def _derive_coverage(
    portfolio_projection: dict[str, object],
) -> dict[str, object]:
    coverage: dict[str, object] = {}
    for output_field, source_field, expected_status in (
        _COVERAGE_SOURCE_FIELDS
    ):
        source_area = _require_dict(
            portfolio_projection.get(source_field)
        )
        status = source_area.get("status")
        if status != expected_status:
            raise _ViewContractFailure(
                _COMPONENT_CORRELATION_INVALID
            )
        coverage[output_field] = status
    return coverage


def _derive_observations(
    portfolio_projection: dict[str, object],
) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    open_buy = _require_dict(
        portfolio_projection.get("open_buy_orders")
    )
    records = _require_list(open_buy.get("records"))
    classification_by_ticker: dict[str, str] = {}
    observations: list[dict[str, object]] = []
    outside_tickers: list[str] = []
    for record_value in records:
        record = _require_dict(record_value)
        ticker = _require_string(record.get("ticker"))
        classification = _require_string(
            record.get("policy_membership_classification")
        )
        previous = classification_by_ticker.get(ticker)
        if previous is not None:
            if previous != classification:
                raise _ViewContractFailure(
                    _CONFLICTING_OBSERVATION_CLASSIFICATION
                )
            continue
        classification_by_ticker[ticker] = classification
        observations.append(
            {
                "ticker": ticker,
                "policy_membership_classification": classification,
            }
        )
        if classification == "OUTSIDE_POLICY_UNIVERSE":
            outside_tickers.append(ticker)
    if len(observations) > 256:
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
    return observations, tuple(outside_tickers)


def _derive_portfolio_view(
    *,
    evidence_bundle: dict[str, object],
    portfolio_projection: dict[str, object] | None,
) -> tuple[dict[str, object], tuple[str, ...]]:
    component = _require_dict(
        evidence_bundle.get("portfolio_component")
    )
    presence_status = component.get("presence_status")
    if presence_status == MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS:
        if portfolio_projection is not None:
            raise _ViewContractFailure(
                _COMPONENT_CORRELATION_INVALID
            )
        return {"presence_status": presence_status}, ()

    if portfolio_projection is None:
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
    coverage = _derive_coverage(portfolio_projection)
    open_buy = _require_dict(
        portfolio_projection.get("open_buy_orders")
    )
    open_buy_status = _require_string(open_buy.get("status"))
    if (
        presence_status
        == MMI_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_STATUS
    ):
        if (
            portfolio_projection.get("portfolio_source_date") is not None
            or open_buy_status != "SOURCE_ABSENT"
            or open_buy.get("records") != []
        ):
            raise _ViewContractFailure(
                _COMPONENT_CORRELATION_INVALID
            )
        return (
            {
                "presence_status": presence_status,
                "portfolio_source_date": None,
                "open_buy_status": "SOURCE_ABSENT",
                "open_buy_observations": [],
                "fact_coverage_statuses": coverage,
            },
            (),
        )
    if presence_status != MMI_EVIDENCE_PORTFOLIO_SOURCE_BOUND_STATUS:
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
    observations, outside_tickers = _derive_observations(
        portfolio_projection
    )
    if open_buy_status == "PARSE_FAILED" and observations:
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
    if open_buy_status not in {"SOURCE_VALIDATED", "PARSE_FAILED"}:
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
    source_date = portfolio_projection.get("portfolio_source_date")
    if source_date is not None:
        _require_string(source_date)
    return (
        {
            "presence_status": presence_status,
            "portfolio_source_date": source_date,
            "open_buy_status": open_buy_status,
            "open_buy_observations": observations,
            "fact_coverage_statuses": coverage,
        },
        outside_tickers,
    )


def _gap_code(value: object) -> tuple[str, list[object]]:
    gap = _require_dict(value)
    code = _require_string(gap.get("code"))
    affected = _require_list(gap.get("affected_tickers"))
    return code, affected


def _translate_limitations(
    *,
    policy_projection: dict[str, object],
    evidence_bundle: dict[str, object],
    portfolio_projection: dict[str, object] | None,
    outside_tickers: tuple[str, ...],
) -> list[dict[str, object]]:
    translated: list[dict[str, object]] = []
    observed_outputs: set[str] = set()

    def append_translated(
        owner: str,
        upstream_code: str,
        *,
        affected_tickers: list[str],
    ) -> None:
        translation = _TRANSLATION_BY_UPSTREAM.get(
            (owner, upstream_code)
        )
        if translation is None:
            raise _ViewContractFailure(_UNKNOWN_UPSTREAM_LIMITATION)
        output_owner, output_code = translation
        if output_code in observed_outputs:
            raise _ViewContractFailure(
                _COMPONENT_CORRELATION_INVALID
            )
        observed_outputs.add(output_code)
        translated.append(
            {
                "owner": output_owner,
                "code": output_code,
                "affected_tickers": affected_tickers,
            }
        )

    for gap_value in _require_list(
        policy_projection.get("known_policy_gaps")
    ):
        code, affected = _gap_code(gap_value)
        if code in _NON_VISIBLE_POLICY_GAP_CODES:
            continue
        if affected:
            raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
        append_translated(_POLICY_OWNER, code, affected_tickers=[])

    for gap_value in _require_list(
        evidence_bundle.get("known_evidence_gaps")
    ):
        gap = _require_dict(gap_value)
        code = _require_string(gap.get("code"))
        append_translated(_EVIDENCE_OWNER, code, affected_tickers=[])

    if portfolio_projection is not None:
        for gap_value in _require_list(
            portfolio_projection.get("known_gaps")
        ):
            code, affected = _gap_code(gap_value)
            if code in _COVERAGE_OWNED_PORTFOLIO_GAP_CODES:
                if affected:
                    raise _ViewContractFailure(
                        _COMPONENT_CORRELATION_INVALID
                    )
                continue
            if code == _OUTSIDE_POLICY_UPSTREAM_CODE:
                checked_affected = [
                    _require_string(ticker) for ticker in affected
                ]
                if (
                    not checked_affected
                    or tuple(checked_affected) != outside_tickers
                ):
                    raise _ViewContractFailure(
                        _COMPONENT_CORRELATION_INVALID
                    )
                append_translated(
                    _PORTFOLIO_OWNER,
                    code,
                    affected_tickers=checked_affected,
                )
            else:
                if affected:
                    raise _ViewContractFailure(
                        _COMPONENT_CORRELATION_INVALID
                    )
                append_translated(
                    _PORTFOLIO_OWNER,
                    code,
                    affected_tickers=[],
                )

    if (
        not translated
        or len(translated) > _MAXIMUM_SOURCE_BOUND_LIMITATIONS
    ):
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)
    return translated


def _v2_identity_sha256(view: dict[str, object]) -> str:
    if (
        set(view) != _V2_TOP_LEVEL_FIELDS
        or view.get("schema_version")
        != MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_SCHEMA_VERSION
        or view.get("artifact_kind")
        != MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_ARTIFACT_KIND
        or view.get("report_only") is not True
        or view.get("authority_effect") != AUTHORITY_EFFECT_NONE
        or view.get("research_component_statuses")
        != dict(
            MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_RESEARCH_COMPONENT_STATUSES
        )
    ):
        raise MmiCanonicalizationError(
            "MMI_ANALYST_VIEW_V2_CONTRACT_INVALID"
        )
    canonical_json_bytes(
        view,
        maximum_bytes=(
            MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES
        ),
    )
    return record_identity_sha256(
        view,
        identity_field=_IDENTITY_FIELD,
        domain=(
            _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_IDENTITY_DOMAIN
        ),
        maximum_bytes=(
            MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES
        ),
    )


def _derive_expected_view(
    *,
    evidence_bundle: dict[str, object],
    policy_projection: dict[str, object],
    portfolio_projection: dict[str, object] | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    policy_view, policy_tickers = _derive_policy_view(
        policy_projection
    )
    portfolio_view, outside_tickers = _derive_portfolio_view(
        evidence_bundle=evidence_bundle,
        portfolio_projection=portfolio_projection,
    )
    limitations = _translate_limitations(
        policy_projection=policy_projection,
        evidence_bundle=evidence_bundle,
        portfolio_projection=portfolio_projection,
        outside_tickers=outside_tickers,
    )
    visible_tickers = set(policy_tickers)
    observations = portfolio_view.get("open_buy_observations", [])
    if type(observations) is list:
        visible_tickers.update(
            item["ticker"]
            for item in observations
            if type(item) is dict and type(item.get("ticker")) is str
        )
    if any(
        not set(limitation["affected_tickers"]) <= visible_tickers
        for limitation in limitations
    ):
        raise _ViewContractFailure(_COMPONENT_CORRELATION_INVALID)

    view: dict[str, object] = {
        "schema_version": (
            MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_SCHEMA_VERSION
        ),
        "artifact_kind": (
            MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_ARTIFACT_KIND
        ),
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "evaluation_timestamp_utc": (
            run_context.evaluation_timestamp_utc
        ),
        "evidence_bundle_identity_sha256": _require_string(
            evidence_bundle.get("evidence_bundle_identity_sha256")
        ),
        "policy_view": policy_view,
        "portfolio_view": portfolio_view,
        "research_component_statuses": dict(
            MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_RESEARCH_COMPONENT_STATUSES
        ),
        "known_view_limitations": limitations,
        "view_completeness_status": (
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS.value
        ),
        "analyst_visible_evidence_view_identity_sha256": _ZERO_SHA256,
    }
    try:
        view["analyst_visible_evidence_view_identity_sha256"] = (
            _v2_identity_sha256(view)
        )
    except MmiCanonicalizationError:
        raise _ViewContractFailure(_VIEW_IDENTITY_FAILURE) from None
    return view


def _validate_derived_view(view: dict[str, object]) -> None:
    try:
        validate_artifact_schema(view, schema_name=_SCHEMA_NAME)
    except Exception:
        raise _ViewContractFailure(_DERIVED_SCHEMA_FAILURE) from None
    try:
        expected_identity = _v2_identity_sha256(view)
    except MmiCanonicalizationError:
        raise _ViewContractFailure(_VIEW_IDENTITY_FAILURE) from None
    if (
        view.get("analyst_visible_evidence_view_identity_sha256")
        != expected_identity
    ):
        raise _ViewContractFailure(_VIEW_IDENTITY_FAILURE)





def _validated_analyst_visible_evidence_view_v2_context(
    value: object,
    *,
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    evidence = _snapshot_mapping(evidence_bundle)
    policy = _snapshot_mapping(policy_projection)
    portfolio = (
        None
        if portfolio_projection is None
        else _snapshot_mapping(portfolio_projection)
    )
    policy_id, portfolio_id = _validate_live_provenance_and_extract_identities(
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=policy_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    return _validated_analyst_visible_evidence_view_v2_context_from_source_record_identities(
        value,
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source_record_identity_sha256=policy_id,
        portfolio_projection=portfolio,
        portfolio_source_record_identity_sha256=portfolio_id,
        run_context=run_context,
    )


def _validated_analyst_visible_evidence_view_v2_context_from_source_record_identities(
    value: object,
    *,
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source_record_identity_sha256: str,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source_record_identity_sha256: str | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    artifact = _snapshot_mapping(value)
    _validate_schema_and_contract(artifact)

    evidence = _snapshot_mapping(evidence_bundle)
    policy = _snapshot_mapping(policy_projection)
    portfolio = (
        None
        if portfolio_projection is None
        else _snapshot_mapping(portfolio_projection)
    )

    _validate_deterministic_source_bound_correlation(
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source_record_identity_sha256=policy_source_record_identity_sha256,
        portfolio_projection=portfolio,
        portfolio_source_record_identity_sha256=portfolio_source_record_identity_sha256,
        run_context=run_context,
    )

    return _verify_view_identity(
        artifact=artifact,
        evidence_bundle=evidence,
        policy_projection=policy,
        portfolio_projection=portfolio,
        run_context=run_context,
    )


def _validate_schema_and_contract(artifact: dict[str, object]) -> None:
    try:
        validate_artifact_schema(artifact, schema_name=_SCHEMA_NAME)
    except Exception:
        raise _ViewBlocked(_CANDIDATE_SCHEMA_FAILURE) from None



def _verify_view_identity(
    *,
    artifact: dict[str, object],
    evidence_bundle: dict[str, object],
    policy_projection: dict[str, object],
    portfolio_projection: dict[str, object] | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:

    expected_view = _derive_expected_view(
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        portfolio_projection=portfolio_projection,
        run_context=run_context,
    )

    if artifact != expected_view:
        raise _ViewContractFailure(_SOURCE_FIDELITY_MISMATCH)

    try:
        expected_identity = _v2_identity_sha256(artifact)
    except MmiCanonicalizationError:
        raise _ViewContractFailure(_VIEW_IDENTITY_FAILURE) from None

    if artifact.get(_IDENTITY_FIELD) != expected_identity:
        raise _ViewContractFailure(_VIEW_IDENTITY_FAILURE)

    return artifact


def build_mmi_analyst_visible_evidence_view_v2(
    *,
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionBuildResult:
    """Build one dormant, source-bound V2 analyst-visible view."""
    try:
        evidence_snapshot = _snapshot_mapping(evidence_bundle)
        policy_snapshot = _snapshot_mapping(policy_projection)
        portfolio_snapshot = (
            None
            if portfolio_projection is None
            else _snapshot_mapping(portfolio_projection)
        )
        policy_id, portfolio_id = _validate_live_provenance_and_extract_identities(
            evidence_bundle=evidence_snapshot,
            policy_projection=policy_snapshot,
            policy_source=policy_source,
            portfolio_projection=portfolio_snapshot,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except _ViewBlocked as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _ViewContractFailure as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _INTERNAL_CONTRACT_FAILURE,
        )

    return _build_mmi_analyst_visible_evidence_view_v2_from_source_record_identities(
        evidence_bundle=evidence_snapshot,
        policy_projection=policy_snapshot,
        policy_source_record_identity_sha256=policy_id,
        portfolio_projection=portfolio_snapshot,
        portfolio_source_record_identity_sha256=portfolio_id,
        run_context=run_context,
    )


def _build_mmi_analyst_visible_evidence_view_v2_from_source_record_identities(
    *,
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source_record_identity_sha256: str,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source_record_identity_sha256: str | None,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionBuildResult:
    try:
        evidence_snapshot = _snapshot_mapping(evidence_bundle)
        policy_snapshot = _snapshot_mapping(policy_projection)
        portfolio_snapshot = (
            None
            if portfolio_projection is None
            else _snapshot_mapping(portfolio_projection)
        )

        _validate_deterministic_source_bound_correlation(
            evidence_bundle=evidence_snapshot,
            policy_projection=policy_snapshot,
            policy_source_record_identity_sha256=policy_source_record_identity_sha256,
            portfolio_projection=portfolio_snapshot,
            portfolio_source_record_identity_sha256=portfolio_source_record_identity_sha256,
            run_context=run_context,
        )
        view = _derive_expected_view(
            evidence_bundle=evidence_snapshot,
            policy_projection=policy_snapshot,
            portfolio_projection=portfolio_snapshot,
            run_context=run_context,
        )
        _validate_derived_view(view)
    except _ViewBlocked as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _ViewContractFailure as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _INTERNAL_CONTRACT_FAILURE,
        )
    limitations = view.get("known_view_limitations")
    if type(limitations) is not list:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _INTERNAL_CONTRACT_FAILURE,
        )
    return _build_result(
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        *(
            limitation["code"]
            for limitation in limitations
            if type(limitation) is dict
            and type(limitation.get("code")) is str
        ),
        projection=view,
    )


def validate_mmi_analyst_visible_evidence_view_v2(
    *,
    value: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionValidationResult:
    """Validate a V2 candidate against exact same-run trusted inputs."""
    try:
        _validated_analyst_visible_evidence_view_v2_context(
            value=value,
            evidence_bundle=evidence_bundle,
            policy_projection=policy_projection,
            policy_source=policy_source,
            portfolio_projection=portfolio_projection,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except _ViewBlocked as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _ViewContractFailure as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _INTERNAL_CONTRACT_FAILURE,
        )
    return _validation_result(MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS)
