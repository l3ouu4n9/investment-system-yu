"""Manual single-file publication of one validated R2F memo report.

The sole completed artifact is the validator-owned canonical envelope. One
unpredictable attempt file is prepared through its retained descriptor and made
visible by Linux ``renameat2(RENAME_NOREPLACE)``. Failed attempts are intentionally
left unconsumed; this module has no runtime consumer or permission effect.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import ctypes
from dataclasses import dataclass
from enum import Enum
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.research.replacement_memo_contract import (
    VALIDATED_MEMO_SCHEMA_VERSION,
    ValidatedMemoEnvelope,
    _validate_generation_memo_at_root_for_tests,
    validate_generation_memo,
)

__all__ = (
    "ReplacementReportError",
    "replacement_report",
)


REPORT_IDENTITY_SCHEMA_VERSION = "r2f_validated_memo_report_identity_v2"
PUBLICATION_PROFILE = "r2f_single_file_validated_memo_report_v1"
SOURCE_GENERATION_PROFILE = "step1_replacement_render_observation_v2"

R2F_ROOT_PARTS = ("artifacts", "current", "step1_research", "r2f_report_only")
REPORTS_DIRECTORY = "reports"
ATTEMPTS_DIRECTORY = "report_attempts"
REPORT_FILENAME_SUFFIX = ".json"
ATTEMPT_FILENAME_PREFIX = ".attempt-"
ATTEMPT_FILENAME_SUFFIX = ".tmp"
RENAME_NOREPLACE = 1

AUTHORITY_MARKERS = {
    "report_only": True,
    "runtime_consumed": False,
    "permission_effect": "NONE",
    "not_authorization": True,
    "order_authorization": False,
    "broker_authorization": False,
}

_GENERATION_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_FINAL_FILENAME_RE = re.compile(r"([0-9a-f]{64})\.json\Z")
_VALIDATED_ENVELOPE_KEYS = frozenset(
    {
        "schema_version",
        "artifact_role",
        *AUTHORITY_MARKERS,
        "source_binding",
        "memo_input",
        "normalized_memo",
        "contract_validation",
    }
)
_SOURCE_BINDING_KEYS = frozenset(
    {
        "generation_profile",
        "generation_identity_schema_version",
        "generation_id",
        "prompt_contract_schema_version",
        "prompt_contract_canonical_sha256",
        "raw_memo_schema_version",
        "replacement_input_manifest_file_sha256",
        "replacement_input_manifest_canonical_sha256",
        "evidence_packet_file_sha256",
        "evidence_packet_canonical_sha256",
        "analyst_memo_prompt_file_sha256",
        "as_of",
    }
)
_MEMO_INPUT_KEYS = frozenset({"byte_size", "file_sha256", "normalized_text_sha256"})
_IDENTITY_KEYS = frozenset(
    {
        "schema_version",
        "publication_profile",
        "source_generation_profile",
        "source_generation_id",
        "validated_envelope_schema_version",
        "validated_envelope_canonical_sha256",
        "prompt_contract_canonical_sha256",
        "raw_memo_file_sha256",
        "normalized_memo_text_sha256",
        "authority_markers",
    }
)


class ReplacementReportError(RuntimeError):
    """Bounded report-publication failure with no source or memo disclosure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


class _RenamePrimitiveUnavailable(RuntimeError):
    pass


class _RenameOutcome(Enum):
    SUCCESS = "rename_returned_success"
    EEXIST = "rename_returned_eexist"


class _PublicationState(Enum):
    RENAME_NOT_CALLED = "rename_not_called"
    RENAME_RETURNED_SUCCESS = "rename_returned_success"
    RENAME_RETURNED_EEXIST = "rename_returned_eexist"
    RENAME_RAISED_AMBIGUOUS_EXCEPTION = "rename_raised_ambiguous_exception"
    POST_RENAME_VERIFICATION_FAILURE = "post_rename_verification_failure"


@dataclass(frozen=True, slots=True)
class _PreparedReport:
    report_id: str
    final_filename: str
    identity: Mapping[str, Any]
    identity_bytes: bytes
    envelope_bytes: bytes
    envelope_canonical_sha256: str


@dataclass(frozen=True, slots=True)
class _RegularFileState:
    device: int
    inode: int
    size: int
    mode: int
    mtime_ns: int
    ctime_ns: int
    link_count: int


@dataclass(frozen=True, slots=True)
class _OwnedAttemptFile:
    name: str
    descriptor: int
    device: int
    inode: int


def replacement_report(generation_id: str) -> dict[str, str]:
    """Validate and manually publish one immutable report-only memo file."""
    return _replacement_report_operation(
        generation_id,
        Path(repo_root()),
        validate_generation_memo,
    )


def _replacement_report_at_root_for_tests(
    generation_id: str,
    repository_root: Path,
) -> dict[str, str]:
    """Private isolated-root seam using the committed one-shot validator."""
    root = Path(repository_root)
    return _replacement_report_operation(
        generation_id,
        root,
        lambda value: _validate_generation_memo_at_root_for_tests(value, root),
    )


