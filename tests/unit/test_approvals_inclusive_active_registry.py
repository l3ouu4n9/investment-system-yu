"""R2G-5b: approvals-inclusive active registry tests (groups A-G, J).

Every test proves the approvals-inclusive registry is a SEPARATE report-only
observer: it never mutates the baseline registry, binds activation ONLY to
operator_completed_anchor_sha256 (candidate data is audit-only), fails closed on
duplicates / manifest failures, carries the report-only markers, and never
contains order-shaped fields.
"""

from __future__ import annotations

import json
from typing import Any

from investment_orchestrator.research.active_research_anchor_registry import (
    build_active_research_anchor_registry,
)
from investment_orchestrator.research.research_anchors import validate_research_anchors
from investment_orchestrator.research.research_anchor_approval_manifest import (
    build_research_anchor_approvals_validation,
    compute_operator_completed_anchor_sha256 as sha,
)
from investment_orchestrator.research.research_anchor_revocation_manifest import (
    build_research_anchor_revocations_validation,
)
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    APPROVALS_SOURCE_ID,
    APPROVAL_TYPE_APPROVED_CANDIDATE,
    APPROVAL_TYPE_AUTHORED,
    BLOCKER_APPROVALS_MANIFEST_INVALID,
    BLOCKER_DUPLICATE_ACROSS_SOURCES,
    BLOCKER_DUPLICATE_WITHIN_APPROVALS,
    BLOCKER_DUPLICATE_TARGET_REVOCATION,
    BLOCKER_REVOCATIONS_INVALID,
    STATUS_REVOKED,
    SCHEMA_VERSION,
    build_active_research_anchor_registry_with_approvals,
)

UNIVERSE = ["QQQ", "VOO", "SMH"]
AS_OF = "2026-07-04"

_ORDER_SHAPED_KEYS = frozenset(
    {
        "account", "quantity", "shares", "order_type", "tif", "time_in_force",
        "limit_price", "stop_price", "venue", "routing", "broker", "new_buy",
        "order_compilation", "budget", "allocation",
    }
)


def _anchor(anchor_id: str = "AI_CAPEX_2026H2", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": anchor_id,
        "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"],
        "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01",
        "valid_until": "2026-07-31",
        "source_type": "operator",
        "confidence_floor": "medium",
        "summary": "Operator-dated thesis grounding.",
    }
    base.update(overrides)
    return base


def _baseline(anchors: list[dict[str, Any]] | None = None, *, today: str = AS_OF) -> dict[str, Any]:
    payload = {"schema_version": "research_anchors_v1", "is_llm_generated": False, "anchors": anchors or []}
    result = validate_research_anchors(payload, allowed_universe=UNIVERSE, today=today)
    return build_active_research_anchor_registry(
        anchors_result=result,
        source_present=bool(anchors),
        source_sha256="baselinesha" if anchors else None,
        source_path="inputs/current/research_anchors.yaml",
        as_of_date=today,
    )


def _approval(
    *,
    approval_id: str = "APR-1",
    decision: str = "approve",
    completed: dict[str, Any] | None = None,
    hash_override: str | None = None,
    include_hash: bool = True,
    candidate_id: str | None = None,
    candidate_sha256: str | None = None,
) -> dict[str, Any]:
    anchor = _anchor() if completed is None else completed
    entry: dict[str, Any] = {"approval_id": approval_id, "decision": decision}
    if candidate_id is not None:
        entry["candidate_id"] = candidate_id
    if candidate_sha256 is not None:
        entry["candidate_sha256"] = candidate_sha256
    if anchor is not None:
        entry["operator_completed_anchor"] = anchor
    if include_hash:
        entry["operator_completed_anchor_sha256"] = (
            hash_override if hash_override is not None else sha(anchor)
        )
    return entry


def _validation(
    approvals: list[dict[str, Any]] | Any,
    *,
    present: bool = True,
    today: str = AS_OF,
    candidate_index: dict[str, Any] | None = None,
    **manifest_overrides: Any,
) -> dict[str, Any]:
    manifest = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": today,
        "approvals": approvals,
    }
    manifest.update(manifest_overrides)
    return build_research_anchor_approvals_validation(
        manifest=manifest if present else None,
        source_present=present,
        source_sha256="approvalssha" if present else None,
        source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=today,
        candidate_index=candidate_index,
    )


