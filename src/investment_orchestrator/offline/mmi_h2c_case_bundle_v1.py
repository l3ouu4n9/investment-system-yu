"""Report-only envelope for the six otherwise-lost H2c case mappings.

A foreground H2c capture builds ``grounded_prompt`` (G2), ``raw_response_envelope``
(R1), ``validated_grounded_analysis_response`` (R2),
``legacy_step1_compatibility_candidate`` (H1) and the two source records, then
drops them when the process exits.  Without them the receipt portable-evidence
validator cannot run on a real prospective case at all.  This owner freezes
those exact mappings into one closed envelope with its own persistent identity.

``SLOT_DISCRIMINATOR_VALIDATION`` is the whole of this owner's scope: the closed
top-level field set, the report-only constants, one ``schema_version`` (and, for
the two source records, one ``source_role``) per slot, the canonical ceiling and
the envelope's own self-identity.  It deliberately does **not** validate nested
artifact bodies.  A nested mapping carrying the correct discriminator but an
otherwise invalid body may pass here and must be rejected later by
``validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence``,
which remains the sole owner of complete nested schemas, persistent identities,
cross-artifact links and exact-byte equality.

It has no filesystem, source-capture, run-context, provider, network, scheduler,
publication, permission, gate, order or broker capability.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Final, Literal, NoReturn

from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_CANONICAL_JSON_BYTES,
    MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES,
    MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES,
    MmiCanonicalizationError,
    _MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_IDENTITY_DOMAIN,
    canonical_json_bytes,
    record_identity_sha256,
)


__all__ = (
    "MmiH2cCaseEvidenceBundleV1Error",
    "validate_mmi_h2c_case_evidence_bundle_v1",
)

_BUNDLE_ERROR: Final = "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID"
_BUNDLE_SCHEMA: Final = "mmi_h2c_case_evidence_bundle_v1.schema.json"
_IDENTITY_FIELD: Final = "case_evidence_bundle_identity_sha256"
_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES: Final = 8_192

BundleErrorCode = Literal["MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID"]


class MmiH2cCaseEvidenceBundleV1Error(ValueError):
    """Raised when the case evidence bundle envelope is invalid."""

    code: BundleErrorCode

    def __init__(self, code: BundleErrorCode) -> None:
        if code != _BUNDLE_ERROR:
            raise TypeError("unsupported H2c case evidence bundle error code")
        super().__init__(code)
        self.code = code


def _fail() -> NoReturn:
    raise MmiH2cCaseEvidenceBundleV1Error(_BUNDLE_ERROR) from None


def _snapshot_member(
    value: Mapping[str, object],
    *,
    maximum_bytes: int,
) -> dict[str, object]:
    """Return one bounded snapshot detached from the caller's mapping."""
    if not isinstance(value, Mapping):
        _fail()
    try:
        encoded = canonical_json_bytes(value, maximum_bytes=maximum_bytes)
    except MmiCanonicalizationError:
        _fail()
    parsed = json.loads(encoded)
    if type(parsed) is not dict:
        _fail()
    return parsed


def validate_mmi_h2c_case_evidence_bundle_v1(
    *,
    bundle: Mapping[str, object],
) -> None:
    """Apply only ``SLOT_DISCRIMINATOR_VALIDATION`` to one bundle envelope."""
    if not isinstance(bundle, Mapping):
        _fail()
    try:
        encoded = canonical_json_bytes(
            bundle,
            maximum_bytes=(
                MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail()
    parsed = json.loads(encoded)
    if type(parsed) is not dict:
        _fail()
    try:
        validate_artifact_schema(parsed, schema_name=_BUNDLE_SCHEMA)
    except ArtifactSchemaError:
        _fail()
    try:
        expected = record_identity_sha256(
            parsed,
            identity_field=_IDENTITY_FIELD,
            domain=_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_IDENTITY_DOMAIN,
            maximum_bytes=(
                MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail()
    if parsed.get(_IDENTITY_FIELD) != expected:
        _fail()


def _build_mmi_h2c_case_evidence_bundle_v1(
    *,
    grounded_prompt: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    legacy_step1_compatibility_candidate: Mapping[str, object],
    strategy_settings_source_record: Mapping[str, object],
    portfolio_snapshot_source_record: Mapping[str, object],
) -> dict[str, object]:
    """Freeze six exact live mappings into one validated detached envelope."""
    bundle: dict[str, object] = {
        "schema_version": "mmi_h2c_case_evidence_bundle_v1",
        "artifact_kind": "MMI_H2C_CASE_EVIDENCE_BUNDLE",
        "report_only": True,
        "authority_effect": "NONE",
        "grounded_prompt": _snapshot_member(
            grounded_prompt,
            maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES,
        ),
        "raw_response_envelope": _snapshot_member(
            raw_response_envelope,
            maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES,
        ),
        "validated_grounded_analysis_response": _snapshot_member(
            validated_grounded_analysis_response,
            maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES,
        ),
        "legacy_step1_compatibility_candidate": _snapshot_member(
            legacy_step1_compatibility_candidate,
            maximum_bytes=(
                MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES
            ),
        ),
        "strategy_settings_source_record": _snapshot_member(
            strategy_settings_source_record,
            maximum_bytes=_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES,
        ),
        "portfolio_snapshot_source_record": _snapshot_member(
            portfolio_snapshot_source_record,
            maximum_bytes=_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES,
        ),
    }
    try:
        bundle[_IDENTITY_FIELD] = record_identity_sha256(
            bundle,
            identity_field=_IDENTITY_FIELD,
            domain=_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_IDENTITY_DOMAIN,
            maximum_bytes=(
                MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail()
    validate_mmi_h2c_case_evidence_bundle_v1(bundle=bundle)
    return bundle