def _replacement_report_operation(
    generation_id: str,
    repository_root: Path,
    validator: Callable[[str], ValidatedMemoEnvelope],
) -> dict[str, str]:
    if not _is_sha256(generation_id):
        raise ReplacementReportError("SOURCE_GENERATION_ID_INVALID")

    repository_chain = _open_repository_directory_chain(repository_root)
    try:
        repository_fd = repository_chain[-1][2]
        repository_identity = _directory_identity_checked(
            repository_fd,
            "REPOSITORY_ROOT_INVALID",
        )

        # Validation and every final byte/identity are complete before the
        # publisher creates any attempt-local output entry.
        validated = validator(generation_id)
        _verify_repository_identity(
            repository_chain,
            repository_fd,
            repository_identity,
        )
        prepared = _prepare_report(generation_id, validated)
        relative_report_path = Path(
            *R2F_ROOT_PARTS,
            REPORTS_DIRECTORY,
            prepared.final_filename,
        )
        common_result = {
            "report_id": prepared.report_id,
            "report_path": str(repository_root / relative_report_path),
            "cli_output": f"{prepared.report_id} {relative_report_path.as_posix()}",
        }
        try:
            return _publish_or_verify_single_report(
                repository_fd=repository_fd,
                repository_chain=repository_chain,
                repository_identity=repository_identity,
                prepared=prepared,
                new_result={**common_result, "report_reused": "false"},
                reused_result={**common_result, "report_reused": "true"},
            )
        except ReplacementReportError:
            raise
        except BaseException:
            raise ReplacementReportError("REPORT_PUBLICATION_FAILED") from None
    finally:
        _dispatch_cleanup_noexcept(_close_directory_chain_noexcept, repository_chain)


def _prepare_report(
    generation_id: str,
    validated: ValidatedMemoEnvelope,
) -> _PreparedReport:
    payload = validated.payload
    _validate_envelope_payload(payload, expected_generation_id=generation_id)

    envelope_bytes = validated.canonical_bytes
    if not isinstance(envelope_bytes, bytes):
        raise ReplacementReportError("VALIDATED_ENVELOPE_INVALID")
    envelope_sha256 = _sha256(envelope_bytes)
    if (
        not _is_sha256(validated.canonical_sha256)
        or validated.canonical_sha256 != envelope_sha256
    ):
        raise ReplacementReportError("VALIDATED_ENVELOPE_INVALID")
    parsed = _parse_json_object(envelope_bytes, "VALIDATED_ENVELOPE_INVALID")
    _validate_envelope_payload(parsed, expected_generation_id=generation_id)
    if _canonical_json_bytes(parsed) != envelope_bytes:
        raise ReplacementReportError("VALIDATED_ENVELOPE_INVALID")

    identity = _report_identity_from_envelope(parsed)
    identity_bytes = _canonical_json_bytes(identity)
    report_id = _sha256(identity_bytes)
    final_filename = f"{report_id}{REPORT_FILENAME_SUFFIX}"
    if _FINAL_FILENAME_RE.fullmatch(final_filename) is None:
        raise ReplacementReportError("REPORT_IDENTITY_INVALID")
    return _PreparedReport(
        report_id=report_id,
        final_filename=final_filename,
        identity=identity,
        identity_bytes=identity_bytes,
        envelope_bytes=envelope_bytes,
        envelope_canonical_sha256=validated.canonical_sha256,
    )


def _report_identity_from_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    source_binding = payload["source_binding"]
    memo_input = payload["memo_input"]
    assert isinstance(source_binding, Mapping)
    assert isinstance(memo_input, Mapping)
    identity: dict[str, Any] = {
        "schema_version": REPORT_IDENTITY_SCHEMA_VERSION,
        "publication_profile": PUBLICATION_PROFILE,
        "source_generation_profile": source_binding["generation_profile"],
        "source_generation_id": source_binding["generation_id"],
        "validated_envelope_schema_version": payload["schema_version"],
        "validated_envelope_canonical_sha256": _sha256(_canonical_json_bytes(payload)),
        "prompt_contract_canonical_sha256": source_binding[
            "prompt_contract_canonical_sha256"
        ],
        "raw_memo_file_sha256": memo_input["file_sha256"],
        "normalized_memo_text_sha256": memo_input["normalized_text_sha256"],
        "authority_markers": dict(AUTHORITY_MARKERS),
    }
    _validate_identity(identity)
    return identity


