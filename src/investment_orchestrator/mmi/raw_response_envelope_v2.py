"""Exact-byte construction of the dormant report-only MMI R1c-v2 envelope."""

from __future__ import annotations

import base64
from collections.abc import Mapping
import hashlib
from typing import Final, NoReturn

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_CANONICAL_JSON_BYTES,
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    MmiCanonicalizationError,
    _MMI_RAW_RESPONSE_ENVELOPE_V2_IDENTITY_DOMAIN,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND,
    MmiCapturedSource,
    MmiProjectionRunContext,
    _MMI_GROUNDED_PROMPT_MANUAL_HANDOFF_REQUIRED,
    _MMI_RAW_RESPONSE_ENVELOPE_V2_SCHEMA_VERSION,
)
from investment_orchestrator.mmi.grounded_prompt_v2 import (
    MmiGroundedPromptV2Error,
    _build_source_bound_grounded_prompt_v2,
    validate_mmi_grounded_prompt_v2,
)


__all__ = (
    "MmiRawResponseEnvelopeV2Error",
    "build_mmi_raw_response_envelope_v2",
    "validate_mmi_raw_response_envelope_v2",
)

_SCHEMA_NAME: Final = "mmi_raw_response_envelope_v2.schema.json"
_PROMPT_IDENTITY_FIELD: Final = (
    "grounded_prompt_artifact_identity_sha256"
)
_ENVELOPE_IDENTITY_FIELD: Final = (
    "raw_response_envelope_identity_sha256"
)
_ZERO_SHA256: Final = "0" * 64
_ENVELOPE_FIELDS: Final = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "manual_handoff_required",
        _PROMPT_IDENTITY_FIELD,
        "raw_response_byte_length",
        "raw_response_sha256",
        "raw_response_base64",
        _ENVELOPE_IDENTITY_FIELD,
    }
)


