"""R2G-5c-1: support_signals dual-ground DRY-RUN diff tests.

Every test proves the dry-run is inert and bounded: it reuses the REAL
build_compiled_support_signals, never grounds via candidate data, fails closed on
an invalid/duplicate registry, keeps operator_completed_anchor_sha256 as the only
activation-binding hash, and carries no order-shaped / grant fields. It changes NO
production consumer.
"""

from __future__ import annotations

import json
from typing import Any

import yaml

import investment_orchestrator.research.support_signals_dual_ground_diff as dual_module
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
from investment_orchestrator.research.support_signals_dual_ground_diff import (
    SCHEMA_VERSION,
    build_support_signals_dual_ground_diff,
    write_support_signals_dual_ground_diff,
)

UNIVERSE = ["QQQ", "VOO", "SMH"]
AS_OF = "2026-07-04"
ANCHORS_SHA = "current_anchors_sha"
APPROVALS_SHA = "current_approvals_sha"

_ORDER_SHAPED_KEYS = frozenset(
    {
        "account", "quantity", "shares", "order_type", "tif", "time_in_force",
        "limit_price", "stop_price", "venue", "routing", "broker", "new_buy",
        "order_compilation", "budget", "allocation",
    }
)


def _anchor(anchor_id: str, ticker: str = "QQQ", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": anchor_id, "anchor_type": "structural_theme",
        "applicable_tickers": [ticker], "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01", "valid_until": "2026-07-31",
        "source_type": "operator", "confidence_floor": "medium", "summary": "x",
    }
    base.update(overrides)
    return base


def _baseline_registry(anchors: list[dict[str, Any]], *, source_sha: str = ANCHORS_SHA) -> dict[str, Any]:
    result = validate_research_anchors(
        {
            "schema_version": "research_anchors_v1",
            "as_of_date": AS_OF,
            "is_llm_generated": False,
            "anchors": anchors,
        },
        allowed_universe=UNIVERSE, today=AS_OF,
    )
    return build_active_research_anchor_registry(
        anchors_result=result, source_present=True, source_sha256=source_sha,
        source_path="inputs/current/research_anchors.yaml", as_of_date=AS_OF,
    )


def _approvals_registry(approvals: list[dict[str, Any]], baseline: dict[str, Any], *,
                        present: bool = True, source_sha: str = APPROVALS_SHA,
                        candidate_index: dict[str, Any] | None = None, **manifest_overrides: Any) -> dict[str, Any]:
    manifest = {"schema_version": "research_anchor_approvals_v1", "is_llm_generated": False,
                "as_of_date": AS_OF, "approvals": approvals}
    manifest.update(manifest_overrides)
    source_text = yaml.safe_dump(manifest, sort_keys=False) if present else None
    return build_active_research_anchor_registry_with_approvals(
        baseline=baseline,
        approval_source_text=source_text,
        approval_source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=AS_OF,
        candidate_index=candidate_index,
    )


def _approval(anchor: dict[str, Any], *, approval_id: str = "APR-1", hash_override: str | None = None,
              candidate_id: str | None = None, candidate_sha256: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"approval_id": approval_id, "decision": "approve",
                             "operator_completed_anchor": anchor,
                             "operator_completed_anchor_sha256": hash_override if hash_override is not None else sha(anchor)}
    if candidate_id is not None:
        entry["candidate_id"] = candidate_id
    if candidate_sha256 is not None:
        entry["candidate_sha256"] = candidate_sha256
    return entry


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


def _packet(registry: dict[str, Any]) -> dict[str, Any]:
    return {"universe": {"allowed_buy_tickers": list(UNIVERSE), "approved_extended_etf": []},
            "active_anchor_registry": registry}


def _memo(rows: list[dict[str, Any]], *, confidence: str = "high") -> dict[str, Any]:
    return {"schema_version": "analyst_memo_v1", "is_llm_generated": True, "confidence": confidence,
            "source_notes": [{"url": "u", "claim": "c"}], "ticker_relative_view": rows}


