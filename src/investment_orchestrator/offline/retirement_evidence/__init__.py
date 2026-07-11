"""Phase 2A: offline archive contract + ingestion tool for Step 1A observations.

This package copies *valid* per-run ``step1a_retirement_observation_v1`` reports
into an append-only archive after strict, full-contract validation.  It performs
no aggregation, no coverage-sufficiency evaluation, and no retirement decision;
it authorizes nothing.  It is never imported by production runtime code.

Public surface:

* :mod:`.archive_contract`  - versions, partitions, provenance/reason vocab
* :mod:`.archive_coordination` - shared/exclusive offline archive leases
* :mod:`.archive_record_contract` - pure shared archive-record shape helpers
* :mod:`.source_validation` - strict full v1-contract validator + classifier
* :mod:`.ingest`            - ingestion library function + append-only writer
* :mod:`.record_verifier`   - pure single-record verifier (Phase 2B-1)
* :mod:`.archive_scan`      - read-only deterministic archive scanner (Phase 2B-2)
* :mod:`.archive_index`     - deterministic archive-integrity index (Phase 2B-2)
* :mod:`.cli`               - operator/CI command-line entry point (ingestion)
* :mod:`.verify_cli`        - operator/CI command-line entry point (archive index)
"""
