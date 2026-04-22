from __future__ import annotations

from datetime import datetime

import pytest

from investment_orchestrator.common.artifact_management import (
    archive_current_artifacts,
    clear_current_artifacts,
    current_artifacts_dir,
    prepare_next_run,
    validate_archive_label,
)


def test_archive_current_artifacts_moves_current_tree_into_archive_directory(tmp_path) -> None:
    current_dir = current_artifacts_dir(tmp_path)
    step_dir = current_dir / "step1_research"
    step_dir.mkdir(parents=True)
    (step_dir / "prompt.txt").write_text("prompt body\n", encoding="utf-8")

    archived_path = archive_current_artifacts(
        root=tmp_path,
        label="before_prompt_refresh",
    )

    assert archived_path == tmp_path / "artifacts" / "archive" / "before_prompt_refresh"
    assert (archived_path / "step1_research" / "prompt.txt").read_text(encoding="utf-8") == "prompt body\n"
    assert current_dir.exists()
    assert list(current_dir.iterdir()) == []


def test_clear_current_artifacts_removes_existing_files_and_recreates_directory(tmp_path) -> None:
    current_dir = current_artifacts_dir(tmp_path)
    nested_dir = current_dir / "step2_decision_builder"
    nested_dir.mkdir(parents=True)
    (nested_dir / "raw_output.txt").write_text("old output\n", encoding="utf-8")

    cleared_path = clear_current_artifacts(root=tmp_path)

    assert cleared_path == current_dir
    assert current_dir.exists()
    assert list(current_dir.iterdir()) == []


def test_prepare_next_run_archives_existing_run_then_resets_current(tmp_path) -> None:
    current_dir = current_artifacts_dir(tmp_path)
    (current_dir / "step3_audit_engine").mkdir(parents=True)
    (current_dir / "step3_audit_engine" / "raw_output.txt").write_text("audit\n", encoding="utf-8")

    result = prepare_next_run(
        root=tmp_path,
        now=datetime(2026, 4, 20, 11, 32, 45),
    )

    assert result.archive_path == tmp_path / "artifacts" / "archive" / "20260420_113245"
    assert (result.archive_path / "step3_audit_engine" / "raw_output.txt").exists()
    assert result.current_path == current_dir
    assert list(current_dir.iterdir()) == []


def test_prepare_next_run_skips_archive_when_current_is_missing_or_empty(tmp_path) -> None:
    result = prepare_next_run(root=tmp_path)

    assert result.archive_path is None
    assert result.current_path == current_artifacts_dir(tmp_path)
    assert result.current_path.exists()
    assert list(result.current_path.iterdir()) == []


def test_validate_archive_label_rejects_path_like_values() -> None:
    with pytest.raises(ValueError):
        validate_archive_label("../bad-label")
