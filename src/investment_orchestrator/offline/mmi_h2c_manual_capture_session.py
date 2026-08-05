"""Foreground, single-use H2c manual capture session.

This owner exposes exact report-only prompt bytes, waits for one explicit
operator handoff, binds two exact response files to the retained live MMI
objects, and persists their portable case bundle before H2 and its
non-authoritative observation receipt.  It has no provider, network, polling,
publication, permission, or order capability.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
from typing import Final, NoReturn, Protocol, runtime_checkable

import yaml

from investment_orchestrator.common.paths import prompt_path
from investment_orchestrator.llm.legacy_step1_prompt_compiler import (
    compile_legacy_step1_prompt_text,
    derive_legacy_approved_extended_etf_json,
)
from investment_orchestrator.llm.manual_output import PromptRenderError
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
    validate_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_CANONICAL_JSON_BYTES,
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES,
    MAX_MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_CANONICAL_BYTES,
    MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES,
    MmiCanonicalizationError,
    _MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_IDENTITY_DOMAIN,
    canonical_json_bytes,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MmiCapturedSource,
    MmiClockContractError,
    MmiPolicyProjectionBuildResult,
    MmiPolicyProjectionValidationResult,
    MmiPortfolioProjectionBuildResult,
    MmiPortfolioProjectionValidationResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    MmiSourceCaptureResult,
    MmiSourceRole,
    begin_mmi_projection_run,
)
from investment_orchestrator.mmi import evidence_bundle as _evidence_bundle
from investment_orchestrator.mmi.grounded_prompt_v2 import (
    MmiGroundedPromptV2Error,
    build_mmi_grounded_prompt_v2,
    validate_mmi_grounded_prompt_v2,
)
from investment_orchestrator.mmi.legacy_step1_compatibility_candidate_v1 import (
    MmiLegacyStep1CompatibilityCandidateV1Error,
    build_mmi_legacy_step1_compatibility_candidate_v1,
    validate_mmi_legacy_step1_compatibility_candidate_v1,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
    validate_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
    validate_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.mmi.raw_response_envelope_v2 import (
    MmiRawResponseEnvelopeV2Error,
    build_mmi_raw_response_envelope_v2,
    validate_mmi_raw_response_envelope_v2,
)
from investment_orchestrator.mmi.source_capture import (
    capture_current_mmi_source,
)
from investment_orchestrator.mmi.validated_grounded_analysis_response_v2 import (
    MmiValidatedGroundedAnalysisResponseV2Error,
    build_mmi_validated_grounded_analysis_response_v2,
    validate_mmi_validated_grounded_analysis_response_v2,
)
import investment_orchestrator.offline.mmi_h2c_case_bundle_v1 as _case_bundle
from investment_orchestrator.offline.mmi_h2c_dual_side_manual_handoff_context_receipt_v1 import (
    MmiH2cDualSideManualHandoffContextReceiptV1Error,
    validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1,
)
from investment_orchestrator.offline.mmi_legacy_step1_comparison_report_v1 import (
    MAX_LEGACY_RESEARCH_RAW_BYTES,
    MmiLegacyStep1ComparisonReportV1Error,
    build_mmi_legacy_step1_comparison_report_v1,
    validate_mmi_legacy_step1_comparison_report_v1,
)
from investment_orchestrator.validators.strategy_settings import (
    StrategySettingsValidationError,
    parse_strategy_settings_text,
)


__all__ = (
    "H2cManualCaptureError",
    "H2cManualCaptureErrorCode",
    "H2cManualCaptureFailureClass",
    "H2cManualCaptureResult",
    "H2cOperatorHandoff",
    "run_h2c_manual_capture",
)


class H2cManualCaptureFailureClass(str, Enum):
    """Closed controlled failure classes for the v1 capture lifecycle."""

    ARTIFACT_CONTENT = "ARTIFACT_CONTENT"
    PROMPT_CONTRACT = "PROMPT_CONTRACT"
    VALIDATOR_SCHEMA = "VALIDATOR_SCHEMA"
    COMPILER_NORMALIZER = "COMPILER_NORMALIZER"
    WORKFLOW_ORCHESTRATOR = "WORKFLOW_ORCHESTRATOR"
    AVAILABILITY_PERMISSION = "AVAILABILITY_PERMISSION"
    OPERATOR_INPUT = "OPERATOR_INPUT"
    PERSISTENCE = "PERSISTENCE"


class H2cManualCaptureErrorCode(str, Enum):
    """Minimal stable operator-facing error codes for H2c capture."""

    H2C_ARGUMENT_INVALID = "H2C_ARGUMENT_INVALID"
    H2C_PATH_CONTRACT_INVALID = "H2C_PATH_CONTRACT_INVALID"
    H2C_CAPABILITY_UNAVAILABLE = "H2C_CAPABILITY_UNAVAILABLE"
    H2C_SOURCE_CAPTURE_INVALID = "H2C_SOURCE_CAPTURE_INVALID"
    H2C_PORTFOLIO_NOT_COMPARABLE = "H2C_PORTFOLIO_NOT_COMPARABLE"
    H2C_LIVE_CHAIN_INVALID = "H2C_LIVE_CHAIN_INVALID"
    H2C_PROMPT_CONTRACT_INVALID = "H2C_PROMPT_CONTRACT_INVALID"
    H2C_LEGACY_COMPILER_INVALID = "H2C_LEGACY_COMPILER_INVALID"
    H2C_PROMPT_EXPOSURE_FAILED = "H2C_PROMPT_EXPOSURE_FAILED"
    H2C_OPERATOR_CANCELLED = "H2C_OPERATOR_CANCELLED"
    H2C_OPERATOR_CONTROL_INVALID = "H2C_OPERATOR_CONTROL_INVALID"
    H2C_RESPONSE_INPUT_INVALID = "H2C_RESPONSE_INPUT_INVALID"
    H2C_RESPONSE_CONTENT_INVALID = "H2C_RESPONSE_CONTENT_INVALID"
    H2C_CASE_EVIDENCE_BUNDLE_VALIDATION_INVALID = (
        "H2C_CASE_EVIDENCE_BUNDLE_VALIDATION_INVALID"
    )
    H2C_H2_VALIDATION_INVALID = "H2C_H2_VALIDATION_INVALID"
    H2C_RECEIPT_VALIDATION_INVALID = "H2C_RECEIPT_VALIDATION_INVALID"
    H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED = (
        "H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED"
    )
    H2C_H2_PERSISTENCE_FAILED = "H2C_H2_PERSISTENCE_FAILED"
    H2C_RECEIPT_PERSISTENCE_FAILED = "H2C_RECEIPT_PERSISTENCE_FAILED"


_ERROR_CLASSES: Final = {
    H2cManualCaptureErrorCode.H2C_ARGUMENT_INVALID: (
        H2cManualCaptureFailureClass.OPERATOR_INPUT
    ),
    H2cManualCaptureErrorCode.H2C_PATH_CONTRACT_INVALID: (
        H2cManualCaptureFailureClass.OPERATOR_INPUT
    ),
    H2cManualCaptureErrorCode.H2C_CAPABILITY_UNAVAILABLE: (
        H2cManualCaptureFailureClass.AVAILABILITY_PERMISSION
    ),
    H2cManualCaptureErrorCode.H2C_SOURCE_CAPTURE_INVALID: (
        H2cManualCaptureFailureClass.ARTIFACT_CONTENT
    ),
    H2cManualCaptureErrorCode.H2C_PORTFOLIO_NOT_COMPARABLE: (
        H2cManualCaptureFailureClass.ARTIFACT_CONTENT
    ),
    H2cManualCaptureErrorCode.H2C_LIVE_CHAIN_INVALID: (
        H2cManualCaptureFailureClass.VALIDATOR_SCHEMA
    ),
    H2cManualCaptureErrorCode.H2C_PROMPT_CONTRACT_INVALID: (
        H2cManualCaptureFailureClass.PROMPT_CONTRACT
    ),
    H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID: (
        H2cManualCaptureFailureClass.COMPILER_NORMALIZER
    ),
    H2cManualCaptureErrorCode.H2C_PROMPT_EXPOSURE_FAILED: (
        H2cManualCaptureFailureClass.PERSISTENCE
    ),
    H2cManualCaptureErrorCode.H2C_OPERATOR_CANCELLED: (
        H2cManualCaptureFailureClass.OPERATOR_INPUT
    ),
    H2cManualCaptureErrorCode.H2C_OPERATOR_CONTROL_INVALID: (
        H2cManualCaptureFailureClass.OPERATOR_INPUT
    ),
    H2cManualCaptureErrorCode.H2C_RESPONSE_INPUT_INVALID: (
        H2cManualCaptureFailureClass.OPERATOR_INPUT
    ),
    H2cManualCaptureErrorCode.H2C_RESPONSE_CONTENT_INVALID: (
        H2cManualCaptureFailureClass.ARTIFACT_CONTENT
    ),
    H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_VALIDATION_INVALID: (
        H2cManualCaptureFailureClass.VALIDATOR_SCHEMA
    ),
    H2cManualCaptureErrorCode.H2C_H2_VALIDATION_INVALID: (
        H2cManualCaptureFailureClass.VALIDATOR_SCHEMA
    ),
    H2cManualCaptureErrorCode.H2C_RECEIPT_VALIDATION_INVALID: (
        H2cManualCaptureFailureClass.VALIDATOR_SCHEMA
    ),
    H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED: (
        H2cManualCaptureFailureClass.PERSISTENCE
    ),
    H2cManualCaptureErrorCode.H2C_H2_PERSISTENCE_FAILED: (
        H2cManualCaptureFailureClass.PERSISTENCE
    ),
    H2cManualCaptureErrorCode.H2C_RECEIPT_PERSISTENCE_FAILED: (
        H2cManualCaptureFailureClass.PERSISTENCE
    ),
}


@dataclass(frozen=True, slots=True, init=False)
class H2cManualCaptureError(RuntimeError):
    """One controlled session failure with a stable public surface."""

    code: H2cManualCaptureErrorCode
    failure_class: H2cManualCaptureFailureClass
    owner_reason_codes: tuple[str, ...]

    def __init__(
        self,
        *,
        code: H2cManualCaptureErrorCode,
        failure_class: H2cManualCaptureFailureClass,
        owner_reason_codes: tuple[str, ...] = (),
    ) -> None:
        if type(code) is not H2cManualCaptureErrorCode:
            raise TypeError("code must be an H2cManualCaptureErrorCode")
        if type(failure_class) is not H2cManualCaptureFailureClass:
            raise TypeError(
                "failure_class must be an H2cManualCaptureFailureClass"
            )
        if _ERROR_CLASSES.get(code) is not failure_class:
            raise ValueError("H2c error code/failure-class mismatch")
        if type(owner_reason_codes) is not tuple or any(
            type(reason) is not str for reason in owner_reason_codes
        ):
            raise TypeError("owner_reason_codes must be an exact string tuple")
        RuntimeError.__init__(self, code.value)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "failure_class", failure_class)
        object.__setattr__(self, "owner_reason_codes", owner_reason_codes)


@runtime_checkable
class H2cOperatorHandoff(Protocol):
    """Purpose-specific foreground boundary for one operator completion signal."""

    def await_response_files_ready(self) -> None:
        """Block once until the operator declares both response files ready."""


@dataclass(frozen=True, slots=True)
class H2cManualCaptureResult:
    """Persistent identities produced by one completed foreground capture."""

    comparison_report_identity_sha256: str
    receipt_identity_sha256: str


_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_TEMPLATE_MAXIMUM_BYTES: Final = 262_144
_LEGACY_PROMPT_MAXIMUM_BYTES: Final = 3_170_307
_LEGACY_SETTINGS_CANONICAL_MAXIMUM_BYTES: Final = 262_144
_READ_CHUNK_BYTES: Final = 65_536
_ZERO_SHA256: Final = "0" * 64
_FIXED_LEGACY_TEMPLATE_NAME: Final = "research_dual_lane.txt"
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
_G2_PROMPT_CODES: Final = frozenset(
    {
        "MMI_GROUNDED_PROMPT_V2_RENDER_INVALID",
        "MMI_GROUNDED_PROMPT_V2_TEXT_INVALID",
        "MMI_GROUNDED_PROMPT_V2_TEXT_SIZE_INVALID",
    }
)
_G2_CODES: Final = frozenset(
    {
        "MMI_GROUNDED_PROMPT_V2_ARTIFACT_IDENTITY_INVALID",
        "MMI_GROUNDED_PROMPT_V2_CONTEXT_INVALID",
        "MMI_GROUNDED_PROMPT_V2_CONTRACT_INVALID",
        "MMI_GROUNDED_PROMPT_V2_INPUT_INVALID",
        "MMI_GROUNDED_PROMPT_V2_RENDER_INVALID",
        "MMI_GROUNDED_PROMPT_V2_SCHEMA_INVALID",
        "MMI_GROUNDED_PROMPT_V2_TEXT_INVALID",
        "MMI_GROUNDED_PROMPT_V2_TEXT_SIZE_INVALID",
        "MMI_GROUNDED_PROMPT_V2_VIEW_IDENTITY_INVALID",
        "MMI_GROUNDED_PROMPT_V2_VIEW_SOURCE_FIDELITY_INVALID",
    }
)
_R1_RESPONSE_CODES: Final = frozenset(
    {"MMI_RAW_RESPONSE_ENVELOPE_V2_BYTES_INVALID"}
)
_R1_CODES: Final = frozenset(
    {
        "MMI_RAW_RESPONSE_ENVELOPE_V2_BYTES_INVALID",
        "MMI_RAW_RESPONSE_ENVELOPE_V2_CONTRACT_INVALID",
        "MMI_RAW_RESPONSE_ENVELOPE_V2_IDENTITY_INVALID",
        "MMI_RAW_RESPONSE_ENVELOPE_V2_INPUT_INVALID",
        "MMI_RAW_RESPONSE_ENVELOPE_V2_PROMPT_INVALID",
        "MMI_RAW_RESPONSE_ENVELOPE_V2_SCHEMA_INVALID",
    }
)
_R2_RESPONSE_CODES: Final = frozenset(
    {
        "MMI_VALIDATED_RESPONSE_V2_CONTEXT_MISMATCH",
        "MMI_VALIDATED_RESPONSE_V2_INSTRUMENT_MISMATCH",
        "MMI_VALIDATED_RESPONSE_V2_JSON_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SCHEMA_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SIZE_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_REFERENCE_MISMATCH",
        "MMI_VALIDATED_RESPONSE_V2_UTF8_BOM_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_UTF8_INVALID",
    }
)
_R2_CODES: Final = frozenset(
    {
        "MMI_VALIDATED_RESPONSE_V2_CONTEXT_MISMATCH",
        "MMI_VALIDATED_RESPONSE_V2_IDENTITY_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_INPUT_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_INSTRUMENT_MISMATCH",
        "MMI_VALIDATED_RESPONSE_V2_JSON_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SCHEMA_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SIZE_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_REFERENCE_MISMATCH",
        "MMI_VALIDATED_RESPONSE_V2_SCHEMA_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_SOURCE_FIDELITY_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_UPSTREAM_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_UTF8_BOM_INVALID",
        "MMI_VALIDATED_RESPONSE_V2_UTF8_INVALID",
    }
)
_H1_CODES: Final = frozenset(
    {
        "MMI_LEGACY_STEP1_CANDIDATE_IDENTITY_MISMATCHED",
        "MMI_LEGACY_STEP1_CANDIDATE_INPUT_INVALID",
        "MMI_LEGACY_STEP1_CANDIDATE_INSTRUMENT_MISMATCHED",
        "MMI_LEGACY_STEP1_CANDIDATE_NON_EXPECTED",
        "MMI_LEGACY_STEP1_CANDIDATE_PROJECTION_INVALID",
        "MMI_LEGACY_STEP1_CANDIDATE_RESOURCE_LIMIT_EXCEEDED",
        "MMI_LEGACY_STEP1_CANDIDATE_SCHEMA_INVALID",
        "MMI_LEGACY_STEP1_CANDIDATE_SOURCE_INCONSISTENT",
        "MMI_LEGACY_STEP1_CANDIDATE_UPSTREAM_RESPONSE_INVALID",
    }
)
_H2_RESPONSE_CODES: Final = frozenset(
    {"MMI_LEGACY_STEP1_COMPARISON_RESOURCE_LIMIT_EXCEEDED"}
)
_H2_CODES: Final = frozenset(
    {
        "MMI_LEGACY_STEP1_COMPARISON_CONTRACT_UNSUPPORTED",
        "MMI_LEGACY_STEP1_COMPARISON_H1_CANDIDATE_INVALID",
        "MMI_LEGACY_STEP1_COMPARISON_IDENTITY_MISMATCHED",
        "MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID",
        "MMI_LEGACY_STEP1_COMPARISON_NON_EXPECTED",
        "MMI_LEGACY_STEP1_COMPARISON_RESOURCE_LIMIT_EXCEEDED",
        "MMI_LEGACY_STEP1_COMPARISON_SCHEMA_INVALID",
    }
)
_CLOCK_CODES: Final = frozenset(
    {
        "MMI_CLOCK_READ_FAILED",
        "MMI_CLOCK_RESULT_INVALID",
        "MMI_CLOCK_TIMESTAMP_NAIVE",
        "MMI_CLOCK_TIMESTAMP_NOT_UTC",
    }
)
_CONTROLLED_INPUT_ERRNOS: Final = frozenset(
    {
        errno.EACCES,
        errno.EISDIR,
        errno.ELOOP,
        errno.ENAMETOOLONG,
        errno.ENOENT,
        errno.ENOTDIR,
        errno.ENXIO,
        errno.EPERM,
        errno.ESTALE,
    }
)
_CONTROLLED_CAPABILITY_ERRNOS: Final = frozenset(
    {errno.EMFILE, errno.ENFILE}
)
_CONTROLLED_PERSISTENCE_ERRNOS: Final = frozenset(
    {
        errno.EACCES,
        errno.EDQUOT,
        errno.EEXIST,
        errno.EFBIG,
        errno.EINTR,
        errno.EIO,
        errno.EISDIR,
        errno.ELOOP,
        errno.EMFILE,
        errno.ENAMETOOLONG,
        errno.ENFILE,
        errno.ENOENT,
        errno.ENOSPC,
        errno.ENOTDIR,
        errno.EPERM,
        errno.EROFS,
    }
)
_SOURCE_REASON_PREFIXES: Final = ("MMI_SOURCE_",)
_POLICY_REASON_PREFIXES: Final = (
    "MMI_POLICY_",
    "MMI_UNIVERSE_",
    "POLICY_",
    "EXTENDED_",
)
_PORTFOLIO_REASON_PREFIXES: Final = (
    "MMI_PORTFOLIO_",
    "MMI_PROJECTION_",
    "PORTFOLIO_",
)
_EVIDENCE_REASON_PREFIXES: Final = (
    "MMI_AUTHENTICATED_",
    "MMI_EVIDENCE_",
    "POLICY_",
    "PORTFOLIO_",
    "EXTENDED_",
)
_VIEW_REASON_PREFIXES: Final = ("MMI_ANALYST_VIEW_V2_", "VIEW_")


def _raise_controlled(
    code: H2cManualCaptureErrorCode,
    *,
    owner_reason_codes: tuple[str, ...] = (),
) -> NoReturn:
    raise H2cManualCaptureError(
        code=code,
        failure_class=_ERROR_CLASSES[code],
        owner_reason_codes=owner_reason_codes,
    ) from None


def _raise_named_owner(
    *,
    observed_code: object,
    allowed_codes: frozenset[str],
    response_codes: frozenset[str],
    response_public_code: H2cManualCaptureErrorCode,
    remaining_public_code: H2cManualCaptureErrorCode,
) -> NoReturn:
    if type(observed_code) is not str or observed_code not in allowed_codes:
        raise RuntimeError("undocumented MMI owner error code")
    _raise_controlled(
        response_public_code
        if observed_code in response_codes
        else remaining_public_code,
        owner_reason_codes=(observed_code,),
    )


def _validate_arguments(
    *,
    strategy_settings_expected_sha256: object,
    portfolio_snapshot_expected_sha256: object,
    paths: tuple[object, ...],
    operator_handoff: object,
) -> tuple[Path, ...]:
    if (
        type(strategy_settings_expected_sha256) is not str
        or _SHA256_RE.fullmatch(strategy_settings_expected_sha256) is None
        or type(portfolio_snapshot_expected_sha256) is not str
        or _SHA256_RE.fullmatch(portfolio_snapshot_expected_sha256) is None
        or not isinstance(operator_handoff, H2cOperatorHandoff)
    ):
        _raise_controlled(H2cManualCaptureErrorCode.H2C_ARGUMENT_INVALID)
    if any(not isinstance(path, Path) for path in paths):
        _raise_controlled(H2cManualCaptureErrorCode.H2C_ARGUMENT_INVALID)
    normalized_strings = tuple(
        os.path.normpath(os.fspath(path)) for path in paths
    )
    if (
        any(not os.path.isabs(path) for path in normalized_strings)
        or len(set(normalized_strings)) != len(normalized_strings)
        or any(os.path.lexists(path) for path in normalized_strings)
        or any(
            not os.path.exists(os.path.dirname(path))
            or not os.path.isdir(os.path.dirname(path))
            for path in normalized_strings
        )
    ):
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_PATH_CONTRACT_INVALID
        )
    return tuple(Path(path) for path in normalized_strings)


def _require_filesystem_capabilities() -> None:
    required = ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY")
    if any(not hasattr(os, name) for name in required):
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_CAPABILITY_UNAVAILABLE
        )


def _result_reason_codes(
    value: object,
    *,
    allowed_prefixes: tuple[str, ...],
) -> tuple[str, ...]:
    reasons = getattr(value, "reason_codes", None)
    if (
        type(reasons) is not tuple
        or any(type(code) is not str for code in reasons)
        or any(
            not any(code.startswith(prefix) for prefix in allowed_prefixes)
            for code in reasons
        )
    ):
        raise RuntimeError("malformed MMI result reason codes")
    return reasons


def _require_source_capture(
    result: object,
    *,
    role: MmiSourceRole,
) -> MmiCapturedSource:
    if type(result) is not MmiSourceCaptureResult:
        raise RuntimeError("malformed MMI source capture result")
    reasons = _result_reason_codes(
        result,
        allowed_prefixes=_SOURCE_REASON_PREFIXES,
    )
    if (
        result.status
        in {
            MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE,
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        }
        and result.authority_effect == AUTHORITY_EFFECT_NONE
        and type(result.source) is MmiCapturedSource
        and result.source.role is role
    ):
        return result.source
    if result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_SOURCE_CAPTURE_INVALID,
            owner_reason_codes=reasons,
        )
    if result.status is MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_LIVE_CHAIN_INVALID,
            owner_reason_codes=reasons,
        )
    raise RuntimeError("malformed MMI source capture result")


def _require_projection_build(
    result: object,
    *,
    expected_type: type[
        MmiPolicyProjectionBuildResult | MmiPortfolioProjectionBuildResult
    ],
    failure_code: H2cManualCaptureErrorCode = (
        H2cManualCaptureErrorCode.H2C_LIVE_CHAIN_INVALID
    ),
    allowed_reason_prefixes: tuple[str, ...],
) -> Mapping[str, object]:
    if type(result) is not expected_type:
        raise RuntimeError("malformed MMI projection build result")
    reasons = _result_reason_codes(
        result,
        allowed_prefixes=allowed_reason_prefixes,
    )
    if (
        result.status
        in {
            MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE,
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        }
        and result.authority_effect == AUTHORITY_EFFECT_NONE
        and isinstance(result.projection, Mapping)
    ):
        return result.projection
    if result.status in {
        MmiProjectionResultCategory.PROJECTION_BLOCKED,
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
    }:
        _raise_controlled(
            failure_code,
            owner_reason_codes=reasons,
        )
    raise RuntimeError("malformed MMI projection build result")


def _require_projection_validation(
    result: object,
    *,
    expected_type: type[
        MmiPolicyProjectionValidationResult
        | MmiPortfolioProjectionValidationResult
    ],
    failure_code: H2cManualCaptureErrorCode = (
        H2cManualCaptureErrorCode.H2C_LIVE_CHAIN_INVALID
    ),
    allowed_reason_prefixes: tuple[str, ...],
) -> None:
    if type(result) is not expected_type:
        raise RuntimeError("malformed MMI projection validation result")
    reasons = _result_reason_codes(
        result,
        allowed_prefixes=allowed_reason_prefixes,
    )
    if (
        result.status
        in {
            MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE,
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        }
        and result.authority_effect == AUTHORITY_EFFECT_NONE
    ):
        return
    if result.status in {
        MmiProjectionResultCategory.PROJECTION_BLOCKED,
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
    }:
        _raise_controlled(
            failure_code,
            owner_reason_codes=reasons,
        )
    raise RuntimeError("malformed MMI projection validation result")


def _legacy_text(exact_bytes: bytes) -> str:
    try:
        return io.TextIOWrapper(
            io.BytesIO(exact_bytes),
            encoding="utf-8",
            errors="strict",
            newline=None,
        ).read()
    except UnicodeDecodeError:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID
        )


def _derive_approved_list(settings_text: str) -> str:
    try:
        return derive_legacy_approved_extended_etf_json(
            strategy_settings_text=settings_text
        )
    except (StrategySettingsValidationError, yaml.YAMLError):
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID
        )
    except ValueError as exc:
        if str(exc) in _APPROVED_LIST_VALUE_ERRORS:
            _raise_controlled(
                H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID
            )
        raise


def _compile_legacy_prompt(
    *,
    template_text: str,
    settings_text: str,
    portfolio_text: str,
    approved_list_json: str,
) -> bytes:
    try:
        text = compile_legacy_step1_prompt_text(
            template_text=template_text,
            strategy_settings_text=settings_text,
            portfolio_snapshot_text=portfolio_text,
            approved_extended_etf_json=approved_list_json,
        )
        exact_bytes = text.encode("utf-8")
    except PromptRenderError:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID
        )
    if not 1 <= len(exact_bytes) <= _LEGACY_PROMPT_MAXIMUM_BYTES:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID
        )
    return exact_bytes


def _parse_legacy_settings(settings_text: str) -> dict[str, object]:
    try:
        value = parse_strategy_settings_text(settings_text)
    except (StrategySettingsValidationError, yaml.YAMLError):
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID
        )
    if type(value) is not dict:
        raise RuntimeError("legacy settings owner returned a non-dict")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID
        )
    if len(encoded) > _LEGACY_SETTINGS_CANONICAL_MAXIMUM_BYTES:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID
        )
    return value


@dataclass(frozen=True, slots=True)
class _CreatedFile:
    path: Path
    device: int
    inode: int


def _translate_persistence_oserror(
    exc: OSError,
    *,
    code: H2cManualCaptureErrorCode,
) -> NoReturn:
    if exc.errno not in _CONTROLLED_PERSISTENCE_ERRNOS:
        raise exc
    _raise_controlled(code)


def _fsync_parent(path: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    parent_fd = os.open(os.fspath(path.parent), flags)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _write_new_exact_file(
    *,
    path: Path,
    exact_bytes: bytes,
    failure_code: H2cManualCaptureErrorCode,
) -> _CreatedFile:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | os.O_CLOEXEC
    )
    fd: int | None = None
    try:
        fd = os.open(os.fspath(path), flags, 0o600)
        witness = os.fstat(fd)
        offset = 0
        while offset < len(exact_bytes):
            written = os.write(fd, exact_bytes[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = None
        _fsync_parent(path)
    except OSError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _translate_persistence_oserror(exc, code=failure_code)
    return _CreatedFile(path=path, device=witness.st_dev, inode=witness.st_ino)


def _best_effort_remove_created_file(created: _CreatedFile) -> None:
    try:
        observed = os.lstat(created.path)
        if (observed.st_dev, observed.st_ino) == (
            created.device,
            created.inode,
        ):
            os.unlink(created.path)
            _fsync_parent(created.path)
    except OSError:
        return


@dataclass(frozen=True, slots=True)
class _ReadWitness:
    device: int
    inode: int
    size: int
    modification_time_ns: int
    change_time_ns: int


def _read_witness(value: os.stat_result) -> _ReadWitness:
    return _ReadWitness(
        device=value.st_dev,
        inode=value.st_ino,
        size=value.st_size,
        modification_time_ns=value.st_mtime_ns,
        change_time_ns=value.st_ctime_ns,
    )


def _read_to_eof_once(fd: int, *, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    observed = 0
    while True:
        chunk = os.read(fd, min(_READ_CHUNK_BYTES, maximum_bytes + 1 - observed))
        if not chunk:
            break
        chunks.append(chunk)
        observed += len(chunk)
        if observed > maximum_bytes:
            break
    return b"".join(chunks)


def _open_stable_regular_file(path: Path) -> tuple[int, _ReadWitness]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    fd = os.open(os.fspath(path), flags)
    try:
        status = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if not stat.S_ISREG(status.st_mode):
        os.close(fd)
        raise OSError(errno.EINVAL, "not a regular file")
    return fd, _read_witness(status)


def _translate_response_oserror(exc: OSError) -> NoReturn:
    if exc.errno in _CONTROLLED_INPUT_ERRNOS or exc.errno == errno.EINVAL:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_RESPONSE_INPUT_INVALID
        )
    if exc.errno in _CONTROLLED_CAPABILITY_ERRNOS:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_CAPABILITY_UNAVAILABLE
        )
    raise exc


def _stable_read_response_pair(
    *,
    h1_path: Path,
    legacy_path: Path,
) -> tuple[bytes, bytes]:
    h1_fd: int | None = None
    legacy_fd: int | None = None
    try:
        h1_fd, h1_before = _open_stable_regular_file(h1_path)
        legacy_fd, legacy_before = _open_stable_regular_file(legacy_path)
        if (h1_before.device, h1_before.inode) == (
            legacy_before.device,
            legacy_before.inode,
        ):
            _raise_controlled(
                H2cManualCaptureErrorCode.H2C_RESPONSE_INPUT_INVALID
            )
        if (
            h1_before.size < 0
            or h1_before.size > MAXIMUM_MMI_RAW_RESPONSE_BYTES
            or legacy_before.size < 0
            or legacy_before.size > MAX_LEGACY_RESEARCH_RAW_BYTES
        ):
            _raise_controlled(
                H2cManualCaptureErrorCode.H2C_RESPONSE_INPUT_INVALID
            )
        h1_bytes = _read_to_eof_once(
            h1_fd,
            maximum_bytes=MAXIMUM_MMI_RAW_RESPONSE_BYTES,
        )
        legacy_bytes = _read_to_eof_once(
            legacy_fd,
            maximum_bytes=MAX_LEGACY_RESEARCH_RAW_BYTES,
        )
        h1_after = _read_witness(os.fstat(h1_fd))
        legacy_after = _read_witness(os.fstat(legacy_fd))
        if (
            h1_before != h1_after
            or legacy_before != legacy_after
            or len(h1_bytes) != h1_before.size
            or len(legacy_bytes) != legacy_before.size
        ):
            _raise_controlled(
                H2cManualCaptureErrorCode.H2C_RESPONSE_INPUT_INVALID
            )
        return h1_bytes, legacy_bytes
    except OSError as exc:
        _translate_response_oserror(exc)
    finally:
        if legacy_fd is not None:
            os.close(legacy_fd)
        if h1_fd is not None:
            os.close(h1_fd)


def _stable_read_legacy_template(path: Path) -> bytes:
    fd: int | None = None
    try:
        fd, before = _open_stable_regular_file(path)
        if before.size < 1 or before.size > _LEGACY_TEMPLATE_MAXIMUM_BYTES:
            _raise_controlled(
                H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID
            )
        exact_bytes = _read_to_eof_once(
            fd,
            maximum_bytes=_LEGACY_TEMPLATE_MAXIMUM_BYTES,
        )
        after = _read_witness(os.fstat(fd))
        if before != after or len(exact_bytes) != before.size:
            _raise_controlled(
                H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID
            )
        return exact_bytes
    except OSError as exc:
        if exc.errno in _CONTROLLED_INPUT_ERRNOS or exc.errno == errno.EINVAL:
            _raise_controlled(
                H2cManualCaptureErrorCode.H2C_LEGACY_COMPILER_INVALID
            )
        if exc.errno in _CONTROLLED_CAPABILITY_ERRNOS:
            _raise_controlled(
                H2cManualCaptureErrorCode.H2C_CAPABILITY_UNAVAILABLE
            )
        raise
    finally:
        if fd is not None:
            os.close(fd)


def _build_receipt(
    *,
    run_context: MmiProjectionRunContext,
    settings_source: MmiCapturedSource,
    portfolio_source: MmiCapturedSource,
    policy_projection: Mapping[str, object],
    portfolio_projection: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    legacy_step1_compatibility_candidate: Mapping[str, object],
    h1_response_bytes: bytes,
    legacy_response_bytes: bytes,
    legacy_strategy_settings: Mapping[str, object],
    legacy_template_bytes: bytes,
    legacy_prompt_bytes: bytes,
    comparison_report: Mapping[str, object],
) -> dict[str, object]:
    try:
        validated_h2 = validate_mmi_legacy_step1_comparison_report_v1(
            value=comparison_report,
            legacy_step1_compatibility_candidate=(
                legacy_step1_compatibility_candidate
            ),
            validated_grounded_analysis_response=(
                validated_grounded_analysis_response
            ),
            raw_response_envelope=raw_response_envelope,
            evidence_bundle=evidence_bundle,
            policy_projection=policy_projection,
            policy_source=settings_source,
            portfolio_projection=portfolio_projection,
            portfolio_source=portfolio_source,
            run_context=run_context,
            legacy_research_raw_bytes=legacy_response_bytes,
            legacy_strategy_settings=legacy_strategy_settings,
        )
    except MmiLegacyStep1ComparisonReportV1Error as exc:
        _raise_named_owner(
            observed_code=exc.code,
            allowed_codes=_H2_CODES,
            response_codes=_H2_RESPONSE_CODES,
            response_public_code=(
                H2cManualCaptureErrorCode.H2C_RESPONSE_CONTENT_INVALID
            ),
            remaining_public_code=(
                H2cManualCaptureErrorCode.H2C_H2_VALIDATION_INVALID
            ),
        )
    if validated_h2 != comparison_report or not h1_response_bytes:
        raise RuntimeError("receipt builder received a non-validated live chain")
    settings_identity = settings_source.source_record.get(
        "source_record_identity_sha256"
    )
    portfolio_identity = portfolio_source.source_record.get(
        "source_record_identity_sha256"
    )
    h2_identity = comparison_report.get("comparison_report_identity_sha256")
    if not all(
        type(value) is str and _SHA256_RE.fullmatch(value) is not None
        for value in (settings_identity, portfolio_identity, h2_identity)
    ):
        raise RuntimeError("validated live owner omitted a persistent identity")
    receipt: dict[str, object] = {
        "schema_version": (
            "mmi_h2c_dual_side_manual_handoff_context_receipt_v1"
        ),
        "artifact_kind": (
            "MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT"
        ),
        "capture_contract_version": "mmi_h2c_manual_capture_v1",
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "live_context_validated_at_capture": True,
        "operator_h1_response_bytes_bound_at_capture": True,
        "operator_legacy_response_bytes_bound_at_capture": True,
        "provider_origin_authentication": "NOT_ESTABLISHED",
        "evaluation_timestamp_utc": run_context.evaluation_timestamp_utc,
        "strategy_settings_source_record_identity_sha256": settings_identity,
        "portfolio_snapshot_source_record_identity_sha256": (
            portfolio_identity
        ),
        "legacy_prompt_template_sha256": hashlib.sha256(
            legacy_template_bytes
        ).hexdigest(),
        "legacy_prompt_sha256": hashlib.sha256(
            legacy_prompt_bytes
        ).hexdigest(),
        "comparison_report_identity_sha256": h2_identity,
        "receipt_identity_sha256": _ZERO_SHA256,
    }
    try:
        receipt["receipt_identity_sha256"] = record_identity_sha256(
            receipt,
            identity_field="receipt_identity_sha256",
            domain=(
                _MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_IDENTITY_DOMAIN
            ),
            maximum_bytes=(
                MAX_MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_RECEIPT_VALIDATION_INVALID
        )
    try:
        validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1(
            receipt=receipt
        )
    except MmiH2cDualSideManualHandoffContextReceiptV1Error as exc:
        if exc.code != "MMI_H2C_RECEIPT_V1_INVALID":
            raise
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_RECEIPT_VALIDATION_INVALID,
            owner_reason_codes=(exc.code,),
        )
    return receipt


def _owner_calls(
    *,
    settings_source: MmiCapturedSource,
    portfolio_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> tuple[
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    dict[str, object],
]:
    policy = _require_projection_build(
        build_mmi_policy_projection(
            settings_source,
            run_context=run_context,
        ),
        expected_type=MmiPolicyProjectionBuildResult,
        allowed_reason_prefixes=_POLICY_REASON_PREFIXES,
    )
    _require_projection_validation(
        validate_mmi_policy_projection(
            policy,
            source=settings_source,
            run_context=run_context,
        ),
        expected_type=MmiPolicyProjectionValidationResult,
        allowed_reason_prefixes=_POLICY_REASON_PREFIXES,
    )
    portfolio = _require_projection_build(
        build_mmi_portfolio_snapshot_projection(
            portfolio_source,
            policy_projection=policy,
            policy_source=settings_source,
            run_context=run_context,
        ),
        expected_type=MmiPortfolioProjectionBuildResult,
        failure_code=(
            H2cManualCaptureErrorCode.H2C_PORTFOLIO_NOT_COMPARABLE
        ),
        allowed_reason_prefixes=_PORTFOLIO_REASON_PREFIXES,
    )
    _require_projection_validation(
        validate_mmi_portfolio_snapshot_projection(
            portfolio,
            portfolio_source=portfolio_source,
            policy_projection=policy,
            policy_source=settings_source,
            run_context=run_context,
        ),
        expected_type=MmiPortfolioProjectionValidationResult,
        failure_code=(
            H2cManualCaptureErrorCode.H2C_PORTFOLIO_NOT_COMPARABLE
        ),
        allowed_reason_prefixes=_PORTFOLIO_REASON_PREFIXES,
    )
    if portfolio.get("portfolio_source_record_identity_sha256") != (
        portfolio_source.source_record.get("source_record_identity_sha256")
    ):
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_PORTFOLIO_NOT_COMPARABLE
        )
    evidence = _require_projection_build(
        _evidence_bundle.build_mmi_authenticated_evidence_bundle(
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        ),
        expected_type=MmiPolicyProjectionBuildResult,
        allowed_reason_prefixes=_EVIDENCE_REASON_PREFIXES,
    )
    _require_projection_validation(
        _evidence_bundle.validate_mmi_authenticated_evidence_bundle(
            evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        ),
        expected_type=MmiPolicyProjectionValidationResult,
        allowed_reason_prefixes=_EVIDENCE_REASON_PREFIXES,
    )
    view = _require_projection_build(
        build_mmi_analyst_visible_evidence_view_v2(
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        ),
        expected_type=MmiPolicyProjectionBuildResult,
        allowed_reason_prefixes=_VIEW_REASON_PREFIXES,
    )
    _require_projection_validation(
        validate_mmi_analyst_visible_evidence_view_v2(
            value=view,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        ),
        expected_type=MmiPolicyProjectionValidationResult,
        allowed_reason_prefixes=_VIEW_REASON_PREFIXES,
    )
    try:
        prompt = build_mmi_grounded_prompt_v2(
            analyst_visible_evidence_view=view,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
        prompt = validate_mmi_grounded_prompt_v2(
            value=prompt,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except MmiGroundedPromptV2Error as exc:
        _raise_named_owner(
            observed_code=exc.code,
            allowed_codes=_G2_CODES,
            response_codes=_G2_PROMPT_CODES,
            response_public_code=(
                H2cManualCaptureErrorCode.H2C_PROMPT_CONTRACT_INVALID
            ),
            remaining_public_code=(
                H2cManualCaptureErrorCode.H2C_LIVE_CHAIN_INVALID
            ),
        )
    return policy, portfolio, evidence, view, prompt


def _revalidate_live_chain(
    *,
    settings_source: MmiCapturedSource,
    portfolio_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
    policy: Mapping[str, object],
    portfolio: Mapping[str, object],
    evidence: Mapping[str, object],
    view: Mapping[str, object],
    prompt: Mapping[str, object],
) -> None:
    _require_projection_validation(
        validate_mmi_policy_projection(
            policy,
            source=settings_source,
            run_context=run_context,
        ),
        expected_type=MmiPolicyProjectionValidationResult,
        allowed_reason_prefixes=_POLICY_REASON_PREFIXES,
    )
    _require_projection_validation(
        validate_mmi_portfolio_snapshot_projection(
            portfolio,
            portfolio_source=portfolio_source,
            policy_projection=policy,
            policy_source=settings_source,
            run_context=run_context,
        ),
        expected_type=MmiPortfolioProjectionValidationResult,
        allowed_reason_prefixes=_PORTFOLIO_REASON_PREFIXES,
    )
    _require_projection_validation(
        _evidence_bundle.validate_mmi_authenticated_evidence_bundle(
            evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        ),
        expected_type=MmiPolicyProjectionValidationResult,
        allowed_reason_prefixes=_EVIDENCE_REASON_PREFIXES,
    )
    _require_projection_validation(
        validate_mmi_analyst_visible_evidence_view_v2(
            value=view,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        ),
        expected_type=MmiPolicyProjectionValidationResult,
        allowed_reason_prefixes=_VIEW_REASON_PREFIXES,
    )
    try:
        validated_prompt = validate_mmi_grounded_prompt_v2(
            value=prompt,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except MmiGroundedPromptV2Error as exc:
        _raise_named_owner(
            observed_code=exc.code,
            allowed_codes=_G2_CODES,
            response_codes=_G2_PROMPT_CODES,
            response_public_code=(
                H2cManualCaptureErrorCode.H2C_PROMPT_CONTRACT_INVALID
            ),
            remaining_public_code=(
                H2cManualCaptureErrorCode.H2C_LIVE_CHAIN_INVALID
            ),
        )
    if validated_prompt != prompt:
        raise RuntimeError("G2 live validator returned a non-equal snapshot")


def run_h2c_manual_capture(
    *,
    strategy_settings_expected_sha256: str,
    portfolio_snapshot_expected_sha256: str,
    h1_prompt_output_path: Path,
    legacy_prompt_output_path: Path,
    h1_response_path: Path,
    legacy_response_path: Path,
    case_evidence_bundle_output_path: Path,
    comparison_report_output_path: Path,
    receipt_output_path: Path,
    operator_handoff: H2cOperatorHandoff,
) -> H2cManualCaptureResult:
    """Run one complete foreground H2c manual capture lifecycle."""
    paths = _validate_arguments(
        strategy_settings_expected_sha256=(
            strategy_settings_expected_sha256
        ),
        portfolio_snapshot_expected_sha256=(
            portfolio_snapshot_expected_sha256
        ),
        paths=(
            h1_prompt_output_path,
            legacy_prompt_output_path,
            h1_response_path,
            legacy_response_path,
            case_evidence_bundle_output_path,
            comparison_report_output_path,
            receipt_output_path,
        ),
        operator_handoff=operator_handoff,
    )
    _require_filesystem_capabilities()
    (
        h1_prompt_path,
        legacy_prompt_path,
        h1_response_path,
        legacy_response_path,
        case_evidence_bundle_output_path,
        h2_output_path,
        receipt_output_path,
    ) = paths
    try:
        run_context = begin_mmi_projection_run()
    except MmiClockContractError as exc:
        if exc.args != (exc.args[0],) or exc.args[0] not in _CLOCK_CODES:
            raise
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_CAPABILITY_UNAVAILABLE
        )
    settings_source = _require_source_capture(
        capture_current_mmi_source(
            MmiSourceRole.STRATEGY_SETTINGS,
            expected_source_sha256=strategy_settings_expected_sha256,
        ),
        role=MmiSourceRole.STRATEGY_SETTINGS,
    )
    portfolio_source = _require_source_capture(
        capture_current_mmi_source(
            MmiSourceRole.PORTFOLIO_SNAPSHOT,
            expected_source_sha256=portfolio_snapshot_expected_sha256,
        ),
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
    )
    settings_text = _legacy_text(settings_source.raw_bytes)
    portfolio_text = _legacy_text(portfolio_source.raw_bytes)
    if not portfolio_source.raw_bytes or not portfolio_text.strip():
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_PORTFOLIO_NOT_COMPARABLE
        )
    policy, portfolio, evidence, view, prompt = _owner_calls(
        settings_source=settings_source,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    template_bytes = _stable_read_legacy_template(
        prompt_path(_FIXED_LEGACY_TEMPLATE_NAME)
    )
    template_text = _legacy_text(template_bytes)
    approved_list_json = _derive_approved_list(settings_text)
    legacy_prompt_bytes = _compile_legacy_prompt(
        template_text=template_text,
        settings_text=settings_text,
        portfolio_text=portfolio_text,
        approved_list_json=approved_list_json,
    )
    prompt_text = prompt.get("prompt_text")
    if type(prompt_text) is not str:
        raise RuntimeError("validated G2 omitted prompt_text")
    try:
        h1_prompt_bytes = prompt_text.encode("utf-8")
    except UnicodeEncodeError:
        raise RuntimeError("validated G2 prompt is not UTF-8 encodable") from None
    h1_created = _write_new_exact_file(
        path=h1_prompt_path,
        exact_bytes=h1_prompt_bytes,
        failure_code=H2cManualCaptureErrorCode.H2C_PROMPT_EXPOSURE_FAILED,
    )
    try:
        _write_new_exact_file(
            path=legacy_prompt_path,
            exact_bytes=legacy_prompt_bytes,
            failure_code=(
                H2cManualCaptureErrorCode.H2C_PROMPT_EXPOSURE_FAILED
            ),
        )
    except (H2cManualCaptureError, OSError):
        _best_effort_remove_created_file(h1_created)
        raise
    try:
        handoff_result = operator_handoff.await_response_files_ready()
    except KeyboardInterrupt:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_OPERATOR_CANCELLED
        )
    if handoff_result is not None:
        raise RuntimeError("operator handoff returned a non-None result")
    h1_response_bytes, legacy_response_bytes = _stable_read_response_pair(
        h1_path=h1_response_path,
        legacy_path=legacy_response_path,
    )
    _revalidate_live_chain(
        settings_source=settings_source,
        portfolio_source=portfolio_source,
        run_context=run_context,
        policy=policy,
        portfolio=portfolio,
        evidence=evidence,
        view=view,
        prompt=prompt,
    )
    try:
        r1 = build_mmi_raw_response_envelope_v2(
            grounded_prompt=prompt,
            raw_response_bytes=h1_response_bytes,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
        r1 = validate_mmi_raw_response_envelope_v2(
            value=r1,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except MmiRawResponseEnvelopeV2Error as exc:
        _raise_named_owner(
            observed_code=exc.code,
            allowed_codes=_R1_CODES,
            response_codes=_R1_RESPONSE_CODES,
            response_public_code=(
                H2cManualCaptureErrorCode.H2C_RESPONSE_CONTENT_INVALID
            ),
            remaining_public_code=(
                H2cManualCaptureErrorCode.H2C_LIVE_CHAIN_INVALID
            ),
        )
    try:
        r2 = build_mmi_validated_grounded_analysis_response_v2(
            raw_response_envelope=r1,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
        r2 = validate_mmi_validated_grounded_analysis_response_v2(
            value=r2,
            raw_response_envelope=r1,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except MmiValidatedGroundedAnalysisResponseV2Error as exc:
        _raise_named_owner(
            observed_code=exc.code,
            allowed_codes=_R2_CODES,
            response_codes=_R2_RESPONSE_CODES,
            response_public_code=(
                H2cManualCaptureErrorCode.H2C_RESPONSE_CONTENT_INVALID
            ),
            remaining_public_code=(
                H2cManualCaptureErrorCode.H2C_LIVE_CHAIN_INVALID
            ),
        )
    try:
        h1 = build_mmi_legacy_step1_compatibility_candidate_v1(
            validated_grounded_analysis_response=r2,
            raw_response_envelope=r1,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
        h1 = validate_mmi_legacy_step1_compatibility_candidate_v1(
            value=h1,
            validated_grounded_analysis_response=r2,
            raw_response_envelope=r1,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except MmiLegacyStep1CompatibilityCandidateV1Error as exc:
        if type(exc.code) is not str or exc.code not in _H1_CODES:
            raise RuntimeError("undocumented H1 owner error code")
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_LIVE_CHAIN_INVALID,
            owner_reason_codes=(exc.code,),
        )
    try:
        case_evidence_bundle = (
            _case_bundle.build_mmi_h2c_case_evidence_bundle_v1(
                grounded_prompt=prompt,
                raw_response_envelope=r1,
                validated_grounded_analysis_response=r2,
                legacy_step1_compatibility_candidate=h1,
                strategy_settings_source_record=dict(
                    settings_source.source_record
                ),
                portfolio_snapshot_source_record=(
                    dict(portfolio_source.source_record)
                ),
            )
        )
    except _case_bundle.MmiH2cCaseEvidenceBundleV1Error as exc:
        if exc.code != "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID":
            raise
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_VALIDATION_INVALID,
            owner_reason_codes=(exc.code,),
        )
    try:
        case_evidence_bundle_bytes = canonical_json_bytes(
            case_evidence_bundle,
            maximum_bytes=(
                MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_VALIDATION_INVALID
        )
    recomputed_legacy_prompt = _compile_legacy_prompt(
        template_text=template_text,
        settings_text=settings_text,
        portfolio_text=portfolio_text,
        approved_list_json=approved_list_json,
    )
    if recomputed_legacy_prompt != legacy_prompt_bytes:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_PROMPT_CONTRACT_INVALID
        )
    legacy_settings = _parse_legacy_settings(settings_text)
    try:
        legacy_response_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_RESPONSE_CONTENT_INVALID
        )
    try:
        h2 = build_mmi_legacy_step1_comparison_report_v1(
            legacy_step1_compatibility_candidate=h1,
            validated_grounded_analysis_response=r2,
            raw_response_envelope=r1,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
            legacy_research_raw_bytes=legacy_response_bytes,
            legacy_strategy_settings=legacy_settings,
        )
        h2 = validate_mmi_legacy_step1_comparison_report_v1(
            value=h2,
            legacy_step1_compatibility_candidate=h1,
            validated_grounded_analysis_response=r2,
            raw_response_envelope=r1,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
            legacy_research_raw_bytes=legacy_response_bytes,
            legacy_strategy_settings=legacy_settings,
        )
    except MmiLegacyStep1ComparisonReportV1Error as exc:
        _raise_named_owner(
            observed_code=exc.code,
            allowed_codes=_H2_CODES,
            response_codes=_H2_RESPONSE_CODES,
            response_public_code=(
                H2cManualCaptureErrorCode.H2C_RESPONSE_CONTENT_INVALID
            ),
            remaining_public_code=(
                H2cManualCaptureErrorCode.H2C_H2_VALIDATION_INVALID
            ),
        )
    try:
        h2_bytes = canonical_json_bytes(
            h2,
            maximum_bytes=(
                MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_H2_VALIDATION_INVALID
        )
    receipt = _build_receipt(
        run_context=run_context,
        settings_source=settings_source,
        portfolio_source=portfolio_source,
        policy_projection=policy,
        portfolio_projection=portfolio,
        evidence_bundle=evidence,
        raw_response_envelope=r1,
        validated_grounded_analysis_response=r2,
        legacy_step1_compatibility_candidate=h1,
        h1_response_bytes=h1_response_bytes,
        legacy_response_bytes=legacy_response_bytes,
        legacy_strategy_settings=legacy_settings,
        legacy_template_bytes=template_bytes,
        legacy_prompt_bytes=legacy_prompt_bytes,
        comparison_report=h2,
    )
    try:
        receipt_bytes = canonical_json_bytes(
            receipt,
            maximum_bytes=(
                MAX_MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _raise_controlled(
            H2cManualCaptureErrorCode.H2C_RECEIPT_VALIDATION_INVALID
        )
    _write_new_exact_file(
        path=case_evidence_bundle_output_path,
        exact_bytes=case_evidence_bundle_bytes,
        failure_code=(
            H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED
        ),
    )
    _write_new_exact_file(
        path=h2_output_path,
        exact_bytes=h2_bytes,
        failure_code=H2cManualCaptureErrorCode.H2C_H2_PERSISTENCE_FAILED,
    )
    _write_new_exact_file(
        path=receipt_output_path,
        exact_bytes=receipt_bytes,
        failure_code=(
            H2cManualCaptureErrorCode.H2C_RECEIPT_PERSISTENCE_FAILED
        ),
    )
    h2_identity = h2.get("comparison_report_identity_sha256")
    receipt_identity = receipt.get("receipt_identity_sha256")
    if type(h2_identity) is not str or type(receipt_identity) is not str:
        raise RuntimeError("validated artifacts omitted persistent identities")
    return H2cManualCaptureResult(
        comparison_report_identity_sha256=h2_identity,
        receipt_identity_sha256=receipt_identity,
    )
