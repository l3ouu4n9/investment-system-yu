"""Phase 2B-2 deterministic archive-integrity index for one scanned archive.

Consumes one read-only :class:`~.archive_scan.ArchiveScan` plus the pure
Phase 2B-1 :func:`~.record_verifier.verify_archive_record` results and emits a
report-only ``retirement_archive_index_v1`` mapping describing archive
integrity - and nothing else.  It computes no coverage, no evidence
sufficiency, no verified provenance, no retirement readiness, and no fallback
or permission recommendation; nothing in the runtime consumes it.

Determinism: the same archive bytes, the same code-owned contract versions,
and the same effective limits always produce the same semantic report and the
same ``report_content_sha256``.  ``archive_clean`` means the complete required
source set was observed consistently through the scanner's final bounded
revalidation while no compliant Phase 2A writer using the same anchor acquired
its exclusive lease.  It does not prove external filesystem quiescence,
immutability, authenticity, malicious-operator resistance, legacy-writer
compliance, protection across different anchors, or power-loss durability.
The report carries no generated-at timestamp,
no absolute path, no source basename, no observation payload, and no raw
operator content; unsafe entry names appear only as deterministic digests.

Duplicate/conflict taxonomy (archive-integrity inventory only):

* exact_archive_record_duplicate  - warning, blocks ``archive_clean``
* canonical_payload_duplicate     - warning, blocks ``archive_clean``
* physical_observation_duplicate  - warning, blocks ``archive_clean``
* observation_id_conflict         - integrity failure
* logical_coverage_repeat         - informational only (never blocks clean;
  an inventory label, not coverage satisfaction or aggregation)
* partition_conflict              - integrity failure

Only records the pure verifier proved content-valid contribute trusted
duplicate/conflict keys; corrupt, unreadable, schema-incompatible, or
identity-invalid records never do.  Rejected reason records never contribute
observation identities - only scanner-observed byte identity.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from investment_orchestrator.research import step1a_retirement_observation as p1a

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence import archive_coordination as coord
from investment_orchestrator.offline.retirement_evidence import archive_scan as scanmod
from investment_orchestrator.offline.retirement_evidence import record_verifier as rv
from investment_orchestrator.offline.retirement_evidence.archive_scan import (
    ArchiveScan,
    ScanLimits,
    ScannedEntry,
    scan_archive,
)


INDEX_SCHEMA_VERSION = "retirement_archive_index_v1"
INDEXER_VERSION = "retirement_archive_indexer_v1"
# Code-owned contract label for the pure Phase 2B-1 verifier this index uses.
RECORD_VERIFIER_CONTRACT = "phase2b1_record_verifier_v1"
# Fixed label - the report never carries a lexical or resolved root path.
ARCHIVE_ROOT_LABEL = "archive_root"

OBSERVED_CONTRACT_PARTITION_SCHEMA = "observed_contract_partition_v1"
MINIMAL_MATERIAL_SCHEMA = "minimal_builder_internal_error_v1"

# --- archive assessment states (archive integrity ONLY) -----------------------
ASSESSMENT_UNVERIFIABLE = "archive_unverifiable"
ASSESSMENT_INTEGRITY_FAILURES = "archive_has_integrity_failures"
ASSESSMENT_WARNINGS = "archive_has_warnings"
ASSESSMENT_CLEAN = "archive_clean"

# Index-level tokens (scanner tokens pass through unchanged).
TOKEN_RECORD_SCHEMA_UNRECOGNIZED = "record_schema_unrecognized"
TOKEN_RECORD_BYTES_UNREADABLE = "record_bytes_unreadable"
TOKEN_RECORD_ENTRY_METADATA_UNSAFE = "record_entry_metadata_unsafe"
TOKEN_RECORD_CORRUPT = "record_corrupt"
TOKEN_OBSERVATION_ID_CONFLICT = "observation_id_conflict"
TOKEN_PARTITION_CONFLICT = "partition_conflict"
TOKEN_EXACT_RECORD_DUPLICATE = "exact_archive_record_duplicate"
TOKEN_PAYLOAD_DUPLICATE = "canonical_payload_duplicate"
TOKEN_PHYSICAL_OBSERVATION_DUPLICATE = "physical_observation_duplicate"
TOKEN_RECORD_FILENAME_MISMATCH = "record_filename_mismatch"
TOKEN_LOGICAL_COVERAGE_REPEAT = "logical_coverage_repeat"
TOKEN_MULTIPLE_CONTRACT_PARTITIONS = "multiple_provisional_contract_partitions"
TOKEN_CANONICAL_SCAN_INCOMPLETE = "canonical_scan_incomplete"
TOKEN_MANIFEST_RECONCILIATION_FAILED = "manifest_reconciliation_failed"

# Duplicate/conflict categories.
CATEGORY_EXACT_RECORD_DUPLICATE = "exact_archive_record_duplicate"
CATEGORY_PAYLOAD_DUPLICATE = "canonical_payload_duplicate"
CATEGORY_PHYSICAL_OBSERVATION_DUPLICATE = "physical_observation_duplicate"
CATEGORY_OBSERVATION_ID_CONFLICT = "observation_id_conflict"
CATEGORY_LOGICAL_COVERAGE_REPEAT = "logical_coverage_repeat"
CATEGORY_PARTITION_CONFLICT = "partition_conflict"

SEVERITY_WARNING = "warning"
SEVERITY_INTEGRITY_FAILURE = "integrity_failure"
SEVERITY_INFORMATIONAL = "informational"

# Exact required authority envelope: this index is inert inventory.
AUTHORITY_ENVELOPE: dict[str, Any] = {
    "is_llm_generated": False,
    "report_only": True,
    "not_authorization": True,
    "not_execution_authorization": True,
    "permission_effect": "none",
    "consumed_by_gates": False,
    "consumed_by_order_path": False,
    "consumed_by_downstream": False,
    "safe_to_ignore": True,
    "assessment_scope": "archive_integrity_only",
}

NON_AUTHORIZATION_NOTE = (
    "Archive-integrity inventory only. This report evaluates no evidence "
    "coverage or sufficiency, verifies no provenance, grants no permission, "
    "recommends no retirement, fallback, or deletion, and is never consumed "
    "by any gate, order path, or runtime workflow."
)


class ArchiveIndexError(Exception):
    """Internal index-construction failure; carries a safe token only."""

    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(token)


class ArchiveOutputError(Exception):
    """Output containment/writeability failure; carries a safe token only."""

    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(token)


@dataclass(frozen=True)
class ArchiveIndexOperationResult:
    report: dict[str, Any]
    resolved_output_path: Path | None


def index_archive(
    archive_root: Any,
    limits: ScanLimits | None = None,
    *,
    coordination_path: Any = None,
) -> dict[str, Any]:
    """Build one report while owning a shared lease for the full operation."""
    return index_archive_operation(
        archive_root,
        limits,
        coordination_path=coordination_path,
    ).report


def index_archive_operation(
    archive_root: Any,
    limits: ScanLimits | None = None,
    *,
    coordination_path: Any = None,
    output_path: Any = None,
) -> ArchiveIndexOperationResult:
    """Build one report and validate optional output containment under the lease."""
    effective = limits if limits is not None else ScanLimits()
    try:
        with coord.acquire_coordination_lease(
            coordination_path,
            archive_root=archive_root,
            mode=coord.LOCK_MODE_SHARED,
        ) as lease:
            scanned = scan_archive(archive_root, effective, lease=lease)
            report = build_archive_index(scanned, lease=lease)
            resolved_output = (
                _resolve_output_path_outside_archive(
                    output_path, scanned._resolved_archive_root
                )
                if output_path is not None
                else None
            )
            coord.complete_coordination_operation(
                lease,
                archive_root=archive_root,
                expected_mode=coord.LOCK_MODE_SHARED,
            )
            return ArchiveIndexOperationResult(report, resolved_output)
    except coord.CoordinationError as exc:
        if output_path is not None:
            raise
        return ArchiveIndexOperationResult(
            build_archive_index(
                _empty_scan(effective),
                coordination_failure_token=exc.token,
            ),
            None,
        )


def _resolve_output_path_outside_archive(
    output_path: Any, resolved_archive_root: Path | None
) -> Path:
    if resolved_archive_root is None:
        raise ArchiveOutputError("output_write_failed")
    lexical_output = Path(output_path)
    try:
        try:
            output_lstat = os.lstat(lexical_output)
        except FileNotFoundError:
            output_lstat = None
        if output_lstat is not None and stat.S_ISLNK(output_lstat.st_mode):
            lexical_output.resolve(strict=True)
        resolved_output = lexical_output.resolve()
    except (OSError, RuntimeError, ValueError):
        raise ArchiveOutputError("output_write_failed") from None
    if resolved_output == resolved_archive_root or resolved_output.is_relative_to(
        resolved_archive_root
    ):
        raise ArchiveOutputError("output_inside_archive_root")
    return resolved_output


def serialize_index_report(report: Mapping[str, Any]) -> str:
    """Human-readable deterministic serialization (sorted, indented, newline)."""
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def compute_report_content_sha256(report: Mapping[str, Any]) -> str | None:
    """Recompute the report self-hash, excluding only the self-hash field."""
    body = {k: v for k, v in report.items() if k != "report_content_sha256"}
    return p1a.canonical_sha256(body)


# --- index construction --------------------------------------------------------
def build_archive_index(
    scan: ArchiveScan,
    *,
    lease: coord.VerifiedCoordinationLease | None = None,
    coordination_failure_token: str | None = None,
) -> dict[str, Any]:
    if type(scan) is not ArchiveScan:
        return build_archive_index(
            _empty_scan(ScanLimits()),
            coordination_failure_token=coord.TOKEN_LEASE_INVALID,
        )
    if not _scan_population_shape_valid(scan):
        return build_archive_index(
            _empty_scan(
                scan.effective_limits
                if type(scan.effective_limits) is ScanLimits
                else ScanLimits()
            ),
            coordination_failure_token=coord.TOKEN_LEASE_INVALID,
        )
    coordination_token = (
        coordination_failure_token
        if coordination_failure_token in coord.FAILURE_TOKENS
        else coord.TOKEN_LEASE_INVALID
        if coordination_failure_token is not None
        else None
    )
    coordination_verified = False
    completion: Any = None
    if coordination_token is None:
        try:
            completion = scanmod.validated_scan_completion(scan, lease)
            coordination_verified = True
        except coord.CoordinationError as exc:
            coordination_token = exc.token

    unverifiable = set(scan.unverifiable_tokens)
    if coordination_token is not None:
        unverifiable.add(coordination_token)
    integrity: set[str] = set()
    warnings = set(scan.warning_tokens)
    informational: set[str] = set()

    verified: list[tuple[ScannedEntry, rv.RecordVerificationResult]] = []
    unread_candidates: list[ScannedEntry] = []
    unexpected: list[ScannedEntry] = []
    for entry in scan.entries:
        if not entry.source_candidate:
            unexpected.append(entry)
        elif (
            coordination_verified
            and
            entry.stable_read_state == scanmod.READ_STABLE
            and entry.final_revalidation_state == scanmod.REVALIDATION_STABLE
            and entry.record_bytes is not None
        ):
            result = rv.verify_archive_record(
                entry.record_bytes,
                filename=entry.safe_name,
                expected_partition=entry.location,
            )
            verified.append((entry, result))
        else:
            unread_candidates.append(entry)

    _collect_result_tokens(verified, unverifiable, integrity, warnings)

    partition_ids = _provisional_partition_ids(verified)
    duplicate_groups = _duplicate_groups(verified, partition_ids)
    for group in duplicate_groups:
        _add_group_token(group, integrity, warnings, informational)

    partitions_report = _partitions_report(verified, partition_ids)
    if len(partitions_report) > 1:
        informational.add(TOKEN_MULTIPLE_CONTRACT_PARTITIONS)

    record_entries = sorted(
        (
            *(
                _record_entry_report(entry, result, partition_ids.get(entry.reference))
                for entry, result in verified
            ),
            *(_record_entry_report(entry, None, None) for entry in unread_candidates),
        ),
        key=lambda item: item["entry"],
    )
    unexpected_entries = sorted(
        (_unexpected_entry_report(entry) for entry in unexpected),
        key=lambda item: item["entry"],
    )
    # The manifest contains exactly one terminal outcome for every name in the
    # complete initial partition snapshots.  Regular entries are bounded record
    # candidates; disappearance, type transition, classification failure,
    # instability, or resource exhaustion remains an unread candidate outcome.
    result_by_reference = {entry.reference: result for entry, result in verified}
    manifest = sorted(
        (
            {
                "entry": entry.reference,
                "stable_read_state": entry.stable_read_state,
                "final_revalidation_state": entry.final_revalidation_state,
                "verification_state": (
                    result_by_reference[entry.reference].verification_state
                    if entry.reference in result_by_reference
                    else None
                ),
                "observed_file_sha256": entry.file_sha256,
                "observed_byte_length": entry.byte_length,
            }
            for entry in (*[entry for entry, _result in verified], *unread_candidates)
        ),
        key=lambda item: item["entry"],
    )
    manifest_sha256 = p1a.canonical_sha256(manifest)
    if manifest_sha256 is None:  # pragma: no cover - manifest is always serializable
        raise ArchiveIndexError("index_source_manifest_not_serializable")

    # Candidate population: every scanner-emitted name from the complete initial
    # partition snapshots, including classification/type anomalies, disappeared,
    # oversized, unstable, unreadable, and total-limit-blocked entries.  Truncated
    # inventories remain explicitly unverifiable and make no completeness
    # claim.  ``unread_record_count`` below is exactly the candidates without
    # successful stable bytes/verifier results.
    candidate_count = len(verified) + len(unread_candidates)
    counts_by_partition = {partition: 0 for partition in c.PARTITIONS}
    for entry, _result in verified:
        counts_by_partition[entry.location] += 1
    for entry in unread_candidates:
        counts_by_partition[entry.location] += 1
    counts_by_verification_state: dict[str, int] = {}
    counts_by_stable_read_state: dict[str, int] = {}
    counts_by_final_revalidation_state: dict[str, int] = {}
    for entry in (*[entry for entry, _result in verified], *unread_candidates):
        assert entry.stable_read_state is not None  # candidate classification invariant
        counts_by_stable_read_state[entry.stable_read_state] = (
            counts_by_stable_read_state.get(entry.stable_read_state, 0) + 1
        )
        if entry.final_revalidation_state is not None:
            counts_by_final_revalidation_state[entry.final_revalidation_state] = (
                counts_by_final_revalidation_state.get(entry.final_revalidation_state, 0) + 1
            )
    for _entry, result in verified:
        counts_by_verification_state[result.verification_state] = (
            counts_by_verification_state.get(result.verification_state, 0) + 1
        )
    for entry in unread_candidates:
        # A failed final observation supersedes the initially stable read for
        # trust and count purposes while preserving both states in the manifest.
        terminal_state = entry.final_revalidation_state or entry.stable_read_state
        assert terminal_state is not None  # candidate classification invariant
        counts_by_verification_state[terminal_state] = (
            counts_by_verification_state.get(terminal_state, 0) + 1
        )

    # The complete semantic operation is gated by the exact live capability
    # bound into the scan result.  Caller booleans/report fields cannot satisfy
    # this check.
    if coordination_verified:
        try:
            assert lease is not None
            completion = scanmod.validated_scan_completion(scan, lease)
        except coord.CoordinationError as exc:
            coordination_verified = False
            coordination_token = exc.token
            unverifiable.add(exc.token)

    reconciliation_ok = _manifest_and_count_reconcile(
        scan=scan,
        manifest=manifest,
        candidate_count=candidate_count,
        verified_count=len(verified),
        unread_count=len(unread_candidates),
        record_entry_count=len(record_entries),
        counts_by_partition=counts_by_partition,
        counts_by_stable_read_state=counts_by_stable_read_state,
        counts_by_final_revalidation_state=counts_by_final_revalidation_state,
        counts_by_verification_state=counts_by_verification_state,
    )
    if not reconciliation_ok:
        unverifiable.add(TOKEN_MANIFEST_RECONCILIATION_FAILED)

    clean_prerequisites_met = _canonical_clean_prerequisites(
        scan=scan,
        completion=completion,
        coordination_verified=coordination_verified,
        candidate_count=candidate_count,
        verified_count=len(verified),
        unread_count=len(unread_candidates),
        reconciliation_ok=reconciliation_ok,
        counts_by_final_revalidation_state=counts_by_final_revalidation_state,
        unverifiable=unverifiable,
        integrity=integrity,
        warnings=warnings,
    )
    if not clean_prerequisites_met and not (unverifiable or integrity or warnings):
        unverifiable.add(TOKEN_CANONICAL_SCAN_INCOMPLETE)

    assessment = _assessment_state(unverifiable, integrity, warnings)

    report: dict[str, Any] = {
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "authority_envelope": dict(AUTHORITY_ENVELOPE),
        "non_authorization_note": NON_AUTHORIZATION_NOTE,
        "tool_versions": {
            "archive_scanner_version": scan.scanner_version,
            "archive_indexer_version": INDEXER_VERSION,
            "record_verifier_contract": RECORD_VERIFIER_CONTRACT,
            "recognized_archive_layout_version": c.ARCHIVE_LAYOUT_VERSION,
            "recognized_archive_record_schema_versions": sorted(
                (c.ARCHIVE_RECORD_SCHEMA_VERSION, c.ARCHIVE_REJECTED_RECORD_SCHEMA_VERSION)
            ),
        },
        "archive_root_label": ARCHIVE_ROOT_LABEL,
        "coordination_contract_version": coord.COORDINATION_CONTRACT_VERSION,
        "coordination_status": (
            coord.STATUS_VERIFIED if coordination_verified else coord.STATUS_FAILED
        ),
        "coordination_lock_mode": coord.LOCK_MODE_SHARED,
        "coordination_scope": coord.COORDINATION_SCOPE,
        "repository_writer_quiescence_verified": coordination_verified,
        "external_filesystem_quiescence_verified": False,
        "archive_layout_version": scan.archive_layout_version,
        "archive_layout_status": scan.layout_status,
        "verification_limits": scan.effective_limits.as_report_mapping(),
        "direct_entry_count": scan.direct_entry_count,
        "entry_inventory_truncated": scan.entry_inventory_truncated,
        "total_bytes_read": scan.total_bytes_read,
        "source_record_count": candidate_count,
        "unread_record_count": len(unread_candidates),
        "counts_by_partition": counts_by_partition,
        "counts_by_stable_read_state": counts_by_stable_read_state,
        "counts_by_final_revalidation_state": counts_by_final_revalidation_state,
        "counts_by_verification_state": counts_by_verification_state,
        "source_set_manifest": manifest,
        "indexed_source_set_sha256": manifest_sha256,
        "record_entries": record_entries,
        "unexpected_entries": unexpected_entries,
        "duplicate_groups": duplicate_groups,
        "provisional_contract_partitions": partitions_report,
        "archive_assessment_state": assessment,
        "assessment_reason_tokens": {
            "unverifiable": sorted(unverifiable),
            "integrity_failures": sorted(integrity),
            "warnings": sorted(warnings),
            "informational": sorted(informational),
        },
    }
    digest = compute_report_content_sha256(report)
    if digest is None:  # pragma: no cover - report is always serializable
        raise ArchiveIndexError("index_report_not_serializable")
    report["report_content_sha256"] = digest
    if coordination_verified:
        try:
            assert lease is not None
            scanmod.validate_scan_lease_binding(scan, lease)
        except coord.CoordinationError as exc:
            _fail_coordination_report(report, exc.token)
    return report


def _empty_scan(limits: ScanLimits) -> ArchiveScan:
    """Minimal no-archive-observation input for coordination acquisition failure."""
    return ArchiveScan(
        scanner_version=scanmod.SCANNER_VERSION,
        effective_limits=limits,
        layout_status=scanmod.LAYOUT_NOT_SCANNED,
        archive_layout_version=None,
        entries=(),
        unverifiable_tokens=(),
        warning_tokens=(),
        direct_entry_count=0,
        total_bytes_read=0,
        entry_inventory_truncated=False,
        _construction_token=object(),
    )


def _scan_population_shape_valid(scan: ArchiveScan) -> bool:
    if type(scan.effective_limits) is not ScanLimits:
        return False
    if not all(
        isinstance(value, tuple)
        for value in (
            scan.entries,
            scan.unverifiable_tokens,
            scan.warning_tokens,
        )
    ):
        return False
    if not all(isinstance(token, str) for token in (*scan.unverifiable_tokens, *scan.warning_tokens)):
        return False
    for entry in scan.entries:
        if type(entry) is not ScannedEntry:
            return False
        if entry.source_candidate:
            if entry.location not in c.PARTITIONS or entry.stable_read_state is None:
                return False
        elif entry.stable_read_state is not None or entry.final_revalidation_state is not None:
            return False
        if entry.safe_relative_path is None and entry.safe_name is not None:
            return False
    return True


def _fail_coordination_report(report: dict[str, Any], token: str) -> None:
    """Make a completed semantic report deterministically coordination-failed."""
    report["coordination_status"] = coord.STATUS_FAILED
    report["repository_writer_quiescence_verified"] = False
    report["archive_assessment_state"] = ASSESSMENT_UNVERIFIABLE
    reasons = report["assessment_reason_tokens"]["unverifiable"]
    report["assessment_reason_tokens"]["unverifiable"] = sorted({*reasons, token})
    digest = compute_report_content_sha256(report)
    if digest is None:  # pragma: no cover - report is always serializable
        raise ArchiveIndexError("index_report_not_serializable")
    report["report_content_sha256"] = digest


def _manifest_and_count_reconcile(
    *,
    scan: ArchiveScan,
    manifest: list[dict[str, Any]],
    candidate_count: int,
    verified_count: int,
    unread_count: int,
    record_entry_count: int,
    counts_by_partition: Mapping[str, int],
    counts_by_stable_read_state: Mapping[str, int],
    counts_by_final_revalidation_state: Mapping[str, int],
    counts_by_verification_state: Mapping[str, int],
) -> bool:
    """Reconcile every independently constructed source-population count."""
    candidate_entries = tuple(entry for entry in scan.entries if entry.source_candidate)
    candidate_references = tuple(entry.reference for entry in candidate_entries)
    manifest_references = tuple(item["entry"] for item in manifest)
    initially_stable = sum(
        1 for entry in candidate_entries if entry.stable_read_state == scanmod.READ_STABLE
    )
    return all(
        (
            candidate_count == len(candidate_entries),
            candidate_count == verified_count + unread_count,
            candidate_count == len(manifest),
            candidate_count == record_entry_count,
            len(set(candidate_references)) == len(candidate_references),
            len(set(manifest_references)) == len(manifest_references),
            set(candidate_references) == set(manifest_references),
            sum(counts_by_partition.values()) == candidate_count,
            sum(counts_by_stable_read_state.values()) == candidate_count,
            sum(counts_by_verification_state.values()) == candidate_count,
            sum(counts_by_final_revalidation_state.values()) == initially_stable,
            set(counts_by_partition) == set(c.PARTITIONS),
        )
    )


def _canonical_clean_prerequisites(
    *,
    scan: ArchiveScan,
    completion: Any,
    coordination_verified: bool,
    candidate_count: int,
    verified_count: int,
    unread_count: int,
    reconciliation_ok: bool,
    counts_by_final_revalidation_state: Mapping[str, int],
    unverifiable: set[str],
    integrity: set[str],
    warnings: set[str],
) -> bool:
    """Single code-owned gate that must succeed before clean is possible."""
    if completion is None:
        return False
    return all(
        (
            coordination_verified,
            completion.all_required_phases_completed,
            completion.final_inventory_completed,
            completion.required_identity_validation_completed,
            scan.layout_status == scanmod.LAYOUT_CANONICAL,
            scan.archive_layout_version == c.ARCHIVE_LAYOUT_VERSION,
            not scan.entry_inventory_truncated,
            scan.total_bytes_read <= scan.effective_limits.max_total_read_bytes,
            reconciliation_ok,
            unread_count == 0,
            verified_count == candidate_count,
            counts_by_final_revalidation_state.get(
                scanmod.REVALIDATION_STABLE, 0
            )
            == candidate_count,
            set(counts_by_final_revalidation_state).issubset(
                {scanmod.REVALIDATION_STABLE}
            ),
            not unverifiable,
            not integrity,
            not warnings,
        )
    )


def _collect_result_tokens(
    verified: list[tuple[ScannedEntry, rv.RecordVerificationResult]],
    unverifiable: set[str],
    integrity: set[str],
    warnings: set[str],
) -> None:
    for _entry, result in verified:
        if result.content_status == rv.CONTENT_SCHEMA_INCOMPATIBLE:
            # An unrecognized record schema prevents full verification of the
            # source set - fail closed to unverifiable, not merely corrupt.
            unverifiable.add(TOKEN_RECORD_SCHEMA_UNRECOGNIZED)
        elif result.content_status == rv.CONTENT_CORRUPT:
            integrity.add(TOKEN_RECORD_CORRUPT)
        elif result.content_status != rv.CONTENT_VALID:  # pragma: no cover - defensive
            unverifiable.add(TOKEN_RECORD_BYTES_UNREADABLE)
        if result.placement_status == rv.PLACEMENT_UNSAFE_ENTRY_METADATA:
            # Defensive: the scanner only submits safe names and partitions.
            unverifiable.add(TOKEN_RECORD_ENTRY_METADATA_UNSAFE)
        if result.content_status == rv.CONTENT_VALID:
            if rv.FINDING_RECORD_PARTITION_MISMATCH in result.placement_findings:
                integrity.add(TOKEN_PARTITION_CONFLICT)
            if rv.FINDING_RECORD_FILENAME_MISMATCH in result.placement_findings:
                warnings.add(TOKEN_RECORD_FILENAME_MISMATCH)


def _assessment_state(
    unverifiable: set[str], integrity: set[str], warnings: set[str]
) -> str:
    if unverifiable:
        return ASSESSMENT_UNVERIFIABLE
    if integrity:
        return ASSESSMENT_INTEGRITY_FAILURES
    if warnings:
        return ASSESSMENT_WARNINGS
    return ASSESSMENT_CLEAN


# --- provisional compatibility partitions ---------------------------------------
def _provisional_partition_ids(
    verified: list[tuple[ScannedEntry, rv.RecordVerificationResult]],
) -> dict[str, str]:
    """Map entry reference -> observed contract partition id (identity-valid only)."""
    ids: dict[str, str] = {}
    for entry, result in verified:
        material = _partition_material(entry, result)
        if material is None:
            continue
        digest = p1a.canonical_sha256(material)
        if digest is not None:
            ids[entry.reference] = digest
    return ids


def _partition_material(
    entry: ScannedEntry, result: rv.RecordVerificationResult
) -> dict[str, Any] | None:
    """Conservative inventory material for one identity-valid observation record.

    Built only from facts the pure verifier already validated; the payload is
    re-materialized from the verified bytes solely to read those validated
    fields.  The ``verifier_recognized_*`` entry is a verifier-side constant,
    not a value observed in the payload.  This is an inventory label ONLY -
    never an evidence-compatibility or coverage decision.
    """
    if not result.identity_facts_valid or entry.record_bytes is None:
        return None
    record = json.loads(entry.record_bytes.decode("utf-8"))
    payload = record["observation_payload"]
    if "classification_contract_version" not in payload:
        # The strict classifier admits exactly two shapes; without the
        # full-shape marker this is the minimal builder-internal-error record.
        return {
            "material_schema": MINIMAL_MATERIAL_SCHEMA,
            "observed_source_schema_version": payload["schema_version"],
            "verifier_recognized_classification_contract_version": (
                p1a.CLASSIFICATION_CONTRACT_VERSION
            ),
        }
    return {
        "material_schema": OBSERVED_CONTRACT_PARTITION_SCHEMA,
        "observed_source_schema_version": payload["schema_version"],
        "observed_classification_contract_version": payload["classification_contract_version"],
        "observed_contract_versions": {
            key: payload["contract_versions"][key]
            for key in sorted(payload["contract_versions"])
        },
        "observed_code_identity_state": payload["code_identity"]["git_state"],
        "observed_clean_source_git_commit": result.source_git_commit,
        "verifier_recognized_classification_contract_version": (
            p1a.CLASSIFICATION_CONTRACT_VERSION
        ),
    }


def _partitions_report(
    verified: list[tuple[ScannedEntry, rv.RecordVerificationResult]],
    partition_ids: dict[str, str],
) -> list[dict[str, Any]]:
    members: dict[str, list[str]] = {}
    materials: dict[str, dict[str, Any]] = {}
    for entry, result in verified:
        partition_id = partition_ids.get(entry.reference)
        if partition_id is None:
            continue
        members.setdefault(partition_id, []).append(entry.reference)
        if partition_id not in materials:
            material = _partition_material(entry, result)
            assert material is not None  # partition_ids only maps valid entries
            materials[partition_id] = material
    return [
        {
            "observed_contract_partition_id": partition_id,
            "material": materials[partition_id],
            "member_count": len(refs),
            "members": sorted(refs),
        }
        for partition_id, refs in sorted(members.items())
    ]


# --- duplicate / conflict taxonomy ----------------------------------------------
def _duplicate_groups(
    verified: list[tuple[ScannedEntry, rv.RecordVerificationResult]],
    partition_ids: dict[str, str],
) -> list[dict[str, Any]]:
    observation_valid = [
        (entry, result)
        for entry, result in verified
        if result.identity_facts_valid
    ]
    rejected_valid = [
        (entry, result)
        for entry, result in verified
        if result.content_status == rv.CONTENT_VALID
        and result.record_kind == rv.RECORD_KIND_REJECTED
    ]

    groups: list[dict[str, Any]] = []

    # A. Exact archive-record duplicate (verified record mapping hash).
    by_record_hash: dict[str, list[str]] = {}
    for entry, result in observation_valid:
        if result.archive_record_content_sha256 is not None:
            by_record_hash.setdefault(result.archive_record_content_sha256, []).append(
                entry.reference
            )
    for record_hash, refs in by_record_hash.items():
        if len(refs) >= 2:
            groups.append(
                _group(
                    CATEGORY_EXACT_RECORD_DUPLICATE,
                    SEVERITY_WARNING,
                    blocks_clean=True,
                    key_domain="verified_archive_record_content_sha256",
                    key={"archive_record_content_sha256": record_hash},
                    members=refs,
                )
            )

    # A. Exact archive-record duplicate for rejected reason records: keyed by
    # scanner-observed byte identity - a scan fact, never embedded integrity.
    by_rejected_bytes: dict[str, list[str]] = {}
    for entry, _result in rejected_valid:
        if entry.file_sha256 is not None:
            by_rejected_bytes.setdefault(entry.file_sha256, []).append(entry.reference)
    for file_hash, refs in by_rejected_bytes.items():
        if len(refs) >= 2:
            groups.append(
                _group(
                    CATEGORY_EXACT_RECORD_DUPLICATE,
                    SEVERITY_WARNING,
                    blocks_clean=True,
                    key_domain="observed_file_sha256",
                    key={"observed_file_sha256": file_hash},
                    members=refs,
                )
            )

    # B. Canonical payload duplicate (content-valid observation records).
    by_payload: dict[str, list[str]] = {}
    for entry, result in observation_valid:
        if result.source_canonical_payload_sha256 is not None:
            by_payload.setdefault(result.source_canonical_payload_sha256, []).append(
                entry.reference
            )
    for payload_hash, refs in by_payload.items():
        if len(refs) >= 2:
            groups.append(
                _group(
                    CATEGORY_PAYLOAD_DUPLICATE,
                    SEVERITY_WARNING,
                    blocks_clean=True,
                    key_domain="source_canonical_payload_sha256",
                    key={"source_canonical_payload_sha256": payload_hash},
                    members=refs,
                )
            )

    # C. Physical-observation duplicate (full observation id + payload hash).
    by_physical: dict[tuple[str, str], list[str]] = {}
    # D. Observation-id conflict (one id, multiple payload hashes).
    by_observation_id: dict[str, dict[str, list[str]]] = {}
    # F. Partition conflict via incompatible decisions for one identity.
    decisions_by_id: dict[str, dict[str, list[str]]] = {}
    decisions_by_payload: dict[str, dict[str, list[str]]] = {}
    for entry, result in observation_valid:
        payload_hash = result.source_canonical_payload_sha256
        if payload_hash is None:  # pragma: no cover - identity-valid always has it
            continue
        if result.ingestion_decision is not None:
            decisions_by_payload.setdefault(payload_hash, {}).setdefault(
                result.ingestion_decision, []
            ).append(entry.reference)
        if result.observation_id is None:
            continue
        by_physical.setdefault((result.observation_id, payload_hash), []).append(
            entry.reference
        )
        by_observation_id.setdefault(result.observation_id, {}).setdefault(
            payload_hash, []
        ).append(entry.reference)
        if result.ingestion_decision is not None:
            decisions_by_id.setdefault(result.observation_id, {}).setdefault(
                result.ingestion_decision, []
            ).append(entry.reference)

    for (observation_id, payload_hash), refs in by_physical.items():
        if len(refs) >= 2:
            groups.append(
                _group(
                    CATEGORY_PHYSICAL_OBSERVATION_DUPLICATE,
                    SEVERITY_WARNING,
                    blocks_clean=True,
                    key_domain="observation_id_and_payload_sha256",
                    key={
                        "observation_id": observation_id,
                        "source_canonical_payload_sha256": payload_hash,
                    },
                    members=refs,
                )
            )

    for observation_id, payload_map in by_observation_id.items():
        if len(payload_map) >= 2:
            refs = [ref for refs in payload_map.values() for ref in refs]
            groups.append(
                _group(
                    CATEGORY_OBSERVATION_ID_CONFLICT,
                    SEVERITY_INTEGRITY_FAILURE,
                    blocks_clean=True,
                    key_domain="observation_id",
                    key={"observation_id": observation_id},
                    members=refs,
                    extra={"distinct_payload_sha256": sorted(payload_map)},
                )
            )

    # E. Logical-coverage repeat: same provisional partition + coverage key,
    # different observation ids.  Inventory label only - informational.
    by_logical: dict[tuple[str, str], dict[str, list[str]]] = {}
    for entry, result in observation_valid:
        partition_id = partition_ids.get(entry.reference)
        if partition_id is None or result.coverage_key is None or result.observation_id is None:
            continue
        by_logical.setdefault((partition_id, result.coverage_key), {}).setdefault(
            result.observation_id, []
        ).append(entry.reference)
    for (partition_id, coverage_key), id_map in by_logical.items():
        if len(id_map) >= 2:
            refs = [ref for refs in id_map.values() for ref in refs]
            groups.append(
                _group(
                    CATEGORY_LOGICAL_COVERAGE_REPEAT,
                    SEVERITY_INFORMATIONAL,
                    blocks_clean=False,
                    key_domain="provisional_partition_and_coverage_key",
                    key={
                        "observed_contract_partition_id": partition_id,
                        "coverage_key": coverage_key,
                    },
                    members=refs,
                    extra={"distinct_observation_ids": sorted(id_map)},
                )
            )

    # F. Partition conflict: physical placement inconsistent with the verified
    # envelope decision (per record), or one identity carrying incompatible
    # decisions across records.
    mismatch_by_pair: dict[tuple[str, str], list[str]] = {}
    for entry, result in verified:
        if (
            result.content_status == rv.CONTENT_VALID
            and rv.FINDING_RECORD_PARTITION_MISMATCH in result.placement_findings
            and result.ingestion_decision is not None
        ):
            mismatch_by_pair.setdefault(
                (entry.location, result.ingestion_decision), []
            ).append(entry.reference)
    for (location, decision), refs in mismatch_by_pair.items():
        groups.append(
            _group(
                CATEGORY_PARTITION_CONFLICT,
                SEVERITY_INTEGRITY_FAILURE,
                blocks_clean=True,
                key_domain="physical_partition_vs_envelope_decision",
                key={"physical_partition": location, "ingestion_decision": decision},
                members=refs,
            )
        )
    for key_domain, decision_maps in (
        ("observation_id", decisions_by_id),
        ("source_canonical_payload_sha256", decisions_by_payload),
    ):
        for identity, decision_map in decision_maps.items():
            if len(decision_map) >= 2:
                refs = [ref for refs in decision_map.values() for ref in refs]
                groups.append(
                    _group(
                        CATEGORY_PARTITION_CONFLICT,
                        SEVERITY_INTEGRITY_FAILURE,
                        blocks_clean=True,
                        key_domain=f"incompatible_decisions_by_{key_domain}",
                        key={key_domain: identity},
                        members=refs,
                        extra={"distinct_decisions": sorted(decision_map)},
                    )
                )

    return sorted(
        groups,
        key=lambda group: (
            group["category"],
            group["key_domain"],
            json.dumps(group["key"], sort_keys=True),
        ),
    )


def _group(
    category: str,
    severity: str,
    *,
    blocks_clean: bool,
    key_domain: str,
    key: dict[str, str],
    members: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    group: dict[str, Any] = {
        "category": category,
        "severity": severity,
        "blocks_archive_clean": blocks_clean,
        "key_domain": key_domain,
        "key": key,
        "members": sorted(members),
    }
    if extra:
        group.update(extra)
    return group


def _add_group_token(
    group: Mapping[str, Any],
    integrity: set[str],
    warnings: set[str],
    informational: set[str],
) -> None:
    token = {
        CATEGORY_EXACT_RECORD_DUPLICATE: TOKEN_EXACT_RECORD_DUPLICATE,
        CATEGORY_PAYLOAD_DUPLICATE: TOKEN_PAYLOAD_DUPLICATE,
        CATEGORY_PHYSICAL_OBSERVATION_DUPLICATE: TOKEN_PHYSICAL_OBSERVATION_DUPLICATE,
        CATEGORY_OBSERVATION_ID_CONFLICT: TOKEN_OBSERVATION_ID_CONFLICT,
        CATEGORY_LOGICAL_COVERAGE_REPEAT: TOKEN_LOGICAL_COVERAGE_REPEAT,
        CATEGORY_PARTITION_CONFLICT: TOKEN_PARTITION_CONFLICT,
    }[group["category"]]
    if group["severity"] == SEVERITY_INTEGRITY_FAILURE:
        integrity.add(token)
    elif group["severity"] == SEVERITY_WARNING:
        warnings.add(token)
    else:
        informational.add(token)


# --- report entry shapes ---------------------------------------------------------
def _record_entry_report(
    entry: ScannedEntry,
    result: rv.RecordVerificationResult | None,
    partition_id: str | None,
) -> dict[str, Any]:
    content_valid = result is not None and result.content_status == rv.CONTENT_VALID
    findings: list[str] = []
    if result is not None:
        findings = sorted(
            set(result.integrity_findings)
            | set(result.compatibility_findings)
            | set(result.placement_findings)
            | set(result.informational_findings)
        )
    return {
        "entry": entry.reference,
        "entry_path_sha256": entry.entry_path_sha256,
        "expected_physical_partition": entry.location,
        "entry_kind": entry.entry_kind,
        "stable_read_state": entry.stable_read_state,
        "final_revalidation_state": entry.final_revalidation_state,
        "observed_file_sha256": entry.file_sha256,
        "observed_byte_length": entry.byte_length,
        "record_kind": result.record_kind if result is not None else None,
        "content_status": result.content_status if result is not None else None,
        "placement_status": result.placement_status if result is not None else None,
        "verification_state": result.verification_state if result is not None else None,
        "identity_facts_valid": result.identity_facts_valid if result is not None else None,
        "self_integrity_status": result.self_integrity_status if result is not None else None,
        "archive_record_content_sha256": (
            result.archive_record_content_sha256 if result is not None else None
        ),
        "source_canonical_payload_sha256": (
            result.source_canonical_payload_sha256 if result is not None else None
        ),
        "observation_id": result.observation_id if result is not None else None,
        "coverage_key": result.coverage_key if result is not None else None,
        "source_git_commit": result.source_git_commit if result is not None else None,
        "ingestion_decision": result.ingestion_decision if result is not None else None,
        "claimed_evidence_provenance": (
            result.claimed_evidence_provenance if result is not None else None
        ),
        # An unverified claim preserved as such: false when a validated claim
        # token exists, null otherwise.  Never a provenance authentication.
        "provenance_verified": (
            False
            if content_valid and result.claimed_evidence_provenance is not None
            else None
        ),
        "observed_contract_partition_id": partition_id,
        "findings": findings,
    }


def _unexpected_entry_report(entry: ScannedEntry) -> dict[str, Any]:
    classification = (
        "unexpected_archive_entry"
        if entry.entry_kind == scanmod.ENTRY_UNEXPECTED_REGULAR_FILE
        else "unsafe_archive_entry"
    )
    return {
        "entry": entry.reference,
        "entry_path_sha256": entry.entry_path_sha256,
        "location": entry.location,
        "entry_kind": entry.entry_kind,
        "classification": classification,
    }