def _row(ticker: str, anchor_ref: str, *, stance: str = "prefer") -> dict[str, Any]:
    return {"ticker": ticker, "stance": stance, "rationale_12m_plus": f"{ticker} 12m+ thesis",
            "anchor_id_refs": [anchor_ref]}


def _run(*, baseline: dict[str, Any], approvals_reg: dict[str, Any], memo: dict[str, Any],
         anchors_sha: str = ANCHORS_SHA, approvals_sha: str = APPROVALS_SHA,
         present: bool = True, mode: str = "evidence_plus_memo") -> dict[str, Any]:
    diff = build_approval_registry_dual_read_diff(baseline_registry=baseline, approvals_registry=approvals_reg)
    if approvals_sha == APPROVALS_SHA:
        approvals_sha = next(
            (
                row.get("sha256")
                for row in approvals_reg.get("source_manifest", [])
                if row.get("source_id") == "operator_research_anchor_approvals_yaml"
            ),
            None,
        )
    return build_support_signals_dual_ground_diff(
        evidence_packet=_packet(baseline), analyst_memo=memo, compilation_mode=mode,
        approvals_registry=approvals_reg, dual_read_diff=diff,
        current_research_anchors_sha256=anchors_sha, current_research_anchor_approvals_sha256=approvals_sha,
        approvals_source_present=present,
    )


def _keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


# --- markers ------------------------------------------------------------------


def test_markers_and_non_consumption() -> None:
    base = _baseline_registry([_anchor("VOO_B", "VOO")])
    wa = _approvals_registry([_approval(_anchor("QQQ_A"))], base)
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")]))
    assert r["schema_version"] == SCHEMA_VERSION
    assert r["is_llm_generated"] is False
    assert r["report_only"] is True
    assert r["dry_run_only"] is True
    assert r["permission_effect"] == "none"
    assert r["not_authorization"] is True
    assert r["not_execution_authorization"] is True
    assert r["cannot_affect_allowed_actions"] is True
    assert r["support_signals_runtime_unchanged"] is True
    assert r["evidence_packet_runtime_unchanged"] is True
    assert r["candidate_sources_used_for_grounding"] is False
    assert r["candidate_sha256_used_as_activation_authority"] is False
    for k in ("consumed_by_support_signals", "consumed_by_active_registry", "consumed_by_availability",
              "consumed_by_gates", "consumed_by_step2", "consumed_by_step4"):
        assert r[k] is False


# --- 1. empty approvals -------------------------------------------------------


def test_empty_approvals_equivalent_to_baseline() -> None:
    base = _baseline_registry([_anchor("VOO_B", "VOO")])
    wa = _approvals_registry([], base)
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("VOO", "VOO_B")]))
    assert r["baseline_accepted_signal_ids"] == r["approvals_inclusive_accepted_signal_ids"] == ["VOO"]
    assert r["added_by_approvals"] == []
    assert r["bounded_broadening_passed"] is True


# --- 2. one valid operator-approved anchor -----------------------------------


def test_valid_approval_adds_signal_only_if_referenced() -> None:
    base = _baseline_registry([_anchor("VOO_B", "VOO")])
    wa = _approvals_registry([_approval(_anchor("QQQ_A"))], base)
    # memo references the approved QQQ anchor -> QQQ becomes newly groundable.
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("VOO", "VOO_B"), _row("QQQ", "QQQ_A")]))
    assert r["added_by_approvals"] == ["QQQ"]
    assert "VOO" in r["unchanged"]
    assert r["bounded_broadening_passed"] is True
    exp = [e for e in r["explanations"] if e["signal_id"] == "QQQ"][0]
    assert exp["explanation"] == "operator_approved_anchor"
    assert exp["ok"] is True
    assert exp["operator_completed_anchor_sha256"] == sha(_anchor("QQQ_A"))


