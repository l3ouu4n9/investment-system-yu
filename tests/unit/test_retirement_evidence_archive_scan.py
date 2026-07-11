"""Phase 2B-2 tests for the read-only deterministic archive scanner."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence import archive_coordination as coord
from investment_orchestrator.offline.retirement_evidence import archive_index as idx
from investment_orchestrator.offline.retirement_evidence import archive_record_contract as rc
from investment_orchestrator.offline.retirement_evidence import archive_scan as scan
from investment_orchestrator.offline.retirement_evidence.ingest import ingest_observation
from investment_orchestrator.research.step1a_retirement_observation import (
    build_step1a_retirement_observation,
)

from test_step1a_retirement_observation import _builder_inputs


_TOOL = {"tool_version": c.ARCHIVE_TOOL_VERSION, "tool_commit": "unavailable"}
_STAMP = "2026-07-10T00:00:00+00:00"


def _obs(**overrides: Any) -> dict[str, Any]:
    values = _builder_inputs()
    values.update(overrides)
    return build_step1a_retirement_observation(**values)


def _ingest_payload(root: Path, payload: Any, name: str = "step1a_retirement_observation.json"):
    source = root.parent / "sources" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (bytes, bytearray)):
        source.write_bytes(payload)
    else:
        source.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return ingest_observation(
        source_path=source,
        dest_root=root,
        coordination_path=_coordination_path(root),
        tool_identity=_TOOL,
        archived_at=_STAMP,
    )


def _coordination_path(root: Path) -> Path:
    anchor = root.parent / "retirement-archive-coordination.anchor"
    if not anchor.exists():
        anchor.write_bytes(coord.COORDINATION_ANCHOR_BYTES)
    return anchor


def _scan(root: Path, limits: scan.ScanLimits | None = None) -> scan.ArchiveScan:
    with coord.acquire_coordination_lease(
        _coordination_path(root), archive_root=root, mode=coord.LOCK_MODE_SHARED
    ) as lease:
        return scan.scan_archive(root, limits, lease=lease)


def _index(root: Path, limits: scan.ScanLimits | None = None) -> dict[str, Any]:
    return idx.index_archive(root, limits, coordination_path=_coordination_path(root))


def _archive(tmp_path: Path, *, rejected: bool = False) -> Path:
    root = tmp_path / "archive"
    _ingest_payload(root, _obs())
    if rejected:
        _ingest_payload(root, b"{ not valid json ")
    return root


def _candidates(result: scan.ArchiveScan) -> list[scan.ScannedEntry]:
    return [e for e in result.entries if e.entry_kind == scan.ENTRY_RECORD_CANDIDATE]


# --- name-convention drift guards against the shared Phase 2A derivations -----
def test_record_name_conventions_match_phase2a_filename_derivations() -> None:
    observation_name = rc.expected_observation_record_filename(
        {"observation_identity": {"generated_at": "2026-07-10T12:00:00+00:00"}},
        "a" * 64,
        "b" * 64,
    )
    fallback_name = rc.expected_observation_record_filename({}, None, "b" * 64)
    rejected_name = rc.expected_rejected_record_filename("c" * 64)

    assert scan.is_record_convention_basename(observation_name)
    assert scan.is_record_convention_basename(fallback_name)
    assert scan.is_record_convention_basename(rejected_name)
    assert rc.safe_record_basename(observation_name) == observation_name
    assert rc.safe_record_basename(fallback_name) == fallback_name


@pytest.mark.parametrize(
    "name",
    (
        "notes.txt",
        ".hidden",
        "record.json~",
        "invalid_archive_record_basename",
        "rejected__short__" + "a" * 64 + ".json",
        "rejected__" + "A" * 16 + "__" + "a" * 64 + ".json",
        b"not-a-string",
        None,
    ),
)
def test_non_record_names_are_not_candidates(name: Any) -> None:
    assert scan.is_record_convention_basename(name) is False


# --- valid archive -------------------------------------------------------------
def test_valid_archive_scans_stable_and_deterministic(tmp_path: Path) -> None:
    root = _archive(tmp_path, rejected=True)

    result = _scan(root)

    assert result.unverifiable_tokens == ()
    assert result.warning_tokens == ()
    assert result.layout_status == scan.LAYOUT_CANONICAL
    assert result.archive_layout_version == c.ARCHIVE_LAYOUT_VERSION
    candidates = _candidates(result)
    assert len(candidates) == 2
    assert all(e.stable_read_state == scan.READ_STABLE for e in candidates)
    assert all(e.final_revalidation_state == scan.REVALIDATION_STABLE for e in candidates)
    assert all(e.file_sha256 and e.byte_length and e.record_bytes for e in candidates)
    assert [e.reference for e in result.entries] == sorted(e.reference for e in result.entries)
    layout_bytes = len((root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).read_bytes())
    assert result.total_bytes_read == layout_bytes + 2 * sum(
        e.byte_length or 0 for e in candidates
    )
    assert result.direct_entry_count == 4 + 2  # layout + 3 partitions + 2 records
    assert result.entry_inventory_truncated is False


def test_explicit_root_symlink_is_resolved_once_and_allowed(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    link = tmp_path / "archive-link"
    link.symlink_to(root)

    result = _scan(link)

    assert result.unverifiable_tokens == ()
    assert len(_candidates(result)) == 1


def test_missing_root_is_unverifiable(tmp_path: Path) -> None:
    result = _scan(tmp_path / "does-not-exist")
    assert result.unverifiable_tokens == (scan.TOKEN_ARCHIVE_ROOT_MISSING,)
    assert result.entries == ()


def test_file_root_is_unverifiable(tmp_path: Path) -> None:
    target = tmp_path / "not-a-dir"
    target.write_text("x", encoding="utf-8")
    result = _scan(target)
    assert result.unverifiable_tokens == (scan.TOKEN_ARCHIVE_ROOT_NOT_DIRECTORY,)


# --- layout ---------------------------------------------------------------------
def test_layout_symlink_is_unsafe_and_stops_reads(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    layout = root / c.ARCHIVE_LAYOUT_VERSION_FILENAME
    copy = tmp_path / "layout-copy"
    copy.write_bytes(layout.read_bytes())
    layout.unlink()
    layout.symlink_to(copy)

    result = _scan(root)

    assert scan.TOKEN_LAYOUT_FILE_UNSAFE in result.unverifiable_tokens
    assert result.layout_status == scan.LAYOUT_UNSAFE
    assert all(
        e.stable_read_state == scan.READ_SKIPPED_UNVERIFIABLE for e in _candidates(result)
    )


def test_layout_missing_is_unverifiable(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    (root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).unlink()
    result = _scan(root)
    assert scan.TOKEN_LAYOUT_FILE_MISSING in result.unverifiable_tokens
    assert result.layout_status == scan.LAYOUT_MISSING
    assert result.archive_layout_version is None


def test_layout_noncanonical_whitespace_is_warning_only(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    (root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).write_text(
        "  " + c.ARCHIVE_LAYOUT_VERSION + "\n\n", encoding="utf-8"
    )

    result = _scan(root)

    assert result.unverifiable_tokens == ()
    assert scan.TOKEN_LAYOUT_NONCANONICAL in result.warning_tokens
    assert result.layout_status == scan.LAYOUT_NONCANONICAL_WHITESPACE
    assert result.archive_layout_version == c.ARCHIVE_LAYOUT_VERSION
    # Records are still read: compatibility warning, not unverifiability.
    assert all(e.stable_read_state == scan.READ_STABLE for e in _candidates(result))


def test_layout_incompatible_token_is_unverifiable(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    (root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).write_text("other_layout_v9\n", encoding="utf-8")
    result = _scan(root)
    assert scan.TOKEN_LAYOUT_VERSION_INCOMPATIBLE in result.unverifiable_tokens
    assert result.layout_status == scan.LAYOUT_INCOMPATIBLE
    assert result.archive_layout_version is None


def test_layout_invalid_utf8_is_unverifiable(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    (root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).write_bytes(b"\xff\xfe")
    result = _scan(root)
    assert scan.TOKEN_LAYOUT_FILE_NOT_UTF8 in result.unverifiable_tokens
    assert result.layout_status == scan.LAYOUT_NOT_UTF8


def test_layout_oversize_is_unverifiable(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    (root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).write_bytes(
        c.ARCHIVE_LAYOUT_VERSION.encode() + b" " * (scan.LAYOUT_FILE_MAX_BYTES + 8)
    )
    result = _scan(root)
    assert scan.TOKEN_LAYOUT_FILE_OVERSIZE in result.unverifiable_tokens
    assert result.layout_status == scan.LAYOUT_OVERSIZE


# --- partitions and entries -------------------------------------------------------
def test_missing_partition_is_unverifiable_and_stops_reads(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    (root / c.PARTITION_REJECTED).rmdir()

    result = _scan(root)

    assert scan.TOKEN_PARTITION_MISSING in result.unverifiable_tokens
    assert all(
        e.stable_read_state == scan.READ_SKIPPED_UNVERIFIABLE for e in _candidates(result)
    )


def test_partition_symlink_is_unsafe(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    real = tmp_path / "elsewhere"
    real.mkdir()
    (root / c.PARTITION_REJECTED).rmdir()
    (root / c.PARTITION_REJECTED).symlink_to(real)

    result = _scan(root)

    assert scan.TOKEN_PARTITION_UNSAFE in result.unverifiable_tokens


def test_record_symlink_is_never_opened(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    aside = tmp_path / "aside.json"
    aside.write_bytes(record.read_bytes())
    link_name = "1" + record.name  # still a conventional record name
    (root / c.PARTITION_ACCEPTED / link_name).symlink_to(aside)

    result = _scan(root)

    assert scan.TOKEN_UNSAFE_ARCHIVE_ENTRY in result.unverifiable_tokens
    symlinks = [e for e in result.entries if e.entry_kind == scan.ENTRY_SYMLINK]
    assert len(symlinks) == 1
    assert symlinks[0].file_sha256 is None
    assert symlinks[0].record_bytes is None
    # Entry-level anomaly: the remaining safe record is still read.
    assert any(e.stable_read_state == scan.READ_STABLE for e in _candidates(result))


def test_dangling_symlink_is_unsafe(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    (root / c.PARTITION_QUARANTINED / "dangling").symlink_to(tmp_path / "gone")
    result = _scan(root)
    assert scan.TOKEN_UNSAFE_ARCHIVE_ENTRY in result.unverifiable_tokens


def test_fifo_entry_is_unsafe(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    os.mkfifo(root / c.PARTITION_ACCEPTED / "pipe")
    result = _scan(root)
    assert scan.TOKEN_UNSAFE_ARCHIVE_ENTRY in result.unverifiable_tokens
    assert any(e.entry_kind == scan.ENTRY_FIFO for e in result.entries)


def test_nested_directory_is_unsafe(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    (root / c.PARTITION_ACCEPTED / "nested").mkdir()
    (root / c.PARTITION_ACCEPTED / "nested" / "hidden.json").write_text("{}", encoding="utf-8")

    result = _scan(root)

    assert scan.TOKEN_UNSAFE_ARCHIVE_ENTRY in result.unverifiable_tokens
    nested = [e for e in result.entries if e.entry_kind == scan.ENTRY_DIRECTORY]
    assert len(nested) == 1  # never recursed into


def test_only_root_regular_strays_are_unopened_warnings(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    (root / "stray_root_note.txt").write_text("x", encoding="utf-8")

    result = _scan(root)

    assert result.unverifiable_tokens == ()
    assert scan.TOKEN_UNEXPECTED_ARCHIVE_ENTRY in result.warning_tokens
    unexpected = [
        e for e in result.entries if e.entry_kind == scan.ENTRY_UNEXPECTED_REGULAR_FILE
    ]
    assert len(unexpected) == 1
    assert all(e.file_sha256 is None and e.record_bytes is None for e in unexpected)
    assert all(e.stable_read_state is None for e in unexpected)
    # The archive root is layout-only; the canonical record is still read.
    assert all(e.stable_read_state == scan.READ_STABLE for e in _candidates(result))


def test_all_regular_partition_files_are_candidates_regardless_of_basename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    filenames = (".hidden", "record.json~", "notes.md")
    for name in filenames:
        (root / c.PARTITION_ACCEPTED / name).write_bytes(b"not archive JSON")

    def convention_must_not_be_called(_value: Any) -> bool:
        raise AssertionError("filename convention must not gate record reading")

    monkeypatch.setattr(scan, "is_record_convention_basename", convention_must_not_be_called)
    result = _scan(root)

    candidates = _candidates(result)
    assert len(candidates) == 4
    assert all(entry.stable_read_state == scan.READ_STABLE for entry in candidates)
    assert {".hidden", "notes.md"} <= {entry.safe_name for entry in candidates}
    unsafe = [entry for entry in candidates if entry.safe_name is None]
    assert len(unsafe) == 1
    assert unsafe[0].stable_read_state == scan.READ_STABLE
    assert [
        entry for entry in result.entries if entry.entry_kind == scan.ENTRY_UNEXPECTED_REGULAR_FILE
    ] == []
    assert result.warning_tokens == ()


def test_unsafe_regular_partition_filename_is_read_but_reported_as_digest(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    (root / c.PARTITION_ACCEPTED / "résumé notes.txt").write_text("x", encoding="utf-8")

    result = _scan(root)

    candidate = [
        entry for entry in _candidates(result) if entry.safe_name is None
    ]
    assert len(candidate) == 1
    assert candidate[0].safe_relative_path is None
    assert candidate[0].stable_read_state == scan.READ_STABLE
    assert candidate[0].record_bytes == b"x"
    assert "résumé" not in candidate[0].reference
    assert candidate[0].entry_path_sha256 in candidate[0].reference


@pytest.mark.skipif(os.geteuid() == 0, reason="permission checks are bypassed as root")
def test_unreadable_partition_is_unverifiable(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    partition = root / c.PARTITION_ACCEPTED
    partition.chmod(0)
    try:
        result = _scan(root)
    finally:
        partition.chmod(0o755)
    assert scan.TOKEN_PARTITION_UNREADABLE in result.unverifiable_tokens


@pytest.mark.skipif(os.geteuid() == 0, reason="permission checks are bypassed as root")
def test_unreadable_record_is_unverifiable_not_silent(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    record.chmod(0)
    try:
        result = _scan(root)
    finally:
        record.chmod(0o644)
    assert scan.TOKEN_RECORD_UNREADABLE in result.unverifiable_tokens
    assert any(
        e.stable_read_state == scan.READ_RECORD_UNREADABLE for e in _candidates(result)
    )


# --- limits ---------------------------------------------------------------------
def test_oversize_record_is_reported_never_silently_skipped(tmp_path: Path) -> None:
    root = _archive(tmp_path)

    result = _scan(root, scan.ScanLimits(record_max_bytes=64))

    assert scan.TOKEN_RECORD_OVERSIZE in result.unverifiable_tokens
    oversize = [
        e for e in _candidates(result) if e.stable_read_state == scan.READ_RECORD_OVERSIZE
    ]
    assert len(oversize) == 1
    assert oversize[0].file_sha256 is None and oversize[0].record_bytes is None


def test_oversize_nonconforming_partition_file_is_reported_never_skipped(
    tmp_path: Path
) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    record.rename(record.with_name("oversize_record.txt"))

    result = _scan(root, scan.ScanLimits(record_max_bytes=64))

    oversize = [
        entry
        for entry in _candidates(result)
        if entry.safe_name == "oversize_record.txt"
    ]
    assert len(oversize) == 1
    assert oversize[0].stable_read_state == scan.READ_RECORD_OVERSIZE
    assert scan.TOKEN_RECORD_OVERSIZE in result.unverifiable_tokens


def test_layout_is_not_read_past_the_global_total_limit(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    layout_bytes = len((root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).read_bytes())

    result = _scan(
        root,
        scan.ScanLimits(max_total_read_bytes=layout_bytes - 1),
    )

    assert result.layout_status == scan.LAYOUT_TOTAL_READ_LIMIT
    assert scan.TOKEN_TOTAL_READ_LIMIT in result.unverifiable_tokens
    assert result.total_bytes_read == 0
    assert result.total_bytes_read <= result.effective_limits.max_total_read_bytes


def test_opened_descriptor_size_not_stale_classification_size_controls_total_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    valid_bytes = record.read_bytes()
    record.write_bytes(b"x")
    layout_bytes = len((root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).read_bytes())
    real_lstat = os.lstat
    calls = {"record": 0}

    def grow_after_classification(path: Any, **kwargs: Any) -> os.stat_result:
        st = real_lstat(path, **kwargs)
        if os.fspath(path) == os.fspath(record):
            calls["record"] += 1
            if calls["record"] == 1:
                record.write_bytes(valid_bytes)
        return st

    monkeypatch.setattr(os, "lstat", grow_after_classification)
    result = _scan(
        root,
        scan.ScanLimits(max_total_read_bytes=layout_bytes + 1),
    )

    candidate = _candidates(result)[0]
    assert candidate.stable_read_state == scan.READ_SKIPPED_TOTAL_LIMIT
    assert scan.TOKEN_TOTAL_READ_LIMIT in result.unverifiable_tokens
    assert result.total_bytes_read == layout_bytes
    assert result.total_bytes_read <= result.effective_limits.max_total_read_bytes


def test_partial_failed_read_is_charged_to_the_global_total_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    foreign = root / c.PARTITION_ACCEPTED / ".partial_read.txt"
    foreign.write_bytes(b"x" * ((1 << 20) + 16))
    layout_bytes = len((root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).read_bytes())
    real_read = os.read
    calls = {"count": 0}

    def fail_second_record_chunk(fd: int, count: int) -> bytes:
        calls["count"] += 1
        # Layout consumes the first read. The large partition file then reads
        # one 1 MiB chunk before this deterministic I/O failure.
        if calls["count"] == 3:
            raise OSError("simulated read failure")
        return real_read(fd, count)

    monkeypatch.setattr(os, "read", fail_second_record_chunk)
    result = _scan(
        root,
        scan.ScanLimits(max_total_read_bytes=layout_bytes + (1 << 20) + 16),
    )

    partial = [entry for entry in _candidates(result) if entry.safe_name == ".partial_read.txt"]
    assert len(partial) == 1
    assert partial[0].stable_read_state == scan.READ_RECORD_UNREADABLE
    assert scan.TOKEN_RECORD_UNREADABLE in result.unverifiable_tokens
    assert result.total_bytes_read == layout_bytes + (1 << 20)
    assert result.total_bytes_read <= result.effective_limits.max_total_read_bytes


def test_unstable_read_is_charged_to_the_global_total_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    record_bytes = record.read_bytes()
    layout_bytes = len((root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).read_bytes())
    real_read = os.read
    calls = {"count": 0}

    def grow_after_record_read(fd: int, count: int) -> bytes:
        data = real_read(fd, count)
        calls["count"] += 1
        if calls["count"] == 2:  # layout is the first bounded read
            with record.open("ab") as handle:
                handle.write(b"x")
        return data

    monkeypatch.setattr(os, "read", grow_after_record_read)
    result = _scan(
        root,
        scan.ScanLimits(max_total_read_bytes=layout_bytes + len(record_bytes)),
    )

    candidate = _candidates(result)[0]
    assert candidate.stable_read_state == scan.READ_RECORD_CHANGED
    assert scan.TOKEN_RECORD_CHANGED in result.unverifiable_tokens
    assert result.total_bytes_read == layout_bytes + len(record_bytes)
    assert result.total_bytes_read <= result.effective_limits.max_total_read_bytes


def test_many_failed_reads_are_charged_until_the_total_limit_is_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    for name in (".failed_a", ".failed_b"):
        (root / c.PARTITION_ACCEPTED / name).write_bytes(b"x" * ((1 << 20) + 16))
    layout_bytes = len((root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).read_bytes())
    real_read = os.read
    calls = {"count": 0}

    def fail_after_each_large_chunk(fd: int, count: int) -> bytes:
        calls["count"] += 1
        if calls["count"] in (3, 5):
            raise OSError("repeated_read_failure_sentinel")
        return real_read(fd, count)

    monkeypatch.setattr(os, "read", fail_after_each_large_chunk)
    limit = layout_bytes + 2 * ((1 << 20) + 16)
    result = _scan(root, scan.ScanLimits(max_total_read_bytes=limit))

    failed = [
        entry
        for entry in _candidates(result)
        if entry.safe_name in {".failed_a", ".failed_b"}
    ]
    assert len(failed) == 2
    assert all(entry.stable_read_state == scan.READ_RECORD_UNREADABLE for entry in failed)
    assert result.total_bytes_read == layout_bytes + 2 * (1 << 20)
    assert result.total_bytes_read <= result.effective_limits.max_total_read_bytes
    assert scan.TOKEN_RECORD_UNREADABLE in result.unverifiable_tokens
    assert scan.TOKEN_TOTAL_READ_LIMIT in result.unverifiable_tokens


def test_entry_count_limit_truncates_inventory_loudly(tmp_path: Path) -> None:
    root = _archive(tmp_path)

    result = _scan(root, scan.ScanLimits(max_direct_entries=3))

    assert scan.TOKEN_ENTRY_COUNT_LIMIT in result.unverifiable_tokens
    assert result.entry_inventory_truncated is True
    assert result.entries == ()
    # Enumeration stops at the first detection entry: the four root layout
    # entries already exceed this deliberately lowered total limit.
    assert result.direct_entry_count == 4


def test_bounded_enumerator_stops_after_one_detection_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    consumed = {"count": 0}

    class _Entry:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Entries:
        def __init__(self) -> None:
            self._next = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def __iter__(self):
            return self

        def __next__(self) -> _Entry:
            if self._next >= 10_000:
                raise StopIteration
            name = f"entry_{self._next}"
            self._next += 1
            consumed["count"] += 1
            return _Entry(name)

    monkeypatch.setattr(os, "scandir", lambda _path: _Entries())
    snapshot = scan._bounded_directory_snapshot(tmp_path, 3)

    assert snapshot is not None
    assert snapshot.truncated is True
    assert len(snapshot.names) == 4
    assert consumed["count"] == 4


def test_postscan_resnapshot_uses_the_same_bounded_enumerator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    accepted = root / c.PARTITION_ACCEPTED
    real_scandir = os.scandir
    accepted_calls = {"count": 0}
    final_consumed = {"count": 0}

    class _Entry:
        def __init__(self, name: str) -> None:
            self.name = name

    class _HugeFinalEntries:
        def __init__(self) -> None:
            self._next = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def __iter__(self):
            return self

        def __next__(self) -> _Entry:
            if self._next >= 10_000:
                raise StopIteration
            name = f"final_entry_{self._next}"
            self._next += 1
            final_consumed["count"] += 1
            return _Entry(name)

    def bounded_final_scandir(path: Any = "."):
        if os.fspath(path) == os.fspath(accepted):
            accepted_calls["count"] += 1
            if accepted_calls["count"] == 2:
                return _HugeFinalEntries()
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", bounded_final_scandir)
    result = _scan(root, scan.ScanLimits(max_direct_entries=6))

    # Four root entries leave room for only two partition names, so the final
    # accepted re-snapshot stops after the third detection entry.
    assert final_consumed["count"] == 3
    assert result.entry_inventory_truncated is True
    assert scan.TOKEN_ENTRY_COUNT_LIMIT in result.unverifiable_tokens
    assert scan.TOKEN_ARCHIVE_CHANGED in result.unverifiable_tokens


def test_total_read_limit_marks_unread_records(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    _ingest_payload(root, b"{ not valid json ")

    layout_bytes = len((root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).read_bytes())
    result = _scan(root, scan.ScanLimits(max_total_read_bytes=layout_bytes + 8))

    assert scan.TOKEN_TOTAL_READ_LIMIT in result.unverifiable_tokens
    skipped = [
        e for e in _candidates(result) if e.stable_read_state == scan.READ_SKIPPED_TOTAL_LIMIT
    ]
    assert len(skipped) == 2  # both records reported, neither silently dropped
    assert result.total_bytes_read <= layout_bytes + 8


def test_total_read_limit_records_nonconforming_candidate(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    record.rename(record.with_name("limited_record.bak"))
    layout_bytes = len((root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).read_bytes())

    result = _scan(root, scan.ScanLimits(max_total_read_bytes=layout_bytes + 1))

    skipped = [
        entry
        for entry in _candidates(result)
        if entry.safe_name == "limited_record.bak"
    ]
    assert len(skipped) == 1
    assert skipped[0].stable_read_state == scan.READ_SKIPPED_TOTAL_LIMIT
    assert scan.TOKEN_TOTAL_READ_LIMIT in result.unverifiable_tokens


@pytest.mark.parametrize(
    ("phase", "expected_state"),
    (
        ("classification", scan.READ_DISAPPEARED_BEFORE_CLASSIFICATION),
        ("before_open", scan.READ_DISAPPEARED_BEFORE_OPEN),
        ("during_read", scan.READ_DISAPPEARED_DURING_READ),
    ),
)
def test_disappeared_partition_record_remains_manifest_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    expected_state: str,
) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    real_lstat = os.lstat
    calls = {"record": 0}
    trigger = {"classification": 1, "before_open": 2, "during_read": 3}[phase]

    def disappear_at_selected_phase(path: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(path) == os.fspath(record):
            calls["record"] += 1
            if calls["record"] == trigger:
                record.unlink()
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", disappear_at_selected_phase)
    scanned = _scan(root)
    report = idx.build_archive_index(scanned)

    candidate = _candidates(scanned)
    assert len(candidate) == 1
    assert candidate[0].stable_read_state == expected_state
    assert report["source_record_count"] == 1
    assert report["unread_record_count"] == 1
    assert report["source_set_manifest"][0]["stable_read_state"] == expected_state
    assert report["record_entries"][0]["stable_read_state"] == expected_state
    assert report["unexpected_entries"] == []
    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE


def test_disappeared_unsafe_partition_name_is_digest_only_in_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    unsafe_name = "UNSAFE_NAME_SENTINEL_☃"
    vanished = root / c.PARTITION_ACCEPTED / unsafe_name
    vanished.write_bytes(b"x")
    real_lstat = os.lstat
    calls = {"unsafe": 0}

    def disappear_before_classification(path: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(path) == os.fspath(vanished):
            calls["unsafe"] += 1
            if calls["unsafe"] == 1:
                vanished.unlink()
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", disappear_before_classification)
    report = idx.build_archive_index(_scan(root))
    serialized = idx.serialize_index_report(report)
    manifest_entry = [
        entry
        for entry in report["source_set_manifest"]
        if entry["stable_read_state"] == scan.READ_DISAPPEARED_BEFORE_CLASSIFICATION
    ]

    assert len(manifest_entry) == 1
    assert manifest_entry[0]["entry"].startswith("unsafe_name:accepted:")
    assert unsafe_name not in serialized


@pytest.mark.parametrize(
    "kwargs, token",
    (
        ({"record_max_bytes": scan.RECORD_MAX_BYTES + 1}, "scan_limit_above_maximum:record_max_bytes"),
        ({"max_direct_entries": scan.MAX_DIRECT_ENTRIES + 1}, "scan_limit_above_maximum:max_direct_entries"),
        ({"max_total_read_bytes": scan.MAX_TOTAL_READ_BYTES + 1}, "scan_limit_above_maximum:max_total_read_bytes"),
        ({"layout_file_max_bytes": scan.LAYOUT_FILE_MAX_BYTES + 1}, "scan_limit_above_maximum:layout_file_max_bytes"),
        ({"record_max_bytes": 0}, "scan_limit_invalid:record_max_bytes"),
        ({"record_max_bytes": True}, "scan_limit_invalid:record_max_bytes"),
        ({"record_max_bytes": "big"}, "scan_limit_invalid:record_max_bytes"),
    ),
)
def test_limits_cannot_exceed_code_owned_maxima(kwargs: dict[str, Any], token: str) -> None:
    with pytest.raises(scan.ScanLimitError) as excinfo:
        scan.ScanLimits(**kwargs)
    assert excinfo.value.token == token


def test_effective_limits_are_always_reported() -> None:
    limits = scan.ScanLimits(record_max_bytes=128)
    assert limits.as_report_mapping() == {
        "layout_file_max_bytes": scan.LAYOUT_FILE_MAX_BYTES,
        "record_max_bytes": 128,
        "max_direct_entries": scan.MAX_DIRECT_ENTRIES,
        "max_total_read_bytes": scan.MAX_TOTAL_READ_BYTES,
    }


# --- determinism and stability -----------------------------------------------------
def test_scan_is_independent_of_filesystem_iteration_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path, rejected=True)
    baseline = _scan(root)

    real_scandir = os.scandir

    class _ReversedScandir:
        def __init__(self, path: Any) -> None:
            self._iterator = real_scandir(path)
            self._entries = list(self._iterator)

        def __enter__(self):
            return reversed(self._entries)

        def __exit__(self, *_args: Any) -> None:
            self._iterator.close()

    monkeypatch.setattr(os, "scandir", _ReversedScandir)
    reordered = _scan(root)

    assert reordered == baseline


def test_partition_entry_set_change_during_scan_is_unverifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    accepted = root / c.PARTITION_ACCEPTED
    real_scandir = os.scandir
    calls = {"accepted": 0}

    def mutating_scandir(path: Any = "."):
        if os.fspath(path) == os.fspath(accepted):
            calls["accepted"] += 1
            if calls["accepted"] == 2:  # the post-read re-snapshot
                (accepted / ("9" * 18 + "__noid__" + "0" * 64 + ".json")).write_text(
                    "{}", encoding="utf-8"
                )
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", mutating_scandir)
    result = _scan(root)

    assert scan.TOKEN_ARCHIVE_CHANGED in result.unverifiable_tokens


def test_record_mutation_during_read_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    unstable = record.with_name("unstable_record.txt")
    record.rename(unstable)
    real_lstat = os.lstat
    calls = {"n": 0}

    def touching_lstat(path: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(path) == os.fspath(unstable):
            calls["n"] += 1
            if calls["n"] == 3:  # the final stability lstat after the read
                os.utime(unstable, ns=(1, 1))
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", touching_lstat)
    result = _scan(root)

    assert scan.TOKEN_RECORD_CHANGED in result.unverifiable_tokens
    assert any(
        e.stable_read_state == scan.READ_RECORD_CHANGED for e in _candidates(result)
    )


def test_final_path_replacement_never_returns_false_stable_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    original_bytes = record.read_bytes()
    replacement_bytes = b"x" * len(original_bytes)
    real_lstat = os.lstat

    for _trial in range(12):
        # Give each trial a valid original file, then replace it immediately
        # before the final path lstat.  Keeping the descriptor open must make
        # the replacement unambiguously different even if the filesystem would
        # otherwise reuse the inode; preserve mtime where the platform allows.
        record.write_bytes(original_bytes)
        initial = real_lstat(record)
        calls = {"record": 0}

        def replace_before_final_lstat(path: Any, **kwargs: Any) -> os.stat_result:
            if os.fspath(path) == os.fspath(record):
                calls["record"] += 1
                if calls["record"] == 3:
                    record.unlink()
                    record.write_bytes(replacement_bytes)
                    os.utime(record, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            return real_lstat(path, **kwargs)

        monkeypatch.setattr(os, "lstat", replace_before_final_lstat)
        result = _scan(root)
        monkeypatch.setattr(os, "lstat", real_lstat)

        candidate = _candidates(result)
        assert len(candidate) == 1
        assert candidate[0].stable_read_state == scan.READ_RECORD_CHANGED
        assert candidate[0].record_bytes is None
        assert record.read_bytes() == replacement_bytes
        assert scan.TOKEN_RECORD_CHANGED in result.unverifiable_tokens


# --- final archive-wide source-set revalidation ---------------------------------
def test_record_replaced_after_initial_read_is_not_finally_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    original_bytes = record.read_bytes()
    original_stat = os.lstat(record)
    replacement_bytes = b"x" * len(original_bytes)
    real_lstat = os.lstat
    calls = {"record": 0}

    def replace_before_revalidation(path: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(path) == os.fspath(record):
            calls["record"] += 1
            if calls["record"] == 4:
                record.unlink()
                record.write_bytes(replacement_bytes)
                os.utime(record, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", replace_before_revalidation)
    report = _index(root)

    entry = report["record_entries"][0]
    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert scan.TOKEN_ARCHIVE_CHANGED in report["assessment_reason_tokens"]["unverifiable"]
    assert entry["stable_read_state"] == scan.READ_STABLE
    assert entry["final_revalidation_state"] == scan.REVALIDATION_CHANGED
    assert entry["observed_file_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert entry["observed_file_sha256"] != hashlib.sha256(record.read_bytes()).hexdigest()
    assert entry["identity_facts_valid"] is None
    assert report["unread_record_count"] == 1
    assert report["duplicate_groups"] == []
    assert report["provisional_contract_partitions"] == []


def test_partition_replacement_with_same_names_is_unverifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    accepted = root / c.PARTITION_ACCEPTED
    record = next(accepted.iterdir())
    original_name = record.name
    original_bytes = record.read_bytes()
    original_partition_inode = os.lstat(accepted).st_ino
    real_scandir = os.scandir
    calls = {"accepted": 0}

    def replace_on_final_snapshot(path: Any = "."):
        if os.fspath(path) == os.fspath(accepted):
            calls["accepted"] += 1
            if calls["accepted"] == 2:
                accepted.rename(tmp_path / "accepted-before-replacement")
                accepted.mkdir()
                (accepted / original_name).write_bytes(b"x" * len(original_bytes))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", replace_on_final_snapshot)
    report = _index(root)

    assert os.lstat(accepted).st_ino != original_partition_inode
    assert sorted(path.name for path in accepted.iterdir()) == [original_name]
    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert scan.TOKEN_ARCHIVE_CHANGED in report["assessment_reason_tokens"]["unverifiable"]
    assert report["record_entries"][0]["final_revalidation_state"] == (
        scan.REVALIDATION_CHANGED
    )
    assert report["record_entries"][0]["identity_facts_valid"] is None


def test_layout_change_after_initial_validation_is_unverifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    layout = root / c.ARCHIVE_LAYOUT_VERSION_FILENAME
    real_lstat = os.lstat
    calls = {"layout": 0}

    def change_before_final_identity(path: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(path) == os.fspath(layout):
            calls["layout"] += 1
            if calls["layout"] == 4:
                layout.write_text(c.ARCHIVE_LAYOUT_VERSION + " \n", encoding="utf-8")
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", change_before_final_identity)
    report = _index(root)

    assert report["archive_layout_status"] == scan.LAYOUT_CANONICAL
    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert scan.TOKEN_ARCHIVE_CHANGED in report["assessment_reason_tokens"]["unverifiable"]


def test_root_replacement_is_unverifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    replacement = tmp_path / "replacement-root"
    shutil.copytree(root, replacement)
    original_root_inode = os.lstat(root).st_ino
    real_scandir = os.scandir
    calls = {"root": 0}

    def replace_during_final_snapshot(path: Any = "."):
        if os.fspath(path) == os.fspath(root):
            calls["root"] += 1
            if calls["root"] == 2:
                root.rename(tmp_path / "root-before-replacement")
                replacement.rename(root)
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", replace_during_final_snapshot)
    report = _index(root)

    assert os.lstat(root).st_ino != original_root_inode
    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert scan.TOKEN_ARCHIVE_CHANGED in report["assessment_reason_tokens"]["unverifiable"]


@pytest.mark.parametrize("transition", ("symlink", "directory", "fifo"))
def test_partition_entry_type_transition_remains_manifest_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transition: str
) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    target = tmp_path / "symlink-target"
    target.write_bytes(b"outside")
    real_lstat = os.lstat
    calls = {"record": 0}

    def transition_before_classification(path: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(path) == os.fspath(record):
            calls["record"] += 1
            if calls["record"] == 1:
                record.unlink()
                if transition == "symlink":
                    record.symlink_to(target)
                elif transition == "directory":
                    record.mkdir()
                else:
                    os.mkfifo(record)
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", transition_before_classification)
    report = _index(root)

    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert report["source_record_count"] == 1
    assert report["unread_record_count"] == 1
    assert len(report["source_set_manifest"]) == len(report["record_entries"]) == 1
    assert report["source_set_manifest"][0]["stable_read_state"] == (
        scan.READ_ENTRY_TYPE_CHANGED_BEFORE_CLASSIFICATION
    )
    assert report["source_set_manifest"][0]["observed_file_sha256"] is None
    assert report["counts_by_verification_state"] == {
        scan.READ_ENTRY_TYPE_CHANGED_BEFORE_CLASSIFICATION: 1
    }
    assert report["unexpected_entries"] == []


@pytest.mark.parametrize(
    ("exception", "expected_state", "expected_token"),
    (
        (FileNotFoundError, scan.READ_DISAPPEARED_BEFORE_CLASSIFICATION, scan.TOKEN_ARCHIVE_CHANGED),
        (
            PermissionError,
            scan.READ_CLASSIFICATION_PERMISSION_DENIED,
            scan.TOKEN_RECORD_CLASSIFICATION_PERMISSION_DENIED,
        ),
        (OSError, scan.READ_CLASSIFICATION_FAILED, scan.TOKEN_RECORD_CLASSIFICATION_FAILED),
    ),
)
def test_classification_failures_have_distinct_manifest_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception: type[OSError],
    expected_state: str,
    expected_token: str,
) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    real_lstat = os.lstat
    calls = {"record": 0}

    def fail_classification(path: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(path) == os.fspath(record):
            calls["record"] += 1
            if calls["record"] == 1:
                raise exception("raw_classification_exception_sentinel")
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", fail_classification)
    report = _index(root)
    serialized = idx.serialize_index_report(report)

    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert report["source_record_count"] == report["unread_record_count"] == 1
    assert report["source_set_manifest"][0]["stable_read_state"] == expected_state
    assert report["counts_by_verification_state"] == {expected_state: 1}
    assert expected_token in report["assessment_reason_tokens"]["unverifiable"]
    assert "raw_classification_exception_sentinel" not in serialized
    assert str(root) not in serialized


def test_final_revalidation_uses_remaining_total_budget(tmp_path: Path) -> None:
    root = _archive(tmp_path)
    record = next((root / c.PARTITION_ACCEPTED).iterdir())
    layout_bytes = len((root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).read_bytes())
    initial_record_bytes = len(record.read_bytes())
    limit = layout_bytes + initial_record_bytes

    report = _index(root, scan.ScanLimits(max_total_read_bytes=limit))

    assert report["total_bytes_read"] == limit
    assert report["total_bytes_read"] <= report["verification_limits"]["max_total_read_bytes"]
    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
    assert report["record_entries"][0]["stable_read_state"] == scan.READ_STABLE
    assert report["record_entries"][0]["final_revalidation_state"] == (
        scan.REVALIDATION_SKIPPED_TOTAL_LIMIT
    )
    assert report["counts_by_verification_state"] == {
        scan.REVALIDATION_SKIPPED_TOTAL_LIMIT: 1
    }


def test_partial_final_revalidation_read_is_charged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _archive(tmp_path)
    next((root / c.PARTITION_ACCEPTED).iterdir()).unlink()
    record = root / c.PARTITION_ACCEPTED / ".large_nonconforming"
    record.write_bytes(b"x" * ((1 << 20) + 16))
    layout_bytes = len((root / c.ARCHIVE_LAYOUT_VERSION_FILENAME).read_bytes())
    real_read = os.read
    calls = {"count": 0}

    def fail_second_final_chunk(fd: int, count: int) -> bytes:
        calls["count"] += 1
        if calls["count"] == 5:
            raise OSError("partial_final_read_sentinel")
        return real_read(fd, count)

    monkeypatch.setattr(os, "read", fail_second_final_chunk)
    report = _index(root)

    assert report["total_bytes_read"] == layout_bytes + (1 << 20) + 16 + (1 << 20)
    assert report["total_bytes_read"] <= report["verification_limits"]["max_total_read_bytes"]
    assert report["record_entries"][0]["final_revalidation_state"] == (
        scan.REVALIDATION_CHANGED
    )
    assert report["archive_assessment_state"] == idx.ASSESSMENT_UNVERIFIABLE
