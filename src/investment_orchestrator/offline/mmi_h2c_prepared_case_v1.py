"""Dormant report-only envelope for one persisted H2c prepared case.

``PERSISTENT_ENVELOPE_VALIDATION`` is deliberately limited to this owner's
closed fields, fixed case-relative roles, bounded detached child mappings,
canonical timestamp, canonical size, and self-identity.  The source-record and
grounded-prompt mappings are opaque here.  Live source fidelity belongs to the
future preparation phase; archive fidelity and rebuilt-G2 equality belong to
the future consumption phase before either response is read.

This owner has no filesystem, live-source, clock, capability, provider,
network, workflow, scheduler, publication, permission, gate, order, broker, or
execution behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
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
    "MmiH2cPreparedCaseV1Error",
    "validate_mmi_h2c_prepared_case_v1",
)

_ERROR: Final = "MMI_H2C_PREPARED_CASE_V1_INVALID"
_SCHEMA: Final = "mmi_h2c_prepared_case_v1.schema.json"
_IDENTITY_FIELD: Final = "prepared_case_identity_sha256"
_IDENTITY_DOMAIN: Final = b"mmi_h2c_prepared_case_v1\0"
# Exact envelope framing (with three empty opaque mappings), plus the existing
# two 8,192-byte source-record bounds and independently derived 393,852-byte
# G2 bound.  The contract tests reproduce this arithmetic without this owner.
_MAXIMUM_CANONICAL_BYTES: Final = 411_753
_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES: Final = 8_192
_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES: Final = 393_852
_MAXIMUM_LEGACY_TEMPLATE_BYTES: Final = 262_144
_MAXIMUM_H1_PROMPT_BYTES: Final = 65_536
_MAXIMUM_LEGACY_PROMPT_BYTES: Final = 3_170_307
_LEGACY_COMPILER_CONTRACT_VERSION: Final = (
    "mmi_legacy_step1_compatibility_compiler_v1"
)
_ZERO_SHA256: Final = "0" * 64

_PreparedCaseErrorCode = Literal["MMI_H2C_PREPARED_CASE_V1_INVALID"]


class MmiH2cPreparedCaseV1Error(ValueError):
    """Raised when a persisted prepared-case envelope is invalid."""

    code: _PreparedCaseErrorCode

    def __init__(self, code: _PreparedCaseErrorCode) -> None:
        if code != _ERROR:
            raise TypeError("unsupported H2c prepared-case error code")
        super().__init__(code)
        self.code = code


def _fail() -> NoReturn:
    raise MmiH2cPreparedCaseV1Error(_ERROR) from None


def _snapshot_mapping(
    value: Mapping[str, object],
    *,
    maximum_bytes: int,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail()
    try:
        # Live source records are immutable Mapping proxies.  Materializing
        # the complete top-level mapping is the sole boundary conversion; the
        # canonical owner then rejects unsupported nested values rather than
        # coercing, normalizing, reconstructing, defaulting, or filtering them.
        encoded = canonical_json_bytes(
            dict(value),
            maximum_bytes=maximum_bytes,
        )
    except MmiCanonicalizationError:
        _fail()
    parsed = json.loads(encoded)
    if type(parsed) is not dict:
        _fail()
    return parsed


def _require_exact_bytes(
    value: bytes,
    *,
    minimum: int,
    maximum: int,
) -> bytes:
    if type(value) is not bytes or not minimum <= len(value) <= maximum:
        _fail()
    return value


def _require_canonical_timestamp(value: object) -> None:
    if type(value) is not str:
        _fail()
    try:
        observed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        _fail()
    if observed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
        _fail()


def _validate_prepared_case_snapshot(
    *,
    value: Mapping[str, object],
) -> dict[str, object]:
    prepared = _snapshot_mapping(
        value,
        maximum_bytes=_MAXIMUM_CANONICAL_BYTES,
    )
    try:
        validate_artifact_schema(prepared, schema_name=_SCHEMA)
    except ArtifactSchemaError:
        _fail()
    _require_canonical_timestamp(prepared.get("evaluation_timestamp_utc"))

    settings = prepared.get("strategy_settings_source")
    portfolio = prepared.get("portfolio_snapshot_source")
    grounded_prompt = prepared.get("grounded_prompt")
    if type(settings) is not dict or type(portfolio) is not dict:
        _fail()
    _snapshot_mapping(
        settings.get("source_record"),
        maximum_bytes=_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES,
    )
    _snapshot_mapping(
        portfolio.get("source_record"),
        maximum_bytes=_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES,
    )
    _snapshot_mapping(
        grounded_prompt,
        maximum_bytes=_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES,
    )
    try:
        expected = record_identity_sha256(
            prepared,
            identity_field=_IDENTITY_FIELD,
            domain=_IDENTITY_DOMAIN,
            maximum_bytes=_MAXIMUM_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        _fail()
    if prepared.get(_IDENTITY_FIELD) != expected:
        _fail()
    return prepared


def _build_mmi_h2c_prepared_case_v1(
    *,
    evaluation_timestamp_utc: str,
    strategy_settings_source_record: Mapping[str, object],
    portfolio_snapshot_source_record: Mapping[str, object],
    legacy_prompt_template_bytes: bytes,
    grounded_prompt: Mapping[str, object],
    h1_prompt_bytes: bytes,
    legacy_prompt_bytes: bytes,
) -> dict[str, object]:
    """Build one detached dormant envelope from already-validated inputs."""
    settings_record = _snapshot_mapping(
        strategy_settings_source_record,
        maximum_bytes=_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES,
    )
    portfolio_record = _snapshot_mapping(
        portfolio_snapshot_source_record,
        maximum_bytes=_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES,
    )
    prompt = _snapshot_mapping(
        grounded_prompt,
        maximum_bytes=_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES,
    )
    template_bytes = _require_exact_bytes(
        legacy_prompt_template_bytes,
        minimum=1,
        maximum=_MAXIMUM_LEGACY_TEMPLATE_BYTES,
    )
    h1_bytes = _require_exact_bytes(
        h1_prompt_bytes,
        minimum=1,
        maximum=_MAXIMUM_H1_PROMPT_BYTES,
    )
    legacy_bytes = _require_exact_bytes(
        legacy_prompt_bytes,
        minimum=1,
        maximum=_MAXIMUM_LEGACY_PROMPT_BYTES,
    )
    prepared: dict[str, object] = {
        "schema_version": "mmi_h2c_prepared_case_v1",
        "artifact_kind": "MMI_H2C_PREPARED_CASE",
        "preparation_contract_version": (
            "mmi_h2c_persisted_case_prepare_v1"
        ),
        "report_only": True,
        "authority_effect": "NONE",
        "workflow_status": "AWAITING_OPERATOR_RESPONSES",
        "evaluation_timestamp_utc": evaluation_timestamp_utc,
        "strategy_settings_source": {
            "source_record": settings_record,
            "archive_relative_path": "archive/strategy_settings.yaml",
        },
        "portfolio_snapshot_source": {
            "source_record": portfolio_record,
            "archive_relative_path": "archive/portfolio_snapshot.txt",
        },
        "legacy_prompt_template": {
            "repository_relative_locator": "prompts/research_dual_lane.txt",
            "archive_relative_path": "archive/research_dual_lane.txt",
            "byte_length": len(template_bytes),
            "sha256": hashlib.sha256(template_bytes).hexdigest(),
        },
        "grounded_prompt": prompt,
        "h1_prompt": {
            "relative_path": "prompts/h1_prompt.txt",
            "byte_length": len(h1_bytes),
            "sha256": hashlib.sha256(h1_bytes).hexdigest(),
        },
        "legacy_prompt": {
            "relative_path": "prompts/legacy_prompt.txt",
            "byte_length": len(legacy_bytes),
            "sha256": hashlib.sha256(legacy_bytes).hexdigest(),
            "compiler_contract_version": (
                _LEGACY_COMPILER_CONTRACT_VERSION
            ),
        },
        "response_leaves": {
            "h1": "responses/h1_response.raw",
            "legacy": "responses/legacy_response.raw",
        },
        "result_leaves": {
            "case_evidence_bundle": "artifacts/case_evidence_bundle.json",
            "comparison_report": "artifacts/comparison_report.json",
            "receipt": "artifacts/receipt.json",
        },
        _IDENTITY_FIELD: _ZERO_SHA256,
    }
    try:
        prepared[_IDENTITY_FIELD] = record_identity_sha256(
            prepared,
            identity_field=_IDENTITY_FIELD,
            domain=_IDENTITY_DOMAIN,
            maximum_bytes=_MAXIMUM_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        _fail()
    return _validate_prepared_case_snapshot(value=prepared)


def validate_mmi_h2c_prepared_case_v1(
    *,
    prepared_case: Mapping[str, object],
) -> None:
    """Validate only the closed persistent envelope and its self-identity."""
    _validate_prepared_case_snapshot(value=prepared_case)