def _publish_or_verify_single_report(
    *,
    repository_fd: int,
    repository_chain: list[tuple[int, str, int]],
    repository_identity: tuple[int, int],
    prepared: _PreparedReport,
    new_result: dict[str, str],
    reused_result: dict[str, str],
) -> dict[str, str]:
    output_chain: list[tuple[int, str, int]] = []
    opened_files: list[int] = []
    owned_attempt: _OwnedAttemptFile | None = None
    attempts_fd: int | None = None
    reports_fd: int | None = None
    try:
        parent_fd = repository_fd
        for component in R2F_ROOT_PARTS:
            child_fd = _open_or_create_directory_at(parent_fd, component)
            output_chain.append((parent_fd, component, child_fd))
            parent_fd = child_fd
        attempts_fd = _open_or_create_directory_at(parent_fd, ATTEMPTS_DIRECTORY)
        output_chain.append((parent_fd, ATTEMPTS_DIRECTORY, attempts_fd))
        reports_fd = _open_or_create_directory_at(parent_fd, REPORTS_DIRECTORY)
        output_chain.append((parent_fd, REPORTS_DIRECTORY, reports_fd))
        _require_same_filesystem(attempts_fd, reports_fd)
        _verify_canonical_output_directories(
            repository_chain,
            output_chain,
            repository_fd,
            repository_identity,
        )

        existing_fd = _open_existing_final_if_present(
            reports_fd,
            prepared.final_filename,
        )
        if existing_fd is not None:
            opened_files.append(existing_fd)
            _verify_existing_final_report(
                repository_chain=repository_chain,
                output_chain=output_chain,
                repository_fd=repository_fd,
                repository_identity=repository_identity,
                reports_fd=reports_fd,
                final_fd=existing_fd,
                prepared=prepared,
            )
            return reused_result

        owned_attempt = _create_owned_attempt_file(attempts_fd)
        _write_and_verify_owned_attempt(attempts_fd, owned_attempt, prepared)
        _fsync_directory(attempts_fd, "REPORT_ATTEMPTS_DURABILITY_FAILURE")
        _verify_canonical_output_directories(
            repository_chain,
            output_chain,
            repository_fd,
            repository_identity,
        )
        return _rename_and_verify_publication(
            repository_chain=repository_chain,
            output_chain=output_chain,
            repository_fd=repository_fd,
            repository_identity=repository_identity,
            attempts_fd=attempts_fd,
            reports_fd=reports_fd,
            owned_attempt=owned_attempt,
            prepared=prepared,
            new_result=new_result,
            reused_result=reused_result,
        )
    finally:
        if owned_attempt is not None:
            _dispatch_cleanup_noexcept(os.close, owned_attempt.descriptor)
        _dispatch_cleanup_noexcept(_close_descriptors_noexcept, opened_files)
        _dispatch_cleanup_noexcept(_close_directory_chain_noexcept, output_chain)


def _rename_and_verify_publication(
    *,
    repository_chain: list[tuple[int, str, int]],
    output_chain: list[tuple[int, str, int]],
    repository_fd: int,
    repository_identity: tuple[int, int],
    attempts_fd: int,
    reports_fd: int,
    owned_attempt: _OwnedAttemptFile,
    prepared: _PreparedReport,
    new_result: dict[str, str],
    reused_result: dict[str, str],
) -> dict[str, str]:
    state = _PublicationState.RENAME_NOT_CALLED
    try:
        outcome = _rename_attempt_to_final_noreplace(
            attempts_fd,
            owned_attempt.name,
            reports_fd,
            prepared.final_filename,
        )
    except BaseException as error:
        state = _PublicationState.RENAME_RAISED_AMBIGUOUS_EXCEPTION
        return _resolve_ambiguous_rename_exception(
            repository_chain=repository_chain,
            output_chain=output_chain,
            repository_fd=repository_fd,
            repository_identity=repository_identity,
            attempts_fd=attempts_fd,
            reports_fd=reports_fd,
            owned_attempt=owned_attempt,
            prepared=prepared,
            new_result=new_result,
            state=state,
            original_error=error,
        )

    if outcome is _RenameOutcome.SUCCESS:
        state = _PublicationState.RENAME_RETURNED_SUCCESS
        try:
            _verify_owned_final_after_rename(
                repository_chain=repository_chain,
                output_chain=output_chain,
                repository_fd=repository_fd,
                repository_identity=repository_identity,
                attempts_fd=attempts_fd,
                reports_fd=reports_fd,
                owned_attempt=owned_attempt,
                prepared=prepared,
            )
        except BaseException:
            state = _PublicationState.POST_RENAME_VERIFICATION_FAILURE
            raise ReplacementReportError(state.value.upper()) from None
        return new_result

    if outcome is _RenameOutcome.EEXIST:
        state = _PublicationState.RENAME_RETURNED_EEXIST
        return _verify_genuine_eexist_reuse(
            repository_chain=repository_chain,
            output_chain=output_chain,
            repository_fd=repository_fd,
            repository_identity=repository_identity,
            attempts_fd=attempts_fd,
            reports_fd=reports_fd,
            owned_attempt=owned_attempt,
            prepared=prepared,
            reused_result=reused_result,
            state=state,
        )
    raise ReplacementReportError("RENAME_OUTCOME_INVALID")


