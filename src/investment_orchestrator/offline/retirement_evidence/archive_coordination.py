"""Offline cooperative writer-quiescence coordination for retirement archives.

The lease in this module proves one narrow fact only: compliant repository-owned
archive tools using the same explicit anchor obeyed a shared-reader / exclusive-
writer advisory lock for the lease interval.  It does not prove filesystem
immutability, external-writer quiescence, authenticity, malicious-operator
resistance, legacy-binary compliance, or power-loss durability.

The anchor is operator-provisioned.  This module never creates or modifies it.

Capability integrity covers supported repository APIs and ordinary direct API
misuse, including stale, copied, reconstructed, wrong-root, wrong-mode, and
wrong-operation handles.  The operator-trusted local-process model does not
claim resistance to malicious same-interpreter code that rewrites functions or
uses unrestricted reflection to alter closure state.

Fork lifecycle handlers are closure-owned and are not callable through the
module API.  Once the registered child handler completes, inherited leases are
invalid and inherited coordination descriptors are closed without an explicit
unlock.  Before that handler receives CPU time, an inherited open-file
description may transiently cause canonical nonblocking contention; callers
may explicitly retry after child cleanup and owner release.
"""

from __future__ import annotations

import errno
import os
import stat
import threading
import weakref
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NoReturn

try:  # Importing is allowed everywhere; acquisition fails closed if absent.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on unsupported OSes
    fcntl = None  # type: ignore[assignment]


COORDINATION_CONTRACT_VERSION = "retirement_archive_coordination_v1"
COORDINATION_ANCHOR_BYTES = (COORDINATION_CONTRACT_VERSION + "\n").encode("ascii")
COORDINATION_ANCHOR_MAX_BYTES = 4096
COORDINATION_REQUIRED_LINK_COUNT = 1

LOCK_MODE_SHARED = "shared"
LOCK_MODE_EXCLUSIVE = "exclusive"
LOCK_MODES = frozenset((LOCK_MODE_SHARED, LOCK_MODE_EXCLUSIVE))

COORDINATION_SCOPE = "repository_owned_compliant_writers_using_same_anchor"
STATUS_VERIFIED = "verified_for_protected_scan_interval"
STATUS_FAILED = "coordination_failed"

# Canonical, code-owned failure tokens.  No exception includes an OS message or
# an operator path.
TOKEN_PATH_OMITTED = "coordination_path_omitted"
TOKEN_PATH_INVALID = "coordination_path_invalid"
TOKEN_PATH_INSIDE_ARCHIVE = "coordination_anchor_inside_archive_root"
TOKEN_CONTENDED = "coordination_lock_contended"
TOKEN_UNSUPPORTED = "coordination_unsupported"
TOKEN_MISSING = "coordination_anchor_missing"
TOKEN_UNSAFE_TYPE = "coordination_anchor_unsafe_type"
TOKEN_UNREADABLE = "coordination_anchor_unreadable"
TOKEN_INCOMPATIBLE_CONTRACT = "coordination_contract_incompatible"
TOKEN_INTERRUPTED = "coordination_acquisition_interrupted"
TOKEN_IDENTITY_CHANGED = "coordination_anchor_identity_changed"
TOKEN_LEASE_INVALID = "coordination_lease_invalid"
FAILURE_TOKENS = frozenset(
    (
        TOKEN_PATH_OMITTED,
        TOKEN_PATH_INVALID,
        TOKEN_PATH_INSIDE_ARCHIVE,
        TOKEN_CONTENDED,
        TOKEN_UNSUPPORTED,
        TOKEN_MISSING,
        TOKEN_UNSAFE_TYPE,
        TOKEN_UNREADABLE,
        TOKEN_INCOMPATIBLE_CONTRACT,
        TOKEN_INTERRUPTED,
        TOKEN_IDENTITY_CHANGED,
        TOKEN_LEASE_INVALID,
    )
)


class CoordinationError(Exception):
    """Fail-closed coordination error carrying one safe token only."""

    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(token)


@dataclass(frozen=True)
class _AnchorIdentity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class _LeaseStatusSnapshot:
    """Non-authoritative status safe for internal diagnostics and tests."""

    mode: str
    state: str
    contract_version: str


