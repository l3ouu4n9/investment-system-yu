"""Combined Phase 2A/2B cooperative writer-quiescence contract tests."""

from __future__ import annotations

import copy
import errno
import inspect
import json
import os
import pickle
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence import archive_coordination as coord
from investment_orchestrator.offline.retirement_evidence import archive_index as idx
from investment_orchestrator.offline.retirement_evidence import archive_scan as scan
from investment_orchestrator.offline.retirement_evidence import cli, verify_cli
from investment_orchestrator.offline.retirement_evidence import ingest as ingest_mod
from investment_orchestrator.offline.retirement_evidence.ingest import (
    IndeterminatePostPublicationError,
    ingest_observation,
)
from investment_orchestrator.research.step1a_retirement_observation import (
    _minimal_incomplete_observation,
)

from test_retirement_evidence_archive import _STAMP, _TOOL, _obs, _write_source
from test_step1a_retirement_observation import _builder_inputs


def _anchor(tmp_path: Path, name: str = "coordination.anchor") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    path.write_bytes(coord.COORDINATION_ANCHOR_BYTES)
    return path


def _ingest(source: Path, root: Path, anchor: Path):
    return ingest_observation(
        source_path=source,
        dest_root=root,
        coordination_path=anchor,
        tool_identity=_TOOL,
        archived_at=_STAMP,
    )


def _initialized_archive(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "archive"
    anchor = _anchor(tmp_path)
    _ingest(_write_source(tmp_path / "source", _obs()), root, anchor)
    return root, anchor


def _force_temp_cleanup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    real_unlink = Path.unlink

    def fail_operation_temp(path: Path, *args: Any, **kwargs: Any) -> None:
        if ".tmp." in path.name:
            raise OSError("temporary cleanup sentinel")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_operation_temp)


def test_anchor_contract_and_live_capability(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    anchor = _anchor(tmp_path)

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    ) as lease:
        assert lease.contract_version == "retirement_archive_coordination_v1"
        assert lease.lock_mode == coord.LOCK_MODE_SHARED
        assert lease.active is True and lease.closed is False
        assert "inode" not in repr(lease)
        assert "device" not in repr(lease)
        assert "anchor" not in repr(lease)
        lease.validate(expected_mode=coord.LOCK_MODE_SHARED)

    assert lease.active is False and lease.closed is True
    with pytest.raises(coord.CoordinationError) as raised:
        lease.validate(expected_mode=coord.LOCK_MODE_SHARED)
    assert raised.value.token == coord.TOKEN_LEASE_INVALID


def test_fabricated_or_wrong_mode_capability_cannot_validate(tmp_path: Path) -> None:
    with pytest.raises((coord.CoordinationError, TypeError)):
        coord.VerifiedCoordinationLease(  # type: ignore[call-arg]
            object(),
            fd=-1,
            mode=coord.LOCK_MODE_SHARED,
            identity=object(),
            anchor_path=tmp_path / "anchor",
        )

    with pytest.raises(TypeError):
        class ForgedLease(coord.VerifiedCoordinationLease):
            pass

    for fake in (Mock(spec=coord.VerifiedCoordinationLease), object(), True, "shared"):
        with pytest.raises(coord.CoordinationError):
            coord.validate_coordination_lease(fake)
    forged = object.__new__(coord.VerifiedCoordinationLease)
    with pytest.raises(coord.CoordinationError):
        coord.validate_coordination_lease(forged)
    assert "inode" not in repr(forged)
    assert "device" not in repr(forged)

    anchor = _anchor(tmp_path)
    with coord.acquire_coordination_lease(
        anchor, archive_root=tmp_path / "archive", mode=coord.LOCK_MODE_SHARED
    ) as lease:
        with pytest.raises(coord.CoordinationError) as wrong_mode:
            coord.validate_coordination_lease(
                lease, expected_mode=coord.LOCK_MODE_EXCLUSIVE
            )
        assert wrong_mode.value.token == coord.TOKEN_LEASE_INVALID
        for operation in (
            lambda: copy.copy(lease),
            lambda: copy.deepcopy(lease),
            lambda: pickle.dumps(lease),
        ):
            with pytest.raises(TypeError):
                operation()


def test_registry_records_and_lease_bindings_resist_reconstruction_and_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    anchor = _anchor(tmp_path)
    lease = coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    )
    assert not hasattr(coord, "_REGISTRY")
    assert not hasattr(coord, "_LeaseRecord")
    assert not hasattr(coord, "_exact_record")
    assert not hasattr(coord, "_make_lease_manager")
    for callback_name in (
        "_before_fork",
        "_after_fork_parent",
        "_after_fork_child",
        "before_fork",
        "after_fork_parent",
        "after_fork_child",
    ):
        assert not hasattr(coord, callback_name)
    snapshot = coord._coordination_status_snapshot(lease)
    for sensitive in ("fd", "inode", "device", "pid", "nonce", "anchor", str(anchor)):
        assert sensitive not in repr(snapshot).lower()
    with pytest.raises(FrozenInstanceError):
        snapshot.state = "active"  # type: ignore[misc]
    detached_replacement = replace(snapshot, state="complete")
    with pytest.raises(coord.CoordinationError):
        coord.validate_coordination_lease(detached_replacement)
    coord.validate_coordination_lease(lease)

    private_fields = (
        ("_VerifiedCoordinationLease__archive_root", tmp_path / "other"),
        ("_VerifiedCoordinationLease__pid", os.getpid() + 1),
        ("_VerifiedCoordinationLease__operation_nonce", object()),
        ("_VerifiedCoordinationLease__state", "complete"),
        ("_VerifiedCoordinationLease__contract_version", "other_v9"),
        ("_VerifiedCoordinationLease__construction_token", object()),
        ("_VerifiedCoordinationLease__record_token", object()),
    )
    for name, value in private_fields:
        original = object.__getattribute__(lease, name)
        object.__setattr__(lease, name, value)
        with pytest.raises(coord.CoordinationError):
            coord.validate_coordination_lease(lease)
        object.__setattr__(lease, name, original)

    owned_fd = object.__getattribute__(lease, "_VerifiedCoordinationLease__fd")
    duplicate_fd = os.dup(owned_fd)
    try:
        object.__setattr__(lease, "_VerifiedCoordinationLease__fd", duplicate_fd)
        with pytest.raises(coord.CoordinationError):
            coord.validate_coordination_lease(lease)
        object.__setattr__(lease, "_VerifiedCoordinationLease__fd", owned_fd)
    finally:
        os.close(duplicate_fd)

    lease.close()
    with pytest.raises(coord.CoordinationError):
        coord.validate_coordination_lease(lease)
    with pytest.raises(coord.CoordinationError):
        coord._coordination_status_snapshot(lease)


