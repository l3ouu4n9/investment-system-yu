"""Step 1 integration tests for grounding_status_observatory.json (R2G-6b).

The observatory is written as a report-only diagnostic artifact. These tests
prove the Step 1 writer is additive and that no runtime decision path consumes
the artifact.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

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


def _setup_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    artifact_dir = tmp_path / "artifacts" / "current" / "step1_research"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "raw_output.txt").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return artifact_dir


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    _setup_repo(tmp_path, monkeypatch)
    return step1_research.parse_step1_output(strategy_settings=_settings())


def test_parse_writes_grounding_status_observatory_report_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(tmp_path, monkeypatch)

    path = Path(result["grounding_status_observatory_path"])
    assert path == tmp_path / "artifacts" / "current" / "step1_research" / "grounding_status_observatory.json"
    assert path.is_file()

    artifact = json.loads(path.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "grounding_status_observatory_v1"
    assert artifact["is_llm_generated"] is False
    assert artifact["report_only"] is True
    assert artifact["permission_effect"] == "none"
    assert artifact["not_authorization"] is True
    assert artifact["not_execution_authorization"] is True
    assert artifact["consumed_by_gates"] is False
    assert artifact["consumed_by_order_path"] is False
    assert artifact["cannot_affect_allowed_actions"] is True
    assert artifact["cannot_affect_registry_selection"] is True
    assert artifact["not_registry_selection_input"] is True
    assert artifact["not_order_input"] is True
    assert artifact["diagnostics"]["files_read"] == []
    assert artifact["diagnostics"]["readiness_recomputed"] is False
    assert artifact["diagnostics"]["registry_selection_recomputed"] is False
    assert json.loads(json.dumps(artifact))["schema_version"] == "grounding_status_observatory_v1"


def test_grounding_status_observatory_does_not_change_allowed_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(tmp_path, monkeypatch)

    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]
    assert "grounding_status_observatory" not in decision


def test_grounding_status_observatory_has_no_order_or_live_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(tmp_path, monkeypatch)
    artifact = json.loads(Path(result["grounding_status_observatory_path"]).read_text(encoding="utf-8"))

    assert artifact["safety_invariants"]["new_buy_permission_opened"] is False
    assert artifact["safety_invariants"]["order_compilation_permission_opened"] is False
    assert artifact["safety_invariants"]["step4_enabled"] is False
    assert artifact["safety_invariants"]["final_execution_enabled"] is False
    assert artifact["safety_invariants"]["weekly_automation_changed"] is False
    assert artifact["safety_invariants"]["broker_live_execution_enabled"] is False
    assert artifact["safety_invariants"]["automatic_order_placement_enabled"] is False
    assert artifact["safety_invariants"]["executable_order_authority"] is False
    assert artifact["candidates_summary"]["candidate_sha256_audit_only"] is True
    assert artifact["candidates_summary"]["candidates_can_activate_anchors"] is False
    assert artifact["candidates_summary"]["candidates_can_revoke_anchors"] is False
    assert artifact["support_grounding_summary"]["can_authorize_trades"] is False
    assert artifact["support_grounding_summary"]["can_authorize_orders"] is False


def test_missing_optional_inputs_produce_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def skip_candidates(*, strategy_settings: Any) -> None:
        return None

    monkeypatch.setattr(
        step1_research,
        "_write_research_anchor_candidates_report_only",
        skip_candidates,
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())
    artifact = json.loads(Path(result["grounding_status_observatory_path"]).read_text(encoding="utf-8"))

    assert artifact["diagnostics"]["diagnostics_incomplete"] is True
    assert artifact["diagnostics"]["partial_data"] is True
    assert any(problem["input"] == "candidates" for problem in artifact["diagnostics"]["input_problems"])
    assert "missing_or_malformed_inputs_present" in artifact["warnings"]
    assert artifact["candidates_summary"]["candidate_count"] == 0
    assert artifact["cannot_affect_allowed_actions"] is True
    assert artifact["consumed_by_gates"] is False


def test_malformed_optional_inputs_produce_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def write_malformed_candidates(*, strategy_settings: Any) -> None:
        step1_research.step1_research_anchor_candidates_path().write_text("{not json", encoding="utf-8")

    monkeypatch.setattr(
        step1_research,
        "_write_research_anchor_candidates_report_only",
        write_malformed_candidates,
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())
    artifact = json.loads(Path(result["grounding_status_observatory_path"]).read_text(encoding="utf-8"))

    assert artifact["diagnostics"]["diagnostics_incomplete"] is True
    assert any(problem["input"] == "candidates" for problem in artifact["diagnostics"]["input_problems"])
    assert "missing_or_malformed_inputs_present" in artifact["warnings"]
    assert artifact["consumed_by_order_path"] is False
    assert artifact["not_order_input"] is True


def test_observatory_writer_failure_does_not_break_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated observatory failure")

    monkeypatch.setattr(step1_research, "build_grounding_status_observatory", boom)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_degraded_mode_decision_path"]).exists()
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert not Path(result["grounding_status_observatory_path"]).exists()


def test_no_unexpected_consumers_import_or_read_observatory() -> None:
    import investment_orchestrator.research.approval_registry_switch_readiness as readiness
    import investment_orchestrator.research.evidence_packet as evidence_packet
    import investment_orchestrator.research.support_signals as support_signals
    import investment_orchestrator.state.final_execution_safety_gate as final_gate
    import investment_orchestrator.state.research_availability as availability
    import investment_orchestrator.workflow.step2_decision_builder as step2
    import investment_orchestrator.workflow.step3_audit_engine as step3
    import investment_orchestrator.workflow.step4_order_compiler as step4
    import investment_orchestrator.workflow.weekly_orchestrator as weekly

    for module in (
        readiness,
        evidence_packet,
        support_signals,
        availability,
        step2,
        step3,
        step4,
        final_gate,
        weekly,
    ):
        assert "grounding_status_observatory" not in inspect.getsource(module)
        assert "grounding_status_observatory.json" not in inspect.getsource(module)

    step1_source = inspect.getsource(step1_research)
    assert "_read_json_if_exists(step1_grounding_status_observatory_path" not in step1_source
    assert "read_json(step1_grounding_status_observatory_path" not in step1_source