def test_approved_anchor_not_referenced_no_broadening() -> None:
    base = _baseline_registry([_anchor("VOO_B", "VOO")])
    wa = _approvals_registry([_approval(_anchor("QQQ_A"))], base)
    # memo does NOT reference the approved anchor -> no new signal.
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("VOO", "VOO_B")]))
    assert r["added_by_approvals"] == []
    assert r["bounded_broadening_passed"] is True


# --- 3. approval without candidate -------------------------------------------


def test_approval_without_candidate_operator_authored() -> None:
    base = _baseline_registry([])
    wa = _approvals_registry([_approval(_anchor("QQQ_A"))], base)
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")]))
    exp = [e for e in r["explanations"] if e["signal_id"] == "QQQ"][0]
    assert exp["approval_type"] == "operator_authored"
    assert exp["ok"] is True
    assert exp.get("candidate_id_audit_only") is None


# --- 4. candidate mismatch (audit-only) --------------------------------------


def test_candidate_mismatch_still_grounds_audit_only() -> None:
    base = _baseline_registry([])
    wa = _approvals_registry(
        [_approval(_anchor("QQQ_A"), candidate_id="C1", candidate_sha256="wrong")],
        base, candidate_index={"C1": "right"},
    )
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")]))
    assert r["added_by_approvals"] == ["QQQ"]
    assert r["bounded_broadening_passed"] is True
    exp = [e for e in r["explanations"] if e["signal_id"] == "QQQ"][0]
    assert exp["explanation"] == "operator_approved_anchor"
    assert exp["candidate_link_status_audit_only"] == "candidate_hash_mismatch"
    # candidate mismatch does NOT gate grounding.
    assert exp["ok"] is True


# --- 5. candidate-only source cannot ground ----------------------------------


def test_candidate_only_source_cannot_ground() -> None:
    # A B_candidate_only row injected into active_anchors must never explain a signal.
    base = _baseline_registry([])
    wa = _approvals_registry([_approval(_anchor("QQQ_A"))], base)
    wa = json.loads(json.dumps(wa))
    # Corrupt the active approval row to look like a candidate-only source.
    for row in wa["active_anchors"]:
        if row.get("source_id") == "operator_research_anchor_approvals_yaml":
            row["source_category"] = "B_candidate_only"
            row["operator_completed_anchor_sha256"] = None
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")]))
    # The dry-run projection downgrades the defense-failing row -> not groundable.
    assert r["added_by_approvals"] == []
    assert r["bounded_broadening_passed"] is True


# --- 6. hash mismatch ---------------------------------------------------------


def test_hash_mismatch_approval_inactive_no_broadening() -> None:
    base = _baseline_registry([])
    # hash mismatch -> the R2G-5b compiler marks the approval inactive.
    wa = _approvals_registry([_approval(_anchor("QQQ_A"), hash_override="0" * 64)], base)
    assert "QQQ_A" not in [a["anchor_id"] for a in wa["active_anchors"]]
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")]))
    assert r["added_by_approvals"] == []
    assert r["bounded_broadening_passed"] is True


# --- 7. stale / expired approval ---------------------------------------------


def test_stale_approval_not_groundable() -> None:
    base = _baseline_registry([])
    stale = _anchor("QQQ_A", valid_from="2026-01-01", valid_until="2026-02-01")
    wa = _approvals_registry([_approval(stale)], base)
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")]))
    assert r["added_by_approvals"] == []
    assert "QQQ" not in r["approvals_inclusive_accepted_signal_ids"]
    assert r["bounded_broadening_passed"] is True


# --- 8. duplicate / registry_valid:false -------------------------------------


