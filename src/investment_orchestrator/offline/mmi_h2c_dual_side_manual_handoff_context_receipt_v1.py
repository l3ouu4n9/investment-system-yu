"""Portable structural validation for the report-only H2c capture receipt.

``PORTABLE_STRUCTURAL_VALIDATION`` checks only closed schemas, persistent
identities, explicit cross-links, supplied archived bytes, and deterministic
legacy prompt reconstruction.  It does not recreate live MMI seals, read
``inputs/current``, authenticate a source or provider, or establish admission,
permission, publication, order, or execution authority.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from datetime import datetime
import hashlib
import io
import json
from typing import Final, Literal, NoReturn

import yaml

from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.llm.legacy_step1_prompt_compiler import (
    compile_legacy_step1_prompt_text,
    derive_legacy_approved_extended_etf_json,
)
from investment_orchestrator.llm.manual_output import PromptRenderError
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_CANONICAL_JSON_BYTES,
    MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES,
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    MAX_MMI_GROUNDED_ANALYSIS_RESPONSE_V2_CANONICAL_BYTES,
    MAX_MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_CANONICAL_BYTES,
    MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES,
    MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES,
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
    _MMI_GROUNDED_PROMPT_V2_ARTIFACT_IDENTITY_DOMAIN,
    _MMI_GROUNDED_PROMPT_V2_CONTEXT_BINDING_DOMAIN,
    _MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_IDENTITY_DOMAIN,
    _MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_IDENTITY_DOMAIN,
    _MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_IDENTITY_DOMAIN,
    _MMI_RAW_RESPONSE_ENVELOPE_V2_IDENTITY_DOMAIN,
    _MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_V2_IDENTITY_DOMAIN,
    canonical_json_bytes,
    domain_separated_sha256,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    MMI_SOURCE_CATALOG,
    MmiSourceRole,
)
from investment_orchestrator.validators.strategy_settings import (
    StrategySettingsValidationError,
    parse_strategy_settings_text,
)


__all__ = (
    "MmiH2cDualSideManualHandoffContextReceiptV1Error",
    "validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1",
    "validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence",
)

_RECEIPT_ERROR: Final = "MMI_H2C_RECEIPT_V1_INVALID"
_PORTABLE_ERROR: Final = "MMI_H2C_PORTABLE_EVIDENCE_INVALID"
_RECEIPT_SCHEMA: Final = (
    "mmi_h2c_dual_side_manual_handoff_context_receipt_v1.schema.json"
)
_G2_SCHEMA: Final = "mmi_grounded_prompt_v2.schema.json"
_R1_SCHEMA: Final = "mmi_raw_response_envelope_v2.schema.json"
_R2_SCHEMA: Final = (
    "mmi_validated_grounded_analysis_response_v2.schema.json"
)
_H1_SCHEMA: Final = (
    "mmi_legacy_step1_compatibility_candidate_v1.schema.json"
)
_H2_SCHEMA: Final = "mmi_legacy_step1_comparison_report_v1.schema.json"
_SOURCE_SCHEMA: Final = "mmi_source_record_v1.schema.json"

_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES: Final = 8_192
_MAXIMUM_LEGACY_TEMPLATE_BYTES: Final = 262_144
_MAXIMUM_LEGACY_RESPONSE_BYTES: Final = 262_144
_MAXIMUM_LEGACY_SETTINGS_CANONICAL_BYTES: Final = 262_144
_MAXIMUM_LEGACY_PROMPT_BYTES: Final = 3_170_307

_G2_CONTEXT_FIELDS: Final = frozenset(
    {
        "analyst_visible_evidence_view_identity_sha256",
        "instruction_set_version",
        "expected_response_schema_version",
        "report_only",
        "authority_effect",
        "manual_handoff_required",
    }
)
_APPROVED_LIST_VALUE_ERRORS: Final = frozenset(
    {
        "Missing required field 'user_approved_extended_etf_static_list' in "
        "inputs/current/strategy_settings.yaml",
        "inputs/current/strategy_settings.yaml field "
        "'user_approved_extended_etf_static_list' must be a list.",
        "inputs/current/strategy_settings.yaml field "
        "'user_approved_extended_etf_static_list' must contain only strings.",
    }
)

ReceiptErrorCode = Literal[
    "MMI_H2C_RECEIPT_V1_INVALID",
    "MMI_H2C_PORTABLE_EVIDENCE_INVALID",
]


class MmiH2cDualSideManualHandoffContextReceiptV1Error(ValueError):
    """Raised when the receipt or supplied portable evidence is invalid."""

    code: ReceiptErrorCode

    def __init__(self, code: ReceiptErrorCode) -> None:
        if code not in {_RECEIPT_ERROR, _PORTABLE_ERROR}:
            raise TypeError("unsupported H2c receipt error code")
        super().__init__(code)
        self.code = code


def _fail(code: ReceiptErrorCode) -> NoReturn:
    raise MmiH2cDualSideManualHandoffContextReceiptV1Error(code)


def _portable_fail() -> NoReturn:
    _fail(_PORTABLE_ERROR)


def _snapshot_mapping(
    value: Mapping[str, object],
    *,
    maximum_bytes: int,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _portable_fail()
    try:
        encoded = canonical_json_bytes(value, maximum_bytes=maximum_bytes)
    except MmiCanonicalizationError:
        _portable_fail()
    parsed = json.loads(encoded)
    if type(parsed) is not dict:
        _portable_fail()
    return parsed


def _require_schema(value: dict[str, object], schema_name: str) -> None:
    try:
        validate_artifact_schema(value, schema_name=schema_name)
    except ArtifactSchemaError:
        _portable_fail()


def _require_record_identity(
    value: dict[str, object],
    *,
    identity_field: str,
    domain: bytes,
    maximum_bytes: int,
) -> None:
    try:
        expected = record_identity_sha256(
            value,
            identity_field=identity_field,
            domain=domain,
            maximum_bytes=maximum_bytes,
        )
    except MmiCanonicalizationError:
        _portable_fail()
    if value.get(identity_field) != expected:
        _portable_fail()


def _validate_portable_grounded_prompt_v2(
    *,
    value: Mapping[str, object],
) -> dict[str, object]:
    artifact = _snapshot_mapping(
        value,
        maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES,
    )
    _require_schema(artifact, _G2_SCHEMA)
    context = {field: artifact[field] for field in _G2_CONTEXT_FIELDS}
    try:
        expected_context = domain_separated_sha256(
            _MMI_GROUNDED_PROMPT_V2_CONTEXT_BINDING_DOMAIN,
            context,
            maximum_bytes=512,
        )
    except MmiCanonicalizationError:
        _portable_fail()
    if artifact.get("prompt_context_binding_sha256") != expected_context:
        _portable_fail()
    prompt_text = artifact.get("prompt_text")
    if type(prompt_text) is not str or not prompt_text:
        _portable_fail()
    try:
        prompt_bytes = prompt_text.encode("utf-8")
    except UnicodeEncodeError:
        _portable_fail()
    if len(prompt_bytes) > MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES:
        _portable_fail()
    _require_record_identity(
        artifact,
        identity_field="grounded_prompt_artifact_identity_sha256",
        domain=_MMI_GROUNDED_PROMPT_V2_ARTIFACT_IDENTITY_DOMAIN,
        maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES,
    )
    return artifact


def _validate_portable_raw_response_envelope_v2(
    *,
    value: Mapping[str, object],
) -> tuple[dict[str, object], bytes]:
    artifact = _snapshot_mapping(
        value,
        maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES,
    )
    _require_schema(artifact, _R1_SCHEMA)
    encoded = artifact.get("raw_response_base64")
    if type(encoded) is not str:
        _portable_fail()
    try:
        exact_bytes = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        _portable_fail()
    if (
        not 1 <= len(exact_bytes) <= MAXIMUM_MMI_RAW_RESPONSE_BYTES
        or base64.b64encode(exact_bytes).decode("ascii") != encoded
        or artifact.get("raw_response_byte_length") != len(exact_bytes)
        or artifact.get("raw_response_sha256")
        != hashlib.sha256(exact_bytes).hexdigest()
    ):
        _portable_fail()
    _require_record_identity(
        artifact,
        identity_field="raw_response_envelope_identity_sha256",
        domain=_MMI_RAW_RESPONSE_ENVELOPE_V2_IDENTITY_DOMAIN,
        maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES,
    )
    return artifact, exact_bytes


def _validate_portable_validated_grounded_analysis_response_v2(
    *,
    value: Mapping[str, object],
) -> dict[str, object]:
    artifact = _snapshot_mapping(
        value,
        maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES,
    )
    _require_schema(artifact, _R2_SCHEMA)
    try:
        canonical_json_bytes(
            artifact["response_payload"],
            maximum_bytes=(
                MAX_MMI_GROUNDED_ANALYSIS_RESPONSE_V2_CANONICAL_BYTES
            ),
        )
    except (KeyError, MmiCanonicalizationError):
        _portable_fail()
    _require_record_identity(
        artifact,
        identity_field=(
            "validated_grounded_analysis_response_identity_sha256"
        ),
        domain=(
            _MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_V2_IDENTITY_DOMAIN
        ),
        maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES,
    )
    return artifact


def _validate_portable_legacy_step1_compatibility_candidate_v1(
    *,
    value: Mapping[str, object],
) -> dict[str, object]:
    artifact = _snapshot_mapping(
        value,
        maximum_bytes=(
            MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES
        ),
    )
    _require_schema(artifact, _H1_SCHEMA)
    _require_record_identity(
        artifact,
        identity_field=(
            "legacy_step1_compatibility_candidate_identity_sha256"
        ),
        domain=(
            _MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_IDENTITY_DOMAIN
        ),
        maximum_bytes=(
            MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES
        ),
    )
    return artifact


def _validate_portable_legacy_step1_comparison_report_v1(
    *,
    value: Mapping[str, object],
) -> dict[str, object]:
    artifact = _snapshot_mapping(
        value,
        maximum_bytes=(
            MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES
        ),
    )
    _require_schema(artifact, _H2_SCHEMA)
    _require_record_identity(
        artifact,
        identity_field="comparison_report_identity_sha256",
        domain=_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_IDENTITY_DOMAIN,
        maximum_bytes=(
            MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES
        ),
    )
    return artifact


def _validate_portable_source_record_v1(
    *,
    value: Mapping[str, object],
    expected_role: MmiSourceRole,
    archived_source_bytes: bytes,
) -> dict[str, object]:
    if (
        type(expected_role) is not MmiSourceRole
        or type(archived_source_bytes) is not bytes
    ):
        _portable_fail()
    spec = MMI_SOURCE_CATALOG[expected_role]
    if not 1 <= len(archived_source_bytes) <= spec.maximum_bytes:
        _portable_fail()
    artifact = _snapshot_mapping(
        value,
        maximum_bytes=_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES,
    )
    _require_schema(artifact, _SOURCE_SCHEMA)
    observed = hashlib.sha256(archived_source_bytes).hexdigest()
    if (
        artifact.get("source_role") != expected_role.value
        or artifact.get("source_id") != spec.source_id
        or artifact.get("repository_relative_locator")
        != str(spec.repository_relative_locator)
        or artifact.get("maximum_bytes") != spec.maximum_bytes
        or artifact.get("observed_size_bytes") != len(archived_source_bytes)
        or artifact.get("expected_sha256") != observed
        or artifact.get("observed_sha256") != observed
        or artifact.get("content_binding_status") != "EXPECTED_SHA256_MATCHED"
        or artifact.get("operator_origin_authentication") != "NOT_ESTABLISHED"
        or artifact.get("stable_read_status") != "STABLE_BEFORE_AND_AFTER"
        or artifact.get("regular_file_status") != "REGULAR_FILE"
        or artifact.get("authority_effect") != "NONE"
    ):
        _portable_fail()
    _require_record_identity(
        artifact,
        identity_field="source_record_identity_sha256",
        domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        maximum_bytes=_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES,
    )
    return artifact


def _validate_receipt_snapshot(
    *,
    value: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail(_RECEIPT_ERROR)
    try:
        encoded = canonical_json_bytes(
            value,
            maximum_bytes=(
                MAX_MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail(_RECEIPT_ERROR)
    if len(encoded) != (
        MAX_MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_CANONICAL_BYTES
    ):
        _fail(_RECEIPT_ERROR)
    parsed = json.loads(encoded)
    if type(parsed) is not dict:
        _fail(_RECEIPT_ERROR)
    try:
        validate_artifact_schema(parsed, schema_name=_RECEIPT_SCHEMA)
    except ArtifactSchemaError:
        _fail(_RECEIPT_ERROR)
    timestamp = parsed.get("evaluation_timestamp_utc")
    if type(timestamp) is not str:
        _fail(_RECEIPT_ERROR)
    try:
        observed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        _fail(_RECEIPT_ERROR)
    if observed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != timestamp:
        _fail(_RECEIPT_ERROR)
    try:
        expected = record_identity_sha256(
            parsed,
            identity_field="receipt_identity_sha256",
            domain=(
                _MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_IDENTITY_DOMAIN
            ),
            maximum_bytes=(
                MAX_MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail(_RECEIPT_ERROR)
    if parsed.get("receipt_identity_sha256") != expected:
        _fail(_RECEIPT_ERROR)
    return parsed


def _nested_dict(value: dict[str, object], field: str) -> dict[str, object]:
    nested = value.get(field)
    if type(nested) is not dict:
        _portable_fail()
    return nested


def _validate_portable_artifact_links(
    *,
    receipt: dict[str, object],
    comparison_report: dict[str, object],
    legacy_step1_compatibility_candidate: dict[str, object],
    validated_grounded_analysis_response: dict[str, object],
    raw_response_envelope: dict[str, object],
    grounded_prompt: dict[str, object],
    decoded_h1_response_bytes: bytes,
    archived_h1_prompt_bytes: bytes,
    archived_h1_response_bytes: bytes,
    archived_legacy_response_bytes: bytes,
    strategy_settings_source_record: dict[str, object],
    portfolio_snapshot_source_record: dict[str, object],
) -> None:
    h2_provenance = _nested_dict(comparison_report, "provenance")
    h1_provenance = _nested_dict(
        legacy_step1_compatibility_candidate,
        "provenance",
    )
    prompt_text = grounded_prompt.get("prompt_text")
    if type(prompt_text) is not str:
        _portable_fail()
    try:
        prompt_bytes = prompt_text.encode("utf-8")
    except UnicodeEncodeError:
        _portable_fail()
    if (
        receipt.get("comparison_report_identity_sha256")
        != comparison_report.get("comparison_report_identity_sha256")
        or h2_provenance.get(
            "legacy_step1_compatibility_candidate_identity_sha256"
        )
        != legacy_step1_compatibility_candidate.get(
            "legacy_step1_compatibility_candidate_identity_sha256"
        )
        or h1_provenance.get(
            "validated_grounded_analysis_response_identity_sha256"
        )
        != validated_grounded_analysis_response.get(
            "validated_grounded_analysis_response_identity_sha256"
        )
        or validated_grounded_analysis_response.get(
            "raw_response_envelope_identity_sha256"
        )
        != raw_response_envelope.get("raw_response_envelope_identity_sha256")
        or raw_response_envelope.get(
            "grounded_prompt_artifact_identity_sha256"
        )
        != grounded_prompt.get("grounded_prompt_artifact_identity_sha256")
        or prompt_bytes != archived_h1_prompt_bytes
        or decoded_h1_response_bytes != archived_h1_response_bytes
        or h2_provenance.get("legacy_raw_bytes_sha256")
        != hashlib.sha256(archived_legacy_response_bytes).hexdigest()
        or receipt.get("strategy_settings_source_record_identity_sha256")
        != strategy_settings_source_record.get(
            "source_record_identity_sha256"
        )
        or receipt.get("portfolio_snapshot_source_record_identity_sha256")
        != portfolio_snapshot_source_record.get(
            "source_record_identity_sha256"
        )
    ):
        _portable_fail()


def _legacy_text(exact_bytes: bytes) -> str:
    try:
        return io.TextIOWrapper(
            io.BytesIO(exact_bytes),
            encoding="utf-8",
            errors="strict",
            newline=None,
        ).read()
    except UnicodeDecodeError:
        _portable_fail()


def _approved_list_json(settings_text: str) -> str:
    try:
        return derive_legacy_approved_extended_etf_json(
            strategy_settings_text=settings_text,
        )
    except (StrategySettingsValidationError, yaml.YAMLError):
        _portable_fail()
    except ValueError as exc:
        if str(exc) in _APPROVED_LIST_VALUE_ERRORS:
            _portable_fail()
        raise


def _legacy_settings_mapping(settings_text: str) -> dict[str, object]:
    try:
        value = parse_strategy_settings_text(settings_text)
    except (StrategySettingsValidationError, yaml.YAMLError):
        _portable_fail()
    if type(value) is not dict:
        _portable_fail()
    return value


def _legacy_canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _portable_fail()
    if len(encoded) > _MAXIMUM_LEGACY_SETTINGS_CANONICAL_BYTES:
        _portable_fail()
    return encoded


def _validate_portable_legacy_prompt_reconstruction(
    *,
    receipt: dict[str, object],
    comparison_report: dict[str, object],
    archived_strategy_settings_bytes: bytes,
    archived_portfolio_snapshot_bytes: bytes,
    archived_legacy_prompt_template_bytes: bytes,
    archived_legacy_prompt_bytes: bytes,
) -> None:
    settings_text = _legacy_text(archived_strategy_settings_bytes)
    portfolio_text = _legacy_text(archived_portfolio_snapshot_bytes)
    template_text = _legacy_text(archived_legacy_prompt_template_bytes)
    approved = _approved_list_json(settings_text)
    try:
        reconstructed = compile_legacy_step1_prompt_text(
            template_text=template_text,
            strategy_settings_text=settings_text,
            portfolio_snapshot_text=portfolio_text,
            approved_extended_etf_json=approved,
        ).encode("utf-8")
    except PromptRenderError:
        _portable_fail()
    settings = _legacy_settings_mapping(settings_text)
    h2_provenance = _nested_dict(comparison_report, "provenance")
    if (
        reconstructed != archived_legacy_prompt_bytes
        or receipt.get("legacy_prompt_template_sha256")
        != hashlib.sha256(archived_legacy_prompt_template_bytes).hexdigest()
        or receipt.get("legacy_prompt_sha256")
        != hashlib.sha256(archived_legacy_prompt_bytes).hexdigest()
        or h2_provenance.get("legacy_strategy_settings_canonical_sha256")
        != hashlib.sha256(_legacy_canonical_bytes(settings)).hexdigest()
    ):
        _portable_fail()


def validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1(
    *,
    receipt: Mapping[str, object],
) -> None:
    """Validate the receipt's closed schema, exact size, and self-identity."""
    _validate_receipt_snapshot(value=receipt)


def validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence(
    *,
    receipt: Mapping[str, object],
    comparison_report: Mapping[str, object],
    legacy_step1_compatibility_candidate: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    grounded_prompt: Mapping[str, object],
    archived_h1_prompt_bytes: bytes,
    archived_h1_response_bytes: bytes,
    archived_legacy_response_bytes: bytes,
    archived_strategy_settings_bytes: bytes,
    strategy_settings_source_record: Mapping[str, object],
    archived_portfolio_snapshot_bytes: bytes,
    portfolio_snapshot_source_record: Mapping[str, object],
    archived_legacy_prompt_template_bytes: bytes,
    archived_legacy_prompt_bytes: bytes,
) -> None:
    """Apply only ``PORTABLE_STRUCTURAL_VALIDATION`` to supplied evidence."""
    byte_limits = (
        (archived_h1_prompt_bytes, 1, MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES),
        (archived_h1_response_bytes, 1, MAXIMUM_MMI_RAW_RESPONSE_BYTES),
        (archived_legacy_response_bytes, 0, _MAXIMUM_LEGACY_RESPONSE_BYTES),
        (archived_strategy_settings_bytes, 1, 262_144),
        (archived_portfolio_snapshot_bytes, 1, 1_048_576),
        (
            archived_legacy_prompt_template_bytes,
            1,
            _MAXIMUM_LEGACY_TEMPLATE_BYTES,
        ),
        (archived_legacy_prompt_bytes, 1, _MAXIMUM_LEGACY_PROMPT_BYTES),
    )
    if any(
        type(value) is not bytes or not minimum <= len(value) <= maximum
        for value, minimum, maximum in byte_limits
    ):
        _portable_fail()
    try:
        receipt_value = _validate_receipt_snapshot(value=receipt)
    except MmiH2cDualSideManualHandoffContextReceiptV1Error as exc:
        if exc.code != _RECEIPT_ERROR:
            raise
        _portable_fail()
    h2 = _validate_portable_legacy_step1_comparison_report_v1(
        value=comparison_report
    )
    h1 = _validate_portable_legacy_step1_compatibility_candidate_v1(
        value=legacy_step1_compatibility_candidate
    )
    r2 = _validate_portable_validated_grounded_analysis_response_v2(
        value=validated_grounded_analysis_response
    )
    r1, decoded_h1 = _validate_portable_raw_response_envelope_v2(
        value=raw_response_envelope
    )
    g2 = _validate_portable_grounded_prompt_v2(value=grounded_prompt)
    settings_record = _validate_portable_source_record_v1(
        value=strategy_settings_source_record,
        expected_role=MmiSourceRole.STRATEGY_SETTINGS,
        archived_source_bytes=archived_strategy_settings_bytes,
    )
    portfolio_record = _validate_portable_source_record_v1(
        value=portfolio_snapshot_source_record,
        expected_role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        archived_source_bytes=archived_portfolio_snapshot_bytes,
    )
    _validate_portable_artifact_links(
        receipt=receipt_value,
        comparison_report=h2,
        legacy_step1_compatibility_candidate=h1,
        validated_grounded_analysis_response=r2,
        raw_response_envelope=r1,
        grounded_prompt=g2,
        decoded_h1_response_bytes=decoded_h1,
        archived_h1_prompt_bytes=archived_h1_prompt_bytes,
        archived_h1_response_bytes=archived_h1_response_bytes,
        archived_legacy_response_bytes=archived_legacy_response_bytes,
        strategy_settings_source_record=settings_record,
        portfolio_snapshot_source_record=portfolio_record,
    )
    _validate_portable_legacy_prompt_reconstruction(
        receipt=receipt_value,
        comparison_report=h2,
        archived_strategy_settings_bytes=archived_strategy_settings_bytes,
        archived_portfolio_snapshot_bytes=archived_portfolio_snapshot_bytes,
        archived_legacy_prompt_template_bytes=(
            archived_legacy_prompt_template_bytes
        ),
        archived_legacy_prompt_bytes=archived_legacy_prompt_bytes,
    )
