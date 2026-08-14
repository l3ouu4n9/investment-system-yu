"""Stable relative-file read primitive for bounded exact byte acquisition."""

import errno
import os
import stat
from dataclasses import dataclass
from enum import Enum
from typing import NoReturn


__all__ = (
    "MmiStableReadError",
    "MmiStableReadErrorCode",
    "stable_read_exact_bytes",
)


class MmiStableReadErrorCode(str, Enum):
    STABLE_READ_INPUT_INVALID = "STABLE_READ_INPUT_INVALID"
    STABLE_READ_CAPABILITY_UNAVAILABLE = "STABLE_READ_CAPABILITY_UNAVAILABLE"


class MmiStableReadError(RuntimeError):
    def __init__(
        self,
        code: MmiStableReadErrorCode,
        *,
        os_error_errno: int | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.os_error_errno = os_error_errno


_CONTROLLED_INPUT_ERRNOS = frozenset(
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
_CONTROLLED_CAPABILITY_ERRNOS = frozenset(
    {errno.EMFILE, errno.ENFILE}
)


def _translate_stable_read_oserror(exc: OSError) -> NoReturn:
    if exc.errno in _CONTROLLED_INPUT_ERRNOS or exc.errno == errno.EINVAL:
        raise MmiStableReadError(
            MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID,
            os_error_errno=exc.errno,
        ) from exc
    if exc.errno in _CONTROLLED_CAPABILITY_ERRNOS:
        raise MmiStableReadError(
            MmiStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE,
            os_error_errno=exc.errno,
        ) from exc
    raise exc


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


_READ_CHUNK_BYTES = 65_536


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


def stable_read_exact_bytes(
    directory_fd: int,
    relative_path: str,
    *,
    maximum_bytes: int,
) -> bytes:
    flags = (
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    )
    fd: int | None = None
    try:
        fd = os.open(relative_path, flags, dir_fd=directory_fd)
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        before = _read_witness(status)
        if before.size < 1 or before.size > maximum_bytes:
            raise MmiStableReadError(MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID)
        exact_bytes = _read_to_eof_once(
            fd,
            maximum_bytes=maximum_bytes,
        )
        after = _read_witness(os.fstat(fd))
        if before != after or len(exact_bytes) != before.size:
            raise MmiStableReadError(MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID)
        return exact_bytes
    except OSError as exc:
        _translate_stable_read_oserror(exc)
    finally:
        if fd is not None:
            os.close(fd)
