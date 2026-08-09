"""Dormant Phase B engine that consumes one persisted H2c case and exits.

Phase B consumes a prepared case, verifies the exact live source fidelity,
rebuilds and proves exact prompt equality, binds two operator-supplied raw
responses, and writes the three report-only persisted artifacts.

It has no provider, network, browser, polling, retry, thread, subprocess,
scheduler, availability, permission, gate, publication, pointer, order, broker
or execution behavior, and no production consumer.
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
from typing import Final, NoReturn

import yaml

from investment_orchestrator.llm.legacy_step1_prompt_compiler import (
    compile_legacy_step1_prompt_text,
    derive_legacy_approved_extended_etf_json,
)
from investment_orchestrator.llm.manual_output import PromptRenderError
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    _build_mmi_analyst_visible_evidence_view_v2_from_source_record_identities,
    build_mmi_analyst_visible_evidence_view_v2,
    validate_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.canonical import (
    MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES,
    MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES,
    MAXIMUM_CANONICAL_JSON_BYTES,
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    MmiCanonicalizationError,
    canonical_json_bytes,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MMI_SOURCE_CATALOG,
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
    _build_mmi_grounded_prompt_v2_from_source_record_identities,
    _validate_mmi_grounded_prompt_v2_from_source_record_identities,
    build_mmi_grounded_prompt_v2,
    validate_mmi_grounded_prompt_v2,
)
from investment_orchestrator.mmi.legacy_step1_compatibility_candidate_v1 import (
    MmiLegacyStep1CompatibilityCandidateV1Error,
    _build_mmi_legacy_step1_compatibility_candidate_v1_from_source_record_identities,
    build_mmi_legacy_step1_compatibility_candidate_v1,
)
from investment_orchestrator.mmi.policy_projection import (
    _ProjectionBlocked,
    _ProjectionContractFailure,
    _build_mmi_policy_projection_from_source_bytes,
    build_mmi_policy_projection,
    validate_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    _PortfolioBlocked,
    _PortfolioContractFailure,
    _build_mmi_portfolio_snapshot_projection_from_source_bytes,
    build_mmi_portfolio_snapshot_projection,
    validate_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.mmi.raw_response_envelope_v2 import (
    MmiRawResponseEnvelopeV2Error,
    _build_mmi_raw_response_envelope_v2_from_source_record_identities,
    _validate_mmi_raw_response_envelope_v2_from_source_record_identities,
    build_mmi_raw_response_envelope_v2,
    validate_mmi_raw_response_envelope_v2,
)
from investment_orchestrator.mmi.source_capture import (
    capture_current_mmi_source,
)
from investment_orchestrator.mmi.validated_grounded_analysis_response_v2 import (
    MmiValidatedGroundedAnalysisResponseV2Error,
    _build_mmi_validated_grounded_analysis_response_v2_from_source_record_identities,
    _validate_mmi_validated_grounded_analysis_response_v2_from_source_record_identities,
    build_mmi_validated_grounded_analysis_response_v2,
    validate_mmi_validated_grounded_analysis_response_v2,
)
from investment_orchestrator.offline.mmi_h2c_archived_source_v1 import (
    MmiH2cArchivedSourceV1Error,
    _build_mmi_h2c_archived_prepared_case_snapshot,
)
from investment_orchestrator.offline.mmi_h2c_case_bundle_v1 import (
    build_mmi_h2c_case_evidence_bundle_v1,
)
from investment_orchestrator.offline.mmi_h2c_dual_side_persisted_case_receipt_v2 import (
    build_mmi_h2c_dual_side_persisted_case_receipt_v2,
)
from investment_orchestrator.offline.mmi_h2c_prepared_case_v1 import (
    resume_mmi_h2c_prepared_case_run_context,
)
from investment_orchestrator.offline.mmi_legacy_step1_comparison_report_v1 import (
    MAX_LEGACY_RESEARCH_RAW_BYTES,
    MmiLegacyStep1ComparisonReportV1Error,
    _build_mmi_legacy_step1_comparison_report_v1_from_validated_h1_candidate,
    build_mmi_legacy_step1_comparison_report_v1,
)
from investment_orchestrator.validators.strategy_settings import (
    StrategySettingsValidationError,
    parse_strategy_settings_text,
)
from investment_orchestrator.offline._mmi_h2c_stable_read_v1 import (
    MmiH2cStableReadError,
    MmiH2cStableReadErrorCode,
    _stable_read_exact_bytes as _neutral_stable_read_exact_bytes,
)


__all__ = (
    "H2cConsumeError",
    "H2cConsumeErrorCode",
    "H2cConsumeFailureClass",
    "H2cConsumeResult",
    "consume_h2c_persisted_case",
    "consume_h2c_persisted_case_from_archives",
)


class H2cConsumeFailureClass(str, Enum):
    """The exact existing H2c failure classes reachable from Phase B."""

    ARTIFACT_CONTENT = "ARTIFACT_CONTENT"
    AVAILABILITY_PERMISSION = "AVAILABILITY_PERMISSION"
    COMPILER_NORMALIZER = "COMPILER_NORMALIZER"
    OPERATOR_INPUT = "OPERATOR_INPUT"
    PERSISTENCE = "PERSISTENCE"
    PROMPT_CONTRACT = "PROMPT_CONTRACT"
    VALIDATOR_SCHEMA = "VALIDATOR_SCHEMA"
    WORKFLOW_ORCHESTRATOR = "WORKFLOW_ORCHESTRATOR"


class H2cConsumeErrorCode(str, Enum):
    """Minimal stable operator-facing error codes for Phase B consume."""

    H2C_CONSUME_ARGUMENT_INVALID = "H2C_CONSUME_ARGUMENT_INVALID"
    H2C_CONSUME_PATH_CONTRACT_INVALID = "H2C_CONSUME_PATH_CONTRACT_INVALID"
    H2C_CONSUME_CAPABILITY_UNAVAILABLE = "H2C_CONSUME_CAPABILITY_UNAVAILABLE"
    H2C_CONSUME_ARTIFACT_CONTENT_INVALID = "H2C_CONSUME_ARTIFACT_CONTENT_INVALID"
    H2C_CONSUME_MANIFEST_INVALID = "H2C_CONSUME_MANIFEST_INVALID"
    H2C_CONSUME_WORKFLOW_STATE_INVALID = "H2C_CONSUME_WORKFLOW_STATE_INVALID"
    H2C_CONSUME_SOURCE_CAPTURE_INVALID = "H2C_CONSUME_SOURCE_CAPTURE_INVALID"
    H2C_CONSUME_LIVE_CHAIN_INVALID = "H2C_CONSUME_LIVE_CHAIN_INVALID"
    H2C_CONSUME_PROMPT_CONTRACT_INVALID = "H2C_CONSUME_PROMPT_CONTRACT_INVALID"
    H2C_CONSUME_LEGACY_COMPILER_INVALID = "H2C_CONSUME_LEGACY_COMPILER_INVALID"
    H2C_CONSUME_RESPONSE_INPUT_INVALID = "H2C_CONSUME_RESPONSE_INPUT_INVALID"
    H2C_CONSUME_RESPONSE_CONTENT_INVALID = "H2C_CONSUME_RESPONSE_CONTENT_INVALID"
    H2C_CONSUME_COLLISION = "H2C_CONSUME_COLLISION"
    H2C_CONSUME_VALIDATION_INVALID = "H2C_CONSUME_VALIDATION_INVALID"
    H2C_CONSUME_PERSISTENCE_FAILED = "H2C_CONSUME_PERSISTENCE_FAILED"


_ERROR_CLASSES: Final = {
    H2cConsumeErrorCode.H2C_CONSUME_ARGUMENT_INVALID: (
        H2cConsumeFailureClass.OPERATOR_INPUT
    ),
    H2cConsumeErrorCode.H2C_CONSUME_PATH_CONTRACT_INVALID: (
        H2cConsumeFailureClass.OPERATOR_INPUT
    ),
    H2cConsumeErrorCode.H2C_CONSUME_CAPABILITY_UNAVAILABLE: (
        H2cConsumeFailureClass.AVAILABILITY_PERMISSION
    ),
    H2cConsumeErrorCode.H2C_CONSUME_ARTIFACT_CONTENT_INVALID: (
        H2cConsumeFailureClass.ARTIFACT_CONTENT
    ),
    H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID: (
        H2cConsumeFailureClass.VALIDATOR_SCHEMA
    ),
    H2cConsumeErrorCode.H2C_CONSUME_WORKFLOW_STATE_INVALID: (
        H2cConsumeFailureClass.WORKFLOW_ORCHESTRATOR
    ),
    H2cConsumeErrorCode.H2C_CONSUME_SOURCE_CAPTURE_INVALID: (
        H2cConsumeFailureClass.ARTIFACT_CONTENT
    ),
    H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID: (
        H2cConsumeFailureClass.VALIDATOR_SCHEMA
    ),
    H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID: (
        H2cConsumeFailureClass.PROMPT_CONTRACT
    ),
    H2cConsumeErrorCode.H2C_CONSUME_LEGACY_COMPILER_INVALID: (
        H2cConsumeFailureClass.COMPILER_NORMALIZER
    ),
    H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_INPUT_INVALID: (
        H2cConsumeFailureClass.OPERATOR_INPUT
    ),
    H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_CONTENT_INVALID: (
        H2cConsumeFailureClass.ARTIFACT_CONTENT
    ),
    H2cConsumeErrorCode.H2C_CONSUME_COLLISION: (
        H2cConsumeFailureClass.PERSISTENCE
    ),
    H2cConsumeErrorCode.H2C_CONSUME_VALIDATION_INVALID: (
        H2cConsumeFailureClass.VALIDATOR_SCHEMA
    ),
    H2cConsumeErrorCode.H2C_CONSUME_PERSISTENCE_FAILED: (
        H2cConsumeFailureClass.PERSISTENCE
    ),
}


@dataclass(frozen=True, slots=True, init=False)
class H2cConsumeError(RuntimeError):
    """One controlled Phase B failure with a stable public surface."""

    code: H2cConsumeErrorCode
    failure_class: H2cConsumeFailureClass
    owner_reason_codes: tuple[str, ...]

    def __init__(
        self,
        *,
        code: H2cConsumeErrorCode,
        failure_class: H2cConsumeFailureClass,
        owner_reason_codes: tuple[str, ...] = (),
    ) -> None:
        if type(code) is not H2cConsumeErrorCode:
            raise TypeError("code must be an H2cConsumeErrorCode")
        if type(failure_class) is not H2cConsumeFailureClass:
            raise TypeError(
                "failure_class must be an H2cConsumeFailureClass"
            )
        if _ERROR_CLASSES.get(code) is not failure_class:
            raise ValueError("H2c consume error code/failure-class mismatch")
        if type(owner_reason_codes) is not tuple or any(
            type(reason) is not str for reason in owner_reason_codes
        ):
            raise TypeError("owner_reason_codes must be an exact string tuple")
        RuntimeError.__init__(self, code.value)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "failure_class", failure_class)
        object.__setattr__(self, "owner_reason_codes", owner_reason_codes)


@dataclass(frozen=True, slots=True)
class H2cConsumeResult:
    """The four values one completed Phase B consumption exposes."""

    workflow_status: str
    case_evidence_bundle_identity_sha256: str
    comparison_report_identity_sha256: str
    receipt_identity_sha256: str


_WORKFLOW_STATUS: Final = "COMPLETED"
_EXPECTED_PREPARED_WORKFLOW_STATUS: Final = "AWAITING_OPERATOR_RESPONSES"
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_READ_CHUNK_BYTES: Final = 65_536
_ZERO_SHA256: Final = "0" * 64

_MANIFEST_MAXIMUM_BYTES: Final = 411_753
_LEGACY_TEMPLATE_MAXIMUM_BYTES: Final = 262_144
_LEGACY_PROMPT_MAXIMUM_BYTES: Final = 3_170_307
_H1_PROMPT_MAXIMUM_BYTES: Final = 65_536
_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES: Final = 393_852

_MANIFEST_RELATIVE_PATH: Final = "prepared/prepared_case.json"
_ARCHIVE_SETTINGS_PATH: Final = "archive/strategy_settings.yaml"
_ARCHIVE_PORTFOLIO_PATH: Final = "archive/portfolio_snapshot.txt"
_ARCHIVE_TEMPLATE_PATH: Final = "archive/research_dual_lane.txt"
_PROMPTS_H1_PATH: Final = "prompts/h1_prompt.txt"
_PROMPTS_LEGACY_PATH: Final = "prompts/legacy_prompt.txt"
_RESPONSES_H1_PATH: Final = "responses/h1_response.raw"
_RESPONSES_LEGACY_PATH: Final = "responses/legacy_response.raw"
_ARTIFACTS_BUNDLE_PATH: Final = "artifacts/case_evidence_bundle.json"
_ARTIFACTS_REPORT_PATH: Final = "artifacts/comparison_report.json"
_ARTIFACTS_RECEIPT_PATH: Final = "artifacts/receipt.json"


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

_E1_ERROR_TRANSLATION: Final = {
    "ARCHIVED_ARGUMENT_INVALID": H2cConsumeErrorCode.H2C_CONSUME_ARGUMENT_INVALID,
    "PREPARED_CASE_INPUT_INVALID": (
        H2cConsumeErrorCode.H2C_CONSUME_ARTIFACT_CONTENT_INVALID
    ),
    "PREPARED_CASE_SCHEMA_INVALID": H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID,
    "ARCHIVE_SOURCE_INPUT_INVALID": (
        H2cConsumeErrorCode.H2C_CONSUME_ARTIFACT_CONTENT_INVALID
    ),
    "ARCHIVE_SOURCE_SCHEMA_INVALID": (
        H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
    ),
    "CAPABILITY_UNAVAILABLE": H2cConsumeErrorCode.H2C_CONSUME_CAPABILITY_UNAVAILABLE,
}


def _raise_controlled(
    code: H2cConsumeErrorCode,
    *,
    owner_reason_codes: tuple[str, ...] = (),
) -> NoReturn:
    raise H2cConsumeError(
        code=code,
        failure_class=_ERROR_CLASSES[code],
        owner_reason_codes=owner_reason_codes,
    ) from None


def _raise_named_owner(
    *,
    observed_code: object,
    allowed_codes: frozenset[str],
    response_codes: frozenset[str],
    response_public_code: H2cConsumeErrorCode,
    remaining_public_code: H2cConsumeErrorCode,
) -> NoReturn:
    if type(observed_code) is not str or observed_code not in allowed_codes:
        raise RuntimeError("undocumented MMI owner error code")
    _raise_controlled(
        response_public_code
        if observed_code in response_codes
        else remaining_public_code,
        owner_reason_codes=(observed_code,),
    )


def _validate_case_root_argument(
    *,
    expected_prepared_case_identity_sha256: object,
    case_root: object,
) -> Path:
    if (
        type(expected_prepared_case_identity_sha256) is not str
        or _SHA256_RE.fullmatch(expected_prepared_case_identity_sha256) is None
        or not isinstance(case_root, Path)
    ):
        _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_ARGUMENT_INVALID)
    normalized_string = os.path.normpath(os.fspath(case_root))
    if not os.path.isabs(normalized_string):
        _raise_controlled(
            H2cConsumeErrorCode.H2C_CONSUME_PATH_CONTRACT_INVALID
        )
    return Path(normalized_string)


def _raise_for_archived_source_error(
    exc: MmiH2cArchivedSourceV1Error,
) -> NoReturn:
    public_code = _E1_ERROR_TRANSLATION[exc.code]
    _raise_controlled(public_code, owner_reason_codes=(exc.code,))


def _raise_for_p2_internal_error(
    exc: RuntimeError,
    *,
    allowed_prefixes: tuple[str, ...],
) -> NoReturn:
    code = getattr(exc, "code", None)
    if type(code) is not str or not any(
        code.startswith(prefix) for prefix in allowed_prefixes
    ):
        raise RuntimeError("malformed MMI internal error code") from exc
    _raise_controlled(
        H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID,
        owner_reason_codes=(code,),
    )


def _policy_roles_from_projection(
    policy_projection: Mapping[str, object],
) -> dict[str, str]:
    universe = policy_projection.get("universe_projection")
    if type(universe) is not dict:
        raise RuntimeError("malformed MMI policy projection universe")
    raw_roles = universe.get("role_by_ticker")
    if type(raw_roles) is not dict:
        raise RuntimeError("malformed MMI policy projection role_by_ticker")
    roles: dict[str, str] = {}
    for ticker, role in raw_roles.items():
        if type(ticker) is not str or type(role) is not str:
            raise RuntimeError("malformed MMI policy projection role entry")
        roles[ticker] = role
    return roles


def _preflight_output_collision(case_fd: int) -> None:
    for relative_path in (
        _ARTIFACTS_BUNDLE_PATH,
        _ARTIFACTS_REPORT_PATH,
        _ARTIFACTS_RECEIPT_PATH,
    ):
        try:
            os.stat(relative_path, dir_fd=case_fd, follow_symlinks=False)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                continue
            _translate_directory_oserror(exc)
        _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_COLLISION)


def _validate_arguments(
    *,
    strategy_settings_expected_sha256: object,
    portfolio_snapshot_expected_sha256: object,
    expected_prepared_case_identity_sha256: object,
    case_root: object,
) -> Path:
    if (
        type(strategy_settings_expected_sha256) is not str
        or _SHA256_RE.fullmatch(strategy_settings_expected_sha256) is None
        or type(portfolio_snapshot_expected_sha256) is not str
        or _SHA256_RE.fullmatch(portfolio_snapshot_expected_sha256) is None
    ):
        _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_ARGUMENT_INVALID)
    return _validate_case_root_argument(
        expected_prepared_case_identity_sha256=(
            expected_prepared_case_identity_sha256
        ),
        case_root=case_root,
    )


def _require_filesystem_capabilities() -> None:
    flags = ("O_CLOEXEC", "O_DIRECTORY", "O_EXCL", "O_NOFOLLOW", "O_NONBLOCK")
    if any(not hasattr(os, name) for name in flags) or any(
        function not in os.supports_dir_fd
        for function in (os.mkdir, os.open, os.stat)
    ):
        _raise_controlled(
            H2cConsumeErrorCode.H2C_CONSUME_CAPABILITY_UNAVAILABLE
        )


def _translate_directory_oserror(
    exc: OSError,
    *,
    input_invalid: H2cConsumeErrorCode = H2cConsumeErrorCode.H2C_CONSUME_PATH_CONTRACT_INVALID,
) -> NoReturn:
    if exc.errno in _CONTROLLED_INPUT_ERRNOS or exc.errno == errno.EINVAL:
        _raise_controlled(input_invalid)
    if exc.errno in _CONTROLLED_CAPABILITY_ERRNOS:
        _raise_controlled(
            H2cConsumeErrorCode.H2C_CONSUME_CAPABILITY_UNAVAILABLE
        )
    raise exc


def _open_case_root(path: Path) -> int:
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        return os.open(os.fspath(path), flags)
    except OSError as exc:
        _translate_directory_oserror(exc)


def _stable_read_exact_bytes(
    case_fd: int,
    relative_path: str,
    *,
    maximum_bytes: int,
    input_invalid: H2cConsumeErrorCode = H2cConsumeErrorCode.H2C_CONSUME_PATH_CONTRACT_INVALID,
) -> bytes:
    try:
        return _neutral_stable_read_exact_bytes(
            case_fd,
            relative_path,
            maximum_bytes=maximum_bytes,
        )
    except MmiH2cStableReadError as exc:
        if exc.code == MmiH2cStableReadErrorCode.STABLE_READ_INPUT_INVALID:
            _raise_controlled(input_invalid)
        elif exc.code == MmiH2cStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE:
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_CAPABILITY_UNAVAILABLE)
        raise exc


def _read_manifest_dict(case_fd: int) -> dict[str, object]:
    exact_bytes = _stable_read_exact_bytes(
        case_fd,
        _MANIFEST_RELATIVE_PATH,
        maximum_bytes=_MANIFEST_MAXIMUM_BYTES,
        input_invalid=H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID,
    )
    try:
        parsed = json.loads(exact_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_ARTIFACT_CONTENT_INVALID)
    if type(parsed) is not dict:
        _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID)
    return parsed


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
            H2cConsumeErrorCode.H2C_CONSUME_SOURCE_CAPTURE_INVALID,
            owner_reason_codes=reasons,
        )
    if (
        result.status
        is MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    ):
        _raise_controlled(
            H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID,
            owner_reason_codes=reasons,
        )
    raise RuntimeError("malformed MMI source capture result")


def _require_projection_build(
    result: object,
    *,
    expected_type: type[
        MmiPolicyProjectionBuildResult | MmiPortfolioProjectionBuildResult
    ],
    failure_code: H2cConsumeErrorCode = (
        H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
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
        _raise_controlled(failure_code, owner_reason_codes=reasons)
    raise RuntimeError("malformed MMI projection build result")


def _require_projection_validation(
    result: object,
    *,
    expected_type: type[
        MmiPolicyProjectionValidationResult
        | MmiPortfolioProjectionValidationResult
    ],
    failure_code: H2cConsumeErrorCode = (
        H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
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
        _raise_controlled(failure_code, owner_reason_codes=reasons)
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
            H2cConsumeErrorCode.H2C_CONSUME_LEGACY_COMPILER_INVALID
        )


def _derive_approved_list(settings_text: str) -> str:
    try:
        return derive_legacy_approved_extended_etf_json(
            strategy_settings_text=settings_text
        )
    except (StrategySettingsValidationError, yaml.YAMLError):
        _raise_controlled(
            H2cConsumeErrorCode.H2C_CONSUME_LEGACY_COMPILER_INVALID
        )
    except ValueError as exc:
        if str(exc) in _APPROVED_LIST_VALUE_ERRORS:
            _raise_controlled(
                H2cConsumeErrorCode.H2C_CONSUME_LEGACY_COMPILER_INVALID
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
            H2cConsumeErrorCode.H2C_CONSUME_LEGACY_COMPILER_INVALID
        )
    if not 1 <= len(exact_bytes) <= _LEGACY_PROMPT_MAXIMUM_BYTES:
        _raise_controlled(
            H2cConsumeErrorCode.H2C_CONSUME_LEGACY_COMPILER_INVALID
        )
    return exact_bytes


def _translate_persistence_oserror(
    exc: OSError,
    *,
    code: H2cConsumeErrorCode = H2cConsumeErrorCode.H2C_CONSUME_PERSISTENCE_FAILED,
) -> NoReturn:
    if exc.errno == errno.EEXIST:
        _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_COLLISION)
    if exc.errno not in _CONTROLLED_PERSISTENCE_ERRNOS:
        raise exc
    _raise_controlled(code)


def _fsync_parent(case_fd: int, relative_path: str) -> None:
    parent_path = os.path.dirname(relative_path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    parent_fd = os.open(parent_path, flags, dir_fd=case_fd)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _write_new_exact_file(
    case_fd: int,
    relative_path: str,
    *,
    exact_bytes: bytes,
) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | os.O_CLOEXEC
    )
    fd: int | None = None
    try:
        fd = os.open(relative_path, flags, 0o600, dir_fd=case_fd)
        offset = 0
        while offset < len(exact_bytes):
            written = os.write(fd, exact_bytes[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = None
        _fsync_parent(case_fd, relative_path)
    except OSError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _translate_persistence_oserror(exc)


def _snapshot_mapping(
    value: Mapping[str, object],
    *,
    maximum_bytes: int,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError("malformed dict")
    try:
        encoded = canonical_json_bytes(
            dict(value),
            maximum_bytes=maximum_bytes,
        )
    except MmiCanonicalizationError:
        raise RuntimeError("malformed dict")
    parsed = json.loads(encoded)
    if type(parsed) is not dict:
        raise RuntimeError("malformed dict")
    return parsed


def consume_h2c_persisted_case(
    *,
    case_root: Path,
    expected_prepared_case_identity_sha256: str,
    strategy_settings_expected_sha256: str,
    portfolio_snapshot_expected_sha256: str,
) -> H2cConsumeResult:
    """Consume one prepared H2c case from disk, persisting artifacts on success."""
    case_root_path = _validate_arguments(
        strategy_settings_expected_sha256=strategy_settings_expected_sha256,
        portfolio_snapshot_expected_sha256=portfolio_snapshot_expected_sha256,
        expected_prepared_case_identity_sha256=(
            expected_prepared_case_identity_sha256
        ),
        case_root=case_root,
    )
    _require_filesystem_capabilities()

    case_fd = _open_case_root(case_root_path)
    try:
        _preflight_output_collision(case_fd)

        prepared_dict = _read_manifest_dict(case_fd)

        try:
            run_context = resume_mmi_h2c_prepared_case_run_context(
                prepared_case=prepared_dict,
                expected_prepared_case_identity_sha256=(
                    expected_prepared_case_identity_sha256
                ),
            )
        except ValueError as exc:
            if str(exc) == "MMI_H2C_PREPARED_CASE_V1_INVALID":
                _raise_controlled(
                    H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID
                )
            raise

        if prepared_dict.get("workflow_status") != _EXPECTED_PREPARED_WORKFLOW_STATUS:
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_WORKFLOW_STATE_INVALID)

        settings_source_dict = prepared_dict.get("strategy_settings_source")
        portfolio_source_dict = prepared_dict.get("portfolio_snapshot_source")
        if type(settings_source_dict) is not dict or type(portfolio_source_dict) is not dict:
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID)

        settings_record = settings_source_dict.get("source_record")
        portfolio_record = portfolio_source_dict.get("source_record")
        if type(settings_record) is not dict or type(portfolio_record) is not dict:
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID)

        settings_source = _require_source_capture(
            capture_current_mmi_source(
                MmiSourceRole.STRATEGY_SETTINGS,
                expected_source_sha256=strategy_settings_expected_sha256,
            ),
            role=MmiSourceRole.STRATEGY_SETTINGS,
        )
        if settings_source.source_record != settings_record:
            _raise_controlled(
                H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
            )

        portfolio_source = _require_source_capture(
            capture_current_mmi_source(
                MmiSourceRole.PORTFOLIO_SNAPSHOT,
                expected_source_sha256=portfolio_snapshot_expected_sha256,
            ),
            role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        )
        if portfolio_source.source_record != portfolio_record:
            _raise_controlled(
                H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
            )

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
                H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
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
                H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
            ),
            allowed_reason_prefixes=_PORTFOLIO_REASON_PREFIXES,
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
            g2 = build_mmi_grounded_prompt_v2(
                analyst_visible_evidence_view=view,
                evidence_bundle=evidence,
                policy_projection=policy,
                policy_source=settings_source,
                portfolio_projection=portfolio,
                portfolio_source=portfolio_source,
                run_context=run_context,
            )
            g2 = validate_mmi_grounded_prompt_v2(
                value=g2,
                evidence_bundle=evidence,
                policy_projection=policy,
                policy_source=settings_source,
                portfolio_projection=portfolio,
                portfolio_source=portfolio_source,
                run_context=run_context,
            )
        except MmiGroundedPromptV2Error as exc:
            _raise_named_owner(
                observed_code=exc.args[0],
                allowed_codes=_G2_CODES,
                response_codes=_G2_PROMPT_CODES,
                response_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID
                ),
                remaining_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
                ),
            )

        g2_canonical = _snapshot_mapping(g2, maximum_bytes=_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES)
        manifest_g2 = prepared_dict.get("grounded_prompt")
        if type(manifest_g2) is not dict:
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID)

        if g2_canonical != manifest_g2:
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID)

        archive_settings_bytes = _stable_read_exact_bytes(
            case_fd,
            _ARCHIVE_SETTINGS_PATH,
            maximum_bytes=MMI_SOURCE_CATALOG[
                MmiSourceRole.STRATEGY_SETTINGS
            ].maximum_bytes,
        )
        if archive_settings_bytes != settings_source.raw_bytes:
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID)

        archive_portfolio_bytes = _stable_read_exact_bytes(
            case_fd,
            _ARCHIVE_PORTFOLIO_PATH,
            maximum_bytes=MMI_SOURCE_CATALOG[
                MmiSourceRole.PORTFOLIO_SNAPSHOT
            ].maximum_bytes,
        )
        if archive_portfolio_bytes != portfolio_source.raw_bytes:
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID)

        archive_template_bytes = _stable_read_exact_bytes(
            case_fd,
            _ARCHIVE_TEMPLATE_PATH,
            maximum_bytes=_LEGACY_TEMPLATE_MAXIMUM_BYTES,
        )
        manifest_template_dict = prepared_dict.get("legacy_prompt_template")
        if type(manifest_template_dict) is not dict:
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID)
        if hashlib.sha256(archive_template_bytes).hexdigest() != manifest_template_dict.get("sha256"):
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID)

        legacy_prompt_bytes = _compile_legacy_prompt(
            template_text=_legacy_text(archive_template_bytes),
            settings_text=_legacy_text(archive_settings_bytes),
            portfolio_text=_legacy_text(archive_portfolio_bytes),
            approved_list_json=_derive_approved_list(
                _legacy_text(archive_settings_bytes)
            ),
        )
        manifest_legacy_dict = prepared_dict.get("legacy_prompt")
        if type(manifest_legacy_dict) is not dict:
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID)
        if hashlib.sha256(legacy_prompt_bytes).hexdigest() != manifest_legacy_dict.get("sha256"):
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID)

        h1_prompt_bytes = _stable_read_exact_bytes(
            case_fd,
            _PROMPTS_H1_PATH,
            maximum_bytes=_H1_PROMPT_MAXIMUM_BYTES,
        )
        manifest_h1_dict = prepared_dict.get("h1_prompt")
        if type(manifest_h1_dict) is not dict:
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID)
        if hashlib.sha256(h1_prompt_bytes).hexdigest() != manifest_h1_dict.get("sha256"):
            _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID)

        h1_response_bytes = _stable_read_exact_bytes(
            case_fd,
            _RESPONSES_H1_PATH,
            maximum_bytes=MAXIMUM_MMI_RAW_RESPONSE_BYTES,
            input_invalid=H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_INPUT_INVALID,
        )
        legacy_response_bytes = _stable_read_exact_bytes(
            case_fd,
            _RESPONSES_LEGACY_PATH,
            maximum_bytes=MAX_LEGACY_RESEARCH_RAW_BYTES,
            input_invalid=H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_INPUT_INVALID,
        )

        try:
            r1 = build_mmi_raw_response_envelope_v2(
                raw_response_bytes=h1_response_bytes,
                grounded_prompt=g2,
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
                observed_code=exc.args[0],
                allowed_codes=_R1_CODES,
                response_codes=_R1_RESPONSE_CODES,
                response_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_CONTENT_INVALID
                ),
                remaining_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
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
                observed_code=exc.args[0],
                allowed_codes=_R2_CODES,
                response_codes=_R2_RESPONSE_CODES,
                response_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_CONTENT_INVALID
                ),
                remaining_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
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
        except MmiLegacyStep1CompatibilityCandidateV1Error as exc:
            _raise_named_owner(
                observed_code=exc.args[0],
                allowed_codes=_H1_CODES,
                response_codes=frozenset(),
                response_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
                ),
                remaining_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
                ),
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
                legacy_strategy_settings=parse_strategy_settings_text(
                    _legacy_text(archive_settings_bytes)
                ),
            )
        except MmiLegacyStep1ComparisonReportV1Error as exc:
            _raise_named_owner(
                observed_code=exc.args[0],
                allowed_codes=_H2_CODES,
                response_codes=_H2_RESPONSE_CODES,
                response_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_CONTENT_INVALID
                ),
                remaining_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_VALIDATION_INVALID
                ),
            )

        try:
            bundle = build_mmi_h2c_case_evidence_bundle_v1(
                grounded_prompt=g2,
                raw_response_envelope=r1,
                validated_grounded_analysis_response=r2,
                legacy_step1_compatibility_candidate=h1,
                strategy_settings_source_record=dict(settings_source.source_record),
                portfolio_snapshot_source_record=dict(portfolio_source.source_record),
            )
        except ValueError as exc:
            print("VALUEERROR:", exc)
            if str(exc) == "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID":
                _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_VALIDATION_INVALID)
            raise

        bundle_sha256 = bundle.get("case_evidence_bundle_identity_sha256")
        report_sha256 = h2.get("comparison_report_identity_sha256")
        if type(bundle_sha256) is not str or type(report_sha256) is not str:
            raise RuntimeError("malformed artifact identities")

        try:
            receipt = build_mmi_h2c_dual_side_persisted_case_receipt_v2(
                evaluation_timestamp_utc=run_context.evaluation_timestamp_utc,
                prepared_case_identity_sha256=expected_prepared_case_identity_sha256,
                case_evidence_bundle_identity_sha256=bundle_sha256,
                comparison_report_identity_sha256=report_sha256,
                strategy_settings_source_record_identity_sha256=strategy_settings_expected_sha256,
                portfolio_snapshot_source_record_identity_sha256=portfolio_snapshot_expected_sha256,
                h1_prompt_sha256=hashlib.sha256(h1_prompt_bytes).hexdigest(),
                legacy_prompt_sha256=hashlib.sha256(legacy_prompt_bytes).hexdigest(),
                h1_operator_supplied_response_sha256=hashlib.sha256(h1_response_bytes).hexdigest(),
                legacy_operator_supplied_response_sha256=hashlib.sha256(legacy_response_bytes).hexdigest(),
            )
        except ValueError as exc:
            if str(exc) == "MMI_H2C_PERSISTED_CASE_RECEIPT_V2_INVALID":
                _raise_controlled(H2cConsumeErrorCode.H2C_CONSUME_VALIDATION_INVALID)
            raise

        receipt_sha256 = receipt.get("receipt_identity_sha256")
        if type(receipt_sha256) is not str:
            raise RuntimeError("malformed receipt identity")

        _write_new_exact_file(
            case_fd,
            _ARTIFACTS_BUNDLE_PATH,
            exact_bytes=canonical_json_bytes(bundle, maximum_bytes=MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES),
        )
        _write_new_exact_file(
            case_fd,
            _ARTIFACTS_REPORT_PATH,
            exact_bytes=canonical_json_bytes(h2, maximum_bytes=MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES),
        )
        _write_new_exact_file(
            case_fd,
            _ARTIFACTS_RECEIPT_PATH,
            exact_bytes=canonical_json_bytes(receipt, maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES),
        )

        return H2cConsumeResult(
            workflow_status=_WORKFLOW_STATUS,
            case_evidence_bundle_identity_sha256=bundle_sha256,
            comparison_report_identity_sha256=report_sha256,
            receipt_identity_sha256=receipt_sha256,
        )
    finally:
        os.close(case_fd)


def consume_h2c_persisted_case_from_archives(
    *,
    case_root: Path,
    expected_prepared_case_identity_sha256: str,
) -> H2cConsumeResult:
    """Consume one prepared H2c case from its authenticated archives.

    Unlike ``consume_h2c_persisted_case``, this entry recovers both source
    bindings from the validated prepared case itself rather than from a live
    recapture, so it takes no source-hash arguments.
    """
    case_root_path = _validate_case_root_argument(
        expected_prepared_case_identity_sha256=(
            expected_prepared_case_identity_sha256
        ),
        case_root=case_root,
    )
    _require_filesystem_capabilities()

    case_fd = _open_case_root(case_root_path)
    try:
        _preflight_output_collision(case_fd)

        try:
            snapshot = _build_mmi_h2c_archived_prepared_case_snapshot(
                case_fd=case_fd,
                expected_prepared_case_identity_sha256=(
                    expected_prepared_case_identity_sha256
                ),
            )
        except MmiH2cArchivedSourceV1Error as exc:
            if exc.code not in _E1_ERROR_TRANSLATION:
                raise
            _raise_for_archived_source_error(exc)

        strategy_record = snapshot.strategy_source_record
        portfolio_record = snapshot.portfolio_source_record
        strategy_sid = strategy_record["source_record_identity_sha256"]
        portfolio_sid = portfolio_record["source_record_identity_sha256"]
        run_context = snapshot.run_context

        try:
            policy = _build_mmi_policy_projection_from_source_bytes(
                snapshot.strategy_archived_bytes,
                source_record_identity_sha256=strategy_sid,
                run_context=run_context,
            )
        except (_ProjectionBlocked, _ProjectionContractFailure) as exc:
            _raise_for_p2_internal_error(
                exc, allowed_prefixes=_POLICY_REASON_PREFIXES
            )
        try:
            policy, policy_component = _evidence_bundle._validate_policy_component_from_source_bytes(
                policy,
                raw_bytes=snapshot.strategy_archived_bytes,
                source_record_identity_sha256=strategy_sid,
                run_context=run_context,
            )
        except (
            _evidence_bundle._BundleBlocked,
            _evidence_bundle._BundleContractFailure,
        ) as exc:
            _raise_for_p2_internal_error(
                exc, allowed_prefixes=_EVIDENCE_REASON_PREFIXES
            )

        policy_roles = _policy_roles_from_projection(policy)
        try:
            portfolio, _policy_roles_echo = _build_mmi_portfolio_snapshot_projection_from_source_bytes(
                snapshot.portfolio_archived_bytes,
                source_record_identity_sha256=portfolio_sid,
                policy_projection_identity_sha256=(
                    policy_component.policy_projection_identity_sha256
                ),
                policy_roles=policy_roles,
                run_context=run_context,
            )
        except (_PortfolioBlocked, _PortfolioContractFailure) as exc:
            _raise_for_p2_internal_error(
                exc, allowed_prefixes=_PORTFOLIO_REASON_PREFIXES
            )
        try:
            portfolio_component = _evidence_bundle._validate_portfolio_component_from_source_bytes(
                portfolio,
                raw_bytes=snapshot.portfolio_archived_bytes,
                source_record_identity_sha256=portfolio_sid,
                policy_projection=policy,
                policy_component=policy_component,
                run_context=run_context,
            )
        except (
            _evidence_bundle._BundleBlocked,
            _evidence_bundle._BundleContractFailure,
        ) as exc:
            _raise_for_p2_internal_error(
                exc, allowed_prefixes=_EVIDENCE_REASON_PREFIXES
            )

        evidence = _evidence_bundle._build_mmi_authenticated_evidence_bundle_from_components(
            policy_component=policy_component,
            portfolio_component=portfolio_component,
            run_context=run_context,
        )

        view = _require_projection_build(
            _build_mmi_analyst_visible_evidence_view_v2_from_source_record_identities(
                evidence_bundle=evidence,
                policy_projection=policy,
                policy_source_record_identity_sha256=strategy_sid,
                portfolio_projection=portfolio,
                portfolio_source_record_identity_sha256=portfolio_sid,
                run_context=run_context,
            ),
            expected_type=MmiPolicyProjectionBuildResult,
            allowed_reason_prefixes=_VIEW_REASON_PREFIXES,
        )

        try:
            g2 = _build_mmi_grounded_prompt_v2_from_source_record_identities(
                analyst_visible_evidence_view=view,
                evidence_bundle=evidence,
                policy_projection=policy,
                policy_source_record_identity_sha256=strategy_sid,
                portfolio_projection=portfolio,
                portfolio_source_record_identity_sha256=portfolio_sid,
                run_context=run_context,
            )
            g2 = _validate_mmi_grounded_prompt_v2_from_source_record_identities(
                value=g2,
                evidence_bundle=evidence,
                policy_projection=policy,
                policy_source_record_identity_sha256=strategy_sid,
                portfolio_projection=portfolio,
                portfolio_source_record_identity_sha256=portfolio_sid,
                run_context=run_context,
            )
        except MmiGroundedPromptV2Error as exc:
            _raise_named_owner(
                observed_code=exc.args[0],
                allowed_codes=_G2_CODES,
                response_codes=_G2_PROMPT_CODES,
                response_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID
                ),
                remaining_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
                ),
            )

        g2_canonical_bytes = canonical_json_bytes(
            _snapshot_mapping(
                g2, maximum_bytes=_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES
            ),
            maximum_bytes=_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES,
        )
        if (
            g2_canonical_bytes
            != snapshot.projection.grounded_prompt_canonical_bytes
        ):
            _raise_controlled(
                H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID
            )

        archive_template_bytes = _stable_read_exact_bytes(
            case_fd,
            _ARCHIVE_TEMPLATE_PATH,
            maximum_bytes=_LEGACY_TEMPLATE_MAXIMUM_BYTES,
        )
        if (
            hashlib.sha256(archive_template_bytes).hexdigest()
            != snapshot.projection.legacy_prompt_template_sha256
        ):
            _raise_controlled(
                H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID
            )

        legacy_prompt_bytes = _compile_legacy_prompt(
            template_text=_legacy_text(archive_template_bytes),
            settings_text=_legacy_text(snapshot.strategy_archived_bytes),
            portfolio_text=_legacy_text(snapshot.portfolio_archived_bytes),
            approved_list_json=_derive_approved_list(
                _legacy_text(snapshot.strategy_archived_bytes)
            ),
        )
        if (
            hashlib.sha256(legacy_prompt_bytes).hexdigest()
            != snapshot.projection.legacy_prompt_sha256
        ):
            _raise_controlled(
                H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID
            )

        h1_prompt_bytes = _stable_read_exact_bytes(
            case_fd,
            _PROMPTS_H1_PATH,
            maximum_bytes=_H1_PROMPT_MAXIMUM_BYTES,
        )
        if (
            hashlib.sha256(h1_prompt_bytes).hexdigest()
            != snapshot.projection.h1_prompt_sha256
        ):
            _raise_controlled(
                H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID
            )

        h1_response_bytes = _stable_read_exact_bytes(
            case_fd,
            _RESPONSES_H1_PATH,
            maximum_bytes=MAXIMUM_MMI_RAW_RESPONSE_BYTES,
            input_invalid=H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_INPUT_INVALID,
        )
        legacy_response_bytes = _stable_read_exact_bytes(
            case_fd,
            _RESPONSES_LEGACY_PATH,
            maximum_bytes=MAX_LEGACY_RESEARCH_RAW_BYTES,
            input_invalid=H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_INPUT_INVALID,
        )

        try:
            r1 = _build_mmi_raw_response_envelope_v2_from_source_record_identities(
                grounded_prompt=g2,
                raw_response_bytes=h1_response_bytes,
                evidence_bundle=evidence,
                policy_projection=policy,
                policy_source_record_identity_sha256=strategy_sid,
                portfolio_projection=portfolio,
                portfolio_source_record_identity_sha256=portfolio_sid,
                run_context=run_context,
            )
            r1 = _validate_mmi_raw_response_envelope_v2_from_source_record_identities(
                value=r1,
                evidence_bundle=evidence,
                policy_projection=policy,
                policy_source_record_identity_sha256=strategy_sid,
                portfolio_projection=portfolio,
                portfolio_source_record_identity_sha256=portfolio_sid,
                run_context=run_context,
            )
        except MmiRawResponseEnvelopeV2Error as exc:
            _raise_named_owner(
                observed_code=exc.args[0],
                allowed_codes=_R1_CODES,
                response_codes=_R1_RESPONSE_CODES,
                response_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_CONTENT_INVALID
                ),
                remaining_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
                ),
            )

        try:
            r2 = _build_mmi_validated_grounded_analysis_response_v2_from_source_record_identities(
                raw_response_envelope=r1,
                evidence_bundle=evidence,
                policy_projection=policy,
                policy_source_record_identity_sha256=strategy_sid,
                portfolio_projection=portfolio,
                portfolio_source_record_identity_sha256=portfolio_sid,
                run_context=run_context,
            )
            r2 = _validate_mmi_validated_grounded_analysis_response_v2_from_source_record_identities(
                value=r2,
                raw_response_envelope=r1,
                evidence_bundle=evidence,
                policy_projection=policy,
                policy_source_record_identity_sha256=strategy_sid,
                portfolio_projection=portfolio,
                portfolio_source_record_identity_sha256=portfolio_sid,
                run_context=run_context,
            )
        except MmiValidatedGroundedAnalysisResponseV2Error as exc:
            _raise_named_owner(
                observed_code=exc.args[0],
                allowed_codes=_R2_CODES,
                response_codes=_R2_RESPONSE_CODES,
                response_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_CONTENT_INVALID
                ),
                remaining_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
                ),
            )

        try:
            h1 = _build_mmi_legacy_step1_compatibility_candidate_v1_from_source_record_identities(
                validated_grounded_analysis_response=r2,
                raw_response_envelope=r1,
                evidence_bundle=evidence,
                policy_projection=policy,
                policy_source_record_identity_sha256=strategy_sid,
                portfolio_projection=portfolio,
                portfolio_source_record_identity_sha256=portfolio_sid,
                run_context=run_context,
            )
        except MmiLegacyStep1CompatibilityCandidateV1Error as exc:
            _raise_named_owner(
                observed_code=exc.args[0],
                allowed_codes=_H1_CODES,
                response_codes=frozenset(),
                response_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
                ),
                remaining_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
                ),
            )

        try:
            h2 = _build_mmi_legacy_step1_comparison_report_v1_from_validated_h1_candidate(
                validated_h1_candidate=h1,
                legacy_research_raw_bytes=legacy_response_bytes,
                legacy_strategy_settings=parse_strategy_settings_text(
                    _legacy_text(snapshot.strategy_archived_bytes)
                ),
            )
        except MmiLegacyStep1ComparisonReportV1Error as exc:
            _raise_named_owner(
                observed_code=exc.args[0],
                allowed_codes=_H2_CODES,
                response_codes=_H2_RESPONSE_CODES,
                response_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_CONTENT_INVALID
                ),
                remaining_public_code=(
                    H2cConsumeErrorCode.H2C_CONSUME_VALIDATION_INVALID
                ),
            )

        try:
            bundle = build_mmi_h2c_case_evidence_bundle_v1(
                grounded_prompt=g2,
                raw_response_envelope=r1,
                validated_grounded_analysis_response=r2,
                legacy_step1_compatibility_candidate=h1,
                strategy_settings_source_record=dict(strategy_record),
                portfolio_snapshot_source_record=dict(portfolio_record),
            )
        except ValueError as exc:
            if str(exc) == "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID":
                _raise_controlled(
                    H2cConsumeErrorCode.H2C_CONSUME_VALIDATION_INVALID
                )
            raise

        bundle_sha256 = bundle.get("case_evidence_bundle_identity_sha256")
        report_sha256 = h2.get("comparison_report_identity_sha256")
        if type(bundle_sha256) is not str or type(report_sha256) is not str:
            raise RuntimeError("malformed artifact identities")

        try:
            receipt = build_mmi_h2c_dual_side_persisted_case_receipt_v2(
                evaluation_timestamp_utc=run_context.evaluation_timestamp_utc,
                prepared_case_identity_sha256=(
                    expected_prepared_case_identity_sha256
                ),
                case_evidence_bundle_identity_sha256=bundle_sha256,
                comparison_report_identity_sha256=report_sha256,
                strategy_settings_source_record_identity_sha256=(
                    strategy_record["observed_sha256"]
                ),
                portfolio_snapshot_source_record_identity_sha256=(
                    portfolio_record["observed_sha256"]
                ),
                h1_prompt_sha256=hashlib.sha256(h1_prompt_bytes).hexdigest(),
                legacy_prompt_sha256=hashlib.sha256(
                    legacy_prompt_bytes
                ).hexdigest(),
                h1_operator_supplied_response_sha256=hashlib.sha256(
                    h1_response_bytes
                ).hexdigest(),
                legacy_operator_supplied_response_sha256=hashlib.sha256(
                    legacy_response_bytes
                ).hexdigest(),
            )
        except ValueError as exc:
            if str(exc) == "MMI_H2C_PERSISTED_CASE_RECEIPT_V2_INVALID":
                _raise_controlled(
                    H2cConsumeErrorCode.H2C_CONSUME_VALIDATION_INVALID
                )
            raise

        receipt_sha256 = receipt.get("receipt_identity_sha256")
        if type(receipt_sha256) is not str:
            raise RuntimeError("malformed receipt identity")

        _write_new_exact_file(
            case_fd,
            _ARTIFACTS_BUNDLE_PATH,
            exact_bytes=canonical_json_bytes(
                bundle,
                maximum_bytes=(
                    MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES
                ),
            ),
        )
        _write_new_exact_file(
            case_fd,
            _ARTIFACTS_REPORT_PATH,
            exact_bytes=canonical_json_bytes(
                h2,
                maximum_bytes=(
                    MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES
                ),
            ),
        )
        _write_new_exact_file(
            case_fd,
            _ARTIFACTS_RECEIPT_PATH,
            exact_bytes=canonical_json_bytes(
                receipt, maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES
            ),
        )

        return H2cConsumeResult(
            workflow_status=_WORKFLOW_STATUS,
            case_evidence_bundle_identity_sha256=bundle_sha256,
            comparison_report_identity_sha256=report_sha256,
            receipt_identity_sha256=receipt_sha256,
        )
    finally:
        os.close(case_fd)