def test_capability_is_root_bound_one_use_and_fork_safe(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    other = tmp_path / "other-archive"
    anchor = _anchor(tmp_path)
    lease = coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    )
    try:
        coord.begin_coordination_operation(
            lease, archive_root=root, expected_mode=coord.LOCK_MODE_SHARED
        )
        assert (
            coord._coordination_operation_identity(
                lease,
                archive_root=root,
                expected_mode=coord.LOCK_MODE_SHARED,
            )
            is lease
        )
        with pytest.raises(coord.CoordinationError):
            coord.validate_coordination_operation(
                lease, archive_root=other, expected_mode=coord.LOCK_MODE_SHARED
            )

        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:  # pragma: no branch - child exits immediately
            os.close(read_fd)
            try:
                lease.close()
                try:
                    coord.validate_coordination_lease(lease)
                except coord.CoordinationError:
                    os.write(write_fd, b"invalid")
                else:
                    os.write(write_fd, b"valid")
            finally:
                os.close(write_fd)
                os._exit(0)
        os.close(write_fd)
        assert os.read(read_fd, 16) == b"invalid"
        os.close(read_fd)
        os.waitpid(child, 0)
        coord.validate_coordination_operation(
            lease, archive_root=root, expected_mode=coord.LOCK_MODE_SHARED
        )
        with pytest.raises(coord.CoordinationError) as contended:
            coord.acquire_coordination_lease(
                anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
            )
        assert contended.value.token == coord.TOKEN_CONTENDED
        coord.complete_coordination_operation(
            lease, archive_root=root, expected_mode=coord.LOCK_MODE_SHARED
        )
        with pytest.raises(coord.CoordinationError):
            coord.begin_coordination_operation(
                lease, archive_root=root, expected_mode=coord.LOCK_MODE_SHARED
            )
    finally:
        lease.close()


def test_coordination_descriptor_is_close_on_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = _anchor(tmp_path)
    opened: list[int] = []
    real_open = coord.os.open

    def capture_open(path: Any, flags: int) -> int:
        descriptor = real_open(path, flags)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(coord.os, "open", capture_open)
    with coord.acquire_coordination_lease(
        anchor,
        archive_root=tmp_path / "archive",
        mode=coord.LOCK_MODE_SHARED,
    ):
        assert opened
        assert os.get_inheritable(opened[-1]) is False


def test_fork_during_acquisition_closes_pending_child_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = _anchor(tmp_path)
    root = tmp_path / "archive"
    assert coord.fcntl is not None
    real_flock = coord.fcntl.flock
    read_fd, write_fd = os.pipe()
    forked = {"done": False}

    def fork_after_lock(fd: int, operation: int) -> None:
        real_flock(fd, operation)
        if not forked["done"] and operation & coord.fcntl.LOCK_NB:
            forked["done"] = True
            child = os.fork()
            if child == 0:  # pragma: no branch - child exits immediately
                os.close(read_fd)
                try:
                    try:
                        coord.acquire_coordination_lease(
                            anchor,
                            archive_root=root,
                            mode=coord.LOCK_MODE_EXCLUSIVE,
                        )
                    except coord.CoordinationError as exc:
                        os.write(write_fd, exc.token.encode("ascii"))
                    else:
                        os.write(write_fd, b"unexpected_success")
                finally:
                    os.close(write_fd)
                    os._exit(0)

    monkeypatch.setattr(coord.fcntl, "flock", fork_after_lock)
    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    ):
        os.close(write_fd)
        assert os.read(read_fd, 64) == coord.TOKEN_CONTENDED.encode("ascii")
        os.close(read_fd)
        os.wait()

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
    ) as released:
        released.validate(expected_mode=coord.LOCK_MODE_EXCLUSIVE)


def test_fork_child_resuming_acquisition_gets_canonical_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = _anchor(tmp_path)
    root = tmp_path / "archive"
    real_open = coord.os.open
    read_fd, write_fd = os.pipe()
    child_pid: int | None = None
    child_branch = False
    forked = False

    def fork_inside_open(path: Any, flags: int) -> int:
        nonlocal child_pid, child_branch, forked
        fd = real_open(path, flags)
        if os.fspath(path) == os.fspath(anchor) and not forked:
            forked = True
            pid = os.fork()
            if pid == 0:  # pragma: no branch - child reports and exits below
                child_branch = True
                os.close(read_fd)
            else:
                child_pid = pid
        return fd

    monkeypatch.setattr(coord.os, "open", fork_inside_open)
    try:
        lease = coord.acquire_coordination_lease(
            anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
        )
    except coord.CoordinationError as exc:
        if child_branch:
            os.write(write_fd, exc.token.encode("ascii"))
            os.close(write_fd)
            os._exit(0)
        raise

    os.close(write_fd)
    assert os.read(read_fd, 128) == coord.TOKEN_INTERRUPTED.encode("ascii")
    os.close(read_fd)
    assert child_pid is not None
    os.waitpid(child_pid, 0)
    lease.close()


