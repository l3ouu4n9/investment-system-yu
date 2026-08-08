"""Source-bound construction and validation of the report-only MMI bundle."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_AUTHENTICATED_EVIDENCE_BUNDLE_CANONICAL_BYTES,
    MmiCanonicalizationError,
    canonical_json_bytes,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MMI_AUTHENTICATED_EVIDENCE_BUNDLE_ARTIFACT_KIND,
    MMI_AUTHENTICATED_EVIDENCE_BUNDLE_SCHEMA_VERSION,
    MMI_EVIDENCE_ASSEMBLY_GAP_SCOPE,
    MMI_EVIDENCE_POLICY_COMPONENT_PRESENCE_STATUS,
    MMI_EVIDENCE_PORTFOLIO_GAP_COMPONENT,
    MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_GAP_CODE,
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
    mmi_authenticated_evidence_bundle_identity_sha256,
)
from investment_orchestrator.mmi.policy_projection import (
    _validate_mmi_policy_projection_from_source_bytes,
)
from investment_orchestrator.mmi.portfolio_projection import (
    _validate_mmi_portfolio_snapshot_projection_from_source_bytes,
)


__all__ = (
    "build_mmi_authenticated_evidence_bundle",
    "validate_mmi_authenticated_evidence_bundle",
)

_SCHEMA_NAME: Final = (
    "mmi_authenticated_evidence_bundle_v1.schema.json"
)
_ZERO_SHA256: Final = "0" * 64
_LOWER_HEX: Final = frozenset("0123456789abcdef")

_PORTFOLIO_PROJECTION_REQUIRED: Final = (
    "MMI_EVIDENCE_PORTFOLIO_PROJECTION_REQUIRED"
)
_COMPONENT_VALIDATION_BLOCKED: Final = (
    "MMI_EVIDENCE_COMPONENT_VALIDATION_BLOCKED"
)
_COMPONENT_CONTRACT_FAILURE: Final = (
    "MMI_EVIDENCE_COMPONENT_VALIDATION_CONTRACT_FAILURE"
)
_DERIVED_SCHEMA_FAILURE: Final = (
    "MMI_EVIDENCE_DERIVED_BUNDLE_SCHEMA_INVALID"
)
_CANDIDATE_SCHEMA_FAILURE: Final = (
    "MMI_EVIDENCE_CANDIDATE_BUNDLE_SCHEMA_INVALID"
)
_BUNDLE_IDENTITY_FAILURE: Final = (
    "MMI_EVIDENCE_BUNDLE_IDENTITY_INVALID"
)
_SOURCE_FIDELITY_MISMATCH: Final = (
    "MMI_EVIDENCE_BUNDLE_SOURCE_FIDELITY_MISMATCH"
)
_INTERNAL_CONTRACT_FAILURE: Final = (
    "MMI_EVIDENCE_BUNDLE_INTERNAL_CONTRACT_FAILURE"
)


class _BundleBlocked(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _BundleContractFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class _ValidatedPolicyComponent:
    source_record_identity_sha256: str
    universe_projection_identity_sha256: str
    policy_projection_identity_sha256: str
    validation_result_category: str


@dataclass(frozen=True, slots=True)
class _ValidatedPortfolioComponent:
    presence_status: str
    portfolio_projection_identity_sha256: str | None
    portfolio_source_record_identity_sha256: str | None
    validation_result_category: str | None


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


def _snapshot_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _BundleBlocked(_COMPONENT_VALIDATION_BLOCKED)
    try:
        return dict(value)
    except (TypeError, ValueError):
        raise _BundleBlocked(_COMPONENT_VALIDATION_BLOCKED) from None


def _require_sha256(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or not set(value) <= _LOWER_HEX
    ):
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
    return value


def _require_policy_initial_trust(
    *,
    policy_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> None:
    if not _mmi_projection_run_context_provenance_is_valid(run_context):
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
    if not _mmi_captured_source_provenance_is_valid(policy_source):
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
    if policy_source.role is not MmiSourceRole.STRATEGY_SETTINGS:
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)


def _raise_for_component_result(
    result: (
        MmiPolicyProjectionValidationResult
        | MmiPortfolioProjectionValidationResult
    ),
) -> None:
    if (
        result.status
        is MmiProjectionResultCategory.PROJECTION_BLOCKED
    ):
        raise _BundleBlocked(_COMPONENT_VALIDATION_BLOCKED)
    if (
        result.status
        is MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    ):
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
    if (
        result.status
        is not MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
        or result.authority_effect != AUTHORITY_EFFECT_NONE
    ):
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)


def _validate_policy_component_from_source_bytes(
    policy_projection: Mapping[str, object],
    *,
    raw_bytes: bytes,
    source_record_identity_sha256: str,
    run_context: MmiProjectionRunContext,
) -> tuple[dict[str, object], _ValidatedPolicyComponent]:
    policy_value = _snapshot_mapping(policy_projection)
    try:
        validation = _validate_mmi_policy_projection_from_source_bytes(
            policy_value,
            raw_bytes=raw_bytes,
            source_record_identity_sha256=source_record_identity_sha256,
            run_context=run_context,
        )
    except Exception:
        raise _BundleContractFailure(_INTERNAL_CONTRACT_FAILURE) from None
    _raise_for_component_result(validation)

    universe = policy_value.get("universe_projection")
    if (
        policy_value.get("schema_version")
        != "mmi_policy_projection_v1"
        or policy_value.get("projection_kind")
        != "MMI_POLICY_PROJECTION"
        or policy_value.get("evaluation_timestamp_utc")
        != run_context.evaluation_timestamp_utc
        or type(universe) is not dict
        or universe.get("schema_version")
        != "mmi_universe_projection_v1"
        or universe.get("projection_kind")
        != "MMI_UNIVERSE_PROJECTION"
    ):
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)

    source_identity = source_record_identity_sha256
    universe_identity = _require_sha256(
        universe.get("universe_projection_identity_sha256")
    )
    policy_identity = _require_sha256(
        policy_value.get("policy_projection_identity_sha256")
    )
    if (
        policy_value.get("source_record_identity_sha256")
        != source_identity
        or universe.get("source_record_identity_sha256")
        != source_identity
        or policy_value.get("universe_projection_identity_sha256")
        != universe_identity
    ):
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
    return (
        policy_value,
        _ValidatedPolicyComponent(
            source_record_identity_sha256=source_identity,
            universe_projection_identity_sha256=universe_identity,
            policy_projection_identity_sha256=policy_identity,
            validation_result_category=validation.status.value,
        ),
    )


def _validate_portfolio_component_from_source_bytes(
    portfolio_projection: Mapping[str, object] | None,
    *,
    raw_bytes: bytes | None,
    source_record_identity_sha256: str | None,
    policy_projection: Mapping[str, object],
    policy_component: _ValidatedPolicyComponent,
    run_context: MmiProjectionRunContext,
) -> _ValidatedPortfolioComponent:
    if portfolio_projection is None:
        if raw_bytes is not None or source_record_identity_sha256 is not None:
            raise _BundleBlocked(_PORTFOLIO_PROJECTION_REQUIRED)
        return _ValidatedPortfolioComponent(
            presence_status=(
                MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS
            ),
            portfolio_projection_identity_sha256=None,
            portfolio_source_record_identity_sha256=None,
            validation_result_category=None,
        )

    portfolio_value = _snapshot_mapping(portfolio_projection)

    universe = policy_projection.get("universe_projection")
    if type(universe) is not dict:
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
    raw_roles = universe.get("role_by_ticker")
    if type(raw_roles) is not dict:
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
    policy_roles: dict[str, str] = {}
    for ticker, role in raw_roles.items():
        if type(ticker) is not str or type(role) is not str:
            raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
        policy_roles[ticker] = role

    try:
        validation = _validate_mmi_portfolio_snapshot_projection_from_source_bytes(
            portfolio_value,
            raw_bytes=raw_bytes,
            source_record_identity_sha256=source_record_identity_sha256,
            policy_projection_identity_sha256=policy_component.policy_projection_identity_sha256,
            policy_roles=policy_roles,
            run_context=run_context,
        )
    except Exception:
        raise _BundleContractFailure(_INTERNAL_CONTRACT_FAILURE) from None
    _raise_for_component_result(validation)

    if (
        portfolio_value.get("schema_version")
        != "mmi_portfolio_snapshot_projection_v1"
        or portfolio_value.get("projection_kind")
        != "MMI_PORTFOLIO_SNAPSHOT_PROJECTION"
        or portfolio_value.get("evaluation_timestamp_utc")
        != run_context.evaluation_timestamp_utc
        or portfolio_value.get("policy_projection_identity_sha256")
        != policy_component.policy_projection_identity_sha256
    ):
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)

    portfolio_identity = _require_sha256(
        portfolio_value.get("portfolio_projection_identity_sha256")
    )
    if raw_bytes is None:
        if (
            portfolio_value.get("portfolio_source_status")
            != "SOURCE_ABSENT"
            or portfolio_value.get(
                "portfolio_source_record_identity_sha256"
            )
            is not None
        ):
            raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
        presence_status = MMI_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_STATUS
        source_identity = None
    else:
        source_identity = source_record_identity_sha256
        if (
            portfolio_value.get("portfolio_source_status")
            != "SOURCE_PRESENT_CONTENT_BOUND"
            or portfolio_value.get(
                "portfolio_source_record_identity_sha256"
            )
            != source_identity
        ):
            raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
        presence_status = MMI_EVIDENCE_PORTFOLIO_SOURCE_BOUND_STATUS

    return _ValidatedPortfolioComponent(
        presence_status=presence_status,
        portfolio_projection_identity_sha256=portfolio_identity,
        portfolio_source_record_identity_sha256=source_identity,
        validation_result_category=validation.status.value,
    )


def _build_mmi_authenticated_evidence_bundle_from_components(
    *,
    policy_component: _ValidatedPolicyComponent,
    portfolio_component: _ValidatedPortfolioComponent,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    policy = policy_component
    portfolio = portfolio_component
    evaluation_timestamp_utc = run_context.evaluation_timestamp_utc
    policy_component: dict[str, object] = {
        "presence_status": (
            MMI_EVIDENCE_POLICY_COMPONENT_PRESENCE_STATUS
        ),
        "strategy_source_schema_version": "mmi_source_record_v1",
        "strategy_source_role": MmiSourceRole.STRATEGY_SETTINGS.value,
        "strategy_source_record_identity_sha256": (
            policy.source_record_identity_sha256
        ),
        "universe_schema_version": "mmi_universe_projection_v1",
        "universe_artifact_kind": "MMI_UNIVERSE_PROJECTION",
        "universe_projection_identity_sha256": (
            policy.universe_projection_identity_sha256
        ),
        "policy_schema_version": "mmi_policy_projection_v1",
        "policy_artifact_kind": "MMI_POLICY_PROJECTION",
        "policy_projection_identity_sha256": (
            policy.policy_projection_identity_sha256
        ),
        "validation_result_category": (
            policy.validation_result_category
        ),
    }

    known_gaps: list[dict[str, object]] = []
    if (
        portfolio.presence_status
        == MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS
    ):
        portfolio_component: dict[str, object] = {
            "presence_status": (
                MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS
            )
        }
        known_gaps.append(
            {
                "code": MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_GAP_CODE,
                "scope": MMI_EVIDENCE_ASSEMBLY_GAP_SCOPE,
                "component": MMI_EVIDENCE_PORTFOLIO_GAP_COMPONENT,
            }
        )
    else:
        portfolio_identity = (
            portfolio.portfolio_projection_identity_sha256
        )
        validation_category = portfolio.validation_result_category
        if (
            type(portfolio_identity) is not str
            or type(validation_category) is not str
        ):
            raise MmiCanonicalizationError(
                "MMI_AUTHENTICATED_EVIDENCE_BUNDLE_CONTRACT_INVALID"
            )
        portfolio_component = {
            "presence_status": portfolio.presence_status,
            "portfolio_schema_version": (
                "mmi_portfolio_snapshot_projection_v1"
            ),
            "portfolio_artifact_kind": (
                "MMI_PORTFOLIO_SNAPSHOT_PROJECTION"
            ),
            "portfolio_projection_identity_sha256": (
                portfolio_identity
            ),
            "policy_projection_identity_sha256": (
                policy.policy_projection_identity_sha256
            ),
            "portfolio_source_status": (
                "SOURCE_ABSENT"
                if portfolio.presence_status
                == MMI_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_STATUS
                else "SOURCE_PRESENT_CONTENT_BOUND"
            ),
            "validation_result_category": validation_category,
        }
        if (
            portfolio.presence_status
            == MMI_EVIDENCE_PORTFOLIO_SOURCE_BOUND_STATUS
        ):
            source_identity = (
                portfolio.portfolio_source_record_identity_sha256
            )
            if type(source_identity) is not str:
                raise MmiCanonicalizationError(
                    "MMI_AUTHENTICATED_EVIDENCE_BUNDLE_CONTRACT_INVALID"
                )
            portfolio_component.update(
                {
                    "portfolio_source_schema_version": (
                        "mmi_source_record_v1"
                    ),
                    "portfolio_source_role": (
                        MmiSourceRole.PORTFOLIO_SNAPSHOT.value
                    ),
                    "portfolio_source_record_identity_sha256": (
                        source_identity
                    ),
                }
            )

    manifest: dict[str, object] = {
        "schema_version": (
            MMI_AUTHENTICATED_EVIDENCE_BUNDLE_SCHEMA_VERSION
        ),
        "artifact_kind": (
            MMI_AUTHENTICATED_EVIDENCE_BUNDLE_ARTIFACT_KIND
        ),
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "evaluation_timestamp_utc": evaluation_timestamp_utc,
        "policy_component": policy_component,
        "portfolio_component": portfolio_component,
        "known_evidence_gaps": known_gaps,
        "evidence_completeness_status": (
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS.value
        ),
        "evidence_bundle_identity_sha256": _ZERO_SHA256,
    }
    manifest["evidence_bundle_identity_sha256"] = (
        mmi_authenticated_evidence_bundle_identity_sha256(manifest)
    )
    return manifest


def _validate_derived_manifest(manifest: dict[str, object]) -> None:
    try:
        validate_artifact_schema(
            manifest,
            schema_name=_SCHEMA_NAME,
        )
    except Exception:
        raise _BundleContractFailure(_DERIVED_SCHEMA_FAILURE) from None
    try:
        expected_identity = (
            mmi_authenticated_evidence_bundle_identity_sha256(
                manifest
            )
        )
    except MmiCanonicalizationError:
        raise _BundleContractFailure(_BUNDLE_IDENTITY_FAILURE) from None
    if (
        manifest.get("evidence_bundle_identity_sha256")
        != expected_identity
    ):
        raise _BundleContractFailure(_BUNDLE_IDENTITY_FAILURE)
    try:
        canonical_json_bytes(
            manifest,
            maximum_bytes=(
                MAXIMUM_AUTHENTICATED_EVIDENCE_BUNDLE_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        raise _BundleContractFailure(_INTERNAL_CONTRACT_FAILURE) from None


def _source_bound_expected_manifest(
    *,
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    _require_policy_initial_trust(
        policy_source=policy_source,
        run_context=run_context,
    )
    try:
        policy_source_record = dict(policy_source.source_record)
    except (TypeError, ValueError):
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE) from None
    if (
        policy_source_record.get("schema_version") != "mmi_source_record_v1"
        or policy_source_record.get("source_role")
        != MmiSourceRole.STRATEGY_SETTINGS.value
    ):
        raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
    policy_source_identity = _require_sha256(
        policy_source_record.get("source_record_identity_sha256")
    )
    policy_raw_bytes = policy_source.raw_bytes

    policy_value, validated_policy = _validate_policy_component_from_source_bytes(
        policy_projection,
        raw_bytes=policy_raw_bytes,
        source_record_identity_sha256=policy_source_identity,
        run_context=run_context,
    )

    if portfolio_source is None:
        portfolio_raw_bytes = None
        portfolio_source_identity = None
    else:
        if (
            not _mmi_captured_source_provenance_is_valid(
                portfolio_source
            )
            or portfolio_source.role
            is not MmiSourceRole.PORTFOLIO_SNAPSHOT
        ):
            raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
        try:
            portfolio_source_record = dict(portfolio_source.source_record)
        except (TypeError, ValueError):
            raise _BundleContractFailure(
                _COMPONENT_CONTRACT_FAILURE
            ) from None
        if (
            portfolio_source_record.get("schema_version")
            != "mmi_source_record_v1"
            or portfolio_source_record.get("source_role")
            != MmiSourceRole.PORTFOLIO_SNAPSHOT.value
        ):
            raise _BundleContractFailure(_COMPONENT_CONTRACT_FAILURE)
        portfolio_source_identity = _require_sha256(
            portfolio_source_record.get("source_record_identity_sha256")
        )
        portfolio_raw_bytes = portfolio_source.raw_bytes

    validated_portfolio = _validate_portfolio_component_from_source_bytes(
        portfolio_projection,
        raw_bytes=portfolio_raw_bytes,
        source_record_identity_sha256=portfolio_source_identity,
        policy_projection=policy_value,
        policy_component=validated_policy,
        run_context=run_context,
    )
    try:
        manifest = _build_mmi_authenticated_evidence_bundle_from_components(
            policy_component=validated_policy,
            portfolio_component=validated_portfolio,
            run_context=run_context,
        )
    except MmiCanonicalizationError:
        raise _BundleContractFailure(_BUNDLE_IDENTITY_FAILURE) from None
    _validate_derived_manifest(manifest)
    return manifest


def build_mmi_authenticated_evidence_bundle(
    *,
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionBuildResult:
    """Build one in-memory report-only identity manifest."""
    try:
        manifest = _source_bound_expected_manifest(
            policy_projection=policy_projection,
            policy_source=policy_source,
            portfolio_projection=portfolio_projection,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except _BundleBlocked as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _BundleContractFailure as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _INTERNAL_CONTRACT_FAILURE,
        )
    gaps = manifest["known_evidence_gaps"]
    if type(gaps) is not list:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _INTERNAL_CONTRACT_FAILURE,
        )
    return _build_result(
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        *(gap["code"] for gap in gaps if type(gap) is dict),
        projection=manifest,
    )


def validate_mmi_authenticated_evidence_bundle(
    value: Mapping[str, object],
    *,
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionValidationResult:
    """Validate a candidate against the exact same-run trusted inputs."""
    try:
        expected = _source_bound_expected_manifest(
            policy_projection=policy_projection,
            policy_source=policy_source,
            portfolio_projection=portfolio_projection,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except _BundleBlocked as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _BundleContractFailure as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _INTERNAL_CONTRACT_FAILURE,
        )

    if not isinstance(value, Mapping):
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            _CANDIDATE_SCHEMA_FAILURE,
        )
    try:
        candidate = dict(value)
    except (TypeError, ValueError):
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            _CANDIDATE_SCHEMA_FAILURE,
        )
    try:
        validate_artifact_schema(
            candidate,
            schema_name=_SCHEMA_NAME,
        )
    except Exception:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            _CANDIDATE_SCHEMA_FAILURE,
        )
    try:
        expected_identity = (
            mmi_authenticated_evidence_bundle_identity_sha256(
                candidate
            )
        )
        canonical_json_bytes(
            candidate,
            maximum_bytes=(
                MAXIMUM_AUTHENTICATED_EVIDENCE_BUNDLE_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _BUNDLE_IDENTITY_FAILURE,
        )
    if (
        candidate.get("evidence_bundle_identity_sha256")
        != expected_identity
    ):
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _BUNDLE_IDENTITY_FAILURE,
        )
    if candidate != expected:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _SOURCE_FIDELITY_MISMATCH,
        )
    return _validation_result(
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