class MmiRawResponseEnvelopeV2Error(ValueError):
    """Raised when no valid dormant R1c-v2 artifact can be returned."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise MmiRawResponseEnvelopeV2Error(code)


def _require_exact_raw_response_bytes(value: object) -> bytes:
    if (
        type(value) is not bytes
        or not 1 <= len(value) <= MAXIMUM_MMI_RAW_RESPONSE_BYTES
    ):
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_BYTES_INVALID")
    return value


def _snapshot_value(
    value: object,
    *,
    active_container_ids: set[int],
) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_container_ids:
            _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_INPUT_INVALID")
        active_container_ids.add(container_id)
        try:
            snapshot: dict[str, object] = {}
            try:
                keys = tuple(value.keys())
                if (
                    any(type(key) is not str for key in keys)
                    or len(keys) != len(set(keys))
                ):
                    _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_INPUT_INVALID")
                for key in keys:
                    snapshot[key] = _snapshot_value(
                        value[key],
                        active_container_ids=active_container_ids,
                    )
            except MmiRawResponseEnvelopeV2Error:
                raise
            except Exception:
                _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_INPUT_INVALID")
            return snapshot
        finally:
            active_container_ids.remove(container_id)
    if type(value) is list:
        container_id = id(value)
        if container_id in active_container_ids:
            _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_INPUT_INVALID")
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
    _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_INPUT_INVALID")


def _snapshot_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_INPUT_INVALID")
    snapshot = _snapshot_value(value, active_container_ids=set())
    if type(snapshot) is not dict:
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_INPUT_INVALID")
    return snapshot


def _source_bound_view_and_prompt(
    *,
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> tuple[dict[str, object], dict[str, object]]:
    evidence = _snapshot_mapping(evidence_bundle)
    policy = _snapshot_mapping(policy_projection)
    portfolio = (
        None
        if portfolio_projection is None
        else _snapshot_mapping(portfolio_projection)
    )
    try:
        return _build_source_bound_grounded_prompt_v2(
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=policy_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except MmiRawResponseEnvelopeV2Error:
        raise
    except Exception:
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_PROMPT_INVALID")


def _decoded_envelope_bytes(artifact: Mapping[str, object]) -> bytes:
    encoded = artifact.get("raw_response_base64")
    if type(encoded) is not str:
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_BYTES_INVALID")
    try:
        exact_bytes = base64.b64decode(encoded, validate=True)
    except ValueError:
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_BYTES_INVALID")
    if (
        not 1 <= len(exact_bytes) <= MAXIMUM_MMI_RAW_RESPONSE_BYTES
        or artifact.get("raw_response_byte_length") != len(exact_bytes)
        or artifact.get("raw_response_sha256")
        != hashlib.sha256(exact_bytes).hexdigest()
        or base64.b64encode(exact_bytes).decode("ascii") != encoded
    ):
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_BYTES_INVALID")
    return exact_bytes


def _envelope_identity(artifact: dict[str, object]) -> str:
    try:
        return record_identity_sha256(
            artifact,
            identity_field=_ENVELOPE_IDENTITY_FIELD,
            domain=_MMI_RAW_RESPONSE_ENVELOPE_V2_IDENTITY_DOMAIN,
            maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES,
        )
    except MmiCanonicalizationError:
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_IDENTITY_INVALID")


def _validated_envelope_context(
    value: object,
    *,
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> tuple[
    dict[str, object],
    bytes,
    dict[str, object],
    dict[str, object],
]:
    artifact = _snapshot_mapping(value)
    try:
        validate_artifact_schema(artifact, schema_name=_SCHEMA_NAME)
    except Exception:
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_SCHEMA_INVALID")
    if (
        set(artifact) != _ENVELOPE_FIELDS
        or artifact.get("schema_version")
        != _MMI_RAW_RESPONSE_ENVELOPE_V2_SCHEMA_VERSION
        or artifact.get("artifact_kind")
        != MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND
        or artifact.get("report_only") is not True
        or artifact.get("authority_effect") != AUTHORITY_EFFECT_NONE
        or artifact.get("manual_handoff_required")
        is not _MMI_GROUNDED_PROMPT_MANUAL_HANDOFF_REQUIRED
    ):
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_CONTRACT_INVALID")
    view, prompt = _source_bound_view_and_prompt(
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    if artifact.get(_PROMPT_IDENTITY_FIELD) != prompt.get(
        _PROMPT_IDENTITY_FIELD
    ):
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_PROMPT_INVALID")
    exact_bytes = _decoded_envelope_bytes(artifact)
    if artifact.get(_ENVELOPE_IDENTITY_FIELD) != _envelope_identity(
        artifact
    ):
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_IDENTITY_INVALID")
    return artifact, exact_bytes, view, prompt


def _validated_envelope_snapshot(
    value: object,
    *,
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    artifact, _exact_bytes, _view, _prompt = _validated_envelope_context(
        value,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    return artifact


def build_mmi_raw_response_envelope_v2(
    *,
    grounded_prompt: Mapping[str, object],
    raw_response_bytes: bytes,
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    """Bind exact operator-supplied bytes to one validated G2 artifact."""
    try:
        prompt = validate_mmi_grounded_prompt_v2(
            value=grounded_prompt,
            evidence_bundle=evidence_bundle,
            policy_projection=policy_projection,
            policy_source=policy_source,
            portfolio_projection=portfolio_projection,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except MmiGroundedPromptV2Error:
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_PROMPT_INVALID")
    exact_bytes = _require_exact_raw_response_bytes(raw_response_bytes)
    raw_response_sha256 = hashlib.sha256(exact_bytes).hexdigest()
    raw_response_base64 = base64.b64encode(exact_bytes).decode("ascii")
    artifact: dict[str, object] = {
        "schema_version": _MMI_RAW_RESPONSE_ENVELOPE_V2_SCHEMA_VERSION,
        "artifact_kind": MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND,
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "manual_handoff_required": (
            _MMI_GROUNDED_PROMPT_MANUAL_HANDOFF_REQUIRED
        ),
        _PROMPT_IDENTITY_FIELD: prompt[_PROMPT_IDENTITY_FIELD],
        "raw_response_byte_length": len(exact_bytes),
        "raw_response_sha256": raw_response_sha256,
        "raw_response_base64": raw_response_base64,
        _ENVELOPE_IDENTITY_FIELD: _ZERO_SHA256,
    }
    artifact[_ENVELOPE_IDENTITY_FIELD] = _envelope_identity(artifact)
    return _validated_envelope_snapshot(
        artifact,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )


def validate_mmi_raw_response_envelope_v2(
    *,
    value: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    """Return a stable snapshot only for one source-bound R1c-v2."""
    return _validated_envelope_snapshot(
        value,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