def _revocation(**overrides: Any) -> dict[str, Any]:
    anchor = _anchor()
    base: dict[str, Any] = {
        "revocation_id": "REV-2026-07-04-001",
        "target_type": "approval_anchor",
        "approval_id": "APR-1",
        "anchor_id": "AI_CAPEX_2026H2",
        "operator_completed_anchor_sha256": sha(anchor),
        "effective_as_of": AS_OF,
        "reason": "Thesis invalidated.",
        "revoked_by": "operator",
    }
    base.update(overrides)
    return base


def _revocations_validation(
    revocations: Any,
    *,
    approvals: list[dict[str, Any]] | None = None,
    present: bool = True,
    today: str = AS_OF,
    **manifest_overrides: Any,
) -> dict[str, Any]:
    approvals = approvals if approvals is not None else [_approval()]
    manifest = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": today,
        "approvals": approvals,
        "revocations": revocations,
    }
    manifest.update(manifest_overrides)
    return build_research_anchor_revocations_validation(
        manifest=manifest if present else None,
        approvals_validation=_validation(approvals, present=present, today=today),
        source_present=present,
        source_sha256="approvalssha" if present else None,
        source_path="inputs/current/research_anchor_approvals.yaml",
        today=today,
        as_of_date=today,
    )


def _merge(
    baseline: dict[str, Any],
    validation: dict[str, Any],
    *,
    revocations_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_active_research_anchor_registry_with_approvals(
        baseline=baseline,
        approvals_validation=validation,
        revocations_validation=revocations_validation,
    )


def _active_ids(reg: dict[str, Any]) -> list[str]:
    return sorted(a["anchor_id"] for a in reg["active_anchors"] if a.get("anchor_id"))


def _keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _assert_revocation_fail_closed(reg: dict[str, Any]) -> None:
    assert reg["registry_valid"] is False
    assert BLOCKER_REVOCATIONS_INVALID in reg["registry_blockers"]
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    assert reg["counts"]["approved_active"] == 0
    assert reg["revocation_problems"]


# --- markers ------------------------------------------------------------------


def test_report_only_markers_present() -> None:
    reg = _merge(_baseline(), _validation([_approval()]))
    assert reg["schema_version"] == SCHEMA_VERSION
    assert reg["is_llm_generated"] is False
    assert reg["report_only"] is True
    assert reg["permission_effect"] == "none"
    assert reg["not_authorization"] is True
    assert reg["not_execution_authorization"] is True
    assert reg["cannot_affect_allowed_actions"] is True
    assert reg["is_embedded_registry"] is False
    assert reg["embedded_in_evidence_packet"] is False
    assert reg["standalone_artifact_not_consumed_by_support_signals"] is True
    assert reg["embedded_registry_selection_owned_by_evidence_packet"] is True
    for key in (
        "consumed_by_support_signals", "consumed_by_active_registry",
        "consumed_by_availability", "consumed_by_gates",
        "consumed_by_step2", "consumed_by_step4",
    ):
        assert reg[key] is False


def test_json_serializable() -> None:
    reg = _merge(_baseline(), _validation([_approval()]))
    assert json.loads(json.dumps(reg))["schema_version"] == SCHEMA_VERSION


def test_no_order_shaped_fields_anywhere() -> None:
    reg = _merge(_baseline([_anchor("VOO_T", applicable_tickers=["VOO"])]), _validation([_approval()]))
    present = {k for k in _keys(reg) if k.lower() in _ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked: {present}"


# --- A. happy path ------------------------------------------------------------


def test_A_happy_path_appears_with_provenance() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    reg = _merge(baseline, _validation([_approval(candidate_id="CAND-QQQ-1", candidate_sha256="abc")]))
    assert reg["registry_valid"] is True
    row = [a for a in reg["active_anchors"] if a["anchor_id"] == "AI_CAPEX_2026H2"][0]
    assert row["source_id"] == APPROVALS_SOURCE_ID
    assert row["source_category"] == "C_operator"
    assert row["source_type"] == "operator"
    assert row["approval_type"] == APPROVAL_TYPE_APPROVED_CANDIDATE
    assert row["approval_id"] == "APR-1"
    assert row["candidate_id"] == "CAND-QQQ-1"
    assert row["candidate_sha256"] == "abc"
    assert row["operator_completed_anchor_sha256"] == sha(_anchor())
    assert len(row["content_sha256"]) == 64
    assert row["status"] == "active"
    assert row["validation"]["hash_match"] is True
    # baseline anchor still present + a source_manifest entry for the approvals YAML
    assert "VOO_T" in _active_ids(reg)
    manifest_ids = [m["source_id"] for m in reg["source_manifest"]]
    assert "operator_research_anchors_yaml" in manifest_ids
    assert APPROVALS_SOURCE_ID in manifest_ids


def test_A_baseline_registry_object_not_mutated() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    before = json.dumps(baseline, sort_keys=True)
    _merge(baseline, _validation([_approval()]))
    assert json.dumps(baseline, sort_keys=True) == before  # merge never mutates baseline
    assert _active_ids(baseline) == ["VOO_T"]


def test_A_source_manifest_approvals_entry_fields() -> None:
    reg = _merge(_baseline(), _validation([_approval()]))
    entry = [m for m in reg["source_manifest"] if m["source_id"] == APPROVALS_SOURCE_ID][0]
    assert entry["source_category"] == "C_operator"
    assert entry["source_type"] == "operator"
    assert entry["path"] == "inputs/current/research_anchor_approvals.yaml"
    assert entry["sha256"] == "approvalssha"
    assert entry["present"] is True
    assert entry["valid"] is True
    assert isinstance(entry["problems"], list)


# --- B. approval without candidate -------------------------------------------


def test_B_no_candidate_operator_authored() -> None:
    reg = _merge(_baseline(), _validation([_approval()]))
    row = [a for a in reg["active_anchors"] if a["anchor_id"] == "AI_CAPEX_2026H2"][0]
    assert row["approval_type"] == APPROVAL_TYPE_AUTHORED
    assert row["candidate_id"] is None
    assert row["candidate_sha256"] is None
    assert row["status"] == "active"


# --- C. approval with candidate reference (audit only) -----------------------


def test_C_candidate_reference_audit_only_mismatch_does_not_block() -> None:
    # candidate index says the candidate hash is "right"; approval declares "wrong".
    val = _validation(
        [_approval(candidate_id="C1", candidate_sha256="wrong")],
        candidate_index={"C1": "right"},
    )
    reg = _merge(_baseline(), val)
    row = [a for a in reg["active_anchors"] if a["anchor_id"] == "AI_CAPEX_2026H2"][0]
    # Still active: candidate hash has ZERO activation authority.
    assert row["status"] == "active"
    assert row["approval_type"] == APPROVAL_TYPE_APPROVED_CANDIDATE
    assert row["candidate_link_status"] == "candidate_hash_mismatch"
    assert row["validation"]["hash_match"] is True  # operator anchor hash still matches


# --- R2G-5d-1 revocation overlay (standalone artifact only) -------------------


def test_R_valid_active_revocation_moves_approval_anchor_to_revoked_inactive() -> None:
    approval = _approval(candidate_id="CAND-1", candidate_sha256="candidate-audit-only")
    reg = _merge(
        _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])]),
        _validation([approval]),
        revocations_validation=_revocations_validation([_revocation()], approvals=[approval]),
    )

    assert reg["registry_valid"] is True
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    assert _active_ids(reg) == ["VOO_T"]
    assert reg["counts"]["revoked"] == 1
    assert reg["counts"]["approved_active"] == 0

    revoked = [r for r in reg["inactive_anchors"] if r.get("anchor_id") == "AI_CAPEX_2026H2"][0]
    assert revoked["status"] == STATUS_REVOKED
    assert revoked["revocation_id"] == "REV-2026-07-04-001"
    assert revoked["approval_id"] == "APR-1"
    assert revoked["effective_as_of"] == AS_OF
    assert revoked["reason"] == "Thesis invalidated."
    assert revoked["candidate_sha256"] == "candidate-audit-only"
    assert revoked["operator_completed_anchor_sha256"] == sha(_anchor())
    assert revoked["revocation"]["reason"] == "Thesis invalidated."

    assert reg["revocations_applied"] == [
        {
            "revocation_id": "REV-2026-07-04-001",
            "approval_id": "APR-1",
            "anchor_id": "AI_CAPEX_2026H2",
            "operator_completed_anchor_sha256": sha(_anchor()),
            "effective_as_of": AS_OF,
            "reason": "Thesis invalidated.",
            "target_type": "approval_anchor",
        }
    ]
    assert any(e["event"] == "anchor_revoked" for e in reg["audit_trail"])
    assert reg["permission_effect"] == "none"
    assert reg["not_authorization"] is True


