"""R2G-5c-1: report-only support_signals dual-ground DRY-RUN diff (inert).

Emits ``support_signals_dual_ground_diff.json`` — a deterministic, strictly
report-only comparison of ``support_signals`` grounding under two registries:

* **baseline** — the current embedded ``evidence_packet.active_anchor_registry``
  (exactly what ``support_signals`` consumes today), and
* **approvals-inclusive** — the freshly-compiled approvals-inclusive registry,
  consumed *subject to R2G-5c-0 readiness semantics* (approvals_inclusive /
  baseline_fallback / fail_closed_empty).

It is a DRY-RUN ONLY. It never changes ``support_signals`` runtime output, never
mutates the evidence packet, never switches the embedded registry, and is
consumed by nothing. Both results are produced by calling the REAL, unchanged
``support_signals.build_compiled_support_signals`` — so the real global blockers,
qualitative gates, and ``_evaluate_anchor_refs`` grounding path are exercised
verbatim.

The current production ``_registry_is_consumable`` only accepts the baseline
schema, so to model the FUTURE R2G-5c-2 acceptance of the approvals-inclusive
schema this module builds a **dry-run-only projection**: the approvals-inclusive
registry re-labelled to the baseline schema (preserving every consumability
marker + ``registry_valid``), with any active approvals-derived row that fails the
R2G-5c-2 per-row defense downgraded to a non-groundable status. This changes NO
production code; the real schema-acceptance change belongs to R2G-5c-2.

Bounded-broadening invariant: every signal accepted under approvals-inclusive
grounding that was NOT accepted under baseline must be explained by a valid
operator-approved anchor (``source_category:"C_operator"``, ``source_type:"operator"``,
allowed ``approval_type``, ``status:"active"``, ``validation.valid/usable/hash_match``
true, ``stale`` false, non-empty ``operator_completed_anchor_sha256``, in a
``registry_valid:true`` registry with no duplicate/registry blockers) — never by a
candidate source, ``candidate_sha256``, ``candidate_link_status``, or an
inactive/expired/hash-mismatched/duplicate-conflicted approval.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from investment_orchestrator.research.support_signals import (
    build_compiled_support_signals,
)
from investment_orchestrator.research.approval_registry_switch_readiness import (
    ALLOWED_APPROVAL_TYPES,
    APPROVALS_SOURCE_ID,
    BASELINE_REGISTRY_SCHEMA,
    BASELINE_SOURCE_ID,
    SWITCH_TARGET_APPROVALS,
    SWITCH_TARGET_BASELINE,
    SWITCH_TARGET_FAIL_CLOSED,
    evaluate_approval_registry_switch_readiness,
)


SCHEMA_VERSION = "support_signals_dual_ground_diff_v1"

EXPLANATION_OPERATOR_APPROVED = "operator_approved_anchor"
EXPLANATION_BASELINE_ACTIVE = "baseline_active_anchor"
EXPLANATION_UNEXPLAINED = "unexplained"

_NOTES = (
    "Report-only R2G-5c-1 dry-run. Compares support_signals grounding under the current baseline "
    "embedded registry vs the freshly-compiled approvals-inclusive registry (subject to R2G-5c-0 "
    "readiness). DRY-RUN ONLY: it does not change support_signals runtime output, does not mutate "
    "evidence_packet.active_anchor_registry, does not switch the embedded registry, and is consumed "
    "by NOTHING (support_signals, active registry, availability, gates, Step 2/3/4, final gate, "
    "weekly, broker/live all ignore it). Both results come from the REAL, unchanged "
    "build_compiled_support_signals. operator_completed_anchor_sha256 is the only activation-binding "
    "hash; candidate_sha256 / candidate_link_status are audit-only and ground nothing. It never "
    "authorizes a trade and adds no NEW_BUY / ORDER_COMPILATION (permission_effect=none, "
    "not_authorization=true). R2G-5c-2 is the future switch."
)

_ORDER_SHAPED_KEYS = frozenset(
    {
        "account", "quantity", "shares", "order_type", "tif", "time_in_force",
        "limit_price", "stop_price", "venue", "routing", "broker", "new_buy",
        "order_compilation", "budget", "allocation",
    }
)


def build_support_signals_dual_ground_diff(
    *,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    compilation_mode: str,
    approvals_registry: Mapping[str, Any] | None,
    dual_read_diff: Mapping[str, Any] | None,
    current_research_anchors_sha256: str | None,
    current_research_anchor_approvals_sha256: str | None,
    approvals_source_present: bool,
    as_of_date: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the report-only dual-ground dry-run diff (pure; never raises)."""
    try:
        return _build(
            evidence_packet=evidence_packet,
            analyst_memo=analyst_memo,
            compilation_mode=compilation_mode,
            approvals_registry=approvals_registry,
            dual_read_diff=dual_read_diff,
            current_research_anchors_sha256=current_research_anchors_sha256,
            current_research_anchor_approvals_sha256=current_research_anchor_approvals_sha256,
            approvals_source_present=approvals_source_present,
            as_of_date=as_of_date,
            generated_at=generated_at,
        )
    except Exception as exc:  # noqa: BLE001 - report-only dry-run must never raise
        return _result(
            readiness={},
            baseline_result={},
            approvals_result={},
            baseline_ids=[],
            approvals_ids=[],
            added=[],
            removed_or_changed=[],
            unchanged=[],
            bounded_ok=False,
            bounded_failures=[_internal_error_failure(exc)],
            explanations=[],
            approvals_registry=approvals_registry if isinstance(approvals_registry, Mapping) else None,
            as_of_date=as_of_date,
            generated_at=generated_at,
        )


