"""R2G-5b: report-only dual-read diff of baseline vs approvals-inclusive registry.

Compares the two report-only registries side by side so the embedded registry
switch can be audited without making this standalone diff an authority source:

* **baseline**: the current ``research_anchors.yaml``-only active registry (the
  one support_signals' embedded registry is built from).
* **approvals-inclusive**: baseline plus validated operator-approved anchors
  (``active_research_anchor_registry_with_approvals``).

Because R2G-5b intentionally overlays approved anchors on the SEPARATE
approvals-inclusive registry, ``added_by_approvals`` may be non-empty — that is
expected and by design. This standalone diff changes NOTHING at runtime:
support_signals consumes the registry embedded in ``evidence_packet`` by the
R2G-5c-2 readiness gate, and this diff is consumed by nothing
(``standalone_artifact_not_consumed_by_support_signals: true``,
``permission_effect: "none"``, ``consumed_by_*: false``,
``cannot_affect_allowed_actions: true``).
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "approval_registry_dual_read_diff_v1"

_NOTES = (
    "Report-only dual-read diff (R2G-5b). Compares the baseline research_anchors-only active "
    "registry against the SEPARATE approvals-inclusive registry. added_by_approvals may be "
    "non-empty by design and changes NOTHING at runtime by itself: support_signals consumes "
    "evidence_packet.active_anchor_registry, whose embedded selection is owned by the R2G-5c-2 "
    "readiness-gated evidence-packet builder. This standalone diff is consumed by NOTHING "
    "(support_signals, active registry, availability, gates, Step 2/3/4, final gate, weekly, "
    "broker/live all ignore it). It never authorizes a trade and "
    "adds no NEW_BUY / ORDER_COMPILATION (permission_effect=none, not_authorization=true). "
    "It is diagnostic only."
)


def build_approval_registry_dual_read_diff(
    *,
    baseline_registry: Mapping[str, Any],
    approvals_registry: Mapping[str, Any],
    baseline_registry_path: str | None = None,
    approvals_registry_path: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Diff two report-only registries into the deterministic diff artifact (never raises)."""
    try:
        return _build(
            baseline_registry=baseline_registry,
            approvals_registry=approvals_registry,
            baseline_registry_path=baseline_registry_path,
            approvals_registry_path=approvals_registry_path,
            generated_at=generated_at,
        )
    except Exception:  # noqa: BLE001 - report-only diff must never raise
        return _result(
            baseline_registry_path=baseline_registry_path,
            approvals_registry_path=approvals_registry_path,
            baseline_registry_sha256=None,
            approvals_registry_sha256=None,
            as_of_date=None,
            baseline_active_ids=[],
            approvals_active_ids=[],
            added=[],
            removed=[],
            changed=[],
            duplicate_blockers=[],
            registry_valid_baseline=False,
            registry_valid_with_approvals=False,
            blockers=["dual_read_diff_internal_error"],
            warnings=[],
            generated_at=generated_at,
        )


