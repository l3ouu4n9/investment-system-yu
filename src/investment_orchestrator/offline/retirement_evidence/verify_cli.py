"""Operator/CI command-line entry point for the Phase 2B-2 archive index.

Report-only.  Reads one explicit archive root, scans it read-only, and prints
(or writes outside the archive root) one deterministic
``retirement_archive_index_v1`` report.  Authorizes nothing; has no runtime
effect on Step 1 parsing, permissions, gates, or the order path; never writes
beneath the archive root.

Exit codes:
* 0 - ``archive_clean``
* 3 - ``archive_has_warnings``
* 4 - ``archive_has_integrity_failures``
* 5 - ``archive_unverifiable``
* 2 - operator-side error (invalid limit, unsafe/unwritable output path,
      output path inside the archive root); fail closed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from investment_orchestrator.offline.retirement_evidence import archive_coordination as coord
from investment_orchestrator.offline.retirement_evidence.archive_index import (
    ASSESSMENT_CLEAN,
    ASSESSMENT_INTEGRITY_FAILURES,
    ASSESSMENT_UNVERIFIABLE,
    ASSESSMENT_WARNINGS,
    ArchiveOutputError,
    index_archive,
    index_archive_operation,
    serialize_index_report,
)
from investment_orchestrator.offline.retirement_evidence.archive_scan import (
    ScanLimitError,
    ScanLimits,
)


_EXIT_CODES = {
    ASSESSMENT_CLEAN: 0,
    ASSESSMENT_WARNINGS: 3,
    ASSESSMENT_INTEGRITY_FAILURES: 4,
    ASSESSMENT_UNVERIFIABLE: 5,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify-retirement-archive",
        description=(
            "Offline, report-only integrity index of one retirement-observation "
            "archive. Read-only; evaluates no coverage, sufficiency, or "
            "readiness; authorizes nothing; no runtime effect."
        ),
    )
    parser.add_argument(
        "--archive-root",
        required=True,
        help="Path to one archive root to scan read-only.",
    )
    parser.add_argument(
        "--coordination-file",
        default=None,
        help=(
            "Explicit pre-existing retirement_archive_coordination_v1 anchor "
            "outside the archive root. The tool never creates or modifies it."
        ),
    )
    parser.add_argument(
        "--output",
        default="-",
        help=(
            "Report destination: '-' for stdout (default) or a file path that "
            "must resolve OUTSIDE the archive root (fail closed otherwise)."
        ),
    )
    parser.add_argument(
        "--max-layout-file-bytes", type=int, default=None,
        help="Lower the layout-file read limit (never above the code-owned maximum).",
    )
    parser.add_argument(
        "--max-record-bytes", type=int, default=None,
        help="Lower the per-record read limit (never above the code-owned maximum).",
    )
    parser.add_argument(
        "--max-direct-entries", type=int, default=None,
        help="Lower the direct-entry limit (never above the code-owned maximum).",
    )
    parser.add_argument(
        "--max-total-read-bytes", type=int, default=None,
        help="Lower the total-bytes-read limit (never above the code-owned maximum).",
    )
    return parser


def _resolve_limits(args: argparse.Namespace) -> ScanLimits:
    overrides = {
        "layout_file_max_bytes": args.max_layout_file_bytes,
        "record_max_bytes": args.max_record_bytes,
        "max_direct_entries": args.max_direct_entries,
        "max_total_read_bytes": args.max_total_read_bytes,
    }
    return ScanLimits(**{k: v for k, v in overrides.items() if v is not None})


def _write_error(token: str) -> None:
    """Emit one code-owned CLI error without paths or exception text."""
    print(json.dumps({"error": token}), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.coordination_file is None:
        print(
            json.dumps(
                {
                    "error": "archive_coordination_error",
                    "token": coord.TOKEN_PATH_OMITTED,
                }
            ),
            file=sys.stderr,
        )
        return 2

    try:
        limits = _resolve_limits(args)
    except ScanLimitError as exc:
        print(json.dumps({"error": "invalid_scan_limit", "token": exc.token}), file=sys.stderr)
        return 2

    try:
        if args.output == "-":
            report = index_archive(
                Path(args.archive_root),
                limits,
                coordination_path=Path(args.coordination_file),
            )
            output_path = None
        else:
            operation = index_archive_operation(
                Path(args.archive_root),
                limits,
                coordination_path=Path(args.coordination_file),
                output_path=Path(args.output),
            )
            report = operation.report
            output_path = operation.resolved_output_path
    except ArchiveOutputError as exc:
        _write_error(exc.token)
        return 2
    except coord.CoordinationError as exc:
        print(
            json.dumps({"error": "archive_coordination_error", "token": exc.token}),
            file=sys.stderr,
        )
        return 2
    serialized = serialize_index_report(report)
    if output_path is None:
        try:
            sys.stdout.write(serialized)
        except (OSError, UnicodeError, ValueError):
            _write_error("output_write_failed")
            return 2
    else:
        try:
            output_path.write_text(serialized, encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            _write_error("output_write_failed")
            return 2
    return _EXIT_CODES[report["archive_assessment_state"]]


if __name__ == "__main__":
    raise SystemExit(main())