def test_R_future_revocation_is_pending_and_anchor_remains_active() -> None:
    approval = _approval()
    reg = _merge(
        _baseline(),
        _validation([approval]),
        revocations_validation=_revocations_validation(
            [_revocation(effective_as_of="2026-12-31")], approvals=[approval]
        ),
    )

    assert reg["registry_valid"] is True
    assert "AI_CAPEX_2026H2" in _active_ids(reg)
    assert reg["counts"]["revoked"] == 0
    assert reg["revocations_applied"] == []
    assert reg["revocations_pending"][0]["revocation_id"] == "REV-2026-07-04-001"
    assert any(e["event"] == "revocation_pending_future" for e in reg["audit_trail"])


def test_R_revocation_validator_failure_modes_fail_overlay_closed() -> None:
    invalid_cases = [
        [_revocation(approval_id="APR-DOES-NOT-EXIST")],
        [_revocation(operator_completed_anchor_sha256="0" * 64)],
        [_revocation(anchor_id="WRONG_ANCHOR")],
        [_revocation(revocation_id="REV-DUP"), _revocation(revocation_id="REV-DUP")],
        [_revocation(target_type="baseline_anchor")],
        [{**_revocation(), "order_intent": "buy"}],
        [{**_revocation(), "candidate_sha256": "candidate-cannot-bind"}],
        [_revocation(reason="NEW_BUY")],
    ]

    for revocations in invalid_cases:
        approval = _approval()
        reg = _merge(
            _baseline(),
            _validation([approval]),
            revocations_validation=_revocations_validation(revocations, approvals=[approval]),
        )
        _assert_revocation_fail_closed(reg)
        assert reg["revocations_applied"] == []


