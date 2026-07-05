"""R2G-6a grounding status observatory tests.

The observatory is a pure, report-only summary of already-loaded artifacts. These
tests prove it is inert: consumed by nothing, no authority, no readiness or
registry selection recomputation, no file IO, and no order/gate permission.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from investment_orchestrator.research.active_research_anchor_registry import (
    build_active_research_anchor_registry,
)
from investment_orchestrator.research.approval_registry_dual_read_diff import (
    build_approval_registry_dual_read_diff,
)
from investment_orchestrator.research.approval_registry_switch_readiness import (
    evaluate_approval_registry_switch_readiness,
)
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    build_active_research_anchor_registry_with_approvals,
)
from investment_orchestrator.research.evidence_packet import (
    build_evidence_packet,
    fail_closed_empty_active_anchor_registry,
)
from investment_orchestrator.research.grounding_status_observatory import (
    SCHEMA_VERSION,
    build_grounding_status_observatory,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    build_research_anchor_approvals_validation,
    compute_operator_completed_anchor_sha256 as sha,
)
from investment_orchestrator.research.research_anchor_candidates import (
    build_research_anchor_candidates,
)
from investment_orchestrator.research.research_anchor_revocation_manifest import (
    build_research_anchor_revocations_validation,
)
from investment_orchestrator.research.research_anchors import validate_research_anchors
from investment_orchestrator.research.support_signals import build_compiled_support_signals


AS_OF = "2026-07-04"
UNIVERSE = ["QQQ", "VOO", "SMH"]
ANCHORS_SHA = "anchors-sha"
APPROVALS_SHA = "approvals-sha"

_ORDER_SHAPED_KEYS = frozenset(
    {
        "account",
        "quantity",
        "shares",
        "order_type",
        "tif",
        "time_in_force",
        "limit_price",
        "stop_price",
        "venue",
        "routing",
        "broker",
        "order_intent",
        "order_sizing",
        "execution_authorization",
    }
)


def _anchor(anchor_id: str = "AI_CAPEX_2026H2", ticker: str = "QQQ", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": anchor_id,
        "anchor_type": "structural_theme",
        "applicable_tickers": [ticker],
        "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01",
        "valid_until": "2026-07-31",
        "source_type": "operator",
        "confidence_floor": "medium",
        "summary": "Operator-dated thesis grounding.",
    }
    base.update(overrides)
    return base


def _baseline(anchors: list[dict[str, Any]] | None = None, *, source_sha: str = ANCHORS_SHA) -> dict[str, Any]:
    payload = {"schema_version": "research_anchors_v1", "is_llm_generated": False, "anchors": anchors or []}
    result = validate_research_anchors(payload, allowed_universe=UNIVERSE, today=AS_OF)
    return build_active_research_anchor_registry(
        anchors_result=result,
        source_present=bool(anchors),
        source_sha256=source_sha if anchors else None,
        source_path="inputs/current/research_anchors.yaml",
        as_of_date=AS_OF,
    )


def _approval(anchor: dict[str, Any] | None = None, *, approval_id: str = "APR-1") -> dict[str, Any]:
    a = anchor or _anchor()
    return {
        "approval_id": approval_id,
        "decision": "approve",
        "operator_completed_anchor": a,
        "operator_completed_anchor_sha256": sha(a),
        "approved_by": "operator",
    }


def _approvals_validation(approvals: list[dict[str, Any]], **manifest_overrides: Any) -> dict[str, Any]:
    manifest = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": AS_OF,
        "approvals": approvals,
    }
    manifest.update(manifest_overrides)
    return build_research_anchor_approvals_validation(
        manifest=manifest,
        source_present=True,
        source_sha256=APPROVALS_SHA,
        source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )


def _revocation(anchor: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    a = anchor or _anchor()
    base: dict[str, Any] = {
        "revocation_id": "REV-1",
        "target_type": "approval_anchor",
        "approval_id": "APR-1",
        "anchor_id": a["anchor_id"],
        "operator_completed_anchor_sha256": sha(a),
        "effective_as_of": AS_OF,
        "reason": "Thesis invalidated.",
        "revoked_by": "operator",
    }
    base.update(overrides)
    return base


def _revocations_validation(
    revocations: Any,
    approvals: list[dict[str, Any]],
    **manifest_overrides: Any,
) -> dict[str, Any]:
    manifest = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": AS_OF,
        "approvals": approvals,
        "revocations": revocations,
    }
    manifest.update(manifest_overrides)
    return build_research_anchor_revocations_validation(
        manifest=manifest,
        approvals_validation=_approvals_validation(approvals),
        source_present=True,
        source_sha256=APPROVALS_SHA,
        source_path="inputs/current/research_anchor_approvals.yaml",
        today=AS_OF,
        as_of_date=AS_OF,
    )


def _memo(anchor_id: str = "AI_CAPEX_2026H2") -> dict[str, Any]:
    return {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "confidence": "high",
        "source_notes": [{"claim": "grounded", "source": "operator"}],
        "ticker_relative_view": [
            {
                "ticker": "QQQ",
                "stance": "prefer",
                "rationale_12m_plus": "grounded thesis",
                "anchor_id_refs": [anchor_id],
            }
        ],
    }


def _packet(registry: dict[str, Any]) -> dict[str, Any]:
    return build_evidence_packet(
        strategy_settings={"as_of": AS_OF, "core_universe": ["QQQ", "VOO"], "satellite_universe": ["SMH"]},
        portfolio_snapshot_text=None,
        now_date=AS_OF,
        active_anchor_registry=registry,
    )


def _selection(
    *,
    baseline: dict[str, Any],
    approvals_registry: dict[str, Any],
    selected_source: str = "approvals_inclusive",
) -> dict[str, Any]:
    diff = build_approval_registry_dual_read_diff(baseline_registry=baseline, approvals_registry=approvals_registry)
    readiness = evaluate_approval_registry_switch_readiness(
        baseline_registry=baseline,
        approvals_registry=approvals_registry,
        dual_read_diff=diff,
        current_research_anchors_sha256=ANCHORS_SHA,
        current_research_anchor_approvals_sha256=APPROVALS_SHA,
        approvals_source_present=True,
        as_of_date=AS_OF,
    )
    selected = (
        approvals_registry
        if selected_source == "approvals_inclusive"
        else baseline
        if selected_source == "baseline_fallback"
        else fail_closed_empty_active_anchor_registry()
    )
    if selected_source == "baseline_fallback":
        readiness = {**readiness, "ready": False, "switch_target": "baseline_fallback", "baseline_fallback_safe": True}
    elif selected_source == "fail_closed_empty":
        readiness = {**readiness, "ready": False, "switch_target": "fail_closed_empty", "baseline_fallback_safe": False}
    return {
        "schema_version": "embedded_active_anchor_registry_selection_v1",
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "selected_source": selected_source,
        "selected_registry": selected,
        "baseline_registry": baseline,
        "approvals_registry": approvals_registry,
        "dual_read_diff": diff,
        "readiness": readiness,
    }


def _candidates_artifact(packet: dict[str, Any]) -> dict[str, Any]:
    return build_research_anchor_candidates(
        evidence_packet=packet,
        analyst_memo=_memo("MISSING_ANCHOR"),
        analyst_memo_valid=True,
        compiled_support_signals=None,
        active_registry=packet["active_anchor_registry"],
        as_of_date=AS_OF,
    )


def _observatory(
    *,
    baseline: dict[str, Any] | None = None,
    approvals_registry: dict[str, Any] | None = None,
    approvals_validation: dict[str, Any] | None = None,
    revocations_validation: dict[str, Any] | None = None,
    selected_source: str = "approvals_inclusive",
    selected_registry: dict[str, Any] | None = None,
    support_anchor_id: str = "AI_CAPEX_2026H2",
) -> dict[str, Any]:
    base = baseline or _baseline([_anchor("VOO_BASE", "VOO")])
    approval = _approval()
    approvals_val = approvals_validation or _approvals_validation([approval])
    appr = approvals_registry or build_active_research_anchor_registry_with_approvals(
        baseline=base,
        approvals_validation=approvals_val,
        revocations_validation=revocations_validation,
    )
    selection = _selection(baseline=base, approvals_registry=appr, selected_source=selected_source)
    registry = selected_registry or selection["selected_registry"]
    packet = _packet(registry)
    support = build_compiled_support_signals(
        evidence_packet=packet,
        analyst_memo=_memo(support_anchor_id),
        compilation_mode="evidence_plus_memo",
    )
    return build_grounding_status_observatory(
        evidence_packet=packet,
        embedded_registry_selection=selection,
        readiness=selection["readiness"],
        baseline_registry=base,
        approvals_registry=appr,
        approvals_validation=approvals_val,
        revocations_validation=revocations_validation,
        candidates=_candidates_artifact(packet),
        support_signals=support,
        generated_at="t",
    )


def _keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_required_top_level_markers_and_schema() -> None:
    result = _observatory()
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["is_llm_generated"] is False
    assert result["report_only"] is True
    assert result["permission_effect"] == "none"
    assert result["not_authorization"] is True
    assert result["not_execution_authorization"] is True
    assert result["consumed_by_gates"] is False
    assert result["consumed_by_order_path"] is False
    assert result["cannot_affect_allowed_actions"] is True
    assert result["cannot_affect_registry_selection"] is True
    assert result["not_registry_selection_input"] is True
    assert result["not_order_input"] is True


def test_json_serializable() -> None:
    result = _observatory()
    assert json.loads(json.dumps(result))["schema_version"] == SCHEMA_VERSION


def test_no_authority_markers_and_no_consumers() -> None:
    result = _observatory()
    for key in (
        "consumed_by_support_signals",
        "consumed_by_active_registry",
        "consumed_by_availability",
        "consumed_by_gates",
        "consumed_by_step2",
        "consumed_by_step3",
        "consumed_by_step4",
        "consumed_by_final_execution",
        "consumed_by_weekly",
        "consumed_by_broker_live",
    ):
        assert result[key] is False
    assert result["not_anchor_source"] is True
    assert result["not_approval_source"] is True
    assert result["not_revocation_source"] is True
    assert result["not_candidate_source"] is True


def test_pure_builder_only_not_imported_by_workflow_or_consumers() -> None:
    import investment_orchestrator.research.evidence_packet as ep
    import investment_orchestrator.research.support_signals as ss
    import investment_orchestrator.state.research_availability as ra
    import investment_orchestrator.workflow.step1_research as step1

    for module in (ep, ss, ra, step1):
        assert "grounding_status_observatory" not in inspect.getsource(module)


def test_approvals_selected_summary() -> None:
    result = _observatory()
    selected = result["selected_registry"]
    assert selected["selected_source"] == "approvals_inclusive"
    assert selected["selection_reason"] == "readiness_safe_approvals_inclusive"
    assert selected["registry_valid"] is True
    assert selected["approval_derived_active_count"] == 1
    assert selected["baseline_derived_active_count"] == 1
    assert len(selected["evidence_packet_registry_sha256"]) == 64


def test_baseline_fallback_summary() -> None:
    approval = _approval()
    base = _baseline([_anchor("BASE_QQQ")])
    rev_val = _revocations_validation([_revocation(approval_id="APR-UNKNOWN")], [approval])
    appr_val = _approvals_validation([approval])
    appr = build_active_research_anchor_registry_with_approvals(
        baseline=base,
        approvals_validation=appr_val,
        revocations_validation=rev_val,
    )
    result = _observatory(
        baseline=base,
        approvals_registry=appr,
        approvals_validation=appr_val,
        revocations_validation=rev_val,
        selected_source="baseline_fallback",
        support_anchor_id="BASE_QQQ",
    )
    assert result["selected_registry"]["selected_source"] == "baseline_fallback"
    assert result["selected_registry"]["fallback_reason"].startswith("approvals_inclusive_failed_readiness")
    assert result["approvals_summary"]["active_approval_anchor_count"] == 0
    assert result["revocations_summary"]["unknown_target_count"] == 1


def test_fail_closed_empty_summary() -> None:
    base = _baseline([_anchor("DUP"), _anchor("DUP")])
    approval = _approval()
    rev_val = _revocations_validation([_revocation(approval_id="APR-UNKNOWN")], [approval])
    appr_val = _approvals_validation([approval])
    appr = build_active_research_anchor_registry_with_approvals(
        baseline=base,
        approvals_validation=appr_val,
        revocations_validation=rev_val,
    )
    result = _observatory(
        baseline=base,
        approvals_registry=appr,
        approvals_validation=appr_val,
        revocations_validation=rev_val,
        selected_source="fail_closed_empty",
        selected_registry=fail_closed_empty_active_anchor_registry(),
    )
    assert result["selected_registry"]["selected_source"] == "fail_closed_empty"
    assert result["selected_registry"]["active_anchor_count"] == 0
    assert result["selected_registry"]["fail_closed_reason"] == "approval_registry_switch_fail_closed_empty"


def test_valid_active_revocation_summary() -> None:
    approval = _approval()
    rev_val = _revocations_validation([_revocation()], [approval])
    result = _observatory(revocations_validation=rev_val, support_anchor_id="AI_CAPEX_2026H2")
    assert result["selected_registry"]["revoked_count"] == 1
    assert result["selected_registry"]["approval_derived_active_count"] == 0
    assert result["revocations_summary"]["revocation_count"] == 1
    assert result["revocations_summary"]["active_revocations_applied"] == 1
    assert result["support_grounding_summary"]["grounded_claim_count"] == 0
    assert result["support_grounding_summary"]["revoked_or_inactive_match_count_if_available"] == 1


def test_invalid_revocation_fallback_summary() -> None:
    approval = _approval()
    rev_val = _revocations_validation([_revocation(operator_completed_anchor_sha256="0" * 64)], [approval])
    appr_val = _approvals_validation([approval])
    appr = build_active_research_anchor_registry_with_approvals(
        baseline=_baseline([_anchor("BASE_QQQ")]),
        approvals_validation=appr_val,
        revocations_validation=rev_val,
    )
    result = _observatory(
        approvals_registry=appr,
        approvals_validation=appr_val,
        revocations_validation=rev_val,
        selected_source="baseline_fallback",
        support_anchor_id="BASE_QQQ",
    )
    assert result["revocations_summary"]["revocations_valid"] is False
    assert result["revocations_summary"]["hash_mismatch_count"] == 1
    assert result["selected_registry"]["selected_source"] == "baseline_fallback"


def test_future_revocation_pending_summary() -> None:
    approval = _approval()
    rev_val = _revocations_validation([_revocation(effective_as_of="2026-12-31")], [approval])
    result = _observatory(revocations_validation=rev_val)
    assert result["revocations_summary"]["future_revocations_pending"] == 1
    assert result["selected_registry"]["pending_revocation_count"] == 1
    assert result["selected_registry"]["approval_derived_active_count"] == 1


def test_candidate_audit_only_summary() -> None:
    result = _observatory()
    candidates = result["candidates_summary"]
    assert candidates["artifact_present"] is True
    assert candidates["candidate_count"] >= 0
    assert candidates["candidate_sha256_audit_only"] is True
    assert candidates["consumed_by_grounding"] is False
    assert candidates["candidates_can_activate_anchors"] is False
    assert candidates["candidates_can_revoke_anchors"] is False


def test_support_signals_report_only_summary() -> None:
    result = _observatory()
    support = result["support_grounding_summary"]
    assert support["support_signals_present"] is True
    assert support["support_signals_report_only"] is True
    assert support["support_signals_not_authorization"] is True
    assert support["registry_valid_seen"] is True
    assert support["grounded_claim_count"] == 1
    assert support["can_authorize_trades"] is False
    assert support["can_authorize_orders"] is False


def test_missing_inputs_produce_diagnostics() -> None:
    result = build_grounding_status_observatory()
    assert result["diagnostics"]["diagnostics_incomplete"] is True
    assert result["diagnostics"]["partial_data"] is True
    assert "missing_or_malformed_inputs_present" in result["warnings"]
    assert result["selected_registry"]["registry_valid"] is None


def test_malformed_inputs_produce_diagnostics() -> None:
    result = build_grounding_status_observatory(evidence_packet=["not", "mapping"])
    assert result["diagnostics"]["diagnostics_incomplete"] is True
    assert any(p["problem"] == "malformed_not_mapping" for p in result["diagnostics"]["input_problems"])
    assert "missing_or_malformed_inputs_present" in result["warnings"]


def test_unknown_schema_produces_diagnostics() -> None:
    base = _baseline([_anchor("BASE_QQQ")])
    packet = _packet({**base, "schema_version": "future_registry_v999"})
    result = build_grounding_status_observatory(evidence_packet=packet)
    assert result["diagnostics"]["diagnostics_incomplete"] is True
    assert result["diagnostics"]["unknown_schema_sources"] == [
        {"input": "selected_registry", "schema_version": "future_registry_v999"}
    ]
    assert "unknown_schema_source_unusable_for_diagnostics" in result["warnings"]


def test_evidence_observer_mismatch_warns_only() -> None:
    base = _baseline([_anchor("VOO_BASE", "VOO")])
    approval = _approval()
    appr_val = _approvals_validation([approval])
    appr = build_active_research_anchor_registry_with_approvals(baseline=base, approvals_validation=appr_val)
    selection = _selection(baseline=base, approvals_registry=appr, selected_source="approvals_inclusive")
    packet = _packet(base)
    result = build_grounding_status_observatory(
        evidence_packet=packet,
        embedded_registry_selection=selection,
        readiness=selection["readiness"],
        baseline_registry=base,
        approvals_registry=appr,
        approvals_validation=appr_val,
    )
    assert result["diagnostics"]["evidence_observer_registry_mismatch"] is True
    assert "evidence_packet_registry_mismatch_observer_registry" in result["warnings"]
    assert result["blockers"] == []


def test_no_new_buy_or_order_compilation_strings() -> None:
    blob = json.dumps(_observatory())
    assert "NEW_BUY" not in blob
    assert "ORDER_COMPILATION" not in blob


def test_no_step4_final_weekly_broker_live_order_authority_fields() -> None:
    result = _observatory()
    assert result["safety_invariants"]["step4_enabled"] is False
    assert result["safety_invariants"]["final_execution_enabled"] is False
    assert result["safety_invariants"]["weekly_automation_changed"] is False
    assert result["safety_invariants"]["broker_live_execution_enabled"] is False
    assert result["safety_invariants"]["automatic_order_placement_enabled"] is False
    assert result["safety_invariants"]["executable_order_authority"] is False
    present = {key for key in _keys(result) if key.lower() in _ORDER_SHAPED_KEYS}
    assert present == set()


def test_no_registry_selection_authority_fields() -> None:
    result = _observatory()
    assert result["not_registry_selection_input"] is True
    assert result["cannot_affect_registry_selection"] is True
    assert result["diagnostics"]["registry_selection_recomputed"] is False
    assert result["diagnostics"]["readiness_recomputed"] is False
    assert result["selected_registry"]["selection_recomputed_by_observatory"] is False
    assert result["diagnostics"]["files_read"] == []
