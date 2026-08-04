"""Dormant Phase A engine that prepares one persisted H2c case and exits.

Phase A captures the two exact current MMI sources, drives the existing live
chain to the complete G2 grounded prompt, compiles the exact Legacy Step 1
prompt, freezes all of it into one frozen ``mmi_h2c_prepared_case_v1`` manifest,
and only then writes a fixed case-relative tree.  The manifest is written last
and is the sole completion marker: the operator later supplies both responses
out of band, so no process needs to survive the handoff.

``PREPARATION_ONLY`` is the whole of this owner's scope.  It never reads a
response leaf, never builds R1/R2/H1/D1/H2/receipt artifacts, never mints a
capability, and never resumes, repairs, or validates an existing case.  It has
no provider, network, browser, polling, retry, thread, subprocess, scheduler,
availability, permission, freshness, gate, publication, pointer, order, broker
or execution behavior, and no production consumer.

Once the case root exists the case identifier is permanently consumed.  Partial
report-only evidence is retained rather than rolled back, and a rerun against
any existing case root fails before that case is read or modified.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import errno
import io
import os
from pathlib import Path
import re
import stat
from typing import Final, NoReturn

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
    MmiCanonicalizationError,
    canonical_json_bytes,
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
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
    validate_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
    validate_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.mmi.source_capture import (
    capture_current_mmi_source,
)
import investment_orchestrator.offline.mmi_h2c_prepared_case_v1 as _prepared_case
from investment_orchestrator.validators.strategy_settings import (
    StrategySettingsValidationError,
)


__all__ = (
    "H2cPrepareError",
    "H2cPrepareErrorCode",
    "H2cPrepareFailureClass",
    "H2cPrepareResult",
    "prepare_h2c_persisted_case",
)


class H2cPrepareFailureClass(str, Enum):
    """The exact existing H2c failure classes reachable from Phase A."""

    ARTIFACT_CONTENT = "ARTIFACT_CONTENT"
    AVAILABILITY_PERMISSION = "AVAILABILITY_PERMISSION"
    COMPILER_NORMALIZER = "COMPILER_NORMALIZER"
    OPERATOR_INPUT = "OPERATOR_INPUT"
    PERSISTENCE = "PERSISTENCE"
    PROMPT_CONTRACT = "PROMPT_CONTRACT"
    VALIDATOR_SCHEMA = "VALIDATOR_SCHEMA"


class H2cPrepareErrorCode(str, Enum):
    """Minimal stable operator-facing error codes for Phase A preparation."""

    H2C_PREPARE_ARGUMENT_INVALID = "H2C_PREPARE_ARGUMENT_INVALID"
    H2C_PREPARE_PATH_CONTRACT_INVALID = "H2C_PREPARE_PATH_CONTRACT_INVALID"
    H2C_PREPARE_CAPABILITY_UNAVAILABLE = "H2C_PREPARE_CAPABILITY_UNAVAILABLE"
    H2C_PREPARE_SOURCE_CAPTURE_INVALID = "H2C_PREPARE_SOURCE_CAPTURE_INVALID"
    H2C_PREPARE_PORTFOLIO_NOT_COMPARABLE = (
        "H2C_PREPARE_PORTFOLIO_NOT_COMPARABLE"
    )
    H2C_PREPARE_LIVE_CHAIN_INVALID = "H2C_PREPARE_LIVE_CHAIN_INVALID"
    H2C_PREPARE_PROMPT_CONTRACT_INVALID = "H2C_PREPARE_PROMPT_CONTRACT_INVALID"
    H2C_PREPARE_LEGACY_COMPILER_INVALID = "H2C_PREPARE_LEGACY_COMPILER_INVALID"
    H2C_PREPARE_MANIFEST_INVALID = "H2C_PREPARE_MANIFEST_INVALID"
    H2C_PREPARE_PERSISTENCE_FAILED = "H2C_PREPARE_PERSISTENCE_FAILED"


# ``AVAILABILITY_PERMISSION`` follows the existing foreground H2c precedent and
# covers only absent operating-system, filesystem or clock capability.  It is
# never research availability and never an investment permission.
_ERROR_CLASSES: Final = {
    H2cPrepareErrorCode.H2C_PREPARE_ARGUMENT_INVALID: (
        H2cPrepareFailureClass.OPERATOR_INPUT
    ),
    H2cPrepareErrorCode.H2C_PREPARE_PATH_CONTRACT_INVALID: (
        H2cPrepareFailureClass.OPERATOR_INPUT
    ),
    H2cPrepareErrorCode.H2C_PREPARE_CAPABILITY_UNAVAILABLE: (
        H2cPrepareFailureClass.AVAILABILITY_PERMISSION
    ),
    H2cPrepareErrorCode.H2C_PREPARE_SOURCE_CAPTURE_INVALID: (
        H2cPrepareFailureClass.ARTIFACT_CONTENT
    ),
    H2cPrepareErrorCode.H2C_PREPARE_PORTFOLIO_NOT_COMPARABLE: (
        H2cPrepareFailureClass.ARTIFACT_CONTENT
    ),
    H2cPrepareErrorCode.H2C_PREPARE_LIVE_CHAIN_INVALID: (
        H2cPrepareFailureClass.VALIDATOR_SCHEMA
    ),
    H2cPrepareErrorCode.H2C_PREPARE_PROMPT_CONTRACT_INVALID: (
        H2cPrepareFailureClass.PROMPT_CONTRACT
    ),
    H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID: (
        H2cPrepareFailureClass.COMPILER_NORMALIZER
    ),
    H2cPrepareErrorCode.H2C_PREPARE_MANIFEST_INVALID: (
        H2cPrepareFailureClass.VALIDATOR_SCHEMA
    ),
    H2cPrepareErrorCode.H2C_PREPARE_PERSISTENCE_FAILED: (
        H2cPrepareFailureClass.PERSISTENCE
    ),
}


@dataclass(frozen=True, slots=True, init=False)
class H2cPrepareError(RuntimeError):
    """One controlled Phase A failure with a stable public surface."""

    code: H2cPrepareErrorCode
    failure_class: H2cPrepareFailureClass
    owner_reason_codes: tuple[str, ...]

    def __init__(
        self,
        *,
        code: H2cPrepareErrorCode,
        failure_class: H2cPrepareFailureClass,
        owner_reason_codes: tuple[str, ...] = (),
    ) -> None:
        if type(code) is not H2cPrepareErrorCode:
            raise TypeError("code must be an H2cPrepareErrorCode")
        if type(failure_class) is not H2cPrepareFailureClass:
            raise TypeError(
                "failure_class must be an H2cPrepareFailureClass"
            )
        if _ERROR_CLASSES.get(code) is not failure_class:
            raise ValueError("H2c prepare error code/failure-class mismatch")
        if type(owner_reason_codes) is not tuple or any(
            type(reason) is not str for reason in owner_reason_codes
        ):
            raise TypeError("owner_reason_codes must be an exact string tuple")
        RuntimeError.__init__(self, code.value)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "failure_class", failure_class)
        object.__setattr__(self, "owner_reason_codes", owner_reason_codes)


@dataclass(frozen=True, slots=True)
class H2cPrepareResult:
    """The only two values one completed Phase A preparation exposes."""

    workflow_status: str
    prepared_case_identity_sha256: str


_WORKFLOW_STATUS: Final = "AWAITING_OPERATOR_RESPONSES"
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_READ_CHUNK_BYTES: Final = 65_536
_LEGACY_TEMPLATE_MAXIMUM_BYTES: Final = 262_144
_LEGACY_PROMPT_MAXIMUM_BYTES: Final = 3_170_307
_FIXED_LEGACY_TEMPLATE_NAME: Final = "research_dual_lane.txt"
_LEGACY_TEMPLATE_LOCATOR: Final = "prompts/research_dual_lane.txt"
_DIRECTORY_MODE: Final = 0o700
_FILE_MODE: Final = 0o600

_ARCHIVE_DIRECTORY: Final = "archive"
_PROMPTS_DIRECTORY: Final = "prompts"
_PREPARED_DIRECTORY: Final = "prepared"
_RESPONSES_DIRECTORY: Final = "responses"
_ARTIFACTS_DIRECTORY: Final = "artifacts"
_CASE_DIRECTORIES: Final = (
    _ARCHIVE_DIRECTORY,
    _PROMPTS_DIRECTORY,
    _PREPARED_DIRECTORY,
    _RESPONSES_DIRECTORY,
    _ARTIFACTS_DIRECTORY,
)

_SETTINGS_ARCHIVE_LEAF: Final = (_ARCHIVE_DIRECTORY, "strategy_settings.yaml")
_PORTFOLIO_ARCHIVE_LEAF: Final = (
    _ARCHIVE_DIRECTORY,
    "portfolio_snapshot.txt",
)
_TEMPLATE_ARCHIVE_LEAF: Final = (_ARCHIVE_DIRECTORY, "research_dual_lane.txt")
_H1_PROMPT_LEAF: Final = (_PROMPTS_DIRECTORY, "h1_prompt.txt")
_LEGACY_PROMPT_LEAF: Final = (_PROMPTS_DIRECTORY, "legacy_prompt.txt")
_MANIFEST_LEAF: Final = (_PREPARED_DIRECTORY, "prepared_case.json")
_H1_RESPONSE_LEAF: Final = (_RESPONSES_DIRECTORY, "h1_response.raw")
_LEGACY_RESPONSE_LEAF: Final = (_RESPONSES_DIRECTORY, "legacy_response.raw")
_CASE_EVIDENCE_BUNDLE_LEAF: Final = (
    _ARTIFACTS_DIRECTORY,
    "case_evidence_bundle.json",
)
_COMPARISON_REPORT_LEAF: Final = (
    _ARTIFACTS_DIRECTORY,
    "comparison_report.json",
)
_RECEIPT_LEAF: Final = (_ARTIFACTS_DIRECTORY, "receipt.json")
_ABSENT_LEAVES: Final = (
    _H1_RESPONSE_LEAF,
    _LEGACY_RESPONSE_LEAF,
    _CASE_EVIDENCE_BUNDLE_LEAF,
    _COMPARISON_REPORT_LEAF,
    _RECEIPT_LEAF,
)


def _case_relative(leaf: tuple[str, str]) -> str:
    return f"{leaf[0]}/{leaf[1]}"


# Every case-relative path the frozen contract declares, in manifest order.
# ``prepared/prepared_case.json`` is deliberately absent: the frozen envelope
# declares no location for itself, so the manifest leaf is this owner's choice.
_DECLARED_CASE_RELATIVE_PATHS: Final = tuple(
    _case_relative(leaf)
    for leaf in (
        _SETTINGS_ARCHIVE_LEAF,
        _PORTFOLIO_ARCHIVE_LEAF,
        _TEMPLATE_ARCHIVE_LEAF,
        _H1_PROMPT_LEAF,
        _LEGACY_PROMPT_LEAF,
        _H1_RESPONSE_LEAF,
        _LEGACY_RESPONSE_LEAF,
        _CASE_EVIDENCE_BUNDLE_LEAF,
        _COMPARISON_REPORT_LEAF,
        _RECEIPT_LEAF,
    )
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
    code: H2cPrepareErrorCode,
    *,
    owner_reason_codes: tuple[str, ...] = (),
) -> NoReturn:
    raise H2cPrepareError(
        code=code,
        failure_class=_ERROR_CLASSES[code],
        owner_reason_codes=owner_reason_codes,
    ) from None


def _raise_named_owner(
    *,
    observed_code: object,
    allowed_codes: frozenset[str],
    prompt_codes: frozenset[str],
    prompt_public_code: H2cPrepareErrorCode,
    remaining_public_code: H2cPrepareErrorCode,
) -> NoReturn:
    if type(observed_code) is not str or observed_code not in allowed_codes:
        raise RuntimeError("undocumented MMI owner error code")
    _raise_controlled(
        prompt_public_code
        if observed_code in prompt_codes
        else remaining_public_code,
        owner_reason_codes=(observed_code,),
    )


def _validate_arguments(
    *,
    strategy_settings_expected_sha256: object,
    portfolio_snapshot_expected_sha256: object,
    case_root: object,
) -> tuple[str, str]:
    if (
        type(strategy_settings_expected_sha256) is not str
        or _SHA256_RE.fullmatch(strategy_settings_expected_sha256) is None
        or type(portfolio_snapshot_expected_sha256) is not str
        or _SHA256_RE.fullmatch(portfolio_snapshot_expected_sha256) is None
        or not isinstance(case_root, Path)
    ):
        _raise_controlled(H2cPrepareErrorCode.H2C_PREPARE_ARGUMENT_INVALID)
    normalized = os.path.normpath(os.fspath(case_root))
    parent, name = os.path.split(normalized)
    if (
        not os.path.isabs(normalized)
        or not parent
        or not name
        or name in {os.curdir, os.pardir}
        or os.path.normpath(parent) != parent
    ):
        _raise_controlled(
            H2cPrepareErrorCode.H2C_PREPARE_PATH_CONTRACT_INVALID
        )
    return parent, name


def _require_filesystem_capabilities() -> None:
    flags = ("O_CLOEXEC", "O_DIRECTORY", "O_EXCL", "O_NOFOLLOW", "O_NONBLOCK")
    if any(not hasattr(os, name) for name in flags) or any(
        function not in os.supports_dir_fd
        for function in (os.mkdir, os.open, os.stat)
    ):
        _raise_controlled(
            H2cPrepareErrorCode.H2C_PREPARE_CAPABILITY_UNAVAILABLE
        )


def _translate_directory_oserror(exc: OSError) -> NoReturn:
    if exc.errno in _CONTROLLED_INPUT_ERRNOS or exc.errno == errno.EINVAL:
        _raise_controlled(
            H2cPrepareErrorCode.H2C_PREPARE_PATH_CONTRACT_INVALID
        )
    if exc.errno in _CONTROLLED_CAPABILITY_ERRNOS:
        _raise_controlled(
            H2cPrepareErrorCode.H2C_PREPARE_CAPABILITY_UNAVAILABLE
        )
    raise exc


def _open_case_parent(parent: str) -> int:
    """Establish the one secure boundary every later operation is relative to."""
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        return os.open(parent, flags)
    except OSError as exc:
        _translate_directory_oserror(exc)


def _require_case_root_absent(name: str, *, parent_fd: int) -> None:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        _translate_directory_oserror(exc)
    _raise_controlled(H2cPrepareErrorCode.H2C_PREPARE_PATH_CONTRACT_INVALID)


def _begin_run() -> MmiProjectionRunContext:
    try:
        return begin_mmi_projection_run()
    except MmiClockContractError as exc:
        if exc.args != (exc.args[0],) or exc.args[0] not in _CLOCK_CODES:
            raise
        _raise_controlled(
            H2cPrepareErrorCode.H2C_PREPARE_CAPABILITY_UNAVAILABLE
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
            H2cPrepareErrorCode.H2C_PREPARE_SOURCE_CAPTURE_INVALID,
            owner_reason_codes=reasons,
        )
    if (
        result.status
        is MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    ):
        _raise_controlled(
            H2cPrepareErrorCode.H2C_PREPARE_LIVE_CHAIN_INVALID,
            owner_reason_codes=reasons,
        )
    raise RuntimeError("malformed MMI source capture result")


def _require_projection_build(
    result: object,
    *,
    expected_type: type[
        MmiPolicyProjectionBuildResult | MmiPortfolioProjectionBuildResult
    ],
    failure_code: H2cPrepareErrorCode = (
        H2cPrepareErrorCode.H2C_PREPARE_LIVE_CHAIN_INVALID
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
    failure_code: H2cPrepareErrorCode = (
        H2cPrepareErrorCode.H2C_PREPARE_LIVE_CHAIN_INVALID
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
            H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID
        )


def _derive_approved_list(settings_text: str) -> str:
    try:
        return derive_legacy_approved_extended_etf_json(
            strategy_settings_text=settings_text
        )
    except (StrategySettingsValidationError, yaml.YAMLError):
        _raise_controlled(
            H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID
        )
    except ValueError as exc:
        if str(exc) in _APPROVED_LIST_VALUE_ERRORS:
            _raise_controlled(
                H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID
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
            H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID
        )
    if not 1 <= len(exact_bytes) <= _LEGACY_PROMPT_MAXIMUM_BYTES:
        _raise_controlled(
            H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID
        )
    return exact_bytes


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
        chunk = os.read(
            fd,
            min(_READ_CHUNK_BYTES, maximum_bytes + 1 - observed),
        )
        if not chunk:
            break
        chunks.append(chunk)
        observed += len(chunk)
        if observed > maximum_bytes:
            break
    return b"".join(chunks)


def _stable_read_legacy_template(path: Path) -> bytes:
    flags = (
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    )
    fd: int | None = None
    try:
        fd = os.open(os.fspath(path), flags)
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        before = _read_witness(status)
        if before.size < 1 or before.size > _LEGACY_TEMPLATE_MAXIMUM_BYTES:
            _raise_controlled(
                H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID
            )
        exact_bytes = _read_to_eof_once(
            fd,
            maximum_bytes=_LEGACY_TEMPLATE_MAXIMUM_BYTES,
        )
        after = _read_witness(os.fstat(fd))
        if before != after or len(exact_bytes) != before.size:
            _raise_controlled(
                H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID
            )
        return exact_bytes
    except OSError as exc:
        if exc.errno in _CONTROLLED_INPUT_ERRNOS or exc.errno == errno.EINVAL:
            _raise_controlled(
                H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID
            )
        if exc.errno in _CONTROLLED_CAPABILITY_ERRNOS:
            _raise_controlled(
                H2cPrepareErrorCode.H2C_PREPARE_CAPABILITY_UNAVAILABLE
            )
        raise
    finally:
        if fd is not None:
            os.close(fd)


def _owner_calls(
    *,
    settings_source: MmiCapturedSource,
    portfolio_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
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
            H2cPrepareErrorCode.H2C_PREPARE_PORTFOLIO_NOT_COMPARABLE
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
            H2cPrepareErrorCode.H2C_PREPARE_PORTFOLIO_NOT_COMPARABLE
        ),
        allowed_reason_prefixes=_PORTFOLIO_REASON_PREFIXES,
    )
    if portfolio.get("portfolio_source_record_identity_sha256") != (
        portfolio_source.source_record.get("source_record_identity_sha256")
    ):
        _raise_controlled(
            H2cPrepareErrorCode.H2C_PREPARE_PORTFOLIO_NOT_COMPARABLE
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
            prompt_codes=_G2_PROMPT_CODES,
            prompt_public_code=(
                H2cPrepareErrorCode.H2C_PREPARE_PROMPT_CONTRACT_INVALID
            ),
            remaining_public_code=(
                H2cPrepareErrorCode.H2C_PREPARE_LIVE_CHAIN_INVALID
            ),
        )
    return prompt


def _require_manifest_layout(prepared: Mapping[str, object]) -> None:
    """Bind this owner's fixed tree to the frozen envelope's own declarations."""
    settings = prepared.get("strategy_settings_source")
    portfolio = prepared.get("portfolio_snapshot_source")
    template = prepared.get("legacy_prompt_template")
    h1_prompt = prepared.get("h1_prompt")
    legacy_prompt = prepared.get("legacy_prompt")
    responses = prepared.get("response_leaves")
    results = prepared.get("result_leaves")
    if any(
        type(value) is not dict
        for value in (
            settings,
            portfolio,
            template,
            h1_prompt,
            legacy_prompt,
            responses,
            results,
        )
    ):
        _raise_controlled(H2cPrepareErrorCode.H2C_PREPARE_MANIFEST_INVALID)
    declared = (
        settings.get("archive_relative_path"),
        portfolio.get("archive_relative_path"),
        template.get("archive_relative_path"),
        h1_prompt.get("relative_path"),
        legacy_prompt.get("relative_path"),
        responses.get("h1"),
        responses.get("legacy"),
        results.get("case_evidence_bundle"),
        results.get("comparison_report"),
        results.get("receipt"),
    )
    if (
        declared != _DECLARED_CASE_RELATIVE_PATHS
        or template.get("repository_relative_locator")
        != _LEGACY_TEMPLATE_LOCATOR
        or prepared.get("workflow_status") != _WORKFLOW_STATUS
    ):
        _raise_controlled(H2cPrepareErrorCode.H2C_PREPARE_MANIFEST_INVALID)


def _build_prepared_manifest(
    *,
    run_context: MmiProjectionRunContext,
    settings_source: MmiCapturedSource,
    portfolio_source: MmiCapturedSource,
    template_bytes: bytes,
    grounded_prompt: Mapping[str, object],
    h1_prompt_bytes: bytes,
    legacy_prompt_bytes: bytes,
) -> tuple[dict[str, object], bytes, str]:
    try:
        prepared = _prepared_case._build_mmi_h2c_prepared_case_v1(
            evaluation_timestamp_utc=run_context.evaluation_timestamp_utc,
            strategy_settings_source_record=dict(
                settings_source.source_record
            ),
            portfolio_snapshot_source_record=dict(
                portfolio_source.source_record
            ),
            legacy_prompt_template_bytes=template_bytes,
            grounded_prompt=grounded_prompt,
            h1_prompt_bytes=h1_prompt_bytes,
            legacy_prompt_bytes=legacy_prompt_bytes,
        )
        _prepared_case.validate_mmi_h2c_prepared_case_v1(
            prepared_case=prepared
        )
    except _prepared_case.MmiH2cPreparedCaseV1Error as exc:
        if exc.code != "MMI_H2C_PREPARED_CASE_V1_INVALID":
            raise
        _raise_controlled(
            H2cPrepareErrorCode.H2C_PREPARE_MANIFEST_INVALID,
            owner_reason_codes=(exc.code,),
        )
    _require_manifest_layout(prepared)
    identity = prepared.get("prepared_case_identity_sha256")
    if type(identity) is not str:
        raise RuntimeError("validated prepared case omitted its identity")
    try:
        manifest_bytes = canonical_json_bytes(
            prepared,
            maximum_bytes=_prepared_case._MAXIMUM_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        _raise_controlled(H2cPrepareErrorCode.H2C_PREPARE_MANIFEST_INVALID)
    return prepared, manifest_bytes, identity


@dataclass(frozen=True, slots=True)
class _WrittenFile:
    name: str
    device: int
    inode: int
    exact_bytes: bytes


def _translate_persistence_oserror(exc: OSError) -> NoReturn:
    if exc.errno not in _CONTROLLED_PERSISTENCE_ERRNOS:
        raise exc
    _raise_controlled(H2cPrepareErrorCode.H2C_PREPARE_PERSISTENCE_FAILED)


def _create_case_root(name: str, *, parent_fd: int) -> None:
    """Consume the case identifier exactly once, exclusively."""
    try:
        os.mkdir(name, _DIRECTORY_MODE, dir_fd=parent_fd)
    except FileExistsError:
        _raise_controlled(
            H2cPrepareErrorCode.H2C_PREPARE_PATH_CONTRACT_INVALID
        )
    except OSError as exc:
        _translate_persistence_oserror(exc)


def _open_case_directory(name: str, *, parent_fd: int) -> int:
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    fd: int | None = None
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
        os.fchmod(fd, _DIRECTORY_MODE)
        os.fsync(parent_fd)
    except OSError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _translate_persistence_oserror(exc)
    return fd


def _create_child_directory(name: str, *, parent_fd: int) -> int:
    try:
        os.mkdir(name, _DIRECTORY_MODE, dir_fd=parent_fd)
    except OSError as exc:
        _translate_persistence_oserror(exc)
    return _open_case_directory(name, parent_fd=parent_fd)


def _write_exact_file(
    *,
    name: str,
    exact_bytes: bytes,
    dir_fd: int,
) -> _WrittenFile:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | os.O_CLOEXEC
    )
    fd: int | None = None
    try:
        fd = os.open(name, flags, _FILE_MODE, dir_fd=dir_fd)
        os.fchmod(fd, _FILE_MODE)
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
        os.fsync(dir_fd)
    except OSError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _translate_persistence_oserror(exc)
    return _WrittenFile(
        name=name,
        device=witness.st_dev,
        inode=witness.st_ino,
        exact_bytes=exact_bytes,
    )


def _require_exact_persisted_file(
    written: _WrittenFile,
    *,
    dir_fd: int,
) -> None:
    flags = (
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    )
    fd: int | None = None
    try:
        fd = os.open(written.name, flags, dir_fd=dir_fd)
        status = os.fstat(fd)
        if (
            not stat.S_ISREG(status.st_mode)
            or stat.S_IMODE(status.st_mode) != _FILE_MODE
            or (status.st_dev, status.st_ino)
            != (written.device, written.inode)
            or status.st_size != len(written.exact_bytes)
        ):
            _raise_controlled(
                H2cPrepareErrorCode.H2C_PREPARE_PERSISTENCE_FAILED
            )
        observed = _read_to_eof_once(
            fd,
            maximum_bytes=len(written.exact_bytes),
        )
        if observed != written.exact_bytes:
            _raise_controlled(
                H2cPrepareErrorCode.H2C_PREPARE_PERSISTENCE_FAILED
            )
    except OSError as exc:
        _translate_persistence_oserror(exc)
    finally:
        if fd is not None:
            os.close(fd)


def _require_leaf_absent(name: str, *, dir_fd: int) -> None:
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        _translate_persistence_oserror(exc)
    _raise_controlled(H2cPrepareErrorCode.H2C_PREPARE_PATH_CONTRACT_INVALID)


def _persist_case(
    *,
    case_name: str,
    parent_fd: int,
    settings_bytes: bytes,
    portfolio_bytes: bytes,
    template_bytes: bytes,
    h1_prompt_bytes: bytes,
    legacy_prompt_bytes: bytes,
    manifest_bytes: bytes,
    identity: str,
) -> H2cPrepareResult:
    _create_case_root(case_name, parent_fd=parent_fd)
    open_fds: list[int] = []
    try:
        root_fd = _open_case_directory(case_name, parent_fd=parent_fd)
        open_fds.append(root_fd)
        directory_fds: dict[str, int] = {}
        for directory in _CASE_DIRECTORIES:
            child_fd = _create_child_directory(directory, parent_fd=root_fd)
            open_fds.append(child_fd)
            directory_fds[directory] = child_fd

        written: list[tuple[str, _WrittenFile]] = []
        for leaf, exact_bytes in (
            (_SETTINGS_ARCHIVE_LEAF, settings_bytes),
            (_PORTFOLIO_ARCHIVE_LEAF, portfolio_bytes),
            (_TEMPLATE_ARCHIVE_LEAF, template_bytes),
            (_H1_PROMPT_LEAF, h1_prompt_bytes),
            (_LEGACY_PROMPT_LEAF, legacy_prompt_bytes),
        ):
            directory, name = leaf
            written.append(
                (
                    directory,
                    _write_exact_file(
                        name=name,
                        exact_bytes=exact_bytes,
                        dir_fd=directory_fds[directory],
                    ),
                )
            )
        for directory, item in written:
            _require_exact_persisted_file(
                item,
                dir_fd=directory_fds[directory],
            )
        for directory, name in _ABSENT_LEAVES:
            _require_leaf_absent(name, dir_fd=directory_fds[directory])

        manifest_directory, manifest_name = _MANIFEST_LEAF
        manifest = _write_exact_file(
            name=manifest_name,
            exact_bytes=manifest_bytes,
            dir_fd=directory_fds[manifest_directory],
        )
        _require_exact_persisted_file(
            manifest,
            dir_fd=directory_fds[manifest_directory],
        )
    finally:
        for fd in reversed(open_fds):
            try:
                os.close(fd)
            except OSError:
                pass
    return H2cPrepareResult(
        workflow_status=_WORKFLOW_STATUS,
        prepared_case_identity_sha256=identity,
    )


def prepare_h2c_persisted_case(
    *,
    strategy_settings_expected_sha256: str,
    portfolio_snapshot_expected_sha256: str,
    case_root: Path,
) -> H2cPrepareResult:
    """Prepare one complete persisted H2c case and exit without waiting."""
    case_parent, case_name = _validate_arguments(
        strategy_settings_expected_sha256=(
            strategy_settings_expected_sha256
        ),
        portfolio_snapshot_expected_sha256=(
            portfolio_snapshot_expected_sha256
        ),
        case_root=case_root,
    )
    _require_filesystem_capabilities()
    parent_fd = _open_case_parent(case_parent)
    try:
        _require_case_root_absent(case_name, parent_fd=parent_fd)
        run_context = _begin_run()
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
                H2cPrepareErrorCode.H2C_PREPARE_PORTFOLIO_NOT_COMPARABLE
            )
        grounded_prompt = _owner_calls(
            settings_source=settings_source,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
        template_bytes = _stable_read_legacy_template(
            prompt_path(_FIXED_LEGACY_TEMPLATE_NAME)
        )
        legacy_prompt_bytes = _compile_legacy_prompt(
            template_text=_legacy_text(template_bytes),
            settings_text=settings_text,
            portfolio_text=portfolio_text,
            approved_list_json=_derive_approved_list(settings_text),
        )
        prompt_text = grounded_prompt.get("prompt_text")
        if type(prompt_text) is not str:
            raise RuntimeError("validated G2 omitted prompt_text")
        try:
            h1_prompt_bytes = prompt_text.encode("utf-8")
        except UnicodeEncodeError:
            raise RuntimeError(
                "validated G2 prompt is not UTF-8 encodable"
            ) from None
        _, manifest_bytes, identity = _build_prepared_manifest(
            run_context=run_context,
            settings_source=settings_source,
            portfolio_source=portfolio_source,
            template_bytes=template_bytes,
            grounded_prompt=grounded_prompt,
            h1_prompt_bytes=h1_prompt_bytes,
            legacy_prompt_bytes=legacy_prompt_bytes,
        )
        return _persist_case(
            case_name=case_name,
            parent_fd=parent_fd,
            settings_bytes=settings_source.raw_bytes,
            portfolio_bytes=portfolio_source.raw_bytes,
            template_bytes=template_bytes,
            h1_prompt_bytes=h1_prompt_bytes,
            legacy_prompt_bytes=legacy_prompt_bytes,
            manifest_bytes=manifest_bytes,
            identity=identity,
        )
    finally:
        os.close(parent_fd)
