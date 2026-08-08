"""Deterministic report-only projection of validated MMI V2 analysis."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, NoReturn

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    _build_mmi_analyst_visible_evidence_view_v2_from_source_record_identities,
    _validate_live_provenance_and_extract_identities,
    _ViewBlocked,
    _ViewContractFailure,
)
from investment_orchestrator.mmi.canonical import (
    MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES,
    MmiCanonicalizationError,
    _MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_IDENTITY_DOMAIN,
    canonical_json_bytes,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MmiCapturedSource,
    MmiProjectionRunContext,
)
from investment_orchestrator.mmi.validated_grounded_analysis_response_v2 import (
    MmiValidatedGroundedAnalysisResponseV2Error,
    _validate_mmi_validated_grounded_analysis_response_v2_from_source_record_identities,
)


__all__ = (
    "MmiLegacyStep1CompatibilityCandidateV1Error",
    "build_mmi_legacy_step1_compatibility_candidate_v1",
    "validate_mmi_legacy_step1_compatibility_candidate_v1",
)

_SCHEMA_VERSION: Final = "mmi_legacy_step1_compatibility_candidate_v1"
_ARTIFACT_KIND: Final = "MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE"
_COMPILER_CONTRACT_VERSION: Final = (
    "mmi_legacy_step1_compatibility_compiler_v1"
)
_SCHEMA_NAME: Final = (
    "mmi_legacy_step1_compatibility_candidate_v1.schema.json"
)
_IDENTITY_FIELD: Final = (
    "legacy_step1_compatibility_candidate_identity_sha256"
)
_VIEW_IDENTITY_FIELD: Final = (
    "analyst_visible_evidence_view_identity_sha256"
)
_RESPONSE_IDENTITY_FIELD: Final = (
    "validated_grounded_analysis_response_identity_sha256"
)
_ZERO_SHA256: Final = "0" * 64
_QUALITATIVE_FIELDS: Final = (
    "evidence_observations",
    "risks",
    "uncertainties",
    "contradictions",
    "research_questions",
)
_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "compiler_contract_version",
        "report_only",
        "authority_effect",
        "provenance",
        "analysis_status",
        "ordered_instrument_assessments",
        *_QUALITATIVE_FIELDS,
        "summary",
        "source_capability_statuses",
        _IDENTITY_FIELD,
    }
)


class MmiLegacyStep1CompatibilityCandidateV1Error(ValueError):
    """Raised when no valid report-only compatibility candidate exists."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise MmiLegacyStep1CompatibilityCandidateV1Error(code)


