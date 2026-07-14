"""R2G-6a: report-only grounding status observatory (pure, inert).

Builds a single diagnostic summary of already-loaded grounding artifacts. This
module is observatory-only: it does not read source files, recompute readiness,
select registries, validate manifests, write artifacts, or feed any consumer.

The artifact is permanently non-authoritative. Any future decision input must be
a separate deterministic artifact and contract, not this observatory output.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "grounding_status_observatory_v1"

_BASELINE_SOURCE_ID = "operator_research_anchors_yaml"
_APPROVALS_SOURCE_ID = "operator_research_anchor_approvals_yaml"
_REVOCATIONS_SOURCE_ID = "operator_research_anchor_revocations_yaml"

_SUPPORTED_SELECTED_REGISTRY_SCHEMAS = frozenset(
    {
        "active_research_anchor_registry_v1",
        "active_research_anchor_registry_with_approvals_v1",
    }
)
_SUPPORTED_READINESS_SCHEMA = "approval_registry_switch_readiness_v1"
_SUPPORTED_APPROVALS_SCHEMA = "research_anchor_approvals_validation_v1"
_SUPPORTED_REVOCATIONS_SCHEMA = "research_anchor_revocations_validation_v1"
_SUPPORTED_CANDIDATES_SCHEMA = "research_anchor_candidates_v1"
_SUPPORTED_SUPPORT_SIGNALS_SCHEMA = "compiled_support_signals_v1"

_NOTES = (
    "Report-only grounding status observatory (R2G-6a). Diagnostic summary only. "
    "Consumed by nothing and permanently not a registry-selection, permission, gate, "
    "budget, allocation, order, or execution input. It summarizes already-loaded "
    "artifacts defensively and never authorizes a trade."
)


def build_grounding_status_observatory(
    *,
    evidence_packet: Mapping[str, Any] | None = None,
    embedded_registry_selection: Mapping[str, Any] | None = None,
    readiness: Mapping[str, Any] | None = None,
    baseline_registry: Mapping[str, Any] | None = None,
    approvals_registry: Mapping[str, Any] | None = None,
    approvals_validation: Mapping[str, Any] | None = None,
    revocations_validation: Mapping[str, Any] | None = None,
    dual_read_diff: Mapping[str, Any] | None = None,
    support_signals_dual_ground_diff: Mapping[str, Any] | None = None,
    candidates: Mapping[str, Any] | None = None,
    support_signals: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Summarize already-loaded grounding artifacts (pure; never raises).

    Inputs are mappings, not file paths. The builder never reads files, never
    recomputes readiness, and never chooses a registry. Missing or malformed
    inputs become warnings and incomplete diagnostics.
    """
    try:
        return _build(
            evidence_packet=evidence_packet,
            embedded_registry_selection=embedded_registry_selection,
            readiness=readiness,
            baseline_registry=baseline_registry,
            approvals_registry=approvals_registry,
            approvals_validation=approvals_validation,
            revocations_validation=revocations_validation,
            dual_read_diff=dual_read_diff,
            support_signals_dual_ground_diff=support_signals_dual_ground_diff,
            candidates=candidates,
            support_signals=support_signals,
            generated_at=generated_at,
        )
    except Exception as exc:  # noqa: BLE001 - observatory must never raise
        return _result(
            generated_at=generated_at,
            as_of_date=None,
            selected_registry=_empty_selected_registry(),
            source_manifest_summary=[],
            baseline_summary=_empty_baseline_summary(),
            approvals_summary=_empty_approvals_summary(),
            revocations_summary=_empty_revocations_summary(),
            candidates_summary=_empty_candidates_summary(),
            support_grounding_summary=_empty_support_summary(),
            blockers=[],
            warnings=["grounding_status_observatory_internal_error"],
            diagnostics={
                "diagnostics_incomplete": True,
                "input_problems": [{"input": "builder", "problem": "internal_error", "detail": str(exc)}],
                "evidence_observer_registry_mismatch": None,
                "workflow_approval_source_identity_mismatch": None,
                "approval_source_sha256_values": [],
                "unknown_schema_sources": [],
                "partial_data": True,
            },
        )


