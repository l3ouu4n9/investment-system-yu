"""R2G-5c-0: approval-registry switch-readiness gate tests.

Every test proves the readiness gate is report-only and fail-closed: it never
switches a consumer, binds nothing to candidate data, treats a stale baseline as
fail_closed_empty (not a safe fallback), and carries no order-shaped / grant
fields. It changes NO production consumer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import yaml

from investment_orchestrator.research.active_research_anchor_registry import (
    build_active_research_anchor_registry,
)
from investment_orchestrator.research.research_anchors import validate_research_anchors
from investment_orchestrator.research.research_anchor_approval_manifest import (
    build_research_anchor_approvals_validation,
    compute_operator_completed_anchor_sha256 as sha,
)
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    build_active_research_anchor_registry_with_approvals,
)
from investment_orchestrator.research.approval_registry_dual_read_diff import (
    build_approval_registry_dual_read_diff,
)
from investment_orchestrator.research.approval_registry_switch_readiness import (
    SCHEMA_VERSION,
    SWITCH_TARGET_APPROVALS,
    SWITCH_TARGET_BASELINE,
    SWITCH_TARGET_FAIL_CLOSED,
    build_approval_registry_switch_readiness,
    evaluate_approval_registry_switch_readiness,
)

UNIVERSE = ["QQQ", "VOO", "SMH"]
AS_OF = "2026-07-04"
ANCHORS_SHA = "current_research_anchors_sha256_value"
APPROVALS_SHA = "current_research_anchor_approvals_sha256_value"
_AUTO = object()

_ORDER_SHAPED_KEYS = frozenset(
    {
        "account", "quantity", "shares", "order_type", "tif", "time_in_force",
        "limit_price", "stop_price", "venue", "routing", "broker", "new_buy",
        "order_compilation", "budget", "allocation",
    }
)


def _anchor(anchor_id: str = "AI_CAPEX", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": anchor_id, "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"], "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01", "valid_until": "2026-07-31",
        "source_type": "operator", "confidence_floor": "medium", "summary": "x",
    }
    base.update(overrides)
    return base


def _baseline(anchors: list[dict[str, Any]] | None = None, *, source_sha: str = ANCHORS_SHA,
              valid_override: bool | None = None) -> dict[str, Any]:
    payload = {"schema_version": "research_anchors_v1", "as_of_date": AS_OF, "is_llm_generated": False,
               "anchors": anchors if anchors is not None else [_anchor("VOO_T", applicable_tickers=["VOO"])]}
    result = validate_research_anchors(payload, allowed_universe=UNIVERSE, today=AS_OF)
    return build_active_research_anchor_registry(
        anchors_result=result, source_present=True, source_sha256=source_sha,
        source_path="inputs/current/research_anchors.yaml", as_of_date=AS_OF,
    )


def _approval(anchor: dict[str, Any] | None = None, *, approval_id: str = "APR-1",
              hash_override: str | None = None) -> dict[str, Any]:
    a = anchor or _anchor()
    return {"approval_id": approval_id, "decision": "approve", "operator_completed_anchor": a,
            "operator_completed_anchor_sha256": hash_override if hash_override is not None else sha(a)}


def _validation(approvals: list[dict[str, Any]], *, present: bool = True, source_sha: str = APPROVALS_SHA,
                **manifest_overrides: Any) -> dict[str, Any]:
    manifest = {"schema_version": "research_anchor_approvals_v1", "is_llm_generated": False,
                "as_of_date": AS_OF, "approvals": approvals}
    manifest.update(manifest_overrides)
    return build_research_anchor_approvals_validation(
        manifest=manifest if present else None, source_present=present,
        source_sha256=source_sha if present else None,
        source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE, today=AS_OF,
    )


def _approvals_registry(approvals: list[dict[str, Any]], *, baseline: dict[str, Any] | None = None,
                        present: bool = True, source_sha: str = APPROVALS_SHA,
                        candidate_index: dict[str, Any] | None = None, **manifest_overrides: Any) -> dict[str, Any]:
    base = baseline if baseline is not None else _baseline()
    manifest = {"schema_version": "research_anchor_approvals_v1", "is_llm_generated": False,
                "as_of_date": AS_OF, "approvals": approvals}
    manifest.update(manifest_overrides)
    source_text = yaml.safe_dump(manifest, sort_keys=False) if present else None
    return build_active_research_anchor_registry_with_approvals(
        baseline=base,
        approval_source_text=source_text,
        approval_source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=AS_OF,
        candidate_index=candidate_index,
    )


def _evaluate(*, baseline: dict[str, Any] | None, approvals_reg: dict[str, Any] | None,
              diff: dict[str, Any] | None = "auto", anchors_sha: str | None = ANCHORS_SHA,
              approvals_sha: Any = _AUTO, approvals_present: bool = True) -> dict[str, Any]:
    if diff == "auto":
        diff = build_approval_registry_dual_read_diff(baseline_registry=baseline or {}, approvals_registry=approvals_reg or {})
    if approvals_sha is _AUTO:
        approvals_sha = next(
            (
                row.get("sha256")
                for row in (approvals_reg or {}).get("source_manifest", [])
                if row.get("source_id") == "operator_research_anchor_approvals_yaml"
            ),
            None,
        )
    return evaluate_approval_registry_switch_readiness(
        baseline_registry=baseline, approvals_registry=approvals_reg, dual_read_diff=diff,
        current_research_anchors_sha256=anchors_sha,
        current_research_anchor_approvals_sha256=approvals_sha,
        approvals_source_present=approvals_present,
    )


def _keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _cond(r: dict[str, Any], cid: str) -> bool:
    return all(c["passed"] for c in r["conditions"] if c["id"] == cid)


def _revocation(anchor: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    base = {
        "revocation_id": "REV-1",
        "target_type": "approval_anchor",
        "approval_id": "APR-1",
        "anchor_id": anchor["anchor_id"],
        "operator_completed_anchor_sha256": sha(anchor),
        "effective_as_of": AS_OF,
        "reason": "Thesis invalidated.",
        "revoked_by": "operator",
    }
    base.update(overrides)
    return base


# --- 1. happy path ------------------------------------------------------------


def test_happy_path_ready_approvals_inclusive() -> None:
    base = _baseline()
    wa = _approvals_registry([_approval()], baseline=base)
    r = _evaluate(baseline=base, approvals_reg=wa)
    assert r["schema_version"] == SCHEMA_VERSION
    assert r["ready"] is True
    assert r["switch_target"] == SWITCH_TARGET_APPROVALS
    assert r["baseline_fallback_safe"] is True
    assert r["fail_closed_empty_required"] is False
    assert r["failed_conditions"] == []
    assert r["registry_valid_baseline"] is True
    assert r["registry_valid_with_approvals"] is True
    assert "AI_CAPEX" in r["added_by_approvals"]


def test_source_identity_mismatch_fails_before_approvals_selection() -> None:
    base = _baseline()
    approvals = _approvals_registry([_approval()], baseline=base)
    diff = build_approval_registry_dual_read_diff(
        baseline_registry=base,
        approvals_registry=approvals,
    )
    mismatched_diff = {**diff, "approval_source_sha256": "f" * 64}

    result = _evaluate(
        baseline=base,
        approvals_reg=approvals,
        diff=mismatched_diff,
    )

    assert result["ready"] is False
    assert result["switch_target"] == SWITCH_TARGET_BASELINE
    assert _cond(result, "workflow_approval_source_identity_mismatch") is False
    assert "workflow_approval_source_identity_mismatch" in result[
        "failed_conditions"
    ]


def test_markers_and_non_consumption_fields() -> None:
    base = _baseline()
    r = _evaluate(baseline=base, approvals_reg=_approvals_registry([_approval()], baseline=base))
    assert r["is_llm_generated"] is False
    assert r["report_only"] is True
    assert r["permission_effect"] == "none"
    assert r["not_authorization"] is True
    assert r["not_execution_authorization"] is True
    assert r["cannot_affect_allowed_actions"] is True
    for key in (
        "consumed_by_support_signals", "consumed_by_active_registry",
        "consumed_by_availability", "consumed_by_gates",
        "consumed_by_step2", "consumed_by_step4",
    ):
        assert r[key] is False


# --- 2. empty approvals -------------------------------------------------------


def test_empty_approvals_ready_equivalent_to_baseline() -> None:
    base = _baseline()
    wa = _approvals_registry([], baseline=base)
    r = _evaluate(baseline=base, approvals_reg=wa)
    assert r["ready"] is True
    assert r["switch_target"] == SWITCH_TARGET_APPROVALS
    assert r["added_by_approvals"] == []


def test_absent_approvals_manifest_ready() -> None:
    base = _baseline()
    wa = _approvals_registry([], baseline=base, present=False)
    r = _evaluate(baseline=base, approvals_reg=wa, approvals_sha=None, approvals_present=False)
    assert r["ready"] is True
    assert r["switch_target"] == SWITCH_TARGET_APPROVALS


# --- 3. malformed approvals-inclusive registry -------------------------------


def test_malformed_approvals_registry_baseline_fallback() -> None:
    base = _baseline()
    r = _evaluate(baseline=base, approvals_reg=None)
    assert r["ready"] is False
    assert r["switch_target"] == SWITCH_TARGET_BASELINE
    assert r["baseline_fallback_safe"] is True


def test_wrong_schema_approvals_registry_baseline_fallback() -> None:
    base = _baseline()
    wa = _approvals_registry([_approval()], baseline=base)
    wa = {**wa, "schema_version": "wrong_schema"}
    r = _evaluate(baseline=base, approvals_reg=wa)
    assert r["ready"] is False
    assert _cond(r, "approvals_registry_schema_valid") is False
    assert r["switch_target"] == SWITCH_TARGET_BASELINE


# --- 4. approvals-inclusive registry_valid:false -----------------------------


def test_approvals_registry_invalid_baseline_fallback() -> None:
    base = _baseline()
    # cross-source duplicate makes registry_valid:false
    wa = _approvals_registry([_approval(_anchor("VOO_T", applicable_tickers=["VOO"]))], baseline=base)
    assert wa["registry_valid"] is False
    r = _evaluate(baseline=base, approvals_reg=wa)
    assert r["ready"] is False
    assert _cond(r, "registry_valid_with_approvals_true") is False
    assert r["switch_target"] == SWITCH_TARGET_BASELINE


def test_disk_readiness_builder_is_revocation_aware_and_falls_back(tmp_path: Any) -> None:
    anchors_path = tmp_path / "research_anchors.yaml"
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    approved_anchor = _anchor("AI_CAPEX")
    anchors_path.write_text(
        json.dumps(
            {
                "schema_version": "research_anchors_v1",
                "is_llm_generated": False,
                "as_of_date": AS_OF,
                "anchors": [_anchor("VOO_T", applicable_tickers=["VOO"])],
            }
        ),
        encoding="utf-8",
    )
    approvals_path.write_text(
        json.dumps(
            {
                "schema_version": "research_anchor_approvals_v1",
                "is_llm_generated": False,
                "as_of_date": AS_OF,
                "approvals": [_approval(approved_anchor)],
                "revocations": [_revocation(approved_anchor, approval_id="APR-DOES-NOT-EXIST")],
            }
        ),
        encoding="utf-8",
    )

    r = build_approval_registry_switch_readiness(
        anchors_path=anchors_path,
        approvals_path=approvals_path,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )

    assert r["ready"] is False
    assert r["switch_target"] == SWITCH_TARGET_BASELINE
    assert r["registry_valid_with_approvals"] is False
    assert "revocations_manifest_invalid" in r["registry_blockers"]


# --- 5. cross-source duplicate -----------------------------------------------


def test_cross_source_duplicate_fails_no_cross_condition() -> None:
    base = _baseline([_anchor("SHARED")])
    wa = _approvals_registry([_approval(_anchor("SHARED"))], baseline=base)
    r = _evaluate(baseline=base, approvals_reg=wa)
    assert r["ready"] is False
    assert _cond(r, "no_cross_source_duplicate_anchor_id") is False
    assert _cond(r, "no_duplicate_blockers") is False
    assert r["switch_target"] == SWITCH_TARGET_BASELINE
    assert r["duplicate_blockers"]


# --- 6. baseline stale / source hash mismatch --------------------------------


def test_baseline_source_hash_mismatch_fail_closed_empty() -> None:
    base = _baseline()
    wa = _approvals_registry([_approval()], baseline=base)
    # current YAML hash differs from what the registries recorded
    r = _evaluate(baseline=base, approvals_reg=wa, anchors_sha="STALE_DIFFERENT")
    assert r["ready"] is False
    assert r["baseline_fallback_safe"] is False
    assert r["fail_closed_empty_required"] is True
    assert r["switch_target"] == SWITCH_TARGET_FAIL_CLOSED
    assert _cond(r, "baseline_source_hash_matches_current_research_anchors_yaml") is False


# --- 7. baseline registry_valid:false ----------------------------------------


def test_baseline_invalid_fail_closed_empty() -> None:
    bad = _baseline([_anchor("D"), _anchor("D")])  # duplicate anchor_id -> registry_valid false
    assert bad["registry_valid"] is False
    wa = _approvals_registry([_approval()], baseline=bad)
    r = _evaluate(baseline=bad, approvals_reg=wa)
    assert r["ready"] is False
    assert r["baseline_fallback_safe"] is False
    assert r["fail_closed_empty_required"] is True
    assert r["switch_target"] == SWITCH_TARGET_FAIL_CLOSED


def test_missing_baseline_fail_closed_empty() -> None:
    r = _evaluate(baseline=None, approvals_reg=None)
    assert r["ready"] is False
    assert r["switch_target"] == SWITCH_TARGET_FAIL_CLOSED
    assert r["fail_closed_empty_required"] is True


# --- 8. missing dual-read diff -----------------------------------------------


def test_missing_dual_read_diff_baseline_fallback() -> None:
    base = _baseline()
    wa = _approvals_registry([_approval()], baseline=base)
    r = _evaluate(baseline=base, approvals_reg=wa, diff=None)
    assert r["ready"] is False
    assert _cond(r, "dual_read_diff_present") is False
    assert r["switch_target"] == SWITCH_TARGET_BASELINE


# --- 9. dual-read diff hash mismatch -----------------------------------------


def test_dual_read_diff_hash_mismatch_baseline_fallback() -> None:
    base = _baseline()
    wa = _approvals_registry([_approval()], baseline=base)
    diff = build_approval_registry_dual_read_diff(baseline_registry=base, approvals_registry=wa)
    diff = {**diff, "approvals_registry_sha256": "tampered"}
    r = _evaluate(baseline=base, approvals_reg=wa, diff=diff)
    assert r["ready"] is False
    assert _cond(r, "dual_read_diff_hashes_match") is False
    assert r["switch_target"] == SWITCH_TARGET_BASELINE


# --- 10/11. active approvals-derived row hash conditions ---------------------


def test_active_row_missing_operator_hash_fails() -> None:
    base = _baseline()
    wa = _approvals_registry([_approval()], baseline=base)
    # Tamper: drop operator_completed_anchor_sha256 on the active approval row.
    wa = json.loads(json.dumps(wa))
    for row in wa["active_anchors"]:
        if row.get("source_id") == "operator_research_anchor_approvals_yaml":
            row["operator_completed_anchor_sha256"] = None
    diff = build_approval_registry_dual_read_diff(baseline_registry=base, approvals_registry=wa)
    r = _evaluate(baseline=base, approvals_reg=wa, diff=diff)
    assert r["ready"] is False
    assert _cond(r, "active_approvals_rows_have_operator_completed_anchor_sha256") is False


def test_active_row_hash_match_false_fails() -> None:
    base = _baseline()
    wa = _approvals_registry([_approval()], baseline=base)
    wa = json.loads(json.dumps(wa))
    for row in wa["active_anchors"]:
        if row.get("source_id") == "operator_research_anchor_approvals_yaml":
            row["validation"]["hash_match"] = False
    diff = build_approval_registry_dual_read_diff(baseline_registry=base, approvals_registry=wa)
    r = _evaluate(baseline=base, approvals_reg=wa, diff=diff)
    assert r["ready"] is False
    assert _cond(r, "active_approvals_rows_hash_match_true") is False


# --- 12. candidate audit-only -------------------------------------------------


def test_candidate_mismatch_audit_only_still_ready() -> None:
    base = _baseline()
    # candidate index says 'right' but the approval declares 'wrong' -> candidate_hash_mismatch
    wa = _approvals_registry(
        [{"approval_id": "APR-1", "decision": "approve", "candidate_id": "C1",
          "candidate_sha256": "wrong", "operator_completed_anchor": _anchor(),
          "operator_completed_anchor_sha256": sha(_anchor())}],
        baseline=base, candidate_index={"C1": "right"},
    )
    row = [a for a in wa["active_anchors"] if a["anchor_id"] == "AI_CAPEX"][0]
    assert row["candidate_link_status"] == "candidate_hash_mismatch"
    r = _evaluate(baseline=base, approvals_reg=wa)
    # candidate mismatch does NOT by itself make readiness false.
    assert r["ready"] is True
    assert r["switch_target"] == SWITCH_TARGET_APPROVALS
    assert _cond(r, "no_candidate_sha256_used_as_activation_authority") is True


def test_candidate_reference_recorded_but_not_gating() -> None:
    base = _baseline()
    wa = _approvals_registry(
        [{"approval_id": "APR-1", "decision": "approve", "candidate_id": "CAND-1",
          "candidate_sha256": "abc", "operator_completed_anchor": _anchor(),
          "operator_completed_anchor_sha256": sha(_anchor())}],
        baseline=base,
    )
    r = _evaluate(baseline=base, approvals_reg=wa)
    assert r["ready"] is True
    row = [a for a in wa["active_anchors"] if a["anchor_id"] == "AI_CAPEX"][0]
    assert row["approval_type"] == "operator_approved_candidate"


# --- 13. no order / permission fields ----------------------------------------


def test_no_order_shaped_fields() -> None:
    base = _baseline()
    r = _evaluate(baseline=base, approvals_reg=_approvals_registry([_approval()], baseline=base))
    present = {k for k in _keys(r) if k.lower() in _ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked: {present}"


def test_no_new_buy_or_order_compilation_grants() -> None:
    base = _baseline()
    r = _evaluate(baseline=base, approvals_reg=_approvals_registry([_approval()], baseline=base))
    blob = json.dumps(r)
    assert '"NEW_BUY"' not in blob
    assert '"ORDER_COMPILATION"' not in blob


# --- 15. never-raise ----------------------------------------------------------


def test_never_raises_on_garbage() -> None:
    r = evaluate_approval_registry_switch_readiness(
        baseline_registry="nonsense", approvals_registry=12345, dual_read_diff=[],
        current_research_anchors_sha256=None, current_research_anchor_approvals_sha256=None,
        approvals_source_present=False,
    )
    assert r["schema_version"] == SCHEMA_VERSION
    assert r["ready"] is False
    assert r["switch_target"] == SWITCH_TARGET_FAIL_CLOSED


def test_json_serializable() -> None:
    base = _baseline()
    r = _evaluate(baseline=base, approvals_reg=_approvals_registry([_approval()], baseline=base))
    assert json.loads(json.dumps(r))["schema_version"] == SCHEMA_VERSION


# --- unknown nested fields invalidate the closed combined source
def test_unknown_operator_anchor_field_fails_combined_source_closed() -> None:
    base = _baseline()
    wa = _approvals_registry([_approval(_anchor("BAD", order_intent="buy"))], baseline=base)
    assert wa["registry_valid"] is False
    r = _evaluate(baseline=base, approvals_reg=wa)
    assert r["ready"] is False
    assert "BAD" not in [a["anchor_id"] for a in wa["active_anchors"]]
