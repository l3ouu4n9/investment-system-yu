"""CLI: explicit operator publication of one manually obtained WS01 response.

The operator renders the deterministic grounded prompt, hands it to an LLM by
hand, saves the exact raw response bytes to a file, and then invokes this
command once.  This module never contacts a model, never chooses a provider,
never submits a prompt, never retrieves or polls for a response, never
retries, and never schedules anything: the manual handoff is mandatory.

It owns exactly one selector -- the operator's raw-response file -- which it
authenticates and reads as one complete stable byte sequence.  Those exact
bytes are handed to the WS01d public report-only publication API exactly once.
WS01d remains the sole owner of validation, artifact construction, identities,
atomic publication, reuse, conflict, and ambiguity behaviour.  This command
creates no pointer, state, permission, gate, portfolio, order, broker, or
execution authority.
"""

from __future__ import annotations

del annotations

import argparse as _argparse
from collections.abc import Sequence as _Sequence
import os as _os
import re as _re
import stat as _stat
import sys as _sys

from investment_orchestrator.observability import (
    weekly_shadow_01_report_publisher as _report_publisher,
)


__all__ = ("build_parser", "main")


# This command depends on exactly one WS01d public operation.  Binding the
# depended-upon surface at import time fails closed if that contract ever
# widens, and it keeps the dependency an ordinary static module-object use.
_EXPECTED_PUBLISHER_SURFACE = ("publish_weekly_shadow_report",)
if _report_publisher.__all__ != _EXPECTED_PUBLISHER_SURFACE:
    raise ImportError("unexpected WS01d public surface")


# The committed WS01 resource profile bounds a raw analyst response at
# 131072 bytes.  This command must not import the WS01 contracts module, so
# the bound is restated here as a private constant and pinned to the committed
# contract value by the focused CLI test.
_MAXIMUM_RAW_RESPONSE_BYTES = 131_072

_EXIT_SUCCESS = 0
_EXIT_WS01_FAILURE = 1
# argparse owns exit 2.
_EXIT_LOCAL_FILE_FAILURE = 3
_EXIT_PUBLICATION_AMBIGUOUS = 4

_AMBIGUOUS_REASON_CODE = "WS01_BR_PUBLICATION_AMBIGUOUS"
_INTERNAL_INVARIANT_REASON_CODE = "WS01_BR_INTERNAL_INVARIANT_FAILURE"

_NOT_ABSOLUTE = "raw_response_file_not_absolute"
_WRONG_TYPE = "raw_response_file_wrong_type"
_UNSTABLE = "raw_response_file_unstable"
_UNREADABLE = "raw_response_file_unreadable"
_OVERSIZED = "raw_response_file_oversized"
_LOCAL_FAILURE_TOKENS = (
    _NOT_ABSOLUTE,
    _WRONG_TYPE,
    _UNSTABLE,
    _UNREADABLE,
    _OVERSIZED,
)

_LOWERCASE_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_PATH_COMPONENTS = ("", ".", "..")
_READ_CHUNK_BYTES = 65_536

_OPEN_DIRECTORY_FLAGS = (
    _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC
)
_OPEN_FILE_FLAGS = (
    _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC | _os.O_NONBLOCK
)


class _RawResponseFileFailure(Exception):
    """Private carrier for one closed CLI-local raw-response-file token."""

    __slots__ = ("token",)

    def __init__(self, token: str) -> None:
        if token not in _LOCAL_FAILURE_TOKENS:
            token = _UNREADABLE
        self.token = token
        super().__init__(token)