def test_fork_after_lock_cannot_retain_lock_past_owner_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = _anchor(tmp_path)
    root = tmp_path / "archive"
    assert coord.fcntl is not None
    real_flock = coord.fcntl.flock
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    child_pid: int | None = None
    forked = False

    def fork_after_lock(fd: int, operation: int) -> None:
        nonlocal child_pid, forked
        real_flock(fd, operation)
        if not forked and operation & coord.fcntl.LOCK_NB:
            forked = True
            pid = os.fork()
            if pid == 0:  # pragma: no branch - child intentionally remains alive
                os.close(ready_read)
                os.close(release_write)
                os.write(ready_write, b"ready")
                os.read(release_read, 1)
                os._exit(0)
            child_pid = pid

    monkeypatch.setattr(coord.fcntl, "flock", fork_after_lock)
    lease = coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    )
    os.close(ready_write)
    os.close(release_read)
    assert os.read(ready_read, 5) == b"ready"
    lease.close()

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
    ) as reacquired:
        reacquired.validate(expected_mode=coord.LOCK_MODE_EXCLUSIVE)

    os.write(release_write, b"x")
    os.close(release_write)
    os.close(ready_read)
    assert child_pid is not None
    os.waitpid(child_pid, 0)


def test_fork_during_open_to_pending_registration_cannot_retain_parent_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = _anchor(tmp_path)
    root = tmp_path / "archive"
    real_open = coord.os.open
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    child_pid: int | None = None
    forked = False

    def fork_inside_open(path: Any, flags: int) -> int:
        nonlocal child_pid, forked
        fd = real_open(path, flags)
        if os.fspath(path) == os.fspath(anchor) and not forked:
            forked = True
            child_pid = os.fork()
            if child_pid == 0:  # pragma: no branch - child exits directly
                os.close(ready_read)
                os.close(release_write)
                os.write(ready_write, b"ready")
                os.read(release_read, 1)
                os._exit(0)
        return fd

    monkeypatch.setattr(coord.os, "open", fork_inside_open)
    lease = coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    )
    os.close(ready_write)
    os.close(release_read)
    assert os.read(ready_read, 5) == b"ready"
    lease.close()

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
    ) as reacquired:
        reacquired.validate(expected_mode=coord.LOCK_MODE_EXCLUSIVE)

    os.write(release_write, b"x")
    os.close(release_write)
    os.close(ready_read)
    assert child_pid is not None
    os.waitpid(child_pid, 0)


def test_fork_during_live_to_release_transition_invalidates_child_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = _anchor(tmp_path)
    root = tmp_path / "archive"
    assert coord.fcntl is not None
    real_flock = coord.fcntl.flock
    read_fd, write_fd = os.pipe()
    child_pid: int | None = None

    lease = coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    )

    def fork_before_unlock(fd: int, operation: int) -> None:
        nonlocal child_pid
        if operation == coord.fcntl.LOCK_UN and child_pid is None:
            child_pid = os.fork()
            if child_pid == 0:  # pragma: no branch - child exits directly
                os.close(read_fd)
                try:
                    coord.validate_coordination_lease(lease)
                except coord.CoordinationError:
                    os.write(write_fd, b"invalid")
                else:
                    os.write(write_fd, b"valid")
                os._exit(0)
        real_flock(fd, operation)

    monkeypatch.setattr(coord.fcntl, "flock", fork_before_unlock)
    lease.close()
    os.close(write_fd)
    assert os.read(read_fd, 16) == b"invalid"
    os.close(read_fd)
    assert child_pid is not None
    os.waitpid(child_pid, 0)

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
    ) as reacquired:
        reacquired.validate(expected_mode=coord.LOCK_MODE_EXCLUSIVE)


def test_fake_writer_capability_and_archive_resident_source_fail_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    tracker = ingest_mod._PublicationTracker()
    with pytest.raises(coord.CoordinationError):
        ingest_mod._ensure_layout(
            root,
            lease=Mock(),
            tracker=tracker,
            mutation_operation=object(),
        )
    assert not root.exists()

    anchor = _anchor(tmp_path)
    other = tmp_path / "other-archive"
    real_tracker = ingest_mod._PublicationTracker()
    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
    ) as lease:
        coord.begin_coordination_operation(
            lease, archive_root=root, expected_mode=coord.LOCK_MODE_EXCLUSIVE
        )
        with pytest.raises(coord.CoordinationError):
            ingest_mod._ensure_layout(
                other,
                lease=lease,
                tracker=real_tracker,
                mutation_operation=object(),
            )
    assert not other.exists()

    root.mkdir()
    source = _write_source(root / "source", _obs())
    with pytest.raises(ingest_mod.ArchiveIngestionError) as raised:
        _ingest(source, root, anchor)
    assert raised.value.token == "source_inside_archive_root"

    resolved_source = tmp_path / "resolved-source-link.json"
    resolved_source.symlink_to(source)
    with pytest.raises(ingest_mod.ArchiveIngestionError) as resolved:
        _ingest(resolved_source, root, anchor)
    assert resolved.value.token == "source_inside_archive_root"


