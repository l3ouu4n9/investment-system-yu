"""S1A-1 Step 1A shadow-run/report-only comparison tests."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.research.research_anchor_approval_manifest import (
    compute_operator_completed_anchor_sha256 as sha,
)
from investment_orchestrator.workflow import step1_research


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "step1_contract_failures"
    / "current_step1_raw_output_minimal.txt"
)


def _settings() -> dict[str, Any]:
    return {
        "as_of": "2026-06-28",
        "benchmark": "QQQ",
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
        "user_approved_extended_etf_static_list": ["GRID", "CIBR"],
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.00,
        "ticker_role_fallback": {
            "QQQ": "benchmark_carrier_core",
            "VOO": "diversified_core_buffer",
            "VTI": "diversified_core_buffer",
            "VT": "diversified_core_buffer",
            "SMH": "sector_alpha_tilt",
            "IGV": "sector_alpha_tilt",
        },
    }


def _anchor(anchor_id: str = "AI_CAPEX_2026H2", ticker: str = "QQQ") -> dict[str, Any]:
    return {
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


def _approval(anchor: dict[str, Any]) -> dict[str, Any]:
    return {
        "approval_id": "APR-1",
        "decision": "approve",
        "operator_completed_anchor": anchor,
        "operator_completed_anchor_sha256": sha(anchor),
        "approved_by": "operator",
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _setup_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    artifact_dir = tmp_path / "artifacts" / "current" / "step1_research"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "raw_output.txt").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    inputs = tmp_path / "inputs" / "current"
    inputs.mkdir(parents=True)
    _write_json(
        inputs / "research_anchors.yaml",
        {
            "schema_version": "research_anchors_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "anchors": [_anchor("BASE_QQQ", "QQQ")],
        },
    )
    approved_anchor = _anchor()
    _write_json(
        inputs / "research_anchor_approvals.yaml",
        {
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "approvals": [_approval(approved_anchor)],
            "revocations": [],
        },
    )
    return artifact_dir


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_shadow_diff_artifact_written_with_required_markers_and_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    path = step1_research.step1a_grounding_compile_shadow_diff_path()
    diff = _read(path)
    assert result["step1a_grounding_compile_shadow_diff_path"] == str(path)
    assert path.name == "step1a_grounding_compile_shadow_diff.json"
    assert diff["schema_version"] == "step1a_grounding_compile_shadow_diff_v1"
    assert diff["is_llm_generated"] is False
    assert diff["report_only"] is True
    assert diff["permission_effect"] == "none"
    assert diff["not_authorization"] is True
    assert diff["not_execution_authorization"] is True
    assert diff["consumed_by_gates"] is False
    assert diff["consumed_by_order_path"] is False
    assert diff["consumed_by_downstream"] is False
    assert diff["cannot_affect_allowed_actions"] is True
    assert diff["cannot_affect_registry_selection"] is True
    assert diff["not_registry_selection_input"] is True
    assert diff["not_order_input"] is True
    assert diff["shadow_run"] is True
    assert diff["production_artifacts_unchanged"] is True
    assert diff["safe_to_ignore"] is True

    # S1A-5.1: the ambiguous production_uses_step1a_outputs flag (stale after
    # the S1A-3/4/5 writer switches) is gone, replaced by precise
    # migration-scope markers that distinguish writer-source switches from
    # artifact-path/evidence/selection/support/readiness/order switches.
    assert "production_uses_step1a_outputs" not in diff
    assert "production_uses_step1a_outputs" not in diff["diagnostics"]
    assert diff["production_artifact_paths_switched"] is False
    assert diff["step1a_writer_source_artifacts"] == [
        "active_research_anchor_registry",
        "research_anchor_approvals_validation",
        "research_anchor_revocations_validation",
        "active_research_anchor_registry_with_approvals",
        "approval_registry_switch_readiness",
        "approval_registry_dual_read_diff",
        "evidence_packet",
    ]
    assert diff["step1a_writer_source_artifact_count"] == 7
    # S1A-11: the evidence_packet disk writer is now Step 1A-sourced behind the
    # strict parity guard; runtime-authority markers stay False.
    assert diff["evidence_packet_uses_step1a_output"] is True
    assert diff["embedded_selection_uses_step1a_output"] is False
    assert diff["support_signals_uses_step1a_output"] is False
    assert diff["readiness_uses_step1a_output"] is False
    assert diff["order_path_uses_step1a_output"] is False
    assert diff["runtime_authority_uses_step1a_output"] is False
    assert "step1a_artifact_switch_status.json" in diff["step1a_writer_source_note"]
    assert "evidence_packet.active_anchor_registry" in diff["step1a_writer_source_note"]
    # Every switched-writer artifact keeps a shadow comparison entry.
    for key in diff["step1a_writer_source_artifacts"]:
        assert key in diff["comparisons"]
    assert diff["diagnostics"]["production_artifact_paths_switched"] is False
    assert (
        diff["diagnostics"]["step1a_writer_source_artifacts"]
        == diff["step1a_writer_source_artifacts"]
    )
    assert diff["diagnostics"]["runtime_authority_uses_step1a_output"] is False

    assert diff["comparison_status"] == "pass"
    assert diff["parity_passed"] is True
    assert diff["available_comparisons_passed"] is True
    assert diff["comparisons"]["active_research_anchor_registry"]["semantic_match"] is True
    assert diff["comparisons"]["research_anchor_approvals_validation"]["semantic_match"] is True
    assert diff["comparisons"]["research_anchor_revocations_validation"]["semantic_match"] is True
    assert diff["comparisons"]["active_research_anchor_registry_with_approvals"]["semantic_match"] is True
    assert diff["comparisons"]["approval_registry_switch_readiness"]["semantic_match"] is True
    assert diff["comparisons"]["evidence_packet"]["semantic_match"] is True
    assert diff["comparisons"]["grounding_status_observatory"]["semantic_match"] is True

    # S1A-2: the persisted production selection completes the comparison set.
    embedded = diff["comparisons"]["embedded_active_anchor_registry_selection"]
    assert embedded["comparison_skipped"] is False
    assert embedded["semantic_match"] is True
    assert diff["comparison_complete"] is True
    assert diff["skipped_artifacts"] == []
    assert diff["mismatch_artifacts"] == []
    assert diff["diagnostics"]["diagnostics_incomplete"] is False
    assert diff["diagnostics"]["mismatch_count"] == 0
    assert diff["diagnostics"]["skipped_count"] == 0

    diagnostics = diff["diagnostics"]
    assert diagnostics["files_written"] == [str(path)]
    evidence_path = str(step1_research.step1_evidence_packet_path())
    selection_path = str(step1_research.step1_embedded_active_registry_selection_path())
    assert evidence_path in diagnostics["comparison_input_paths"]
    assert evidence_path in diagnostics["files_read"]
    assert selection_path in diagnostics["comparison_input_paths"]
    assert selection_path in diagnostics["files_read"]
    assert str(path) not in diagnostics["files_read"]
    for entry in diagnostics["files_read"]:
        assert Path(entry).is_file()
    optional_paths = {
        str(step1_research.step1_research_anchor_candidates_path()),
        str(step1_research.step1_compiled_support_signals_path()),
    }
    optional_read = set(diagnostics["optional_inputs_read"])
    optional_missing = set(diagnostics["optional_inputs_missing"])
    assert optional_read.isdisjoint(optional_missing)
    assert optional_read | optional_missing == optional_paths
    assert optional_read <= set(diagnostics["files_read"])
    for entry in optional_missing:
        assert not Path(entry).is_file()


def test_embedded_selection_artifact_written_with_markers_and_production_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    step1_research.parse_step1_output(strategy_settings=_settings())

    artifact = _read(step1_research.step1_embedded_active_registry_selection_path())
    assert artifact["schema_version"] == "embedded_active_anchor_registry_selection_v1"
    assert artifact["is_llm_generated"] is False
    assert artifact["report_only"] is True
    assert artifact["permission_effect"] == "none"
    assert artifact["not_authorization"] is True
    assert artifact["not_execution_authorization"] is True
    assert artifact["consumed_by_gates"] is False
    assert artifact["consumed_by_order_path"] is False
    assert artifact["consumed_by_downstream"] is False
    assert artifact["cannot_affect_allowed_actions"] is True
    assert artifact["cannot_affect_registry_selection"] is True
    assert artifact["not_registry_selection_input"] is True
    assert artifact["not_order_input"] is True
    assert artifact["production_source"] is True
    assert artifact["step1a_output"] is False
    assert artifact["safe_to_ignore"] is True

    # The artifact IS the production selection: its selected registry equals the
    # registry the evidence packet embedded, byte-for-byte.
    packet = _read(step1_research.step1_evidence_packet_path())
    assert artifact["selected_registry"] == packet["active_anchor_registry"]
    assert artifact["selected_source"] in ("baseline_fallback", "approvals_inclusive", "fail_closed_empty")


def test_embedded_selection_write_failure_is_swallowed_and_shadow_reports_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_writer(_selection: Any) -> None:
        raise RuntimeError("selection artifact write failed")

    monkeypatch.setattr(
        step1_research,
        "_write_embedded_active_registry_selection_report_only",
        broken_writer,
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Step 1 parse continues and the evidence packet is unaffected.
    assert Path(result["research_output_path"]).is_file()
    packet = _read(step1_research.step1_evidence_packet_path())
    assert packet["active_anchor_registry"]["registry_valid"] is True
    assert not step1_research.step1_embedded_active_registry_selection_path().is_file()

    # The shadow diff reports the missing input as an explicit skip, not a pass.
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "pass_with_skips"
    assert diff["parity_passed"] is False
    assert diff["available_comparisons_passed"] is True
    assert diff["comparison_complete"] is False
    assert diff["diagnostics"]["diagnostics_incomplete"] is True
    embedded = diff["comparisons"]["embedded_active_anchor_registry_selection"]
    assert embedded["comparison_skipped"] is True
    assert embedded["skip_reason"] == "current_step1_artifact_unavailable_or_malformed"
    assert diff["production_artifacts_unchanged"] is True
    assert diff["production_artifact_paths_switched"] is False
    assert diff["runtime_authority_uses_step1a_output"] is False
    assert "production_uses_step1a_outputs" not in diff

    decision = _read(Path(result["research_degraded_mode_decision_path"]))
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]


def test_evidence_packet_comparison_strengthened_catches_embedded_registry_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1A-10: the strengthened evidence_packet comparison catches an embedded
    active_anchor_registry content mismatch that the old count-only summary
    would have false-passed (same anchor count, different content_sha256).
    """
    _setup_repo(tmp_path, monkeypatch)
    original = step1_research.build_step1a_grounding_compile_bundle

    def mismatched_bundle(**kwargs: Any) -> dict[str, Any]:
        bundle = original(**kwargs)
        packet = bundle["artifacts"]["evidence_packet"]
        registry = packet.get("active_anchor_registry")
        if isinstance(registry, dict):
            anchors = registry.get("active_anchors")
            if isinstance(anchors, list) and anchors:
                # Mutate content WITHOUT changing the anchor count or source —
                # exactly the class the old count/selected_source summary missed.
                anchors[0] = {**anchors[0], "content_sha256": "f" * 64}
            else:
                # Empty-manifest inputs: perturb a runtime-relevant field the old
                # summary also ignored (validity), still count-preserving.
                registry["registry_valid"] = not registry.get("registry_valid", True)
        return bundle

    monkeypatch.setattr(step1_research, "build_step1a_grounding_compile_bundle", mismatched_bundle)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "mismatch"
    assert diff["parity_passed"] is False
    assert "evidence_packet" in diff["mismatch_artifacts"]
    assert diff["comparisons"]["evidence_packet"]["semantic_match"] is False
    # The strengthened summary embeds the normalized registry and proves only
    # generated_at was normalized (no over-normalization hiding the mismatch).
    current_summary = diff["comparisons"]["evidence_packet"]["current_summary"]
    assert current_summary["parity_normalized_paths"] == [
        "active_anchor_registry.generated_at",
        "generated_at",
    ]
    assert current_summary["parity_unknown_runtime_timestamp_fields"] == []

    # Diagnostic only — never opens an order path.
    decision = _read(Path(result["research_degraded_mode_decision_path"]))
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]


