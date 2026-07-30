"""Source-bound construction and validation of an exact raw response."""

from __future__ import annotations

import base64
from collections.abc import Mapping
import hashlib
from typing import Final

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    _MMI_RAW_RESPONSE_ENVELOPE_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND,
    MMI_RAW_RESPONSE_ENVELOPE_SCHEMA_VERSION,
    MmiCapturedSource,
    MmiPolicyProjectionBuildResult,
    MmiPolicyProjectionValidationResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    mmi_raw_response_envelope_identity_sha256,
)
from investment_orchestrator.mmi.grounded_prompt import (
    validate_mmi_grounded_prompt,
)


__all__ = (
    "build_mmi_raw_response_envelope",
    "validate_mmi_raw_response_envelope",
)

_SCHEMA_NAME: Final = "mmi_raw_response_envelope_v1.schema.json"
_IDENTITY_FIELD: Final = "raw_response_envelope_identity_sha256"
_PROMPT_IDENTITY_FIELD: Final = (
    "grounded_prompt_artifact_identity_sha256"
)
_ZERO_SHA256: Final = "0" * 64

_INPUT_SNAPSHOT_INVALID: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_INPUT_SNAPSHOT_INVALID"
)
_RAW_RESPONSE_INPUT_INVALID: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_RAW_RESPONSE_INPUT_INVALID"
)
_UPSTREAM_RESULT_INVALID: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_UPSTREAM_RESULT_INVALID"
)
_DERIVED_SCHEMA_INVALID: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_DERIVED_SCHEMA_INVALID"
)
_DERIVED_CONTRACT_INVALID: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_DERIVED_CONTRACT_INVALID"
)
_CANDIDATE_SCHEMA_INVALID: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_CANDIDATE_SCHEMA_INVALID"
)
_CANDIDATE_CONTRACT_INVALID: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_CANDIDATE_CONTRACT_INVALID"
)
_SOURCE_FIDELITY_MISMATCH: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_SOURCE_FIDELITY_MISMATCH"
)
_INTERNAL_CONTRACT_FAILURE: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_INTERNAL_CONTRACT_FAILURE"
)

_STRUCTURAL_REPRESENTATION_INVALID: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_REPRESENTATION_INVALID"
)
_STRUCTURAL_LENGTH_CONTRADICTION: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_LENGTH_CONTRADICTION"
)
_STRUCTURAL_DIGEST_CONTRADICTION: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_DIGEST_CONTRADICTION"
)
_STRUCTURAL_IDENTITY_CONTRADICTION: Final = (
    "MMI_RAW_RESPONSE_ENVELOPE_IDENTITY_CONTRADICTION"
)