@pytest.mark.parametrize("kind", ("symlink", "dangling", "directory", "fifo"))
def test_unsafe_existing_layout_entries_fail_before_partition_or_record_mutation(
    tmp_path: Path, kind: str
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    layout = root / c.ARCHIVE_LAYOUT_VERSION_FILENAME
    if kind == "symlink":
        outside = tmp_path / "outside-layout"
        outside.write_text(c.ARCHIVE_LAYOUT_VERSION + "\n", encoding="utf-8")
        layout.symlink_to(outside)
    elif kind == "dangling":
        layout.symlink_to(tmp_path / "missing-layout")
    elif kind == "directory":
        layout.mkdir()
    else:
        os.mkfifo(layout)
    anchor = _anchor(tmp_path)
    source = _write_source(tmp_path / "source", _obs())

    with pytest.raises(ingest_mod.ArchiveLayoutError) as raised:
        _ingest(source, root, anchor)

    assert raised.value.token == "archive_layout_entry_unsafe"
    assert not any((root / partition).exists() for partition in c.PARTITIONS)


def test_unsafe_partition_and_arbitrary_private_target_fail_before_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    (root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).write_text(
        c.ARCHIVE_LAYOUT_VERSION + "\n", encoding="utf-8"
    )
    outside = tmp_path / "outside-partition"
    outside.mkdir()
    (root / c.PARTITION_ACCEPTED).symlink_to(outside, target_is_directory=True)
    for partition in (c.PARTITION_QUARANTINED, c.PARTITION_REJECTED):
        (root / partition).mkdir()
    anchor = _anchor(tmp_path)
    source = _write_source(tmp_path / "source", _obs())

    with pytest.raises(ingest_mod.ArchiveLayoutError) as unsafe:
        _ingest(source, root, anchor)
    assert unsafe.value.token == "archive_partition_unsafe"
    assert list(outside.iterdir()) == []

    guarded_root, guarded_anchor = _initialized_archive(tmp_path / "guarded")
    tracker = ingest_mod._PublicationTracker()
    arbitrary = guarded_root / c.PARTITION_ACCEPTED / "caller-selected.json"
    assert not hasattr(ingest_mod, "_exclusive_write_text")
    assert not hasattr(ingest_mod, "_exclusive_write_json")
    assert not hasattr(ingest_mod, "_make_archive_mutation_api")
    assert not hasattr(ingest_mod, "_PublicationDescriptor")
    assert not hasattr(ingest_mod, "_run_ingestion_under_exclusive_lease")
    assert "_operation_runner" not in inspect.signature(
        ingest_mod.ingest_observation
    ).parameters
    with coord.acquire_coordination_lease(
        guarded_anchor,
        archive_root=guarded_root,
        mode=coord.LOCK_MODE_EXCLUSIVE,
    ) as lease:
        coord.begin_coordination_operation(
            lease,
            archive_root=guarded_root,
            expected_mode=coord.LOCK_MODE_EXCLUSIVE,
        )
        with pytest.raises(TypeError):
            ingest_mod._publish_canonical_record(
                {},
                target=arbitrary,
                archive_root=guarded_root,
                lease=lease,
                tracker=tracker,
                mutation_operation=object(),
            )
        with pytest.raises(TypeError):
            ingest_mod._publish_canonical_layout(
                archive_root=guarded_root,
                lease=lease,
                tracker=tracker,
                mutation_operation=object(),
                text="caller selected\n",
            )
        with pytest.raises(coord.CoordinationError):
            ingest_mod._publish_canonical_record(
                {
                    "archive_record_schema_version": c.ARCHIVE_LAYOUT_VERSION,
                },
                archive_root=guarded_root,
                lease=lease,
                tracker=tracker,
                mutation_operation=object(),
            )
    assert not arbitrary.exists()


def test_partial_root_initialization_failure_is_never_reported_as_no_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "parent" / "archive"
    root.parent.mkdir()
    anchor = _anchor(tmp_path)
    source = _write_source(tmp_path / "source", _obs())
    real_mkdir = Path.mkdir

    def create_then_fail(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == root:
            real_mkdir(path)
            raise OSError("partial initialization sentinel")
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", create_then_fail)
    with pytest.raises(IndeterminatePostPublicationError) as raised:
        _ingest(source, root, anchor)

    assert root.is_dir()
    assert raised.value.mutation_state == ingest_mod.PUBLICATION_INITIALIZATION_STARTED
    assert raised.value.layout_published is False
    assert raised.value.record_published is False
    assert "sentinel" not in str(raised.value)


def test_layout_publication_is_distinct_when_partition_creation_later_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"
    anchor = _anchor(tmp_path)
    source = _write_source(tmp_path / "source", _obs())
    real_mkdir = ingest_mod.os.mkdir

    def fail_first_partition(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path) == root / c.PARTITION_ACCEPTED:
            raise OSError("partition failure sentinel")
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(ingest_mod.os, "mkdir", fail_first_partition)
    with pytest.raises(IndeterminatePostPublicationError) as raised:
        _ingest(source, root, anchor)

    assert raised.value.mutation_state == ingest_mod.PUBLICATION_LAYOUT_PUBLISHED
    assert raised.value.layout_published is True
    assert raised.value.record_published is False
    assert (root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).is_file()


def test_ingestion_cli_reports_code_owned_mutation_state_without_nothing_written_claim(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "archive"
    anchor = _anchor(tmp_path)
    source = _write_source(tmp_path / "source", _obs())

    def fail_result(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("raw result sentinel")

    monkeypatch.setattr(ingest_mod, "IngestResult", fail_result)
    code = cli.main(
        [
            "--source",
            str(source),
            "--dest",
            str(root),
            "--coordination-file",
            str(anchor),
        ]
    )

    error = json.loads(capsys.readouterr().err)
    assert code == 2
    assert error == {
        "error": "archive_ingestion_indeterminate_post_publication",
        "token": "coordination_indeterminate_post_publication",
        "coordination_token": "archive_visible_mutation_indeterminate",
        "mutation_state": ingest_mod.PUBLICATION_RECORD_PUBLISHED,
        "layout_published": True,
        "record_published": True,
        "cleanup_incomplete": False,
    }


def test_result_construction_failure_after_link_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"
    anchor = _anchor(tmp_path)
    source = _write_source(tmp_path / "source", _obs())

    def fail_result(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("raw result construction sentinel")

    monkeypatch.setattr(ingest_mod, "IngestResult", fail_result)
    with pytest.raises(IndeterminatePostPublicationError) as raised:
        _ingest(source, root, anchor)
    assert raised.value.token == "coordination_indeterminate_post_publication"
    assert raised.value.mutation_state == ingest_mod.PUBLICATION_RECORD_PUBLISHED
    assert raised.value.record_published is True
    assert "sentinel" not in str(raised.value)
    assert list((root / c.PARTITION_ACCEPTED).glob("*.json"))


@pytest.mark.parametrize(
    ("setup", "token"),
    (
        ("missing", coord.TOKEN_MISSING),
        ("directory", coord.TOKEN_UNSAFE_TYPE),
        ("symlink", coord.TOKEN_UNSAFE_TYPE),
        ("fifo", coord.TOKEN_UNSAFE_TYPE),
        ("incompatible", coord.TOKEN_INCOMPATIBLE_CONTRACT),
        ("oversize", coord.TOKEN_INCOMPATIBLE_CONTRACT),
        ("hardlink", coord.TOKEN_UNSAFE_TYPE),
    ),
)
def test_anchor_failures_are_canonical_and_path_free(
    tmp_path: Path, setup: str, token: str
) -> None:
    anchor = tmp_path / "anchor"
    if setup == "directory":
        anchor.mkdir()
    elif setup == "symlink":
        target = _anchor(tmp_path, "target")
        anchor.symlink_to(target)
    elif setup == "fifo":
        os.mkfifo(anchor)
    elif setup == "incompatible":
        anchor.write_bytes(b"other_contract_v9\n")
    elif setup == "oversize":
        anchor.write_bytes(b"x" * (coord.COORDINATION_ANCHOR_MAX_BYTES + 1))
    elif setup == "hardlink":
        target = _anchor(tmp_path, "target")
        os.link(target, anchor)

    with pytest.raises(coord.CoordinationError) as raised:
        coord.acquire_coordination_lease(
            anchor, archive_root=tmp_path / "archive", mode=coord.LOCK_MODE_SHARED
        )
    assert raised.value.token == token
    assert str(tmp_path) not in str(raised.value)


def test_omitted_invalid_and_inside_archive_paths_fail_closed(tmp_path: Path) -> None:
    anchor = _anchor(tmp_path)
    cases: tuple[tuple[Any, Path, str], ...] = (
        (None, tmp_path / "archive", coord.TOKEN_PATH_OMITTED),
        ("", tmp_path / "archive", coord.TOKEN_PATH_INVALID),
        (anchor, tmp_path, coord.TOKEN_PATH_INSIDE_ARCHIVE),
    )
    for path, root, token in cases:
        with pytest.raises(coord.CoordinationError) as raised:
            coord.acquire_coordination_lease(
                path, archive_root=root, mode=coord.LOCK_MODE_SHARED
            )
        assert raised.value.token == token


def test_ingestion_library_and_both_clis_require_explicit_coordination(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "archive"
    source = _write_source(tmp_path / "source", _obs())
    with pytest.raises(coord.CoordinationError) as library:
        ingest_observation(source_path=source, dest_root=root)
    assert library.value.token == coord.TOKEN_PATH_OMITTED
    assert not root.exists()

    assert cli.main(["--source", str(source), "--dest", str(root)]) == 2
    ingest_error = json.loads(capsys.readouterr().err)
    assert ingest_error == {
        "error": "archive_coordination_error",
        "token": coord.TOKEN_PATH_OMITTED,
    }
    assert verify_cli.main(["--archive-root", str(root)]) == 2
    verify_error = json.loads(capsys.readouterr().err)
    assert verify_error == ingest_error

    assert (
        verify_cli.main(
            [
                "--archive-root",
                str(root),
                "--coordination-file",
                str(tmp_path / "missing.anchor"),
                "--output",
                str(tmp_path / "report.json"),
            ]
        )
        == 2
    )
    verify_output_error = json.loads(capsys.readouterr().err)
    assert verify_output_error == {
        "error": "archive_coordination_error",
        "token": coord.TOKEN_MISSING,
    }


def test_unreadable_unsupported_and_interrupted_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = _anchor(tmp_path)
    root = tmp_path / "archive"
    real_open = coord.os.open

    def denied(path: Any, flags: int) -> int:
        if os.fspath(path) == os.fspath(anchor):
            raise PermissionError(errno.EACCES, "raw denied", str(anchor))
        return real_open(path, flags)

    monkeypatch.setattr(coord.os, "open", denied)
    with pytest.raises(coord.CoordinationError) as unreadable:
        coord.acquire_coordination_lease(anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED)
    assert unreadable.value.token == coord.TOKEN_UNREADABLE
    monkeypatch.setattr(coord.os, "open", real_open)

    real_fcntl = coord.fcntl
    monkeypatch.setattr(coord, "fcntl", None)
    with pytest.raises(coord.CoordinationError) as unsupported:
        coord.acquire_coordination_lease(anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED)
    assert unsupported.value.token == coord.TOKEN_UNSUPPORTED
    monkeypatch.setattr(coord, "fcntl", real_fcntl)

    assert real_fcntl is not None
    real_flock = real_fcntl.flock
    monkeypatch.setattr(real_fcntl, "flock", lambda *_args: (_ for _ in ()).throw(InterruptedError()))
    with pytest.raises(coord.CoordinationError) as interrupted:
        coord.acquire_coordination_lease(anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED)
    assert interrupted.value.token == coord.TOKEN_INTERRUPTED
    monkeypatch.setattr(real_fcntl, "flock", real_flock)


def test_platform_without_required_fork_descriptor_inventory_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = _anchor(tmp_path)
    real_is_dir = coord.Path.is_dir

    def unsupported(path: Path) -> bool:
        if os.fspath(path) == "/proc/self/fd":
            return False
        return real_is_dir(path)

    monkeypatch.setattr(coord.Path, "is_dir", unsupported)
    with pytest.raises(coord.CoordinationError) as raised:
        coord.acquire_coordination_lease(
            anchor,
            archive_root=tmp_path / "archive",
            mode=coord.LOCK_MODE_SHARED,
        )
    assert raised.value.token == coord.TOKEN_UNSUPPORTED


def test_shared_coexists_and_exclusive_contention_is_immediate(tmp_path: Path) -> None:
    anchor = _anchor(tmp_path)
    root = tmp_path / "archive"
    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    ) as first:
        with coord.acquire_coordination_lease(
            anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
        ) as second:
            first.validate(expected_mode=coord.LOCK_MODE_SHARED)
            second.validate(expected_mode=coord.LOCK_MODE_SHARED)
            with pytest.raises(coord.CoordinationError) as contended:
                coord.acquire_coordination_lease(
                    anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
                )
            assert contended.value.token == coord.TOKEN_CONTENDED


def test_writer_excludes_writer_and_scanner_excludes_initialization_then_retry(
    tmp_path: Path,
) -> None:
    anchor = _anchor(tmp_path)
    root = tmp_path / "archive"
    source = _write_source(tmp_path / "source", _obs())

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
    ):
        with pytest.raises(coord.CoordinationError) as writer_contended:
            _ingest(source, root, anchor)
        assert writer_contended.value.token == coord.TOKEN_CONTENDED
        assert not root.exists()

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    ):
        with pytest.raises(coord.CoordinationError) as scanner_contended:
            _ingest(source, root, anchor)
        assert scanner_contended.value.token == coord.TOKEN_CONTENDED
        assert not root.exists()

    result = _ingest(source, root, anchor)
    assert result.decision == c.DECISION_ACCEPTED
    assert Path(result.archived_path).exists()


def test_scanner_shared_lock_blocks_publication_and_allows_explicit_retry(tmp_path: Path) -> None:
    root, anchor = _initialized_archive(tmp_path)
    source = tmp_path / "second-source" / "step1a_retirement_observation.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"{ not valid json ")
    before = sorted(path.relative_to(root) for path in root.rglob("*"))

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    ):
        with pytest.raises(coord.CoordinationError) as raised:
            _ingest(source, root, anchor)
        assert raised.value.token == coord.TOKEN_CONTENDED
        assert sorted(path.relative_to(root) for path in root.rglob("*")) == before

    retried = _ingest(source, root, anchor)
    assert retried.decision == c.DECISION_REJECTED
    assert Path(retried.archived_path).exists()


def test_index_acquires_before_root_observation_and_holds_through_report_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, anchor = _initialized_archive(tmp_path)
    real_resolve = scan._resolve_root
    real_hash = idx.compute_report_content_sha256
    observed = {"resolve": False, "hash": False}

    def assert_writer_blocked() -> None:
        with pytest.raises(coord.CoordinationError) as raised:
            coord.acquire_coordination_lease(
                anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
            )
        assert raised.value.token == coord.TOKEN_CONTENDED

    def wrapped_resolve(*args: Any, **kwargs: Any):
        assert_writer_blocked()
        observed["resolve"] = True
        return real_resolve(*args, **kwargs)

    def wrapped_hash(report: Any):
        assert_writer_blocked()
        observed["hash"] = True
        return real_hash(report)

    monkeypatch.setattr(scan, "_resolve_root", wrapped_resolve)
    monkeypatch.setattr(idx, "compute_report_content_sha256", wrapped_hash)
    report = idx.index_archive(root, coordination_path=anchor)

    assert observed == {"resolve": True, "hash": True}
    assert report["archive_assessment_state"] == idx.ASSESSMENT_CLEAN
    assert report["repository_writer_quiescence_verified"] is True


def test_clean_gate_rejects_missing_closed_and_direct_builder_bypasses(tmp_path: Path) -> None:
    root, anchor = _initialized_archive(tmp_path)

    missing = idx.index_archive(root)
    assert missing["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert missing["repository_writer_quiescence_verified"] is False
    assert coord.TOKEN_PATH_OMITTED in missing["assessment_reason_tokens"]["unverifiable"]

    with pytest.raises(coord.CoordinationError) as direct_scan:
        scan.scan_archive(root)
    assert direct_scan.value.token == coord.TOKEN_LEASE_INVALID

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    ) as lease:
        scanned = scan.scan_archive(root, lease=lease)
        fabricated_scan = replace(scanned)
        fabricated = idx.build_archive_index(fabricated_scan, lease=lease)
        assert fabricated["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
        assert fabricated["repository_writer_quiescence_verified"] is False
    direct = idx.build_archive_index(scanned, lease=lease)
    assert direct["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert direct["repository_writer_quiescence_verified"] is False
    assert coord.TOKEN_LEASE_INVALID in direct["assessment_reason_tokens"]["unverifiable"]


def test_anchor_identity_replacement_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    anchor = _anchor(tmp_path)
    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_SHARED
    ) as lease:
        old = tmp_path / "old-anchor"
        anchor.rename(old)
        anchor.write_bytes(coord.COORDINATION_ANCHOR_BYTES)
        with pytest.raises(coord.CoordinationError) as changed:
            lease.validate(expected_mode=coord.LOCK_MODE_SHARED)
        assert changed.value.token == coord.TOKEN_IDENTITY_CHANGED


def test_anchor_change_before_link_aborts_and_after_link_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive-before"
    anchor = _anchor(tmp_path, "before.anchor")
    source = _write_source(tmp_path / "source-before", _obs())
    real_fsync = ingest_mod.os.fsync
    changed = {"done": False}

    def replace_during_fsync(fd: int) -> None:
        real_fsync(fd)
        if not changed["done"]:
            changed["done"] = True
            anchor.rename(tmp_path / "before-old.anchor")
            anchor.write_bytes(coord.COORDINATION_ANCHOR_BYTES)

    monkeypatch.setattr(ingest_mod.os, "fsync", replace_during_fsync)
    with pytest.raises(IndeterminatePostPublicationError) as before:
        _ingest(source, root, anchor)
    assert before.value.coordination_token == coord.TOKEN_IDENTITY_CHANGED
    assert not (root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).exists()

    monkeypatch.setattr(ingest_mod.os, "fsync", real_fsync)
    post_root = tmp_path / "archive-after"
    post_anchor = _anchor(tmp_path, "after.anchor")
    post_source = _write_source(tmp_path / "source-after", _obs())
    real_link = ingest_mod.os.link
    linked = {"done": False}

    def replace_after_link(src: Any, dst: Any) -> None:
        real_link(src, dst)
        if not linked["done"]:
            linked["done"] = True
            post_anchor.rename(tmp_path / "after-old.anchor")
            post_anchor.write_bytes(coord.COORDINATION_ANCHOR_BYTES)

    monkeypatch.setattr(ingest_mod.os, "link", replace_after_link)
    with pytest.raises(IndeterminatePostPublicationError) as after:
        _ingest(post_source, post_root, post_anchor)
    assert after.value.token == "coordination_indeterminate_post_publication"
    assert after.value.coordination_token == coord.TOKEN_IDENTITY_CHANGED
    assert (post_root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).exists()


def test_cleanup_helper_requires_operation_temp_authority(tmp_path: Path) -> None:
    root, anchor = _initialized_archive(tmp_path)
    unrelated = root / c.PARTITION_ACCEPTED / ".record.json.tmp.caller"
    unrelated.write_text("leave me alone\n", encoding="utf-8")
    tracker = ingest_mod._PublicationTracker()
    assert not hasattr(tracker, "temporary_paths")
    assert not hasattr(ingest_mod, "_unlink_operation_temp")

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
    ) as lease:
        coord.begin_coordination_operation(
            lease, archive_root=root, expected_mode=coord.LOCK_MODE_EXCLUSIVE
        )
        with pytest.raises(coord.CoordinationError):
            ingest_mod._publish_canonical_record(
                {},
                archive_root=root,
                lease=lease,
                tracker=tracker,
                mutation_operation=object(),
            )
    assert unrelated.read_text(encoding="utf-8") == "leave me alone\n"


def test_operation_cleanup_never_consumes_other_root_or_partition_files(
    tmp_path: Path,
) -> None:
    root, anchor = _initialized_archive(tmp_path / "one")
    other_root, _other_anchor = _initialized_archive(tmp_path / "two")
    retained = (
        root / c.PARTITION_ACCEPTED / ".record.json.tmp.other-operation",
        root / c.PARTITION_REJECTED / ".record.json.tmp.other-partition",
        other_root / c.PARTITION_ACCEPTED / ".record.json.tmp.other-root",
    )
    for path in retained:
        path.write_text("retain\n", encoding="utf-8")

    source = _write_source(
        tmp_path / "one" / "new-source",
        _obs(generated_at="2026-07-10T15:00:00+00:00"),
    )
    _ingest(source, root, anchor)

    assert all(path.read_text(encoding="utf-8") == "retain\n" for path in retained)


def test_temp_identity_substitution_is_not_removed_or_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, anchor = _initialized_archive(tmp_path)
    source = _write_source(
        tmp_path / "identity-substitution-source",
        _obs(generated_at="2026-07-10T15:01:00+00:00"),
    )
    real_link = ingest_mod.os.link
    substituted: list[Path] = []

    def link_then_substitute(src: Any, dst: Any) -> None:
        real_link(src, dst)
        temp = Path(src)
        temp.unlink()
        temp.write_text("replacement sentinel\n", encoding="utf-8")
        substituted.append(temp)

    monkeypatch.setattr(ingest_mod.os, "link", link_then_substitute)
    with pytest.raises(IndeterminatePostPublicationError) as raised:
        _ingest(source, root, anchor)

    assert raised.value.record_published is True
    assert raised.value.cleanup_incomplete is True
    assert raised.value.mutation_state == ingest_mod.PUBLICATION_CLEANUP_INCOMPLETE
    assert substituted
    assert substituted[0].read_text(encoding="utf-8") == "replacement sentinel\n"
    assert "sentinel" not in str(raised.value)


def test_canonical_temp_cleanup_consumes_only_its_registered_file(tmp_path: Path) -> None:
    root, anchor = _initialized_archive(tmp_path)
    source = _write_source(
        tmp_path / "canonical-cleanup-source",
        _obs(generated_at="2026-07-10T15:02:00+00:00"),
    )

    _ingest(source, root, anchor)

    assert not tuple(path for path in root.rglob("*") if ".tmp." in path.name)


def test_duplicate_temp_cleanup_failure_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, anchor = _initialized_archive(tmp_path)
    source = _write_source(tmp_path / "duplicate-source", _obs())

    _force_temp_cleanup_failure(monkeypatch)

    with pytest.raises(IndeterminatePostPublicationError) as raised:
        _ingest(source, root, anchor)

    assert raised.value.token == "coordination_indeterminate_post_publication"
    assert raised.value.coordination_token == "archive_visible_mutation_indeterminate"
    assert raised.value.mutation_state == ingest_mod.PUBLICATION_CLEANUP_INCOMPLETE
    assert raised.value.record_published is False


@pytest.mark.parametrize("kind", ("layout", "accepted", "rejected"))
def test_cleanup_incomplete_never_returns_success_for_any_publication_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    if kind == "layout":
        root = tmp_path / "archive"
        anchor = _anchor(tmp_path)
        source = _write_source(tmp_path / "source", _obs())
    else:
        root, anchor = _initialized_archive(tmp_path)
        payload: Any = (
            _obs(generated_at="2026-07-10T13:00:00+00:00")
            if kind == "accepted"
            else b"{ not valid json "
        )
        if isinstance(payload, bytes):
            source = tmp_path / "source-after-init" / "source.json"
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
        else:
            source = _write_source(tmp_path / "source-after-init", payload)

    _force_temp_cleanup_failure(monkeypatch)
    with pytest.raises(IndeterminatePostPublicationError) as raised:
        _ingest(source, root, anchor)

    assert raised.value.mutation_state == ingest_mod.PUBLICATION_CLEANUP_INCOMPLETE
    assert raised.value.layout_published is (kind == "layout")
    assert raised.value.record_published is (kind != "layout")


def test_ambiguous_file_exists_after_link_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, anchor = _initialized_archive(tmp_path)
    source = _write_source(
        tmp_path / "second-source",
        _obs(generated_at="2026-07-10T13:00:00+00:00"),
    )
    real_link = ingest_mod.os.link

    def create_then_file_exists(src: Any, dst: Any) -> None:
        real_link(src, dst)
        raise FileExistsError(errno.EEXIST, "ambiguous", os.fspath(dst))

    monkeypatch.setattr(ingest_mod.os, "link", create_then_file_exists)

    with pytest.raises(IndeterminatePostPublicationError) as raised:
        _ingest(source, root, anchor)

    assert raised.value.token == "coordination_indeterminate_post_publication"
    assert raised.value.coordination_token == "archive_visible_mutation_indeterminate"
    assert raised.value.mutation_state == ingest_mod.PUBLICATION_OUTCOME_INDETERMINATE


@pytest.mark.parametrize("kind", ("accepted", "quarantined", "minimal", "rejected"))
def test_coordination_does_not_change_record_bytes_or_filenames(
    tmp_path: Path, kind: str
) -> None:
    if kind == "accepted":
        payload: Any = _obs()
    elif kind == "quarantined":
        packet = deepcopy(_builder_inputs()["evidence_packet"])
        packet.pop("strategy_settings_hash")
        payload = _obs(evidence_packet=packet)
    elif kind == "minimal":
        payload = _minimal_incomplete_observation("2026-07-10T12:00:00+00:00")
    else:
        payload = b"{ not valid json "

    records: list[tuple[str, bytes]] = []
    for label in ("one", "two"):
        base = tmp_path / label
        anchor = _anchor(base)
        root = base / "archive"
        source = base / "source.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            source.write_bytes(payload)
        else:
            source.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        result = _ingest(source, root, anchor)
        record = Path(result.archived_path)
        records.append((record.name, record.read_bytes()))
        assert b"coordination" not in record.read_bytes()
    assert records[0] == records[1]


@pytest.mark.parametrize("held_mode", (coord.LOCK_MODE_SHARED, coord.LOCK_MODE_EXCLUSIVE))
def test_writer_or_scanner_process_termination_releases_os_managed_lock(
    tmp_path: Path, held_mode: str
) -> None:
    anchor = _anchor(tmp_path)
    root = tmp_path / "archive"
    code = """
import sys, time
from pathlib import Path
from investment_orchestrator.offline.retirement_evidence import archive_coordination as c
lease = c.acquire_coordination_lease(Path(sys.argv[1]), archive_root=Path(sys.argv[2]), mode=sys.argv[3])
print('ready', flush=True)
time.sleep(60)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(anchor), str(root), held_mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(coord.CoordinationError) as held:
            coord.acquire_coordination_lease(
                anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
            )
        assert held.value.token == coord.TOKEN_CONTENDED
    finally:
        process.terminate()
        process.wait(timeout=10)

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
    ) as released:
        released.validate(expected_mode=coord.LOCK_MODE_EXCLUSIVE)


def test_owner_release_after_confirmed_child_cleanup_is_not_prolonged(
    tmp_path: Path,
) -> None:
    anchor = _anchor(tmp_path)
    root = tmp_path / "archive"
    code = """
import os, sys
from pathlib import Path
from investment_orchestrator.offline.retirement_evidence import archive_coordination as c
anchor, root = map(Path, sys.argv[1:3])
lease = c.acquire_coordination_lease(anchor, archive_root=root, mode=c.LOCK_MODE_SHARED)
ready_read, ready_write = os.pipe()
child = os.fork()
if child == 0:
    os.close(ready_read)
    try:
        c.validate_coordination_lease(lease)
    except c.CoordinationError:
        status = 'invalid'
    else:
        status = 'valid'
    print('child_cleanup_complete:' + status, flush=True)
    os.write(ready_write, b'x')
    sys.stdin.buffer.read(1)
    print('child_released', flush=True)
    os._exit(0)
os.close(ready_write)
os.read(ready_read, 1)
print('owner_exiting_after_child_cleanup', flush=True)
os._exit(0)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(anchor), str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "child_cleanup_complete:invalid"
    assert process.stdout.readline().strip() == "owner_exiting_after_child_cleanup"
    process.wait(timeout=10)

    with coord.acquire_coordination_lease(
        anchor, archive_root=root, mode=coord.LOCK_MODE_EXCLUSIVE
    ) as reacquired:
        reacquired.validate(expected_mode=coord.LOCK_MODE_EXCLUSIVE)

    assert process.stdin is not None
    process.stdin.write("x")
    process.stdin.flush()
    assert process.stdout.readline().strip() == "child_released"


def test_process_scanner_blocks_writer_while_another_scanner_coexists(
    tmp_path: Path,
) -> None:
    root, anchor = _initialized_archive(tmp_path)
    release = tmp_path / "release-scanner"
    code = """
import sys, time
from pathlib import Path
from investment_orchestrator.offline.retirement_evidence import archive_index as idx
root, anchor, release = map(Path, sys.argv[1:4])
real_hash = idx.compute_report_content_sha256
def blocked_hash(report):
    print('shared_lease_held_through_hash', flush=True)
    while not release.exists():
        time.sleep(0.01)
    return real_hash(report)
idx.compute_report_content_sha256 = blocked_hash
report = idx.index_archive(root, coordination_path=anchor)
print(report['archive_assessment_state'], flush=True)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(root), str(anchor), str(release)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )
    writer_source = tmp_path / "writer-source.json"
    writer_source.write_bytes(b"{ not valid json ")
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "shared_lease_held_through_hash"

        concurrent_scan = idx.index_archive(root, coordination_path=anchor)
        assert concurrent_scan["archive_assessment_state"] == idx.ASSESSMENT_CLEAN

        with pytest.raises(coord.CoordinationError) as writer:
            _ingest(writer_source, root, anchor)
        assert writer.value.token == coord.TOKEN_CONTENDED
        assert not any((root / c.PARTITION_REJECTED).iterdir())
    finally:
        release.write_text("release\n", encoding="utf-8")
        process.wait(timeout=10)

    assert process.returncode == 0
    assert process.stdout is not None
    assert process.stdout.readline().strip() == idx.ASSESSMENT_CLEAN
    retried = _ingest(writer_source, root, anchor)
    assert retried.decision == c.DECISION_REJECTED


def test_module_documentation_preserves_narrow_snapshot_and_cleanup_claims() -> None:
    assert coord.__doc__ is not None
    assert ingest_mod.__doc__ is not None
    assert scan.__doc__ is not None
    assert "after child cleanup and owner release" in coord.__doc__
    assert "transiently cause canonical nonblocking contention" in coord.__doc__
    assert "on any failure leaves no partial record visible" not in ingest_mod.__doc__
    assert "cleanup-incomplete or indeterminate" in ingest_mod.__doc__
    assert "remained consistent through the final bounded" not in scan.__doc__
    assert "not an atomic filesystem snapshot" in scan.__doc__
    assert "different-anchor mutation is not excluded" in scan.__doc__
