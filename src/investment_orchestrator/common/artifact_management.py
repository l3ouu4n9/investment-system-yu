"""Helpers for archiving and resetting current workflow artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import shutil

from investment_orchestrator.common.io import ensure_dir
from investment_orchestrator.common.paths import repo_root


ARCHIVE_LABEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class PrepareNextRunResult:
    archive_path: Path | None
    current_path: Path


def _resolve_repo_root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else repo_root()


def artifacts_root(root: str | Path | None = None) -> Path:
    """Return the top-level artifacts directory for the selected repo root."""
    return _resolve_repo_root(root) / "artifacts"


def current_artifacts_dir(root: str | Path | None = None) -> Path:
    """Return the current-run artifacts directory."""
    return artifacts_root(root) / "current"


def archive_artifacts_dir(root: str | Path | None = None) -> Path:
    """Return the archive directory for historical runs."""
    return artifacts_root(root) / "archive"


def default_archive_label(now: datetime | None = None) -> str:
    """Build a timestamp label for archive directories."""
    timestamp = now or datetime.now()
    return timestamp.strftime("%Y%m%d_%H%M%S")


def validate_archive_label(label: str) -> str:
    """Require a simple archive label that cannot escape the archive directory."""
    cleaned = label.strip()
    if not cleaned:
        raise ValueError("Archive label cannot be empty.")
    if not ARCHIVE_LABEL_RE.fullmatch(cleaned):
        raise ValueError(
            "Archive label may contain only letters, numbers, dot, underscore, and hyphen."
        )
    return cleaned


def _has_entries(path: Path) -> bool:
    return path.exists() and any(path.iterdir())


def clear_current_artifacts(*, root: str | Path | None = None) -> Path:
    """Delete and recreate artifacts/current."""
    current_dir = current_artifacts_dir(root)
    if current_dir.exists():
        shutil.rmtree(current_dir)
    return ensure_dir(current_dir)


def archive_current_artifacts(
    *,
    root: str | Path | None = None,
    label: str | None = None,
    now: datetime | None = None,
) -> Path | None:
    """Archive artifacts/current into artifacts/archive/<label> when it is non-empty."""
    current_dir = current_artifacts_dir(root)
    if not _has_entries(current_dir):
        ensure_dir(current_dir)
        return None

    archive_root = ensure_dir(archive_artifacts_dir(root))
    archive_label = validate_archive_label(label or default_archive_label(now))
    destination = archive_root / archive_label
    if destination.exists():
        raise FileExistsError(f"Archive destination already exists: {destination}")

    shutil.move(str(current_dir), str(destination))
    ensure_dir(current_dir)
    return destination


def prepare_next_run(
    *,
    root: str | Path | None = None,
    label: str | None = None,
    now: datetime | None = None,
) -> PrepareNextRunResult:
    """Archive the current run when present, then recreate a clean current directory."""
    archived_path = archive_current_artifacts(root=root, label=label, now=now)
    current_path = clear_current_artifacts(root=root)
    return PrepareNextRunResult(
        archive_path=archived_path,
        current_path=current_path,
    )
