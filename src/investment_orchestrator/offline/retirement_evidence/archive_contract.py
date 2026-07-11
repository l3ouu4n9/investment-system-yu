"""Versioned constants for the Phase 2A retirement-observation archive.

Pure constants and small pure helpers only: no file, environment, or network
access at import time (the tool-identity resolver runs git only when explicitly
called, and never raises).  These names define the archive's file-layout,
record-envelope, provenance, and reason-token contracts in one place so the
ingestion library, CLI, and tests share a single source of truth.
"""

from __future__ import annotations

import subprocess
from typing import Any


# --- versions ----------------------------------------------------------------
# Directory-layout contract for an archive root.  The verifier/indexer (future
# Phase 2B) keys off this; a differing/malformed value fails ingestion closed.
ARCHIVE_LAYOUT_VERSION = "retirement_archive_layout_v1"
ARCHIVE_LAYOUT_VERSION_FILENAME = "retirement_archive_layout_version"

# Single-file envelope contract wrapping each accepted/quarantined observation.
ARCHIVE_RECORD_SCHEMA_VERSION = "retirement_archive_record_v1"
# Minimal reason-only record contract for rejected inputs (never holds payload).
ARCHIVE_REJECTED_RECORD_SCHEMA_VERSION = "retirement_archive_rejected_record_v1"

# Code-owned tool identity.  NOT operator-settable via the CLI (item 7); tests
# inject an identity through an internal function parameter instead.
ARCHIVE_TOOL_VERSION = "retirement_archive_tool_v1"

# The exact schema version of the only source artifact this tool ingests.
SOURCE_SCHEMA_VERSION = "step1a_retirement_observation_v1"


# --- partitions --------------------------------------------------------------
PARTITION_ACCEPTED = "accepted"
PARTITION_QUARANTINED = "quarantined"
PARTITION_REJECTED = "rejected"
PARTITIONS = (PARTITION_ACCEPTED, PARTITION_QUARANTINED, PARTITION_REJECTED)

# Decisions (equal to the partition names, but named separately for clarity).
DECISION_ACCEPTED = PARTITION_ACCEPTED
DECISION_QUARANTINED = PARTITION_QUARANTINED
DECISION_REJECTED = PARTITION_REJECTED


# --- provenance --------------------------------------------------------------
# An *unverified* operator/CI claim only.  Phase 2A never verifies it, never
# infers it from path/filename/content, and never evaluates whether it satisfies
# any future coverage class.
PROVENANCE_REAL_CURRENT = "real_current"
PROVENANCE_ISOLATED_PRODUCTION_PATH = "isolated_production_path"
PROVENANCE_INTEGRATION_TEST = "integration_test"
PROVENANCE_UNIT_TEST = "unit_test"
PROVENANCE_FAULT_INJECTION = "fault_injection"
PROVENANCE_UNSPECIFIED = "unspecified"
PROVENANCE_VALUES = frozenset(
    {
        PROVENANCE_REAL_CURRENT,
        PROVENANCE_ISOLATED_PRODUCTION_PATH,
        PROVENANCE_INTEGRATION_TEST,
        PROVENANCE_UNIT_TEST,
        PROVENANCE_FAULT_INJECTION,
        PROVENANCE_UNSPECIFIED,
    }
)
DEFAULT_PROVENANCE = PROVENANCE_UNSPECIFIED

PROVENANCE_CLAIM_SOURCE_DEFAULT = "default"
PROVENANCE_CLAIM_SOURCE_OPERATOR = "operator_argument"


# --- reason-token vocabulary -------------------------------------------------
# Canonical, non-raw reason tokens.  Structured tokens only ever embed field /
# section names drawn from this tool's OWN allowlists (never attacker-controlled
# payload keys or values), so no raw content can leak through a reason token.