class _EnvelopeBlocked(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _EnvelopeContractFailure(RuntimeError):
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
            raise _EnvelopeBlocked(_INPUT_SNAPSHOT_INVALID)
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
                        raise _EnvelopeBlocked(
                            _INPUT_SNAPSHOT_INVALID
                        )
                    seen_keys.add(key)
                    snapshot[key] = _snapshot_value(
                        value[key],
                        active_container_ids=active_container_ids,
                    )
            except _EnvelopeBlocked:
                raise
            except Exception:
                raise _EnvelopeBlocked(
                    _INPUT_SNAPSHOT_INVALID
                ) from None
            return snapshot
        finally:
            active_container_ids.remove(container_id)

    if type(value) is list:
        container_id = id(value)
        if container_id in active_container_ids:
            raise _EnvelopeBlocked(_INPUT_SNAPSHOT_INVALID)
        active_container_ids.add(container_id)
        try:
            try:
                materialized = list(value)
            except Exception:
                raise _EnvelopeBlocked(
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
            raise _EnvelopeBlocked(_INPUT_SNAPSHOT_INVALID)
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

    raise _EnvelopeBlocked(_INPUT_SNAPSHOT_INVALID)


def _snapshot_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _EnvelopeBlocked(_INPUT_SNAPSHOT_INVALID)
    snapshot = _snapshot_value(value, active_container_ids=set())
    if type(snapshot) is not dict:
        raise _EnvelopeBlocked(_INPUT_SNAPSHOT_INVALID)
    return snapshot


def _snapshot_if_mapping(value: object) -> object:
    if isinstance(value, Mapping):
        return _snapshot_mapping(value)
    return value


def _require_raw_response_bytes(value: object) -> bytes:
    if (
        type(value) is not bytes
        or not 1 <= len(value) <= MAXIMUM_MMI_RAW_RESPONSE_BYTES
    ):
        raise _EnvelopeBlocked(_RAW_RESPONSE_INPUT_INVALID)
    return value


def _require_source_bound_prompt(
    *,
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
        result = validate_mmi_grounded_prompt(
            value=grounded_prompt,
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
        raise _EnvelopeContractFailure(
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
        raise _EnvelopeContractFailure(_UPSTREAM_RESULT_INVALID)
    prompt_identity = grounded_prompt.get(_PROMPT_IDENTITY_FIELD)
    if type(prompt_identity) is not str:
        raise _EnvelopeContractFailure(_UPSTREAM_RESULT_INVALID)
    return prompt_identity, result.reason_codes


def _calculate_envelope_identity(
    artifact: dict[str, object],
) -> str:
    try:
        return record_identity_sha256(
            artifact,
            identity_field=_IDENTITY_FIELD,
            domain=_MMI_RAW_RESPONSE_ENVELOPE_IDENTITY_DOMAIN,
        )
    except MmiCanonicalizationError:
        raise _EnvelopeContractFailure(
            _DERIVED_CONTRACT_INVALID
        ) from None


def _validate_derived_envelope(
    artifact: dict[str, object],
) -> None:
    try:
        validate_artifact_schema(artifact, schema_name=_SCHEMA_NAME)
    except Exception:
        raise _EnvelopeContractFailure(
            _DERIVED_SCHEMA_INVALID
        ) from None
    try:
        calculated = mmi_raw_response_envelope_identity_sha256(
            artifact
        )
    except MmiCanonicalizationError:
        raise _EnvelopeContractFailure(
            _DERIVED_CONTRACT_INVALID
        ) from None
    if calculated != artifact.get(_IDENTITY_FIELD):
        raise _EnvelopeContractFailure(_DERIVED_CONTRACT_INVALID)


def _derive_expected_envelope(
    *,
    grounded_prompt: dict[str, object],
    raw_response_bytes: bytes,
    analyst_visible_evidence_view: dict[str, object],
    evidence_bundle: dict[str, object],
    policy_projection: dict[str, object],
    policy_source: object,
    portfolio_projection: dict[str, object] | None,
    portfolio_source: object,
    run_context: object,
) -> tuple[dict[str, object], tuple[str, ...]]:
    prompt_identity, reason_codes = _require_source_bound_prompt(
        grounded_prompt=grounded_prompt,
        analyst_visible_evidence_view=analyst_visible_evidence_view,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    try:
        raw_response_sha256 = hashlib.sha256(
            raw_response_bytes
        ).hexdigest()
        raw_response_base64 = base64.b64encode(
            raw_response_bytes
        ).decode("ascii")
    except Exception:
        raise _EnvelopeContractFailure(
            _INTERNAL_CONTRACT_FAILURE
        ) from None
    artifact: dict[str, object] = {
        "schema_version": MMI_RAW_RESPONSE_ENVELOPE_SCHEMA_VERSION,
        "artifact_kind": MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND,
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "manual_handoff_required": True,
        _PROMPT_IDENTITY_FIELD: prompt_identity,
        "raw_response_byte_length": len(raw_response_bytes),
        "raw_response_sha256": raw_response_sha256,
        "raw_response_base64": raw_response_base64,
        _IDENTITY_FIELD: _ZERO_SHA256,
    }
    artifact[_IDENTITY_FIELD] = _calculate_envelope_identity(
        artifact
    )
    _validate_derived_envelope(artifact)
    return artifact, reason_codes


def _validate_candidate_envelope(
    candidate: dict[str, object],
    *,
    expected: dict[str, object],
) -> None:
    try:
        validate_artifact_schema(candidate, schema_name=_SCHEMA_NAME)
    except Exception:
        raise _EnvelopeBlocked(_CANDIDATE_SCHEMA_INVALID) from None
    try:
        calculated = mmi_raw_response_envelope_identity_sha256(
            candidate
        )
    except MmiCanonicalizationError as exc:
        if exc.code == _STRUCTURAL_REPRESENTATION_INVALID:
            raise _EnvelopeBlocked(exc.code) from None
        if exc.code in {
            _STRUCTURAL_LENGTH_CONTRADICTION,
            _STRUCTURAL_DIGEST_CONTRADICTION,
            _STRUCTURAL_IDENTITY_CONTRADICTION,
        }:
            raise _EnvelopeContractFailure(exc.code) from None
        raise _EnvelopeContractFailure(
            _CANDIDATE_CONTRACT_INVALID
        ) from None
    if calculated != candidate.get(_IDENTITY_FIELD):
        raise _EnvelopeContractFailure(
            _STRUCTURAL_IDENTITY_CONTRADICTION
        )
    if candidate != expected:
        raise _EnvelopeContractFailure(_SOURCE_FIDELITY_MISMATCH)


def build_mmi_raw_response_envelope(
    *,
    grounded_prompt: Mapping[str, object],
    raw_response_bytes: bytes,
    analyst_visible_evidence_view: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionBuildResult:
    """Build one source-bound, exact-byte, report-only response envelope."""
    try:
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
        exact_raw_response = _require_raw_response_bytes(
            raw_response_bytes
        )
        artifact, reason_codes = _derive_expected_envelope(
            grounded_prompt=prompt_snapshot,
            raw_response_bytes=exact_raw_response,
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
    except _EnvelopeBlocked as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _EnvelopeContractFailure as exc:
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


def validate_mmi_raw_response_envelope(
    *,
    value: Mapping[str, object],
    grounded_prompt: Mapping[str, object],
    raw_response_bytes: bytes,
    analyst_visible_evidence_view: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionValidationResult:
    """Validate one envelope against a source-bound prompt and exact bytes."""
    try:
        candidate = _snapshot_mapping(value)
    except _EnvelopeBlocked:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            _CANDIDATE_SCHEMA_INVALID,
        )
    try:
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
        exact_raw_response = _require_raw_response_bytes(
            raw_response_bytes
        )
        expected, _reason_codes = _derive_expected_envelope(
            grounded_prompt=prompt_snapshot,
            raw_response_bytes=exact_raw_response,
            analyst_visible_evidence_view=view_snapshot,
            evidence_bundle=evidence_snapshot,
            policy_projection=policy_snapshot,
            policy_source=policy_source_snapshot,
            portfolio_projection=portfolio_snapshot,
            portfolio_source=portfolio_source_snapshot,
            run_context=run_context_snapshot,
        )
        _validate_candidate_envelope(candidate, expected=expected)
    except _UpstreamFailure as exc:
        return _validation_result(
            exc.status,
            *exc.reason_codes,
        )
    except _EnvelopeBlocked as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _EnvelopeContractFailure as exc:
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