def test_cross_source_duplicate_fails_closed_no_partial_read() -> None:
    # baseline has SHARED; approval also SHARED -> registry_valid:false -> baseline_fallback.
    base = _baseline_registry([_anchor("SHARED", "QQQ")])
    wa = _approvals_registry([_approval(_anchor("SHARED"))], base)
    assert wa["registry_valid"] is False
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "SHARED")]))
    # baseline_fallback: approvals-inclusive grounding == baseline (no partial read).
    assert r["readiness_summary"]["switch_target"] == "baseline_fallback"
    assert r["added_by_approvals"] == []
    assert r["baseline_accepted_signal_ids"] == r["approvals_inclusive_accepted_signal_ids"]
    assert r["bounded_broadening_passed"] is True


# --- 9. baseline invalid -> fail_closed_empty --------------------------------


def test_baseline_invalid_fail_closed_empty_zero_grounding() -> None:
    bad = _baseline_registry([_anchor("D"), _anchor("D")])  # duplicate -> registry_valid false
    assert bad["registry_valid"] is False
    wa = _approvals_registry([_approval(_anchor("QQQ_A"))], bad)
    r = _run(baseline=bad, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")]))
    assert r["readiness_summary"]["switch_target"] == "fail_closed_empty"
    assert r["approvals_inclusive_accepted_signal_ids"] == []  # zero usable anchors
    assert r["added_by_approvals"] == []
    assert r["bounded_broadening_passed"] is True


# --- 10. wrong schema / missing markers --------------------------------------


def test_wrong_schema_approvals_registry_fallback_baseline() -> None:
    base = _baseline_registry([_anchor("VOO_B", "VOO")])
    wa = _approvals_registry([_approval(_anchor("QQQ_A"))], base)
    wa = {**wa, "schema_version": "wrong"}
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("VOO", "VOO_B"), _row("QQQ", "QQQ_A")]))
    # readiness -> baseline_fallback; approvals grounding == baseline.
    assert r["readiness_summary"]["switch_target"] == "baseline_fallback"
    assert "QQQ" not in r["approvals_inclusive_accepted_signal_ids"]
    assert r["bounded_broadening_passed"] is True


# --- 11. _evaluate_anchor_refs still enforced --------------------------------


def test_wrong_ticker_applicability_not_grounded() -> None:
    base = _baseline_registry([])
    wa = _approvals_registry([_approval(_anchor("QQQ_A", ticker="QQQ"))], base)
    # memo references QQQ_A for VOO -> anchor not applicable to VOO.
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("VOO", "QQQ_A")]))
    assert r["added_by_approvals"] == []


def test_low_confidence_below_floor_not_grounded() -> None:
    base = _baseline_registry([])
    wa = _approvals_registry([_approval(_anchor("QQQ_A", confidence_floor="high"))], base)
    # memo confidence 'low' -> whole run blocked + floor not met.
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")], confidence="low"))
    assert r["added_by_approvals"] == []


def test_wrong_anchor_type_not_grounded() -> None:
    base = _baseline_registry([])
    # A bad anchor_type would fail validation -> not active -> not merged.
    wa = _approvals_registry([_approval(_anchor("QQQ_A", anchor_type="not_a_type"))], base)
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")]))
    assert r["added_by_approvals"] == []


# --- 13. safety ---------------------------------------------------------------