# Reject-class (input is not safe to preserve as an observation payload):
REASON_SOURCE_NOT_VALID_JSON = "source_not_valid_json"
REASON_SOURCE_NOT_JSON_OBJECT = "source_not_json_object"
REASON_SCHEMA_VERSION_UNRECOGNIZED = "schema_version_unrecognized"
REASON_TOP_LEVEL_KEYS_INVALID = "top_level_keys_invalid"
REASON_AUTHORITY_ENVELOPE_VIOLATION = "authority_envelope_violation"
REASON_READINESS_CLAIM_PRESENT = "readiness_claim_present"
REASON_RAW_CONTENT_UNSAFE = "raw_content_unsafe"
REASON_COMPOSITE_FINGERPRINT_MISMATCH = "composite_fingerprint_mismatch"
REASON_COVERAGE_KEY_MISMATCH = "coverage_key_mismatch"
REASON_OBSERVATION_ID_MISMATCH = "observation_id_mismatch"
REASON_OBSERVATION_ID_CONTENT_CONFLICT = "observation_id_content_conflict"
REASON_ARCHIVE_FILENAME_COLLISION = "archive_filename_collision"
# The committed Phase 1A writer derives completeness from the four diagnostic
# collections; a payload whose completeness field contradicts them is fabricated.
REASON_OBSERVATION_COMPLETENESS_INCONSISTENT = "observation_completeness_inconsistent"
# Parameterized reject categories (companion tokens carry a KNOWN field/section):
REASON_PREFIX_FIELD_DOMAIN_INVALID = "field_domain_invalid"
REASON_PREFIX_NESTED_STRUCTURE_INVALID = "nested_structure_invalid"

# Quarantine-class (structurally valid + raw-safe, but non-countable):
REASON_OBSERVATION_INCOMPLETE = "observation_incomplete"
REASON_CODE_IDENTITY_DIRTY = "code_identity_dirty"
REASON_CODE_IDENTITY_UNAVAILABLE = "code_identity_unavailable"
REASON_CODE_VERSION_NOT_USABLE = "code_version_not_usable_for_evidence"
REASON_BUILDER_INTERNAL_ERROR_OBSERVATION = "builder_internal_error_observation"

# Existing-archive-record integrity failure (fail-closed ingestion error, not an
# ingestion decision): an already-archived candidate record consulted for a
# duplicate/conflict decision failed independent re-verification.
EXISTING_RECORD_INTEGRITY_FAILED = "existing_archive_record_integrity_failed"


def field_domain_invalid(field: str) -> str:
    """Reason token for a known field whose value is outside its domain."""
    return f"{REASON_PREFIX_FIELD_DOMAIN_INVALID}:{field}"


def nested_structure_invalid(section: str) -> str:
    """Reason token for a known section whose shape/keys are invalid."""
    return f"{REASON_PREFIX_NESTED_STRUCTURE_INVALID}:{section}"


# Exact readiness/authorization tokens that must never appear anywhere in a
# report-only observation.  Defense-in-depth on top of the exact-key allowlist.
READINESS_DENYLIST = (
    "retirement_ready",
    "retirement_recommended",
    "ready_pending_operator",
    "ready_pending",
    "safe_to_retire",
    "coverage_satisfied",
    "disable_fallback",
    "delete_legacy",
)


def resolve_tool_identity(repo_root: Any = None) -> dict[str, str]:
    """Return the code-owned tool identity with a best-effort clean commit.

    Never raises.  ``tool_version`` is always the code-owned constant.
    ``tool_commit`` is the current commit hash iff git resolves and the tree is
    clean, otherwise ``"unavailable"`` - reported truthfully, never inferred.
    """
    return {
        "tool_version": ARCHIVE_TOOL_VERSION,
        "tool_commit": _best_effort_clean_commit(repo_root),
    }


def _best_effort_clean_commit(repo_root: Any = None) -> str:
    try:
        cwd = str(repo_root) if repo_root is not None else None
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
    except Exception:  # noqa: BLE001 - offline best-effort; failure -> unavailable
        return "unavailable"
    commit = head.stdout.strip()
    if head.returncode != 0 or status.returncode != 0:
        return "unavailable"
    if status.stdout.strip():
        return "unavailable"
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        return "unavailable"
    return commit