def _validated_live_provenance_and_extracted_identities(
    *,
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> tuple[str, str | None]:
    try:
        return _validate_live_provenance_and_extract_identities(
            evidence_bundle=evidence_bundle,
            policy_projection=policy_projection,
            policy_source=policy_source,
            portfolio_projection=portfolio_projection,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except (_ViewBlocked, _ViewContractFailure):
        _fail("MMI_LEGACY_STEP1_CANDIDATE_UPSTREAM_RESPONSE_INVALID")


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
            _fail("MMI_LEGACY_STEP1_CANDIDATE_INPUT_INVALID")
        active_container_ids.add(container_id)
        try:
            snapshot: dict[str, object] = {}
            try:
                keys = tuple(value.keys())
                if (
                    any(type(key) is not str for key in keys)
                    or len(keys) != len(set(keys))
                ):
                    _fail("MMI_LEGACY_STEP1_CANDIDATE_INPUT_INVALID")
                for key in keys:
                    snapshot[key] = _snapshot_value(
                        value[key],
                        active_container_ids=active_container_ids,
                    )
            except MmiLegacyStep1CompatibilityCandidateV1Error:
                raise
            except Exception:
                _fail("MMI_LEGACY_STEP1_CANDIDATE_INPUT_INVALID")
            return snapshot
        finally:
            active_container_ids.remove(container_id)
    if type(value) is list:
        container_id = id(value)
        if container_id in active_container_ids:
            _fail("MMI_LEGACY_STEP1_CANDIDATE_INPUT_INVALID")
        active_container_ids.add(container_id)
        try:
            return [
                _snapshot_value(
                    item,
                    active_container_ids=active_container_ids,
                )
                for item in value
            ]
        finally:
            active_container_ids.remove(container_id)
    _fail("MMI_LEGACY_STEP1_CANDIDATE_INPUT_INVALID")


def _snapshot_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail("MMI_LEGACY_STEP1_CANDIDATE_INPUT_INVALID")
    snapshot = _snapshot_value(value, active_container_ids=set())
    if type(snapshot) is not dict:
        _fail("MMI_LEGACY_STEP1_CANDIDATE_INPUT_INVALID")
    return snapshot


def _require_dict(value: object) -> dict[str, object]:
    if type(value) is not dict:
        _fail("MMI_LEGACY_STEP1_CANDIDATE_PROJECTION_INVALID")
    return value


def _require_list(value: object) -> list[object]:
    if type(value) is not list:
        _fail("MMI_LEGACY_STEP1_CANDIDATE_PROJECTION_INVALID")
    return value


def _require_string(value: object) -> str:
    if type(value) is not str:
        _fail("MMI_LEGACY_STEP1_CANDIDATE_PROJECTION_INVALID")
    return value


def _validate_candidate_canonical_size(value: object) -> None:
    try:
        canonical_json_bytes(
            value,
            maximum_bytes=(
                MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail("MMI_LEGACY_STEP1_CANDIDATE_RESOURCE_LIMIT_EXCEEDED")


def _candidate_identity(candidate: dict[str, object]) -> str:
    if set(candidate) != _TOP_LEVEL_FIELDS:
        _fail("MMI_LEGACY_STEP1_CANDIDATE_SCHEMA_INVALID")
    try:
        return record_identity_sha256(
            candidate,
            identity_field=_IDENTITY_FIELD,
            domain=(
                _MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_IDENTITY_DOMAIN
            ),
            maximum_bytes=(
                MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail("MMI_LEGACY_STEP1_CANDIDATE_IDENTITY_MISMATCHED")


def _validate_candidate_schema(candidate: dict[str, object]) -> None:
    try:
        validate_artifact_schema(candidate, schema_name=_SCHEMA_NAME)
    except Exception:
        _fail("MMI_LEGACY_STEP1_CANDIDATE_SCHEMA_INVALID")


def _project_instrument_assessments(
    *,
    view: dict[str, object],
    response_payload: dict[str, object],
) -> list[object]:
    policy_view = _require_dict(view.get("policy_view"))
    view_rows = _require_list(policy_view.get("analysis_instruments"))
    response_rows = _require_list(response_payload.get("instrument_views"))
    if len(view_rows) != len(response_rows):
        _fail("MMI_LEGACY_STEP1_CANDIDATE_INSTRUMENT_MISMATCHED")

    assessments: list[object] = []
    observed_tickers: set[str] = set()
    for view_value, response_value in zip(view_rows, response_rows, strict=True):
        view_row = _require_dict(view_value)
        response_row = _require_dict(response_value)
        view_ticker = _require_string(view_row.get("ticker"))
        response_ticker = _require_string(response_row.get("ticker"))
        if view_ticker != response_ticker or view_ticker in observed_tickers:
            _fail("MMI_LEGACY_STEP1_CANDIDATE_INSTRUMENT_MISMATCHED")
        observed_tickers.add(view_ticker)
        references = _snapshot_value(
            response_row.get("references"),
            active_container_ids=set(),
        )
        assessments.append(
            {
                "ticker": view_ticker,
                "policy_role": _require_string(
                    view_row.get("policy_role")
                ),
                "evidence_status": _require_string(
                    response_row.get("evidence_status")
                ),
                "rationale_12m_plus": _snapshot_value(
                    response_row.get("rationale_12m_plus"),
                    active_container_ids=set(),
                ),
                "references": references,
            }
        )
    return assessments


def _source_capability_statuses(
    *,
    view: dict[str, object],
    response_payload: dict[str, object],
) -> dict[str, object]:
    policy_view = _require_dict(view.get("policy_view"))
    return {
        "anchor_associations_status": _require_string(
            response_payload.get("anchor_associations_status")
        ),
        "scheduled_events_status": _require_string(
            response_payload.get("scheduled_events_status")
        ),
        "regime_inputs_status": _require_string(
            response_payload.get("regime_observation_status")
        ),
        "target_weights_absence_reason": _require_string(
            policy_view.get("target_weights_absence_reason")
        ),
    }


def _build_mmi_legacy_step1_compatibility_candidate_v1_from_source_record_identities(
    *,
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source_record_identity_sha256: str,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source_record_identity_sha256: str | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    response_snapshot = _snapshot_mapping(
        validated_grounded_analysis_response
    )
    envelope_snapshot = _snapshot_mapping(raw_response_envelope)
    evidence_snapshot = _snapshot_mapping(evidence_bundle)
    policy_snapshot = _snapshot_mapping(policy_projection)
    portfolio_snapshot = (
        None
        if portfolio_projection is None
        else _snapshot_mapping(portfolio_projection)
    )

    try:
        response = _validate_mmi_validated_grounded_analysis_response_v2_from_source_record_identities(
            value=response_snapshot,
            raw_response_envelope=envelope_snapshot,
            evidence_bundle=evidence_snapshot,
            policy_projection=policy_snapshot,
            policy_source_record_identity_sha256=(
                policy_source_record_identity_sha256
            ),
            portfolio_projection=portfolio_snapshot,
            portfolio_source_record_identity_sha256=(
                portfolio_source_record_identity_sha256
            ),
            run_context=run_context,
        )
    except MmiValidatedGroundedAnalysisResponseV2Error:
        _fail("MMI_LEGACY_STEP1_CANDIDATE_UPSTREAM_RESPONSE_INVALID")

    view_result = _build_mmi_analyst_visible_evidence_view_v2_from_source_record_identities(
        evidence_bundle=evidence_snapshot,
        policy_projection=policy_snapshot,
        policy_source_record_identity_sha256=(
            policy_source_record_identity_sha256
        ),
        portfolio_projection=portfolio_snapshot,
        portfolio_source_record_identity_sha256=(
            portfolio_source_record_identity_sha256
        ),
        run_context=run_context,
    )
    if (
        not view_result.valid
        or view_result.authority_effect != AUTHORITY_EFFECT_NONE
        or view_result.projection is None
    ):
        _fail("MMI_LEGACY_STEP1_CANDIDATE_SOURCE_INCONSISTENT")
    view = _snapshot_mapping(view_result.projection)
    payload = _require_dict(response.get("response_payload"))

    candidate: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_kind": _ARTIFACT_KIND,
        "compiler_contract_version": _COMPILER_CONTRACT_VERSION,
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "provenance": {
            _VIEW_IDENTITY_FIELD: _require_string(
                view.get(_VIEW_IDENTITY_FIELD)
            ),
            _RESPONSE_IDENTITY_FIELD: _require_string(
                response.get(_RESPONSE_IDENTITY_FIELD)
            ),
        },
        "analysis_status": _require_string(payload.get("analysis_status")),
        "ordered_instrument_assessments": (
            _project_instrument_assessments(
                view=view,
                response_payload=payload,
            )
        ),
        **{
            field: _snapshot_value(
                payload.get(field),
                active_container_ids=set(),
            )
            for field in _QUALITATIVE_FIELDS
        },
        "summary": _snapshot_value(
            payload.get("summary"),
            active_container_ids=set(),
        ),
        "source_capability_statuses": _source_capability_statuses(
            view=view,
            response_payload=payload,
        ),
        _IDENTITY_FIELD: _ZERO_SHA256,
    }
    _validate_candidate_canonical_size(candidate)
    candidate[_IDENTITY_FIELD] = _candidate_identity(candidate)
    _validate_candidate_schema(candidate)
    return candidate


def _validate_mmi_legacy_step1_compatibility_candidate_v1_from_source_record_identities(
    *,
    value: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source_record_identity_sha256: str,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source_record_identity_sha256: str | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    candidate = _snapshot_mapping(value)
    _validate_candidate_schema(candidate)
    _validate_candidate_canonical_size(candidate)
    if candidate.get(_IDENTITY_FIELD) != _candidate_identity(candidate):
        _fail("MMI_LEGACY_STEP1_CANDIDATE_IDENTITY_MISMATCHED")
    expected = _build_mmi_legacy_step1_compatibility_candidate_v1_from_source_record_identities(
        validated_grounded_analysis_response=(
            validated_grounded_analysis_response
        ),
        raw_response_envelope=raw_response_envelope,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source_record_identity_sha256=(
            policy_source_record_identity_sha256
        ),
        portfolio_projection=portfolio_projection,
        portfolio_source_record_identity_sha256=(
            portfolio_source_record_identity_sha256
        ),
        run_context=run_context,
    )
    if candidate != expected:
        _fail("MMI_LEGACY_STEP1_CANDIDATE_NON_EXPECTED")
    return candidate


def build_mmi_legacy_step1_compatibility_candidate_v1(
    *,
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    """Build one source-bound report-only compatibility candidate."""
    policy_source_record_identity_sha256, portfolio_source_record_identity_sha256 = _validated_live_provenance_and_extracted_identities(
        evidence_bundle=_snapshot_mapping(evidence_bundle),
        policy_projection=_snapshot_mapping(policy_projection),
        policy_source=policy_source,
        portfolio_projection=(
            None
            if portfolio_projection is None
            else _snapshot_mapping(portfolio_projection)
        ),
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    return _build_mmi_legacy_step1_compatibility_candidate_v1_from_source_record_identities(
        validated_grounded_analysis_response=(
            validated_grounded_analysis_response
        ),
        raw_response_envelope=raw_response_envelope,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source_record_identity_sha256=(
            policy_source_record_identity_sha256
        ),
        portfolio_projection=portfolio_projection,
        portfolio_source_record_identity_sha256=(
            portfolio_source_record_identity_sha256
        ),
        run_context=run_context,
    )


def validate_mmi_legacy_step1_compatibility_candidate_v1(
    *,
    value: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    """Return one stable candidate equal to the source-bound projection."""
    candidate = _snapshot_mapping(value)
    _validate_candidate_schema(candidate)
    _validate_candidate_canonical_size(candidate)
    if candidate.get(_IDENTITY_FIELD) != _candidate_identity(candidate):
        _fail("MMI_LEGACY_STEP1_CANDIDATE_IDENTITY_MISMATCHED")

    policy_source_record_identity_sha256, portfolio_source_record_identity_sha256 = _validated_live_provenance_and_extracted_identities(
        evidence_bundle=_snapshot_mapping(evidence_bundle),
        policy_projection=_snapshot_mapping(policy_projection),
        policy_source=policy_source,
        portfolio_projection=(
            None
            if portfolio_projection is None
            else _snapshot_mapping(portfolio_projection)
        ),
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    return _validate_mmi_legacy_step1_compatibility_candidate_v1_from_source_record_identities(
        value=value,
        validated_grounded_analysis_response=(
            validated_grounded_analysis_response
        ),
        raw_response_envelope=raw_response_envelope,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source_record_identity_sha256=(
            policy_source_record_identity_sha256
        ),
        portfolio_projection=portfolio_projection,
        portfolio_source_record_identity_sha256=(
            portfolio_source_record_identity_sha256
        ),
        run_context=run_context,
    )
