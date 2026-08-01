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
    validate_mmi_grounded_prompt_v2,
)


__all__ = (
    "MmiRawResponseEnvelopeV2Error",
    "build_mmi_raw_response_envelope_v2",
)

_SCHEMA_NAME: Final = "mmi_raw_response_envelope_v2.schema.json"
_PROMPT_IDENTITY_FIELD: Final = (
    "grounded_prompt_artifact_identity_sha256"
)
_ENVELOPE_IDENTITY_FIELD: Final = (
    "raw_response_envelope_identity_sha256"
)
_ZERO_SHA256: Final = "0" * 64


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
    try:
        validate_artifact_schema(artifact, schema_name=_SCHEMA_NAME)
    except Exception:
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_SCHEMA_INVALID")
    if artifact[_ENVELOPE_IDENTITY_FIELD] != _envelope_identity(artifact):
        _fail("MMI_RAW_RESPONSE_ENVELOPE_V2_IDENTITY_INVALID")
    return artifact