def _resolve_ambiguous_rename_exception(
    *,
    repository_chain: list[tuple[int, str, int]],
    output_chain: list[tuple[int, str, int]],
    repository_fd: int,
    repository_identity: tuple[int, int],
    attempts_fd: int,
    reports_fd: int,
    owned_attempt: _OwnedAttemptFile,
    prepared: _PreparedReport,
    new_result: dict[str, str],
    state: _PublicationState,
    original_error: BaseException,
) -> dict[str, str]:
    attempt_entry = _entry_state_if_present(attempts_fd, owned_attempt.name)
    final_entry = _entry_state_if_present(reports_fd, prepared.final_filename)
    if (
        attempt_entry is None
        and final_entry is not None
        and _state_matches_owned_attempt(final_entry, owned_attempt)
    ):
        try:
            _verify_owned_final_after_rename(
                repository_chain=repository_chain,
                output_chain=output_chain,
                repository_fd=repository_fd,
                repository_identity=repository_identity,
                attempts_fd=attempts_fd,
                reports_fd=reports_fd,
                owned_attempt=owned_attempt,
                prepared=prepared,
            )
        except BaseException:
            raise ReplacementReportError("AMBIGUOUS_RENAME_VERIFICATION_FAILED") from None
        return new_result
    if isinstance(original_error, _RenamePrimitiveUnavailable):
        raise ReplacementReportError("REQUIRED_NOREPLACE_RENAME_UNAVAILABLE") from None
    raise ReplacementReportError(state.value.upper()) from None


def _verify_genuine_eexist_reuse(
    *,
    repository_chain: list[tuple[int, str, int]],
    output_chain: list[tuple[int, str, int]],
    repository_fd: int,
    repository_identity: tuple[int, int],
    attempts_fd: int,
    reports_fd: int,
    owned_attempt: _OwnedAttemptFile,
    prepared: _PreparedReport,
    reused_result: dict[str, str],
    state: _PublicationState,
) -> dict[str, str]:
    _verify_owned_attempt_ready(attempts_fd, owned_attempt, prepared)
    final_fd = _open_existing_final_if_present(reports_fd, prepared.final_filename)
    if final_fd is None:
        raise ReplacementReportError(state.value.upper())
    try:
        _verify_existing_final_report(
            repository_chain=repository_chain,
            output_chain=output_chain,
            repository_fd=repository_fd,
            repository_identity=repository_identity,
            reports_fd=reports_fd,
            final_fd=final_fd,
            prepared=prepared,
        )
        _verify_owned_attempt_ready(attempts_fd, owned_attempt, prepared)
        # Recheck the complete existing final after re-proving that the genuine
        # EEXIST left this attempt name on its original inode.
        _verify_existing_final_report(
            repository_chain=repository_chain,
            output_chain=output_chain,
            repository_fd=repository_fd,
            repository_identity=repository_identity,
            reports_fd=reports_fd,
            final_fd=final_fd,
            prepared=prepared,
        )
    except BaseException:
        raise ReplacementReportError("EXISTING_REPORT_INVALID") from None
    finally:
        _dispatch_cleanup_noexcept(os.close, final_fd)
    return reused_result


def _create_owned_attempt_file(attempts_fd: int) -> _OwnedAttemptFile:
    # O_RDWR is required because exact readback is performed through this same
    # descriptor.  The O_EXCL open is both creation and ownership acquisition.
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    for _attempt in range(16):
        name = (
            f"{ATTEMPT_FILENAME_PREFIX}{secrets.token_hex(16)}"
            f"{ATTEMPT_FILENAME_SUFFIX}"
        )
        if _FINAL_FILENAME_RE.fullmatch(name) is not None:
            raise ReplacementReportError("REPORT_ATTEMPT_NAME_INVALID")
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=attempts_fd)
        except FileExistsError:
            continue
        except OSError:
            raise ReplacementReportError("REPORT_ATTEMPT_CREATE_FAILED") from None

        # From this point forward the descriptor is the only ownership token.
        # Failure deliberately leaves the attempt entry rather than performing
        # a racy pathname deletion.
        try:
            opened = _regular_file_state(os.fstat(descriptor), require_single_link=True)
            if stat.S_IMODE(opened.mode) != 0o600:
                raise ReplacementReportError("REPORT_ATTEMPT_MODE_INVALID")
            owned = _OwnedAttemptFile(
                name=name,
                descriptor=descriptor,
                device=opened.device,
                inode=opened.inode,
            )
            _verify_owned_attempt_entry(attempts_fd, owned, expected_state=opened)
            return owned
        except BaseException:
            _dispatch_cleanup_noexcept(os.close, descriptor)
            raise ReplacementReportError("REPORT_ATTEMPT_CREATE_FAILED") from None
    raise ReplacementReportError("REPORT_ATTEMPT_CREATE_FAILED")