def _build(
    *,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    compilation_mode: str,
    approvals_registry: Mapping[str, Any] | None,
    dual_read_diff: Mapping[str, Any] | None,
    current_research_anchors_sha256: str | None,
    current_research_anchor_approvals_sha256: str | None,
    approvals_source_present: bool,
    as_of_date: str | None,
    generated_at: str | None,
) -> dict[str, Any]:
    packet = evidence_packet if isinstance(evidence_packet, Mapping) else {}
    baseline_registry = packet.get("active_anchor_registry")

    readiness = evaluate_approval_registry_switch_readiness(
        baseline_registry=baseline_registry,
        approvals_registry=approvals_registry,
        dual_read_diff=dual_read_diff,
        current_research_anchors_sha256=current_research_anchors_sha256,
        current_research_anchor_approvals_sha256=current_research_anchor_approvals_sha256,
        approvals_source_present=approvals_source_present,
        as_of_date=as_of_date,
    )
    target = readiness.get("switch_target")

    # A. baseline result: the REAL builder on the packet exactly as today.
    baseline_result = build_compiled_support_signals(
        evidence_packet=packet, analyst_memo=analyst_memo, compilation_mode=compilation_mode
    )

    # B. approvals-inclusive result: driven by the readiness target.
    if target == SWITCH_TARGET_APPROVALS:
        projected = _dry_run_projection(approvals_registry)
        appr_packet = {**packet, "active_anchor_registry": projected}
        approvals_result = build_compiled_support_signals(
            evidence_packet=appr_packet, analyst_memo=analyst_memo, compilation_mode=compilation_mode
        )
    elif target == SWITCH_TARGET_BASELINE:
        # The future switch would fall back to baseline grounding.
        approvals_result = baseline_result
    else:  # fail_closed_empty -> zero usable anchors (baseline is not a safe fallback).
        appr_packet = {**packet, "active_anchor_registry": _fail_closed_empty_registry()}
        approvals_result = build_compiled_support_signals(
            evidence_packet=appr_packet, analyst_memo=analyst_memo, compilation_mode=compilation_mode
        )

    baseline_accepted = _accepted_by_ticker(baseline_result)
    approvals_accepted = _accepted_by_ticker(approvals_result)
    baseline_ids = sorted(baseline_accepted)
    approvals_ids = sorted(approvals_accepted)
    bset, aset = set(baseline_ids), set(approvals_ids)

    added = sorted(aset - bset)
    removed_or_changed = sorted(
        [t for t in (bset - aset)]
        + [
            t
            for t in (bset & aset)
            if baseline_accepted[t].get("anchor_id") != approvals_accepted[t].get("anchor_id")
        ]
    )
    unchanged = sorted(
        t
        for t in (bset & aset)
        if baseline_accepted[t].get("anchor_id") == approvals_accepted[t].get("anchor_id")
    )

    # Bounded-broadening: every ADDED signal must be explained by a valid
    # operator-approved anchor (or, defensively, an already-groundable baseline
    # anchor). Removals are always safe (stricter) and not constrained here.
    explanations: list[dict[str, Any]] = []
    bounded_failures: list[dict[str, Any]] = []
    active_by_id = _active_anchor_index(approvals_registry)
    for ticker in added:
        anchor_id = approvals_accepted[ticker].get("anchor_id")
        anchor = active_by_id.get(anchor_id) if isinstance(anchor_id, str) else None
        explanation = _explain(anchor, approvals_registry)
        explanations.append({"signal_id": ticker, "matched_anchor_id": anchor_id, **explanation})
        if not explanation["ok"]:
            bounded_failures.append(
                {"signal_id": ticker, "matched_anchor_id": anchor_id, "reason": explanation["explanation"]}
            )

    bounded_ok = not bounded_failures

    return _result(
        readiness=readiness,
        baseline_result=baseline_result,
        approvals_result=approvals_result,
        baseline_ids=baseline_ids,
        approvals_ids=approvals_ids,
        added=added,
        removed_or_changed=removed_or_changed,
        unchanged=unchanged,
        bounded_ok=bounded_ok,
        bounded_failures=bounded_failures,
        explanations=explanations,
        approvals_registry=approvals_registry if isinstance(approvals_registry, Mapping) else None,
        as_of_date=as_of_date if isinstance(as_of_date, str) else readiness.get("as_of_date"),
        generated_at=generated_at,
    )


