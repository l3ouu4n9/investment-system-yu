"""Dormant report-only identity receipt for one persisted H2c case.

The receipt binds only durable prepared-case, response, source, bundle, prompt,
and comparison identities.  It does not claim live-process continuity,
authorship, origin, transport, permission, publication, order, or execution
authority and has no workflow or filesystem behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
from typing import Final, Literal, NoReturn

from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MmiCanonicalizationError,
    canonical_json_bytes,
    record_identity_sha256,
)


__all__ = (
    "MmiH2cDualSidePersistedCaseReceiptV2Error",
    "validate_mmi_h2c_dual_side_persisted_case_receipt_v2",
)

_ERROR: Final = "MMI_H2C_PERSISTED_CASE_RECEIPT_V2_INVALID"
_SCHEMA: Final = (
    "mmi_h2c_dual_side_persisted_case_receipt_v2.schema.json"
)
_IDENTITY_FIELD: Final = "receipt_identity_sha256"
_IDENTITY_DOMAIN: Final = (
    b"mmi_h2c_dual_side_persisted_case_receipt_v2\0"
)
# All closed constants, the canonical six-microsecond timestamp, and every
# SHA-256 field have fixed lengths, so every valid canonical receipt is exact.
_EXACT_CANONICAL_BYTES: Final = 1_320
_ZERO_SHA256: Final = "0" * 64

_PersistedReceiptErrorCode = Literal[
    "MMI_H2C_PERSISTED_CASE_RECEIPT_V2_INVALID"
]


class MmiH2cDualSidePersistedCaseReceiptV2Error(ValueError):
    """Raised when a persisted-case receipt v2 is invalid."""

    code: _PersistedReceiptErrorCode

    def __init__(self, code: _PersistedReceiptErrorCode) -> None:
        if code != _ERROR:
            raise TypeError("unsupported H2c persisted-case receipt error code")
        super().__init__(code)
        self.code = code


def _fail() -> NoReturn:
    raise MmiH2cDualSidePersistedCaseReceiptV2Error(_ERROR) from None


def _require_canonical_timestamp(value: object) -> None:
    if type(value) is not str:
        _fail()
    try:
        observed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        _fail()
    if observed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
        _fail()


def _validate_receipt_snapshot(
    *,
    value: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail()
    try:
        encoded = canonical_json_bytes(
            value,
            maximum_bytes=_EXACT_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        _fail()
    if len(encoded) != _EXACT_CANONICAL_BYTES:
        _fail()
    parsed = json.loads(encoded)
    if type(parsed) is not dict:
        _fail()
    try:
        validate_artifact_schema(parsed, schema_name=_SCHEMA)
    except ArtifactSchemaError:
        _fail()
    _require_canonical_timestamp(parsed.get("evaluation_timestamp_utc"))
    try:
        expected = record_identity_sha256(
            parsed,
            identity_field=_IDENTITY_FIELD,
            domain=_IDENTITY_DOMAIN,
            maximum_bytes=_EXACT_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        _fail()
    if parsed.get(_IDENTITY_FIELD) != expected:
        _fail()
    return parsed


def _build_mmi_h2c_dual_side_persisted_case_receipt_v2(
    *,
    evaluation_timestamp_utc: str,
    prepared_case_identity_sha256: str,
    case_evidence_bundle_identity_sha256: str,
    comparison_report_identity_sha256: str,
    strategy_settings_source_record_identity_sha256: str,
    portfolio_snapshot_source_record_identity_sha256: str,
    h1_prompt_sha256: str,
    legacy_prompt_sha256: str,
    h1_operator_supplied_response_sha256: str,
    legacy_operator_supplied_response_sha256: str,
) -> dict[str, object]:
    """Build one dormant receipt from already-validated persistent links."""
    receipt: dict[str, object] = {
        "schema_version": (
            "mmi_h2c_dual_side_persisted_case_receipt_v2"
        ),
        "artifact_kind": "MMI_H2C_DUAL_SIDE_PERSISTED_CASE_RECEIPT",
        "consumption_contract_version": (
            "mmi_h2c_persisted_case_consume_v1"
        ),
        "report_only": True,
        "authority_effect": "NONE",
        "evaluation_timestamp_utc": evaluation_timestamp_utc,
        "prepared_case_identity_sha256": prepared_case_identity_sha256,
        "case_evidence_bundle_identity_sha256": (
            case_evidence_bundle_identity_sha256
        ),
        "comparison_report_identity_sha256": (
            comparison_report_identity_sha256
        ),
        "strategy_settings_source_record_identity_sha256": (
            strategy_settings_source_record_identity_sha256
        ),
        "portfolio_snapshot_source_record_identity_sha256": (
            portfolio_snapshot_source_record_identity_sha256
        ),
        "h1_prompt_sha256": h1_prompt_sha256,
        "legacy_prompt_sha256": legacy_prompt_sha256,
        "h1_operator_supplied_response_sha256": (
            h1_operator_supplied_response_sha256
        ),
        "legacy_operator_supplied_response_sha256": (
            legacy_operator_supplied_response_sha256
        ),
        _IDENTITY_FIELD: _ZERO_SHA256,
    }
    try:
        receipt[_IDENTITY_FIELD] = record_identity_sha256(
            receipt,
            identity_field=_IDENTITY_FIELD,
            domain=_IDENTITY_DOMAIN,
            maximum_bytes=_EXACT_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        _fail()
    return _validate_receipt_snapshot(value=receipt)


def validate_mmi_h2c_dual_side_persisted_case_receipt_v2(
    *,
    receipt: Mapping[str, object],
) -> None:
    """Validate the closed receipt, exact canonical size, and identity."""
    _validate_receipt_snapshot(value=receipt)
