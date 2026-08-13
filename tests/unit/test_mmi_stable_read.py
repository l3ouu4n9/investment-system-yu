import errno
import os
import stat
from pathlib import Path
from unittest import mock

import pytest

from investment_orchestrator.mmi.stable_read import (
    MmiStableReadError,
    MmiStableReadErrorCode,
    stable_read_exact_bytes,
)


def test_stable_read_exact_bytes_success(tmp_path: Path) -> None:
    test_file = tmp_path / "test.txt"
    test_file.write_bytes(b"hello world")
    case_fd = os.open(os.fspath(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        result = stable_read_exact_bytes(
            case_fd, "test.txt", maximum_bytes=100
        )
        assert result == b"hello world"
    finally:
        os.close(case_fd)


def test_stable_read_symlink_rejection(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"target")
    link = tmp_path / "link.txt"
    os.symlink("target.txt", link)
    case_fd = os.open(os.fspath(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(MmiStableReadError) as exc_info:
            stable_read_exact_bytes(case_fd, "link.txt", maximum_bytes=100)
        assert exc_info.value.code == MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID
    finally:
        os.close(case_fd)


def test_stable_read_directory_rejection(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    case_fd = os.open(os.fspath(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(MmiStableReadError) as exc_info:
            stable_read_exact_bytes(case_fd, "subdir", maximum_bytes=100)
        assert exc_info.value.code == MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID
    finally:
        os.close(case_fd)


def test_stable_read_maximum_plus_one_rejected(tmp_path: Path) -> None:
    test_file = tmp_path / "test.txt"
    test_file.write_bytes(b"hello world")
    case_fd = os.open(os.fspath(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(MmiStableReadError) as exc_info:
            stable_read_exact_bytes(case_fd, "test.txt", maximum_bytes=10)
        assert exc_info.value.code == MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID
    finally:
        os.close(case_fd)


def test_stable_read_zero_bytes_rejected(tmp_path: Path) -> None:
    test_file = tmp_path / "test.txt"
    test_file.write_bytes(b"")
    case_fd = os.open(os.fspath(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(MmiStableReadError) as exc_info:
            stable_read_exact_bytes(case_fd, "test.txt", maximum_bytes=100)
        assert exc_info.value.code == MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID
    finally:
        os.close(case_fd)


def test_stable_read_exact_bytes_single_open_close_oracle(tmp_path: Path) -> None:
    test_file = tmp_path / "test.txt"
    test_file.write_bytes(b"hello world")
    case_fd = os.open(os.fspath(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    original_open = os.open
    original_close = os.close

    open_calls = []
    close_calls = []

    def tracked_open(path: str, flags: int, dir_fd: int | None = None) -> int:
        open_calls.append((path, flags, dir_fd))
        if dir_fd is not None:
            return original_open(path, flags, dir_fd=dir_fd)
        return original_open(path, flags)

    def tracked_close(fd: int) -> None:
        close_calls.append(fd)
        return original_close(fd)

    try:
        with mock.patch("investment_orchestrator.mmi.stable_read.os.open", side_effect=tracked_open):
            with mock.patch("investment_orchestrator.mmi.stable_read.os.close", side_effect=tracked_close):
                result = stable_read_exact_bytes(case_fd, "test.txt", maximum_bytes=100)

        assert result == b"hello world"
        assert len(open_calls) == 1
        assert len(close_calls) == 1

        path, flags, dir_fd = open_calls[0]
        assert path == "test.txt"
        assert dir_fd == case_fd
        assert (flags & (os.O_WRONLY | os.O_RDWR)) == 0
        assert flags & os.O_NOFOLLOW
        assert flags & os.O_CLOEXEC
        assert flags & os.O_NONBLOCK
    finally:
        os.close(case_fd)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX-only test")
def test_stable_read_fifo_nonblocking_oracle(tmp_path: Path) -> None:
    fifo_path = tmp_path / "test_fifo"
    os.mkfifo(fifo_path)
    case_fd = os.open(os.fspath(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    original_open = os.open
    original_close = os.close

    open_calls = []
    close_calls = []

    def tracked_open(path: str, flags: int, dir_fd: int | None = None) -> int:
        open_calls.append((path, flags, dir_fd))
        if dir_fd is not None:
            return original_open(path, flags, dir_fd=dir_fd)
        return original_open(path, flags)

    def tracked_close(fd: int) -> None:
        close_calls.append(fd)
        return original_close(fd)

    try:
        with mock.patch("investment_orchestrator.mmi.stable_read.os.open", side_effect=tracked_open):
            with mock.patch("investment_orchestrator.mmi.stable_read.os.close", side_effect=tracked_close):
                with pytest.raises(MmiStableReadError) as exc_info:
                    stable_read_exact_bytes(case_fd, "test_fifo", maximum_bytes=100)

        assert exc_info.value.code == MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID
        assert len(open_calls) == 1
        assert len(close_calls) == 1

        path, flags, dir_fd = open_calls[0]
        assert path == "test_fifo"
        assert dir_fd == case_fd
        assert flags & os.O_NONBLOCK
        assert (flags & (os.O_WRONLY | os.O_RDWR)) == 0
        assert flags & os.O_NOFOLLOW
        assert flags & os.O_CLOEXEC
    finally:
        os.close(case_fd)

@mock.patch("investment_orchestrator.mmi.stable_read.os.fstat")
def test_stable_read_mid_read_mutation_via_witness(mock_fstat, tmp_path: Path) -> None:
    # A cleaner test using mocked fstat that returns different witnesses.
    case_fd = 999

    class FakeStat:
        st_mode = stat.S_IFREG
        st_dev = 1
        st_ino = 2
        st_size = 5
        st_mtime_ns = 3
        st_ctime_ns = 4

    class FakeStatMutated:
        st_mode = stat.S_IFREG
        st_dev = 1
        st_ino = 2
        st_size = 6
        st_mtime_ns = 3
        st_ctime_ns = 4

    mock_fstat.side_effect = [FakeStat(), FakeStatMutated()]

    with mock.patch("investment_orchestrator.mmi.stable_read.os.open", return_value=123):
        with mock.patch("investment_orchestrator.mmi.stable_read.os.read", side_effect=[b"hello", b""]):
            with mock.patch("investment_orchestrator.mmi.stable_read.os.close"):
                with pytest.raises(MmiStableReadError) as exc_info:
                    stable_read_exact_bytes(case_fd, "test.txt", maximum_bytes=100)
                assert exc_info.value.code == MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID


@mock.patch("investment_orchestrator.mmi.stable_read.os.open")
def test_stable_read_capability_unavailable(mock_open, tmp_path: Path) -> None:
    mock_open.side_effect = OSError(errno.EMFILE, "Too many open files")
    case_fd = 999

    with pytest.raises(MmiStableReadError) as exc_info:
        stable_read_exact_bytes(case_fd, "test.txt", maximum_bytes=100)
    assert exc_info.value.code == MmiStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE


@pytest.mark.parametrize("error_number", (errno.EACCES, errno.EPERM))
@mock.patch("investment_orchestrator.mmi.stable_read.os.open")
def test_stable_read_preserves_controlled_input_errno(
    mock_open,
    tmp_path: Path,
    error_number: int,
) -> None:
    mock_open.side_effect = OSError(error_number, "permission denied")

    with pytest.raises(MmiStableReadError) as exc_info:
        stable_read_exact_bytes(999, "test.txt", maximum_bytes=100)

    assert exc_info.value.code == MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID
    assert exc_info.value.os_error_errno == error_number


@mock.patch("investment_orchestrator.mmi.stable_read.os.open")
def test_stable_read_unexpected_error_not_swallowed(mock_open, tmp_path: Path) -> None:
    # A totally unexpected OS error like EIO or something not in the controlled list
    mock_open.side_effect = OSError(errno.EIO, "Input/output error")
    case_fd = 999

    with pytest.raises(OSError) as exc_info:
        stable_read_exact_bytes(case_fd, "test.txt", maximum_bytes=100)
    assert exc_info.value.errno == errno.EIO