class _RegularFileWitness:
    """Immutable stat evidence for one authenticated regular file."""

    __slots__ = (
        "device",
        "inode",
        "mode",
        "links",
        "size",
        "modified_ns",
        "changed_ns",
    )

    def __init__(self, status: _os.stat_result) -> None:
        self.device = status.st_dev
        self.inode = status.st_ino
        self.mode = status.st_mode
        self.links = status.st_nlink
        self.size = status.st_size
        self.modified_ns = status.st_mtime_ns
        self.changed_ns = status.st_ctime_ns

    def _key(self) -> tuple[int, int, int, int, int, int, int]:
        # Device, inode, mode (type and permission bits), link count and size
        # are the primary evidence.  The two timestamps are supplementary and
        # never stand alone.
        return (
            self.device,
            self.inode,
            self.mode,
            self.links,
            self.size,
            self.modified_ns,
            self.changed_ns,
        )

    def __eq__(self, other: object) -> bool:
        if type(other) is not _RegularFileWitness:
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())


def _fail(token: str) -> "None":
    raise _RawResponseFileFailure(token)


def _require_regular(status: _os.stat_result) -> None:
    if not _stat.S_ISREG(status.st_mode):
        _fail(_WRONG_TYPE)


def _selector_components(selector: object) -> tuple[str, ...]:
    """Evaluate the operator's raw-response selector exactly once."""
    if type(selector) is not str or not selector.startswith("/"):
        _fail(_NOT_ABSOLUTE)
    parts = selector.split("/")
    if len(parts) < 2 or parts[0] != "":
        _fail(_NOT_ABSOLUTE)
    components = tuple(parts[1:])
    if any(
        component in _FORBIDDEN_PATH_COMPONENTS for component in components
    ):
        _fail(_NOT_ABSOLUTE)
    return components


def _read_exactly(descriptor: int, *, expected_size: int) -> bytes:
    """Read the expected sequence and require immediate end of file."""
    chunks: list[bytes] = []
    total = 0
    while total < expected_size:
        try:
            chunk = _os.read(
                descriptor,
                min(_READ_CHUNK_BYTES, expected_size - total),
            )
        except OSError:
            _fail(_UNREADABLE)
        if not chunk:
            # The file shrank underneath the descriptor.
            _fail(_UNSTABLE)
        total += len(chunk)
        if total > expected_size:
            _fail(_UNSTABLE)
        chunks.append(chunk)
    try:
        trailing = _os.read(descriptor, 1)
    except OSError:
        _fail(_UNREADABLE)
    if trailing:
        # The file grew underneath the descriptor.
        _fail(_UNSTABLE)
    return b"".join(chunks)


def _authenticated_response_bytes(selector: object) -> bytes:
    """Authenticate and stably read the one operator-selected response file."""
    components = _selector_components(selector)
    descriptors: list[int] = []
    try:
        try:
            parent = _os.open("/", _OPEN_DIRECTORY_FLAGS)
        except OSError:
            _fail(_UNREADABLE)
        descriptors.append(parent)
        for component in components[:-1]:
            try:
                child = _os.open(
                    component,
                    _OPEN_DIRECTORY_FLAGS,
                    dir_fd=parent,
                )
            except OSError:
                # A symlinked or otherwise unusable ancestor fails closed.
                _fail(_UNREADABLE)
            descriptors.append(child)
            parent = child
        leaf = components[-1]

        try:
            entry_status = _os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except OSError:
            _fail(_UNREADABLE)
        _require_regular(entry_status)
        entry_witness = _RegularFileWitness(entry_status)
        if entry_witness.size > _MAXIMUM_RAW_RESPONSE_BYTES:
            _fail(_OVERSIZED)
        expected_size = entry_witness.size

        try:
            descriptor = _os.open(leaf, _OPEN_FILE_FLAGS, dir_fd=parent)
        except OSError:
            _fail(_UNREADABLE)
        descriptors.append(descriptor)

        try:
            opened_status = _os.fstat(descriptor)
        except OSError:
            _fail(_UNREADABLE)
        _require_regular(opened_status)
        if _RegularFileWitness(opened_status) != entry_witness:
            _fail(_UNSTABLE)

        first = _read_exactly(descriptor, expected_size=expected_size)
        _require_stable(
            descriptor,
            leaf,
            parent=parent,
            expected=entry_witness,
        )

        try:
            if _os.lseek(descriptor, 0, _os.SEEK_SET) != 0:
                _fail(_UNREADABLE)
        except OSError:
            _fail(_UNREADABLE)

        second = _read_exactly(descriptor, expected_size=expected_size)
        _require_stable(
            descriptor,
            leaf,
            parent=parent,
            expected=entry_witness,
        )
        if first != second or len(first) != expected_size:
            _fail(_UNSTABLE)
        return first
    finally:
        _close_all(descriptors)