# --- dry-run projection (models the FUTURE R2G-5c-2 consumer contract) --------


def _dry_run_projection(approvals_registry: Any) -> dict[str, Any]:
    """Re-label the approvals-inclusive registry so the CURRENT builder consumes it.

    Models R2G-5c-2's consumer changes WITHOUT touching production code:

    * schema re-labelled to the baseline schema so the current
      ``_registry_is_consumable`` accepts it (R2G-5c-2 will accept the new schema);
    * every consumability marker + ``registry_valid`` preserved from the real
      registry (so ``registry_valid:false`` still fails closed to zero anchors);
    * any active approvals-derived row that fails the R2G-5c-2 per-row defense is
      downgraded to a non-groundable ``invalid`` status.
    """
    if not _switch_consumable(approvals_registry):
        return _fail_closed_empty_registry()

    active_out: list[dict[str, Any]] = []
    inactive_out: list[dict[str, Any]] = [
        dict(r) for r in _as_list(approvals_registry.get("inactive_anchors")) if isinstance(r, Mapping)
    ]
    for row in _as_list(approvals_registry.get("active_anchors")):
        if not isinstance(row, Mapping):
            continue
        if _is_approvals_derived(row) and not _row_defense_ok(row):
            inactive_out.append({**row, "status": "invalid", "reason": "dry_run_row_defense_failed"})
        else:
            active_out.append(dict(row))

    return {
        "schema_version": BASELINE_REGISTRY_SCHEMA,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "registry_valid": True,
        "active_anchors": active_out,
        "inactive_anchors": inactive_out,
    }


def _fail_closed_empty_registry() -> dict[str, Any]:
    """A consumable-but-empty registry: zero groundable anchors (fail closed)."""
    return {
        "schema_version": BASELINE_REGISTRY_SCHEMA,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "registry_valid": True,
        "active_anchors": [],
        "inactive_anchors": [],
    }


def _switch_consumable(registry: Any) -> bool:
    """Whole-registry fail-closed gate (mirrors R2G-5c-2 _registry_is_consumable)."""
    return (
        isinstance(registry, Mapping)
        and registry.get("is_llm_generated") is False
        and registry.get("report_only") is True
        and registry.get("permission_effect") == "none"
        and registry.get("not_authorization") is True
        and registry.get("registry_valid") is True
        and not _as_list(registry.get("duplicate_blockers"))
        and not _as_list(registry.get("registry_blockers"))
    )


def _is_approvals_derived(row: Mapping[str, Any]) -> bool:
    # source_id is the reliable discriminator: BASELINE rows also carry
    # approval_type:"operator_authored", so approval_type must NOT be used here.
    return row.get("source_id") == APPROVALS_SOURCE_ID


def _row_defense_ok(row: Mapping[str, Any]) -> bool:
    """Per-row approvals defense (R2G-5c-2). candidate fields are never consulted."""
    return (
        row.get("source_category") == "C_operator"
        and row.get("source_type") == "operator"
        and row.get("approval_type") in ALLOWED_APPROVAL_TYPES
        and _nonempty(row.get("operator_completed_anchor_sha256"))
        and _validation_ok(row)
    )


