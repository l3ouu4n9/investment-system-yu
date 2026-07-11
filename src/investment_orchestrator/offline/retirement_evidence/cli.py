"""Operator/CI command-line entry point for offline archive ingestion.

Report-only.  Has no runtime effect on Step 1 parsing, permissions, gates, or
the order path.  Reads one explicit source observation and one explicit
destination archive root; writes only beneath that root.

Exit codes:
* 0 - accepted or quarantined (including a deterministic duplicate no-op)
* 3 - input rejected (a reason-only record was written; no payload preserved)
* 2 - archive layout error, existing-record integrity failure, or operator-side
      error (nothing written; fail closed)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence.ingest import (
    ArchiveIngestionError,
    ArchiveLayoutError,
    ExistingRecordIntegrityError,
    ingest_observation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline, report-only ingestion of a Step 1A retirement observation "
            "into an append-only archive. Authorizes nothing; no runtime effect."
        )
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Path to one step1a_retirement_observation_v1 JSON file to ingest.",
    )
    parser.add_argument(
        "--dest",
        required=True,
        help="Path to the destination archive root (outside artifacts/current).",
    )
    parser.add_argument(
        "--provenance",
        choices=sorted(c.PROVENANCE_VALUES),
        default=None,
        help=(
            "Optional UNVERIFIED evidence-provenance claim. Stored as-is with "
            "provenance_verified=false; never verified, inferred, or evaluated. "
            f"Defaults to '{c.DEFAULT_PROVENANCE}'."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.provenance is None:
        claimed = c.DEFAULT_PROVENANCE
        claim_source = c.PROVENANCE_CLAIM_SOURCE_DEFAULT
    else:
        claimed = args.provenance
        claim_source = c.PROVENANCE_CLAIM_SOURCE_OPERATOR

    try:
        result = ingest_observation(
            source_path=Path(args.source),
            dest_root=Path(args.dest),
            claimed_provenance=claimed,
            provenance_claim_source=claim_source,
        )
    except ArchiveLayoutError as exc:
        print(json.dumps({"error": "archive_layout_error", "token": exc.token}), file=sys.stderr)
        return 2
    except ExistingRecordIntegrityError as exc:
        print(
            json.dumps(
                {
                    "error": "existing_archive_record_integrity_error",
                    "token": exc.token,
                    "record_basename": exc.record_basename,
                }
            ),
            file=sys.stderr,
        )
        return 2
    except ArchiveIngestionError as exc:
        print(json.dumps({"error": "archive_ingestion_error", "token": exc.token}), file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "decision": result.decision,
                "archived_path": result.archived_path,
                "reason_tokens": list(result.reason_tokens),
                "duplicate": result.duplicate,
                "conflict": result.conflict,
                "source_file_sha256": result.source_file_sha256,
                "source_canonical_payload_sha256": result.source_canonical_payload_sha256,
                "record_content_sha256": result.record_content_sha256,
            },
            sort_keys=True,
        )
    )
    if result.decision == c.DECISION_REJECTED:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
