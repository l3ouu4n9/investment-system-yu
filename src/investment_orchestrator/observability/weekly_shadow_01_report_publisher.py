"""Deterministic, report-only WEEKLY-SHADOW-01 artifact publication.

The sole public operation reconstructs one authenticated private WS01c context
from primitive inputs, constructs the two frozen successful artifacts entirely
in memory, and publishes their containing directory with Linux
``renameat2(RENAME_NOREPLACE)``.  Publication is report-only filesystem output:
it creates no pointer, state, permission, gate, portfolio, order, broker, or
execution authority.
"""

from __future__ import annotations

del annotations

import ctypes as _ctypes
from dataclasses import dataclass as _dataclass
from enum import Enum as _Enum
import errno as _errno
import hashlib as _hashlib
import json as _json
import os as _os
from pathlib import Path as _Path
import re as _re
import secrets as _secrets
import stat as _stat
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

from jsonschema import Draft202012Validator as _Draft202012Validator
from jsonschema.exceptions import SchemaError as _SchemaError
from jsonschema.exceptions import ValidationError as _ValidationError

from investment_orchestrator.observability import (
    weekly_shadow_01_response_validator as _response_validator,
)

if _TYPE_CHECKING:
    from os import PathLike
    from typing import Any, Mapping


__all__ = ("publish_weekly_shadow_report",)


_CONCRETE_PATH_TYPE = type(_Path())
_CONTEXT_TYPE = _response_validator._WS01cDownstreamContext
_CONTRACT_TYPE = _response_validator._AuthenticatedArtifactContract
_DOWNSTREAM_RESULT_TYPE = _response_validator._WS01cDownstreamResult
_BLOCKING_REASON_CODES = _response_validator._BLOCKING_REASON_CODES

_REPORT_SCHEMA_VERSION = "weekly_shadow_01_analyst_report_v1"
_RUN_SUMMARY_SCHEMA_VERSION = "weekly_shadow_01_run_summary_v1"
_REPORT_SCHEMA_IDENTITY = (
    "7b415fa8eb7cb4ecce92ddf06eb394574f7d1435dd840657396dd2eeb0f4feb8"
)
_RUN_SUMMARY_SCHEMA_IDENTITY = (
    "114e92f0d151bba7266a651172cd7dac01f9652a4c6fe47557582b10dcf706a7"
)
_REPORT_SEMANTIC_IDENTITY = (
    "195112bf9087b1f63f680c93a77d41487e4bceae4564a621c55c15b6cb684014"
)
_RUN_SUMMARY_SEMANTIC_IDENTITY = (
    "88bc37d815c348fa0791c51fbdc660f2527c2d9975a01ab2bde2b9853c2a99b3"
)
_NEGATIVE_AUTHORITY_IDENTITY = (
    "b20ea7218880c5799897d7d3fbd74515af88ad6fcc9e2f4c1d4cc83649e61ff1"
)
_REPORT_DOMAIN = b"weekly_shadow_01_report_v1\0"
_RUN_SUMMARY_DOMAIN = b"weekly_shadow_01_run_summary_v1\0"
_MAXIMUM_REPORT_BYTES = 262_144
_MAXIMUM_RUN_SUMMARY_BYTES = 65_536

_REPORT_FILENAME = "weekly_shadow_01_analyst_report.json"
_RUN_SUMMARY_FILENAME = "weekly_shadow_01_run_summary.json"
_ARTIFACT_FILENAMES = (_REPORT_FILENAME, _RUN_SUMMARY_FILENAME)
_ATTEMPTS_DIRECTORY_NAME = "report_attempts"
_REPORTS_DIRECTORY_NAME = "reports"
_ATTEMPT_PREFIX = ".attempt-"
_ATTEMPT_SUFFIX = ".tmp"
_MAXIMUM_ATTEMPT_NAME_TRIES = 16
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_LOWERCASE_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_ATTEMPT_NAME = _re.compile(r"^\.attempt-[0-9a-f]{32}\.tmp$")

_EXPECTED_NEGATIVE_AUTHORITY = (
    ("authority_effect", "none"),
    ("permission_effect", "none"),
    ("approval_eligible", False),
    ("precompile_eligible", False),
    ("order_eligible", False),
    ("portfolio_effect", "none"),
    ("order_path_effect", "none"),
    ("execution_authority", False),
)
_REPORT_ANALYST_CONTENT_FIELDS = (
    "analyst_conclusion",
    "analyst_confidence",
    "analytical_sections",
    "analyst_limitation_codes",
)
_REPORT_CONTRACT_FIELDS = frozenset(
    {
        "contract_version",
        "contract_id",
        "schema_identity_sha256",
        "owner",
        "ordered_relevant_blocking_reason_codes",
        "ordered_relevant_analyst_limitation_codes",
        "required_profile_identities_sha256",
        "authority_effect",
    }
)
_RUN_SUMMARY_CONTRACT_FIELDS = _REPORT_CONTRACT_FIELDS

_OPEN_DIRECTORY_FLAGS = (
    _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC
)
_OPEN_READ_FLAGS = _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC
_OPEN_CREATE_FLAGS = (
    _os.O_RDWR
    | _os.O_CREAT
    | _os.O_EXCL
    | _os.O_NOFOLLOW
    | _os.O_CLOEXEC
)
_RENAME_NOREPLACE = 1