def _write_and_verify_owned_attempt(
    attempts_fd: int,
    owned_attempt: _OwnedAttemptFile,
    prepared: _PreparedReport,
) -> None:
    descriptor = owned_attempt.descriptor
    try:
        offset = 0
        while offset < len(prepared.envelope_bytes):
            written = os.write(descriptor, prepared.envelope_bytes[offset:])
            if written <= 0:
                raise ReplacementReportError("REPORT_ATTEMPT_WRITE_INCOMPLETE")
            offset += written
        os.fsync(descriptor)
    except ReplacementReportError:
        raise
    except BaseException:
        raise ReplacementReportError("REPORT_ATTEMPT_WRITE_FAILED") from None

    _verify_owned_attempt_ready(attempts_fd, owned_attempt, prepared)


def _verify_owned_attempt_ready(
    attempts_fd: int,
    owned_attempt: _OwnedAttemptFile,
    prepared: _PreparedReport,
) -> None:
    observed, state = _read_stable_descriptor(
        owned_attempt.descriptor,
        require_single_link=True,
    )
    if observed != prepared.envelope_bytes or _sha256(observed) != (
        prepared.envelope_canonical_sha256
    ):
        raise ReplacementReportError("REPORT_ATTEMPT_CONTENT_MISMATCH")
    _verify_envelope_bytes(observed, prepared)
    _verify_owned_attempt_entry(attempts_fd, owned_attempt, expected_state=state)


def _rename_attempt_to_final_noreplace(
    attempts_fd: int,
    attempt_name: str,
    reports_fd: int,
    final_name: str,
) -> _RenameOutcome:
    """Invoke Linux renameat2 with RENAME_NOREPLACE and no fallback."""
    if sys.platform != "linux":
        raise _RenamePrimitiveUnavailable
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError):
        raise _RenamePrimitiveUnavailable from None

    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = renameat2(
        attempts_fd,
        os.fsencode(attempt_name),
        reports_fd,
        os.fsencode(final_name),
        RENAME_NOREPLACE,
    )
    if result == 0:
        return _RenameOutcome.SUCCESS

    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        return _RenameOutcome.EEXIST
    unsupported = {
        errno.ENOSYS,
        errno.EINVAL,
        errno.EOPNOTSUPP,
        getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
    }
    if error_number in unsupported:
        raise _RenamePrimitiveUnavailable
    if error_number == errno.EXDEV:
        raise ReplacementReportError("REPORT_DIRECTORIES_FILESYSTEM_MISMATCH")
    raise OSError(error_number, "required no-replace rename failed")


def _verify_owned_final_after_rename(
    *,
    repository_chain: list[tuple[int, str, int]],
    output_chain: list[tuple[int, str, int]],
    repository_fd: int,
    repository_identity: tuple[int, int],
    attempts_fd: int,
    reports_fd: int,
    owned_attempt: _OwnedAttemptFile,
    prepared: _PreparedReport,
) -> None:
    # A successful no-replace rename removes the attempt name and leaves one
    # link at the final name.  Verification remains on the original owned fd.
    _fsync_directory(attempts_fd, "REPORT_ATTEMPTS_DURABILITY_FAILURE")
    _fsync_directory(reports_fd, "REPORT_FINAL_DURABILITY_FAILURE")
    _verify_canonical_output_directories(
        repository_chain,
        output_chain,
        repository_fd,
        repository_identity,
    )
    if _entry_state_if_present(attempts_fd, owned_attempt.name) is not None:
        raise ReplacementReportError("REPORT_ATTEMPT_STILL_PRESENT")
    entry = _required_regular_entry_state(reports_fd, prepared.final_filename)
    if not _state_matches_owned_attempt(entry, owned_attempt) or entry.link_count != 1:
        raise ReplacementReportError("REPORT_FINAL_IDENTITY_MISMATCH")

    observed, state = _read_stable_descriptor(
        owned_attempt.descriptor,
        require_single_link=True,
    )
    _verify_envelope_bytes(observed, prepared)
    entry_after = _required_regular_entry_state(reports_fd, prepared.final_filename)
    if (
        not _state_matches_owned_attempt(state, owned_attempt)
        or not _state_matches_owned_attempt(entry_after, owned_attempt)
        or entry != state
        or state != entry_after
    ):
        raise ReplacementReportError("REPORT_FINAL_IDENTITY_MISMATCH")

    # Canonical-path verification is immediately followed by final-name and
    # attempt-name verification so success refers to the retained namespace.
    _verify_canonical_output_directories(
        repository_chain,
        output_chain,
        repository_fd,
        repository_identity,
    )
    final_entry = _required_regular_entry_state(reports_fd, prepared.final_filename)
    if final_entry != state or not _state_matches_owned_attempt(
        final_entry,
        owned_attempt,
    ):
        raise ReplacementReportError("REPORT_FINAL_IDENTITY_MISMATCH")
    if _entry_state_if_present(attempts_fd, owned_attempt.name) is not None:
        raise ReplacementReportError("REPORT_ATTEMPT_STILL_PRESENT")
    final_entry = _required_regular_entry_state(reports_fd, prepared.final_filename)
    if final_entry != state:
        raise ReplacementReportError("REPORT_FINAL_IDENTITY_MISMATCH")