# --- bounded-broadening explanation ------------------------------------------


def _explain(anchor: Mapping[str, Any] | None, approvals_registry: Any) -> dict[str, Any]:
    """Classify how an added accepted signal is grounded (never via candidate data)."""
    if not isinstance(anchor, Mapping):
        return {"explanation": EXPLANATION_UNEXPLAINED, "ok": False, "detail": "no matching active anchor"}

    if _is_approvals_derived(anchor):
        ok = _operator_approved_ok(anchor, approvals_registry)
        return {
            "explanation": EXPLANATION_OPERATOR_APPROVED if ok else EXPLANATION_UNEXPLAINED,
            "ok": ok,
            "approval_type": anchor.get("approval_type"),
            "operator_completed_anchor_sha256": anchor.get("operator_completed_anchor_sha256"),
            # Audit-only note; NEVER part of the ok decision.
            "candidate_link_status_audit_only": anchor.get("candidate_link_status"),
            "candidate_id_audit_only": anchor.get("candidate_id"),
        }

    if anchor.get("source_id") == BASELINE_SOURCE_ID:
        return {"explanation": EXPLANATION_BASELINE_ACTIVE, "ok": True, "detail": "already groundable baseline anchor"}

    return {"explanation": EXPLANATION_UNEXPLAINED, "ok": False, "detail": "unknown anchor source"}


def _operator_approved_ok(anchor: Mapping[str, Any], approvals_registry: Any) -> bool:
    """All operator-approval constraints; candidate_sha256 is NOT among them."""
    registry = approvals_registry if isinstance(approvals_registry, Mapping) else {}
    return (
        anchor.get("source_id") == APPROVALS_SOURCE_ID
        and anchor.get("source_category") == "C_operator"
        and anchor.get("source_type") == "operator"
        and anchor.get("approval_type") in ALLOWED_APPROVAL_TYPES
        and anchor.get("status") == "active"
        and _validation_ok(anchor)
        and _nonempty(anchor.get("operator_completed_anchor_sha256"))
        and registry.get("registry_valid") is True
        and not _as_list(registry.get("duplicate_blockers"))
        and not _as_list(registry.get("registry_blockers"))
    )


def _validation_ok(row: Mapping[str, Any]) -> bool:
    v = row.get("validation")
    return (
        isinstance(v, Mapping)
        and v.get("valid") is True
        and v.get("usable") is True
        and v.get("stale") is False
        and v.get("hash_match") is True
    )


# --- result assembly ---------------------------------------------------------


def _result(
    *,
    readiness: Mapping[str, Any],
    baseline_result: Mapping[str, Any],
    approvals_result: Mapping[str, Any],
    baseline_ids: list[str],
    approvals_ids: list[str],
    added: list[str],
    removed_or_changed: list[str],
    unchanged: list[str],
    bounded_ok: bool,
    bounded_failures: list[dict[str, Any]],
    explanations: list[dict[str, Any]],
    approvals_registry: Mapping[str, Any] | None,
    as_of_date: str | None,
    generated_at: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "dry_run_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "readiness_summary": {
            "ready": readiness.get("ready"),
            "switch_target": readiness.get("switch_target"),
            "baseline_fallback_safe": readiness.get("baseline_fallback_safe"),
            "fail_closed_empty_required": readiness.get("fail_closed_empty_required"),
            "failed_conditions": readiness.get("failed_conditions"),
        },
        "baseline_support_signals_sha256": _sha256_of(baseline_result),
        "approvals_inclusive_support_signals_sha256": _sha256_of(approvals_result),
        "baseline_accepted_signal_ids": baseline_ids,
        "approvals_inclusive_accepted_signal_ids": approvals_ids,
        "added_by_approvals": added,
        "removed_or_changed": removed_or_changed,
        "unchanged": unchanged,
        "bounded_broadening_passed": bounded_ok,
        "bounded_broadening_failures": bounded_failures,
        "explanations": explanations,
        "registry_valid_baseline": bool(readiness.get("registry_valid_baseline")),
        "registry_valid_with_approvals": bool(readiness.get("registry_valid_with_approvals")),
        "duplicate_blockers": _as_list(approvals_registry.get("duplicate_blockers"))
        if isinstance(approvals_registry, Mapping)
        else [],
        "registry_blockers": _as_list(approvals_registry.get("registry_blockers"))
        if isinstance(approvals_registry, Mapping)
        else [],
        "candidate_sources_used_for_grounding": False,
        "candidate_sha256_used_as_activation_authority": False,
        "support_signals_runtime_unchanged": True,
        "evidence_packet_runtime_unchanged": True,
        "consumed_by_support_signals": False,
        "consumed_by_active_registry": False,
        "consumed_by_availability": False,
        "consumed_by_gates": False,
        "consumed_by_step2": False,
        "consumed_by_step4": False,
        "cannot_affect_allowed_actions": True,
        "notes": _NOTES,
    }


