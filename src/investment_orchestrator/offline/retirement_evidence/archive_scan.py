"""Phase 2B-2 read-only deterministic scanner for one retirement-archive root.

Reads one explicit operator-supplied archive root and produces a deterministic,
raw-content-free inventory of its direct entries plus stably-read record bytes
for the pure Phase 2B-1 record verifier.  It requires and validates a live
shared lease acquired by the complete index operation, but never writes,
repairs, renames, moves, or deletes anything; it computes no coverage,
sufficiency, readiness, or retirement conclusion; it authorizes nothing.

Path policy (fail closed):

* The explicit root argument may itself be a symlink; it is resolved once up
  front.  The resolved root must be a readable directory.
* Below the resolved root nothing is ever followed: the layout-version file
  must be a regular non-symlink file, the three partitions must be regular
  non-symlink directories, and record entries must be direct regular
  non-symlink files.  Traversal depth is exactly one partition level.
* Symlinks (including dangling ones), FIFOs, sockets, devices, and nested
  directories are never opened.  Any such entry makes the archive
  unverifiable: the scanner refuses to interact with it, so the record source
  set cannot be established as safely and completely inspectable.
* Root-level stray *regular* files (hidden files, editor backups, stale temp
  files) are inert: the archive root is a layout location, not a record
  partition, so those files are reported as unexpected entries and never
  opened.  In contrast, every direct regular file in a record partition is a
  bounded record candidate, irrespective of its basename.  Its filename can
  affect placement only; it can never make its content uninspected.
* Every name in a complete initial partition snapshot receives exactly one
  source-manifest outcome.  A disappearance, classification failure, or
  transition to a nonregular type is an unread candidate, never an omission.
* No absolute path - lexical or resolved - ever appears in scan output.
  Entries are identified by safe relative paths; unsafe names appear only as
  a deterministic path digest plus a code-owned location token.

Structural failures (unreadable root, layout failure, missing/unsafe
partition, entry-count exhaustion) stop all record reads: the archive is
already unverifiable and its records must not be semantically interpreted as
a complete source set.  Entry-level anomalies (a symlink record, an oversize
record) still allow the remaining safe records to be read so the report stays
useful, while the assessment fails closed to unverifiable.

Initial layout/record reads and final record revalidation share the single
``max_total_read_bytes`` hard bound.  The individual and final observations are
bounded consistency checks, not an atomic filesystem snapshot.  Cooperative
coordination excludes only compliant repository writers using the same anchor;
arbitrary external or different-anchor mutation is not excluded during or after
the sequential scan.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import weakref
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence import archive_coordination as coord
from investment_orchestrator.offline.retirement_evidence import archive_record_contract as rc


SCANNER_VERSION = "retirement_archive_scanner_v1"

# --- code-owned v1 resource maxima -------------------------------------------
# An operator may lower these per scan; nothing may raise them.
LAYOUT_FILE_MAX_BYTES = 4 * 1024
RECORD_MAX_BYTES = 8 * 1024 * 1024
MAX_DIRECT_ENTRIES = 100_000
MAX_TOTAL_READ_BYTES = 512 * 1024 * 1024


# --- code-owned scan tokens ---------------------------------------------------
# Unverifiable-class: the complete record source set cannot be safely
# established or inspected.
TOKEN_ARCHIVE_ROOT_MISSING = "archive_root_missing"
TOKEN_ARCHIVE_ROOT_NOT_DIRECTORY = "archive_root_not_directory"
TOKEN_ARCHIVE_ROOT_UNREADABLE = "archive_root_unreadable"
TOKEN_LAYOUT_FILE_MISSING = "archive_layout_file_missing"
TOKEN_LAYOUT_FILE_UNSAFE = "archive_layout_file_unsafe"
TOKEN_LAYOUT_FILE_UNREADABLE = "archive_layout_file_unreadable"
TOKEN_LAYOUT_FILE_OVERSIZE = "archive_layout_file_oversize"
TOKEN_LAYOUT_FILE_NOT_UTF8 = "archive_layout_file_not_utf8"
TOKEN_LAYOUT_FILE_CHANGED = "archive_layout_file_changed_during_scan"
TOKEN_LAYOUT_VERSION_INCOMPATIBLE = "archive_layout_version_incompatible"
TOKEN_PARTITION_MISSING = "archive_partition_missing"
TOKEN_PARTITION_UNSAFE = "archive_partition_unsafe"
TOKEN_PARTITION_UNREADABLE = "archive_partition_unreadable"
TOKEN_UNSAFE_ARCHIVE_ENTRY = "unsafe_archive_entry"
TOKEN_RECORD_OVERSIZE = "record_oversize"
TOKEN_RECORD_UNREADABLE = "record_unreadable"
TOKEN_RECORD_CHANGED = "record_changed_during_scan"
TOKEN_RECORD_CLASSIFICATION_PERMISSION_DENIED = "record_classification_permission_denied"
TOKEN_RECORD_CLASSIFICATION_FAILED = "record_classification_failed"
TOKEN_ARCHIVE_CHANGED = "archive_changed_during_scan"
TOKEN_ENTRY_COUNT_LIMIT = "entry_count_limit_exceeded"
TOKEN_TOTAL_READ_LIMIT = "total_read_limit_exceeded"

# Warning-class: inspection completed, but the archive is not pristine.
TOKEN_LAYOUT_NONCANONICAL = "archive_layout_noncanonical_whitespace"
TOKEN_UNEXPECTED_ARCHIVE_ENTRY = "unexpected_archive_entry"

# Layout status values (report label domain).
LAYOUT_CANONICAL = "canonical"
LAYOUT_NONCANONICAL_WHITESPACE = "noncanonical_whitespace"
LAYOUT_INCOMPATIBLE = "incompatible"
LAYOUT_MISSING = "missing"
LAYOUT_UNSAFE = "unsafe"
LAYOUT_UNREADABLE = "unreadable"
LAYOUT_OVERSIZE = "oversize"
LAYOUT_NOT_UTF8 = "not_utf8"
LAYOUT_CHANGED_DURING_SCAN = "changed_during_scan"
LAYOUT_TOTAL_READ_LIMIT = "total_read_limit"
LAYOUT_NOT_SCANNED = "not_scanned"

# Entry kinds (classified via lstat; nothing is followed).
ENTRY_RECORD_CANDIDATE = "record_candidate"
ENTRY_UNEXPECTED_REGULAR_FILE = "unexpected_regular_file"
ENTRY_SYMLINK = "symlink"
ENTRY_DIRECTORY = "directory"
ENTRY_FIFO = "fifo"
ENTRY_SOCKET = "socket"
ENTRY_BLOCK_DEVICE = "block_device"
ENTRY_CHARACTER_DEVICE = "character_device"
ENTRY_UNKNOWN = "unknown"

# Stable-read states for record candidates.
READ_STABLE = "stable"
READ_RECORD_OVERSIZE = "record_oversize"
READ_RECORD_UNREADABLE = "record_unreadable"
READ_RECORD_CHANGED = "record_changed_during_scan"
READ_DISAPPEARED_BEFORE_CLASSIFICATION = "disappeared_before_classification"
READ_ENTRY_TYPE_CHANGED_BEFORE_CLASSIFICATION = "entry_type_changed_before_classification"
READ_CLASSIFICATION_PERMISSION_DENIED = "classification_permission_denied"
READ_CLASSIFICATION_FAILED = "classification_failed"
READ_DISAPPEARED_BEFORE_OPEN = "disappeared_before_open"
READ_DISAPPEARED_DURING_READ = "disappeared_during_read"
READ_SKIPPED_UNVERIFIABLE = "not_read_archive_unverifiable"
READ_SKIPPED_TOTAL_LIMIT = "not_read_total_read_limit_exceeded"

# Final-revalidation states.  Initial and final record reads share the single
# reported ``max_total_read_bytes`` budget.
REVALIDATION_STABLE = "revalidated_stable"
REVALIDATION_CHANGED = "changed_after_initial_read"
REVALIDATION_SKIPPED_TOTAL_LIMIT = "not_revalidated_total_read_limit_exceeded"

ARCHIVE_ROOT_LOCATION = "archive_root"

# Record filename conventions are placement-only.  They mirror the exact
# Phase 2A filename derivations in ``archive_record_contract`` and are useful
# as a drift guard, but never decide whether a direct regular partition file
# receives a bounded stable read and pure-verifier pass.
_OBSERVATION_RECORD_BASENAME_RE = re.compile(
    r"(?:[0-9]+|nogen)__(?:[0-9a-f]{12}|noid)__[0-9a-f]{64}\.json\Z"
)
_REJECTED_RECORD_BASENAME_RE = re.compile(
    r"rejected__[0-9a-f]{16}__[0-9a-f]{64}\.json\Z"
)


def is_record_convention_basename(value: Any) -> bool:
    """True iff one basename matches a Phase 2A record filename convention."""
    if not isinstance(value, str):
        return False
    return (
        _OBSERVATION_RECORD_BASENAME_RE.fullmatch(value) is not None
        or _REJECTED_RECORD_BASENAME_RE.fullmatch(value) is not None
    )


class ScanLimitError(Exception):
    """An operator-supplied limit is invalid or above a code-owned maximum.

    Carries a safe token only; never raw values or paths.
    """

    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(token)


@dataclass(frozen=True)
class ScanLimits:
    """Effective per-scan resource limits, bounded by the code-owned maxima."""

    layout_file_max_bytes: int = LAYOUT_FILE_MAX_BYTES
    record_max_bytes: int = RECORD_MAX_BYTES
    max_direct_entries: int = MAX_DIRECT_ENTRIES
    max_total_read_bytes: int = MAX_TOTAL_READ_BYTES

    def __post_init__(self) -> None:
        maxima = {
            "layout_file_max_bytes": LAYOUT_FILE_MAX_BYTES,
            "record_max_bytes": RECORD_MAX_BYTES,
            "max_direct_entries": MAX_DIRECT_ENTRIES,
            "max_total_read_bytes": MAX_TOTAL_READ_BYTES,
        }
        for name, maximum in maxima.items():
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ScanLimitError(f"scan_limit_invalid:{name}")
            if value > maximum:
                raise ScanLimitError(f"scan_limit_above_maximum:{name}")

    def as_report_mapping(self) -> dict[str, int]:
        return {
            "layout_file_max_bytes": self.layout_file_max_bytes,
            "record_max_bytes": self.record_max_bytes,
            "max_direct_entries": self.max_direct_entries,
            "max_total_read_bytes": self.max_total_read_bytes,
        }


@dataclass(frozen=True)
class ScannedEntry:
    """One direct archive entry, identified without any raw unsafe content."""

    location: str  # archive_root / accepted / quarantined / rejected
    safe_name: str | None
    safe_relative_path: str | None
    entry_path_sha256: str
    entry_kind: str
    stable_read_state: str | None
    file_sha256: str | None
    byte_length: int | None
    final_revalidation_state: str | None = None
    record_bytes: bytes | None = field(repr=False, default=None)
    initial_identity: _FileIdentity | None = field(repr=False, compare=False, default=None)
    source_name: str | None = field(repr=False, compare=False, default=None)
    source_candidate: bool = False

    @property
    def reference(self) -> str:
        """Deterministic raw-content-free identifier for report use."""
        if self.safe_relative_path is not None:
            return self.safe_relative_path
        return f"unsafe_name:{self.location}:{self.entry_path_sha256}"


@dataclass(frozen=True)
class ArchiveScan:
    """Deterministic result of scanning one archive root once."""

    scanner_version: str
    effective_limits: ScanLimits
    layout_status: str
    archive_layout_version: str | None
    entries: tuple[ScannedEntry, ...]
    unverifiable_tokens: tuple[str, ...]
    warning_tokens: tuple[str, ...]
    # Exact for a complete initial snapshot; capped at limit + 1 when the
    # bounded enumerator detects truncation.
    direct_entry_count: int
    total_bytes_read: int
    entry_inventory_truncated: bool
    _construction_token: object = field(repr=False, compare=False)
    _resolved_archive_root: Path | None = field(repr=False, compare=False, default=None)


@dataclass(frozen=True, slots=True)
class _CanonicalScanCompletion:
    """Code-owned phase facts retained outside caller-constructible scan data."""

    layout_validation_completed: bool
    required_partitions_inspected: bool
    initial_inventory_completed: bool
    classification_completed: bool
    initial_reads_completed: bool
    final_inventory_completed: bool
    final_record_validation_completed: bool
    required_identity_validation_completed: bool
    source_population_finalized: bool
    canonical_terminal_state: bool

    @property
    def all_required_phases_completed(self) -> bool:
        return all(
            (
                self.layout_validation_completed,
                self.required_partitions_inspected,
                self.initial_inventory_completed,
                self.classification_completed,
                self.initial_reads_completed,
                self.final_inventory_completed,
                self.final_record_validation_completed,
                self.required_identity_validation_completed,
                self.source_population_finalized,
                self.canonical_terminal_state,
            )
        )


# --- stable read ---------------------------------------------------------------
@dataclass(frozen=True)
class _StableReadResult:
    """One bounded read result, including bytes consumed on every outcome."""

    state: str
    data: bytes | None
    bytes_read: int
    identity: _FileIdentity | None = None


@dataclass(frozen=True)
class _FileIdentity:
    """Internal stable path identity; never serialized into a report."""

    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


def _file_identity(st: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=st.st_dev,
        inode=st.st_ino,
        mode=st.st_mode,
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        ctime_ns=st.st_ctime_ns,
    )


def _stat_signature(st: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _stable_read(path: Path, max_bytes: int, remaining_bytes: int) -> _StableReadResult:
    """Read one regular file under the Phase 2B-2 stable-read contract.

    ``lstat`` first (never following a symlink), open read-only with
    ``O_NOFOLLOW``, verify the opened file is the stat'd file, and admit only
    its opened-descriptor size when it fits both per-file and remaining global
    limits.  Read exactly that admitted size, then require
    device/inode/mode/size/mtime/ctime stability across open-time, post-read,
    and a final path ``lstat`` while the descriptor remains open.  Every path
    returns the actual number of bytes consumed, even when the result cannot
    be trusted.
    """
    try:
        st_initial = os.lstat(path)
    except FileNotFoundError:
        return _StableReadResult(READ_DISAPPEARED_BEFORE_OPEN, None, 0)
    except OSError:
        return _StableReadResult(READ_RECORD_UNREADABLE, None, 0)
    if not stat.S_ISREG(st_initial.st_mode):
        # Raced from regular to non-regular between classification and read.
        return _StableReadResult(READ_RECORD_CHANGED, None, 0)
    if st_initial.st_size > max_bytes:
        return _StableReadResult(READ_RECORD_OVERSIZE, None, 0)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return _StableReadResult(READ_DISAPPEARED_BEFORE_OPEN, None, 0)
    except OSError:
        return _StableReadResult(READ_RECORD_UNREADABLE, None, 0)
    bytes_read = 0
    try:
        st_open = os.fstat(fd)
        if _stat_signature(st_open) != _stat_signature(st_initial):
            return _StableReadResult(READ_RECORD_CHANGED, None, 0)
        if st_open.st_size > max_bytes:
            return _StableReadResult(READ_RECORD_OVERSIZE, None, 0)
        if st_open.st_size > remaining_bytes:
            return _StableReadResult(READ_SKIPPED_TOTAL_LIMIT, None, 0)
        chunks: list[bytes] = []
        remaining = st_open.st_size
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
            bytes_read += len(chunk)
        st_post_read = os.fstat(fd)
        try:
            st_final = os.lstat(path)
        except FileNotFoundError:
            return _StableReadResult(READ_DISAPPEARED_DURING_READ, None, bytes_read)
        except OSError:
            return _StableReadResult(READ_RECORD_CHANGED, None, bytes_read)
        if _stat_signature(st_post_read) != _stat_signature(st_open):
            return _StableReadResult(READ_RECORD_CHANGED, None, bytes_read)
        if _stat_signature(st_final) != _stat_signature(st_open):
            return _StableReadResult(READ_RECORD_CHANGED, None, bytes_read)
        data = b"".join(chunks)
        if len(data) != st_open.st_size:
            return _StableReadResult(READ_RECORD_CHANGED, None, bytes_read)
        return _StableReadResult(READ_STABLE, data, bytes_read, _file_identity(st_open))
    except OSError:
        return _StableReadResult(READ_RECORD_UNREADABLE, None, bytes_read)
    finally:
        try:
            os.close(fd)
        except OSError:
            # The content decision is already complete and fail-closed on all
            # read/stat errors; do not let a cleanup-only close error leak an
            # archive-content exception to the caller.
            pass


# --- entry classification --------------------------------------------------------
def _entry_kind_for_mode(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return ENTRY_SYMLINK
    if stat.S_ISDIR(mode):
        return ENTRY_DIRECTORY
    if stat.S_ISFIFO(mode):
        return ENTRY_FIFO
    if stat.S_ISSOCK(mode):
        return ENTRY_SOCKET
    if stat.S_ISBLK(mode):
        return ENTRY_BLOCK_DEVICE
    if stat.S_ISCHR(mode):
        return ENTRY_CHARACTER_DEVICE
    return ENTRY_UNKNOWN


def _entry_path_sha256(location: str, name: str) -> str:
    material = f"{location}/{name}".encode("utf-8", "surrogateescape")
    return hashlib.sha256(material).hexdigest()


def _named_entry(
    location: str,
    name: str,
    entry_kind: str,
    *,
    stable_read_state: str | None = None,
    file_sha256: str | None = None,
    byte_length: int | None = None,
    final_revalidation_state: str | None = None,
    record_bytes: bytes | None = None,
    initial_identity: _FileIdentity | None = None,
) -> ScannedEntry:
    safe = rc.is_safe_entry_filename(name)
    return ScannedEntry(
        location=location,
        safe_name=name if safe else None,
        safe_relative_path=f"{location}/{name}" if safe else None,
        entry_path_sha256=_entry_path_sha256(location, name),
        entry_kind=entry_kind,
        stable_read_state=stable_read_state,
        file_sha256=file_sha256,
        byte_length=byte_length,
        final_revalidation_state=final_revalidation_state,
        record_bytes=record_bytes,
        initial_identity=initial_identity,
        source_name=name,
        source_candidate=location in c.PARTITIONS and stable_read_state is not None,
    )


# --- scan ---------------------------------------------------------------------
def _run_canonical_scan(
    archive_root: Path | str,
    effective: ScanLimits,
    lease: coord.VerifiedCoordinationLease,
    construction_token: object,
) -> tuple[ArchiveScan, _CanonicalScanCompletion]:
    """Execute the one canonical scanner phase sequence.

    This function only accumulates data.  Trusted provenance is registered by
    the private public-operation closure after this function returns; neither
    this function nor ``_ScanState`` possesses registration authority.
    """
    state = _ScanState(effective, lease, Path(archive_root))

    resolved_root = _resolve_root(archive_root, state)
    if resolved_root is None:
        return _untrusted_scan_result(state, construction_token)

    root_snapshot = _bounded_directory_snapshot(resolved_root, effective.max_direct_entries)
    if root_snapshot is None:
        state.unverifiable.add(TOKEN_ARCHIVE_ROOT_UNREADABLE)
        return _untrusted_scan_result(state, construction_token)
    root_names = root_snapshot.names
    state.direct_entry_count = len(root_names)
    if root_snapshot.truncated:
        state.unverifiable.add(TOKEN_ENTRY_COUNT_LIMIT)
        state.entry_inventory_truncated = True
        return _untrusted_scan_result(state, construction_token)

    _verify_layout(resolved_root, state)
    state.layout_validation_completed = True

    partition_snapshots = _snapshot_partitions(resolved_root, state)
    if state.entry_inventory_truncated:
        # The bounded enumerator observed only the code-owned detection window;
        # no source-set inventory can be complete in this state.
        state.entries = []
        return _untrusted_scan_result(state, construction_token)
    state.initial_inventory_completed = (
        state.required_partitions_inspected
        and set(partition_snapshots) == set(c.PARTITIONS)
    )

    # Structural failure (root/layout/partition/count) stops all record reads:
    # the archive is already unverifiable and its records must never be
    # semantically read as a complete source set.  Entry-level anomalies found
    # during classification below do NOT stop the remaining safe reads.
    structural_failure = bool(state.unverifiable)

    _classify_root_entries(resolved_root, root_names, state)
    candidates = _classify_partition_entries(resolved_root, partition_snapshots, state)
    state.classification_completed = True

    if structural_failure:
        for location, name in candidates:
            state.entries.append(
                _named_entry(location, name, ENTRY_RECORD_CANDIDATE,
                             stable_read_state=READ_SKIPPED_UNVERIFIABLE)
            )
        return _untrusted_scan_result(state, construction_token)

    _read_candidates(resolved_root, candidates, state)
    state.initial_reads_completed = True

    # Final source-set revalidation is deliberately bounded.  First establish
    # that required paths and direct-entry sets still match the initial scan,
    # then re-read every initially stable record under the same total-byte
    # budget, and finally recheck required path identities once more.
    state.final_inventory_completed = _check_post_scan_snapshots(
        resolved_root, root_names, partition_snapshots, state
    )
    _revalidate_stable_candidates(resolved_root, state)
    state.final_record_validation_completed = True
    state.required_identity_validation_completed = _revalidate_required_identities(
        resolved_root, state
    )
    state.source_population_finalized = True
    state.canonical_terminal_state = True

    return _untrusted_scan_result(state, construction_token)


class _ScanState:
    def __init__(
        self,
        effective: ScanLimits,
        lease: coord.VerifiedCoordinationLease,
        archive_root: Path,
    ) -> None:
        self.limits = effective
        self.lease = lease
        self.archive_root = archive_root
        self.unverifiable: set[str] = set()
        self.warnings: set[str] = set()
        self.entries: list[ScannedEntry] = []
        self.layout_status: str = LAYOUT_NOT_SCANNED
        self.layout_version: str | None = None
        self.total_bytes_read = 0
        self.direct_entry_count = 0
        self.entry_inventory_truncated = False
        self.root_identity: tuple[int, int, int, int, int] | None = None
        self.layout_identity: _FileIdentity | None = None
        self.partition_identities: dict[str, tuple[int, int, int, int, int]] = {}
        self.resolved_archive_root: Path | None = None
        self.layout_validation_completed = False
        self.required_partitions_inspected = False
        self.initial_inventory_completed = False
        self.classification_completed = False
        self.initial_reads_completed = False
        self.final_inventory_completed = False
        self.final_record_validation_completed = False
        self.required_identity_validation_completed = False
        self.source_population_finalized = False
        self.canonical_terminal_state = False


def _untrusted_scan_result(
    state: _ScanState,
    construction_token: object,
) -> tuple[ArchiveScan, _CanonicalScanCompletion]:
    """Materialize scan data without granting it trusted provenance."""
    repaired_entries: list[ScannedEntry] = []
    for entry in state.entries:
        if (
            entry.source_candidate
            and entry.stable_read_state == READ_STABLE
            and entry.final_revalidation_state is None
        ):
            state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
            repaired_entries.append(
                replace(entry, final_revalidation_state=REVALIDATION_CHANGED)
            )
        else:
            repaired_entries.append(entry)
    completion = _CanonicalScanCompletion(
        layout_validation_completed=state.layout_validation_completed,
        required_partitions_inspected=state.required_partitions_inspected,
        initial_inventory_completed=state.initial_inventory_completed,
        classification_completed=state.classification_completed,
        initial_reads_completed=state.initial_reads_completed,
        final_inventory_completed=state.final_inventory_completed,
        final_record_validation_completed=state.final_record_validation_completed,
        required_identity_validation_completed=(
            state.required_identity_validation_completed
        ),
        source_population_finalized=state.source_population_finalized,
        canonical_terminal_state=state.canonical_terminal_state,
    )
    scanned = ArchiveScan(
        scanner_version=SCANNER_VERSION,
        effective_limits=state.limits,
        layout_status=state.layout_status,
        archive_layout_version=state.layout_version,
        entries=tuple(sorted(repaired_entries, key=lambda entry: entry.reference)),
        unverifiable_tokens=tuple(sorted(state.unverifiable)),
        warning_tokens=tuple(sorted(state.warnings)),
        direct_entry_count=state.direct_entry_count,
        total_bytes_read=state.total_bytes_read,
        entry_inventory_truncated=state.entry_inventory_truncated,
        _construction_token=construction_token,
        _resolved_archive_root=state.resolved_archive_root,
    )
    return scanned, completion


def _make_canonical_scan_api(run_operation: Any) -> tuple[Any, Any, Any]:
    """Keep provenance authority in a closure unavailable to scan accumulators."""
    construction_token = object()
    provenance: dict[
        int,
        tuple[
            weakref.ReferenceType[ArchiveScan],
            weakref.ReferenceType[coord.VerifiedCoordinationLease],
            object,
            Path,
            _CanonicalScanCompletion,
        ],
    ] = {}

    def validate_binding(
        scanned: ArchiveScan,
        lease: coord.VerifiedCoordinationLease,
    ) -> _CanonicalScanCompletion:
        if type(scanned) is not ArchiveScan:
            raise coord.CoordinationError(coord.TOKEN_LEASE_INVALID)
        binding = provenance.get(id(scanned))
        if (
            binding is None
            or binding[0]() is not scanned
            or binding[1]() is not lease
            or scanned._construction_token is not construction_token
        ):
            raise coord.CoordinationError(coord.TOKEN_LEASE_INVALID)
        nonce, root, completion = binding[2], binding[3], binding[4]
        actual_nonce = coord._coordination_operation_identity(
            lease,
            archive_root=root,
            expected_mode=coord.LOCK_MODE_SHARED,
        )
        if nonce is not actual_nonce:
            raise coord.CoordinationError(coord.TOKEN_LEASE_INVALID)
        return completion

    def canonical_scan_archive(
        archive_root: Path | str,
        limits: ScanLimits | None = None,
        *,
        lease: coord.VerifiedCoordinationLease | None = None,
    ) -> ArchiveScan:
        """Run and provenance-bind the one canonical read-only scan operation."""
        coord.begin_coordination_operation(
            lease,
            archive_root=archive_root,
            expected_mode=coord.LOCK_MODE_SHARED,
        )
        effective = limits if limits is not None else ScanLimits()
        scanned, completion = run_operation(
            archive_root,
            effective,
            lease,
            construction_token,
        )
        coord.validate_coordination_operation(
            lease,
            archive_root=archive_root,
            expected_mode=coord.LOCK_MODE_SHARED,
        )
        nonce = coord._coordination_operation_identity(
            lease,
            archive_root=archive_root,
            expected_mode=coord.LOCK_MODE_SHARED,
        )
        key = id(scanned)

        def discard(_reference: object) -> None:
            provenance.pop(key, None)

        provenance[key] = (
            weakref.ref(scanned, discard),
            weakref.ref(lease),
            nonce,
            Path(archive_root),
            completion,
        )
        return scanned

    def validate_scan_lease_binding(
        scanned: ArchiveScan,
        lease: coord.VerifiedCoordinationLease,
    ) -> None:
        validate_binding(scanned, lease)

    def validated_scan_completion(
        scanned: ArchiveScan,
        lease: coord.VerifiedCoordinationLease,
    ) -> _CanonicalScanCompletion:
        return validate_binding(scanned, lease)

    return (
        canonical_scan_archive,
        validate_scan_lease_binding,
        validated_scan_completion,
    )


(
    scan_archive,
    validate_scan_lease_binding,
    validated_scan_completion,
) = _make_canonical_scan_api(_run_canonical_scan)


def _resolve_root(archive_root: Path | str, state: _ScanState) -> Path | None:
    # The explicit operator-supplied root is the ONLY path ever resolved; it
    # is resolved once, up front.  Nothing below it is ever followed.
    try:
        resolved = Path(archive_root).resolve(strict=True)
    except FileNotFoundError:
        state.unverifiable.add(TOKEN_ARCHIVE_ROOT_MISSING)
        return None
    except (OSError, RuntimeError):
        state.unverifiable.add(TOKEN_ARCHIVE_ROOT_UNREADABLE)
        return None
    try:
        st = os.lstat(resolved)
    except OSError:
        state.unverifiable.add(TOKEN_ARCHIVE_ROOT_UNREADABLE)
        return None
    if not stat.S_ISDIR(st.st_mode):
        state.unverifiable.add(TOKEN_ARCHIVE_ROOT_NOT_DIRECTORY)
        return None
    state.root_identity = _directory_signature(st)
    state.resolved_archive_root = resolved
    return resolved


@dataclass(frozen=True)
class _DirectorySnapshot:
    """Bounded direct-entry names for one directory, never recursive."""

    names: list[str]
    truncated: bool


def _directory_signature(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_mode, st.st_mtime_ns, st.st_ctime_ns)


def _bounded_directory_snapshot(path: Path, limit: int) -> _DirectorySnapshot | None:
    """Enumerate at most ``limit + 1`` direct names with deterministic order."""
    names: list[str] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) > limit:
                    return _DirectorySnapshot(sorted(names), truncated=True)
    except OSError:
        return None
    return _DirectorySnapshot(sorted(names), truncated=False)


def _classify_root_entries(root: Path, root_names: list[str], state: _ScanState) -> None:
    reserved = {c.ARCHIVE_LAYOUT_VERSION_FILENAME, *c.PARTITIONS}
    for name in sorted(root_names):
        if name in reserved:
            continue
        try:
            st = os.lstat(root / name)
        except OSError:
            state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
            continue
        if stat.S_ISREG(st.st_mode):
            # Inert stray regular file: reported, never opened, blocks clean.
            state.warnings.add(TOKEN_UNEXPECTED_ARCHIVE_ENTRY)
            state.entries.append(
                _named_entry(ARCHIVE_ROOT_LOCATION, name, ENTRY_UNEXPECTED_REGULAR_FILE)
            )
        else:
            state.unverifiable.add(TOKEN_UNSAFE_ARCHIVE_ENTRY)
            state.entries.append(
                _named_entry(ARCHIVE_ROOT_LOCATION, name, _entry_kind_for_mode(st.st_mode))
            )


def _verify_layout(root: Path, state: _ScanState) -> None:
    layout_path = root / c.ARCHIVE_LAYOUT_VERSION_FILENAME
    try:
        st = os.lstat(layout_path)
    except FileNotFoundError:
        state.layout_status = LAYOUT_MISSING
        state.unverifiable.add(TOKEN_LAYOUT_FILE_MISSING)
        return
    except OSError:
        state.layout_status = LAYOUT_UNREADABLE
        state.unverifiable.add(TOKEN_LAYOUT_FILE_UNREADABLE)
        return
    if not stat.S_ISREG(st.st_mode):
        state.layout_status = LAYOUT_UNSAFE
        state.unverifiable.add(TOKEN_LAYOUT_FILE_UNSAFE)
        return

    remaining_bytes = state.limits.max_total_read_bytes - state.total_bytes_read
    result = _stable_read(
        layout_path,
        state.limits.layout_file_max_bytes,
        remaining_bytes,
    )
    state.total_bytes_read += result.bytes_read
    if result.state == READ_RECORD_OVERSIZE:
        state.layout_status = LAYOUT_OVERSIZE
        state.unverifiable.add(TOKEN_LAYOUT_FILE_OVERSIZE)
        return
    if result.state == READ_SKIPPED_TOTAL_LIMIT:
        state.layout_status = LAYOUT_TOTAL_READ_LIMIT
        state.unverifiable.add(TOKEN_TOTAL_READ_LIMIT)
        return
    if result.state in (
        READ_RECORD_CHANGED,
        READ_DISAPPEARED_BEFORE_OPEN,
        READ_DISAPPEARED_DURING_READ,
    ):
        state.layout_status = LAYOUT_CHANGED_DURING_SCAN
        state.unverifiable.add(TOKEN_LAYOUT_FILE_CHANGED)
        return
    if result.state != READ_STABLE or result.data is None:
        state.layout_status = LAYOUT_UNREADABLE
        state.unverifiable.add(TOKEN_LAYOUT_FILE_UNREADABLE)
        return
    state.layout_identity = result.identity

    try:
        text = result.data.decode("utf-8")
    except UnicodeDecodeError:
        state.layout_status = LAYOUT_NOT_UTF8
        state.unverifiable.add(TOKEN_LAYOUT_FILE_NOT_UTF8)
        return
    if text == c.ARCHIVE_LAYOUT_VERSION + "\n":
        state.layout_status = LAYOUT_CANONICAL
        state.layout_version = c.ARCHIVE_LAYOUT_VERSION
    elif text.strip() == c.ARCHIVE_LAYOUT_VERSION:
        # Compatible but not the exact canonical bytes Phase 2A writes.
        state.layout_status = LAYOUT_NONCANONICAL_WHITESPACE
        state.layout_version = c.ARCHIVE_LAYOUT_VERSION
        state.warnings.add(TOKEN_LAYOUT_NONCANONICAL)
    else:
        state.layout_status = LAYOUT_INCOMPATIBLE
        state.unverifiable.add(TOKEN_LAYOUT_VERSION_INCOMPATIBLE)


def _snapshot_partitions(root: Path, state: _ScanState) -> dict[str, list[str]]:
    snapshots: dict[str, list[str]] = {}
    for partition in c.PARTITIONS:
        path = root / partition
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            state.unverifiable.add(TOKEN_PARTITION_MISSING)
            continue
        except OSError:
            state.unverifiable.add(TOKEN_PARTITION_UNREADABLE)
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            state.unverifiable.add(TOKEN_PARTITION_UNSAFE)
            continue
        state.partition_identities[partition] = _directory_signature(st)
        remaining_entries = state.limits.max_direct_entries - state.direct_entry_count
        snapshot = _bounded_directory_snapshot(path, remaining_entries)
        if snapshot is None:
            state.unverifiable.add(TOKEN_PARTITION_UNREADABLE)
            continue
        state.direct_entry_count += len(snapshot.names)
        if snapshot.truncated:
            state.unverifiable.add(TOKEN_ENTRY_COUNT_LIMIT)
            state.entry_inventory_truncated = True
            return snapshots
        snapshots[partition] = snapshot.names
    state.required_partitions_inspected = True
    return snapshots


def _check_post_scan_snapshots(
    root: Path,
    root_names: list[str],
    partition_snapshots: dict[str, list[str]],
    state: _ScanState,
) -> bool:
    """Fail closed when required identities or bounded entry sets differ."""
    complete = _revalidate_required_identities(root, state)
    root_snapshot = _bounded_directory_snapshot(root, state.limits.max_direct_entries)
    if root_snapshot is None:
        state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
        return False
    if root_snapshot.truncated:
        state.unverifiable.add(TOKEN_ENTRY_COUNT_LIMIT)
        state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
        state.entry_inventory_truncated = True
        return False
    if root_snapshot.names != root_names:
        state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
        complete = False

    observed_count = len(root_snapshot.names)
    for partition in c.PARTITIONS:
        path = root / partition
        remaining_entries = state.limits.max_direct_entries - observed_count
        snapshot = _bounded_directory_snapshot(path, remaining_entries)
        if snapshot is None:
            state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
            complete = False
            continue
        observed_count += len(snapshot.names)
        if snapshot.truncated:
            state.unverifiable.add(TOKEN_ENTRY_COUNT_LIMIT)
            state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
            state.entry_inventory_truncated = True
            return False
        if snapshot.names != partition_snapshots.get(partition):
            state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
            complete = False
    return complete


def _revalidate_required_identities(root: Path, state: _ScanState) -> bool:
    """Compare root, layout, and partition identities without following links."""
    valid = True
    try:
        root_st = os.lstat(root)
    except OSError:
        state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
        valid = False
    else:
        if (
            not stat.S_ISDIR(root_st.st_mode)
            or state.root_identity is None
            or _directory_signature(root_st) != state.root_identity
        ):
            state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
            valid = False

    layout_path = root / c.ARCHIVE_LAYOUT_VERSION_FILENAME
    try:
        layout_st = os.lstat(layout_path)
    except OSError:
        state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
        valid = False
    else:
        if (
            not stat.S_ISREG(layout_st.st_mode)
            or state.layout_identity is None
            or _file_identity(layout_st) != state.layout_identity
        ):
            state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
            valid = False

    for partition in c.PARTITIONS:
        try:
            partition_st = os.lstat(root / partition)
        except OSError:
            state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
            valid = False
            continue
        expected_identity = state.partition_identities.get(partition)
        if (
            not stat.S_ISDIR(partition_st.st_mode)
            or expected_identity is None
            or _directory_signature(partition_st) != expected_identity
        ):
            state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
            valid = False
    return valid


def _classify_partition_entries(
    root: Path,
    partition_snapshots: dict[str, list[str]],
    state: _ScanState,
) -> list[tuple[str, str]]:
    """Classify direct partition entries; return every regular file to read.

    A record partition is a semantic record location.  Therefore a direct
    regular file is a record candidate even when its basename is foreign,
    hidden, or otherwise nonconforming.  The pure verifier decides content
    and placement separately after the bounded stable read.  Only non-regular
    entries remain unopened unsafe archive entries.
    """
    candidates: list[tuple[str, str]] = []
    for partition in c.PARTITIONS:
        names = partition_snapshots.get(partition)
        if names is None:
            continue
        for name in names:
            try:
                st = os.lstat(root / partition / name)
            except FileNotFoundError:
                state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
                # A name in the initial partition snapshot is a source-set
                # candidate even when it vanishes before type classification.
                # Preserve it in the manifest rather than relegating it to a
                # generic unexpected-entry bucket.
                state.entries.append(
                    _named_entry(
                        partition,
                        name,
                        ENTRY_RECORD_CANDIDATE,
                        stable_read_state=READ_DISAPPEARED_BEFORE_CLASSIFICATION,
                    )
                )
                continue
            except PermissionError:
                state.unverifiable.add(TOKEN_RECORD_CLASSIFICATION_PERMISSION_DENIED)
                state.entries.append(
                    _named_entry(
                        partition,
                        name,
                        ENTRY_RECORD_CANDIDATE,
                        stable_read_state=READ_CLASSIFICATION_PERMISSION_DENIED,
                    )
                )
                continue
            except OSError:
                state.unverifiable.add(TOKEN_RECORD_CLASSIFICATION_FAILED)
                state.entries.append(
                    _named_entry(
                        partition,
                        name,
                        ENTRY_RECORD_CANDIDATE,
                        stable_read_state=READ_CLASSIFICATION_FAILED,
                    )
                )
                continue
            if stat.S_ISREG(st.st_mode):
                # Admission uses the still-open descriptor size in
                # ``_stable_read``; this classification never supplies a
                # byte-budget estimate.
                candidates.append((partition, name))
            else:
                state.unverifiable.add(TOKEN_UNSAFE_ARCHIVE_ENTRY)
                state.entries.append(
                    _named_entry(
                        partition,
                        name,
                        _entry_kind_for_mode(st.st_mode),
                        stable_read_state=READ_ENTRY_TYPE_CHANGED_BEFORE_CLASSIFICATION,
                    )
                )
    return candidates


def _read_candidates(
    root: Path,
    candidates: list[tuple[str, str]],
    state: _ScanState,
) -> None:
    limits = state.limits
    exhausted = False
    for location, name in candidates:
        remaining_bytes = limits.max_total_read_bytes - state.total_bytes_read
        if exhausted or remaining_bytes <= 0:
            state.unverifiable.add(TOKEN_TOTAL_READ_LIMIT)
            exhausted = True
            state.entries.append(
                _named_entry(location, name, ENTRY_RECORD_CANDIDATE,
                             stable_read_state=READ_SKIPPED_TOTAL_LIMIT)
            )
            continue
        result = _stable_read(root / location / name, limits.record_max_bytes, remaining_bytes)
        state.total_bytes_read += result.bytes_read
        if result.state == READ_SKIPPED_TOTAL_LIMIT:
            state.unverifiable.add(TOKEN_TOTAL_READ_LIMIT)
            exhausted = True
            state.entries.append(
                _named_entry(
                    location,
                    name,
                    ENTRY_RECORD_CANDIDATE,
                    stable_read_state=READ_SKIPPED_TOTAL_LIMIT,
                )
            )
            continue
        if result.state == READ_STABLE and result.data is not None:
            state.entries.append(
                _named_entry(
                    location,
                    name,
                    ENTRY_RECORD_CANDIDATE,
                    stable_read_state=READ_STABLE,
                    file_sha256=hashlib.sha256(result.data).hexdigest(),
                    byte_length=len(result.data),
                    record_bytes=result.data,
                    initial_identity=result.identity,
                )
            )
            continue
        token = {
            READ_RECORD_OVERSIZE: TOKEN_RECORD_OVERSIZE,
            READ_RECORD_UNREADABLE: TOKEN_RECORD_UNREADABLE,
            READ_RECORD_CHANGED: TOKEN_RECORD_CHANGED,
            READ_DISAPPEARED_BEFORE_OPEN: TOKEN_ARCHIVE_CHANGED,
            READ_DISAPPEARED_DURING_READ: TOKEN_ARCHIVE_CHANGED,
        }.get(result.state, TOKEN_RECORD_UNREADABLE)
        state.unverifiable.add(token)
        state.entries.append(
            _named_entry(
                location,
                name,
                ENTRY_RECORD_CANDIDATE,
                stable_read_state=result.state,
            )
        )


def _revalidate_stable_candidates(root: Path, state: _ScanState) -> None:
    """Boundedly re-read each initially stable record before final assessment."""
    exhausted = False
    updated: list[ScannedEntry] = []
    for entry in state.entries:
        if not entry.source_candidate or entry.stable_read_state != READ_STABLE:
            updated.append(entry)
            continue

        remaining_bytes = state.limits.max_total_read_bytes - state.total_bytes_read
        if exhausted or remaining_bytes <= 0:
            exhausted = True
            state.unverifiable.add(TOKEN_TOTAL_READ_LIMIT)
            updated.append(
                replace(entry, final_revalidation_state=REVALIDATION_SKIPPED_TOTAL_LIMIT)
            )
            continue

        result = _stable_read(
            root / entry.location / (entry.source_name or ""),
            state.limits.record_max_bytes,
            remaining_bytes,
        )
        state.total_bytes_read += result.bytes_read
        if result.state == READ_SKIPPED_TOTAL_LIMIT:
            exhausted = True
            state.unverifiable.add(TOKEN_TOTAL_READ_LIMIT)
            updated.append(
                replace(entry, final_revalidation_state=REVALIDATION_SKIPPED_TOTAL_LIMIT)
            )
            continue

        second_hash = (
            hashlib.sha256(result.data).hexdigest()
            if result.state == READ_STABLE and result.data is not None
            else None
        )
        if (
            result.state == READ_STABLE
            and result.data is not None
            and result.identity == entry.initial_identity
            and second_hash == entry.file_sha256
        ):
            updated.append(replace(entry, final_revalidation_state=REVALIDATION_STABLE))
            continue

        state.unverifiable.add(TOKEN_ARCHIVE_CHANGED)
        updated.append(replace(entry, final_revalidation_state=REVALIDATION_CHANGED))
    state.entries = updated