def test_evidence_packet_comparison_normalizes_only_generated_at_on_clean_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1A-10: a clean run keeps evidence_packet matching, with the embedded
    registry compared exactly and only generated_at normalized."""
    _setup_repo(tmp_path, monkeypatch)
    step1_research.parse_step1_output(strategy_settings=_settings())

    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "pass"
    assert diff["comparison_complete"] is True
    ep = diff["comparisons"]["evidence_packet"]
    assert ep["semantic_match"] is True
    summary = ep["current_summary"]
    # Strengthened: the full normalized embedded registry is present and its
    # generated_at is the sentinel (not a wall-clock timestamp).
    assert summary["active_anchor_registry"]["generated_at"] == "<normalized_generated_at>"
    assert summary["parity_normalized_paths"] == [
        "active_anchor_registry.generated_at",
        "generated_at",
    ]
    assert summary["parity_unknown_runtime_timestamp_fields"] == []
    # Runtime-relevant fields are carried in the summary (not just counts).
    for key in ("strategy_settings_hash", "universe", "budget_settings", "data_gaps"):
        assert key in summary


def test_shadow_mismatch_is_diagnostic_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    original = step1_research.build_step1a_grounding_compile_bundle

    def mismatched_bundle(**kwargs: Any) -> dict[str, Any]:
        bundle = original(**kwargs)
        registry = bundle["artifacts"]["active_research_anchor_registry"]
        registry["registry_valid"] = False
        return bundle

    monkeypatch.setattr(step1_research, "build_step1a_grounding_compile_bundle", mismatched_bundle)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "mismatch"
    assert diff["parity_passed"] is False
    assert diff["available_comparisons_passed"] is False
    assert diff["production_artifacts_unchanged"] is True
    assert diff["production_artifact_paths_switched"] is False
    assert diff["runtime_authority_uses_step1a_output"] is False
    assert "production_uses_step1a_outputs" not in diff
    assert diff["diagnostics"]["mismatch_is_diagnostic_only"] is True
    assert "active_research_anchor_registry" in diff["mismatch_artifacts"]

    decision = _read(Path(result["research_degraded_mode_decision_path"]))
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]


def test_shadow_run_exception_is_swallowed_and_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_bundle(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("shadow bundle failed")

    monkeypatch.setattr(step1_research, "build_step1a_grounding_compile_bundle", broken_bundle)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "failed"
    assert diff["parity_passed"] is False
    assert diff["available_comparisons_passed"] is False
    assert diff["comparison_complete"] is False
    assert diff["diagnostics"]["shadow_run_failed"] is True
    assert diff["diagnostics"]["diagnostics_incomplete"] is True
    assert "shadow bundle failed" in diff["diagnostics"]["shadow_run_error"]
    # A failed shadow run claims no reads instead of guessing what was loaded.
    assert diff["diagnostics"]["files_read"] == []
    assert diff["diagnostics"]["optional_inputs_read"] == []
    assert diff["diagnostics"]["optional_inputs_missing"] == []
    assert diff["diagnostics"]["comparison_input_paths"]
    assert diff["production_artifacts_unchanged"] is True
    assert diff["production_artifact_paths_switched"] is False
    assert diff["runtime_authority_uses_step1a_output"] is False
    assert "production_uses_step1a_outputs" not in diff
    assert Path(result["research_output_path"]).is_file()


def test_shadow_diff_has_no_downstream_consumer_or_artifact_path_switch() -> None:
    import investment_orchestrator.research.evidence_packet as evidence_packet
    import investment_orchestrator.research.support_signals as support_signals
    import investment_orchestrator.state.final_execution_safety_gate as final_gate
    import investment_orchestrator.state.research_availability as availability
    import investment_orchestrator.workflow.step2_decision_builder as step2
    import investment_orchestrator.workflow.step3_audit_engine as step3
    import investment_orchestrator.workflow.step4_order_compiler as step4
    import investment_orchestrator.workflow.weekly_orchestrator as weekly

    step1_source = inspect.getsource(step1_research)
    assert "step1a_grounding_compile_shadow_diff.json" in step1_source
    assert "embedded_active_registry_selection.json" in step1_source
    assert "step1_evidence_packet_path" in step1_source
    assert "step1_compiled_support_signals_path" in step1_source
    assert "step1_research_degraded_mode_decision_path" in step1_source

    for module in (evidence_packet, support_signals, availability, step2, step3, step4, final_gate, weekly):
        source = inspect.getsource(module)
        assert "step1a_grounding_compile_shadow_diff" not in source
        assert "build_step1a_grounding_compile_bundle" not in source
        assert "step1a_grounding_compile" not in source
        # S1A-2: the persisted production selection is read only by the shadow
        # comparison — no production module references the artifact or its helper.
        assert "embedded_active_registry_selection.json" not in source
        assert "step1_embedded_active_registry_selection_path" not in source
        # S1A-3/4/5/6/7/8: the switch status artifact and the narrow per-artifact
        # accessors (the with-approvals accessor is covered by its S1A-3 prefix;
        # the S1A-7 readiness and S1A-8 dual-read-diff accessors are NOT
        # prefix-covered, so they are asserted explicitly) are likewise invisible
        # to every downstream module.
        assert "step1a_artifact_switch_status" not in source
        assert "build_step1a_active_research_anchor_registry" not in source
        assert "build_step1a_research_anchor_approvals_validation" not in source
        assert "build_step1a_research_anchor_revocations_validation" not in source
        assert "build_step1a_approval_registry_switch_readiness" not in source
        assert "build_step1a_approval_registry_dual_read_diff" not in source