def _require_stable(
    descriptor: int,
    leaf: str,
    *,
    parent: int,
    expected: _RegularFileWitness,
) -> None:
    """Recheck descriptor and named-entry evidence against the accepted one."""
    try:
        descriptor_status = _os.fstat(descriptor)
        entry_status = _os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    except OSError:
        _fail(_UNSTABLE)
    if not _stat.S_ISREG(descriptor_status.st_mode) or not _stat.S_ISREG(
        entry_status.st_mode
    ):
        _fail(_UNSTABLE)
    if (
        _RegularFileWitness(descriptor_status) != expected
        or _RegularFileWitness(entry_status) != expected
    ):
        _fail(_UNSTABLE)


def _close_all(descriptors: list[int]) -> None:
    failed = False
    while descriptors:
        descriptor = descriptors.pop()
        try:
            _os.close(descriptor)
        except OSError:
            failed = True
    if failed:
        _fail(_UNREADABLE)


def _emit_failure(token: str, exit_code: int) -> int:
    _sys.stderr.write(f"{token}\n")
    return exit_code


def _render_success(receipt: object) -> int:
    """Emit one preconstructed bounded receipt block, or fail closed."""
    try:
        report_identity = receipt.report_identity_sha256
        summary_identity = receipt.run_summary_identity_sha256
        relative_path = receipt.publication_relative_path
        artifact_filenames = receipt.artifact_filenames
        reused = receipt.publication_reused
    except AttributeError:
        return _emit_failure(
            _AMBIGUOUS_REASON_CODE,
            _EXIT_PUBLICATION_AMBIGUOUS,
        )
    if (
        type(report_identity) is not str
        or _LOWERCASE_SHA256.fullmatch(report_identity) is None
        or type(summary_identity) is not str
        or _LOWERCASE_SHA256.fullmatch(summary_identity) is None
        or type(relative_path) is not str
        or not relative_path
        or relative_path.startswith("/")
        or type(artifact_filenames) is not tuple
        or len(artifact_filenames) != 2
        or any(
            type(name) is not str or not name or "/" in name
            for name in artifact_filenames
        )
        or type(reused) is not bool
    ):
        return _emit_failure(
            _AMBIGUOUS_REASON_CODE,
            _EXIT_PUBLICATION_AMBIGUOUS,
        )
    block = (
        f"publication_reused={'true' if reused else 'false'}\n"
        f"report_identity_sha256={report_identity}\n"
        f"run_summary_identity_sha256={summary_identity}\n"
        f"publication_relative_path={relative_path}\n"
        f"artifact_filenames={','.join(artifact_filenames)}\n"
    )
    _sys.stdout.write(block)
    return _EXIT_SUCCESS


def _render_result(result: object) -> int:
    """Map one WS01d envelope to exactly one operator result and exit code."""
    try:
        ok = result.ok
        reason_code = result.reason_code
        value = result.value
    except AttributeError:
        return _emit_failure(
            _AMBIGUOUS_REASON_CODE,
            _EXIT_PUBLICATION_AMBIGUOUS,
        )
    if ok is False:
        if type(reason_code) is not str or not reason_code or value is not None:
            return _emit_failure(
                _AMBIGUOUS_REASON_CODE,
                _EXIT_PUBLICATION_AMBIGUOUS,
            )
        if reason_code == _AMBIGUOUS_REASON_CODE:
            return _emit_failure(
                reason_code,
                _EXIT_PUBLICATION_AMBIGUOUS,
            )
        return _emit_failure(reason_code, _EXIT_WS01_FAILURE)
    if ok is not True or reason_code is not None or value is None:
        return _emit_failure(
            _AMBIGUOUS_REASON_CODE,
            _EXIT_PUBLICATION_AMBIGUOUS,
        )
    return _render_success(value)