class VerifiedCoordinationLease:
    """Immutable live capability owning one verified OS-managed advisory lock.

    Instances can only be created by :func:`acquire_coordination_lease`.
    Closing the descriptor removes the instance from the module-owned live
    registry, so a closed, released, or fabricated object cannot validate.
    """

    __slots__ = (
        "__fd",
        "__mode",
        "__identity",
        "__anchor_path",
        "__archive_root",
        "__pid",
        "__operation_nonce",
        "__contract_version",
        "__state",
        "__record_token",
        "__closed",
        "__construction_token",
        "__weakref__",
    )

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("coordination capabilities cannot be constructed")

    def __init_subclass__(cls, **kwargs: Any) -> NoReturn:
        raise TypeError("coordination capabilities cannot be subclassed")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("VerifiedCoordinationLease is immutable")

    @property
    def contract_version(self) -> str:
        return COORDINATION_CONTRACT_VERSION

    @property
    def lock_mode(self) -> str:
        return self.__mode

    @property
    def active(self) -> bool:
        return _lease_is_active(self)

    @property
    def closed(self) -> bool:
        return not self.active

    def validate(self, *, expected_mode: str | None = None) -> None:
        """Compatibility check; authorization uses the exact module validator."""
        validate_coordination_lease(self, expected_mode=expected_mode)

    def close(self) -> None:
        """Release the lock and permanently invalidate this capability."""
        _close_lease(self)

    def __enter__(self) -> VerifiedCoordinationLease:
        self.validate(expected_mode=self.__mode)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - deterministic use is via context manager
        try:
            self.close()
        except BaseException:
            pass

    def __copy__(self) -> NoReturn:
        raise TypeError("coordination capabilities cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("coordination capabilities cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("coordination capabilities cannot be serialized")

    def __reduce_ex__(self, protocol: int) -> NoReturn:
        raise TypeError("coordination capabilities cannot be serialized")

    def __repr__(self) -> str:
        return f"<VerifiedCoordinationLease {'active' if self.active else 'closed'}>"


def _resolved_root(path: Path | str) -> Path:
    try:
        return Path(os.path.abspath(os.path.normpath(os.fspath(path)))).resolve(
            strict=False
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        raise CoordinationError(TOKEN_PATH_INVALID) from None


def _make_lease_manager() -> tuple[Any, ...]:
    """Create the only authority-bearing lease registry and lifecycle API.

    The construction token and authoritative records remain closure-owned.  The
    returned functions validate, transition, or release an existing capability;
    none accepts a caller-supplied record or provides registry insertion.
    """

    @dataclass(frozen=True, slots=True)
    class LeaseRecord:
        fd: int
        mode: str
        identity: _AnchorIdentity
        anchor_path: Path
        archive_root: Path
        pid: int
        operation_nonce: object
        contract_version: str
        state: str
        record_token: object

    @dataclass(frozen=True, slots=True)
    class PendingOpen:
        identity: _AnchorIdentity
        baseline_fds: frozenset[int]

    construction_token = object()
    records: weakref.WeakKeyDictionary[
        VerifiedCoordinationLease, LeaseRecord
    ] = weakref.WeakKeyDictionary()
    pending_fds: set[int] = set()
    pending_opens: dict[object, PendingOpen] = {}
    lifecycle_lock = threading.RLock()

    def record_for(value: object) -> LeaseRecord:
        if type(value) is not VerifiedCoordinationLease:
            raise CoordinationError(TOKEN_LEASE_INVALID)
        try:
            record = records.get(value)  # type: ignore[arg-type]
            if record is None:
                raise KeyError
            token = object.__getattribute__(
                value, "_VerifiedCoordinationLease__construction_token"
            )
            closed = object.__getattribute__(
                value, "_VerifiedCoordinationLease__closed"
            )
            owned_fd = object.__getattribute__(
                value, "_VerifiedCoordinationLease__fd"
            )
            owned_mode = object.__getattribute__(
                value, "_VerifiedCoordinationLease__mode"
            )
            owned_identity = object.__getattribute__(
                value, "_VerifiedCoordinationLease__identity"
            )
            owned_path = object.__getattribute__(
                value, "_VerifiedCoordinationLease__anchor_path"
            )
            owned_root = object.__getattribute__(
                value, "_VerifiedCoordinationLease__archive_root"
            )
            owned_pid = object.__getattribute__(
                value, "_VerifiedCoordinationLease__pid"
            )
            owned_nonce = object.__getattribute__(
                value, "_VerifiedCoordinationLease__operation_nonce"
            )
            owned_version = object.__getattribute__(
                value, "_VerifiedCoordinationLease__contract_version"
            )
            owned_state = object.__getattribute__(
                value, "_VerifiedCoordinationLease__state"
            )
            owned_record_token = object.__getattribute__(
                value, "_VerifiedCoordinationLease__record_token"
            )
        except (KeyError, TypeError, AttributeError):
            raise CoordinationError(TOKEN_LEASE_INVALID) from None
        if (
            token is not construction_token
            or closed
            or record.state == "closed"
            or owned_fd != record.fd
            or owned_mode != record.mode
            or owned_identity != record.identity
            or owned_path != record.anchor_path
            or owned_root != record.archive_root
            or owned_pid != record.pid
            or owned_nonce is not record.operation_nonce
            or owned_version != record.contract_version
            or owned_state != record.state
            or owned_record_token is not record.record_token
            or record.contract_version != COORDINATION_CONTRACT_VERSION
            or record.pid != os.getpid()
        ):
            raise CoordinationError(TOKEN_LEASE_INVALID)
        return record

    def validate_record(
        value: object,
        *,
        expected_mode: str | None = None,
        archive_root: Path | str | None = None,
        require_active_operation: bool = False,
    ) -> LeaseRecord:
        record = record_for(value)
        if expected_mode is not None and (
            expected_mode not in LOCK_MODES or record.mode != expected_mode
        ):
            raise CoordinationError(TOKEN_LEASE_INVALID)
        if require_active_operation and record.state != "active":
            raise CoordinationError(TOKEN_LEASE_INVALID)
        if record.state not in {"acquired", "active"}:
            raise CoordinationError(TOKEN_LEASE_INVALID)
        if (
            archive_root is not None
            and _resolved_root(archive_root) != record.archive_root
        ):
            raise CoordinationError(TOKEN_LEASE_INVALID)
        try:
            descriptor_stat = os.fstat(record.fd)
        except OSError:
            raise CoordinationError(TOKEN_LEASE_INVALID) from None
        if not _stat_is_valid_anchor(descriptor_stat):
            raise CoordinationError(TOKEN_IDENTITY_CHANGED)
        if _identity(descriptor_stat) != record.identity:
            raise CoordinationError(TOKEN_IDENTITY_CHANGED)
        _validate_contract_bytes(record.fd, descriptor_stat)
        _recheck_path_identity(record.anchor_path, descriptor_stat)
        return record

    def transition(value: object, record: LeaseRecord, state: str) -> LeaseRecord:
        try:
            current = records.get(value)  # type: ignore[arg-type]
        except TypeError:
            current = None
        if current is not record:
            raise CoordinationError(TOKEN_LEASE_INVALID)
        updated = replace(record, state=state)
        records[value] = updated  # type: ignore[index]
        object.__setattr__(value, "_VerifiedCoordinationLease__state", state)
        return updated

    def validate_coordination_lease(
        value: object,
        *,
        expected_mode: str | None = None,
        archive_root: Path | str | None = None,
        require_active_operation: bool = False,
    ) -> None:
        """Nonvirtual exact capability validator for every authority gate."""
        with lifecycle_lock:
            validate_record(
                value,
                expected_mode=expected_mode,
                archive_root=archive_root,
                require_active_operation=require_active_operation,
            )

    def begin_coordination_operation(
        value: object, *, archive_root: Path | str, expected_mode: str
    ) -> None:
        with lifecycle_lock:
            record = validate_record(
                value, expected_mode=expected_mode, archive_root=archive_root
            )
            if record.state != "acquired":
                raise CoordinationError(TOKEN_LEASE_INVALID)
            transition(value, record, "active")

    def validate_coordination_operation(
        value: object, *, archive_root: Path | str, expected_mode: str
    ) -> None:
        with lifecycle_lock:
            validate_record(
                value,
                expected_mode=expected_mode,
                archive_root=archive_root,
                require_active_operation=True,
            )

    def coordination_operation_identity(
        value: object, *, archive_root: Path | str, expected_mode: str
    ) -> object:
        with lifecycle_lock:
            validate_record(
                value,
                expected_mode=expected_mode,
                archive_root=archive_root,
                require_active_operation=True,
            )
            # Compatibility hook for scanner/ingestion binding.  The exact live
            # lease is already one-operation-only, so its identity is sufficient
            # and no internal operation nonce is returned.
            return value

    def coordination_status_snapshot(value: object) -> _LeaseStatusSnapshot:
        with lifecycle_lock:
            record = validate_record(value)
            return _LeaseStatusSnapshot(
                mode=record.mode,
                state=record.state,
                contract_version=record.contract_version,
            )

    def complete_coordination_operation(
        value: object, *, archive_root: Path | str, expected_mode: str
    ) -> None:
        with lifecycle_lock:
            record = validate_record(
                value,
                expected_mode=expected_mode,
                archive_root=archive_root,
                require_active_operation=True,
            )
            transition(value, record, "complete")

    def lease_is_active(value: object) -> bool:
        with lifecycle_lock:
            try:
                return record_for(value).state in {"acquired", "active"}
            except CoordinationError:
                return False

    def close_lease(value: object) -> None:
        with lifecycle_lock:
            if type(value) is not VerifiedCoordinationLease:
                raise CoordinationError(TOKEN_LEASE_INVALID)
            try:
                record = records.get(value)
            except TypeError:
                record = None
            if record is None or record.state == "closed":
                try:
                    object.__setattr__(
                        value, "_VerifiedCoordinationLease__closed", True
                    )
                except (AttributeError, TypeError):
                    pass
                return
            owner = record.pid == os.getpid()
            closing = transition(value, record, "closed")
            object.__setattr__(value, "_VerifiedCoordinationLease__closed", True)
            pending_fds.discard(record.fd)
            if owner:
                try:
                    if fcntl is not None:
                        fcntl.flock(record.fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(record.fd)
            except OSError:
                pass
            if records.get(value) is closing:
                records.pop(value, None)

    def before_fork() -> None:
        lifecycle_lock.acquire()

    def after_fork_parent() -> None:
        lifecycle_lock.release()

    def after_fork_child() -> None:
        nonlocal lifecycle_lock
        # flock belongs to the open-file description shared with the parent.
        # Close inherited descriptors, but never issue LOCK_UN in the child.
        for lease, record in tuple(records.items()):
            try:
                os.close(record.fd)
            except OSError:
                pass
            object.__setattr__(lease, "_VerifiedCoordinationLease__closed", True)
        for fd in tuple(pending_fds):
            try:
                os.close(fd)
            except OSError:
                pass
        for pending in tuple(pending_opens.values()):
            matching_fds = _matching_anchor_fds(pending.identity)
            if matching_fds is None:
                os._exit(70)
            for fd in matching_fds - set(pending.baseline_fds):
                try:
                    os.close(fd)
                except OSError:
                    pass
        records.clear()
        pending_fds.clear()
        pending_opens.clear()
        lifecycle_lock = threading.RLock()

    # Register closure-owned handlers directly.  They are deliberately neither
    # returned nor assigned to module globals: ordinary callers must not be able
    # to run child cleanup or manipulate the lifecycle lock of a live owner.
    if hasattr(os, "register_at_fork"):
        os.register_at_fork(
            before=before_fork,
            after_in_parent=after_fork_parent,
            after_in_child=after_fork_child,
        )

    def acquire_coordination_lease(
        coordination_path: Path | str | None,
        *,
        archive_root: Path | str,
        mode: str,
    ) -> VerifiedCoordinationLease:
        """Validate an explicit existing anchor and acquire a nonblocking lease."""
        if coordination_path is None:
            raise CoordinationError(TOKEN_PATH_OMITTED)
        if mode not in LOCK_MODES:
            raise CoordinationError(TOKEN_LEASE_INVALID)
        if (
            fcntl is None
            or not hasattr(os, "O_NOFOLLOW")
            or not hasattr(os, "O_CLOEXEC")
            or not hasattr(os, "register_at_fork")
            or not Path("/proc/self/fd").is_dir()
        ):
            raise CoordinationError(TOKEN_UNSUPPORTED)
        try:
            lexical_root = Path(
                os.path.abspath(os.path.normpath(os.fspath(archive_root)))
            )
        except (OSError, TypeError, ValueError):
            raise CoordinationError(TOKEN_PATH_INVALID) from None
        anchor_path = _validated_anchor_path(coordination_path, lexical_root)

        with lifecycle_lock:
            try:
                path_stat = os.lstat(anchor_path)
            except FileNotFoundError:
                raise CoordinationError(TOKEN_MISSING) from None
            except (OSError, ValueError):
                raise CoordinationError(TOKEN_UNREADABLE) from None
            if not _stat_is_valid_anchor(path_stat):
                raise CoordinationError(TOKEN_UNSAFE_TYPE)

            matching_fds = _matching_anchor_fds(_identity(path_stat))
            if matching_fds is None:
                raise CoordinationError(TOKEN_UNSUPPORTED)
            pending_open_token = object()
            pending_opens[pending_open_token] = PendingOpen(
                identity=_identity(path_stat),
                baseline_fds=frozenset(matching_fds),
            )
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
            try:
                fd = os.open(anchor_path, flags)
            except FileNotFoundError:
                raise CoordinationError(TOKEN_MISSING) from None
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise CoordinationError(TOKEN_UNSAFE_TYPE) from None
                if exc.errno in _UNSUPPORTED_ERRNOS:
                    raise CoordinationError(TOKEN_UNSUPPORTED) from None
                raise CoordinationError(TOKEN_UNREADABLE) from None
            else:
                pending_fds.add(fd)
            finally:
                pending_opens.pop(pending_open_token, None)

            lease: VerifiedCoordinationLease | None = None
            try:
                try:
                    descriptor_stat = os.fstat(fd)
                except OSError as exc:
                    token = (
                        TOKEN_INTERRUPTED
                        if exc.errno == errno.EBADF
                        else TOKEN_UNREADABLE
                    )
                    raise CoordinationError(token) from None
                if not _stat_is_valid_anchor(descriptor_stat):
                    raise CoordinationError(TOKEN_UNSAFE_TYPE)
                if _identity(descriptor_stat) != _identity(path_stat):
                    raise CoordinationError(TOKEN_IDENTITY_CHANGED)
                _recheck_path_identity(anchor_path, descriptor_stat)

                operation = (
                    fcntl.LOCK_SH if mode == LOCK_MODE_SHARED else fcntl.LOCK_EX
                )
                try:
                    fcntl.flock(fd, operation | fcntl.LOCK_NB)
                except InterruptedError:
                    raise CoordinationError(TOKEN_INTERRUPTED) from None
                except BlockingIOError:
                    raise CoordinationError(TOKEN_CONTENDED) from None
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EACCES):
                        raise CoordinationError(TOKEN_CONTENDED) from None
                    if exc.errno == errno.EINTR:
                        raise CoordinationError(TOKEN_INTERRUPTED) from None
                    raise CoordinationError(TOKEN_UNSUPPORTED) from None

                _validate_contract_bytes(fd, descriptor_stat)
                _recheck_path_identity(anchor_path, descriptor_stat)
                resolved_root = _resolved_root(lexical_root)
                if anchor_path == resolved_root or anchor_path.is_relative_to(
                    resolved_root
                ):
                    raise CoordinationError(TOKEN_PATH_INSIDE_ARCHIVE)

                lease = object.__new__(VerifiedCoordinationLease)
                operation_nonce = object()
                record_token = object()
                identity = _identity(descriptor_stat)
                values = {
                    "fd": fd,
                    "mode": mode,
                    "identity": identity,
                    "anchor_path": anchor_path,
                    "archive_root": resolved_root,
                    "pid": os.getpid(),
                    "operation_nonce": operation_nonce,
                    "contract_version": COORDINATION_CONTRACT_VERSION,
                    "state": "acquired",
                    "record_token": record_token,
                }
                for name, value in values.items():
                    object.__setattr__(
                        lease, f"_VerifiedCoordinationLease__{name}", value
                    )
                object.__setattr__(lease, "_VerifiedCoordinationLease__closed", False)
                object.__setattr__(
                    lease,
                    "_VerifiedCoordinationLease__construction_token",
                    construction_token,
                )
                records[lease] = LeaseRecord(**values)
                pending_fds.discard(fd)
                validate_record(lease, expected_mode=mode, archive_root=resolved_root)
                return lease
            except BaseException:
                pending_fds.discard(fd)
                if lease is not None:
                    records.pop(lease, None)
                    object.__setattr__(
                        lease, "_VerifiedCoordinationLease__closed", True
                    )
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise

    return (
        acquire_coordination_lease,
        validate_coordination_lease,
        begin_coordination_operation,
        validate_coordination_operation,
        coordination_operation_identity,
        complete_coordination_operation,
        lease_is_active,
        close_lease,
        coordination_status_snapshot,
    )


(
    acquire_coordination_lease,
    validate_coordination_lease,
    begin_coordination_operation,
    validate_coordination_operation,
    _coordination_operation_identity,
    complete_coordination_operation,
    _lease_is_active,
    _close_lease,
    _coordination_status_snapshot,
) = _make_lease_manager()
del _make_lease_manager


_UNSUPPORTED_ERRNOS = frozenset(
    value
    for value in (
        getattr(errno, "ENOSYS", None),
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
    )
    if value is not None
)


def _matching_anchor_fds(identity: _AnchorIdentity) -> set[int] | None:
    """Return open descriptors for one anchor identity on supported Linux POSIX."""
    matches: set[int] = set()
    try:
        names = os.listdir("/proc/self/fd")
    except OSError:
        return None
    for name in names:
        try:
            fd = int(name)
            descriptor_stat = os.fstat(fd)
        except (OSError, ValueError):
            continue
        if _identity(descriptor_stat) == identity:
            matches.add(fd)
    return matches


def _validated_anchor_path(
    coordination_path: Path | str, archive_root: Path | str
) -> Path:
    try:
        if isinstance(coordination_path, str) and not coordination_path:
            raise ValueError
        lexical_anchor = Path(coordination_path)
        if not lexical_anchor.name:
            raise ValueError
        resolved_parent = lexical_anchor.parent.resolve(strict=True)
        anchor_path = resolved_parent / lexical_anchor.name
        resolved_archive = Path(os.path.abspath(os.path.normpath(os.fspath(archive_root))))
    except (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError):
        raise CoordinationError(TOKEN_PATH_INVALID) from None
    if anchor_path == resolved_archive or anchor_path.is_relative_to(resolved_archive):
        raise CoordinationError(TOKEN_PATH_INSIDE_ARCHIVE)
    return anchor_path


def _identity(st: os.stat_result) -> _AnchorIdentity:
    return _AnchorIdentity(st.st_dev, st.st_ino)


def _stat_is_valid_anchor(st: os.stat_result) -> bool:
    return stat.S_ISREG(st.st_mode) and st.st_nlink == COORDINATION_REQUIRED_LINK_COUNT


def _recheck_path_identity(path: Path, descriptor_stat: os.stat_result) -> None:
    try:
        path_stat = os.lstat(path)
    except OSError:
        raise CoordinationError(TOKEN_IDENTITY_CHANGED) from None
    if not _stat_is_valid_anchor(path_stat):
        raise CoordinationError(TOKEN_IDENTITY_CHANGED)
    if _identity(path_stat) != _identity(descriptor_stat):
        raise CoordinationError(TOKEN_IDENTITY_CHANGED)


def _validate_contract_bytes(fd: int, descriptor_stat: os.stat_result) -> None:
    if descriptor_stat.st_size > COORDINATION_ANCHOR_MAX_BYTES:
        raise CoordinationError(TOKEN_INCOMPATIBLE_CONTRACT)
    try:
        if hasattr(os, "pread"):
            data = os.pread(fd, COORDINATION_ANCHOR_MAX_BYTES + 1, 0)
        else:  # pragma: no cover - supported acquisition platforms provide pread
            os.lseek(fd, 0, os.SEEK_SET)
            data = os.read(fd, COORDINATION_ANCHOR_MAX_BYTES + 1)
    except OSError:
        raise CoordinationError(TOKEN_UNREADABLE) from None
    if data != COORDINATION_ANCHOR_BYTES:
        raise CoordinationError(TOKEN_INCOMPATIBLE_CONTRACT)
