"""Step 1C promotion POINTER PREVIEW + effective-handoff PREVIEW (R2E.5b-4, report-only).

Previews what the future active-pointer promotion (R2E.5b-2 §25.3 option B) would
look like — **without promoting anything**. It builds a report-only *pointer
preview* from the R2E.5b-3 eligibility verdict and, when the preview says
``would_promote: true``, materializes a report-only *effective-handoff preview*
(a byte-identical copy of the actionable candidate) and re-validates it with the
existing strict validator.

This module changes **no** production behavior:

* The real pointer (`active_research_handoff_source.json`) and the real effective
  handoff (`research_handoff_candidate_effective.json`) are **NOT created** —
  those names stay reserved for a future explicit promotion PR (R2E.5b-5+).
* No consumer reads these previews: they are NEVER fed into the availability
  evaluator, the degraded-mode decision, Step 2 render, the weekly path, the
  order compiler, or any gate. The active
  `compiled_research_handoff_candidate.json` stays non-actionable.
* It NEVER authorizes a trade and NEVER adds `NEW_BUY` / `ORDER_COMPILATION`;
  `STRICT_FRESH_COMPILED_ACTIONABLE` / `STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES`
  are NOT enabled.

``would_promote: true`` is strictly diagnostic. Every artifact carries
``is_llm_generated: false``, ``report_only: true``, ``permission_effect: "none"``,
``not_authorization: true``, ``active_pointer_created: false``,
``effective_handoff_created: false``, and ``future_pr_required: true``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from typing import Any

from investment_orchestrator.research.actionable_handoff_candidate import (
    CANDIDATE_SCHEMA_VERSION as ACTIONABLE_CANDIDATE_SCHEMA_VERSION,
)
from investment_orchestrator.research.actionable_promotion_eligibility import (
    SCHEMA_VERSION as ELIGIBILITY_SCHEMA_VERSION,
)
from investment_orchestrator.research.research_anchors import normalize_iso_date_value


SCHEMA_VERSION = "compiled_actionable_handoff_promotion_pointer_preview_v1"

# The only promotion source the future pointer may reference.
PROMOTION_SOURCE = "compiled_actionable_research_handoff_candidate"

# Reserved names for the FUTURE explicit promotion PR — never written here.
RESERVED_ACTIVE_POINTER_PATH = "artifacts/current/step1_research/active_research_handoff_source.json"
RESERVED_EFFECTIVE_HANDOFF_PATH = (
    "artifacts/current/step1_research/research_handoff_candidate_effective.json"
)

_ACTIONABLE_STATUS = "actionable_this_run"

# --- deterministic pointer blocker reason codes --------------------------------
POINTER_BLOCKER_ELIGIBILITY_MISSING = "eligibility_missing"
POINTER_BLOCKER_ELIGIBILITY_MALFORMED = "eligibility_malformed"
POINTER_BLOCKER_ELIGIBILITY_NOT_ELIGIBLE = "eligibility_not_eligible"
POINTER_BLOCKER_PERMISSION_MARKERS_INVALID = "permission_markers_invalid"
POINTER_BLOCKER_CANDIDATE_MISSING = "candidate_missing"
POINTER_BLOCKER_CANDIDATE_VALIDATION_FAILED = "candidate_validation_failed"
POINTER_BLOCKER_CANDIDATE_HASH_MISMATCH = "candidate_hash_mismatch"
POINTER_BLOCKER_CANDIDATE_EXPIRED = "candidate_expired"
POINTER_BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS = "no_candidate_actionable_rows"
POINTER_BLOCKER_SOURCE_CHAIN_MISSING = "source_chain_missing"

POINTER_BLOCKER_REASON_CODES = (
    POINTER_BLOCKER_ELIGIBILITY_MISSING,
    POINTER_BLOCKER_ELIGIBILITY_MALFORMED,
    POINTER_BLOCKER_ELIGIBILITY_NOT_ELIGIBLE,
    POINTER_BLOCKER_PERMISSION_MARKERS_INVALID,
    POINTER_BLOCKER_CANDIDATE_MISSING,
    POINTER_BLOCKER_CANDIDATE_VALIDATION_FAILED,
    POINTER_BLOCKER_CANDIDATE_HASH_MISMATCH,
    POINTER_BLOCKER_CANDIDATE_EXPIRED,
    POINTER_BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS,
    POINTER_BLOCKER_SOURCE_CHAIN_MISSING,
)

# --- deterministic pointer warning reason codes (never affect would_promote) ---
POINTER_WARNING_ACTIVE_BASE_UNVERIFIED = "active_compiled_handoff_hash_unverified"

POINTER_WARNING_REASON_CODES = (POINTER_WARNING_ACTIVE_BASE_UNVERIFIED,)

_NON_AUTHORIZATION_NOTE = (
    "Report-only promotion POINTER PREVIEW (R2E.5b-4). This artifact previews what the future "
    "active-pointer promotion WOULD look like; would_promote=true is strictly diagnostic. Nothing "
    "is promoted: the reserved active_research_handoff_source.json and "
    "research_handoff_candidate_effective.json are NOT created (future_pr_required=true), the active "
    "compiled_research_handoff_candidate.json stays non-actionable, and no consumer reads this "
    "preview — it is never fed into the availability evaluator, the degraded-mode decision, Step 2, "
    "the weekly path, the order compiler, or any gate. It NEVER authorizes a trade and adds no "
    "NEW_BUY / ORDER_COMPILATION permission (permission_effect=none, not_authorization=true). "
    "Creating the real pointer and opening any gate each require a future explicit PR."
)


# --- pure builder ---------------------------------------------------------------


def build_actionable_promotion_pointer_preview(
    *,
    eligibility: Mapping[str, Any] | None,
    actionable_candidate: Mapping[str, Any] | None,
    actionable_candidate_validation: Mapping[str, Any] | None,
    actionable_candidate_metadata: Mapping[str, Any] | None,
    today: Any = None,
    generated_at: str | None = None,
    candidate_path: Any = None,
    eligibility_path: Any = None,
) -> dict[str, Any]:
    """Build the report-only pointer preview (pure; never raises).

    ``would_promote`` is true only when the R2E.5b-3 eligibility verdict is
    eligible AND every pointer-level re-check passes (fail closed on any missing /
    malformed / mismatched / expired input). ``today`` (ISO string or ``date``)
    re-checks ``promotion_expires_at`` at pointer time; when omitted it falls back
    to the eligibility artifact's own ``today``.
    """
    elig = eligibility if isinstance(eligibility, Mapping) else None
    candidate = actionable_candidate if isinstance(actionable_candidate, Mapping) else None
    validation = (
        actionable_candidate_validation
        if isinstance(actionable_candidate_validation, Mapping)
        else None
    )
    metadata = (
        actionable_candidate_metadata if isinstance(actionable_candidate_metadata, Mapping) else None
    )

    blockers: list[str] = []
    warnings: list[str] = []

    def block(code: str) -> None:
        if code not in blockers:
            blockers.append(code)

    # --- eligibility presence / shape / markers ---------------------------------
    if elig is None:
        block(POINTER_BLOCKER_ELIGIBILITY_MISSING)
    else:
        if elig.get("schema_version") != ELIGIBILITY_SCHEMA_VERSION or not isinstance(
            elig.get("eligible_for_promotion"), bool
        ):
            block(POINTER_BLOCKER_ELIGIBILITY_MALFORMED)
        if (
            elig.get("report_only") is not True
            or elig.get("not_authorization") is not True
            or elig.get("permission_effect") != "none"
        ):
            block(POINTER_BLOCKER_PERMISSION_MARKERS_INVALID)
        if elig.get("eligible_for_promotion") is not True:
            block(POINTER_BLOCKER_ELIGIBILITY_NOT_ELIGIBLE)

    # --- candidate presence / markers --------------------------------------------
    if candidate is None or candidate.get("schema_version") != ACTIONABLE_CANDIDATE_SCHEMA_VERSION:
        block(POINTER_BLOCKER_CANDIDATE_MISSING)
    elif (
        candidate.get("is_llm_generated") is not False
        or candidate.get("report_only") is not True
        or candidate.get("not_authorization") is not True
        or candidate.get("permission_effect") != "none"
    ):
        block(POINTER_BLOCKER_PERMISSION_MARKERS_INVALID)

    # --- strict validation (defense in depth; eligibility already checked) -------
    candidate_validation_passed = validation is not None and validation.get("valid") is True
    if not candidate_validation_passed:
        block(POINTER_BLOCKER_CANDIDATE_VALIDATION_FAILED)
    if metadata is not None and metadata.get("validation_passed") is not True:
        block(POINTER_BLOCKER_CANDIDATE_VALIDATION_FAILED)

    # --- the candidate must be EXACTLY the one eligibility evaluated -------------
    candidate_sha256 = _sha256_of(candidate) if candidate is not None else None
    approved_sha256 = elig.get("candidate_sha256") if elig else None
    approved_sha256 = approved_sha256 if isinstance(approved_sha256, str) and approved_sha256 else None
    if candidate_sha256 is None or approved_sha256 is None or candidate_sha256 != approved_sha256:
        block(POINTER_BLOCKER_CANDIDATE_HASH_MISMATCH)

    # --- expiry re-check at pointer time -------------------------------------------
    today_iso = normalize_iso_date_value(today)
    if today_iso is None and elig is not None:
        today_iso = normalize_iso_date_value(elig.get("today"))
    expires_iso = normalize_iso_date_value(elig.get("promotion_expires_at")) if elig else None
    if expires_iso is None or today_iso is None:
        block(POINTER_BLOCKER_CANDIDATE_EXPIRED)
    elif date.fromisoformat(expires_iso) < date.fromisoformat(today_iso):
        block(POINTER_BLOCKER_CANDIDATE_EXPIRED)

    # --- promoted rows: non-empty and consistent with the eligibility verdict -----
    actionable_tickers = _actionable_tickers(candidate)
    row_count = len(actionable_tickers)
    if row_count == 0:
        block(POINTER_BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS)
    if elig is not None and (
        elig.get("candidate_actionable_row_count") != row_count
        or _string_items(elig.get("actionable_this_run_tickers")) != actionable_tickers
    ):
        block(POINTER_BLOCKER_ELIGIBILITY_MALFORMED)

    # --- source chain carried by the eligibility verdict ---------------------------
    source_chain = elig.get("source_hashes") if elig else None
    source_chain = source_chain if isinstance(source_chain, Mapping) else None
    chain_ok = (
        source_chain is not None
        and (elig or {}).get("hash_chain_valid") is True
        and all(
            isinstance(source_chain.get(label), Mapping)
            and source_chain[label].get("match") is True
            for label in ("evidence_packet", "compiled_support_signals", "actionable_handoff_preview")
        )
    )
    if not chain_ok:
        block(POINTER_BLOCKER_SOURCE_CHAIN_MISSING)
    active_ref = source_chain.get("active_compiled_handoff") if source_chain else None
    if isinstance(active_ref, Mapping) and active_ref.get("match") is None:
        warnings.append(POINTER_WARNING_ACTIVE_BASE_UNVERIFIED)

    would_promote = not blockers

    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "generated_at": generated_at,
        "today": today_iso,
        # Strictly diagnostic: nothing is promoted even when true.
        "would_promote": would_promote,
        "promotion_source": PROMOTION_SOURCE,
        "candidate_path": str(candidate_path) if candidate_path is not None else None,
        "candidate_sha256": candidate_sha256,
        "candidate_schema_version": candidate.get("schema_version") if candidate else None,
        "eligibility_path": str(eligibility_path) if eligibility_path is not None else None,
        "eligibility_sha256": _sha256_of(elig) if elig is not None else None,
        "eligibility_schema_version": elig.get("schema_version") if elig else None,
        # Alias of eligibility_sha256 under the exact field name the future real
        # pointer will carry.
        "eligibility_hash": _sha256_of(elig) if elig is not None else None,
        "candidate_validation_passed": candidate_validation_passed,
        "candidate_actionable_row_count": row_count,
        "actionable_this_run_tickers": actionable_tickers,
        "earliest_anchor_valid_until": (
            normalize_iso_date_value(elig.get("earliest_anchor_valid_until")) if elig else None
        ),
        "promotion_expires_at": expires_iso,
        "source_chain_hashes": dict(source_chain) if source_chain is not None else None,
        "pointer_blockers": blockers,
        "pointer_warnings": warnings,
        # Reserved names for the FUTURE explicit promotion PR; never written here.
        "reserved_active_pointer_path": RESERVED_ACTIVE_POINTER_PATH,
        "reserved_effective_handoff_path": RESERVED_EFFECTIVE_HANDOFF_PATH,
        "active_pointer_created": False,
        "effective_handoff_created": False,
        "future_pr_required": True,
        "consumed_by_availability": False,
        "consumed_by_step2": False,
        "consumed_by_gates": False,
        "notes": _NON_AUTHORIZATION_NOTE,
    }


# --- disk wrapper -----------------------------------------------------------------


def write_actionable_promotion_pointer_preview(
    *,
    pointer_preview_path: Any,
    effective_preview_path: Any,
    effective_preview_validation_path: Any,
    eligibility: Mapping[str, Any] | None,
    actionable_candidate: Mapping[str, Any] | None,
    actionable_candidate_validation: Mapping[str, Any] | None,
    actionable_candidate_metadata: Mapping[str, Any] | None,
    strategy_settings: Mapping[str, Any] | None = None,
    today: Any = None,
    generated_at: str | None = None,
    candidate_path: Any = None,
    eligibility_path: Any = None,
) -> dict[str, Any]:
    """Build + write the pointer preview; on ``would_promote`` also write the
    effective-handoff preview (an unmodified candidate copy) + its validation.

    The effective preview deliberately does NOT mutate the handoff body (the
    strict validator re-validates it as-is); all promotion metadata lives in the
    pointer preview. When ``would_promote`` is false, no effective-preview files
    are written — the pointer preview's explicit blockers are the record.
    """
    from investment_orchestrator.common.io import write_json
    from investment_orchestrator.validators.validate_research_handoff import (
        research_handoff_validation_result_to_dict,
        validate_research_handoff,
    )

    preview = build_actionable_promotion_pointer_preview(
        eligibility=eligibility,
        actionable_candidate=actionable_candidate,
        actionable_candidate_validation=actionable_candidate_validation,
        actionable_candidate_metadata=actionable_candidate_metadata,
        today=today,
        generated_at=generated_at,
        candidate_path=candidate_path,
        eligibility_path=eligibility_path,
    )

    effective_written = False
    effective_valid: bool | None = None
    if preview["would_promote"] and isinstance(actionable_candidate, Mapping):
        effective = _deep_copy(actionable_candidate)
        validation = validate_research_handoff(effective, strategy_settings=strategy_settings)
        write_json(effective_preview_path, effective)
        write_json(
            effective_preview_validation_path,
            research_handoff_validation_result_to_dict(validation),
        )
        effective_written = True
        effective_valid = validation.valid is True

    preview["effective_preview_written"] = effective_written
    preview["effective_preview_path"] = str(effective_preview_path) if effective_written else None
    preview["effective_preview_validation_path"] = (
        str(effective_preview_validation_path) if effective_written else None
    )
    preview["effective_preview_valid"] = effective_valid
    write_json(pointer_preview_path, preview)

    return {
        "actionable_promotion_pointer_preview_path": str(pointer_preview_path),
        "would_promote": str(preview["would_promote"]),
        "effective_preview_written": str(effective_written),
        "actionable_effective_handoff_preview_path": (
            str(effective_preview_path) if effective_written else ""
        ),
        "actionable_effective_handoff_preview_validation_path": (
            str(effective_preview_validation_path) if effective_written else ""
        ),
    }


# --- helpers -------------------------------------------------------------------------


def _actionable_tickers(candidate: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(candidate, Mapping):
        return []
    scorecard = candidate.get("buy_universe_scorecard")
    if not isinstance(scorecard, list):
        return []
    out: list[str] = []
    for row in scorecard:
        if isinstance(row, Mapping) and row.get("actionability_status") == _ACTIONABLE_STATUS:
            ticker = row.get("ticker")
            if isinstance(ticker, str) and ticker.strip():
                out.append(ticker.strip().upper())
    return out


def _sha256_of(value: Any) -> str | None:
    """Canonical content hash — identical serialization to the eligibility artifact."""
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
