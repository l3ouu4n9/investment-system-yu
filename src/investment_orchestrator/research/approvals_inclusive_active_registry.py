"""R2G-5b: report-only approvals-inclusive active research-anchor registry.

Builds a SEPARATE, report-only ``active_research_anchor_registry_with_approvals``
that overlays validated operator-approved anchors (from
``inputs/current/research_anchor_approvals.yaml``) on top of the exact baseline
registry compiled from ``research_anchors.yaml``. It is an OBSERVER only:

* It is **not directly consumed** by ``support_signals``. Runtime grounding consumes
  whatever registry is embedded in ``evidence_packet.active_anchor_registry`` by
  the R2G-5c-2 readiness-gated evidence-packet selector.
* NOTHING consumes this standalone artifact directly: not ``support_signals``, not
  the compiler, not the actionable preview / candidate / promotion eligibility,
  not availability, not gates, not Step 2/3/4, not the final gate, not weekly, not
  broker/live.

Activation model (recomputed here — the R2G-5a validation artifact is NEVER read
as authority; approvals are re-validated directly from YAML via the R2G-5a
validator functions):

* ``operator_completed_anchor_sha256`` is the ONLY activation-binding hash. A
  mismatched / missing hash, or any mutation of ``operator_completed_anchor``,
  makes the approval inactive.
* approval activation also requires the compiler's independent explicit
  ``today`` boundary. The boundary is normalized locally and the exact approval
  source bytes are revalidated in the same call. A missing or invalid boundary
  blocks every otherwise eligible approval before it can enter ``active_anchors``;
  the approval manifest's own date is never a substitute.
* the anchor must pass the existing ``validate_research_anchors`` (never
  loosened; no new ``source_type``; an approved anchor's intrinsic
  ``source_type`` stays ``"operator"``), be fresh/usable, and carry an
  ``approve`` decision.
* ``candidate_id`` / ``candidate_sha256`` / ``candidate_link_status`` are
  **audit-only** and can never authorize or block activation.

Duplicate policy (fail-closed, no silent precedence):

* ``anchor_id`` must be globally unique across ``research_anchors.yaml`` and the
  approvals manifest. A cross-source duplicate sets ``registry_valid: false``,
  records ``duplicate_anchor_id_across_sources``, and excludes the conflicting
  anchor from ``active_anchors`` on **both** sides (neither wins).
* a within-approvals duplicate ``anchor_id`` (or duplicate ``approval_id``, wrong
  schema, ``is_llm_generated: true``, malformed YAML) fails the approvals source
  closed: zero approval anchors are merged, ``registry_valid: false``.

It grants nothing: ``permission_effect: "none"``, ``not_authorization: true``, no
``NEW_BUY`` / ``ORDER_COMPILATION``, no order path.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from investment_orchestrator.research.active_research_anchor_registry import (
    OPERATOR_SOURCE_CATEGORY,
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_INVALID,
    compile_active_research_anchor_registry,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    ResearchAnchorApprovalYamlPolicyError,
    build_research_anchor_approvals_validation,
    load_research_anchor_approval_yaml,
)
from investment_orchestrator.research.research_anchors import (
    RESEARCH_ANCHOR_TRUSTED_DATE_INVALID,
    RESEARCH_ANCHOR_TRUSTED_DATE_MISSING,
    normalize_iso_date_value,
)
from investment_orchestrator.research.research_anchor_revocation_manifest import (
    BIND_RESOLVED,
    STATUS_VALID_ACTIVE as REVOCATION_STATUS_VALID_ACTIVE,
    STATUS_VALID_PENDING_FUTURE as REVOCATION_STATUS_VALID_PENDING_FUTURE,
    build_research_anchor_revocations_validation,
)


SCHEMA_VERSION = "active_research_anchor_registry_with_approvals_v1"
COMPILER_VERSION = "active_registry_with_approvals_compiler_v1"

APPROVALS_SOURCE_ID = "operator_research_anchor_approvals_yaml"
BASELINE_SOURCE_ID = "operator_research_anchors_yaml"
REVOCATIONS_SOURCE_ID = "operator_research_anchor_revocations_yaml"

APPROVAL_TYPE_AUTHORED = "operator_authored"
APPROVAL_TYPE_APPROVED_CANDIDATE = "operator_approved_candidate"

DECISION_APPROVE = "approve"

# Registry-level blocker codes specific to the approvals overlay.
BLOCKER_APPROVALS_MANIFEST_INVALID = "approvals_manifest_invalid"
BLOCKER_DUPLICATE_ACROSS_SOURCES = "duplicate_anchor_id_across_sources"
BLOCKER_DUPLICATE_WITHIN_APPROVALS = "duplicate_anchor_id_within_approvals"
BLOCKER_REVOCATIONS_INVALID = "revocations_manifest_invalid"
BLOCKER_DUPLICATE_TARGET_REVOCATION = "duplicate_target_revocation"
WORKFLOW_APPROVAL_SOURCE_IDENTITY_MISMATCH = (
    "workflow_approval_source_identity_mismatch"
)

STATUS_REVOKED = "revoked"


class ApprovalSourceState(str, Enum):
    """Closed filesystem state for one combined approval/revocation snapshot."""

    ABSENT = "absent"
    PRESENT = "present"
    READ_ERROR = "read_error"


CAPTURE_INVALID = "approval_source_capture_invalid"
_APPROVAL_SOURCE_TEXT_INVALID = "approval_source_text_invalid"
_APPROVAL_SOURCE_UTF8_DECODE_ERROR = "approval_source_utf8_decode_error"
_APPROVAL_SOURCE_READ_ERROR = "approval_source_read_error"
_CODE_OWNED_CAPTURE_ERRORS = frozenset(
    {
        CAPTURE_INVALID,
        _APPROVAL_SOURCE_TEXT_INVALID,
        _APPROVAL_SOURCE_UTF8_DECODE_ERROR,
        _APPROVAL_SOURCE_READ_ERROR,
    }
)


@dataclass(frozen=True, slots=True)
class CapturedResearchAnchorApprovalSource:
    """One immutable byte snapshot shared by every workflow derivation.

    ``source_path`` is diagnostic provenance only.  Authority comes exclusively
    from ``source_bytes`` captured in one read and their SHA-256.  A zero-byte or
    whitespace-only file is PRESENT; only FileNotFoundError is ABSENT.
    """

    source_state: ApprovalSourceState
    source_path: str | None
    source_bytes: bytes | None
    source_text: str | None
    source_sha256: str | None
    read_error: str | None

    def __post_init__(self) -> None:
        """Reject contradictory caller construction before it reaches a consumer.

        A blank PRESENT snapshot is representable because it records that a file
        existed; the activation-boundary invariant below rejects it as invalid
        input. Every other state/field contradiction is rejected immediately.
        """
        reason = _captured_source_invariant_error(self, allow_blank_present=True)
        if reason is not None:
            raise ValueError(reason)


@dataclass(frozen=True, slots=True)
class _ValidatedCapturedResearchAnchorApprovalSource:
    """Code-owned one-read projection used by validation and activation only."""

    source_state: ApprovalSourceState
    source_path: str | None
    source_bytes: bytes
    source_text: str
    source_sha256: str | None
    read_error: str | None


_NOTES = (
    "Report-only approvals-inclusive active anchor registry (R2G-5b). This is a "
    "SEPARATE standalone observer artifact. Runtime support_signals consumes "
    "evidence_packet.active_anchor_registry, whose embedded registry selection is "
    "owned by the R2G-5c-2 readiness-gated evidence-packet builder; this standalone "
    "artifact is NOT read directly by support_signals and is consumed by NOTHING: "
    "not support_signals, not the compiler, not the actionable preview / candidate "
    "/ promotion eligibility, not availability, not gates, not Step 2/3/4, not the "
    "final gate, not weekly, not broker/live. Activation is recomputed directly from "
    "research_anchor_approvals.yaml; the "
    "R2G-5a validation artifact and its would_activate flag are NEVER read as authority. "
    "operator_completed_anchor_sha256 is the only activation-binding hash; candidate_sha256 / "
    "candidate_link_status are audit-only with zero activation authority. Revocations are "
    "always freshly validated from those same captured source bytes and enforced before any "
    "approval-derived anchor can become active; invalid revocation state fails the approvals "
    "overlay closed. "
    "Cross-source duplicate anchor_id fails closed (no silent precedence). It never authorizes "
    "a trade and adds no NEW_BUY / ORDER_COMPILATION (permission_effect=none, "
    "not_authorization=true)."
)


def build_active_research_anchor_registry_with_approvals(
    *,
    baseline: Mapping[str, Any],
    approval_source_text: Any,
    approval_source_path: str | None,
    allowed_universe: Any,
    today: Any,
    generated_at: str | None = None,
    candidate_index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Revalidate one exact approval source, then build its active overlay.

    ``baseline`` is the output of ``active_research_anchor_registry`` (the same
    baseline compiler used by the evidence-packet embedded registry selector).
    Activation accepts only the raw operator source text plus an independently
    supplied trusted ``today`` boundary. It never accepts the persisted R2G-5a
    validation mapping as input. Approval and revocation validation results are
    freshly derived as local values from these exact bytes on every call. This
    function never mutates ``baseline``.
    """
    try:
        captured_source = (
            approval_source_text
            if type(approval_source_text) is CapturedResearchAnchorApprovalSource
            else capture_research_anchor_approval_source_text(
                approval_source_text,
                source_path=approval_source_path,
            )
        )
        return _build_from_captured_source(
            baseline=baseline,
            approval_source=captured_source,
            allowed_universe=allowed_universe,
            today=today,
            generated_at=generated_at,
            candidate_index=candidate_index,
        )
    except Exception:  # noqa: BLE001 - report-only builder must never raise
        # Fail closed: surface the baseline registry unchanged, with zero approvals merged.
        base = baseline if isinstance(baseline, Mapping) else {}
        return {
            "schema_version": SCHEMA_VERSION,
            "is_llm_generated": False,
            "report_only": True,
            "permission_effect": "none",
            "not_authorization": True,
            "not_execution_authorization": True,
            "compiler_version": COMPILER_VERSION,
            "as_of_date": base.get("as_of_date"),
            "generated_at": generated_at,
            "source_manifest": list(base.get("source_manifest") or []),
            "active_anchors": [dict(r) for r in base.get("active_anchors") or []],
            "inactive_anchors": [dict(r) for r in base.get("inactive_anchors") or []],
            "counts": dict(base.get("counts") or {}),
            "registry_valid": False,
            "registry_blockers": ["with_approvals_builder_internal_error"],
            "duplicate_blockers": [],
            "revocations_applied": [],
            "revocations_pending": [],
            "revocation_problems": [],
            "audit_trail": [],
            "is_embedded_registry": False,
            "embedded_in_evidence_packet": False,
            "consumed_by_support_signals": False,
            "consumed_by_active_registry": False,
            "consumed_by_availability": False,
            "consumed_by_gates": False,
            "consumed_by_step2": False,
            "consumed_by_step4": False,
            "cannot_affect_allowed_actions": True,
            "standalone_artifact_not_consumed_by_support_signals": True,
            "embedded_registry_selection_owned_by_evidence_packet": True,
            "notes": _NOTES,
        }