def test_no_order_shaped_fields() -> None:
    base = _baseline_registry([_anchor("VOO_B", "VOO")])
    wa = _approvals_registry([_approval(_anchor("QQQ_A"))], base)
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")]))
    present = {k for k in _keys(r) if k.lower() in _ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked: {present}"


def test_no_new_buy_or_order_compilation_grants() -> None:
    base = _baseline_registry([_anchor("VOO_B", "VOO")])
    wa = _approvals_registry([_approval(_anchor("QQQ_A"))], base)
    blob = json.dumps(_run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")])))
    assert '"NEW_BUY"' not in blob
    assert '"ORDER_COMPILATION"' not in blob


def test_never_raises_on_garbage() -> None:
    r = build_support_signals_dual_ground_diff(
        evidence_packet="nonsense", analyst_memo=123, compilation_mode="evidence_only",
        approvals_registry=[], dual_read_diff=None,
        current_research_anchors_sha256=None, current_research_anchor_approvals_sha256=None,
        approvals_source_present=False,
    )
    assert r["schema_version"] == SCHEMA_VERSION
    assert r["bounded_broadening_passed"] in (True, False)


def test_error_fallback_schema_remains_report_only(monkeypatch: Any) -> None:
    def boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("forced fallback")

    monkeypatch.setattr(dual_module, "_build", boom)

    r = build_support_signals_dual_ground_diff(
        evidence_packet={}, analyst_memo={}, compilation_mode="evidence_only",
        approvals_registry={}, dual_read_diff={},
        current_research_anchors_sha256=None, current_research_anchor_approvals_sha256=None,
        approvals_source_present=False,
    )

    assert r["report_only"] is True
    assert r["dry_run_only"] is True
    assert r["not_authorization"] is True
    failures = r["bounded_broadening_failures"]
    assert isinstance(failures, list)
    assert failures
    assert all(isinstance(entry, dict) for entry in failures)
    assert failures[0]["failure_id"] == "dual_ground_diff_internal_error"
    present = {k for k in _keys(r) if k.lower() in _ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked: {present}"
    blob = json.dumps(r)
    assert '"NEW_BUY"' not in blob
    assert '"ORDER_COMPILATION"' not in blob


def test_disk_writer_uses_revocation_aware_approvals_registry(tmp_path: Any) -> None:
    anchors_path = tmp_path / "research_anchors.yaml"
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    output_path = tmp_path / "support_signals_dual_ground_diff.json"
    baseline = _baseline_registry([_anchor("VOO_B", "VOO")])
    approved_anchor = _anchor("QQQ_A")
    anchors_path.write_text(
        json.dumps(
            {
                "schema_version": "research_anchors_v1",
                "is_llm_generated": False,
                "as_of_date": AS_OF,
                "anchors": [_anchor("VOO_B", "VOO")],
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
                "revocations": [_revocation(approved_anchor)],
            }
        ),
        encoding="utf-8",
    )

    summary = write_support_signals_dual_ground_diff(
        output_path=output_path,
        evidence_packet=_packet(baseline),
        analyst_memo=_memo([_row("QQQ", "QQQ_A")]),
        compilation_mode="evidence_plus_memo",
        anchors_path=anchors_path,
        approvals_path=approvals_path,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert summary["added_by_approvals_count"] == "0"
    assert payload["added_by_approvals"] == []
    assert payload["approvals_inclusive_accepted_signal_ids"] == []


def test_json_serializable() -> None:
    base = _baseline_registry([_anchor("VOO_B", "VOO")])
    wa = _approvals_registry([_approval(_anchor("QQQ_A"))], base)
    r = _run(baseline=base, approvals_reg=wa, memo=_memo([_row("QQQ", "QQQ_A")]))
    assert json.loads(json.dumps(r))["schema_version"] == SCHEMA_VERSION


def test_baseline_object_not_mutated() -> None:
    base = _baseline_registry([_anchor("VOO_B", "VOO")])
    wa = _approvals_registry([_approval(_anchor("QQQ_A"))], base)
    packet = _packet(base)
    before = json.dumps(packet, sort_keys=True)
    diff = build_approval_registry_dual_read_diff(baseline_registry=base, approvals_registry=wa)
    approvals_sha = next(
        row.get("sha256")
        for row in wa["source_manifest"]
        if row.get("source_id") == "operator_research_anchor_approvals_yaml"
    )
    build_support_signals_dual_ground_diff(
        evidence_packet=packet, analyst_memo=_memo([_row("QQQ", "QQQ_A")]),
        compilation_mode="evidence_plus_memo", approvals_registry=wa, dual_read_diff=diff,
        current_research_anchors_sha256=ANCHORS_SHA, current_research_anchor_approvals_sha256=approvals_sha,
        approvals_source_present=True,
    )
    assert json.dumps(packet, sort_keys=True) == before  # dry-run never mutates the packet
