"""Step 1C REAL active-pointer writer (R2E.5b-5a — pending-gates artifacts).

Writes the real promotion-pointer artifacts designed in §25.3 / previewed in
R2E.5b-4 — `active_research_handoff_source.json`, a byte-identical
`research_handoff_candidate_effective.json`, and its strict re-validation —
**only** when the R2E.5b-4 pointer preview reports ``would_promote: true`` and
every fail-closed creation rule passes.

**This writer creates pending-gates artifacts only. It does not make them
trading authorization.** In R2E.5b-5a, nothing read the pointer or the effective
handoff; R2E.5b-5b may recognize them only as a HOLD/NO_TRADE pending-gates
availability state:

* Step 2 render, weekly actionable path, order compiler, and every gate are
  unchanged and never receive these files. The active
  `compiled_research_handoff_candidate.json` stays the non-actionable source of
  record for the existing compiled-handoff behavior.
* The pointer explicitly carries ``promotion_status: "pending_gates"`` and
  ``permission_effect: "none_until_consumed_by_future_gate_pr"`` — it is **not**
  trading authorization. Opening each gate (R2E.5b-6/7) requires future explicit
  PRs.
* No ``NEW_BUY`` / ``ORDER_COMPILATION`` permission is added;
  ``STRICT_FRESH_COMPILED_ACTIONABLE`` is NOT enabled.

Fail-closed: any missing / malformed / mismatched / expired input yields
``active_pointer_created: false`` with deterministic blockers, no pointer /
effective files are written, and any stale pointer files from a previous
now-not-promotable run are removed — the pointer file exists **iff** the latest
run was promotable. A deterministic write-status artifact
(`active_research_handoff_source_write_status.json`) records the outcome either
way. The writer never raises.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from investment_orchestrator.research.actionable_handoff_candidate import (
    CANDIDATE_SCHEMA_VERSION as ACTIONABLE_CANDIDATE_SCHEMA_VERSION,
)
from investment_orchestrator.research.actionable_promotion_pointer_preview import (
    SCHEMA_VERSION as POINTER_PREVIEW_SCHEMA_VERSION,
)
from investment_orchestrator.research.research_anchors import normalize_iso_date_value


SCHEMA_VERSION = "active_research_handoff_source_v1"
WRITE_STATUS_SCHEMA_VERSION = "active_research_handoff_source_write_status_v1"

# The only promotion source the pointer may reference (§25.5 gate design).
POINTER_SOURCE = "promoted_compiled_actionable_handoff"

# The pointer exists but no gate PR has landed: NOT trading authorization.
PROMOTION_STATUS_PENDING_GATES = "pending_gates"
PERMISSION_EFFECT_PENDING_GATES = "none_until_consumed_by_future_gate_pr"

# --- deterministic creation-blocker reason codes --------------------------------
POINTER_WRITE_BLOCKER_PREVIEW_MISSING = "pointer_preview_missing"
POINTER_WRITE_BLOCKER_PREVIEW_MALFORMED = "pointer_preview_malformed"
POINTER_WRITE_BLOCKER_PREVIEW_MARKERS_INVALID = "preview_markers_invalid"
POINTER_WRITE_BLOCKER_WOULD_PROMOTE_FALSE = "would_promote_false"
POINTER_WRITE_BLOCKER_EFFECTIVE_PREVIEW_MISSING = "effective_preview_missing"
POINTER_WRITE_BLOCKER_EFFECTIVE_VALIDATION_FAILED = "effective_preview_validation_failed"
POINTER_WRITE_BLOCKER_EFFECTIVE_HASH_MISMATCH = "effective_hash_mismatch"
POINTER_WRITE_BLOCKER_PROMOTION_EXPIRED = "promotion_expired"
POINTER_WRITE_BLOCKER_NO_ACTIONABLE_ROWS = "no_actionable_rows"

POINTER_WRITE_BLOCKER_REASON_CODES = (
    POINTER_WRITE_BLOCKER_PREVIEW_MISSING,
    POINTER_WRITE_BLOCKER_PREVIEW_MALFORMED,
    POINTER_WRITE_BLOCKER_PREVIEW_MARKERS_INVALID,
    POINTER_WRITE_BLOCKER_WOULD_PROMOTE_FALSE,
    POINTER_WRITE_BLOCKER_EFFECTIVE_PREVIEW_MISSING,
    POINTER_WRITE_BLOCKER_EFFECTIVE_VALIDATION_FAILED,
    POINTER_WRITE_BLOCKER_EFFECTIVE_HASH_MISMATCH,
    POINTER_WRITE_BLOCKER_PROMOTION_EXPIRED,
    POINTER_WRITE_BLOCKER_NO_ACTIONABLE_ROWS,
)

_PENDING_GATES_NOTE = (
    "REAL active promotion pointer (R2E.5b-5a) with promotion_status=pending_gates: it records that a "
    "fully verified, validator-passing actionable compiled handoff has been promoted to 'effective' — "
    "but it is NOT trading authorization "
    "(permission_effect=none_until_consumed_by_future_gate_pr, not_authorization=true): availability may "
    "recognize it only as a HOLD/NO_TRADE pending-gates diagnostic; Step 2, the weekly actionable path, "
    "the order compiler, and every gate are unchanged and still key off the non-actionable active compiled "
    "handoff. Opening each gate for NEW_BUY / ORDER_COMPILATION (R2E.5b-6/7) requires future explicit PRs "
    "(future_pr_required=true)."
)

_NOT_CREATED_NOTE = (
    "Write-status record (R2E.5b-5a): the real active pointer was NOT created this run — a fail-closed "
    "creation rule blocked it (see pointer_blockers). No pointer / effective-handoff files exist for this "
    "run; any stale ones from a previous run were removed. Report-only; no NEW_BUY / ORDER_COMPILATION "
    "permission exists."
)


def write_actionable_promotion_pointer_if_eligible(
    *,
    pointer_preview: Mapping[str, Any] | None,
    effective_preview: Mapping[str, Any] | None,
    effective_preview_validation: Mapping[str, Any] | None,
    output_pointer_path: Any,
    output_effective_path: Any,
    output_effective_validation_path: Any,
    output_status_path: Any = None,
    strategy_settings: Mapping[str, Any] | None = None,
    today: Any = None,
    created_at: str | None = None,
    pointer_preview_path: Any = None,
) -> dict[str, Any]:
    """Write the real pointer + effective handoff iff every creation rule passes.

    Never raises. On any failed rule: no pointer / effective files are written,
    stale ones from a previous run are removed, and the returned (and written)
    status records ``active_pointer_created: false`` with deterministic blockers.
    The effective handoff body is the preview copied **unmutated** and must
    re-pass :func:`validate_research_handoff` before the pointer is created.
    """
    from investment_orchestrator.common.io import write_json
    from investment_orchestrator.validators.validate_research_handoff import (
        research_handoff_validation_result_to_dict,
        validate_research_handoff,
    )

    preview = pointer_preview if isinstance(pointer_preview, Mapping) else None
    effective_src = effective_preview if isinstance(effective_preview, Mapping) else None
    preview_validation = (
        effective_preview_validation if isinstance(effective_preview_validation, Mapping) else None
    )

    blockers: list[str] = []

    def block(code: str) -> None:
        if code not in blockers:
            blockers.append(code)

    # --- creation rules (all fail closed) -----------------------------------------
    if preview is None:
        block(POINTER_WRITE_BLOCKER_PREVIEW_MISSING)
    else:
        would_promote = preview.get("would_promote")
        preview_blockers = preview.get("pointer_blockers")
        if (
            preview.get("schema_version") != POINTER_PREVIEW_SCHEMA_VERSION
            or not isinstance(would_promote, bool)
            or not isinstance(preview_blockers, list)
            or (would_promote is True and len(preview_blockers) > 0)
        ):
            block(POINTER_WRITE_BLOCKER_PREVIEW_MALFORMED)
        if (
            preview.get("report_only") is not True
            or preview.get("permission_effect") != "none"
            or preview.get("not_authorization") is not True
            or preview.get("future_pr_required") is not True
            or preview.get("active_pointer_created") is not False
        ):
            block(POINTER_WRITE_BLOCKER_PREVIEW_MARKERS_INVALID)
        if would_promote is not True:
            block(POINTER_WRITE_BLOCKER_WOULD_PROMOTE_FALSE)

    if effective_src is None or effective_src.get("schema_version") != ACTIONABLE_CANDIDATE_SCHEMA_VERSION:
        block(POINTER_WRITE_BLOCKER_EFFECTIVE_PREVIEW_MISSING)

    if preview_validation is None or preview_validation.get("valid") is not True:
        block(POINTER_WRITE_BLOCKER_EFFECTIVE_VALIDATION_FAILED)

    # The effective body must be EXACTLY the candidate the preview approved.
    effective_sha256 = _sha256_of(effective_src) if effective_src is not None else None
    approved_sha256 = preview.get("candidate_sha256") if preview else None
    approved_sha256 = approved_sha256 if isinstance(approved_sha256, str) and approved_sha256 else None
    if effective_sha256 is None or approved_sha256 is None or effective_sha256 != approved_sha256:
        block(POINTER_WRITE_BLOCKER_EFFECTIVE_HASH_MISMATCH)

    # Expiry re-check at write time (falls back to the preview's own today).
    today_iso = normalize_iso_date_value(today)
    if today_iso is None and preview is not None:
        today_iso = normalize_iso_date_value(preview.get("today"))
    expires_iso = normalize_iso_date_value(preview.get("promotion_expires_at")) if preview else None
    if expires_iso is None or today_iso is None:
        block(POINTER_WRITE_BLOCKER_PROMOTION_EXPIRED)
    elif date.fromisoformat(expires_iso) < date.fromisoformat(today_iso):
        block(POINTER_WRITE_BLOCKER_PROMOTION_EXPIRED)

    row_count = preview.get("candidate_actionable_row_count") if preview else None
    tickers = _string_items(preview.get("actionable_this_run_tickers")) if preview else []
    if not (isinstance(row_count, int) and not isinstance(row_count, bool) and row_count > 0 and tickers):
        block(POINTER_WRITE_BLOCKER_NO_ACTIONABLE_ROWS)

    # Independent strict re-validation of the exact body about to be written.
    effective_copy: dict[str, Any] | None = None
    revalidation_dict: dict[str, Any] | None = None
    if not blockers and effective_src is not None:
        try:
            effective_copy = _deep_copy(effective_src)
            revalidation = validate_research_handoff(effective_copy, strategy_settings=strategy_settings)
            revalidation_dict = research_handoff_validation_result_to_dict(revalidation)
            if revalidation.valid is not True:
                block(POINTER_WRITE_BLOCKER_EFFECTIVE_VALIDATION_FAILED)
        except Exception:  # noqa: BLE001 - fail closed, never raise
            block(POINTER_WRITE_BLOCKER_EFFECTIVE_VALIDATION_FAILED)

    created = not blockers

    removed_stale: list[str] = []
    pointer_payload: dict[str, Any] | None = None
    try:
        if created and effective_copy is not None and revalidation_dict is not None:
            write_json(output_effective_path, effective_copy)
            write_json(output_effective_validation_path, revalidation_dict)
            pointer_payload = {
                "schema_version": SCHEMA_VERSION,
                "is_llm_generated": False,
                "source": POINTER_SOURCE,
                # Pending gates: the pointer exists but is NOT yet trading
                # authorization — no consumer reads it until future explicit PRs.
                "promotion_status": PROMOTION_STATUS_PENDING_GATES,
                "active_pointer_created": True,
                "effective_handoff_created": True,
                "permission_effect": PERMISSION_EFFECT_PENDING_GATES,
                "not_authorization": True,
                "candidate_path": preview.get("candidate_path") if preview else None,
                "candidate_sha256": approved_sha256,
                "effective_handoff_path": str(output_effective_path),
                "effective_handoff_sha256": effective_sha256,
                "effective_validation_path": str(output_effective_validation_path),
                "eligibility_path": preview.get("eligibility_path") if preview else None,
                "eligibility_sha256": preview.get("eligibility_sha256") if preview else None,
                "pointer_preview_path": (
                    str(pointer_preview_path) if pointer_preview_path is not None else None
                ),
                "pointer_preview_sha256": _sha256_of(preview),
                "candidate_schema_version": preview.get("candidate_schema_version") if preview else None,
                "candidate_validation_passed": (
                    preview.get("candidate_validation_passed") if preview else None
                ),
                "candidate_actionable_row_count": row_count,
                "actionable_this_run_tickers": tickers,
                "earliest_anchor_valid_until": (
                    preview.get("earliest_anchor_valid_until") if preview else None
                ),
                "promotion_expires_at": expires_iso,
                "source_chain_hashes": preview.get("source_chain_hashes") if preview else None,
                "created_at": created_at if created_at is not None else today_iso,
                "consumed_by_availability": False,
                "consumed_by_step2": False,
                "consumed_by_gates": False,
                "future_pr_required": True,
                "notes": _PENDING_GATES_NOTE,
            }
            write_json(output_pointer_path, pointer_payload)
        else:
            # Fail closed: the pointer file must exist iff the LATEST run was
            # promotable — remove any stale pointer / effective files.
            removed_stale = _remove_if_exists(
                output_pointer_path, output_effective_path, output_effective_validation_path
            )
    except Exception:  # noqa: BLE001 - a write failure is a non-creation, never a crash
        created = False
        block("pointer_write_failed")
        removed_stale = _remove_if_exists(
            output_pointer_path, output_effective_path, output_effective_validation_path
        )

    status = {
        "schema_version": WRITE_STATUS_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "not_authorization": True,
        "future_pr_required": True,
        "active_pointer_created": created,
        "effective_handoff_created": created,
        "promotion_status": PROMOTION_STATUS_PENDING_GATES if created else None,
        "permission_effect": PERMISSION_EFFECT_PENDING_GATES if created else "none",
        "pointer_blockers": blockers,
        "active_pointer_path": str(output_pointer_path) if created else None,
        "effective_handoff_path": str(output_effective_path) if created else None,
        "effective_validation_path": str(output_effective_validation_path) if created else None,
        "removed_stale_artifacts": removed_stale,
        "today": today_iso,
        "created_at": (created_at if created_at is not None else today_iso) if created else None,
        "consumed_by_availability": False,
        "consumed_by_step2": False,
        "consumed_by_gates": False,
        "notes": _PENDING_GATES_NOTE if created else _NOT_CREATED_NOTE,
    }
    if output_status_path is not None:
        try:
            write_json(output_status_path, status)
        except Exception:  # noqa: BLE001 - status write is best-effort
            pass

    return {
        "active_pointer_created": str(created),
        "active_research_handoff_source_path": str(output_pointer_path) if created else "",
        "effective_research_handoff_path": str(output_effective_path) if created else "",
        "effective_research_handoff_validation_path": (
            str(output_effective_validation_path) if created else ""
        ),
        "active_pointer_write_status_path": (
            str(output_status_path) if output_status_path is not None else ""
        ),
        "pointer_blockers": list(blockers),
    }


# --- helpers ---------------------------------------------------------------------


def _remove_if_exists(*paths: Any) -> list[str]:
    removed: list[str] = []
    for raw in paths:
        try:
            path = Path(raw)
            if path.is_file():
                path.unlink()
                removed.append(str(path))
        except Exception:  # noqa: BLE001 - best-effort cleanup, never raise
            continue
    return removed


def _sha256_of(value: Any) -> str | None:
    """Canonical content hash — identical serialization to the preview chain."""
    if value is None:
        return None
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _deep_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value))


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]