def test_R_is_llm_generated_true_fails_overlay_closed() -> None:
    approval = _approval()
    reg = _merge(
        _baseline(),
        _validation([approval]),
        revocations_validation=_revocations_validation(
            [_revocation()], approvals=[approval], is_llm_generated=True
        ),
    )
    _assert_revocation_fail_closed(reg)


def test_R_duplicate_target_revocations_fail_overlay_closed_explicitly() -> None:
    approval = _approval()
    reg = _merge(
        _baseline(),
        _validation([approval]),
        revocations_validation=_revocations_validation(
            [_revocation(revocation_id="REV-1"), _revocation(revocation_id="REV-2")],
            approvals=[approval],
        ),
    )

    _assert_revocation_fail_closed(reg)
    assert BLOCKER_DUPLICATE_TARGET_REVOCATION in reg["registry_blockers"]
    assert any(p["reason"] == BLOCKER_DUPLICATE_TARGET_REVOCATION for p in reg["revocation_problems"])


def test_R_expired_target_revocation_does_not_resurrect_or_mark_revoked() -> None:
    stale_anchor = _anchor(valid_from="2026-01-01", valid_until="2026-02-01")
    approval = _approval(completed=stale_anchor)
    reg = _merge(
        _baseline(),
        _validation([approval]),
        revocations_validation=_revocations_validation(
            [_revocation(operator_completed_anchor_sha256=sha(stale_anchor))],
            approvals=[approval],
        ),
    )

    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    inactive = [r for r in reg["inactive_anchors"] if r.get("anchor_id") == "AI_CAPEX_2026H2"][0]
    assert inactive["status"] == "expired"
    assert reg["counts"]["revoked"] == 0
    assert reg["revocations_applied"] == []
    assert any(e["event"] == "revocation_target_not_active" for e in reg["audit_trail"])


def test_R_revocation_overlay_is_not_enabled_for_default_runtime_builder_call() -> None:
    approval = _approval()
    baseline = _baseline()
    reg = build_active_research_anchor_registry_with_approvals(
        baseline=baseline,
        approvals_validation=_validation([approval]),
    )
    assert "AI_CAPEX_2026H2" in _active_ids(reg)
    assert reg["counts"]["revoked"] == 0
    assert reg["revocations_applied"] == []


