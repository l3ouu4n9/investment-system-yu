"""Generate one standalone, report-only LTETF-01 prerequisite-gap report."""

from __future__ import annotations

from pathlib import Path
import sys

from investment_orchestrator.observability.ltetf_target_architecture_gap_report import (
    ObserverIntegrityError,
    build_and_write_gap_report,
)


def _repository_root(start: Path) -> Path:
    """Find the repository root without consulting environment state."""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "src" / "investment_orchestrator"
        ).is_dir():
            return candidate
    raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")


def main() -> int:
    """Write the immutable report and print exactly its repository-relative path."""
    try:
        root = _repository_root(Path.cwd().resolve())
        path = build_and_write_gap_report(root)
    except ObserverIntegrityError as error:
        print(error.code, file=sys.stderr)
        return 1
    print(path.relative_to(root).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
