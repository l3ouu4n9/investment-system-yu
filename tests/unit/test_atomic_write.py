"""Unit tests for the atomic_write_text helper (PR G2.2).

Covers: correct content on success; overwrite-in-place; temp-in-dir + os.replace
behavior; failure during replace leaves the target unchanged (never partial) and
removes the temp; failure during the temp write leaves no target and no temp.
Tests avoid asserting exact temp file names (they only require the temp to live
in the target directory and to be cleaned up).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import investment_orchestrator.common.io as io_mod
from investment_orchestrator.common.io import atomic_write_text


def _temp_leftovers(directory: Path) -> list[str]:
    return [p.name for p in directory.iterdir() if ".tmp." in p.name]


def test_atomic_write_creates_file_with_expected_content(tmp_path: Path) -> None:
    target = tmp_path / "orders.txt"
    atomic_write_text(target, "hello\nworld\n")
    assert target.read_text(encoding="utf-8") == "hello\nworld\n"
    assert _temp_leftovers(tmp_path) == []


def test_atomic_write_overwrites_existing_in_place(tmp_path: Path) -> None:
    target = tmp_path / "orders.txt"
    target.write_text("OLD\n", encoding="utf-8")
    atomic_write_text(target, "NEW CONTENT\n")
    assert target.read_text(encoding="utf-8") == "NEW CONTENT\n"
    assert _temp_leftovers(tmp_path) == []


def test_atomic_write_uses_temp_in_same_dir_then_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "orders.txt"
    seen: list[tuple[Path, Path]] = []
    real_replace = io_mod.os.replace

    def spy_replace(src: Any, dst: Any) -> None:
        src_path, dst_path = Path(src), Path(dst)
        # The temp source lives in the SAME directory as the target (no cross-device).
        assert src_path.parent == dst_path.parent
        # Content is fully on disk in the temp before the rename happens.
        assert src_path.read_text(encoding="utf-8") == "PAYLOAD\n"
        seen.append((src_path, dst_path))
        real_replace(src, dst)

    monkeypatch.setattr(io_mod.os, "replace", spy_replace)
    atomic_write_text(target, "PAYLOAD\n")

    assert len(seen) == 1
    assert seen[0][1] == target
    assert target.read_text(encoding="utf-8") == "PAYLOAD\n"
    assert _temp_leftovers(tmp_path) == []


def test_atomic_write_replace_failure_leaves_prior_content_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "orders.txt"
    target.write_text("PRIOR_GOOD\n", encoding="utf-8")

    def boom(src: Any, dst: Any) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(io_mod.os, "replace", boom)
    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_text(target, "NEW_BUT_UNCOMMITTED\n")

    # Target is unchanged (full prior content, never partial); temp cleaned up.
    assert target.read_text(encoding="utf-8") == "PRIOR_GOOD\n"
    assert _temp_leftovers(tmp_path) == []


def test_atomic_write_temp_write_failure_leaves_no_target_and_no_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "orders.txt"  # does not pre-exist

    def boom_fsync(fd: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(io_mod.os, "fsync", boom_fsync)
    with pytest.raises(OSError, match="simulated fsync failure"):
        atomic_write_text(target, "NEVER_PUBLISHED\n")

    # No partial target was created and the temp was cleaned up.
    assert not target.exists()
    assert _temp_leftovers(tmp_path) == []
