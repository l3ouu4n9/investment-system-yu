"""S1A-0 extraction tests for the deterministic Step 1A compile bundle."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.research.approval_registry_switch_readiness import SWITCH_TARGET_FAIL_CLOSED
from investment_orchestrator.research.research_anchor_approval_manifest import (
    compute_operator_completed_anchor_sha256 as sha,
)
from investment_orchestrator.workflow import step1_research
from investment_orchestrator.workflow.step1a_grounding_compile import (
    SCHEMA_VERSION,
    build_step1a_grounding_compile_bundle,
)


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


def _approval(anchor: dict[str, Any] | None = None, approval_id: str = "APR-1") -> dict[str, Any]:
    a = anchor or _anchor()
    return {
        "approval_id": approval_id,
        "decision": "approve",
        "operator_completed_anchor": a,
        "operator_completed_anchor_sha256": sha(a),
        "approved_by": "operator",
    }


def _revocation(
    anchor: dict[str, Any] | None = None,
    *,
    approval_id: str = "APR-1",
    anchor_id: str | None = None,
) -> dict[str, Any]:
    a = anchor or _anchor()
    return {
        "revocation_id": "REV-1",
        "target_type": "approval_anchor",
        "approval_id": approval_id,
        "anchor_id": anchor_id or a["anchor_id"],
        "operator_completed_anchor_sha256": sha(a),
        "effective_as_of": "2026-06-28",
        "reason": "Thesis invalidated.",
        "revoked_by": "operator",
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _setup_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
    *,
    anchors: list[dict[str, Any]] | None = None,
    approvals_payload: Any = "default",
) -> dict[str, Path]:
    if monkeypatch is not None:
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
            "anchors": anchors if anchors is not None else [_anchor("BASE_QQQ", "QQQ")],
        },
    )
    if approvals_payload == "default":
        approvals_payload = {
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "approvals": [_approval()],
            "revocations": [],
        }
    if approvals_payload is not None:
        if isinstance(approvals_payload, str):
            (inputs / "research_anchor_approvals.yaml").write_text(approvals_payload, encoding="utf-8")
        else:
            _write_json(inputs / "research_anchor_approvals.yaml", approvals_payload)
    return {"artifact_dir": artifact_dir, "inputs": inputs, "state": tmp_path / "artifacts" / "state"}


def _bundle(
    tmp_path: Path,
    *,
    optional_candidates: dict[str, Any] | None = None,
    optional_support: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inputs = tmp_path / "inputs" / "current"
    artifact_dir = tmp_path / "artifacts" / "current" / "step1_research"
    return build_step1a_grounding_compile_bundle(
        strategy_settings=_settings(),
        research_anchors_path=inputs / "research_anchors.yaml",
        research_anchor_approvals_path=inputs / "research_anchor_approvals.yaml",
        portfolio_snapshot_text=None,
        portfolio_snapshot_path=inputs / "portfolio_snapshot.txt",
        last_good_available=False,
        last_good_metadata={},
        strategy_settings_path=inputs / "strategy_settings.yaml",
        last_good_metadata_path=tmp_path / "artifacts" / "state" / "last_good_research_handoff_metadata.json",
        active_registry_artifact_path=artifact_dir / "active_research_anchor_registry.json",
        approvals_registry_artifact_path=artifact_dir / "active_research_anchor_registry_with_approvals.json",
        optional_research_anchor_candidates=optional_candidates,
        optional_compiled_support_signals=optional_support,
    )


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_generated_at(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_generated_at(v) for k, v in value.items() if k != "generated_at"}
    if isinstance(value, list):
        return [_strip_generated_at(item) for item in value]
    return value


def _active_ids(registry: dict[str, Any]) -> list[str]:
    return sorted(row["anchor_id"] for row in registry.get("active_anchors", []))


def _approval_sourced_active_count(registry: dict[str, Any]) -> int:
    return sum(
        1
        for row in registry.get("active_anchors", [])
        if row.get("source_id") == "operator_research_anchor_approvals_yaml"
    )


def test_builder_returns_json_serializable_unwired_bundle(tmp_path: Path) -> None:
    paths = _setup_repo(tmp_path)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())

    bundle = _bundle(tmp_path)

    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    assert after == before
    assert json.loads(json.dumps(bundle))["schema_version"] == SCHEMA_VERSION
    assert bundle["is_llm_generated"] is False
    assert bundle["extraction_only"] is True
    assert bundle["not_wired_to_production"] is True
    assert bundle["permission_effect"] == "none"
    assert bundle["not_authorization"] is True
    assert bundle["not_execution_authorization"] is True
    assert bundle["consumed_by_gates"] is False
    assert bundle["consumed_by_order_path"] is False
    assert bundle["cannot_affect_allowed_actions"] is True
    assert bundle["diagnostics"]["files_written"] == []
    assert paths["artifact_dir"].is_dir()


def test_builder_semantic_parity_with_current_step1_deterministic_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())
    optional_candidates = _read(paths["artifact_dir"] / "research_anchor_candidates.json")
    optional_support = _read(Path(result["compiled_support_signals_path"]))

    bundle = _bundle(tmp_path, optional_candidates=optional_candidates, optional_support=optional_support)
    artifacts = bundle["artifacts"]

    assert artifacts["active_research_anchor_registry"] == _read(
        paths["artifact_dir"] / "active_research_anchor_registry.json"
    )
    assert artifacts["research_anchor_approvals_validation"] == _read(
        paths["artifact_dir"] / "research_anchor_approvals_validation.json"
    )
    assert artifacts["research_anchor_revocations_validation"] == _read(
        paths["artifact_dir"] / "research_anchor_revocations_validation.json"
    )
    assert artifacts["active_research_anchor_registry_with_approvals"] == _read(
        paths["artifact_dir"] / "active_research_anchor_registry_with_approvals.json"
    )
    assert artifacts["approval_registry_dual_read_diff"] == _read(
        paths["artifact_dir"] / "approval_registry_dual_read_diff.json"
    )
    assert artifacts["approval_registry_switch_readiness"] == _read(
        paths["artifact_dir"] / "approval_registry_switch_readiness.json"
    )
    assert _strip_generated_at(artifacts["evidence_packet"]) == _strip_generated_at(
        _read(paths["artifact_dir"] / "evidence_packet.json")
    )

    current_observatory = _read(paths["artifact_dir"] / "grounding_status_observatory.json")
    new_observatory = artifacts["grounding_status_observatory"]
    assert new_observatory["schema_version"] == current_observatory["schema_version"]
    assert new_observatory["selected_registry"]["active_anchor_count"] == current_observatory["selected_registry"][
        "active_anchor_count"
    ]
    assert new_observatory["candidates_summary"]["candidate_count"] == current_observatory["candidates_summary"][
        "candidate_count"
    ]
    assert new_observatory["support_grounding_summary"]["grounded_claim_count"] == current_observatory[
        "support_grounding_summary"
    ]["grounded_claim_count"]


def test_missing_approvals_source_produces_diagnostics_without_authority(tmp_path: Path) -> None:
    _setup_repo(tmp_path, approvals_payload=None)
    bundle = _bundle(tmp_path)
    artifacts = bundle["artifacts"]

    assert artifacts["research_anchor_approvals_validation"]["source_present"] is False
    assert artifacts["research_anchor_revocations_validation"]["source_present"] is False
    assert _approval_sourced_active_count(artifacts["active_research_anchor_registry_with_approvals"]) == 0
    assert bundle["cannot_affect_allowed_actions"] is True
    assert bundle["consumed_by_order_path"] is False


def test_malformed_approvals_source_produces_validation_diagnostics(tmp_path: Path) -> None:
    _setup_repo(tmp_path, approvals_payload="approvals: [unterminated\n : :\n")
    artifacts = _bundle(tmp_path)["artifacts"]

    assert artifacts["research_anchor_approvals_validation"]["source_valid"] is False
    assert artifacts["research_anchor_revocations_validation"]["source_valid"] is False
    assert _approval_sourced_active_count(artifacts["active_research_anchor_registry_with_approvals"]) == 0


def test_malformed_revocations_fail_closed_in_revocation_validation(tmp_path: Path) -> None:
    _setup_repo(
        tmp_path,
        approvals_payload={
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "approvals": [_approval()],
            "revocations": "not-a-list",
        },
    )
    revocations = _bundle(tmp_path)["artifacts"]["research_anchor_revocations_validation"]

    assert revocations["source_valid"] is False
    assert revocations["revocations_valid"] is False


def test_invalid_revocation_target_fails_closed_without_runtime_authority(tmp_path: Path) -> None:
    _setup_repo(
        tmp_path,
        approvals_payload={
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "approvals": [_approval()],
            "revocations": [_revocation(approval_id="APR-UNKNOWN")],
        },
    )
    artifacts = _bundle(tmp_path)["artifacts"]
    revocations = artifacts["research_anchor_revocations_validation"]

    assert revocations["revocations_valid"] is False
    assert any(r["target_binding_status"] == "target_not_found" for r in revocations["revocation_results"])
    assert artifacts["active_research_anchor_registry_with_approvals"]["registry_valid"] is False
    assert artifacts["grounding_status_observatory"]["revocations_summary"]["unknown_target_count"] == 1


def test_invalid_baseline_registry_selects_fail_closed_empty(tmp_path: Path) -> None:
    _setup_repo(tmp_path, anchors=[_anchor("DUP", "QQQ"), _anchor("DUP", "VOO")])
    artifacts = _bundle(tmp_path)["artifacts"]

    assert artifacts["active_research_anchor_registry"]["registry_valid"] is False
    assert artifacts["approval_registry_switch_readiness"]["switch_target"] == SWITCH_TARGET_FAIL_CLOSED
    assert artifacts["embedded_active_anchor_registry_selection"]["selected_source"] == SWITCH_TARGET_FAIL_CLOSED
    assert artifacts["evidence_packet"]["active_anchor_registry"]["active_anchors"] == []


def test_unknown_schema_and_partial_optional_inputs_are_observatory_diagnostics(tmp_path: Path) -> None:
    _setup_repo(tmp_path)
    bundle = _bundle(tmp_path, optional_candidates={"schema_version": "future_candidates_v999"})
    observatory = bundle["artifacts"]["grounding_status_observatory"]

    assert observatory["diagnostics"]["diagnostics_incomplete"] is True
    assert {"input": "candidates", "schema_version": "future_candidates_v999"} in observatory["diagnostics"][
        "unknown_schema_sources"
    ]
    assert any(problem["input"] == "support_signals" for problem in observatory["diagnostics"]["input_problems"])
    assert observatory["cannot_affect_allowed_actions"] is True
    assert observatory["consumed_by_gates"] is False


def test_builder_source_has_no_llm_or_support_signal_interpretation_imports() -> None:
    import investment_orchestrator.workflow.step1a_grounding_compile as s1a

    source = inspect.getsource(s1a)
    assert "investment_orchestrator.llm" not in source
    assert "manual_output" not in source
    assert "render_prompt" not in source
    assert "support_signals import" not in source
    assert "write_json" not in source


def test_no_production_consumer_imports_step1a_bundle() -> None:
    import investment_orchestrator.research.approval_registry_switch_readiness as readiness
    import investment_orchestrator.research.evidence_packet as evidence_packet
    import investment_orchestrator.research.support_signals as support_signals
    import investment_orchestrator.state.final_execution_safety_gate as final_gate
    import investment_orchestrator.state.research_availability as availability
    import investment_orchestrator.workflow.step1_research as step1
    import investment_orchestrator.workflow.step2_decision_builder as step2
    import investment_orchestrator.workflow.step3_audit_engine as step3
    import investment_orchestrator.workflow.step4_order_compiler as step4
    import investment_orchestrator.workflow.weekly_orchestrator as weekly

    for module in (
        step1,
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
        assert "step1a_grounding_compile" not in inspect.getsource(module)
        assert "build_step1a_grounding_compile_bundle" not in inspect.getsource(module)


def test_bundle_opens_no_order_or_permission_path(tmp_path: Path) -> None:
    _setup_repo(tmp_path)
    bundle = _bundle(tmp_path)
    blob = json.dumps(bundle)

    assert '"allowed_actions"' not in blob
    assert bundle["safety_invariants"]["no_new_buy_permission"] is True
    assert bundle["safety_invariants"]["no_order_compilation_permission"] is True
    assert bundle["safety_invariants"]["no_step4_enablement"] is True
    assert bundle["safety_invariants"]["no_final_execution"] is True
    assert bundle["safety_invariants"]["no_weekly_automation_change"] is True
    assert bundle["safety_invariants"]["no_broker_live_execution"] is True
    assert bundle["safety_invariants"]["no_executable_order_authority"] is True
