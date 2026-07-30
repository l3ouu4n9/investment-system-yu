"""Source-bound decoding and validation of one grounded response."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Final

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    _MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_ARTIFACT_KIND,
    MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_SCHEMA_VERSION,
    MmiCapturedSource,
    MmiPolicyProjectionBuildResult,
    MmiPolicyProjectionValidationResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    _validate_grounded_analysis_response_payload,
    mmi_validated_grounded_analysis_response_identity_sha256,
)
from investment_orchestrator.mmi.raw_response_envelope import (
    validate_mmi_raw_response_envelope,
)


__all__ = (
    "build_mmi_validated_grounded_analysis_response",
    "validate_mmi_validated_grounded_analysis_response",
)

_SCHEMA_NAME: Final = (
    "mmi_validated_grounded_analysis_response_v1.schema.json"
)
_IDENTITY_FIELD: Final = (
    "validated_grounded_analysis_response_identity_sha256"
)
_R1_IDENTITY_FIELD: Final = (
    "raw_response_envelope_identity_sha256"
)
_CONTEXT_FIELD: Final = "prompt_context_binding_sha256"
_ZERO_SHA256: Final = "0" * 64

_INPUT_SNAPSHOT_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_INPUT_SNAPSHOT_INVALID"
)
_UPSTREAM_RESULT_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_UPSTREAM_RESULT_INVALID"
)
_UTF8_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_UTF8_INVALID"
)
_UTF8_BOM_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_UTF8_BOM_INVALID"
)
_EMPTY_RESPONSE_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_EMPTY_RESPONSE_INVALID"
)
_JSON_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_JSON_INVALID"
)
_RESPONSE_SCHEMA_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_RESPONSE_SCHEMA_INVALID"
)
_CONTEXT_BINDING_MISMATCH: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_CONTEXT_BINDING_MISMATCH"
)
_REFERENCE_MEMBERSHIP_MISMATCH: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_REFERENCE_MEMBERSHIP_MISMATCH"
)
_DERIVED_SCHEMA_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_DERIVED_SCHEMA_INVALID"
)
_DERIVED_CONTRACT_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_DERIVED_CONTRACT_INVALID"
)
_CANDIDATE_SCHEMA_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_CANDIDATE_SCHEMA_INVALID"
)
_CANDIDATE_CONTRACT_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_CANDIDATE_CONTRACT_INVALID"
)
_ARTIFACT_IDENTITY_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_ARTIFACT_IDENTITY_INVALID"
)
_SOURCE_FIDELITY_MISMATCH: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_SOURCE_FIDELITY_MISMATCH"
)
_INTERNAL_CONTRACT_FAILURE: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_INTERNAL_CONTRACT_FAILURE"
)

_STRUCTURAL_WRAPPER_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_WRAPPER_INVALID"
)
_STRUCTURAL_PAYLOAD_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_PAYLOAD_INVALID"
)
_STRUCTURAL_REFERENCE_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_REFERENCE_INVALID"
)
_STRUCTURAL_TEXT_BYTES_INVALID: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_TEXT_BYTES_INVALID"
)
_STRUCTURAL_IDENTITY_CONTRADICTION: Final = (
    "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_IDENTITY_CONTRADICTION"
)

_TASK_ARRAY_FIELDS: Final = (
    "evidence_observations",
    "risks",
    "uncertainties",
    "contradictions",
    "research_questions",
)
_ALWAYS_ALLOWED_REFERENCES: Final = frozenset(
    {
        "VIEW.EVALUATION_TIMESTAMP",
        "VIEW.COMPLETENESS_STATUS",
        "POLICY.AS_OF_DATE",
        "POLICY.METHOD",
        "POLICY.BENCHMARK.0001",
        "POLICY.EXTENDED_ACTIVATION_STATUS",
        "POLICY.INSTRUMENT_AVAILABILITY_STATUS",
        "POLICY.TARGET_WEIGHTS_ABSENCE_REASON",
        "PORTFOLIO.PRESENCE_STATUS",
    }
)
_PRESENT_PORTFOLIO_REFERENCES: Final = frozenset(
    {
        "PORTFOLIO.SOURCE_DATE",
        "PORTFOLIO.OPEN_BUY_STATUS",
        "PORTFOLIO.COVERAGE.HOLDINGS",
        "PORTFOLIO.COVERAGE.CASH",
        "PORTFOLIO.COVERAGE.DEPLOYABLE_CASH",
        "PORTFOLIO.COVERAGE.OPEN_SELLS",
        "PORTFOLIO.COVERAGE.TAX_LOTS",
        "PORTFOLIO.COVERAGE.HOLDING_DATES",
        "PORTFOLIO.COVERAGE.GAINS_LOSSES",
        "PORTFOLIO.COVERAGE.WEIGHTS",
        "PORTFOLIO.COVERAGE.NAV_CONCENTRATION",
        "PORTFOLIO.COVERAGE.LOOK_THROUGH_EXPOSURE",
    }
)
_PRESENT_PORTFOLIO_STATUSES: Final = frozenset(
    {
        "PRESENT_VALIDATED_SOURCE_ABSENT",
        "PRESENT_SOURCE_BOUND_VALIDATED",
    }
)
_JSON_WHITESPACE: Final = frozenset(" \t\r\n")


class _ResponseBlocked(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ResponseContractFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _UpstreamFailure(RuntimeError):
    def __init__(
        self,
        *,
        status: MmiProjectionResultCategory,
        reason_codes: tuple[str, ...],
    ) -> None:
        super().__init__(status.value)
        self.status = status
        self.reason_codes = reason_codes


class _DuplicateJsonKey(ValueError):
    pass


class _NonstandardJsonConstant(ValueError):
    pass


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
            raise _ResponseBlocked(_INPUT_SNAPSHOT_INVALID)
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
                        raise _ResponseBlocked(
                            _INPUT_SNAPSHOT_INVALID
                        )
                    seen_keys.add(key)
                    snapshot[key] = _snapshot_value(
                        value[key],
                        active_container_ids=active_container_ids,
                    )
            except _ResponseBlocked:
                raise
            except Exception:
                raise _ResponseBlocked(
                    _INPUT_SNAPSHOT_INVALID
                ) from None
            return snapshot
        finally:
            active_container_ids.remove(container_id)

    if type(value) is list:
        container_id = id(value)
        if container_id in active_container_ids:
            raise _ResponseBlocked(_INPUT_SNAPSHOT_INVALID)
        active_container_ids.add(container_id)
        try:
            try:
                materialized = list(value)
            except Exception:
                raise _ResponseBlocked(
                    _INPUT_SNAPSHOT_INVALID
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
        container_id = id(value)
        if container_id in active_container_ids:
            raise _ResponseBlocked(_INPUT_SNAPSHOT_INVALID)
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

    raise _ResponseBlocked(_INPUT_SNAPSHOT_INVALID)


def _snapshot_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _ResponseBlocked(_INPUT_SNAPSHOT_INVALID)
    snapshot = _snapshot_value(value, active_container_ids=set())
    if type(snapshot) is not dict:
        raise _ResponseBlocked(_INPUT_SNAPSHOT_INVALID)
    return snapshot


def _snapshot_if_mapping(value: object) -> object:
    if isinstance(value, Mapping):
        return _snapshot_mapping(value)
    return value


def _require_source_bound_envelope(
    *,
    raw_response_envelope: dict[str, object],
    raw_response_bytes: object,
    grounded_prompt: dict[str, object],
    analyst_visible_evidence_view: dict[str, object],
    evidence_bundle: dict[str, object],
    policy_projection: dict[str, object],
    policy_source: object,
    portfolio_projection: dict[str, object] | None,
    portfolio_source: object,
    run_context: object,
) -> tuple[str, tuple[str, ...]]:
    try:
        result = validate_mmi_raw_response_envelope(
            value=raw_response_envelope,
            grounded_prompt=grounded_prompt,
            raw_response_bytes=raw_response_bytes,  # type: ignore[arg-type]
            analyst_visible_evidence_view=(
                analyst_visible_evidence_view
            ),
            evidence_bundle=evidence_bundle,
            policy_projection=policy_projection,
            policy_source=policy_source,  # type: ignore[arg-type]
            portfolio_projection=portfolio_projection,
            portfolio_source=portfolio_source,  # type: ignore[arg-type]
            run_context=run_context,  # type: ignore[arg-type]
        )
    except Exception:
        raise _ResponseContractFailure(
            _INTERNAL_CONTRACT_FAILURE
        ) from None
    if result.status in {
        MmiProjectionResultCategory.PROJECTION_BLOCKED,
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
    }:
        raise _UpstreamFailure(
            status=result.status,
            reason_codes=result.reason_codes,
        )
    if (
        result.status
        is not MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
        or result.authority_effect != AUTHORITY_EFFECT_NONE
    ):
        raise _ResponseContractFailure(_UPSTREAM_RESULT_INVALID)
    envelope_identity = raw_response_envelope.get(
        _R1_IDENTITY_FIELD
    )
    if type(envelope_identity) is not str:
        raise _ResponseContractFailure(_UPSTREAM_RESULT_INVALID)
    return envelope_identity, result.reason_codes


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonstandard_constant(_value: str) -> object:
    raise _NonstandardJsonConstant


def _reject_unpaired_surrogates(value: object) -> None:
    if type(value) is str:
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise _ResponseBlocked(_JSON_INVALID) from None
        return
    if type(value) is dict:
        for key, nested in value.items():
            _reject_unpaired_surrogates(key)
            _reject_unpaired_surrogates(nested)
        return
    if type(value) is list:
        for nested in value:
            _reject_unpaired_surrogates(nested)


def _decode_and_parse_response(
    raw_response_bytes: object,
) -> dict[str, object]:
    if type(raw_response_bytes) is not bytes:
        raise _ResponseContractFailure(_UPSTREAM_RESULT_INVALID)
    try:
        text = raw_response_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _ResponseBlocked(_UTF8_INVALID) from None
    if text.startswith("\ufeff"):
        raise _ResponseBlocked(_UTF8_BOM_INVALID)
    if not text or all(character in _JSON_WHITESPACE for character in text):
        raise _ResponseBlocked(_EMPTY_RESPONSE_INVALID)
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except (
        json.JSONDecodeError,
        _DuplicateJsonKey,
        _NonstandardJsonConstant,
        RecursionError,
        ValueError,
    ):
        raise _ResponseBlocked(_JSON_INVALID) from None
    if type(parsed) is not dict:
        raise _ResponseBlocked(_JSON_INVALID)
    _reject_unpaired_surrogates(parsed)
    return parsed


def _validate_response_payload(payload: dict[str, object]) -> None:
    try:
        _validate_grounded_analysis_response_payload(payload)
    except MmiCanonicalizationError:
        raise _ResponseBlocked(_RESPONSE_SCHEMA_INVALID) from None


def _require_context_correlation(
    *,
    payload: dict[str, object],
    grounded_prompt: dict[str, object],
) -> None:
    trusted_context = grounded_prompt.get(_CONTEXT_FIELD)
    if type(trusted_context) is not str:
        raise _ResponseContractFailure(_UPSTREAM_RESULT_INVALID)
    if payload.get(_CONTEXT_FIELD) != trusted_context:
        raise _ResponseContractFailure(_CONTEXT_BINDING_MISMATCH)


def _source_bound_reference_catalog(
    analyst_visible_evidence_view: dict[str, object],
) -> frozenset[str]:
    try:
        policy_view = analyst_visible_evidence_view["policy_view"]
        limitations = analyst_visible_evidence_view[
            "known_view_limitations"
        ]
        portfolio_view = analyst_visible_evidence_view[
            "portfolio_view"
        ]
        if (
            type(policy_view) is not dict
            or type(limitations) is not list
            or type(portfolio_view) is not dict
        ):
            raise TypeError
        instruments = policy_view["analysis_instruments"]
        if type(instruments) is not list:
            raise TypeError
        presence_status = portfolio_view["presence_status"]
        if type(presence_status) is not str:
            raise TypeError
    except (KeyError, TypeError):
        raise _ResponseContractFailure(
            _UPSTREAM_RESULT_INVALID
        ) from None

    allowed = set(_ALWAYS_ALLOWED_REFERENCES)
    allowed.update(
        f"POLICY.INSTRUMENT.{index:04d}"
        for index in range(1, len(instruments) + 1)
    )
    allowed.update(
        f"LIMITATION.{index:04d}"
        for index in range(1, len(limitations) + 1)
    )
    if presence_status == "NOT_SUPPLIED":
        return frozenset(allowed)
    if presence_status not in _PRESENT_PORTFOLIO_STATUSES:
        raise _ResponseContractFailure(_UPSTREAM_RESULT_INVALID)
    try:
        observations = portfolio_view["open_buy_observations"]
        if type(observations) is not list:
            raise TypeError
    except (KeyError, TypeError):
        raise _ResponseContractFailure(
            _UPSTREAM_RESULT_INVALID
        ) from None
    allowed.update(_PRESENT_PORTFOLIO_REFERENCES)
    allowed.update(
        f"PORTFOLIO.OBSERVATION.{index:04d}"
        for index in range(1, len(observations) + 1)
    )
    return frozenset(allowed)


def _iter_payload_references(
    payload: dict[str, object],
) -> tuple[str, ...]:
    references: list[str] = []
    for field in _TASK_ARRAY_FIELDS:
        items = payload[field]
        if type(items) is not list:
            raise _ResponseContractFailure(
                _INTERNAL_CONTRACT_FAILURE
            )
        for item in items:
            if type(item) is not dict:
                raise _ResponseContractFailure(
                    _INTERNAL_CONTRACT_FAILURE
                )
            item_references = item["references"]
            if type(item_references) is not list:
                raise _ResponseContractFailure(
                    _INTERNAL_CONTRACT_FAILURE
                )
            references.extend(item_references)
    summary = payload["summary"]
    if type(summary) is not dict:
        raise _ResponseContractFailure(_INTERNAL_CONTRACT_FAILURE)
    summary_references = summary["references"]
    if type(summary_references) is not list:
        raise _ResponseContractFailure(_INTERNAL_CONTRACT_FAILURE)
    references.extend(summary_references)
    if any(type(reference) is not str for reference in references):
        raise _ResponseContractFailure(_INTERNAL_CONTRACT_FAILURE)
    return tuple(references)


def _require_reference_membership(
    *,
    payload: dict[str, object],
    analyst_visible_evidence_view: dict[str, object],
) -> None:
    allowed = _source_bound_reference_catalog(
        analyst_visible_evidence_view
    )
    if any(
        reference not in allowed
        for reference in _iter_payload_references(payload)
    ):
        raise _ResponseContractFailure(
            _REFERENCE_MEMBERSHIP_MISMATCH
        )


def _calculate_artifact_identity(
    artifact: dict[str, object],
) -> str:
    try:
        return record_identity_sha256(
            artifact,
            identity_field=_IDENTITY_FIELD,
            domain=(
                _MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_IDENTITY_DOMAIN
            ),
        )
    except MmiCanonicalizationError:
        raise _ResponseContractFailure(
            _DERIVED_CONTRACT_INVALID
        ) from None


def _validate_derived_artifact(
    artifact: dict[str, object],
) -> None:
    try:
        validate_artifact_schema(artifact, schema_name=_SCHEMA_NAME)
    except Exception:
        raise _ResponseContractFailure(
            _DERIVED_SCHEMA_INVALID
        ) from None
    try:
        calculated = (
            mmi_validated_grounded_analysis_response_identity_sha256(
                artifact
            )
        )
    except MmiCanonicalizationError:
        raise _ResponseContractFailure(
            _DERIVED_CONTRACT_INVALID
        ) from None
    if calculated != artifact.get(_IDENTITY_FIELD):
        raise _ResponseContractFailure(_DERIVED_CONTRACT_INVALID)


def _derive_expected_artifact(
    *,
    raw_response_envelope: dict[str, object],
    raw_response_bytes: object,
    grounded_prompt: dict[str, object],
    analyst_visible_evidence_view: dict[str, object],
    evidence_bundle: dict[str, object],
    policy_projection: dict[str, object],
    policy_source: object,
    portfolio_projection: dict[str, object] | None,
    portfolio_source: object,
    run_context: object,
) -> tuple[dict[str, object], tuple[str, ...]]:
    envelope_identity, reason_codes = _require_source_bound_envelope(
        raw_response_envelope=raw_response_envelope,
        raw_response_bytes=raw_response_bytes,
        grounded_prompt=grounded_prompt,
        analyst_visible_evidence_view=analyst_visible_evidence_view,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    payload = _decode_and_parse_response(raw_response_bytes)
    _validate_response_payload(payload)
    _require_context_correlation(
        payload=payload,
        grounded_prompt=grounded_prompt,
    )
    _require_reference_membership(
        payload=payload,
        analyst_visible_evidence_view=analyst_visible_evidence_view,
    )
    artifact: dict[str, object] = {
        "schema_version": (
            MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_SCHEMA_VERSION
        ),
        "artifact_kind": (
            MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_ARTIFACT_KIND
        ),
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "manual_handoff_required": True,
        _R1_IDENTITY_FIELD: envelope_identity,
        "response_payload": payload,
        _IDENTITY_FIELD: _ZERO_SHA256,
    }
    artifact[_IDENTITY_FIELD] = _calculate_artifact_identity(
        artifact
    )
    _validate_derived_artifact(artifact)
    return artifact, reason_codes


def _validate_candidate_artifact(
    candidate: dict[str, object],
    *,
    expected: dict[str, object],
) -> None:
    try:
        validate_artifact_schema(candidate, schema_name=_SCHEMA_NAME)
    except Exception:
        raise _ResponseBlocked(_CANDIDATE_SCHEMA_INVALID) from None
    try:
        calculated = (
            mmi_validated_grounded_analysis_response_identity_sha256(
                candidate
            )
        )
    except MmiCanonicalizationError as exc:
        if exc.code in {
            _STRUCTURAL_WRAPPER_INVALID,
            _STRUCTURAL_PAYLOAD_INVALID,
            _STRUCTURAL_REFERENCE_INVALID,
            _STRUCTURAL_TEXT_BYTES_INVALID,
        }:
            raise _ResponseBlocked(exc.code) from None
        if exc.code == _STRUCTURAL_IDENTITY_CONTRADICTION:
            raise _ResponseContractFailure(exc.code) from None
        raise _ResponseContractFailure(
            _CANDIDATE_CONTRACT_INVALID
        ) from None
    if calculated != candidate.get(_IDENTITY_FIELD):
        raise _ResponseContractFailure(_ARTIFACT_IDENTITY_INVALID)
    if candidate != expected:
        raise _ResponseContractFailure(_SOURCE_FIDELITY_MISMATCH)


def build_mmi_validated_grounded_analysis_response(
    *,
    raw_response_envelope: Mapping[str, object],
    raw_response_bytes: bytes,
    grounded_prompt: Mapping[str, object],
    analyst_visible_evidence_view: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionBuildResult:
    """Build one source-bound, report-only validated response."""
    try:
        envelope_snapshot = _snapshot_mapping(raw_response_envelope)
        prompt_snapshot = _snapshot_mapping(grounded_prompt)
        view_snapshot = _snapshot_mapping(
            analyst_visible_evidence_view
        )
        evidence_snapshot = _snapshot_mapping(evidence_bundle)
        policy_snapshot = _snapshot_mapping(policy_projection)
        policy_source_snapshot = _snapshot_if_mapping(policy_source)
        portfolio_snapshot = (
            None
            if portfolio_projection is None
            else _snapshot_mapping(portfolio_projection)
        )
        portfolio_source_snapshot = _snapshot_if_mapping(
            portfolio_source
        )
        run_context_snapshot = _snapshot_if_mapping(run_context)
        artifact, reason_codes = _derive_expected_artifact(
            raw_response_envelope=envelope_snapshot,
            raw_response_bytes=raw_response_bytes,
            grounded_prompt=prompt_snapshot,
            analyst_visible_evidence_view=view_snapshot,
            evidence_bundle=evidence_snapshot,
            policy_projection=policy_snapshot,
            policy_source=policy_source_snapshot,
            portfolio_projection=portfolio_snapshot,
            portfolio_source=portfolio_source_snapshot,
            run_context=run_context_snapshot,
        )
    except _UpstreamFailure as exc:
        return _build_result(
            exc.status,
            *exc.reason_codes,
        )
    except _ResponseBlocked as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _ResponseContractFailure as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _INTERNAL_CONTRACT_FAILURE,
        )
    return _build_result(
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        *reason_codes,
        projection=artifact,
    )


def validate_mmi_validated_grounded_analysis_response(
    *,
    value: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    raw_response_bytes: bytes,
    grounded_prompt: Mapping[str, object],
    analyst_visible_evidence_view: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionValidationResult:
    """Validate one candidate against authoritative source-bound bytes."""
    try:
        candidate = _snapshot_mapping(value)
    except _ResponseBlocked:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            _CANDIDATE_SCHEMA_INVALID,
        )
    try:
        envelope_snapshot = _snapshot_mapping(raw_response_envelope)
        prompt_snapshot = _snapshot_mapping(grounded_prompt)
        view_snapshot = _snapshot_mapping(
            analyst_visible_evidence_view
        )
        evidence_snapshot = _snapshot_mapping(evidence_bundle)
        policy_snapshot = _snapshot_mapping(policy_projection)
        policy_source_snapshot = _snapshot_if_mapping(policy_source)
        portfolio_snapshot = (
            None
            if portfolio_projection is None
            else _snapshot_mapping(portfolio_projection)
        )
        portfolio_source_snapshot = _snapshot_if_mapping(
            portfolio_source
        )
        run_context_snapshot = _snapshot_if_mapping(run_context)
        expected, _reason_codes = _derive_expected_artifact(
            raw_response_envelope=envelope_snapshot,
            raw_response_bytes=raw_response_bytes,
            grounded_prompt=prompt_snapshot,
            analyst_visible_evidence_view=view_snapshot,
            evidence_bundle=evidence_snapshot,
            policy_projection=policy_snapshot,
            policy_source=policy_source_snapshot,
            portfolio_projection=portfolio_snapshot,
            portfolio_source=portfolio_source_snapshot,
            run_context=run_context_snapshot,
        )
        _validate_candidate_artifact(candidate, expected=expected)
    except _UpstreamFailure as exc:
        return _validation_result(
            exc.status,
            *exc.reason_codes,
        )
    except _ResponseBlocked as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _ResponseContractFailure as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            _INTERNAL_CONTRACT_FAILURE,
        )
    return _validation_result(
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