def _build(
    *,
    evidence_packet: Mapping[str, Any] | None,
    embedded_registry_selection: Mapping[str, Any] | None,
    readiness: Mapping[str, Any] | None,
    baseline_registry: Mapping[str, Any] | None,
    approvals_registry: Mapping[str, Any] | None,
    approvals_validation: Mapping[str, Any] | None,
    revocations_validation: Mapping[str, Any] | None,
    dual_read_diff: Mapping[str, Any] | None,
    support_signals_dual_ground_diff: Mapping[str, Any] | None,
    candidates: Mapping[str, Any] | None,
    support_signals: Mapping[str, Any] | None,
    generated_at: str | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    input_problems: list[dict[str, Any]] = []
    unknown_schema_sources: list[dict[str, Any]] = []

    packet = _mapping_or_none(evidence_packet, "evidence_packet", input_problems)
    selection = _mapping_or_none(embedded_registry_selection, "embedded_registry_selection", input_problems)
    readiness_map = _mapping_or_none(readiness, "readiness", input_problems)
    base = _mapping_or_none(baseline_registry, "baseline_registry", input_problems)
    appr_reg = _mapping_or_none(approvals_registry, "approvals_registry", input_problems)
    approvals_val = _mapping_or_none(approvals_validation, "approvals_validation", input_problems)
    revocations_val = _mapping_or_none(revocations_validation, "revocations_validation", input_problems)
    dual_diff = dual_read_diff if isinstance(dual_read_diff, Mapping) else None
    dual_ground_diff = (
        support_signals_dual_ground_diff
        if isinstance(support_signals_dual_ground_diff, Mapping)
        else None
    )
    candidates_artifact = _mapping_or_none(candidates, "candidates", input_problems)
    support = _mapping_or_none(support_signals, "support_signals", input_problems)

    if packet is None:
        warnings.append("missing_or_malformed_evidence_packet")
    if selection is None:
        warnings.append("missing_or_malformed_embedded_registry_selection")
    if readiness_map is None and selection is not None:
        readiness_map = selection.get("readiness") if isinstance(selection.get("readiness"), Mapping) else None
    if readiness_map is None:
        warnings.append("missing_or_malformed_readiness")

    selected_registry_source = "evidence_packet.active_anchor_registry"
    selected_registry = (
        packet.get("active_anchor_registry")
        if isinstance(packet, Mapping) and isinstance(packet.get("active_anchor_registry"), Mapping)
        else None
    )
    if selected_registry is None and selection is not None and isinstance(selection.get("selected_registry"), Mapping):
        selected_registry = selection.get("selected_registry")
        selected_registry_source = "embedded_registry_selection.selected_registry"
    if selected_registry is None:
        warnings.append("missing_or_malformed_selected_registry")

    if base is None and selection is not None and isinstance(selection.get("baseline_registry"), Mapping):
        base = selection.get("baseline_registry")
    if appr_reg is None and selection is not None and isinstance(selection.get("approvals_registry"), Mapping):
        appr_reg = selection.get("approvals_registry")

    _check_schema(
        selected_registry,
        "selected_registry",
        _SUPPORTED_SELECTED_REGISTRY_SCHEMAS,
        unknown_schema_sources,
        input_problems,
    )
    _check_schema(readiness_map, "readiness", {_SUPPORTED_READINESS_SCHEMA}, unknown_schema_sources, input_problems)
    _check_schema(
        approvals_val,
        "approvals_validation",
        {_SUPPORTED_APPROVALS_SCHEMA},
        unknown_schema_sources,
        input_problems,
    )
    _check_schema(
        revocations_val,
        "revocations_validation",
        {_SUPPORTED_REVOCATIONS_SCHEMA},
        unknown_schema_sources,
        input_problems,
    )
    _check_schema(
        candidates_artifact,
        "candidates",
        {_SUPPORTED_CANDIDATES_SCHEMA},
        unknown_schema_sources,
        input_problems,
    )
    _check_schema(
        support,
        "support_signals",
        {_SUPPORTED_SUPPORT_SIGNALS_SCHEMA},
        unknown_schema_sources,
        input_problems,
    )

    if unknown_schema_sources:
        warnings.append("unknown_schema_source_unusable_for_diagnostics")
    if input_problems:
        warnings.append("missing_or_malformed_inputs_present")

    evidence_registry_sha = _sha256_of(selected_registry)
    observer_registry_sha = _sha256_of(appr_reg)
    evidence_observer_mismatch = (
        evidence_registry_sha is not None
        and observer_registry_sha is not None
        and evidence_registry_sha != observer_registry_sha
        and _selected_source(selection, readiness_map) == "approvals_inclusive"
    )
    if evidence_observer_mismatch:
        warnings.append("evidence_packet_registry_mismatch_observer_registry")

    approval_source_hashes = {
        value
        for value in (
            _str_or_none(_get(approvals_val, "source_sha256")),
            _str_or_none(_get(revocations_val, "source_sha256")),
            _str_or_none(_source_entry(appr_reg, _APPROVALS_SOURCE_ID).get("sha256")),
            _str_or_none(_source_entry(appr_reg, _REVOCATIONS_SOURCE_ID).get("sha256")),
            _str_or_none(_get(dual_diff, "approval_source_sha256")),
            _str_or_none(_get(dual_ground_diff, "approval_source_sha256")),
            _str_or_none(
                _get(
                    readiness_map,
                    "source_hashes",
                    "research_anchor_approvals_yaml",
                    "approvals_source_manifest",
                )
            ),
            (
                _str_or_none(
                    _source_entry(selected_registry, _APPROVALS_SOURCE_ID).get("sha256")
                )
                if _selected_source(selection, readiness_map) == "approvals_inclusive"
                else None
            ),
        )
        if value is not None
    }
    workflow_source_mismatch = len(approval_source_hashes) > 1
    blockers: list[str] = []
    if workflow_source_mismatch:
        blockers.append("workflow_approval_source_identity_mismatch")

    as_of_date = _first_str(
        _get(selected_registry, "as_of_date"),
        _get(packet, "strategy_settings_summary", "as_of"),
        _get(readiness_map, "as_of_date"),
        _get(base, "as_of_date"),
        _get(appr_reg, "as_of_date"),
    )

    diagnostics_incomplete = bool(input_problems or warnings or blockers)
    selected_summary = _selected_registry_summary(
        selected_registry=selected_registry,
        selected_registry_source=selected_registry_source,
        selected_source=_selected_source(selection, readiness_map),
        readiness=readiness_map,
        selection=selection,
        registry_sha=evidence_registry_sha,
    )

    return _result(
        generated_at=generated_at,
        as_of_date=as_of_date,
        selected_registry=selected_summary,
        source_manifest_summary=_source_manifest_summary(selected_registry),
        baseline_summary=_baseline_summary(base),
        approvals_summary=_approvals_summary(approvals_val, appr_reg),
        revocations_summary=_revocations_summary(revocations_val, appr_reg),
        candidates_summary=_candidates_summary(candidates_artifact),
        support_grounding_summary=_support_grounding_summary(support, selected_registry),
        blockers=blockers,
        warnings=warnings,
        diagnostics={
            "diagnostics_incomplete": diagnostics_incomplete,
            "input_problems": input_problems,
            "evidence_observer_registry_mismatch": evidence_observer_mismatch,
            "workflow_approval_source_identity_mismatch": workflow_source_mismatch,
            "approval_source_sha256_values": sorted(approval_source_hashes),
            "unknown_schema_sources": unknown_schema_sources,
            "partial_data": diagnostics_incomplete,
            "selected_registry_source": selected_registry_source if selected_registry is not None else None,
            "readiness_recomputed": False,
            "registry_selection_recomputed": False,
            "files_read": [],
        },
    )


def _result(
    *,
    generated_at: str | None,
    as_of_date: str | None,
    selected_registry: dict[str, Any],
    source_manifest_summary: list[dict[str, Any]],
    baseline_summary: dict[str, Any],
    approvals_summary: dict[str, Any],
    revocations_summary: dict[str, Any],
    candidates_summary: dict[str, Any],
    support_grounding_summary: dict[str, Any],
    blockers: list[str],
    warnings: list[str],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "consumed_by_support_signals": False,
        "consumed_by_active_registry": False,
        "consumed_by_availability": False,
        "consumed_by_gates": False,
        "consumed_by_order_path": False,
        "consumed_by_step2": False,
        "consumed_by_step3": False,
        "consumed_by_step4": False,
        "consumed_by_final_execution": False,
        "consumed_by_weekly": False,
        "consumed_by_broker_live": False,
        "cannot_affect_allowed_actions": True,
        "cannot_affect_registry_selection": True,
        "not_registry_selection_input": True,
        "not_order_input": True,
        "not_permission_input": True,
        "not_gate_input": True,
        "not_budget_input": True,
        "not_allocation_input": True,
        "not_anchor_source": True,
        "not_approval_source": True,
        "not_revocation_source": True,
        "not_candidate_source": True,
        "selected_registry": selected_registry,
        "source_manifest_summary": source_manifest_summary,
        "baseline_summary": baseline_summary,
        "approvals_summary": approvals_summary,
        "revocations_summary": revocations_summary,
        "candidates_summary": candidates_summary,
        "support_grounding_summary": support_grounding_summary,
        "blockers": blockers,
        "warnings": warnings,
        "diagnostics": diagnostics,
        "safety_invariants": {
            "llm_assisted_not_llm_authorized": True,
            "support_signals_report_only": True,
            "candidates_audit_only": True,
            "candidate_sha256_audit_only": True,
            "operator_completed_anchor_sha256_binding_hash": True,
            "revocation_reason_non_authoritative": True,
            "observatory_failure_opens_no_gate": True,
            "observatory_failure_opens_no_order_path": True,
            "new_buy_permission_opened": False,
            "order_compilation_permission_opened": False,
            "step4_enabled": False,
            "final_execution_enabled": False,
            "weekly_automation_changed": False,
            "broker_live_execution_enabled": False,
            "automatic_order_placement_enabled": False,
            "executable_order_authority": False,
        },
        "notes": _NOTES,
    }


def _selected_registry_summary(
    *,
    selected_registry: Mapping[str, Any] | None,
    selected_registry_source: str,
    selected_source: str | None,
    readiness: Mapping[str, Any] | None,
    selection: Mapping[str, Any] | None,
    registry_sha: str | None,
) -> dict[str, Any]:
    active = _active_anchors(selected_registry)
    inactive = _inactive_anchors(selected_registry)
    return {
        "selected_source": selected_source,
        "selection_reason": _selection_reason(selected_source, readiness),
        "fallback_reason": _fallback_reason(selected_source, readiness),
        "fail_closed_reason": _fail_closed_reason(selected_source, selected_registry),
        "readiness_ready": _bool_or_none(_get(readiness, "ready")),
        "readiness_switch_target": _str_or_none(_get(readiness, "switch_target")),
        "registry_valid": _bool_or_none(_get(selected_registry, "registry_valid")),
        "active_anchor_count": len(active),
        "inactive_anchor_count": len(inactive),
        "baseline_derived_active_count": sum(1 for row in active if row.get("source_id") == _BASELINE_SOURCE_ID),
        "approval_derived_active_count": sum(1 for row in active if row.get("source_id") == _APPROVALS_SOURCE_ID),
        "revoked_count": sum(1 for row in inactive if row.get("status") == "revoked"),
        "pending_revocation_count": len(_as_list(_get(selected_registry, "revocations_pending"))),
        "evidence_packet_registry_sha256": registry_sha,
        "selected_registry_source": selected_registry_source if selected_registry is not None else None,
        "selection_recomputed_by_observatory": False,
        "selection_artifact_sha256": _sha256_of(selection),
    }


def _baseline_summary(registry: Mapping[str, Any] | None) -> dict[str, Any]:
    if registry is None:
        out = _empty_baseline_summary()
        out["present"] = False
        return out
    active = _active_anchors(registry)
    inactive = _inactive_anchors(registry)
    return {
        "present": True,
        "schema_version": _str_or_none(registry.get("schema_version")),
        "registry_valid": registry.get("registry_valid") is True,
        "source_present": _source_entry(registry, _BASELINE_SOURCE_ID).get("present"),
        "source_sha256": _source_entry(registry, _BASELINE_SOURCE_ID).get("sha256"),
        "active_anchor_count": len(active),
        "inactive_anchor_count": len(inactive),
        "blocker_count": len(_as_list(registry.get("registry_blockers"))),
        "warning_count": 0,
        "artifact_sha256": _sha256_of(registry),
    }


def _approvals_summary(
    approvals_validation: Mapping[str, Any] | None,
    approvals_registry: Mapping[str, Any] | None,
) -> dict[str, Any]:
    summary = _empty_approvals_summary()
    if approvals_validation is not None:
        counts = approvals_validation.get("counts") if isinstance(approvals_validation.get("counts"), Mapping) else {}
        summary.update(
            {
                "source_present": approvals_validation.get("source_present") is True,
                "source_sha256": _str_or_none(approvals_validation.get("source_sha256")),
                "source_valid": approvals_validation.get("source_valid") is True,
                "approval_count": _int_from(counts.get("approvals"), len(_as_list(approvals_validation.get("approval_results")))),
                "valid_approval_count": _int_from(counts.get("valid_report_only"), 0),
                "invalid_approval_count": _int_from(counts.get("rejected"), 0),
                "blocker_count": len(_as_list(approvals_validation.get("manifest_errors"))),
                "warning_count": len(_as_list(approvals_validation.get("manifest_warnings"))),
            }
        )
    elif approvals_registry is not None:
        entry = _source_entry(approvals_registry, _APPROVALS_SOURCE_ID)
        summary.update(
            {
                "source_present": entry.get("present") is True,
                "source_sha256": _str_or_none(entry.get("sha256")),
                "source_valid": entry.get("valid") is True,
                "blocker_count": len(_as_list(entry.get("problems"))),
            }
        )
    if approvals_registry is not None:
        summary["active_approval_anchor_count"] = sum(
            1 for row in _active_anchors(approvals_registry) if row.get("source_id") == _APPROVALS_SOURCE_ID
        )
    return summary


def _revocations_summary(
    revocations_validation: Mapping[str, Any] | None,
    approvals_registry: Mapping[str, Any] | None,
) -> dict[str, Any]:
    summary = _empty_revocations_summary()
    if revocations_validation is not None:
        results = [r for r in _as_list(revocations_validation.get("revocation_results")) if isinstance(r, Mapping)]
        counts = revocations_validation.get("counts") if isinstance(revocations_validation.get("counts"), Mapping) else {}
        summary.update(
            {
                "source_present": revocations_validation.get("source_present") is True,
                "source_sha256": _str_or_none(revocations_validation.get("source_sha256")),
                "revocations_valid": revocations_validation.get("revocations_valid") is True,
                "revocation_count": _int_from(counts.get("checked"), len(results)),
                "future_revocations_pending": _int_from(counts.get("pending_future"), _count_status(results, "valid_pending_future")),
                "blocker_count": len(_as_list(revocations_validation.get("blockers"))),
                "warning_count": len(_as_list(revocations_validation.get("warnings"))),
                "unknown_target_count": _count_binding(results, "target_not_found"),
                "hash_mismatch_count": _count_binding(results, "hash_mismatch"),
                "inconsistent_triple_count": _count_binding(results, "inconsistent"),
            }
        )
    elif approvals_registry is not None:
        entry = _source_entry(approvals_registry, _REVOCATIONS_SOURCE_ID)
        summary.update(
            {
                "source_present": entry.get("present") is True,
                "source_sha256": _str_or_none(entry.get("sha256")),
                "revocations_valid": entry.get("valid") is True,
                "blocker_count": len(_as_list(entry.get("problems"))),
            }
        )
    if approvals_registry is not None:
        summary["active_revocations_applied"] = len(_as_list(approvals_registry.get("revocations_applied")))
        summary["future_revocations_pending"] = len(_as_list(approvals_registry.get("revocations_pending")))
        summary["duplicate_target_count"] = sum(
            1
            for problem in _as_list(approvals_registry.get("revocation_problems"))
            if isinstance(problem, Mapping) and problem.get("reason") == "duplicate_target_revocation"
        )
    return summary


def _candidates_summary(candidates: Mapping[str, Any] | None) -> dict[str, Any]:
    summary = _empty_candidates_summary()
    if candidates is None:
        return summary
    counts = candidates.get("counts") if isinstance(candidates.get("counts"), Mapping) else {}
    summary.update(
        {
            "artifact_present": True,
            "artifact_sha256": _sha256_of(candidates),
            "candidate_count": _int_from(counts.get("candidates"), len(_as_list(candidates.get("candidates")))),
        }
    )
    return summary


def _support_grounding_summary(
    support_signals: Mapping[str, Any] | None,
    selected_registry: Mapping[str, Any] | None,
) -> dict[str, Any]:
    summary = _empty_support_summary()
    if support_signals is None:
        return summary
    candidate_rows = _as_list(support_signals.get("candidate_ticker_signals"))
    accepted = _as_list(support_signals.get("accepted_support_signals"))
    rejected = _as_list(support_signals.get("rejected_support_signals"))
    qualitative = _as_list(support_signals.get("qualitative_support_only"))
    total_claims = len(candidate_rows) if candidate_rows else len(accepted) + len(rejected) + len(qualitative)
    grounded = len(accepted)
    ungrounded = max(total_claims - grounded, 0)
    summary.update(
        {
            "support_signals_present": True,
            "support_signals_report_only": support_signals.get("report_only") is True,
            "support_signals_not_authorization": support_signals.get("not_authorization") is True,
            "registry_valid_seen": _bool_or_none(_get(selected_registry, "registry_valid")),
            "total_memo_claims": total_claims,
            "grounded_claim_count": grounded,
            "ungrounded_claim_count": ungrounded,
            "rejected_support_signal_count": len(rejected),
            "qualitative_support_only_count": len(qualitative),
            "revoked_or_inactive_match_count_if_available": _revoked_or_inactive_match_count(
                support_signals, selected_registry
            ),
        }
    )
    return summary


def _source_manifest_summary(registry: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    manifest = _as_list(_get(registry, "source_manifest"))
    out: list[dict[str, Any]] = []
    for entry in manifest:
        if not isinstance(entry, Mapping):
            continue
        out.append(
            {
                "source_id": _str_or_none(entry.get("source_id")),
                "source_type": _str_or_none(entry.get("source_type")),
                "path": _str_or_none(entry.get("path")),
                "sha256": _str_or_none(entry.get("sha256")),
                "present": _bool_or_none(entry.get("present")),
                "valid": _bool_or_none(entry.get("valid")),
                "problem_count": len(_as_list(entry.get("problems"))),
            }
        )
    return out


def _empty_selected_registry() -> dict[str, Any]:
    return {
        "selected_source": None,
        "selection_reason": "unavailable",
        "fallback_reason": None,
        "fail_closed_reason": None,
        "readiness_ready": None,
        "readiness_switch_target": None,
        "registry_valid": None,
        "active_anchor_count": 0,
        "inactive_anchor_count": 0,
        "baseline_derived_active_count": 0,
        "approval_derived_active_count": 0,
        "revoked_count": 0,
        "pending_revocation_count": 0,
        "evidence_packet_registry_sha256": None,
        "selected_registry_source": None,
        "selection_recomputed_by_observatory": False,
        "selection_artifact_sha256": None,
    }


def _empty_baseline_summary() -> dict[str, Any]:
    return {
        "present": False,
        "schema_version": None,
        "registry_valid": None,
        "source_present": None,
        "source_sha256": None,
        "active_anchor_count": 0,
        "inactive_anchor_count": 0,
        "blocker_count": 0,
        "warning_count": 0,
        "artifact_sha256": None,
    }


def _empty_approvals_summary() -> dict[str, Any]:
    return {
        "source_present": False,
        "source_sha256": None,
        "source_valid": None,
        "approval_count": 0,
        "valid_approval_count": 0,
        "active_approval_anchor_count": 0,
        "invalid_approval_count": 0,
        "blocker_count": 0,
        "warning_count": 0,
    }


def _empty_revocations_summary() -> dict[str, Any]:
    return {
        "source_present": False,
        "source_sha256": None,
        "revocations_valid": None,
        "revocation_count": 0,
        "active_revocations_applied": 0,
        "future_revocations_pending": 0,
        "blocker_count": 0,
        "warning_count": 0,
        "unknown_target_count": 0,
        "hash_mismatch_count": 0,
        "inconsistent_triple_count": 0,
        "duplicate_target_count": 0,
        "reason_parsed_for_logic": False,
    }


def _empty_candidates_summary() -> dict[str, Any]:
    return {
        "artifact_present": False,
        "artifact_sha256": None,
        "candidate_count": 0,
        "candidate_sha256_audit_only": True,
        "consumed_by_grounding": False,
        "candidates_can_activate_anchors": False,
        "candidates_can_revoke_anchors": False,
    }


def _empty_support_summary() -> dict[str, Any]:
    return {
        "support_signals_present": False,
        "support_signals_report_only": None,
        "support_signals_not_authorization": None,
        "registry_valid_seen": None,
        "total_memo_claims": 0,
        "grounded_claim_count": 0,
        "ungrounded_claim_count": 0,
        "rejected_support_signal_count": 0,
        "qualitative_support_only_count": 0,
        "revoked_or_inactive_match_count_if_available": None,
        "can_authorize_trades": False,
        "can_authorize_orders": False,
    }


def _mapping_or_none(value: Any, name: str, problems: list[dict[str, Any]]) -> Mapping[str, Any] | None:
    if value is None:
        problems.append({"input": name, "problem": "missing"})
        return None
    if not isinstance(value, Mapping):
        problems.append({"input": name, "problem": "malformed_not_mapping"})
        return None
    return value


def _check_schema(
    value: Mapping[str, Any] | None,
    name: str,
    supported: set[str] | frozenset[str],
    unknown: list[dict[str, Any]],
    problems: list[dict[str, Any]],
) -> None:
    if value is None:
        return
    schema = value.get("schema_version")
    if not isinstance(schema, str):
        problems.append({"input": name, "problem": "schema_version_missing_or_malformed"})
    elif schema not in supported:
        unknown.append({"input": name, "schema_version": schema})


def _active_anchors(registry: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    return [row for row in _as_list(_get(registry, "active_anchors")) if isinstance(row, Mapping)]


def _inactive_anchors(registry: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    return [row for row in _as_list(_get(registry, "inactive_anchors")) if isinstance(row, Mapping)]


def _selected_source(selection: Mapping[str, Any] | None, readiness: Mapping[str, Any] | None) -> str | None:
    return _first_str(_get(selection, "selected_source"), _get(readiness, "switch_target"))


def _selection_reason(selected_source: str | None, readiness: Mapping[str, Any] | None) -> str | None:
    if selected_source == "approvals_inclusive":
        return "readiness_safe_approvals_inclusive"
    if selected_source == "baseline_fallback":
        return "approvals_not_ready_baseline_fallback_safe"
    if selected_source == "fail_closed_empty":
        return "fail_closed_empty_selected"
    failed = _as_list(_get(readiness, "failed_conditions"))
    if failed:
        return "readiness_failed"
    return None


def _fallback_reason(selected_source: str | None, readiness: Mapping[str, Any] | None) -> str | None:
    if selected_source != "baseline_fallback":
        return None
    failed = _as_list(_get(readiness, "failed_conditions"))
    return "approvals_inclusive_failed_readiness" + (f":{','.join(map(str, failed))}" if failed else "")


def _fail_closed_reason(selected_source: str | None, registry: Mapping[str, Any] | None) -> str | None:
    if selected_source != "fail_closed_empty":
        return None
    blockers = [b for b in _as_list(_get(registry, "registry_blockers")) if isinstance(b, str)]
    return blockers[0] if blockers else "fail_closed_empty"


def _source_entry(registry: Mapping[str, Any] | None, source_id: str) -> Mapping[str, Any]:
    for entry in _as_list(_get(registry, "source_manifest")):
        if isinstance(entry, Mapping) and entry.get("source_id") == source_id:
            return entry
    return {}


def _count_status(rows: list[Mapping[str, Any]], status: str) -> int:
    return sum(1 for row in rows if row.get("status") == status)


def _count_binding(rows: list[Mapping[str, Any]], status: str) -> int:
    return sum(1 for row in rows if row.get("target_binding_status") == status)


def _revoked_or_inactive_match_count(
    support_signals: Mapping[str, Any],
    selected_registry: Mapping[str, Any] | None,
) -> int | None:
    inactive_ids = {
        row.get("anchor_id")
        for row in _inactive_anchors(selected_registry)
        if isinstance(row.get("anchor_id"), str)
    }
    if not inactive_ids:
        return 0
    count = 0
    for row in _as_list(support_signals.get("candidate_ticker_signals")):
        if not isinstance(row, Mapping):
            continue
        refs = row.get("anchor_id_refs")
        if isinstance(refs, list) and any(ref in inactive_ids for ref in refs if isinstance(ref, str)):
            count += 1
    return count


def _get(value: Mapping[str, Any] | None, *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _first_str(*values: Any) -> str | None:
    for value in values:
        result = _str_or_none(value)
        if result is not None:
            return result
    return None


def _int_from(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
