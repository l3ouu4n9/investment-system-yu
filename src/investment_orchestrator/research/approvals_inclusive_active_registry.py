"""R2G-5b: report-only approvals-inclusive active research-anchor registry.

Builds a SEPARATE, report-only ``active_research_anchor_registry_with_approvals``
that overlays validated operator-approved anchors (from
``inputs/current/research_anchor_approvals.yaml``) on top of the exact baseline
registry compiled from ``research_anchors.yaml``. It is an OBSERVER only:

* It is **not** the embedded registry consumed by ``support_signals`` (that stays
  the baseline ``active_research_anchor_registry`` embedded in the evidence
  packet — untouched by this module).
* NOTHING consumes this artifact in R2G-5b: not ``support_signals``, not the
  compiler, not the actionable preview / candidate / promotion eligibility, not
  availability, not gates, not Step 2/3/4, not the final gate, not weekly, not
  broker/live. R2G-5c is the future behavior switch, after a post-audit.

Activation model (recomputed here — the R2G-5a validation artifact is NEVER read
as authority; approvals are re-validated directly from YAML via the R2G-5a
validator functions):

* ``operator_completed_anchor_sha256`` is the ONLY activation-binding hash. A
  mismatched / missing hash, or any mutation of ``operator_completed_anchor``,
  makes the approval inactive.
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
from collections import Counter
import hashlib
import json
from typing import Any

from investment_orchestrator.research.active_research_anchor_registry import (
    OPERATOR_SOURCE_CATEGORY,
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_INVALID,
    compile_active_research_anchor_registry,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    validate_research_anchor_approvals,
)


SCHEMA_VERSION = "active_research_anchor_registry_with_approvals_v1"
COMPILER_VERSION = "active_registry_with_approvals_compiler_v1"

APPROVALS_SOURCE_ID = "operator_research_anchor_approvals_yaml"
BASELINE_SOURCE_ID = "operator_research_anchors_yaml"

APPROVAL_TYPE_AUTHORED = "operator_authored"
APPROVAL_TYPE_APPROVED_CANDIDATE = "operator_approved_candidate"

DECISION_APPROVE = "approve"

# Registry-level blocker codes specific to the approvals overlay.
BLOCKER_APPROVALS_MANIFEST_INVALID = "approvals_manifest_invalid"
BLOCKER_DUPLICATE_ACROSS_SOURCES = "duplicate_anchor_id_across_sources"
BLOCKER_DUPLICATE_WITHIN_APPROVALS = "duplicate_anchor_id_within_approvals"

_NOTES = (
    "Report-only approvals-inclusive active anchor registry (R2G-5b). SEPARATE from the "
    "baseline active_research_anchor_registry that support_signals consumes (this artifact "
    "is NOT embedded in the evidence packet and is consumed by NOTHING: not support_signals, "
    "not the compiler, not the actionable preview / candidate / promotion eligibility, not "
    "availability, not gates, not Step 2/3/4, not the final gate, not weekly, not "
    "broker/live). Activation is recomputed directly from research_anchor_approvals.yaml; the "
    "R2G-5a validation artifact and its would_activate flag are NEVER read as authority. "
    "operator_completed_anchor_sha256 is the only activation-binding hash; candidate_sha256 / "
    "candidate_link_status are audit-only with zero activation authority. Cross-source "
    "duplicate anchor_id fails closed (no silent precedence). It never authorizes a trade and "
    "adds no NEW_BUY / ORDER_COMPILATION (permission_effect=none, not_authorization=true). "
    "R2G-5c is the future behavior switch, after a post-audit and readiness proof."
)


def build_active_research_anchor_registry_with_approvals(
    *,
    baseline: Mapping[str, Any],
    approvals_validation: Mapping[str, Any],
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Merge a baseline registry with recomputed operator approvals (pure; never raises).

    ``baseline`` is the output of ``active_research_anchor_registry`` (the exact
    same object support_signals' embedded registry is built from). ``approvals_validation``
    is the R2G-5a validation dict, RECOMPUTED from the approvals YAML (not read
    from the report artifact). This function never mutates ``baseline``.
    """
    try:
        return _build(baseline=baseline, approvals_validation=approvals_validation, generated_at=generated_at)
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
            "support_signals_still_consumes_baseline_registry": True,
            "notes": _NOTES,
        }


