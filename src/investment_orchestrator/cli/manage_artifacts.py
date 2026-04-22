"""CLI helpers for archiving and resetting current workflow artifacts."""

from __future__ import annotations

import argparse
import sys

from investment_orchestrator.common.artifact_management import (
    archive_current_artifacts,
    clear_current_artifacts,
    prepare_next_run,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the artifact management CLI."""
    parser = argparse.ArgumentParser(description="Archive or reset artifacts/current for a new run.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    archive_parser = subparsers.add_parser(
        "archive-current",
        help="Move artifacts/current into artifacts/archive/<label> and recreate an empty current directory.",
    )
    archive_parser.add_argument(
        "--label",
        help="Optional archive directory label. Defaults to a timestamp like 20260420_103015.",
    )

    subparsers.add_parser(
        "clear-current",
        help="Delete all contents under artifacts/current and recreate an empty directory.",
    )

    prepare_parser = subparsers.add_parser(
        "prepare-next-run",
        help="Archive current artifacts when present, then recreate a clean artifacts/current.",
    )
    prepare_parser.add_argument(
        "--label",
        help="Optional archive directory label. Defaults to a timestamp like 20260420_103015.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "archive-current":
            archive_path = archive_current_artifacts(label=args.label)
            if archive_path is None:
                print("artifacts/current was already empty")
            else:
                print(str(archive_path))
            return 0

        if args.command == "clear-current":
            current_path = clear_current_artifacts()
            print(str(current_path))
            return 0

        if args.command == "prepare-next-run":
            result = prepare_next_run(label=args.label)
            archive_path = result.archive_path
            current_path = result.current_path
            if archive_path is not None:
                print(f"archived: {archive_path}")
            else:
                print("archived: <skipped; artifacts/current was empty>")
            print(f"current: {current_path}")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