# --- disk wrapper ------------------------------------------------------------


def write_support_signals_dual_ground_diff(
    *,
    output_path: Any,
    evidence_packet: Mapping[str, Any] | None,
    analyst_memo: Mapping[str, Any] | None,
    compilation_mode: str,
    anchors_path: Any,
    approvals_path: Any,
    allowed_universe: Any,
    today: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Recompute the approvals-inclusive registry + diff fresh, then build + write."""
    from investment_orchestrator.common.io import write_json
    from investment_orchestrator.research.approvals_inclusive_active_registry import (
        compile_active_research_anchor_registry_with_approvals,
    )
    from investment_orchestrator.research.approval_registry_dual_read_diff import (
        build_approval_registry_dual_read_diff,
    )

    approvals_registry = compile_active_research_anchor_registry_with_approvals(
        anchors_path=anchors_path,
        approvals_path=approvals_path,
        allowed_universe=allowed_universe,
        today=today,
    )
    packet = evidence_packet if isinstance(evidence_packet, Mapping) else {}
    baseline_registry = packet.get("active_anchor_registry")
    diff = build_approval_registry_dual_read_diff(
        baseline_registry=baseline_registry if isinstance(baseline_registry, Mapping) else {},
        approvals_registry=approvals_registry,
    )
    anchors_text = _read_text_or_none(anchors_path)
    approvals_text = _read_text_or_none(approvals_path)
    payload = build_support_signals_dual_ground_diff(
        evidence_packet=packet,
        analyst_memo=analyst_memo,
        compilation_mode=compilation_mode,
        approvals_registry=approvals_registry,
        dual_read_diff=diff,
        current_research_anchors_sha256=_sha256_of_text(anchors_text),
        current_research_anchor_approvals_sha256=_sha256_of_text(approvals_text),
        approvals_source_present=approvals_text is not None and approvals_text.strip() != "",
        as_of_date=today if isinstance(today, str) else None,
        generated_at=generated_at,
    )
    write_json(output_path, payload)
    return {
        "support_signals_dual_ground_diff_path": str(output_path),
        "switch_target": str(payload["readiness_summary"]["switch_target"]),
        "added_by_approvals_count": str(len(payload["added_by_approvals"])),
        "bounded_broadening_passed": str(payload["bounded_broadening_passed"]),
    }


# --- helpers -----------------------------------------------------------------


def _accepted_by_ticker(result: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    if not isinstance(result, Mapping):
        return out
    for sig in _as_list(result.get("accepted_support_signals")):
        if isinstance(sig, Mapping):
            ticker = sig.get("ticker")
            if isinstance(ticker, str) and ticker:
                out[ticker] = sig
    return out


def _active_anchor_index(registry: Any) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    if not isinstance(registry, Mapping):
        return out
    for row in _as_list(registry.get("active_anchors")):
        if isinstance(row, Mapping):
            anchor_id = row.get("anchor_id")
            if isinstance(anchor_id, str) and anchor_id:
                out[anchor_id] = row
    return out


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _sha256_of_text(value: str | None) -> str | None:
    if not isinstance(value, str) or value == "":
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_text_or_none(path: Any) -> str | None:
    from investment_orchestrator.common.io import file_exists, read_text

    if path is None or not file_exists(path):
        return None
    try:
        return read_text(path)
    except Exception:  # noqa: BLE001 - unreadable file treated as absent
        return None


def _internal_error_failure(exc: Exception) -> dict[str, str]:
    message = str(exc).strip()
    if len(message) > 200:
        message = message[:197] + "..."
    return {
        "failure_id": "dual_ground_diff_internal_error",
        "reason": "exception",
        "message": message,
    }