def _build(
    *,
    baseline: Mapping[str, Any],
    approvals_validation: Mapping[str, Any],
    generated_at: str | None,
) -> dict[str, Any]:
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

    audit_trail: list[dict[str, Any]] = []

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
        activate = eligible and baseline_valid and (aid not in conflicting)
        if activate:
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
        else:
            status, reason = _inactive_status_reason(
                validation=validation,
                aid=aid,
                cross_source_dupes=cross_source_dupes,
                within_approvals_dupes=within_approvals_dupes,
                approvals_manifest_invalid=approvals_manifest_invalid,
                baseline_valid=baseline_valid,
            )
            final_inactive.append({**identity, "status": status, "reason": reason, "validation": validation})
            audit_trail.append(
                {"event": "approval_anchor_rejected", "anchor_id": aid, "reason": reason}
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

    registry_valid = (
        baseline_valid
        and not approvals_manifest_invalid
        and not cross_source_dupes
        and not within_approvals_dupes
    )

    counts = {
        "active": len(final_active),
        "expired": sum(1 for r in final_inactive if r.get("status") == STATUS_EXPIRED),
        "revoked": 0,
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
        "source_manifest": [_baseline_source_entry(baseline), _approvals_source_entry(approvals_validation)],
        "active_anchors": final_active,
        "inactive_anchors": final_inactive,
        "counts": counts,
        "registry_valid": registry_valid,
        "registry_blockers": registry_blockers,
        "duplicate_blockers": duplicate_blockers,
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
        "support_signals_still_consumes_baseline_registry": True,
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
        approvals_validation = validate_research_anchor_approvals(
            manifest_path=approvals_path,
            allowed_universe=allowed_universe,
            today=today,
            candidate_index=candidate_index,
        )
        return build_active_research_anchor_registry_with_approvals(
            baseline=baseline, approvals_validation=approvals_validation, generated_at=generated_at
        )
    except Exception:  # noqa: BLE001 - report-only: never break the reporting flow
        return build_active_research_anchor_registry_with_approvals(
            baseline={"registry_valid": False, "active_anchors": [], "inactive_anchors": []},
            approvals_validation={"source_present": False, "source_valid": False, "approval_results": []},
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


def _inactive_status_reason(
    *,
    validation: Mapping[str, Any],
    aid: str,
    cross_source_dupes: set[str],
    within_approvals_dupes: set[str],
    approvals_manifest_invalid: bool,
    baseline_valid: bool,
) -> tuple[str, str]:
    if aid in cross_source_dupes:
        return STATUS_INVALID, "duplicate_anchor_id_across_sources; excluded (no silent precedence)."
    if aid in within_approvals_dupes:
        return STATUS_INVALID, "duplicate_anchor_id_within_approvals; excluded (fail closed)."
    if approvals_manifest_invalid:
        return STATUS_INVALID, "approvals manifest invalid; approval not trusted (fail closed)."
    if not baseline_valid:
        return STATUS_INVALID, "baseline registry invalid; approvals not merged (fail closed)."
    if validation.get("valid") and validation.get("stale"):
        return STATUS_EXPIRED, "operator_completed_anchor is stale/expired; not active."
    problems = validation.get("problems") or []
    return STATUS_INVALID, "; ".join(problems) or "approval not activatable."


# --- source manifest ---------------------------------------------------------


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


# --- helpers -----------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _duplicate_strings(values: Any) -> set[str]:
    counts = Counter(v for v in values if isinstance(v, str) and v)
    return {value for value, count in counts.items() if count > 1}


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
