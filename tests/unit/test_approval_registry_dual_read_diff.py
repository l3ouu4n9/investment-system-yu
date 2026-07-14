"""R2G-5b: dual-read diff tests (group H) + non-consumption / safety (I, J).

Proves the diff faithfully compares baseline vs approvals-inclusive registries,
records both hashes, surfaces added_by_approvals without changing anything at
runtime, and carries the report-only / non-consumption markers.
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from investment_orchestrator.research.active_research_anchor_registry import (
    build_active_research_anchor_registry,
    compile_active_research_anchor_registry,
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
    build_active_research_anchor_registry_with_approvals as _build_approvals_registry,
    compile_active_research_anchor_registry_with_approvals,
)
from investment_orchestrator.research.approval_registry_dual_read_diff import (
    SCHEMA_VERSION,
    build_approval_registry_dual_read_diff,
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


class _SourceBackedValidation(dict[str, Any]):
    def __init__(self, payload: dict[str, Any], source_text: str | None) -> None:
        super().__init__(payload)
        self.source_text = source_text


def _anchor(anchor_id: str = "AI_CAPEX_2026H2", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": anchor_id, "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"], "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01", "valid_until": "2026-07-31",
        "source_type": "operator", "confidence_floor": "medium", "summary": "x",
    }
    base.update(overrides)
    return base


def _baseline(anchors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload = {
        "schema_version": "research_anchors_v1",
        "as_of_date": AS_OF,
        "is_llm_generated": False,
        "anchors": anchors or [],
    }
    result = validate_research_anchors(payload, allowed_universe=UNIVERSE, today=AS_OF)
    return build_active_research_anchor_registry(
        anchors_result=result, source_present=bool(anchors), source_sha256="b" if anchors else None,
        source_path="inputs/current/research_anchors.yaml", as_of_date=AS_OF,
    )


def _validation(approvals: list[dict[str, Any]], *, present: bool = True) -> _SourceBackedValidation:
    manifest = {"schema_version": "research_anchor_approvals_v1", "is_llm_generated": False,
                "as_of_date": AS_OF, "approvals": approvals}
    source_text = yaml.safe_dump(manifest, sort_keys=False) if present else None
    payload = build_research_anchor_approvals_validation(
        manifest=manifest if present else None, source_present=present,
        source_sha256="a" if present else None,
        source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE, today=AS_OF,
    )
    return _SourceBackedValidation(payload, source_text)


def _approval(anchor: dict[str, Any] | None = None, *, approval_id: str = "APR-1") -> dict[str, Any]:
    a = anchor or _anchor()
    return {"approval_id": approval_id, "decision": "approve",
            "operator_completed_anchor": a, "operator_completed_anchor_sha256": sha(a)}


def _revocation(anchor: dict[str, Any] | None = None, *, revocation_id: str = "REV-1") -> dict[str, Any]:
    a = anchor or _anchor()
    return {
        "revocation_id": revocation_id,
        "target_type": "approval_anchor",
        "approval_id": "APR-1",
        "anchor_id": a["anchor_id"],
        "operator_completed_anchor_sha256": sha(a),
        "effective_as_of": AS_OF,
        "reason": "Thesis invalidated.",
        "revoked_by": "operator",
    }


def _revocations_validation(revocations: Any, approvals: list[dict[str, Any]]) -> _SourceBackedValidation:
    manifest = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": AS_OF,
        "approvals": approvals,
        "revocations": revocations,
    }
    source_text = yaml.safe_dump(manifest, sort_keys=False)
    payload = build_research_anchor_revocations_validation(
        manifest=manifest,
        approvals_validation=_validation(approvals),
        source_present=True,
        source_sha256="a",
        source_path="inputs/current/research_anchor_approvals.yaml",
        today=AS_OF,
        as_of_date=AS_OF,
    )
    return _SourceBackedValidation(payload, source_text)


def build_active_research_anchor_registry_with_approvals(
    *,
    baseline: dict[str, Any],
    approvals_validation: _SourceBackedValidation,
    revocations_validation: _SourceBackedValidation | None = None,
) -> dict[str, Any]:
    source = revocations_validation or approvals_validation
    return _build_approvals_registry(
        baseline=baseline,
        approval_source_text=source.source_text,
        approval_source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )


def _diff(baseline: dict[str, Any], approvals_reg: dict[str, Any]) -> dict[str, Any]:
    return build_approval_registry_dual_read_diff(
        baseline_registry=baseline, approvals_registry=approvals_reg,
        baseline_registry_path="baseline.json", approvals_registry_path="with_approvals.json",
    )


def _keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


# --- H. dual-read diff --------------------------------------------------------


def test_H_all_required_fields_present() -> None:
    baseline = _baseline()
    wa = build_active_research_anchor_registry_with_approvals(baseline=baseline, approvals_validation=_validation([_approval()]))
    d = _diff(baseline, wa)
    for key in (
        "schema_version", "is_llm_generated", "report_only", "permission_effect",
        "not_authorization", "not_execution_authorization", "generated_at", "as_of_date",
        "baseline_registry_path", "approvals_registry_path", "baseline_registry_sha256",
        "approvals_registry_sha256", "baseline_active_anchor_ids",
        "approvals_inclusive_active_anchor_ids", "added_by_approvals",
        "removed_or_deactivated", "changed_existing_anchors", "duplicate_blockers",
        "registry_valid_baseline", "registry_valid_with_approvals", "no_behavior_change",
        "standalone_artifact_not_consumed_by_support_signals",
        "embedded_registry_selection_owned_by_evidence_packet", "consumed_by_support_signals",
        "consumed_by_active_registry", "consumed_by_availability", "consumed_by_gates",
        "consumed_by_step2", "consumed_by_step4", "cannot_affect_allowed_actions",
        "blockers", "warnings", "notes",
    ):
        assert key in d, f"missing diff field: {key}"
    assert d["schema_version"] == SCHEMA_VERSION


def test_H_added_by_approvals_populated_and_hashes_recorded() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    wa = build_active_research_anchor_registry_with_approvals(baseline=baseline, approvals_validation=_validation([_approval()]))
    d = _diff(baseline, wa)
    assert d["added_by_approvals"] == ["AI_CAPEX_2026H2"]
    assert d["removed_or_deactivated"] == []
    assert d["baseline_active_anchor_ids"] == ["VOO_T"]
    assert set(d["approvals_inclusive_active_anchor_ids"]) == {"VOO_T", "AI_CAPEX_2026H2"}
    assert len(d["baseline_registry_sha256"]) == 64
    assert len(d["approvals_registry_sha256"]) == 64
    assert d["baseline_registry_sha256"] != d["approvals_registry_sha256"]
    assert d["registry_valid_baseline"] is True
    assert d["registry_valid_with_approvals"] is True


def test_H_no_behavior_change_markers() -> None:
    baseline = _baseline()
    wa = build_active_research_anchor_registry_with_approvals(baseline=baseline, approvals_validation=_validation([_approval()]))
    d = _diff(baseline, wa)
    assert d["no_behavior_change"] is True
    assert d["standalone_artifact_not_consumed_by_support_signals"] is True
    assert d["embedded_registry_selection_owned_by_evidence_packet"] is True
    for key in (
        "consumed_by_support_signals", "consumed_by_active_registry",
        "consumed_by_availability", "consumed_by_gates",
        "consumed_by_step2", "consumed_by_step4",
    ):
        assert d[key] is False
    assert d["cannot_affect_allowed_actions"] is True


def test_H_empty_approvals_no_added() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    wa = build_active_research_anchor_registry_with_approvals(baseline=baseline, approvals_validation=_validation([], present=False))
    d = _diff(baseline, wa)
    assert d["added_by_approvals"] == []
    assert d["baseline_active_anchor_ids"] == d["approvals_inclusive_active_anchor_ids"] == ["VOO_T"]


def test_H_cross_source_duplicate_recorded_in_diff() -> None:
    baseline = _baseline([_anchor("SHARED")])
    wa = build_active_research_anchor_registry_with_approvals(
        baseline=baseline, approvals_validation=_validation([_approval(_anchor("SHARED"))])
    )
    d = _diff(baseline, wa)
    assert d["registry_valid_with_approvals"] is False
    assert any(b["reason"] == "duplicate_anchor_id_across_sources" for b in d["duplicate_blockers"])
    assert "duplicate_anchor_id_across_sources" in d["blockers"]
    # SHARED is deactivated relative to baseline (excluded on both sides).
    assert "SHARED" in d["removed_or_deactivated"]


def test_H_changed_existing_anchor_detected() -> None:
    # Same anchor_id active in both but with different content_sha256 would be 'changed'.
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    # Build a fake approvals registry whose VOO_T row has a different content_sha256.
    wa = build_active_research_anchor_registry_with_approvals(baseline=baseline, approvals_validation=_validation([], present=False))
    wa = json.loads(json.dumps(wa))
    for row in wa["active_anchors"]:
        if row["anchor_id"] == "VOO_T":
            row["content_sha256"] = "different"
    d = _diff(baseline, wa)
    assert any(c["anchor_id"] == "VOO_T" for c in d["changed_existing_anchors"])


def test_H_revoked_approval_anchor_is_not_projected_as_added_or_active() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    approval = _approval()
    wa = build_active_research_anchor_registry_with_approvals(
        baseline=baseline,
        approvals_validation=_validation([approval]),
        revocations_validation=_revocations_validation([_revocation()], [approval]),
    )
    d = _diff(baseline, wa)
    assert "AI_CAPEX_2026H2" not in d["approvals_inclusive_active_anchor_ids"]
    assert d["added_by_approvals"] == []
    assert d["baseline_active_anchor_ids"] == ["VOO_T"]
    assert d["registry_valid_with_approvals"] is True


# --- I. non-consumption -------------------------------------------------------


def test_I_diff_does_not_mutate_inputs() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    wa = build_active_research_anchor_registry_with_approvals(baseline=baseline, approvals_validation=_validation([_approval()]))
    b_before, w_before = json.dumps(baseline, sort_keys=True), json.dumps(wa, sort_keys=True)
    _diff(baseline, wa)
    assert json.dumps(baseline, sort_keys=True) == b_before
    assert json.dumps(wa, sort_keys=True) == w_before


def test_I_observer_artifacts_not_imported_by_downstream_gate_consumers() -> None:
    # R2G-5c-2 intentionally lets support_signals consume the embedded approvals
    # schema and lets evidence_packet compile fresh readiness inputs. The on-disk
    # observer artifacts must still not become downstream gate authority.
    import inspect
    import investment_orchestrator.research.support_signals as ss
    import investment_orchestrator.research.evidence_packet as ep
    import investment_orchestrator.state.research_availability as ra
    assert "support_signals_dual_ground_diff" not in inspect.getsource(ss)
    assert "support_signals_dual_ground_diff" not in inspect.getsource(ep)
    ra_src = inspect.getsource(ra)
    assert "approvals_inclusive_active_registry" not in ra_src
    assert "approval_registry_dual_read_diff" not in ra_src
    assert "support_signals_dual_ground_diff" not in ra_src


# --- J. safety ----------------------------------------------------------------


def test_J_no_order_shaped_fields() -> None:
    baseline = _baseline()
    wa = build_active_research_anchor_registry_with_approvals(baseline=baseline, approvals_validation=_validation([_approval()]))
    d = _diff(baseline, wa)
    present = {k for k in _keys(d) if k.lower() in _ORDER_SHAPED_KEYS}
    assert present == set()


def test_J_no_new_buy_or_order_compilation_grants() -> None:
    baseline = _baseline()
    wa = build_active_research_anchor_registry_with_approvals(baseline=baseline, approvals_validation=_validation([_approval()]))
    blob = json.dumps(_diff(baseline, wa))
    assert '"NEW_BUY"' not in blob
    assert '"ORDER_COMPILATION"' not in blob


def test_J_diff_never_raises_on_garbage() -> None:
    d = build_approval_registry_dual_read_diff(baseline_registry="x", approvals_registry=12345)
    assert d["schema_version"] == SCHEMA_VERSION


# --- disk compile integration -------------------------------------------------


def test_compile_from_disk(tmp_path: Any) -> None:
    anchors = tmp_path / "research_anchors.yaml"
    anchors.write_text(
        "schema_version: research_anchors_v1\nis_llm_generated: false\nas_of_date: \"2026-07-04\"\n"
        "anchors:\n  - anchor_id: VOO_T\n    anchor_type: structural_theme\n"
        "    applicable_tickers: [VOO]\n    anchor_date_et: \"2026-06-10\"\n"
        "    valid_from: \"2026-06-01\"\n    valid_until: \"2026-08-31\"\n"
        "    source_type: operator\n    confidence_floor: high\n"
    )
    a = _anchor()
    approvals = tmp_path / "research_anchor_approvals.yaml"
    approvals.write_text(
        "schema_version: research_anchor_approvals_v1\nis_llm_generated: false\n"
        "as_of_date: \"2026-07-04\"\napprovals:\n  - approval_id: APR-1\n    decision: approve\n"
        "    operator_completed_anchor:\n      anchor_id: AI_CAPEX_2026H2\n"
        "      anchor_type: structural_theme\n      applicable_tickers: [QQQ]\n"
        "      anchor_date_et: \"2026-06-15\"\n      valid_from: \"2026-06-01\"\n"
        "      valid_until: \"2026-07-31\"\n      source_type: operator\n"
        "      confidence_floor: medium\n      summary: \"x\"\n"
        f"    operator_completed_anchor_sha256: \"{sha(a)}\"\n"
    )
    reg = compile_active_research_anchor_registry_with_approvals(
        anchors_path=anchors, approvals_path=approvals, allowed_universe=UNIVERSE, today=AS_OF
    )
    ids = sorted(x["anchor_id"] for x in reg["active_anchors"])
    assert ids == ["AI_CAPEX_2026H2", "VOO_T"]
    assert reg["registry_valid"] is True
    # baseline recompiled independently is unchanged (no approvals merged into it)
    baseline = compile_active_research_anchor_registry(anchors_path=anchors, allowed_universe=UNIVERSE, today=AS_OF)
    assert sorted(x["anchor_id"] for x in baseline["active_anchors"]) == ["VOO_T"]


def test_compile_from_disk_mandatorily_applies_revocations(tmp_path: Any) -> None:
    anchors = tmp_path / "research_anchors.yaml"
    anchors.write_text(
        "schema_version: research_anchors_v1\nis_llm_generated: false\nas_of_date: \"2026-07-04\"\n"
        "anchors:\n  - anchor_id: VOO_T\n    anchor_type: structural_theme\n"
        "    applicable_tickers: [VOO]\n    anchor_date_et: \"2026-06-10\"\n"
        "    valid_from: \"2026-06-01\"\n    valid_until: \"2026-08-31\"\n"
        "    source_type: operator\n    confidence_floor: high\n"
    )
    a = _anchor()
    approvals = tmp_path / "research_anchor_approvals.yaml"
    approvals.write_text(
        "schema_version: research_anchor_approvals_v1\nis_llm_generated: false\n"
        "as_of_date: \"2026-07-04\"\napprovals:\n  - approval_id: APR-1\n    decision: approve\n"
        "    operator_completed_anchor:\n      anchor_id: AI_CAPEX_2026H2\n"
        "      anchor_type: structural_theme\n      applicable_tickers: [QQQ]\n"
        "      anchor_date_et: \"2026-06-15\"\n      valid_from: \"2026-06-01\"\n"
        "      valid_until: \"2026-07-31\"\n      source_type: operator\n"
        "      confidence_floor: medium\n      summary: \"x\"\n"
        f"    operator_completed_anchor_sha256: \"{sha(a)}\"\n"
        "revocations:\n  - revocation_id: REV-1\n    target_type: approval_anchor\n"
        "    approval_id: APR-1\n    anchor_id: AI_CAPEX_2026H2\n"
        f"    operator_completed_anchor_sha256: \"{sha(a)}\"\n"
        "    effective_as_of: \"2026-07-04\"\n    reason: \"Thesis invalidated.\"\n"
        "    revoked_by: \"operator\"\n"
    )

    standalone_reg = compile_active_research_anchor_registry_with_approvals(
        anchors_path=anchors,
        approvals_path=approvals,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    assert sorted(x["anchor_id"] for x in standalone_reg["active_anchors"]) == ["VOO_T"]
    assert standalone_reg["counts"]["revoked"] == 1
    assert standalone_reg["revocations_applied"][0]["revocation_id"] == "REV-1"
