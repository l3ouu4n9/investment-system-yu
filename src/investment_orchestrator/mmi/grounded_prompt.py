"""Source-bound construction and validation of the report-only MMI prompt."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.analyst_visible_evidence_view import (
    validate_mmi_analyst_visible_evidence_view,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES,
    MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES,
    _MAXIMUM_GROUNDED_PROMPT_CANONICAL_BYTES,
    _MMI_GROUNDED_PROMPT_ARTIFACT_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
    canonical_json_bytes,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MMI_GROUNDED_PROMPT_ARTIFACT_KIND,
    MMI_GROUNDED_PROMPT_EXPECTED_RESPONSE_SCHEMA_VERSION,
    MMI_GROUNDED_PROMPT_INSTRUCTION_SET_VERSION,
    MMI_GROUNDED_PROMPT_SCHEMA_VERSION,
    MmiCapturedSource,
    MmiPolicyProjectionBuildResult,
    MmiPolicyProjectionValidationResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    _MMI_GROUNDED_PROMPT_BETWEEN_CONTEXT_BINDING_AND_EVIDENCE_LENGTH,
    _MMI_GROUNDED_PROMPT_MANUAL_HANDOFF_REQUIRED,
    _MMI_GROUNDED_PROMPT_PREFIX_BEFORE_CONTEXT_BINDING,
    _MMI_GROUNDED_PROMPT_SUFFIX_AFTER_EVIDENCE,
    mmi_grounded_prompt_artifact_identity_sha256,
    mmi_grounded_prompt_context_binding_sha256,
)


__all__ = (
    "build_mmi_grounded_prompt",
    "validate_mmi_grounded_prompt",
)

_SCHEMA_NAME: Final = "mmi_grounded_prompt_v1.schema.json"
_ZERO_SHA256: Final = "0" * 64

_INPUT_SNAPSHOT_INVALID: Final = (
    "MMI_GROUNDED_PROMPT_INPUT_SNAPSHOT_INVALID"
)
_CANDIDATE_SCHEMA_INVALID: Final = (
    "MMI_GROUNDED_PROMPT_CANDIDATE_SCHEMA_INVALID"
)
_UPSTREAM_RESULT_INVALID: Final = (
    "MMI_GROUNDED_PROMPT_UPSTREAM_RESULT_INVALID"
)
_DERIVED_SCHEMA_INVALID: Final = (
    "MMI_GROUNDED_PROMPT_DERIVED_SCHEMA_INVALID"
)
_DERIVED_CONTRACT_INVALID: Final = (
    "MMI_GROUNDED_PROMPT_DERIVED_CONTRACT_INVALID"
)
_CANDIDATE_CONTRACT_INVALID: Final = (
    "MMI_GROUNDED_PROMPT_CANDIDATE_CONTRACT_INVALID"
)
_CONTEXT_BINDING_INVALID: Final = (
    "MMI_GROUNDED_PROMPT_CONTEXT_BINDING_INVALID"
)
_ARTIFACT_IDENTITY_INVALID: Final = (
    "MMI_GROUNDED_PROMPT_ARTIFACT_IDENTITY_INVALID"
)
_SOURCE_FIDELITY_MISMATCH: Final = (
    "MMI_GROUNDED_PROMPT_SOURCE_FIDELITY_MISMATCH"
)
_RESOURCE_LIMIT_EXCEEDED: Final = (
    "MMI_GROUNDED_PROMPT_RESOURCE_LIMIT_EXCEEDED"
)
_INTERNAL_CONTRACT_FAILURE: Final = (
    "MMI_GROUNDED_PROMPT_INTERNAL_CONTRACT_FAILURE"
)


class _PromptBlocked(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _PromptContractFailure(RuntimeError):
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
            raise _PromptBlocked(_INPUT_SNAPSHOT_INVALID)
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
                        raise _PromptBlocked(_INPUT_SNAPSHOT_INVALID)
                    seen_keys.add(key)
                    snapshot[key] = _snapshot_value(
                        value[key],
                        active_container_ids=active_container_ids,
                    )
            except _PromptBlocked:
                raise
            except Exception:
                raise _PromptBlocked(
                    _INPUT_SNAPSHOT_INVALID
                ) from None
            return snapshot
        finally:
            active_container_ids.remove(container_id)

    if type(value) is list:
        container_id = id(value)
        if container_id in active_container_ids:
            raise _PromptBlocked(_INPUT_SNAPSHOT_INVALID)
        active_container_ids.add(container_id)
        try:
            try:
                materialized = list(value)
            except Exception:
                raise _PromptBlocked(
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
            raise _PromptBlocked(_INPUT_SNAPSHOT_INVALID)
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

    raise _PromptBlocked(_INPUT_SNAPSHOT_INVALID)


def _snapshot_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _PromptBlocked(_INPUT_SNAPSHOT_INVALID)
    snapshot = _snapshot_value(value, active_container_ids=set())
    if type(snapshot) is not dict:
        raise _PromptBlocked(_INPUT_SNAPSHOT_INVALID)
    return snapshot


def _snapshot_if_mapping(value: object) -> object:
    if isinstance(value, Mapping):
        return _snapshot_mapping(value)
    return value


def _require_source_bound_view(
    *,
    analyst_visible_evidence_view: dict[str, object],
    evidence_bundle: dict[str, object],
    policy_projection: dict[str, object],
    policy_source: object,
    portfolio_projection: dict[str, object] | None,
    portfolio_source: object,
    run_context: object,
) -> tuple[str, ...]:
    try:
        result = validate_mmi_analyst_visible_evidence_view(
            value=analyst_visible_evidence_view,
            evidence_bundle=evidence_bundle,
            policy_projection=policy_projection,
            policy_source=policy_source,  # type: ignore[arg-type]
            portfolio_projection=portfolio_projection,
            portfolio_source=portfolio_source,  # type: ignore[arg-type]
            run_context=run_context,  # type: ignore[arg-type]
        )
    except Exception:
        raise _PromptContractFailure(
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
        raise _PromptContractFailure(_UPSTREAM_RESULT_INVALID)
    return result.reason_codes


def _context_binding(
    analyst_visible_evidence_view: dict[str, object],
) -> str:
    view_identity = analyst_visible_evidence_view.get(
        "analyst_visible_evidence_view_identity_sha256"
    )
    context = {
        "analyst_visible_evidence_view_identity_sha256": view_identity,
        "instruction_set_version": (
            MMI_GROUNDED_PROMPT_INSTRUCTION_SET_VERSION
        ),
        "expected_response_schema_version": (
            MMI_GROUNDED_PROMPT_EXPECTED_RESPONSE_SCHEMA_VERSION
        ),
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "manual_handoff_required": (
            _MMI_GROUNDED_PROMPT_MANUAL_HANDOFF_REQUIRED
        ),
    }
    try:
        return mmi_grounded_prompt_context_binding_sha256(context)
    except MmiCanonicalizationError:
        raise _PromptContractFailure(
            _CONTEXT_BINDING_INVALID
        ) from None


def _render_prompt_text(
    *,
    analyst_visible_evidence_view: dict[str, object],
    context_binding: str,
) -> str:
    try:
        evidence_bytes = canonical_json_bytes(
            analyst_visible_evidence_view,
            maximum_bytes=(
                MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES
            ),
        )
        evidence_text = evidence_bytes.decode("ascii")
    except (MmiCanonicalizationError, UnicodeDecodeError):
        raise _PromptContractFailure(
            _DERIVED_CONTRACT_INVALID
        ) from None
    prompt_text = (
        _MMI_GROUNDED_PROMPT_PREFIX_BEFORE_CONTEXT_BINDING
        + context_binding
        + _MMI_GROUNDED_PROMPT_BETWEEN_CONTEXT_BINDING_AND_EVIDENCE_LENGTH
        + str(len(evidence_bytes))
        + "\n"
        + evidence_text
        + _MMI_GROUNDED_PROMPT_SUFFIX_AFTER_EVIDENCE
    )
    try:
        prompt_bytes = prompt_text.encode("ascii")
    except UnicodeEncodeError:
        raise _PromptContractFailure(
            _DERIVED_CONTRACT_INVALID
        ) from None
    if len(prompt_bytes) > MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES:
        raise _PromptBlocked(_RESOURCE_LIMIT_EXCEEDED)
    return prompt_text


def _calculate_artifact_identity(
    artifact: dict[str, object],
) -> str:
    try:
        return record_identity_sha256(
            artifact,
            identity_field=(
                "grounded_prompt_artifact_identity_sha256"
            ),
            domain=_MMI_GROUNDED_PROMPT_ARTIFACT_IDENTITY_DOMAIN,
            maximum_bytes=_MAXIMUM_GROUNDED_PROMPT_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError as exc:
        if exc.code == "MMI_CANONICAL_SIZE_EXCEEDED":
            raise _PromptBlocked(_RESOURCE_LIMIT_EXCEEDED) from None
        raise _PromptContractFailure(
            _ARTIFACT_IDENTITY_INVALID
        ) from None


def _validate_derived_artifact(artifact: dict[str, object]) -> None:
    try:
        validate_artifact_schema(artifact, schema_name=_SCHEMA_NAME)
    except Exception:
        raise _PromptContractFailure(
            _DERIVED_SCHEMA_INVALID
        ) from None
    try:
        canonical_json_bytes(
            artifact,
            maximum_bytes=_MAXIMUM_GROUNDED_PROMPT_CANONICAL_BYTES,
        )
        calculated = mmi_grounded_prompt_artifact_identity_sha256(
            artifact
        )
    except MmiCanonicalizationError as exc:
        if exc.code == "MMI_CANONICAL_SIZE_EXCEEDED":
            raise _PromptBlocked(_RESOURCE_LIMIT_EXCEEDED) from None
        raise _PromptContractFailure(
            _DERIVED_CONTRACT_INVALID
        ) from None
    if calculated != artifact.get(
        "grounded_prompt_artifact_identity_sha256"
    ):
        raise _PromptContractFailure(_DERIVED_CONTRACT_INVALID)


def _derive_expected_artifact(
    *,
    analyst_visible_evidence_view: dict[str, object],
    evidence_bundle: dict[str, object],
    policy_projection: dict[str, object],
    policy_source: object,
    portfolio_projection: dict[str, object] | None,
    portfolio_source: object,
    run_context: object,
) -> tuple[dict[str, object], tuple[str, ...]]:
    reason_codes = _require_source_bound_view(
        analyst_visible_evidence_view=analyst_visible_evidence_view,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    context_binding = _context_binding(
        analyst_visible_evidence_view
    )
    prompt_text = _render_prompt_text(
        analyst_visible_evidence_view=analyst_visible_evidence_view,
        context_binding=context_binding,
    )
    artifact: dict[str, object] = {
        "schema_version": MMI_GROUNDED_PROMPT_SCHEMA_VERSION,
        "artifact_kind": MMI_GROUNDED_PROMPT_ARTIFACT_KIND,
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "analyst_visible_evidence_view_identity_sha256": (
            analyst_visible_evidence_view.get(
                "analyst_visible_evidence_view_identity_sha256"
            )
        ),
        "instruction_set_version": (
            MMI_GROUNDED_PROMPT_INSTRUCTION_SET_VERSION
        ),
        "expected_response_schema_version": (
            MMI_GROUNDED_PROMPT_EXPECTED_RESPONSE_SCHEMA_VERSION
        ),
        "manual_handoff_required": (
            _MMI_GROUNDED_PROMPT_MANUAL_HANDOFF_REQUIRED
        ),
        "prompt_context_binding_sha256": context_binding,
        "prompt_text": prompt_text,
        "grounded_prompt_artifact_identity_sha256": _ZERO_SHA256,
    }
    artifact["grounded_prompt_artifact_identity_sha256"] = (
        _calculate_artifact_identity(artifact)
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
        raise _PromptBlocked(_CANDIDATE_SCHEMA_INVALID) from None

    try:
        canonical_json_bytes(
            candidate,
            maximum_bytes=_MAXIMUM_GROUNDED_PROMPT_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError as exc:
        if exc.code == "MMI_CANONICAL_SIZE_EXCEEDED":
            raise _PromptBlocked(_RESOURCE_LIMIT_EXCEEDED) from None
        raise _PromptContractFailure(
            _CANDIDATE_CONTRACT_INVALID
        ) from None

    calculated_identity = _calculate_artifact_identity(candidate)
    if calculated_identity != candidate.get(
        "grounded_prompt_artifact_identity_sha256"
    ):
        raise _PromptContractFailure(
            _ARTIFACT_IDENTITY_INVALID
        )

    context = {
        "analyst_visible_evidence_view_identity_sha256": candidate.get(
            "analyst_visible_evidence_view_identity_sha256"
        ),
        "instruction_set_version": candidate.get(
            "instruction_set_version"
        ),
        "expected_response_schema_version": candidate.get(
            "expected_response_schema_version"
        ),
        "report_only": candidate.get("report_only"),
        "authority_effect": candidate.get("authority_effect"),
        "manual_handoff_required": candidate.get(
            "manual_handoff_required"
        ),
    }
    try:
        calculated_context = (
            mmi_grounded_prompt_context_binding_sha256(context)
        )
    except MmiCanonicalizationError:
        raise _PromptContractFailure(
            _CONTEXT_BINDING_INVALID
        ) from None
    if calculated_context != candidate.get(
        "prompt_context_binding_sha256"
    ):
        raise _PromptContractFailure(
            _CONTEXT_BINDING_INVALID
        )

    try:
        structural_identity = (
            mmi_grounded_prompt_artifact_identity_sha256(candidate)
        )
    except MmiCanonicalizationError:
        raise _PromptContractFailure(
            _CANDIDATE_CONTRACT_INVALID
        ) from None
    if structural_identity != calculated_identity:
        raise _PromptContractFailure(
            _ARTIFACT_IDENTITY_INVALID
        )
    if candidate != expected:
        raise _PromptContractFailure(_SOURCE_FIDELITY_MISMATCH)


def build_mmi_grounded_prompt(
    *,
    analyst_visible_evidence_view: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionBuildResult:
    """Build one in-memory, source-bound, report-only grounded prompt."""
    try:
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
    except _PromptBlocked as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _PromptContractFailure as exc:
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


def validate_mmi_grounded_prompt(
    *,
    value: Mapping[str, object],
    analyst_visible_evidence_view: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionValidationResult:
    """Validate a candidate against one source-bound expected prompt."""
    try:
        candidate = _snapshot_mapping(value)
    except _PromptBlocked:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            _CANDIDATE_SCHEMA_INVALID,
        )
    try:
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
    except _PromptBlocked as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _PromptContractFailure as exc:
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