def _open_existing_final_if_present(reports_fd: int, final_name: str) -> int | None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        return os.open(final_name, flags, dir_fd=reports_fd)
    except FileNotFoundError:
        return None
    except OSError:
        raise ReplacementReportError("REPORT_FILE_OPEN_FAILED") from None


def _verify_existing_final_report(
    *,
    repository_chain: list[tuple[int, str, int]],
    output_chain: list[tuple[int, str, int]],
    repository_fd: int,
    repository_identity: tuple[int, int],
    reports_fd: int,
    final_fd: int,
    prepared: _PreparedReport,
) -> None:
    _verify_canonical_output_directories(
        repository_chain,
        output_chain,
        repository_fd,
        repository_identity,
    )
    opened = _regular_file_state(os.fstat(final_fd), require_single_link=True)
    entry = _required_regular_entry_state(reports_fd, prepared.final_filename)
    if (opened.device, opened.inode) != (entry.device, entry.inode):
        raise ReplacementReportError("REPORT_FILE_IDENTITY_CHANGED")
    observed, state = _read_stable_descriptor(final_fd, require_single_link=True)
    if observed != prepared.envelope_bytes:
        raise ReplacementReportError("EXISTING_REPORT_MISMATCH")
    _verify_envelope_bytes(observed, prepared)
    entry_after = _required_regular_entry_state(reports_fd, prepared.final_filename)
    if (
        (entry_after.device, entry_after.inode) != (state.device, state.inode)
        or state != opened
    ):
        raise ReplacementReportError("REPORT_FILE_IDENTITY_CHANGED")
    _verify_canonical_output_directories(
        repository_chain,
        output_chain,
        repository_fd,
        repository_identity,
    )
    entry_final = _required_regular_entry_state(reports_fd, prepared.final_filename)
    if entry_final != state:
        raise ReplacementReportError("REPORT_FILE_IDENTITY_CHANGED")


def _verify_envelope_bytes(value: bytes, prepared: _PreparedReport) -> None:
    if value != prepared.envelope_bytes or _sha256(value) != prepared.envelope_canonical_sha256:
        raise ReplacementReportError("VALIDATED_ENVELOPE_MISMATCH")
    payload = _parse_json_object(value, "VALIDATED_ENVELOPE_MISMATCH")
    _validate_envelope_payload(
        payload,
        expected_generation_id=prepared.identity["source_generation_id"],
    )
    if _canonical_json_bytes(payload) != value:
        raise ReplacementReportError("VALIDATED_ENVELOPE_MISMATCH")
    identity = _report_identity_from_envelope(payload)
    if identity != prepared.identity:
        raise ReplacementReportError("REPORT_IDENTITY_MISMATCH")
    identity_bytes = _canonical_json_bytes(identity)
    if identity_bytes != prepared.identity_bytes or _sha256(identity_bytes) != prepared.report_id:
        raise ReplacementReportError("REPORT_IDENTITY_MISMATCH")
    if prepared.final_filename != f"{prepared.report_id}{REPORT_FILENAME_SUFFIX}":
        raise ReplacementReportError("REPORT_IDENTITY_MISMATCH")


def _validate_envelope_payload(
    payload: Any,
    *,
    expected_generation_id: str,
) -> None:
    if not isinstance(payload, Mapping) or set(payload) != _VALIDATED_ENVELOPE_KEYS:
        raise ReplacementReportError("VALIDATED_ENVELOPE_INVALID")
    if (
        payload.get("schema_version") != VALIDATED_MEMO_SCHEMA_VERSION
        or payload.get("artifact_role") != "NON_AUTHORITATIVE_RESEARCH_OBSERVATION"
        or payload.get("contract_validation") != "VALID"
        or any(payload.get(key) != value for key, value in AUTHORITY_MARKERS.items())
    ):
        raise ReplacementReportError("VALIDATED_ENVELOPE_INVALID")
    source = payload.get("source_binding")
    memo_input = payload.get("memo_input")
    if not isinstance(source, Mapping) or set(source) != _SOURCE_BINDING_KEYS:
        raise ReplacementReportError("VALIDATED_ENVELOPE_INVALID")
    if not isinstance(memo_input, Mapping) or set(memo_input) != _MEMO_INPUT_KEYS:
        raise ReplacementReportError("VALIDATED_ENVELOPE_INVALID")
    if (
        source.get("generation_profile") != SOURCE_GENERATION_PROFILE
        or source.get("generation_id") != expected_generation_id
        or not _is_sha256(source.get("prompt_contract_canonical_sha256"))
        or not _is_sha256(memo_input.get("file_sha256"))
        or not _is_sha256(memo_input.get("normalized_text_sha256"))
    ):
        raise ReplacementReportError("VALIDATED_ENVELOPE_INVALID")