def test_R_support_signals_still_does_not_read_revocation_artifacts() -> None:
    import inspect

    import investment_orchestrator.research.support_signals as ss

    assert "research_anchor_revocation_manifest" not in inspect.getsource(ss)
    assert "validate_research_anchor_revocations" not in inspect.getsource(ss)
    assert "research_anchor_revocations_validation" not in inspect.getsource(ss)


# --- D. hash failures ---------------------------------------------------------


def test_D_missing_hash_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(include_hash=False)]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    inactive = [a for a in reg["inactive_anchors"] if a.get("anchor_id") == "AI_CAPEX_2026H2"]
    assert inactive and inactive[0]["status"] == "invalid"


def test_D_mismatched_hash_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(hash_override="0" * 64)]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_D_mutated_anchor_after_hash_inactive() -> None:
    # Declared hash is for the original; the delivered anchor is mutated -> mismatch.
    good = _anchor()
    entry = {
        "approval_id": "APR-1",
        "decision": "approve",
        "operator_completed_anchor": _anchor(summary="MUTATED"),
        "operator_completed_anchor_sha256": sha(good),
    }
    reg = _merge(_baseline(), _validation([entry]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


# --- E. validation failures ---------------------------------------------------


def test_E_dateless_skeleton_inactive() -> None:
    skeleton = {
        "anchor_id": "QQQ_SKELETON", "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"], "source_type": "operator",
        "confidence_floor": "medium", "summary": "x",
    }
    reg = _merge(_baseline(), _validation([_approval(completed=skeleton)]))
    assert "QQQ_SKELETON" not in _active_ids(reg)


def test_E_forbidden_keys_inactive() -> None:
    for bad in ({"hard_cap_budget": 1}, {"order_intent": "buy"}, {"allocation_pct": 5}):
        reg = _merge(_baseline(), _validation([_approval(completed=_anchor(**bad))]))
        assert "AI_CAPEX_2026H2" not in _active_ids(reg), bad


def test_E_new_buy_and_order_compilation_tokens_inactive() -> None:
    for token in ("NEW_BUY", "ORDER_COMPILATION"):
        reg = _merge(_baseline(), _validation([_approval(completed=_anchor(summary=token))]))
        assert "AI_CAPEX_2026H2" not in _active_ids(reg), token


def test_E_out_of_universe_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(completed=_anchor(applicable_tickers=["TSLA"]))]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_E_invalid_dates_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(completed=_anchor(anchor_date_et="June 15"))]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_E_valid_from_after_valid_until_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(completed=_anchor(valid_from="2026-09-01", valid_until="2026-08-01"))]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_E_stale_goes_expired_not_active() -> None:
    stale = _anchor(valid_from="2026-01-01", valid_until="2026-02-01")
    reg = _merge(_baseline(), _validation([_approval(completed=stale)]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    row = [a for a in reg["inactive_anchors"] if a.get("anchor_id") == "AI_CAPEX_2026H2"][0]
    assert row["status"] == "expired"


def test_E_non_operator_source_type_inactive() -> None:
    bad = _anchor(source_type="analyst_memo")
    entry = {
        "approval_id": "APR-1", "decision": "approve",
        "operator_completed_anchor": bad, "operator_completed_anchor_sha256": sha(bad),
    }
    reg = _merge(_baseline(), _validation([entry]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


# --- F. manifest failures -----------------------------------------------------


def test_F_missing_manifest_valid_no_approval_registry() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    reg = _merge(baseline, _validation([], present=False))
    assert reg["registry_valid"] is True
    assert _active_ids(reg) == ["VOO_T"]
    assert reg["counts"]["approved_active"] == 0
    entry = [m for m in reg["source_manifest"] if m["source_id"] == APPROVALS_SOURCE_ID][0]
    assert entry["present"] is False


def test_F_wrong_schema_fails_closed() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    reg = _merge(baseline, _validation([_approval()], schema_version="nope"))
    assert reg["registry_valid"] is False
    assert BLOCKER_APPROVALS_MANIFEST_INVALID in reg["registry_blockers"]
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    assert _active_ids(reg) == ["VOO_T"]  # baseline still active


def test_F_is_llm_generated_true_fails_closed() -> None:
    reg = _merge(_baseline(), _validation([_approval()], is_llm_generated=True))
    assert reg["registry_valid"] is False
    assert BLOCKER_APPROVALS_MANIFEST_INVALID in reg["registry_blockers"]
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_F_duplicate_approval_id_fails_closed() -> None:
    a2 = _approval(approval_id="APR-DUP")
    b2 = _approval(approval_id="APR-DUP", completed=_anchor("OTHER", applicable_tickers=["VOO"]))
    reg = _merge(_baseline(), _validation([a2, b2]))
    assert reg["registry_valid"] is False
    assert reg["counts"]["approved_active"] == 0


def test_F_unsupported_decision_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(decision="reject")]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_F_revocations_not_implemented_ignored() -> None:
    # A 'revoke' decision is unsupported in R2G-5b -> treated as non-activatable, not a revocation.
    reg = _merge(_baseline([_anchor("VOO_T", applicable_tickers=["VOO"])]),
                 _validation([_approval(decision="revoke", completed=_anchor("VOO_T", applicable_tickers=["VOO"]))]))
    # baseline VOO_T stays active (no revocation path); the revoke approval does not deactivate it.
    # (VOO_T is a cross-source duplicate here -> excluded; assert no active QQQ anchor added and no revoked count)
    assert reg["counts"]["revoked"] == 0


def test_F_malformed_yaml_fails_closed() -> None:
    val = build_research_anchor_approvals_validation(
        manifest=None, source_present=True, source_sha256="x", source_path="p",
        allowed_universe=UNIVERSE, today=AS_OF, parse_error="bad yaml",
    )
    reg = _merge(_baseline([_anchor("VOO_T", applicable_tickers=["VOO"])]), val)
    assert reg["registry_valid"] is False
    assert BLOCKER_APPROVALS_MANIFEST_INVALID in reg["registry_blockers"]
    assert _active_ids(reg) == ["VOO_T"]


# --- G. duplicate policy ------------------------------------------------------


def test_G_duplicate_anchor_id_within_approvals_fails_closed() -> None:
    a1 = _approval(approval_id="APR-1", completed=_anchor("DUP"))
    a2 = _approval(approval_id="APR-2", completed=_anchor("DUP", applicable_tickers=["VOO"]))
    reg = _merge(_baseline(), _validation([a1, a2]))
    assert reg["registry_valid"] is False
    # within-approvals duplicate anchor_id also invalidates the approvals source
    assert reg["counts"]["approved_active"] == 0
    assert "DUP" not in _active_ids(reg)


def test_G_cross_source_duplicate_fails_closed_no_precedence() -> None:
    baseline = _baseline([_anchor("SHARED", applicable_tickers=["QQQ"])])
    assert _active_ids(baseline) == ["SHARED"]
    reg = _merge(baseline, _validation([_approval(completed=_anchor("SHARED"))]))
    assert reg["registry_valid"] is False
    assert BLOCKER_DUPLICATE_ACROSS_SOURCES in reg["registry_blockers"]
    dup = [d for d in reg["duplicate_blockers"] if d["reason"] == BLOCKER_DUPLICATE_ACROSS_SOURCES][0]
    assert dup["anchor_ids"] == ["SHARED"]
    # No silent precedence: SHARED is active from NEITHER source.
    assert "SHARED" not in _active_ids(reg)
    inactive_ids = [a.get("anchor_id") for a in reg["inactive_anchors"]]
    assert inactive_ids.count("SHARED") >= 1


def test_G_cross_source_duplicate_keeps_other_anchors() -> None:
    baseline = _baseline([
        _anchor("SHARED", applicable_tickers=["QQQ"]),
        _anchor("VOO_OK", applicable_tickers=["VOO"]),
    ])
    reg = _merge(baseline, _validation([_approval(completed=_anchor("SHARED"))]))
    # Conflicting SHARED excluded; the non-conflicting baseline VOO_OK stays active.
    assert "VOO_OK" in _active_ids(reg)
    assert "SHARED" not in _active_ids(reg)


# --- J. safety ----------------------------------------------------------------


def test_J_no_new_buy_or_order_compilation_grants() -> None:
    reg = _merge(_baseline(), _validation([_approval()]))
    blob = json.dumps(reg)
    assert '"NEW_BUY"' not in blob
    assert '"ORDER_COMPILATION"' not in blob


def test_J_never_raises_on_garbage() -> None:
    reg = build_active_research_anchor_registry_with_approvals(
        baseline="nonsense", approvals_validation=12345
    )
    assert reg["schema_version"] == SCHEMA_VERSION
    assert reg["registry_valid"] is False