def build_research_anchor_approval_source_validations(
    *,
    approval_source: CapturedResearchAnchorApprovalSource,
    allowed_universe: Any,
    today: Any,
    as_of_date: str | None = None,
    generated_at: str | None = None,
    candidate_index: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build both report-only validations from one immutable source snapshot."""
    sanitized_source = _sanitize_captured_source(approval_source)
    return _build_research_anchor_approval_source_validations_from_sanitized(
        approval_source=sanitized_source,
        allowed_universe=allowed_universe,
        today=today,
        as_of_date=as_of_date,
        generated_at=generated_at,
        candidate_index=candidate_index,
    )


def _build_research_anchor_approval_source_validations_from_sanitized(
    *,
    approval_source: _ValidatedCapturedResearchAnchorApprovalSource,
    allowed_universe: Any,
    today: Any,
    as_of_date: str | None,
    generated_at: str | None,
    candidate_index: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build both validation reports from the already detached private value."""
    source = _decode_approval_source(
        _validated_or_invalid_sanitized_source(approval_source)
    )
    approvals_validation = build_research_anchor_approvals_validation(
        manifest=source["manifest"],
        source_present=source["present"],
        source_sha256=source["sha256"],
        source_path=source["path"],
        allowed_universe=allowed_universe,
        today=today,
        as_of_date=as_of_date,
        generated_at=generated_at,
        candidate_index=candidate_index,
        parse_error=source["parse_error"],
    )
    revocations_validation = build_research_anchor_revocations_validation(
        manifest=source["manifest"],
        approvals_validation=approvals_validation,
        source_present=source["present"],
        source_sha256=source["sha256"],
        source_path=source["path"],
        today=today,
        as_of_date=as_of_date,
        generated_at=generated_at,
        parse_error=source["parse_error"],
    )
    return approvals_validation, revocations_validation


def _build_from_captured_source(
    *,
    baseline: Mapping[str, Any],
    approval_source: CapturedResearchAnchorApprovalSource,
    allowed_universe: Any,
    today: Any,
    generated_at: str | None,
    candidate_index: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate one captured combined source, enforce revocations, then merge.

    This is deliberately not a validation-result merge API. Approval and
    revocation validation mappings are local values derived from the exact same
    captured source text during this call and cannot be supplied by callers.
    """
    sanitized_source = _sanitize_captured_source(approval_source)
    return _build_from_sanitized_source(
        baseline=baseline,
        approval_source=sanitized_source,
        allowed_universe=allowed_universe,
        today=today,
        generated_at=generated_at,
        candidate_index=candidate_index,
    )


def _build_from_sanitized_source(
    *,
    baseline: Mapping[str, Any],
    approval_source: _ValidatedCapturedResearchAnchorApprovalSource,
    allowed_universe: Any,
    today: Any,
    generated_at: str | None,
    candidate_index: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Freshly validate and merge one already detached workflow snapshot.

    This is the sole merge core used by workflow-owned snapshot paths.  The
    private value carries raw input only: approval and revocation validity,
    temporal eligibility, and active rows are recomputed locally on every call.
    A forged or contradictory private value is replaced with the code-owned
    invalid capture before parsing.
    """
    sanitized_source = _validated_or_invalid_sanitized_source(approval_source)
    trusted_today, trusted_date_reason = _trusted_activation_date(today)
    baseline_as_of = baseline.get("as_of_date")
    approvals_validation, revocations_validation = (
        _build_research_anchor_approval_source_validations_from_sanitized(
            approval_source=sanitized_source,
            allowed_universe=allowed_universe,
            today=trusted_today,
            as_of_date=baseline_as_of if isinstance(baseline_as_of, str) else None,
            generated_at=generated_at,
            candidate_index=candidate_index,
        )
    )
    trusted_date_valid = trusted_date_reason is None

    baseline_active = [dict(r) for r in _as_list(baseline.get("active_anchors")) if isinstance(r, Mapping)]
    baseline_inactive = [dict(r) for r in _as_list(baseline.get("inactive_anchors")) if isinstance(r, Mapping)]
    baseline_valid = baseline.get("registry_valid") is True
    baseline_blockers = [b for b in _as_list(baseline.get("registry_blockers")) if isinstance(b, str)]
    baseline_all_ids = {
        r.get("anchor_id")
        for r in (baseline_active + baseline_inactive)
        if isinstance(r.get("anchor_id"), str) and r.get("anchor_id")
    }

    approvals_present = approvals_validation.get("source_present") is True
    approvals_source_valid = approvals_validation.get("source_valid") is True
    approval_results = _as_list(approvals_validation.get("approval_results"))

    # Recompute each approval's activation eligibility independently (NOT read from
    # the artifact's would_activate flag).
    pending: list[tuple[dict[str, Any], dict[str, Any], bool, Mapping[str, Any]]] = []
    inactive_no_anchor: list[dict[str, Any]] = []
    approval_anchor_ids: list[str] = []

    for ar in approval_results:
        if not isinstance(ar, Mapping):
            continue
        validation = _approval_validation(ar)
        eligible = _approval_eligible(ar, approvals_source_valid)
        preview = ar.get("normalized_anchor_preview")
        if not (isinstance(preview, Mapping) and isinstance(preview.get("anchor_id"), str) and preview.get("anchor_id")):
            inactive_no_anchor.append(_approval_inactive_no_anchor(ar, validation))
            continue
        identity = _approval_anchor_identity(ar, preview)
        approval_anchor_ids.append(identity["anchor_id"])
        pending.append((identity, validation, eligible, ar))

    within_approvals_dupes = _duplicate_strings(approval_anchor_ids)
    cross_source_dupes = {aid for aid in approval_anchor_ids if aid in baseline_all_ids}
    conflicting = within_approvals_dupes | cross_source_dupes
    approvals_manifest_invalid = approvals_present and not approvals_source_valid
    activation_requested = any(eligible for _, _, eligible, _ in pending)
    trusted_date_invalid = activation_requested and not trusted_date_valid
    # Convert only the revocation result produced immediately above into local
    # merge decisions. There is intentionally no module-level result-to-merge
    # helper that a caller could invoke with a fabricated validation mapping.
    revocation_results = [
        r
        for r in _as_list(revocations_validation.get("revocation_results"))
        if isinstance(r, Mapping)
    ]
    revocation_validation_blockers = [
        b
        for b in _as_list(revocations_validation.get("blockers"))
        if isinstance(b, str)
    ]
    revocation_problems = _revocation_validation_problems(
        revocations_validation, revocation_results
    )
    revocation_blockers: list[str] = []
    revocations_source_present = revocations_validation.get("source_present") is True
    revocations_source_valid = revocations_validation.get("source_valid") is True
    revocations_valid = revocations_validation.get("revocations_valid") is True
    revocations_overlay_invalid = bool(
        revocations_source_present
        and (not revocations_source_valid or not revocations_valid)
    )
    if revocations_overlay_invalid:
        revocation_blockers.append(BLOCKER_REVOCATIONS_INVALID)
        _extend_unique(revocation_blockers, revocation_validation_blockers)

    revocations_by_target: dict[
        tuple[str | None, str | None, str | None], list[Mapping[str, Any]]
    ] = defaultdict(list)
    for result in revocation_results:
        if (
            result.get("target_binding_status") == BIND_RESOLVED
            and result.get("status")
            in (
                REVOCATION_STATUS_VALID_ACTIVE,
                REVOCATION_STATUS_VALID_PENDING_FUTURE,
            )
        ):
            revocations_by_target[_revocation_target_key(result)].append(result)

    duplicate_revocation_targets = {
        key: rows for key, rows in revocations_by_target.items() if len(rows) > 1
    }
    if duplicate_revocation_targets:
        revocations_overlay_invalid = True
        _extend_unique(
            revocation_blockers,
            [BLOCKER_REVOCATIONS_INVALID, BLOCKER_DUPLICATE_TARGET_REVOCATION],
        )
        for key, rows in sorted(
            duplicate_revocation_targets.items(),
            key=lambda item: _target_sort_key(item[0]),
        ):
            revocation_problems.append(
                {
                    "reason": BLOCKER_DUPLICATE_TARGET_REVOCATION,
                    "approval_id": key[0],
                    "anchor_id": key[1],
                    "operator_completed_anchor_sha256": key[2],
                    "revocation_ids": sorted(
                        str(r.get("revocation_id"))
                        for r in rows
                        if isinstance(r.get("revocation_id"), str)
                    ),
                }
            )

    active_revocations_by_target: dict[
        tuple[str | None, str | None, str | None], Mapping[str, Any]
    ] = {}
    pending_revocations_by_target: dict[
        tuple[str | None, str | None, str | None], list[Mapping[str, Any]]
    ] = defaultdict(list)
    if not revocations_overlay_invalid:
        for result in revocation_results:
            key = _revocation_target_key(result)
            if result.get("status") == REVOCATION_STATUS_VALID_ACTIVE:
                active_revocations_by_target[key] = result
            elif result.get("status") == REVOCATION_STATUS_VALID_PENDING_FUTURE:
                pending_revocations_by_target[key].append(result)

    revocation_state = {
        "overlay_invalid": revocations_overlay_invalid,
        "blockers": (
            revocation_blockers or [BLOCKER_REVOCATIONS_INVALID]
            if revocations_overlay_invalid
            else []
        ),
        "problems": revocation_problems,
        "active_by_target": (
            {} if revocations_overlay_invalid else active_revocations_by_target
        ),
        "pending_by_target": (
            {}
            if revocations_overlay_invalid
            else dict(pending_revocations_by_target)
        ),
    }
    revocations_overlay_invalid = revocation_state["overlay_invalid"]
    active_revocations_by_target = revocation_state["active_by_target"]
    pending_revocations_by_target = revocation_state["pending_by_target"]

    audit_trail: list[dict[str, Any]] = []
    revocations_applied: list[dict[str, Any]] = []
    revocations_pending: list[dict[str, Any]] = []

    # Baseline actives carry over unless the anchor_id conflicts across sources.
    final_active: list[dict[str, Any]] = []
    final_inactive: list[dict[str, Any]] = list(baseline_inactive)
    for row in baseline_active:
        aid = row.get("anchor_id")
        if isinstance(aid, str) and aid in cross_source_dupes:
            final_inactive.append(
                {
                    **row,
                    "status": STATUS_INVALID,
                    "reason": "duplicate_anchor_id_across_sources; excluded (no silent precedence).",
                }
            )
            audit_trail.append(
                {"event": "baseline_anchor_excluded_duplicate", "anchor_id": aid, "source_id": BASELINE_SOURCE_ID}
            )
        else:
            final_active.append(row)

    # Approval anchors: active only when independently eligible, baseline valid, and
    # not part of any duplicate conflict.
    approved_active_count = 0
    for identity, validation, eligible, ar in pending:
        aid = identity["anchor_id"]
        target_key = _revocation_target_key(identity)
        active_revocation = active_revocations_by_target.get(target_key)
        pending_revocations = pending_revocations_by_target.get(target_key, [])
        activate = (
            eligible
            and baseline_valid
            and trusted_date_valid
            and (aid not in conflicting)
            and not revocations_overlay_invalid
        )
        if activate:
            if active_revocation is not None:
                revoked_row = _revoked_anchor_row(identity, validation, active_revocation)
                final_inactive.append(revoked_row)
                applied = _revocation_audit_row(active_revocation)
                revocations_applied.append(applied)
                audit_trail.append(
                    {
                        "event": "anchor_revoked",
                        "anchor_id": aid,
                        "source_id": APPROVALS_SOURCE_ID,
                        "approval_id": identity.get("approval_id"),
                        "revocation_id": active_revocation.get("revocation_id"),
                        "effective_as_of": active_revocation.get("effective_as_of"),
                    }
                )
            else:
                final_active.append({**identity, "status": STATUS_ACTIVE, "validation": validation})
                approved_active_count += 1
                audit_trail.append(
                    {
                        "event": "approval_anchor_activated",
                        "anchor_id": aid,
                        "source_id": APPROVALS_SOURCE_ID,
                        "approval_id": identity.get("approval_id"),
                    }
                )
                for revocation in pending_revocations:
                    pending = _revocation_audit_row(revocation)
                    revocations_pending.append(pending)
                    audit_trail.append(
                        {
                            "event": "revocation_pending_future",
                            "anchor_id": aid,
                            "approval_id": identity.get("approval_id"),
                            "revocation_id": revocation.get("revocation_id"),
                            "effective_as_of": revocation.get("effective_as_of"),
                        }
                    )
        else:
            status, reason = _inactive_status_reason(
                validation=validation,
                aid=aid,
                cross_source_dupes=cross_source_dupes,
                within_approvals_dupes=within_approvals_dupes,
                approvals_manifest_invalid=approvals_manifest_invalid,
                baseline_valid=baseline_valid,
                trusted_date_invalid=trusted_date_invalid,
                trusted_date_reason=trusted_date_reason,
                revocations_overlay_invalid=revocations_overlay_invalid,
            )
            final_inactive.append({**identity, "status": status, "reason": reason, "validation": validation})
            audit_trail.append(
                {"event": "approval_anchor_rejected", "anchor_id": aid, "reason": reason}
            )
            for revocation in ([active_revocation] if active_revocation is not None else []) + list(pending_revocations):
                audit_trail.append(
                    {
                        "event": "revocation_target_not_active",
                        "anchor_id": aid,
                        "approval_id": identity.get("approval_id"),
                        "revocation_id": revocation.get("revocation_id"),
                        "effective_as_of": revocation.get("effective_as_of"),
                        "target_status": status,
                    }
                )
    final_inactive.extend(inactive_no_anchor)

    # Registry-level blockers + validity.
    registry_blockers = list(baseline_blockers)
    duplicate_blockers: list[dict[str, Any]] = []
    if within_approvals_dupes:
        registry_blockers.append(BLOCKER_DUPLICATE_WITHIN_APPROVALS)
        duplicate_blockers.append(
            {"reason": BLOCKER_DUPLICATE_WITHIN_APPROVALS, "anchor_ids": sorted(within_approvals_dupes)}
        )
    if cross_source_dupes:
        registry_blockers.append(BLOCKER_DUPLICATE_ACROSS_SOURCES)
        duplicate_blockers.append(
            {"reason": BLOCKER_DUPLICATE_ACROSS_SOURCES, "anchor_ids": sorted(cross_source_dupes)}
        )
    if approvals_manifest_invalid:
        registry_blockers.append(BLOCKER_APPROVALS_MANIFEST_INVALID)
    if trusted_date_invalid:
        _extend_unique(
            registry_blockers,
            [trusted_date_reason or RESEARCH_ANCHOR_TRUSTED_DATE_INVALID],
        )
    if revocations_overlay_invalid:
        _extend_unique(registry_blockers, revocation_state["blockers"])

    registry_valid = (
        baseline_valid
        and not approvals_manifest_invalid
        and not cross_source_dupes
        and not within_approvals_dupes
        and not trusted_date_invalid
        and not revocations_overlay_invalid
    )

    counts = {
        "active": len(final_active),
        "expired": sum(1 for r in final_inactive if r.get("status") == STATUS_EXPIRED),
        "revoked": sum(1 for r in final_inactive if r.get("status") == STATUS_REVOKED),
        "invalid": sum(1 for r in final_inactive if r.get("status") == STATUS_INVALID),
        "superseded": 0,
        "baseline_active": len(baseline_active),
        "approved_active": approved_active_count,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "compiler_version": COMPILER_VERSION,
        "as_of_date": baseline.get("as_of_date"),
        "generated_at": generated_at,
        "source_manifest": _source_manifest(
            baseline=baseline,
            approvals_validation=approvals_validation,
            revocations_validation=revocations_validation,
        ),
        "active_anchors": final_active,
        "inactive_anchors": final_inactive,
        "counts": counts,
        "registry_valid": registry_valid,
        "registry_blockers": registry_blockers,
        "duplicate_blockers": duplicate_blockers,
        "revocations_applied": revocations_applied,
        "revocations_pending": revocations_pending,
        "revocation_problems": revocation_state["problems"],
        "audit_trail": audit_trail,
        # Explicit boundary markers: this is NOT the embedded registry.
        "is_embedded_registry": False,
        "embedded_in_evidence_packet": False,
        "consumed_by_support_signals": False,
        "consumed_by_active_registry": False,
        "consumed_by_availability": False,
        "consumed_by_gates": False,
        "consumed_by_step2": False,
        "consumed_by_step4": False,
        "cannot_affect_allowed_actions": True,
        "standalone_artifact_not_consumed_by_support_signals": True,
        "embedded_registry_selection_owned_by_evidence_packet": True,
        "notes": _NOTES,
    }


def compile_active_research_anchor_registry_with_approvals(
    *,
    anchors_path: Any,
    approvals_path: Any,
    allowed_universe: Any,
    today: Any = None,
    generated_at: str | None = None,
    candidate_index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile the baseline registry and merge recomputed approvals (never raises).

    Reuses ``compile_active_research_anchor_registry`` for the baseline (identical
    to what support_signals' embedded registry uses) and
    ``validate_research_anchor_approvals`` to RECOMPUTE approval validity from the
    YAML source (never reading the R2G-5a report artifact).
    """
    try:
        baseline = compile_active_research_anchor_registry(
            anchors_path=anchors_path,
            allowed_universe=allowed_universe,
            today=today,
            generated_at=generated_at,
        )
        return build_active_research_anchor_registry_with_approvals(
            baseline=baseline,
            approval_source_text=capture_research_anchor_approval_source(approvals_path),
            approval_source_path=str(approvals_path) if approvals_path is not None else None,
            allowed_universe=allowed_universe,
            today=today,
            generated_at=generated_at,
            candidate_index=candidate_index,
        )
    except Exception:  # noqa: BLE001 - report-only: never break the reporting flow
        return build_active_research_anchor_registry_with_approvals(
            baseline={"registry_valid": False, "active_anchors": [], "inactive_anchors": []},
            approval_source_text=None,
            approval_source_path=str(approvals_path) if approvals_path is not None else None,
            allowed_universe=allowed_universe,
            today=today,
            generated_at=generated_at,
        )


def write_active_research_anchor_registry_with_approvals(
    *,
    output_path: Any,
    anchors_path: Any,
    approvals_path: Any,
    allowed_universe: Any,
    today: Any = None,
    generated_at: str | None = None,
    candidate_index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile + write the report-only approvals-inclusive registry; small summary."""
    from investment_orchestrator.common.io import write_json

    registry = compile_active_research_anchor_registry_with_approvals(
        anchors_path=anchors_path,
        approvals_path=approvals_path,
        allowed_universe=allowed_universe,
        today=today,
        generated_at=generated_at,
        candidate_index=candidate_index,
    )
    write_json(output_path, registry)
    return {
        "active_research_anchor_registry_with_approvals_path": str(output_path),
        "registry_valid": str(registry["registry_valid"]),
        "active_anchor_count": str(registry["counts"]["active"]),
        "approved_active_count": str(registry["counts"]["approved_active"]),
    }


# --- approval row assembly ---------------------------------------------------


def _approval_eligible(ar: Mapping[str, Any], approvals_source_valid: bool) -> bool:
    """Independent recomputation of activation eligibility (NOT read from would_activate).

    Candidate fields are intentionally absent from this predicate — candidate data
    can never authorize or block activation.
    """
    return bool(
        approvals_source_valid
        and ar.get("decision") == DECISION_APPROVE
        and ar.get("hash_match") is True
        and ar.get("validation_valid") is True
        and ar.get("validation_usable") is True
        and ar.get("validation_stale") is not True
        and not _as_list(ar.get("approval_errors"))
    )


def _approval_validation(ar: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "valid": ar.get("validation_valid") is True,
        "stale": ar.get("validation_stale") is True,
        "usable": ar.get("validation_usable") is True,
        "hash_match": ar.get("hash_match") is True,
        "problems": [p for p in _as_list(ar.get("approval_errors")) if isinstance(p, str)],
    }


def _approval_anchor_identity(ar: Mapping[str, Any], preview: Mapping[str, Any]) -> dict[str, Any]:
    """Build the intrinsic identity + provenance row for an approved anchor.

    ``content_sha256`` covers the identity/provenance fields only (not the
    run-dependent validation / candidate_link_status), so it is stable across runs
    while the approved anchor definition is unchanged.
    """
    candidate_id = _string_of(ar.get("candidate_id"))
    candidate_sha256 = _string_of(ar.get("candidate_sha256"))
    approval_type = APPROVAL_TYPE_APPROVED_CANDIDATE if candidate_id else APPROVAL_TYPE_AUTHORED
    identity = {
        "anchor_id": preview.get("anchor_id"),
        "anchor_type": preview.get("anchor_type"),
        "applicable_tickers": list(preview.get("applicable_tickers") or []),
        "anchor_date_et": preview.get("anchor_date_et"),
        "valid_from": preview.get("valid_from"),
        "valid_until": preview.get("valid_until"),
        "confidence_floor": preview.get("confidence_floor"),
        "blocks_if_stale": True,
        "summary": preview.get("summary"),
        # Intrinsic source_type stays whatever the validator normalized ("operator"
        # for any valid anchor) — no new source_type is introduced.
        "source_type": preview.get("source_type"),
        "source_id": APPROVALS_SOURCE_ID,
        "source_category": OPERATOR_SOURCE_CATEGORY,
        "approval_type": approval_type,
        "approval_id": _string_of(ar.get("approval_id")),
        "candidate_id": candidate_id,
        "candidate_sha256": candidate_sha256,
        "operator_completed_anchor_sha256": _string_of(ar.get("operator_completed_anchor_sha256")),
    }
    content_sha256 = _sha256_of(identity)
    # Audit-only fields live outside the content hash.
    return {**identity, "candidate_link_status": ar.get("candidate_link_status"), "content_sha256": content_sha256}


def _approval_inactive_no_anchor(ar: Mapping[str, Any], validation: Mapping[str, Any]) -> dict[str, Any]:
    """An approval with no usable completed anchor (bad decision / missing anchor)."""
    problems = validation.get("problems") or []
    return {
        "anchor_id": None,
        "approval_id": _string_of(ar.get("approval_id")),
        "source_id": APPROVALS_SOURCE_ID,
        "source_category": OPERATOR_SOURCE_CATEGORY,
        "candidate_id": _string_of(ar.get("candidate_id")),
        "candidate_sha256": _string_of(ar.get("candidate_sha256")),
        "candidate_link_status": ar.get("candidate_link_status"),
        "operator_completed_anchor_sha256": _string_of(ar.get("operator_completed_anchor_sha256")),
        "status": STATUS_INVALID,
        "reason": "; ".join(problems) or "approval carries no activatable operator_completed_anchor.",
        "validation": dict(validation),
    }


def _revocation_validation_problems(
    revocations_validation: Mapping[str, Any],
    revocation_results: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    blockers = [b for b in _as_list(revocations_validation.get("blockers")) if isinstance(b, str)]
    if blockers:
        problems.append(
            {
                "reason": "revocation_validation_blockers",
                "blockers": blockers,
                "source_valid": revocations_validation.get("source_valid") is True,
                "revocations_valid": revocations_validation.get("revocations_valid") is True,
            }
        )
    for result in revocation_results:
        errors = [e for e in _as_list(result.get("errors")) if isinstance(e, str)]
        if result.get("status") == REVOCATION_STATUS_VALID_PENDING_FUTURE:
            continue
        if not errors:
            continue
        problems.append(
            {
                "reason": "revocation_rejected",
                "revocation_id": result.get("revocation_id"),
                "approval_id": result.get("approval_id"),
                "anchor_id": result.get("anchor_id"),
                "target_binding_status": result.get("target_binding_status"),
                "errors": errors,
            }
        )
    return problems


def _revoked_anchor_row(
    identity: Mapping[str, Any],
    validation: Mapping[str, Any],
    revocation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        **identity,
        "status": STATUS_REVOKED,
        "reason": _string_of(revocation.get("reason")) or "approval-derived anchor revoked.",
        "revocation_id": _string_of(revocation.get("revocation_id")),
        "effective_as_of": _string_of(revocation.get("effective_as_of")),
        "revoked_by": _string_of(revocation.get("revoked_by")),
        "validation": dict(validation),
        "revocation": _revocation_audit_row(revocation),
    }


def _revocation_audit_row(revocation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "revocation_id": _string_of(revocation.get("revocation_id")),
        "approval_id": _string_of(revocation.get("approval_id")),
        "anchor_id": _string_of(revocation.get("anchor_id")),
        "operator_completed_anchor_sha256": _string_of(
            revocation.get("operator_completed_anchor_sha256")
        ),
        "effective_as_of": _string_of(revocation.get("effective_as_of")),
        "reason": _string_of(revocation.get("reason")),
        "target_type": _string_of(revocation.get("target_type")),
    }


def _revocation_target_key(value: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    return (
        _string_of(value.get("approval_id")),
        _string_of(value.get("anchor_id")),
        _string_of(value.get("operator_completed_anchor_sha256")),
    )


def _inactive_status_reason(
    *,
    validation: Mapping[str, Any],
    aid: str,
    cross_source_dupes: set[str],
    within_approvals_dupes: set[str],
    approvals_manifest_invalid: bool,
    baseline_valid: bool,
    trusted_date_invalid: bool,
    trusted_date_reason: str | None,
    revocations_overlay_invalid: bool,
) -> tuple[str, str]:
    if aid in cross_source_dupes:
        return STATUS_INVALID, "duplicate_anchor_id_across_sources; excluded (no silent precedence)."
    if aid in within_approvals_dupes:
        return STATUS_INVALID, "duplicate_anchor_id_within_approvals; excluded (fail closed)."
    if approvals_manifest_invalid:
        return STATUS_INVALID, "approvals manifest invalid; approval not trusted (fail closed)."
    if trusted_date_invalid:
        return STATUS_INVALID, trusted_date_reason or RESEARCH_ANCHOR_TRUSTED_DATE_INVALID
    if revocations_overlay_invalid:
        return STATUS_INVALID, "revocation manifest invalid; approvals overlay not trusted (fail closed)."
    if not baseline_valid:
        return STATUS_INVALID, "baseline registry invalid; approvals not merged (fail closed)."
    if validation.get("valid") and validation.get("stale"):
        return STATUS_EXPIRED, "operator_completed_anchor is stale/expired; not active."
    problems = validation.get("problems") or []
    return STATUS_INVALID, "; ".join(problems) or "approval not activatable."


# --- source manifest ---------------------------------------------------------


def _source_manifest(
    *,
    baseline: Mapping[str, Any],
    approvals_validation: Mapping[str, Any],
    revocations_validation: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    manifest = [_baseline_source_entry(baseline), _approvals_source_entry(approvals_validation)]
    if isinstance(revocations_validation, Mapping):
        manifest.append(_revocations_source_entry(revocations_validation))
    return manifest


def _baseline_source_entry(baseline: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _as_list(baseline.get("source_manifest"))
    for entry in manifest:
        if isinstance(entry, Mapping) and entry.get("source_id") == BASELINE_SOURCE_ID:
            return dict(entry)
    return {
        "source_id": BASELINE_SOURCE_ID,
        "source_category": OPERATOR_SOURCE_CATEGORY,
        "source_type": "operator",
        "path": None,
        "sha256": None,
        "present": False,
        "valid": False,
        "problems": ["baseline_source_manifest_missing"],
    }


def _approvals_source_entry(approvals_validation: Mapping[str, Any]) -> dict[str, Any]:
    problems = [p for p in _as_list(approvals_validation.get("manifest_errors")) if isinstance(p, str)]
    problems += [w for w in _as_list(approvals_validation.get("manifest_warnings")) if isinstance(w, str)]
    return {
        "source_id": APPROVALS_SOURCE_ID,
        "source_category": OPERATOR_SOURCE_CATEGORY,
        "source_type": "operator",
        "path": approvals_validation.get("source_path"),
        "sha256": approvals_validation.get("source_sha256"),
        "present": approvals_validation.get("source_present") is True,
        "valid": approvals_validation.get("source_valid") is True,
        "problems": problems,
    }


def _revocations_source_entry(revocations_validation: Mapping[str, Any]) -> dict[str, Any]:
    problems = [p for p in _as_list(revocations_validation.get("blockers")) if isinstance(p, str)]
    problems += [w for w in _as_list(revocations_validation.get("warnings")) if isinstance(w, str)]
    return {
        "source_id": REVOCATIONS_SOURCE_ID,
        "source_category": OPERATOR_SOURCE_CATEGORY,
        "source_type": "operator",
        "path": revocations_validation.get("source_path"),
        "sha256": revocations_validation.get("source_sha256"),
        "present": revocations_validation.get("source_present") is True,
        "valid": (
            revocations_validation.get("source_valid") is True
            and revocations_validation.get("revocations_valid") is True
        ),
        "problems": problems,
    }


# --- helpers -----------------------------------------------------------------


def _trusted_activation_date(today: Any) -> tuple[str | None, str | None]:
    """Normalize the code-owned activation boundary without any fallback."""
    if today is None:
        return None, RESEARCH_ANCHOR_TRUSTED_DATE_MISSING
    normalized = normalize_iso_date_value(today)
    if normalized is None:
        return None, RESEARCH_ANCHOR_TRUSTED_DATE_INVALID
    return normalized, None


def _captured_source_invariant_error(
    source: Any,
    *,
    allow_blank_present: bool = False,
) -> str | None:
    """Snapshot public fields once, then validate only the code-owned copy."""
    _, reason = _snapshot_captured_source_once(
        source,
        allow_blank_present=allow_blank_present,
    )
    return reason


def _snapshot_captured_source_once(
    source: Any,
    *,
    allow_blank_present: bool = False,
) -> tuple[_ValidatedCapturedResearchAnchorApprovalSource, str | None]:
    """Read each public field exactly once and return an immutable local value."""
    if type(source) is not CapturedResearchAnchorApprovalSource:
        return _invalid_captured_source_snapshot(), CAPTURE_INVALID
    try:
        (
            source_state,
            source_path,
            source_bytes,
            source_text,
            source_sha256,
            read_error,
        ) = (
            source.source_state,
            source.source_path,
            source.source_bytes,
            source.source_text,
            source.source_sha256,
            source.read_error,
        )
    except Exception:  # noqa: BLE001 - an attribute read cannot become authority
        return _invalid_captured_source_snapshot(), CAPTURE_INVALID

    snapshot = _ValidatedCapturedResearchAnchorApprovalSource(
        source_state=source_state,
        source_path=source_path,
        source_bytes=source_bytes,
        source_text=source_text,
        source_sha256=source_sha256,
        read_error=read_error,
    )
    reason = _validated_captured_source_invariant_error(
        snapshot,
        allow_blank_present=allow_blank_present,
    )
    if reason is not None:
        safe_path = source_path if type(source_path) is str else None
        return _invalid_captured_source_snapshot(safe_path), reason
    return snapshot, None


def _validated_captured_source_invariant_error(
    source: _ValidatedCapturedResearchAnchorApprovalSource,
    *,
    allow_blank_present: bool = False,
) -> str | None:
    """Validate only a private, one-read projection of the public object."""
    if type(source) is not _ValidatedCapturedResearchAnchorApprovalSource:
        return CAPTURE_INVALID
    if type(source.source_state) is not ApprovalSourceState:
        return CAPTURE_INVALID
    if source.source_path is not None and type(source.source_path) is not str:
        return CAPTURE_INVALID

    if source.source_state is ApprovalSourceState.ABSENT:
        if (
            type(source.source_bytes) is not bytes
            or source.source_bytes != b""
            or type(source.source_text) is not str
            or source.source_text != ""
            or source.source_sha256 is not None
            or source.read_error is not None
        ):
            return CAPTURE_INVALID
        return None

    if source.source_state is ApprovalSourceState.READ_ERROR:
        if (
            type(source.source_bytes) is not bytes
            or source.source_bytes != b""
            or type(source.source_text) is not str
            or source.source_text != ""
            or source.source_sha256 is not None
            or type(source.read_error) is not str
            or source.read_error not in _CODE_OWNED_CAPTURE_ERRORS
        ):
            return CAPTURE_INVALID
        return None

    if source.source_state is not ApprovalSourceState.PRESENT:
        return CAPTURE_INVALID
    if (
        source.read_error is not None
        or type(source.source_bytes) is not bytes
        or type(source.source_text) is not str
        or type(source.source_sha256) is not str
        or (not allow_blank_present and not source.source_bytes)
    ):
        return CAPTURE_INVALID
    try:
        decoded = source.source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return CAPTURE_INVALID
    if decoded != source.source_text:
        return CAPTURE_INVALID
    if hashlib.sha256(source.source_bytes).hexdigest() != source.source_sha256:
        return CAPTURE_INVALID
    if not allow_blank_present and not source.source_text.strip():
        return CAPTURE_INVALID
    return None


def _invalid_captured_source_snapshot(
    source_path: str | None = None,
) -> _ValidatedCapturedResearchAnchorApprovalSource:
    """Create the sole internal representation of a rejected public capture."""
    return _ValidatedCapturedResearchAnchorApprovalSource(
        source_state=ApprovalSourceState.READ_ERROR,
        source_path=source_path,
        source_bytes=b"",
        source_text="",
        source_sha256=None,
        read_error=CAPTURE_INVALID,
    )


def _sanitize_captured_source(
    source: Any,
) -> _ValidatedCapturedResearchAnchorApprovalSource:
    """Return a private snapshot detached from the caller-controlled object."""
    # Preserve a verified PRESENT identity for a blank existing file so reporting
    # can distinguish it from absence. ``_decode_approval_source`` still rejects
    # the blank payload before YAML construction or activation.
    snapshot, _ = _snapshot_captured_source_once(
        source,
        allow_blank_present=True,
    )
    return snapshot


def _validated_or_invalid_sanitized_source(
    source: Any,
) -> _ValidatedCapturedResearchAnchorApprovalSource:
    """Return an invariant-checked private snapshot without touching public input.

    Internal workflow paths may share this exact frozen value across derivations.
    Rechecking its primitive invariant is cheap defense in depth and never
    re-enters the caller-controlled public capture boundary.
    """
    if (
        type(source) is not _ValidatedCapturedResearchAnchorApprovalSource
        or _validated_captured_source_invariant_error(
            source,
            allow_blank_present=True,
        )
        is not None
    ):
        return _invalid_captured_source_snapshot()
    return source


def _verified_approval_source_sha256(
    source: Any,
) -> str | None:
    """Return only the identity verified by the private snapshot invariant."""
    snapshot = _validated_or_invalid_sanitized_source(source)
    return (
        snapshot.source_sha256
        if snapshot.source_state is ApprovalSourceState.PRESENT
        else None
    )


def _verified_approval_source_present(source: Any) -> bool:
    """Report PRESENT only for a fully invariant-checked private snapshot."""
    snapshot = _validated_or_invalid_sanitized_source(source)
    return snapshot.source_state is ApprovalSourceState.PRESENT


def _verified_approval_source_validation_present(source: Any) -> bool:
    """Match validation-artifact presence semantics (read errors are present)."""
    snapshot = _validated_or_invalid_sanitized_source(source)
    return snapshot.source_state is not ApprovalSourceState.ABSENT


def _verified_approval_source_summary(
    source: Any,
) -> tuple[str, str | None, bool]:
    """Return reporting fields from the private snapshot, never public input."""
    snapshot = _validated_or_invalid_sanitized_source(source)
    if snapshot.source_state is ApprovalSourceState.ABSENT:
        return ApprovalSourceState.ABSENT.value, None, False
    if snapshot.source_state is ApprovalSourceState.PRESENT:
        return ApprovalSourceState.PRESENT.value, snapshot.source_sha256, False
    return ApprovalSourceState.READ_ERROR.value, None, True


def _decode_approval_source(
    source: _ValidatedCapturedResearchAnchorApprovalSource,
) -> dict[str, Any]:
    """Decode only bytes from the validated private snapshot."""
    if (
        _validated_captured_source_invariant_error(
            source,
            allow_blank_present=True,
        )
        is not None
    ):
        return {
            "manifest": None,
            "present": True,
            "sha256": None,
            "path": None,
            "parse_error": CAPTURE_INVALID,
        }
    if source.source_state is ApprovalSourceState.ABSENT:
        return {
            "manifest": None,
            "present": False,
            "sha256": None,
            "path": source.source_path,
            "parse_error": None,
        }
    if source.source_state is ApprovalSourceState.READ_ERROR:
        return {
            "manifest": None,
            "present": True,
            "sha256": None,
            "path": source.source_path,
            "parse_error": source.read_error,
        }
    if not source.source_text.strip():
        return {
            "manifest": None,
            "present": True,
            "sha256": source.source_sha256,
            "path": source.source_path,
            "parse_error": CAPTURE_INVALID,
        }
    try:
        parser_text = source.source_bytes.decode("utf-8")
        manifest = load_research_anchor_approval_yaml(parser_text)
    except ResearchAnchorApprovalYamlPolicyError as exc:
        return {
            "manifest": None,
            "present": True,
            "sha256": source.source_sha256,
            "path": source.source_path,
            "parse_error": exc.reason,
        }
    except yaml.constructor.ConstructorError:
        return {
            "manifest": None,
            "present": True,
            "sha256": source.source_sha256,
            "path": source.source_path,
            "parse_error": "approval_source_yaml_duplicate_key",
        }
    except yaml.YAMLError:
        return {
            "manifest": None,
            "present": True,
            "sha256": source.source_sha256,
            "path": source.source_path,
            "parse_error": "approval_source_yaml_invalid",
        }
    return {
        "manifest": manifest,
        "present": True,
        "sha256": source.source_sha256,
        "path": source.source_path,
        "parse_error": None,
    }


def capture_research_anchor_approval_source_text(
    source_text: Any,
    *,
    source_path: str | None,
) -> CapturedResearchAnchorApprovalSource:
    """Wrap caller-captured raw text in the same closed snapshot contract."""
    if source_text is None:
        return CapturedResearchAnchorApprovalSource(
            source_state=ApprovalSourceState.ABSENT,
            source_path=source_path,
            source_bytes=b"",
            source_text="",
            source_sha256=None,
            read_error=None,
        )
    if not isinstance(source_text, str):
        return CapturedResearchAnchorApprovalSource(
            source_state=ApprovalSourceState.READ_ERROR,
            source_path=source_path,
            source_bytes=b"",
            source_text="",
            source_sha256=None,
            read_error=_APPROVAL_SOURCE_TEXT_INVALID,
        )
    source_bytes = source_text.encode("utf-8")
    return CapturedResearchAnchorApprovalSource(
        source_state=ApprovalSourceState.PRESENT,
        source_path=source_path,
        source_bytes=source_bytes,
        source_text=source_text,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        read_error=None,
    )


def capture_research_anchor_approval_source(
    path: Any,
) -> CapturedResearchAnchorApprovalSource:
    """Capture the optional combined approval/revocation source exactly once.

    Only a genuinely absent file represents the fixed no-source policy. Other
    path or read failures propagate to the caller's fail-closed boundary instead
    of being misclassified as an empty source.
    """
    source_path = str(path) if path is not None else None
    if path is None:
        return capture_research_anchor_approval_source_text(
            None,
            source_path=source_path,
        )
    try:
        source_text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return capture_research_anchor_approval_source_text(
            None,
            source_path=source_path,
        )
    except UnicodeDecodeError:
        return CapturedResearchAnchorApprovalSource(
            source_state=ApprovalSourceState.READ_ERROR,
            source_path=source_path,
            source_bytes=b"",
            source_text="",
            source_sha256=None,
            read_error=_APPROVAL_SOURCE_UTF8_DECODE_ERROR,
        )
    except OSError:
        return CapturedResearchAnchorApprovalSource(
            source_state=ApprovalSourceState.READ_ERROR,
            source_path=source_path,
            source_bytes=b"",
            source_text="",
            source_sha256=None,
            read_error=_APPROVAL_SOURCE_READ_ERROR,
        )
    # ``Path.read_text`` performs the repository's established universal-newline
    # normalization. These encoded bytes are the exact immutable parser input,
    # so hashing, YAML parsing, validation, and activation all share one identity.
    source_bytes = source_text.encode("utf-8")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    return CapturedResearchAnchorApprovalSource(
        source_state=ApprovalSourceState.PRESENT,
        source_path=source_path,
        source_bytes=source_bytes,
        source_text=source_text,
        source_sha256=source_sha256,
        read_error=None,
    )


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _duplicate_strings(values: Any) -> set[str]:
    counts = Counter(v for v in values if isinstance(v, str) and v)
    return {value for value, count in counts.items() if count > 1}


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _target_sort_key(key: tuple[str | None, str | None, str | None]) -> tuple[str, str, str]:
    return tuple(part or "" for part in key)


def _string_of(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