def _validate_identity(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != _IDENTITY_KEYS:
        raise ReplacementReportError("REPORT_IDENTITY_INVALID")
    if (
        value.get("schema_version") != REPORT_IDENTITY_SCHEMA_VERSION
        or value.get("publication_profile") != PUBLICATION_PROFILE
        or value.get("source_generation_profile") != SOURCE_GENERATION_PROFILE
        or not _is_sha256(value.get("source_generation_id"))
        or value.get("validated_envelope_schema_version") != VALIDATED_MEMO_SCHEMA_VERSION
        or not _is_sha256(value.get("validated_envelope_canonical_sha256"))
        or not _is_sha256(value.get("prompt_contract_canonical_sha256"))
        or not _is_sha256(value.get("raw_memo_file_sha256"))
        or not _is_sha256(value.get("normalized_memo_text_sha256"))
        or value.get("authority_markers") != AUTHORITY_MARKERS
    ):
        raise ReplacementReportError("REPORT_IDENTITY_INVALID")


def _verify_owned_attempt_entry(
    attempts_fd: int,
    owned_attempt: _OwnedAttemptFile,
    *,
    expected_state: _RegularFileState,
) -> None:
    entry = _required_regular_entry_state(attempts_fd, owned_attempt.name)
    opened = _regular_file_state(
        os.fstat(owned_attempt.descriptor),
        require_single_link=True,
    )
    if (
        not _state_matches_owned_attempt(entry, owned_attempt)
        or not _state_matches_owned_attempt(opened, owned_attempt)
        or entry != opened
        or opened != expected_state
        or stat.S_IMODE(opened.mode) != 0o600
    ):
        raise ReplacementReportError("REPORT_ATTEMPT_IDENTITY_CHANGED")


def _entry_state_if_present(
    directory_fd: int,
    filename: str,
) -> _RegularFileState | None:
    try:
        value = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise ReplacementReportError("REPORT_FILE_OPEN_FAILED") from None
    return _regular_file_state(value, require_single_link=False)


def _required_regular_entry_state(directory_fd: int, filename: str) -> _RegularFileState:
    value = _entry_state_if_present(directory_fd, filename)
    if value is None:
        raise ReplacementReportError("REPORT_FILE_MISSING")
    return value


def _state_matches_owned_attempt(
    value: _RegularFileState,
    owned_attempt: _OwnedAttemptFile,
) -> bool:
    return (value.device, value.inode) == (owned_attempt.device, owned_attempt.inode)


def _read_stable_descriptor(
    descriptor: int,
    *,
    require_single_link: bool,
) -> tuple[bytes, _RegularFileState]:
    before = _regular_file_state(
        os.fstat(descriptor),
        require_single_link=require_single_link,
    )
    first = _read_all_descriptor_bytes(descriptor)
    middle = _regular_file_state(
        os.fstat(descriptor),
        require_single_link=require_single_link,
    )
    second = _read_all_descriptor_bytes(descriptor)
    after = _regular_file_state(
        os.fstat(descriptor),
        require_single_link=require_single_link,
    )
    if before != middle or middle != after or first != second or len(first) != before.size:
        raise ReplacementReportError("REPORT_FILE_UNSTABLE")
    return first, after


def _read_all_descriptor_bytes(descriptor: int) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > 1_048_576:
                raise ReplacementReportError("REPORT_FILE_TOO_LARGE")
            chunks.append(chunk)
    except ReplacementReportError:
        raise
    except OSError:
        raise ReplacementReportError("REPORT_FILE_READ_FAILED") from None


def _regular_file_state(
    value: os.stat_result,
    *,
    require_single_link: bool,
) -> _RegularFileState:
    if not stat.S_ISREG(value.st_mode) or value.st_nlink < 1:
        raise ReplacementReportError("REPORT_FILE_NOT_REGULAR")
    if require_single_link and value.st_nlink != 1:
        raise ReplacementReportError("REPORT_FILE_LINK_COUNT_INVALID")
    return _RegularFileState(
        device=value.st_dev,
        inode=value.st_ino,
        size=value.st_size,
        mode=value.st_mode,
        mtime_ns=value.st_mtime_ns,
        ctime_ns=value.st_ctime_ns,
        link_count=value.st_nlink,
    )


def _open_repository_directory_chain(root: Path) -> list[tuple[int, str, int]]:
    _require_descriptor_primitives()
    if not root.is_absolute():
        raise ReplacementReportError("REPOSITORY_ROOT_INVALID")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        filesystem_fd = os.open("/", flags)
    except OSError:
        raise ReplacementReportError("REPOSITORY_ROOT_INVALID") from None
    chain: list[tuple[int, str, int]] = [(-1, "/", filesystem_fd)]
    parent_fd = filesystem_fd
    try:
        for component in root.parts[1:]:
            child_fd = os.open(component, flags, dir_fd=parent_fd)
            if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                _dispatch_cleanup_noexcept(os.close, child_fd)
                raise ReplacementReportError("REPOSITORY_ROOT_INVALID")
            chain.append((parent_fd, component, child_fd))
            parent_fd = child_fd
        return chain
    except ReplacementReportError:
        _dispatch_cleanup_noexcept(_close_directory_chain_noexcept, chain)
        raise
    except OSError:
        _dispatch_cleanup_noexcept(_close_directory_chain_noexcept, chain)
        raise ReplacementReportError("REPOSITORY_ROOT_INVALID") from None


def _open_or_create_directory_at(parent_fd: int, name: str) -> int:
    created = False
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    except OSError:
        raise ReplacementReportError("REPORT_ROOT_INVALID") from None
    if created:
        _fsync_directory(parent_fd, "REPORT_PARENT_DURABILITY_FAILURE")
    return _open_directory_at(parent_fd, name)


def _open_directory_at(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError:
        raise ReplacementReportError("REPORT_DIRECTORY_OPEN_FAILED") from None
    try:
        opened = os.fstat(descriptor)
    except OSError:
        _dispatch_cleanup_noexcept(os.close, descriptor)
        raise ReplacementReportError("REPORT_DIRECTORY_OPEN_FAILED") from None
    if not stat.S_ISDIR(opened.st_mode):
        _dispatch_cleanup_noexcept(os.close, descriptor)
        raise ReplacementReportError("REPORT_DIRECTORY_OPEN_FAILED")
    return descriptor


def _verify_canonical_output_directories(
    repository_chain: list[tuple[int, str, int]],
    output_chain: list[tuple[int, str, int]],
    repository_fd: int,
    repository_identity: tuple[int, int],
) -> None:
    _verify_repository_identity(repository_chain, repository_fd, repository_identity)
    _verify_directory_chain(output_chain, "REPORT_DIRECTORY_IDENTITY_CHANGED")


def _require_same_filesystem(attempts_fd: int, reports_fd: int) -> None:
    attempts_identity = _directory_identity_checked(
        attempts_fd,
        "REPORT_ATTEMPTS_DIRECTORY_INVALID",
    )
    reports_identity = _directory_identity_checked(
        reports_fd,
        "REPORTS_DIRECTORY_INVALID",
    )
    if attempts_identity[0] != reports_identity[0]:
        raise ReplacementReportError("REPORT_DIRECTORIES_FILESYSTEM_MISMATCH")


def _verify_repository_identity(
    repository_chain: list[tuple[int, str, int]],
    repository_fd: int,
    expected: tuple[int, int],
) -> None:
    _verify_directory_chain(repository_chain, "REPOSITORY_IDENTITY_CHANGED")
    observed = _directory_identity_checked(repository_fd, "REPOSITORY_IDENTITY_CHANGED")
    if observed != expected:
        raise ReplacementReportError("REPOSITORY_IDENTITY_CHANGED")


def _verify_directory_chain(
    chain: list[tuple[int, str, int]],
    error_code: str,
) -> None:
    for parent_fd, name, child_fd in chain:
        if parent_fd < 0:
            continue
        try:
            entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            opened = os.fstat(child_fd)
        except OSError:
            raise ReplacementReportError(error_code) from None
        if (
            not stat.S_ISDIR(entry.st_mode)
            or not stat.S_ISDIR(opened.st_mode)
            or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ReplacementReportError(error_code)


def _directory_identity_checked(descriptor: int, error_code: str) -> tuple[int, int]:
    try:
        value = os.fstat(descriptor)
    except OSError:
        raise ReplacementReportError(error_code) from None
    if not stat.S_ISDIR(value.st_mode):
        raise ReplacementReportError(error_code)
    return (value.st_dev, value.st_ino)


def _fsync_directory(directory_fd: int, error_code: str) -> None:
    try:
        os.fsync(directory_fd)
    except OSError:
        raise ReplacementReportError(error_code) from None


def _close_descriptors_noexcept(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except BaseException:
            continue


def _close_directory_chain_noexcept(chain: list[tuple[int, str, int]]) -> None:
    closed: set[int] = set()
    for _parent_fd, _name, child_fd in reversed(chain):
        if child_fd in closed:
            continue
        closed.add(child_fd)
        try:
            os.close(child_fd)
        except BaseException:
            continue


def _dispatch_cleanup_noexcept(action: Callable[..., Any], *args: Any) -> None:
    try:
        action(*args)
    except BaseException:
        return


def _require_descriptor_primitives() -> None:
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")
    if any(not hasattr(os, flag) for flag in required_flags):
        raise ReplacementReportError("REQUIRED_NOFOLLOW_PRIMITIVES_UNAVAILABLE")
    required_dir_fd = (os.open, os.mkdir, os.stat)
    if any(function not in os.supports_dir_fd for function in required_dir_fd):
        raise ReplacementReportError("REQUIRED_DIRFD_PRIMITIVES_UNAVAILABLE")


def _parse_json_object(value: bytes, error_code: str) -> dict[str, Any]:
    try:
        parsed = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ReplacementReportError(error_code) from None
    if not isinstance(parsed, dict):
        raise ReplacementReportError(error_code)
    return parsed


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ValueError


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _GENERATION_ID_RE.fullmatch(value) is not None