_SUPPLIED_SELECTOR_DESTINATIONS = "_supplied_selector_destinations"


class _SingleOccurrenceAction(_argparse.Action):
    """Store one verbatim value and reject a repeated operational selector.

    Every operational selector names exactly one operand of one explicit
    publication.  A repeated occurrence is ambiguous operator input even when
    the repeated values are identical, so it is reported as an ordinary
    argparse usage error and exits 2 rather than silently choosing one value.
    The already-supplied set lives on the per-parse namespace, so no state is
    shared between invocations and values are never normalised or rewritten.
    """

    def __call__(
        self,
        parser: _argparse.ArgumentParser,
        namespace: _argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        # ``vars`` exposes the parse namespace mapping argparse itself writes,
        # so no reflective attribute dispatch is needed to record or store one
        # selector.
        state = vars(namespace)
        supplied = state.setdefault(_SUPPLIED_SELECTOR_DESTINATIONS, set())
        if self.dest in supplied:
            parser.error(
                f"argument {option_string}: may be supplied at most once"
            )
        supplied.add(self.dest)
        state[self.dest] = values


def build_parser() -> _argparse.ArgumentParser:
    """Build the explicit WS01e operator publication CLI."""
    parser = _argparse.ArgumentParser(
        description=(
            "Publish one report-only WEEKLY-SHADOW-01 generation from a raw "
            "analyst response the operator obtained and saved manually. This "
            "command never contacts a model and never retries."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    publish_parser = subparsers.add_parser(
        "publish",
        help=(
            "Publish one generation from one explicitly selected raw-response "
            "file."
        ),
    )
    publish_parser.add_argument(
        "--generation-id",
        action=_SingleOccurrenceAction,
        required=True,
        help="Exact source generation identifier typed by the operator.",
    )
    publish_parser.add_argument(
        "--raw-response-file",
        action=_SingleOccurrenceAction,
        required=True,
        help=(
            "Absolute path to the single file holding the exact raw analyst "
            "response bytes the operator saved by hand."
        ),
    )
    publish_parser.add_argument(
        "--output-root",
        action=_SingleOccurrenceAction,
        required=True,
        help="Absolute report-only publication output root.",
    )
    publish_parser.add_argument(
        "--repository-root",
        action=_SingleOccurrenceAction,
        default=None,
        help=(
            "Optional absolute repository root. Omit to let the publisher use "
            "its own default."
        ),
    )
    return parser


def main(argv: _Sequence[str] | None = None) -> int:
    """Read one operator response file and publish it exactly once."""
    parser = build_parser()
    arguments = parser.parse_args(argv)

    try:
        raw_response_bytes = _authenticated_response_bytes(
            arguments.raw_response_file
        )
    except _RawResponseFileFailure as failure:
        return _emit_failure(failure.token, _EXIT_LOCAL_FILE_FAILURE)
    except Exception:  # noqa: BLE001 - no diagnostic may reach the operator
        return _emit_failure(
            _INTERNAL_INVARIANT_REASON_CODE,
            _EXIT_WS01_FAILURE,
        )

    try:
        result = _report_publisher.publish_weekly_shadow_report(
            arguments.generation_id,
            raw_response_bytes=raw_response_bytes,
            output_root=arguments.output_root,
            repository_root=arguments.repository_root,
        )
    except Exception:  # noqa: BLE001 - post-invocation uncertainty is ambiguity
        return _emit_failure(
            _AMBIGUOUS_REASON_CODE,
            _EXIT_PUBLICATION_AMBIGUOUS,
        )

    try:
        return _render_result(result)
    except Exception:  # noqa: BLE001 - never downgrade committed uncertainty
        return _emit_failure(
            _AMBIGUOUS_REASON_CODE,
            _EXIT_PUBLICATION_AMBIGUOUS,
        )


if __name__ == "__main__":
    raise SystemExit(main())