def _build(
    *,
    baseline_registry: Mapping[str, Any],
    approvals_registry: Mapping[str, Any],
    baseline_registry_path: str | None,
    approvals_registry_path: str | None,
    generated_at: str | None,
) -> dict[str, Any]:
    baseline_active = [r for r in _as_list(baseline_registry.get("active_anchors")) if isinstance(r, Mapping)]
    approvals_active = [r for r in _as_list(approvals_registry.get("active_anchors")) if isinstance(r, Mapping)]

    baseline_by_id = {r.get("anchor_id"): r for r in baseline_active if _is_id(r.get("anchor_id"))}
    approvals_by_id = {r.get("anchor_id"): r for r in approvals_active if _is_id(r.get("anchor_id"))}
    baseline_ids = set(baseline_by_id)
    approvals_ids = set(approvals_by_id)

    added = sorted(approvals_ids - baseline_ids)
    removed = sorted(baseline_ids - approvals_ids)

    changed: list[dict[str, Any]] = []
    for aid in sorted(baseline_ids & approvals_ids):
        b, a = baseline_by_id[aid], approvals_by_id[aid]
        if b.get("content_sha256") != a.get("content_sha256"):
            changed.append(
                {
                    "anchor_id": aid,
                    "baseline_content_sha256": b.get("content_sha256"),
                    "approvals_content_sha256": a.get("content_sha256"),
                    "baseline_source_id": b.get("source_id"),
                    "approvals_source_id": a.get("source_id"),
                }
            )

    duplicate_blockers = [d for d in _as_list(approvals_registry.get("duplicate_blockers")) if isinstance(d, Mapping)]
    registry_blockers = [b for b in _as_list(approvals_registry.get("registry_blockers")) if isinstance(b, str)]

    warnings: list[str] = []
    if approvals_registry.get("registry_valid") is not True:
        warnings.append(
            "approvals-inclusive registry is not valid (see blockers); would NOT be switch-ready."
        )
    if added:
        warnings.append(
            "added_by_approvals is non-empty: this is expected in R2G-5b and this standalone "
            "diff affects NOTHING at runtime; support_signals consumes evidence_packet."
            "active_anchor_registry selected by the R2G-5c-2 evidence-packet gate."
        )

    as_of_date = approvals_registry.get("as_of_date") or baseline_registry.get("as_of_date")

    return _result(
        baseline_registry_path=baseline_registry_path,
        approvals_registry_path=approvals_registry_path,
        baseline_registry_sha256=_sha256_of(baseline_registry),
        approvals_registry_sha256=_sha256_of(approvals_registry),
        as_of_date=as_of_date if isinstance(as_of_date, str) else None,
        baseline_active_ids=sorted(baseline_ids),
        approvals_active_ids=sorted(approvals_ids),
        added=added,
        removed=removed,
        changed=changed,
        duplicate_blockers=duplicate_blockers,
        registry_valid_baseline=baseline_registry.get("registry_valid") is True,
        registry_valid_with_approvals=approvals_registry.get("registry_valid") is True,
        blockers=registry_blockers,
        warnings=warnings,
        generated_at=generated_at,
    )


def write_approval_registry_dual_read_diff(
    *,
    output_path: Any,
    baseline_registry: Mapping[str, Any],
    approvals_registry: Mapping[str, Any],
    baseline_registry_path: str | None = None,
    approvals_registry_path: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build + write the report-only dual-read diff; return a small summary."""
    from investment_orchestrator.common.io import write_json

    diff = build_approval_registry_dual_read_diff(
        baseline_registry=baseline_registry,
        approvals_registry=approvals_registry,
        baseline_registry_path=baseline_registry_path,
        approvals_registry_path=approvals_registry_path,
        generated_at=generated_at,
    )
    write_json(output_path, diff)
    return {
        "approval_registry_dual_read_diff_path": str(output_path),
        "added_by_approvals_count": str(len(diff["added_by_approvals"])),
        "registry_valid_with_approvals": str(diff["registry_valid_with_approvals"]),
    }


# --- result assembly ---------------------------------------------------------


def _result(
    *,
    baseline_registry_path: str | None,
    approvals_registry_path: str | None,
    baseline_registry_sha256: str | None,
    approvals_registry_sha256: str | None,
    as_of_date: str | None,
    baseline_active_ids: list[str],
    approvals_active_ids: list[str],
    added: list[str],
    removed: list[str],
    changed: list[dict[str, Any]],
    duplicate_blockers: list[dict[str, Any]],
    registry_valid_baseline: bool,
    registry_valid_with_approvals: bool,
    blockers: list[str],
    warnings: list[str],
    generated_at: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "baseline_registry_path": baseline_registry_path,
        "approvals_registry_path": approvals_registry_path,
        "baseline_registry_sha256": baseline_registry_sha256,
        "approvals_registry_sha256": approvals_registry_sha256,
        "baseline_active_anchor_ids": baseline_active_ids,
        "approvals_inclusive_active_anchor_ids": approvals_active_ids,
        "added_by_approvals": added,
        "removed_or_deactivated": removed,
        "changed_existing_anchors": changed,
        "duplicate_blockers": duplicate_blockers,
        "registry_valid_baseline": registry_valid_baseline,
        "registry_valid_with_approvals": registry_valid_with_approvals,
        "no_behavior_change": True,
        "standalone_artifact_not_consumed_by_support_signals": True,
        "embedded_registry_selection_owned_by_evidence_packet": True,
        "consumed_by_support_signals": False,
        "consumed_by_active_registry": False,
        "consumed_by_availability": False,
        "consumed_by_gates": False,
        "consumed_by_step2": False,
        "consumed_by_step4": False,
        "cannot_affect_allowed_actions": True,
        "blockers": blockers,
        "warnings": warnings,
        "notes": _NOTES,
    }


# --- helpers -----------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