class _WS01dFailure(RuntimeError):
    """Private deterministic reason-code carrier."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if code not in _BLOCKING_REASON_CODES:
            code = "WS01_BR_INTERNAL_INVARIANT_FAILURE"
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    __slots__ = ()


class _NonFiniteJsonNumber(ValueError):
    __slots__ = ()


class _RenamePrimitiveUnavailable(RuntimeError):
    __slots__ = ()


class _RenameDeterministicFailure(RuntimeError):
    __slots__ = ()


class _PublicationPhase(_Enum):
    PRECOMMIT = "PRECOMMIT"
    NAMESPACE_COMMITTED_NOT_DURABLE = "NAMESPACE_COMMITTED_NOT_DURABLE"
    DURABLE_AND_VERIFIED = "DURABLE_AND_VERIFIED"


@_dataclass(frozen=True, slots=True, init=False)
class _WS01dResult:
    """Closed reason-only WS01d success/failure envelope."""

    ok: bool
    value: object | None
    reason_code: str | None

    def __new__(cls, *_args: object, **_kwargs: object) -> "_WS01dResult":
        raise TypeError("WS01d results are created only by private factories")


@_dataclass(frozen=True, slots=True, init=False, repr=False)
class _PublicationReceipt:
    """Immutable non-authoritative identification of published bytes."""

    report_identity_sha256: str
    run_summary_identity_sha256: str
    publication_relative_path: str
    artifact_filenames: tuple[str, str]
    publication_reused: bool

    def __new__(
        cls, *_args: object, **_kwargs: object
    ) -> "_PublicationReceipt":
        raise TypeError("publication receipts are created only by private factories")

    def __reduce__(self) -> object:
        raise TypeError("publication receipts are not serializable")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("publication receipts are not serializable")


@_dataclass(frozen=True, slots=True)
class _PreparedArtifacts:
    report: "Mapping[str, Any]"
    run_summary: "Mapping[str, Any]"
    report_bytes: bytes
    run_summary_bytes: bytes
    report_identity_sha256: str
    run_summary_identity_sha256: str


@_dataclass(frozen=True, slots=True)
class _DirectoryWitness:
    device: int
    inode: int


@_dataclass(frozen=True, slots=True)
class _RegularFileWitness:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int


@_dataclass(frozen=True, slots=True)
class _DirectoryClosingWitness:
    device: int
    inode: int
    mode: int
    links: int
    modified_ns: int
    changed_ns: int


@_dataclass(frozen=True, slots=True)
class _DirectoryChainEntry:
    parent_descriptor: int
    name: str
    descriptor: int
    witness: _DirectoryWitness


@_dataclass(frozen=True, slots=True)
class _OwnedDirectory:
    descriptor: int
    witness: _DirectoryWitness
    name: str
    parent_descriptor: int


@_dataclass(frozen=True, slots=True)
class _OwnedAttempt:
    directory: _OwnedDirectory
    name: str
    report_descriptor: int
    summary_descriptor: int


@_dataclass(frozen=True, slots=True)
class _VerifiedArtifactEntry:
    name: str
    descriptor: int
    witness: _RegularFileWitness
    raw: bytes


@_dataclass(frozen=True, slots=True)
class _VerifiedGeneration:
    directory: _OwnedDirectory
    report: _VerifiedArtifactEntry
    summary: _VerifiedArtifactEntry


class _DescriptorOwner:
    __slots__ = ("_descriptors",)

    def __init__(self) -> None:
        self._descriptors: list[int] = []

    def register(self, descriptor: int) -> int:
        if type(descriptor) is not int or descriptor < 0:
            _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        self._descriptors.append(descriptor)
        return descriptor

    def close_all(self) -> bool:
        failed = False
        descriptors, self._descriptors = self._descriptors, []
        for descriptor in reversed(descriptors):
            try:
                _os.close(descriptor)
            except Exception:
                failed = True
        return failed


def _fail(code: str) -> "NoReturn":
    raise _WS01dFailure(code)


def _result_failure(reason_code: object) -> _WS01dResult:
    code = (
        reason_code
        if type(reason_code) is str and reason_code in _BLOCKING_REASON_CODES
        else "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    )
    result = object.__new__(_WS01dResult)
    object.__setattr__(result, "ok", False)
    object.__setattr__(result, "value", None)
    object.__setattr__(result, "reason_code", code)
    return result


def _result_success(receipt: object) -> _WS01dResult:
    if type(receipt) is not _PublicationReceipt:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    result = object.__new__(_WS01dResult)
    object.__setattr__(result, "ok", True)
    object.__setattr__(result, "value", receipt)
    object.__setattr__(result, "reason_code", None)
    return result


def _new_receipt(
    artifacts: _PreparedArtifacts,
    *,
    reused: bool,
) -> _PublicationReceipt:
    if type(artifacts) is not _PreparedArtifacts or type(reused) is not bool:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    relative = f"{_REPORTS_DIRECTORY_NAME}/{artifacts.report_identity_sha256}"
    receipt = object.__new__(_PublicationReceipt)
    object.__setattr__(
        receipt,
        "report_identity_sha256",
        artifacts.report_identity_sha256,
    )
    object.__setattr__(
        receipt,
        "run_summary_identity_sha256",
        artifacts.run_summary_identity_sha256,
    )
    object.__setattr__(receipt, "publication_relative_path", relative)
    object.__setattr__(receipt, "artifact_filenames", _ARTIFACT_FILENAMES)
    object.__setattr__(receipt, "publication_reused", reused)
    return receipt


def _canonical_json_bytes(value: object, *, maximum: int) -> bytes:
    if type(maximum) is not int or maximum <= 0:
        _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")
    try:
        encoded = _json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")
    if len(encoded) > maximum:
        _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")
    return encoded


def _strict_json_object(raw: bytes) -> dict[str, object]:
    def reject_duplicate(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, member in pairs:
            if key in value:
                raise _DuplicateJsonKey(key)
            value[key] = member
        return value

    def reject_nonfinite(value: str) -> object:
        raise _NonFiniteJsonNumber(value)

    try:
        text = raw.decode("utf-8")
        value = _json.loads(
            text,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_nonfinite,
        )
    except (
        UnicodeDecodeError,
        _json.JSONDecodeError,
        _DuplicateJsonKey,
        _NonFiniteJsonNumber,
        OverflowError,
        ValueError,
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    if type(value) is not dict:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    return value


def _deep_thaw(value: object) -> object:
    if isinstance(value, _MappingProxyType):
        return {key: _deep_thaw(member) for key, member in value.items()}
    if type(value) is tuple:
        return [_deep_thaw(member) for member in value]
    if type(value) in (str, int, bool, bytes) or value is None:
        return bytes(value) if type(value) is bytes else value
    _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")


def _validate_schema(schema: object, value: object, *, code: str) -> None:
    if type(schema) is not dict or type(value) is not dict:
        _fail(code)
    try:
        _Draft202012Validator.check_schema(schema)
        _Draft202012Validator(schema).validate(value)
    except (_SchemaError, _ValidationError, TypeError, ValueError):
        _fail(code)


def _sha256_identity(domain: bytes, value: object, *, maximum: int) -> str:
    if type(domain) is not bytes or not domain.endswith(b"\0"):
        _fail("WS01_BR_REPORT_IDENTITY_FAILURE")
    canonical = _canonical_json_bytes(value, maximum=maximum)
    try:
        identity = _hashlib.sha256(domain + canonical).hexdigest()
    except Exception:
        _fail("WS01_BR_REPORT_IDENTITY_FAILURE")
    if _LOWERCASE_SHA256.fullmatch(identity) is None:
        _fail("WS01_BR_REPORT_IDENTITY_FAILURE")
    return identity


def _require_sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _LOWERCASE_SHA256.fullmatch(value) is None:
        _fail(code)
    return value


def _require_artifact_contract(
    contract: object,
    *,
    schema_version: str,
    schema_identity: str,
    semantic_identity: str,
    identity_domain: bytes,
    maximum: int,
    owner: str,
) -> tuple[dict[str, object], dict[str, object]]:
    if type(contract) is not _CONTRACT_TYPE:
        _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")
    schema = _deep_thaw(contract.schema)
    semantic = _deep_thaw(contract.semantic_contract)
    if type(schema) is not dict or type(semantic) is not dict:
        _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")
    required_profiles = semantic.get("required_profile_identities_sha256")
    if (
        contract.schema_version != schema_version
        or contract.schema_identity_sha256 != schema_identity
        or contract.semantic_contract_identity_sha256 != semantic_identity
        or contract.identity_domain != identity_domain
        or contract.maximum_canonical_bytes != maximum
        or set(semantic) != _REPORT_CONTRACT_FIELDS
        or semantic.get("contract_version") != f"{schema_version}_contract_v1"
        or semantic.get("contract_id") != f"{schema_version}_semantic_contract"
        or semantic.get("schema_identity_sha256") != schema_identity
        or semantic.get("owner") != owner
        or semantic.get("authority_effect") != "none"
        or type(required_profiles) is not list
        or _NEGATIVE_AUTHORITY_IDENTITY not in required_profiles
    ):
        _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")
    try:
        _Draft202012Validator.check_schema(schema)
    except (_SchemaError, TypeError, ValueError):
        _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")
    return schema, semantic


def _require_authenticated_context(
    context: object,
) -> tuple[
    _CONTEXT_TYPE,
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    if type(context) is not _CONTEXT_TYPE:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    if (
        context.negative_authority_profile_identity_sha256
        != _NEGATIVE_AUTHORITY_IDENTITY
    ):
        _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")
    negative = _deep_thaw(context.negative_authority_profile)
    analyst_content = _deep_thaw(context.validated_analyst_content)
    if (
        type(negative) is not dict
        or negative != dict(_EXPECTED_NEGATIVE_AUTHORITY)
        or type(analyst_content) is not dict
        or set(analyst_content) != set(_REPORT_ANALYST_CONTENT_FIELDS)
    ):
        _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")
    for identity in (
        context.input_package_identity_sha256,
        context.response_capture_identity_sha256,
        context.validation_identity_sha256,
    ):
        _require_sha256(identity, code="WS01_BR_REPORT_CONSTRUCTION_FAILED")
    if (
        type(context.run_id) is not str
        or not context.run_id
        or len(context.run_id) > 128
    ):
        _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")
    report_schema, report_semantic = _require_artifact_contract(
        context.analyst_report_contract,
        schema_version=_REPORT_SCHEMA_VERSION,
        schema_identity=_REPORT_SCHEMA_IDENTITY,
        semantic_identity=_REPORT_SEMANTIC_IDENTITY,
        identity_domain=_REPORT_DOMAIN,
        maximum=_MAXIMUM_REPORT_BYTES,
        owner="deterministic_code_and_validated_llm_content",
    )
    summary_schema, summary_semantic = _require_artifact_contract(
        context.run_summary_contract,
        schema_version=_RUN_SUMMARY_SCHEMA_VERSION,
        schema_identity=_RUN_SUMMARY_SCHEMA_IDENTITY,
        semantic_identity=_RUN_SUMMARY_SEMANTIC_IDENTITY,
        identity_domain=_RUN_SUMMARY_DOMAIN,
        maximum=_MAXIMUM_RUN_SUMMARY_BYTES,
        owner="deterministic_code",
    )
    if (
        report_semantic.get("ordered_relevant_blocking_reason_codes") != []
        or summary_semantic.get("ordered_relevant_analyst_limitation_codes") != []
    ):
        _fail("WS01_BR_REPORT_CONSTRUCTION_FAILED")
    return context, report_schema, summary_schema, {
        "negative": negative,
        "analyst_content": analyst_content,
    }


def _prepare_artifacts(context: object) -> _PreparedArtifacts:
    (
        authenticated,
        report_schema,
        summary_schema,
        projected,
    ) = _require_authenticated_context(context)
    negative = projected["negative"]
    analyst_content = projected["analyst_content"]
    report_without_identity = {
        "schema_version": _REPORT_SCHEMA_VERSION,
        "run_id": authenticated.run_id,
        "input_package_identity_sha256": (
            authenticated.input_package_identity_sha256
        ),
        "response_capture_identity_sha256": (
            authenticated.response_capture_identity_sha256
        ),
        "validation_identity_sha256": authenticated.validation_identity_sha256,
        "code_owned_status": {
            "run_status": "ANALYSIS_COMPLETE",
            "validation_status": "VALID",
            "publication_status": "PUBLISHED",
            "blocking_reason_codes": [],
        },
        "validated_analyst_content": analyst_content,
        "negative_authority_profile": negative,
    }
    report_identity = _sha256_identity(
        _REPORT_DOMAIN,
        report_without_identity,
        maximum=_MAXIMUM_REPORT_BYTES,
    )
    report = {**report_without_identity, "report_identity_sha256": report_identity}
    _validate_schema(
        report_schema,
        report,
        code="WS01_BR_REPORT_CONSTRUCTION_FAILED",
    )
    report_bytes = _canonical_json_bytes(
        report,
        maximum=_MAXIMUM_REPORT_BYTES,
    )
    if (
        _sha256_identity(
            _REPORT_DOMAIN,
            {
                key: value
                for key, value in report.items()
                if key != "report_identity_sha256"
            },
            maximum=_MAXIMUM_REPORT_BYTES,
        )
        != report_identity
    ):
        _fail("WS01_BR_REPORT_IDENTITY_FAILURE")

    summary_without_identity = {
        "schema_version": _RUN_SUMMARY_SCHEMA_VERSION,
        "run_id": authenticated.run_id,
        "run_status": "ANALYSIS_COMPLETE",
        "validation_status": "VALID",
        "publication_status": "PUBLISHED",
        "blocking_reason_codes": [],
        "report_identity_sha256": report_identity,
        "negative_authority_profile": negative,
    }
    summary_identity = _sha256_identity(
        _RUN_SUMMARY_DOMAIN,
        summary_without_identity,
        maximum=_MAXIMUM_RUN_SUMMARY_BYTES,
    )
    summary = {
        **summary_without_identity,
        "run_summary_identity_sha256": summary_identity,
    }
    _validate_schema(
        summary_schema,
        summary,
        code="WS01_BR_REPORT_CONSTRUCTION_FAILED",
    )
    summary_bytes = _canonical_json_bytes(
        summary,
        maximum=_MAXIMUM_RUN_SUMMARY_BYTES,
    )
    if (
        _sha256_identity(
            _RUN_SUMMARY_DOMAIN,
            {
                key: value
                for key, value in summary.items()
                if key != "run_summary_identity_sha256"
            },
            maximum=_MAXIMUM_RUN_SUMMARY_BYTES,
        )
        != summary_identity
    ):
        _fail("WS01_BR_REPORT_IDENTITY_FAILURE")
    _verify_completed_values(
        report,
        summary,
        report_schema=report_schema,
        summary_schema=summary_schema,
    )
    if (
        _canonical_json_bytes(report, maximum=_MAXIMUM_REPORT_BYTES)
        != report_bytes
        or _canonical_json_bytes(summary, maximum=_MAXIMUM_RUN_SUMMARY_BYTES)
        != summary_bytes
    ):
        _fail("WS01_BR_REPORT_IDENTITY_FAILURE")
    return _PreparedArtifacts(
        report=_MappingProxyType(report),
        run_summary=_MappingProxyType(summary),
        report_bytes=report_bytes,
        run_summary_bytes=summary_bytes,
        report_identity_sha256=report_identity,
        run_summary_identity_sha256=summary_identity,
    )


def _verify_completed_values(
    report: object,
    summary: object,
    *,
    report_schema: object,
    summary_schema: object,
) -> None:
    if type(report) is not dict or type(summary) is not dict:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    _validate_schema(
        report_schema,
        report,
        code="WS01_BR_IMMUTABLE_VERIFICATION_FAILED",
    )
    _validate_schema(
        summary_schema,
        summary,
        code="WS01_BR_IMMUTABLE_VERIFICATION_FAILED",
    )
    report_identity = _require_sha256(
        report.get("report_identity_sha256"),
        code="WS01_BR_IMMUTABLE_VERIFICATION_FAILED",
    )
    summary_identity = _require_sha256(
        summary.get("run_summary_identity_sha256"),
        code="WS01_BR_IMMUTABLE_VERIFICATION_FAILED",
    )
    if (
        _sha256_identity(
            _REPORT_DOMAIN,
            {
                key: value
                for key, value in report.items()
                if key != "report_identity_sha256"
            },
            maximum=_MAXIMUM_REPORT_BYTES,
        )
        != report_identity
        or _sha256_identity(
            _RUN_SUMMARY_DOMAIN,
            {
                key: value
                for key, value in summary.items()
                if key != "run_summary_identity_sha256"
            },
            maximum=_MAXIMUM_RUN_SUMMARY_BYTES,
        )
        != summary_identity
        or summary.get("report_identity_sha256") != report_identity
        or summary.get("run_id") != report.get("run_id")
        or summary.get("negative_authority_profile")
        != report.get("negative_authority_profile")
        or report.get("negative_authority_profile")
        != dict(_EXPECTED_NEGATIVE_AUTHORITY)
        or summary.get("negative_authority_profile")
        != dict(_EXPECTED_NEGATIVE_AUTHORITY)
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")


def _verify_artifact_bytes(
    artifacts: _PreparedArtifacts,
    report_raw: bytes,
    summary_raw: bytes,
    *,
    context: _CONTEXT_TYPE,
) -> None:
    if (
        type(report_raw) is not bytes
        or type(summary_raw) is not bytes
        or len(report_raw) > _MAXIMUM_REPORT_BYTES
        or len(summary_raw) > _MAXIMUM_RUN_SUMMARY_BYTES
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    report = _strict_json_object(report_raw)
    summary = _strict_json_object(summary_raw)
    report_schema = _deep_thaw(context.analyst_report_contract.schema)
    summary_schema = _deep_thaw(context.run_summary_contract.schema)
    _verify_completed_values(
        report,
        summary,
        report_schema=report_schema,
        summary_schema=summary_schema,
    )
    if (
        _canonical_json_bytes(report, maximum=_MAXIMUM_REPORT_BYTES) != report_raw
        or _canonical_json_bytes(summary, maximum=_MAXIMUM_RUN_SUMMARY_BYTES)
        != summary_raw
        or report_raw != artifacts.report_bytes
        or summary_raw != artifacts.run_summary_bytes
        or report.get("report_identity_sha256")
        != artifacts.report_identity_sha256
        or summary.get("run_summary_identity_sha256")
        != artifacts.run_summary_identity_sha256
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")


def _normalize_repository_root(
    value: "str | PathLike[str] | None",
) -> _Path:
    if value is None:
        root = _Path(_response_validator.__file__).parents[3]
    else:
        try:
            root = _Path(value)
        except TypeError:
            _fail("WS01_BR_SOURCE_GENERATION_INVALID")
    if not root.is_absolute() or any(
        component in ("", ".", "..") for component in root.parts[1:]
    ):
        _fail("WS01_BR_SOURCE_GENERATION_INVALID")
    return root


def _normalize_output_root(value: "str | PathLike[str]") -> _Path:
    try:
        root = _Path(value)
    except TypeError:
        _fail("WS01_BR_PUBLICATION_FAILED")
    if not root.is_absolute() or any(
        component in ("", ".", "..") for component in root.parts[1:]
    ):
        _fail("WS01_BR_PUBLICATION_FAILED")
    return root


def _directory_witness(descriptor: int) -> _DirectoryWitness:
    try:
        status = _os.fstat(descriptor)
    except OSError:
        _fail("WS01_BR_PUBLICATION_FAILED")
    if not _stat.S_ISDIR(status.st_mode):
        _fail("WS01_BR_PUBLICATION_FAILED")
    return _DirectoryWitness(status.st_dev, status.st_ino)


def _require_directory_status(
    status: object,
    *,
    expected: _DirectoryWitness | None = None,
    exact_mode: int | None = None,
) -> _DirectoryWitness:
    if (
        not hasattr(status, "st_mode")
        or not _stat.S_ISDIR(status.st_mode)
        or (
            exact_mode is not None
            and _stat.S_IMODE(status.st_mode) != exact_mode
        )
    ):
        _fail("WS01_BR_PUBLICATION_FAILED")
    witness = _DirectoryWitness(status.st_dev, status.st_ino)
    if expected is not None and witness != expected:
        _fail("WS01_BR_PUBLICATION_FAILED")
    return witness


def _open_output_root_chain(
    root: _Path,
    *,
    owner: _DescriptorOwner,
) -> tuple[int, tuple[_DirectoryChainEntry, ...]]:
    if (
        type(root) is not _CONCRETE_PATH_TYPE
        or not root.is_absolute()
        or any(component in ("", ".", "..") for component in root.parts[1:])
    ):
        _fail("WS01_BR_PUBLICATION_FAILED")
    try:
        descriptor = owner.register(_os.open("/", _OPEN_DIRECTORY_FLAGS))
    except OSError:
        _fail("WS01_BR_PUBLICATION_FAILED")
    root_witness = _directory_witness(descriptor)
    entries: list[_DirectoryChainEntry] = [
        _DirectoryChainEntry(-1, "/", descriptor, root_witness)
    ]
    for component in root.parts[1:]:
        parent_descriptor = descriptor
        try:
            descriptor = owner.register(
                _os.open(
                    component,
                    _OPEN_DIRECTORY_FLAGS,
                    dir_fd=parent_descriptor,
                )
            )
            entry_status = _os.stat(
                component,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            _fail("WS01_BR_PUBLICATION_FAILED")
        witness = _directory_witness(descriptor)
        _require_directory_status(entry_status, expected=witness)
        entries.append(
            _DirectoryChainEntry(
                parent_descriptor,
                component,
                descriptor,
                witness,
            )
        )
    return descriptor, tuple(entries)


def _verify_directory_chain(entries: tuple[_DirectoryChainEntry, ...]) -> None:
    if not entries or entries[0].name != "/":
        _fail("WS01_BR_PUBLICATION_FAILED")
    for index, entry in enumerate(entries):
        current = _directory_witness(entry.descriptor)
        if current != entry.witness:
            _fail("WS01_BR_PUBLICATION_FAILED")
        if index == 0:
            continue
        try:
            status = _os.stat(
                entry.name,
                dir_fd=entry.parent_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            _fail("WS01_BR_PUBLICATION_FAILED")
        _require_directory_status(status, expected=entry.witness)


def _open_or_create_fixed_directory(
    parent_descriptor: int,
    name: str,
    *,
    owner: _DescriptorOwner,
    parent_device: int,
) -> tuple[_OwnedDirectory, bool]:
    created = False
    creation_witness: _DirectoryWitness | None = None
    try:
        _os.mkdir(name, _DIRECTORY_MODE, dir_fd=parent_descriptor)
        created = True
        creation_status = _os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        creation_witness = _require_directory_status(creation_status)
    except FileExistsError:
        pass
    except OSError:
        _fail("WS01_BR_PUBLICATION_FAILED")
    try:
        descriptor = owner.register(
            _os.open(name, _OPEN_DIRECTORY_FLAGS, dir_fd=parent_descriptor)
        )
        status = _os.fstat(descriptor)
        entry_status = _os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if created:
            _os.fchmod(descriptor, _DIRECTORY_MODE)
            status = _os.fstat(descriptor)
            entry_status = _os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
    except OSError:
        _fail("WS01_BR_PUBLICATION_FAILED")
    witness = _require_directory_status(
        status,
        exact_mode=_DIRECTORY_MODE,
    )
    _require_directory_status(
        entry_status,
        expected=witness,
        exact_mode=_DIRECTORY_MODE,
    )
    if creation_witness is not None and witness != creation_witness:
        _fail("WS01_BR_PUBLICATION_FAILED")
    if witness.device != parent_device:
        _fail("WS01_BR_PUBLICATION_FAILED")
    return (
        _OwnedDirectory(descriptor, witness, name, parent_descriptor),
        created,
    )


def _verify_owned_directory(directory: _OwnedDirectory) -> None:
    try:
        descriptor_status = _os.fstat(directory.descriptor)
        entry_status = _os.stat(
            directory.name,
            dir_fd=directory.parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        _fail("WS01_BR_PUBLICATION_FAILED")
    _require_directory_status(
        descriptor_status,
        expected=directory.witness,
        exact_mode=_DIRECTORY_MODE,
    )
    _require_directory_status(
        entry_status,
        expected=directory.witness,
        exact_mode=_DIRECTORY_MODE,
    )


def _fsync_directory(descriptor: int) -> None:
    try:
        _os.fsync(descriptor)
    except OSError:
        _fail("WS01_BR_PUBLICATION_FAILED")


def _create_attempt_directory(
    attempts: _OwnedDirectory,
    *,
    owner: _DescriptorOwner,
) -> _OwnedDirectory:
    for _ in range(_MAXIMUM_ATTEMPT_NAME_TRIES):
        name = f"{_ATTEMPT_PREFIX}{_secrets.token_hex(16)}{_ATTEMPT_SUFFIX}"
        if _ATTEMPT_NAME.fullmatch(name) is None:
            _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        try:
            _os.mkdir(name, _DIRECTORY_MODE, dir_fd=attempts.descriptor)
        except FileExistsError:
            continue
        except OSError:
            _fail("WS01_BR_PUBLICATION_FAILED")
        try:
            creation_status = _os.stat(
                name,
                dir_fd=attempts.descriptor,
                follow_symlinks=False,
            )
            creation_witness = _require_directory_status(creation_status)
            descriptor = owner.register(
                _os.open(
                    name,
                    _OPEN_DIRECTORY_FLAGS,
                    dir_fd=attempts.descriptor,
                )
            )
            _os.fchmod(descriptor, _DIRECTORY_MODE)
            descriptor_status = _os.fstat(descriptor)
            entry_status = _os.stat(
                name,
                dir_fd=attempts.descriptor,
                follow_symlinks=False,
            )
        except OSError:
            _fail("WS01_BR_PUBLICATION_FAILED")
        witness = _require_directory_status(
            descriptor_status,
            exact_mode=_DIRECTORY_MODE,
        )
        _require_directory_status(
            entry_status,
            expected=witness,
            exact_mode=_DIRECTORY_MODE,
        )
        if (
            witness != creation_witness
            or witness.device != attempts.witness.device
        ):
            _fail("WS01_BR_PUBLICATION_FAILED")
        _fsync_directory(attempts.descriptor)
        return _OwnedDirectory(
            descriptor,
            witness,
            name,
            attempts.descriptor,
        )
    _fail("WS01_BR_PUBLICATION_FAILED")


def _regular_file_witness(
    status: object,
    *,
    expected_size: int,
) -> _RegularFileWitness:
    if (
        not hasattr(status, "st_mode")
        or not _stat.S_ISREG(status.st_mode)
        or _stat.S_IMODE(status.st_mode) != _FILE_MODE
        or status.st_nlink != 1
        or status.st_size != expected_size
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    return _RegularFileWitness(
        status.st_dev,
        status.st_ino,
        _stat.S_IMODE(status.st_mode),
        status.st_nlink,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _directory_closing_witness(
    descriptor: int,
) -> _DirectoryClosingWitness:
    try:
        status = _os.fstat(descriptor)
    except OSError:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    if (
        not _stat.S_ISDIR(status.st_mode)
        or _stat.S_IMODE(status.st_mode) != _DIRECTORY_MODE
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    return _DirectoryClosingWitness(
        status.st_dev,
        status.st_ino,
        _stat.S_IMODE(status.st_mode),
        status.st_nlink,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _require_named_artifact_binding(
    directory: _OwnedDirectory,
    *,
    name: str,
    descriptor: int,
    expected_size: int,
    expected_witness: _RegularFileWitness | None = None,
) -> _RegularFileWitness:
    try:
        descriptor_status = _os.fstat(descriptor)
        entry_status = _os.stat(
            name,
            dir_fd=directory.descriptor,
            follow_symlinks=False,
        )
    except OSError:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    descriptor_witness = _regular_file_witness(
        descriptor_status,
        expected_size=expected_size,
    )
    entry_witness = _regular_file_witness(
        entry_status,
        expected_size=expected_size,
    )
    if (
        descriptor_witness != entry_witness
        or (
            expected_witness is not None
            and descriptor_witness != expected_witness
        )
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    return descriptor_witness


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    view = memoryview(payload)
    while offset < len(payload):
        try:
            written = _os.write(descriptor, view[offset:])
        except OSError:
            _fail("WS01_BR_PUBLICATION_FAILED")
        if type(written) is not int or written <= 0 or written > len(payload) - offset:
            _fail("WS01_BR_PUBLICATION_FAILED")
        offset += written


def _read_descriptor_stably(
    descriptor: int,
    *,
    expected_size: int,
) -> bytes:
    try:
        before_status = _os.fstat(descriptor)
        before = _regular_file_witness(
            before_status,
            expected_size=expected_size,
        )
        _os.lseek(descriptor, 0, _os.SEEK_SET)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = _os.read(descriptor, min(65_536, expected_size - total + 1))
            if type(chunk) is not bytes:
                _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
            if not chunk:
                break
            total += len(chunk)
            if total > expected_size:
                _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
            chunks.append(chunk)
        after = _regular_file_witness(
            _os.fstat(descriptor),
            expected_size=expected_size,
        )
    except _WS01dFailure:
        raise
    except OSError:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    if before != after or total != expected_size:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    return b"".join(chunks)


def _create_artifact_file(
    directory: _OwnedDirectory,
    name: str,
    payload: bytes,
    *,
    owner: _DescriptorOwner,
) -> int:
    try:
        descriptor = owner.register(
            _os.open(
                name,
                _OPEN_CREATE_FLAGS,
                _FILE_MODE,
                dir_fd=directory.descriptor,
            )
        )
        _os.fchmod(descriptor, _FILE_MODE)
    except OSError:
        _fail("WS01_BR_PUBLICATION_FAILED")
    _write_all(descriptor, payload)
    try:
        _os.fsync(descriptor)
    except OSError:
        _fail("WS01_BR_PUBLICATION_FAILED")
    actual = _read_descriptor_stably(descriptor, expected_size=len(payload))
    if actual != payload:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    try:
        entry_status = _os.stat(
            name,
            dir_fd=directory.descriptor,
            follow_symlinks=False,
        )
    except OSError:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    entry = _regular_file_witness(entry_status, expected_size=len(payload))
    held = _regular_file_witness(_os.fstat(descriptor), expected_size=len(payload))
    if entry.device != held.device or entry.inode != held.inode:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    return descriptor


def _directory_inventory(descriptor: int) -> tuple[str, ...]:
    try:
        names = _os.listdir(descriptor)
    except OSError:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    if not all(type(name) is str for name in names):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    return tuple(sorted(names))


def _read_verified_artifact_entry(
    directory: _OwnedDirectory,
    *,
    name: str,
    descriptor: int,
    expected_size: int,
) -> _VerifiedArtifactEntry:
    witness = _require_named_artifact_binding(
        directory,
        name=name,
        descriptor=descriptor,
        expected_size=expected_size,
    )
    raw = _read_descriptor_stably(
        descriptor,
        expected_size=expected_size,
    )
    _require_named_artifact_binding(
        directory,
        name=name,
        descriptor=descriptor,
        expected_size=expected_size,
        expected_witness=witness,
    )
    return _VerifiedArtifactEntry(
        name=name,
        descriptor=descriptor,
        witness=witness,
        raw=raw,
    )


def _verify_staged_attempt(
    attempt: _OwnedAttempt,
    artifacts: _PreparedArtifacts,
    *,
    context: _CONTEXT_TYPE,
) -> _VerifiedGeneration:
    _verify_owned_directory(attempt.directory)
    if _directory_inventory(attempt.directory.descriptor) != tuple(
        sorted(_ARTIFACT_FILENAMES)
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    report = _read_verified_artifact_entry(
        attempt.directory,
        name=_REPORT_FILENAME,
        descriptor=attempt.report_descriptor,
        expected_size=len(artifacts.report_bytes),
    )
    summary = _read_verified_artifact_entry(
        attempt.directory,
        name=_RUN_SUMMARY_FILENAME,
        descriptor=attempt.summary_descriptor,
        expected_size=len(artifacts.run_summary_bytes),
    )
    _verify_artifact_bytes(
        artifacts,
        report.raw,
        summary.raw,
        context=context,
    )
    return _VerifiedGeneration(
        directory=attempt.directory,
        report=report,
        summary=summary,
    )


def _open_artifact_for_verification(
    directory: _OwnedDirectory,
    name: str,
    expected_size: int,
    *,
    owner: _DescriptorOwner,
) -> _VerifiedArtifactEntry:
    try:
        descriptor = owner.register(
            _os.open(
                name,
                _OPEN_READ_FLAGS,
                dir_fd=directory.descriptor,
            )
        )
    except OSError:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    return _read_verified_artifact_entry(
        directory,
        name=name,
        descriptor=descriptor,
        expected_size=expected_size,
    )


def _verify_generation_closing(
    generation: _VerifiedGeneration,
) -> None:
    directory = generation.directory
    before = _directory_closing_witness(directory.descriptor)
    _verify_owned_directory(directory)
    if _directory_inventory(directory.descriptor) != tuple(
        sorted(_ARTIFACT_FILENAMES)
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    for entry in (generation.report, generation.summary):
        _require_named_artifact_binding(
            directory,
            name=entry.name,
            descriptor=entry.descriptor,
            expected_size=entry.witness.size,
            expected_witness=entry.witness,
        )
    if _directory_inventory(directory.descriptor) != tuple(
        sorted(_ARTIFACT_FILENAMES)
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    _verify_owned_directory(directory)
    after = _directory_closing_witness(directory.descriptor)
    if before != after:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")


def _verify_public_generation_route(
    generation: _VerifiedGeneration,
    *,
    chain: tuple[_DirectoryChainEntry, ...],
    reports: _OwnedDirectory,
) -> None:
    if (
        not chain
        or reports.name != _REPORTS_DIRECTORY_NAME
        or reports.parent_descriptor != chain[-1].descriptor
        or generation.directory.parent_descriptor != reports.descriptor
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    _verify_directory_chain(chain)
    _verify_owned_directory(reports)
    _verify_owned_directory(generation.directory)


def _verify_public_generation_closing(
    generation: _VerifiedGeneration,
    *,
    chain: tuple[_DirectoryChainEntry, ...],
    reports: _OwnedDirectory,
) -> None:
    _verify_public_generation_route(
        generation,
        chain=chain,
        reports=reports,
    )
    _verify_generation_closing(generation)
    _verify_public_generation_route(
        generation,
        chain=chain,
        reports=reports,
    )


def _open_final_directory(
    reports: _OwnedDirectory,
    final_name: str,
    *,
    owner: _DescriptorOwner,
) -> _OwnedDirectory | None:
    try:
        descriptor = owner.register(
            _os.open(
                final_name,
                _OPEN_DIRECTORY_FLAGS,
                dir_fd=reports.descriptor,
            )
        )
    except FileNotFoundError:
        return None
    except OSError:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    try:
        descriptor_status = _os.fstat(descriptor)
        entry_status = _os.stat(
            final_name,
            dir_fd=reports.descriptor,
            follow_symlinks=False,
        )
    except OSError:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    witness = _require_directory_status(
        descriptor_status,
        exact_mode=_DIRECTORY_MODE,
    )
    _require_directory_status(
        entry_status,
        expected=witness,
        exact_mode=_DIRECTORY_MODE,
    )
    return _OwnedDirectory(
        descriptor,
        witness,
        final_name,
        reports.descriptor,
    )


def _verify_final_generation(
    reports: _OwnedDirectory,
    artifacts: _PreparedArtifacts,
    *,
    context: _CONTEXT_TYPE,
    owner: _DescriptorOwner,
    expected_directory_witness: _DirectoryWitness | None,
) -> _VerifiedGeneration:
    final = _open_final_directory(
        reports,
        artifacts.report_identity_sha256,
        owner=owner,
    )
    if final is None:
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    if (
        expected_directory_witness is not None
        and final.witness != expected_directory_witness
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    if _directory_inventory(final.descriptor) != tuple(
        sorted(_ARTIFACT_FILENAMES)
    ):
        _fail("WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    report_raw = _open_artifact_for_verification(
        final,
        _REPORT_FILENAME,
        len(artifacts.report_bytes),
        owner=owner,
    )
    summary_raw = _open_artifact_for_verification(
        final,
        _RUN_SUMMARY_FILENAME,
        len(artifacts.run_summary_bytes),
        owner=owner,
    )
    _verify_artifact_bytes(
        artifacts,
        report_raw.raw,
        summary_raw.raw,
        context=context,
    )
    return _VerifiedGeneration(
        directory=final,
        report=report_raw,
        summary=summary_raw,
    )


def _rename_attempt_to_final_noreplace(
    source_parent_descriptor: int,
    source_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
) -> bool:
    try:
        library = _ctypes.CDLL(None, use_errno=True)
        rename = library.renameat2
    except (OSError, AttributeError):
        raise _RenamePrimitiveUnavailable from None
    rename.argtypes = (
        _ctypes.c_int,
        _ctypes.c_char_p,
        _ctypes.c_int,
        _ctypes.c_char_p,
        _ctypes.c_uint,
    )
    rename.restype = _ctypes.c_int
    result = rename(
        source_parent_descriptor,
        _os.fsencode(source_name),
        destination_parent_descriptor,
        _os.fsencode(destination_name),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return True
    error = _ctypes.get_errno()
    if error == _errno.EEXIST:
        return False
    if error in {
        _errno.ENOSYS,
        _errno.EINVAL,
        getattr(_errno, "EOPNOTSUPP", _errno.ENOTSUP),
        _errno.ENOTSUP,
    }:
        raise _RenamePrimitiveUnavailable
    raise _RenameDeterministicFailure(error)


def _publish_prepared(
    artifacts: _PreparedArtifacts,
    *,
    context: _CONTEXT_TYPE,
    output_root: _Path,
) -> _WS01dResult:
    owner = _DescriptorOwner()
    phase = _PublicationPhase.PRECOMMIT
    result: _WS01dResult | None = None
    try:
        output_descriptor, chain = _open_output_root_chain(
            output_root,
            owner=owner,
        )
        output_witness = _directory_witness(output_descriptor)
        attempts, attempts_created = _open_or_create_fixed_directory(
            output_descriptor,
            _ATTEMPTS_DIRECTORY_NAME,
            owner=owner,
            parent_device=output_witness.device,
        )
        reports, reports_created = _open_or_create_fixed_directory(
            output_descriptor,
            _REPORTS_DIRECTORY_NAME,
            owner=owner,
            parent_device=output_witness.device,
        )
        if attempts_created or reports_created:
            _fsync_directory(output_descriptor)
        _verify_directory_chain(chain)
        _verify_owned_directory(attempts)
        _verify_owned_directory(reports)

        existing: _OwnedDirectory | None = None
        try:
            existing = _open_final_directory(
                reports,
                artifacts.report_identity_sha256,
                owner=owner,
            )
        except _WS01dFailure:
            result = _result_failure("WS01_BR_PUBLICATION_CONFLICT")
        if existing is not None and result is None:
            try:
                _verify_final_generation(
                    reports,
                    artifacts,
                    context=context,
                    owner=owner,
                    expected_directory_witness=existing.witness,
                )
            except _WS01dFailure:
                result = _result_failure("WS01_BR_PUBLICATION_CONFLICT")
            else:
                phase = _PublicationPhase.NAMESPACE_COMMITTED_NOT_DURABLE
                _fsync_directory(reports.descriptor)
                verified = _verify_final_generation(
                    reports,
                    artifacts,
                    context=context,
                    owner=owner,
                    expected_directory_witness=existing.witness,
                )
                _verify_directory_chain(chain)
                _verify_owned_directory(attempts)
                _verify_owned_directory(reports)
                success = _result_success(_new_receipt(artifacts, reused=True))
                if not success.ok:
                    _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
                _verify_public_generation_closing(
                    verified,
                    chain=chain,
                    reports=reports,
                )
                phase = _PublicationPhase.DURABLE_AND_VERIFIED
                result = success

        if result is None:
            attempt_directory = _create_attempt_directory(
                attempts,
                owner=owner,
            )
            report_descriptor = _create_artifact_file(
                attempt_directory,
                _REPORT_FILENAME,
                artifacts.report_bytes,
                owner=owner,
            )
            summary_descriptor = _create_artifact_file(
                attempt_directory,
                _RUN_SUMMARY_FILENAME,
                artifacts.run_summary_bytes,
                owner=owner,
            )
            attempt = _OwnedAttempt(
                attempt_directory,
                attempt_directory.name,
                report_descriptor,
                summary_descriptor,
            )
            staged = _verify_staged_attempt(
                attempt,
                artifacts,
                context=context,
            )
            _verify_generation_closing(staged)
            _fsync_directory(attempt_directory.descriptor)
            _verify_directory_chain(chain)
            _verify_owned_directory(attempts)
            _verify_owned_directory(reports)
            staged = _verify_staged_attempt(
                attempt,
                artifacts,
                context=context,
            )
            _verify_generation_closing(staged)

            try:
                committed = _rename_attempt_to_final_noreplace(
                    attempts.descriptor,
                    attempt.name,
                    reports.descriptor,
                    artifacts.report_identity_sha256,
                )
            except (_RenamePrimitiveUnavailable, _RenameDeterministicFailure):
                _fail("WS01_BR_PUBLICATION_FAILED")
            except Exception:
                phase = _PublicationPhase.NAMESPACE_COMMITTED_NOT_DURABLE
                _fail("WS01_BR_PUBLICATION_AMBIGUOUS")

            if not committed:
                try:
                    _verify_final_generation(
                        reports,
                        artifacts,
                        context=context,
                        owner=owner,
                        expected_directory_witness=None,
                    )
                except _WS01dFailure:
                    _fail("WS01_BR_PUBLICATION_CONFLICT")
                phase = _PublicationPhase.NAMESPACE_COMMITTED_NOT_DURABLE
                _fsync_directory(reports.descriptor)
                verified = _verify_final_generation(
                    reports,
                    artifacts,
                    context=context,
                    owner=owner,
                    expected_directory_witness=None,
                )
                _verify_directory_chain(chain)
                _verify_owned_directory(attempts)
                _verify_owned_directory(reports)
                success = _result_success(_new_receipt(artifacts, reused=True))
                if not success.ok:
                    _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
                _verify_public_generation_closing(
                    verified,
                    chain=chain,
                    reports=reports,
                )
                phase = _PublicationPhase.DURABLE_AND_VERIFIED
                result = success
            else:
                phase = _PublicationPhase.NAMESPACE_COMMITTED_NOT_DURABLE
                _fsync_directory(attempts.descriptor)
                _fsync_directory(reports.descriptor)
                _verify_directory_chain(chain)
                _verify_owned_directory(attempts)
                _verify_owned_directory(reports)
                try:
                    _os.stat(
                        attempt.name,
                        dir_fd=attempts.descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    pass
                except OSError:
                    _fail("WS01_BR_PUBLICATION_AMBIGUOUS")
                else:
                    _fail("WS01_BR_PUBLICATION_AMBIGUOUS")
                verified = _verify_final_generation(
                    reports,
                    artifacts,
                    context=context,
                    owner=owner,
                    expected_directory_witness=attempt.directory.witness,
                )
                _verify_directory_chain(chain)
                _verify_owned_directory(attempts)
                _verify_owned_directory(reports)
                success = _result_success(_new_receipt(artifacts, reused=False))
                if not success.ok:
                    _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
                _verify_public_generation_closing(
                    verified,
                    chain=chain,
                    reports=reports,
                )
                phase = _PublicationPhase.DURABLE_AND_VERIFIED
                result = success
    except _WS01dFailure as failure:
        code = failure.code
        if phase is _PublicationPhase.NAMESPACE_COMMITTED_NOT_DURABLE:
            code = "WS01_BR_PUBLICATION_AMBIGUOUS"
        result = _result_failure(code)
    except Exception:
        code = (
            "WS01_BR_PUBLICATION_AMBIGUOUS"
            if phase is _PublicationPhase.NAMESPACE_COMMITTED_NOT_DURABLE
            else "WS01_BR_INTERNAL_INVARIANT_FAILURE"
        )
        result = _result_failure(code)
    close_failed = owner.close_all()
    if (
        close_failed
        and phase is not _PublicationPhase.DURABLE_AND_VERIFIED
        and (
            result is None
            or result.ok
        )
    ):
        return _result_failure(
            "WS01_BR_PUBLICATION_AMBIGUOUS"
            if phase is _PublicationPhase.NAMESPACE_COMMITTED_NOT_DURABLE
            else "WS01_BR_INTERNAL_INVARIANT_FAILURE"
        )
    return (
        result
        if type(result) is _WS01dResult
        else _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    )


def publish_weekly_shadow_report(
    generation_id: str,
    *,
    raw_response_bytes: bytes,
    output_root: "str | PathLike[str]",
    repository_root: "str | PathLike[str] | None" = None,
) -> _WS01dResult:
    """Construct and atomically publish one explicit report-only generation."""
    try:
        normalized_repository_root = _normalize_repository_root(repository_root)
        normalized_output_root = _normalize_output_root(output_root)
    except _WS01dFailure as failure:
        return _result_failure(failure.code)
    except Exception:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    try:
        downstream = (
            _response_validator._validate_analyst_response_for_downstream(
                generation_id,
                raw_response_bytes=raw_response_bytes,
                repository_root=normalized_repository_root,
            )
        )
    except Exception:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    if type(downstream) is not _DOWNSTREAM_RESULT_TYPE:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    if not downstream.ok:
        return _result_failure(downstream.reason_code)
    if type(downstream.value) is not _CONTEXT_TYPE:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    try:
        artifacts = _prepare_artifacts(downstream.value)
    except _WS01dFailure as failure:
        return _result_failure(failure.code)
    except Exception:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _publish_prepared(
        artifacts,
        context=downstream.value,
        output_root=normalized_output_root,
    )
