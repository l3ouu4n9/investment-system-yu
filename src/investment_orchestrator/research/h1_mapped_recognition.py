"""Validated, ephemeral facts for a later mapped-H1 recognition boundary.

This module accepts a complete report-only H1-to-Legacy mapping only as
evidence.  It returns no availability state, permission, freshness verdict,
or durable artifact.  ``research_availability`` remains the future owner of
any recognition and freshness decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Final, Literal, NoReturn

from investment_orchestrator.mmi.raw_response_envelope_v2 import (
    validate_mmi_raw_response_envelope_v2,
)
from investment_orchestrator.mmi.mmi_h1_legacy_step1_mapping_report_v1 import (
    MmiH1LegacyStep1MappingReportV1Error,
    validate_mmi_h1_legacy_step1_mapping_report_v1,
)
from investment_orchestrator.validators.validate_research_handoff import (
    LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION,
)


__all__ = (
    "H1MappedRecognitionError",
    "H1MappedRecognitionFacts",
    "build_validated_h1_mapped_recognition_facts",
)

_MAPPING_SCHEMA_VERSION: Final = "mmi_h1_legacy_step1_mapping_report_v1"
_MAPPING_ARTIFACT_KIND: Final = "MMI_H1_LEGACY_STEP1_MAPPING_REPORT"
_ROLE_MAP_VERSION: Final = "h1_legacy_step1_role_map_v1"
_AUTHORITY_EFFECT_NONE: Final = "NONE"
_CANONICAL_UTC_TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"
_SOURCE_KIND: Final = "H1_ROLE_MAPPED"
_SHA256_HEX: Final = frozenset("0123456789abcdef")
_IDENTITY_CHAIN_FIELDS: Final = (
    "strategy_settings_source_record_identity_sha256",
    "policy_projection_identity_sha256",
    "universe_projection_identity_sha256",
    "portfolio_source_record_identity_sha256",
    "portfolio_projection_identity_sha256",
    "evidence_bundle_identity_sha256",
    "analyst_visible_evidence_view_identity_sha256",
    "grounded_prompt_artifact_identity_sha256",
    "prompt_context_binding_sha256",
    "raw_response_envelope_identity_sha256",
    "validated_grounded_analysis_response_identity_sha256",
    "legacy_step1_compatibility_candidate_identity_sha256",
)
_CURRENT_SOURCE_BINDING_FIELDS: Final = (
    "strategy_settings_source_record_identity_sha256",
    "policy_projection_identity_sha256",
    "universe_projection_identity_sha256",
    "portfolio_source_record_identity_sha256",
    "portfolio_projection_identity_sha256",
)
_ERROR_CODES: Final = frozenset(
    {
        "H1_RECOGNITION_INPUT_MISSING",
        "H1_RECOGNITION_UPSTREAM_INVALID",
        "H1_RECOGNITION_IDENTITY_MISMATCH",
        "H1_RECOGNITION_CURRENT_SOURCE_MISMATCH",
        "H1_RECOGNITION_CAPTURE_BINDING_INVALID",
        "H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID",
        "H1_RECOGNITION_VERSION_UNSUPPORTED",
    }
)
_ErrorCode = Literal[
    "H1_RECOGNITION_INPUT_MISSING",
    "H1_RECOGNITION_UPSTREAM_INVALID",
    "H1_RECOGNITION_IDENTITY_MISMATCH",
    "H1_RECOGNITION_CURRENT_SOURCE_MISMATCH",
    "H1_RECOGNITION_CAPTURE_BINDING_INVALID",
    "H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID",
    "H1_RECOGNITION_VERSION_UNSUPPORTED",
]


class H1MappedRecognitionError(ValueError):
    """Raised when no complete, non-authorizing mapped-H1 facts exist."""

    code: _ErrorCode

    def __init__(self, code: _ErrorCode) -> None:
        if code not in _ERROR_CODES:
            raise TypeError("unsupported H1 mapped-recognition error code")
        super().__init__(code)
        self.code = code


def _fail(code: _ErrorCode) -> NoReturn:
    raise H1MappedRecognitionError(code)


@dataclass(frozen=True, slots=True, init=False)
class H1MappedRecognitionFacts:
    """Immutable, validation-created facts for a future availability owner."""

    source_kind: Literal["H1_ROLE_MAPPED"]
    mapping_schema_version: str
    mapping_report_identity_sha256: str
    role_map_version: str
    target_legacy_validator_contract_version: str
    strategy_settings_source_record_identity_sha256: str
    policy_projection_identity_sha256: str
    universe_projection_identity_sha256: str
    portfolio_source_record_identity_sha256: str
    portfolio_projection_identity_sha256: str
    evidence_bundle_identity_sha256: str
    analyst_visible_evidence_view_identity_sha256: str
    grounded_prompt_artifact_identity_sha256: str
    prompt_context_binding_sha256: str
    raw_response_envelope_identity_sha256: str
    raw_response_sha256: str
    validated_grounded_analysis_response_identity_sha256: str
    legacy_step1_compatibility_candidate_identity_sha256: str
    policy_as_of_date: str
    policy_source_run_timestamp_utc: str | None
    portfolio_source_date: str
    context_evaluation_timestamp_utc: str

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "H1MappedRecognitionFacts are created only by the validated "
            "mapped-H1 recognition factory."
        )


def _create_facts(
    *,
    mapping_report: Mapping[str, object],
    upstream_identity_chain: Mapping[str, object],
    raw_response_sha256: str,
    policy_as_of_date: str,
    policy_source_run_timestamp_utc: str | None,
    portfolio_source_date: str,
    context_evaluation_timestamp_utc: str,
) -> H1MappedRecognitionFacts:
    facts = object.__new__(H1MappedRecognitionFacts)
    object.__setattr__(facts, "source_kind", _SOURCE_KIND)
    object.__setattr__(
        facts,
        "mapping_schema_version",
        mapping_report["schema_version"],
    )
    object.__setattr__(
        facts,
        "mapping_report_identity_sha256",
        mapping_report["mapping_report_identity_sha256"],
    )
    object.__setattr__(facts, "role_map_version", mapping_report["role_map_version"])
    object.__setattr__(
        facts,
        "target_legacy_validator_contract_version",
        mapping_report["target_legacy_validator_contract_version"],
    )
    for field in _IDENTITY_CHAIN_FIELDS:
        object.__setattr__(facts, field, upstream_identity_chain[field])
    object.__setattr__(facts, "raw_response_sha256", raw_response_sha256)
    object.__setattr__(facts, "policy_as_of_date", policy_as_of_date)
    object.__setattr__(
        facts,
        "policy_source_run_timestamp_utc",
        policy_source_run_timestamp_utc,
    )
    object.__setattr__(facts, "portfolio_source_date", portfolio_source_date)
    object.__setattr__(
        facts,
        "context_evaluation_timestamp_utc",
        context_evaluation_timestamp_utc,
    )
    return facts


def _mapping_value(
    value: object,
    *,
    missing_code: _ErrorCode,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(missing_code)
    return value


def _mapping_get(
    value: Mapping[str, object],
    field: str,
    *,
    code: _ErrorCode,
) -> object:
    try:
        return value.get(field)
    except Exception:
        _fail(code)


def _require_sha256(value: object, *, code: _ErrorCode) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or not set(value) <= _SHA256_HEX
    ):
        _fail(code)
    return value


def _require_date(value: object) -> tuple[str, date]:
    if type(value) is not str:
        _fail("H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail("H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID")
    if parsed.isoformat() != value:
        _fail("H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID")
    return value, parsed


def _require_timestamp(value: object) -> tuple[str, datetime]:
    if type(value) is not str or len(value) != 27:
        _fail("H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID")
    try:
        parsed = datetime.strptime(value, _CANONICAL_UTC_TIMESTAMP_FORMAT)
    except ValueError:
        _fail("H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID")
    if parsed.strftime(_CANONICAL_UTC_TIMESTAMP_FORMAT) != value:
        _fail("H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID")
    return value, parsed.replace(tzinfo=timezone.utc)


def _source_record_identity(source: object) -> str | None:
    try:
        record = getattr(source, "source_record")
    except Exception:
        return None
    if not isinstance(record, Mapping):
        return None
    try:
        value = record.get("source_record_identity_sha256")
    except Exception:
        return None
    if (
        type(value) is not str
        or len(value) != 64
        or not set(value) <= _SHA256_HEX
    ):
        return None
    return value


def _mapping_chain(value: Mapping[str, object]) -> Mapping[str, object] | None:
    try:
        chain = value.get("upstream_identity_chain")
    except Exception:
        return None
    return chain if isinstance(chain, Mapping) else None


def _current_source_chain_mismatch(
    *,
    mapping_report: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: object,
    portfolio_projection: Mapping[str, object],
    portfolio_source: object,
) -> bool:
    """Compare supplied current captures to the report's bound identities.

    This is intentionally only an equality check.  The report validator owns
    validation of each artifact and of the complete upstream chain.
    """
    chain = _mapping_chain(mapping_report)
    if chain is None:
        return False
    try:
        current = {
            "strategy_settings_source_record_identity_sha256": (
                _source_record_identity(policy_source)
            ),
            "policy_projection_identity_sha256": policy_projection.get(
                "policy_projection_identity_sha256"
            ),
            "universe_projection_identity_sha256": policy_projection.get(
                "universe_projection_identity_sha256"
            ),
            "portfolio_source_record_identity_sha256": (
                _source_record_identity(portfolio_source)
            ),
            "portfolio_projection_identity_sha256": portfolio_projection.get(
                "portfolio_projection_identity_sha256"
            ),
        }
    except Exception:
        return False
    for field in _CURRENT_SOURCE_BINDING_FIELDS:
        expected = chain.get(field)
        actual = current[field]
        if (
            type(expected) is str
            and len(expected) == 64
            and set(expected) <= _SHA256_HEX
            and type(actual) is str
            and len(actual) == 64
            and set(actual) <= _SHA256_HEX
            and actual != expected
        ):
            return True
    return False


def _require_mapping_contract_assertions(
    mapping_report: Mapping[str, object],
) -> None:
    if (
        _mapping_get(
            mapping_report,
            "schema_version",
            code="H1_RECOGNITION_UPSTREAM_INVALID",
        )
        != _MAPPING_SCHEMA_VERSION
        or _mapping_get(
            mapping_report,
            "artifact_kind",
            code="H1_RECOGNITION_UPSTREAM_INVALID",
        )
        != _MAPPING_ARTIFACT_KIND
        or _mapping_get(
            mapping_report,
            "role_map_version",
            code="H1_RECOGNITION_UPSTREAM_INVALID",
        )
        != _ROLE_MAP_VERSION
        or _mapping_get(
            mapping_report,
            "target_legacy_validator_contract_version",
            code="H1_RECOGNITION_UPSTREAM_INVALID",
        )
        != LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION
    ):
        _fail("H1_RECOGNITION_VERSION_UNSUPPORTED")
    if (
        _mapping_get(
            mapping_report,
            "report_only",
            code="H1_RECOGNITION_UPSTREAM_INVALID",
        )
        is not True
        or _mapping_get(
            mapping_report,
            "authority_effect",
            code="H1_RECOGNITION_UPSTREAM_INVALID",
        )
        != _AUTHORITY_EFFECT_NONE
        or _mapping_get(
            mapping_report,
            "not_authorization",
            code="H1_RECOGNITION_UPSTREAM_INVALID",
        )
        is not True
        or _mapping_get(
            mapping_report,
            "full_legacy_compatibility",
            code="H1_RECOGNITION_UPSTREAM_INVALID",
        )
        is not False
    ):
        _fail("H1_RECOGNITION_UPSTREAM_INVALID")


def _temporal_facts(
    *,
    policy_projection: Mapping[str, object],
    portfolio_projection: Mapping[str, object],
    run_context: object,
) -> tuple[str, str | None, str, str]:
    try:
        context_value = getattr(run_context, "evaluation_timestamp_utc")
    except Exception:
        _fail("H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID")
    context_text, context_time = _require_timestamp(context_value)
    policy_as_of_text, policy_as_of = _require_date(
        _mapping_get(
            policy_projection,
            "policy_as_of_date",
            code="H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID",
        )
    )
    source_run_value = _mapping_get(
        policy_projection,
        "source_run_timestamp_utc",
        code="H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID",
    )
    if source_run_value is None:
        source_run_text = None
    else:
        source_run_text, source_run_time = _require_timestamp(source_run_value)
        if source_run_time > context_time:
            _fail("H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID")
    portfolio_date_text, portfolio_date = _require_date(
        _mapping_get(
            portfolio_projection,
            "portfolio_source_date",
            code="H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID",
        )
    )
    if (
        _mapping_get(
            policy_projection,
            "evaluation_timestamp_utc",
            code="H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID",
        )
        != context_text
        or _mapping_get(
            portfolio_projection,
            "evaluation_timestamp_utc",
            code="H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID",
        )
        != context_text
        or policy_as_of > context_time.date()
        or portfolio_date > context_time.date()
    ):
        _fail("H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID")
    return (
        policy_as_of_text,
        source_run_text,
        portfolio_date_text,
        context_text,
    )


def _validate_capture_binding(
    *,
    mapping_report: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
) -> str:
    chain = _mapping_chain(mapping_report)
    if chain is None:
        _fail("H1_RECOGNITION_CAPTURE_BINDING_INVALID")
    envelope_identity = _require_sha256(
        _mapping_get(
            raw_response_envelope,
            "raw_response_envelope_identity_sha256",
            code="H1_RECOGNITION_CAPTURE_BINDING_INVALID",
        ),
        code="H1_RECOGNITION_CAPTURE_BINDING_INVALID",
    )
    raw_response_sha256 = _require_sha256(
        _mapping_get(
            raw_response_envelope,
            "raw_response_sha256",
            code="H1_RECOGNITION_CAPTURE_BINDING_INVALID",
        ),
        code="H1_RECOGNITION_CAPTURE_BINDING_INVALID",
    )
    if chain.get("raw_response_envelope_identity_sha256") != envelope_identity:
        _fail("H1_RECOGNITION_CAPTURE_BINDING_INVALID")
    if (
        _mapping_get(
            validated_grounded_analysis_response,
            "raw_response_envelope_identity_sha256",
            code="H1_RECOGNITION_CAPTURE_BINDING_INVALID",
        )
        != envelope_identity
    ):
        _fail("H1_RECOGNITION_CAPTURE_BINDING_INVALID")
    payload = _mapping_get(
        validated_grounded_analysis_response,
        "response_payload",
        code="H1_RECOGNITION_CAPTURE_BINDING_INVALID",
    )
    if not isinstance(payload, Mapping) or (
        _mapping_get(
            payload,
            "prompt_context_binding_sha256",
            code="H1_RECOGNITION_CAPTURE_BINDING_INVALID",
        )
        != chain.get("prompt_context_binding_sha256")
    ):
        _fail("H1_RECOGNITION_CAPTURE_BINDING_INVALID")
    return raw_response_sha256


def _mapping_error_code(
    error: MmiH1LegacyStep1MappingReportV1Error,
) -> _ErrorCode:
    if error.code == "MMI_H1_LEGACY_MAPPING_TARGET_VALIDATOR_VERSION_MISMATCH":
        return "H1_RECOGNITION_VERSION_UNSUPPORTED"
    if error.code in {
        "MMI_H1_LEGACY_MAPPING_IDENTITY_MISMATCH",
        "MMI_H1_LEGACY_MAPPING_NON_EXPECTED",
        "MMI_H1_LEGACY_MAPPING_SEQUENCE_MEMBERSHIP_MISMATCH",
    }:
        return "H1_RECOGNITION_IDENTITY_MISMATCH"
    return "H1_RECOGNITION_UPSTREAM_INVALID"


def build_validated_h1_mapped_recognition_facts(
    *,
    mapping_report: Mapping[str, object],
    legacy_step1_compatibility_candidate: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: object,
    portfolio_projection: Mapping[str, object],
    portfolio_source: object,
    run_context: object,
) -> H1MappedRecognitionFacts:
    """Validate a complete H1 mapping and return only future recognition facts.

    The function intentionally reads no clock and writes no artifact.  It does
    not classify freshness or availability, and it does not select H1 over
    Legacy research.
    """
    if any(
        value is None
        for value in (
            mapping_report,
            legacy_step1_compatibility_candidate,
            validated_grounded_analysis_response,
            raw_response_envelope,
            evidence_bundle,
            policy_projection,
            policy_source,
            portfolio_projection,
            portfolio_source,
            run_context,
        )
    ):
        _fail("H1_RECOGNITION_INPUT_MISSING")
    report = _mapping_value(
        mapping_report, missing_code="H1_RECOGNITION_INPUT_MISSING"
    )
    candidate = _mapping_value(
        legacy_step1_compatibility_candidate,
        missing_code="H1_RECOGNITION_INPUT_MISSING",
    )
    response = _mapping_value(
        validated_grounded_analysis_response,
        missing_code="H1_RECOGNITION_INPUT_MISSING",
    )
    envelope = _mapping_value(
        raw_response_envelope,
        missing_code="H1_RECOGNITION_INPUT_MISSING",
    )
    evidence = _mapping_value(
        evidence_bundle, missing_code="H1_RECOGNITION_INPUT_MISSING"
    )
    policy = _mapping_value(
        policy_projection, missing_code="H1_RECOGNITION_INPUT_MISSING"
    )
    portfolio = _mapping_value(
        portfolio_projection, missing_code="H1_RECOGNITION_INPUT_MISSING"
    )

    _require_mapping_contract_assertions(report)
    if _current_source_chain_mismatch(
        mapping_report=report,
        policy_projection=policy,
        policy_source=policy_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
    ):
        _fail("H1_RECOGNITION_CURRENT_SOURCE_MISMATCH")
    (
        policy_as_of_date,
        policy_source_run_timestamp_utc,
        portfolio_source_date,
        context_evaluation_timestamp_utc,
    ) = _temporal_facts(
        policy_projection=policy,
        portfolio_projection=portfolio,
        run_context=run_context,
    )
    try:
        validated_envelope = validate_mmi_raw_response_envelope_v2(
            value=envelope,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=policy_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except Exception:
        _fail("H1_RECOGNITION_CAPTURE_BINDING_INVALID")
    raw_response_sha256 = _validate_capture_binding(
        mapping_report=report,
        raw_response_envelope=validated_envelope,
        validated_grounded_analysis_response=response,
    )
    try:
        validated_report = validate_mmi_h1_legacy_step1_mapping_report_v1(
            value=report,
            legacy_step1_compatibility_candidate=candidate,
            validated_grounded_analysis_response=response,
            raw_response_envelope=validated_envelope,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=policy_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except MmiH1LegacyStep1MappingReportV1Error as error:
        if _current_source_chain_mismatch(
            mapping_report=report,
            policy_projection=policy,
            policy_source=policy_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
        ):
            _fail("H1_RECOGNITION_CURRENT_SOURCE_MISMATCH")
        _fail(_mapping_error_code(error))
    except Exception:
        _fail("H1_RECOGNITION_UPSTREAM_INVALID")

    validated_chain = _mapping_chain(validated_report)
    if validated_chain is None:
        _fail("H1_RECOGNITION_UPSTREAM_INVALID")
    for field in _IDENTITY_CHAIN_FIELDS:
        _require_sha256(
            _mapping_get(
                validated_chain,
                field,
                code="H1_RECOGNITION_UPSTREAM_INVALID",
            ),
            code="H1_RECOGNITION_UPSTREAM_INVALID",
        )
    _require_sha256(
        _mapping_get(
            validated_report,
            "mapping_report_identity_sha256",
            code="H1_RECOGNITION_IDENTITY_MISMATCH",
        ),
        code="H1_RECOGNITION_IDENTITY_MISMATCH",
    )
    return _create_facts(
        mapping_report=validated_report,
        upstream_identity_chain=validated_chain,
        raw_response_sha256=raw_response_sha256,
        policy_as_of_date=policy_as_of_date,
        policy_source_run_timestamp_utc=policy_source_run_timestamp_utc,
        portfolio_source_date=portfolio_source_date,
        context_evaluation_timestamp_utc=context_evaluation_timestamp_utc,
    )
