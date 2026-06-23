from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.common import paths as path_helpers
from investment_orchestrator.common.io import write_json, write_text
from investment_orchestrator.state.upstream_artifact_guard import UpstreamArtifactGuardError
from investment_orchestrator.workflow import (
    step1_research,
    step2_decision_builder,
    step3_audit_engine,
    step4_order_compiler,
)


def market_snapshot() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "snapshot_type": "MARKET_DATA_SNAPSHOT",
        "run_timestamp_et": "2026-06-22 16:33 ET",
        "execution_date_et": "2026-06-22",
        "market_data_target_close_date_et": "2026-06-22",
        "close_time_zone": "America/New_York",
        "display_time_zone": "America/Los_Angeles",
        "primary_source": "fixture",
        "fallback_source_for_last_close_and_price_asof_only": "fixture",
        "holiday_aware_close_resolution": True,
        "tickers": [],
    }


def decision_packet() -> dict[str, Any]:
    return {
        "effective_allowed_buy_universe": ["QQQ"],
        "MARKET_DATA_SNAPSHOT": market_snapshot(),
        "active_shortlist": [],
        "buy_side_delta_table": [],
        "rotation_decision_layer_8_15": [],
        "sell_side_delta_table_8_2": [],
        "execution_plan_drafts_8_5": [],
        "sell_execution_plan_drafts_8_6": [],
        "assumptions_and_data_gaps": [],
        "decision_builder_ready_for_audit": True,
    }


def audited_decision_packet() -> dict[str, Any]:
    return {
        "audit_passed": True,
        "order_compiler_ready": True,
        "final_buy_side_delta_table": [],
        "final_sell_side_delta_table": [],
        "final_execution_plans": [],
        "final_sell_execution_plans": [],
    }


def prepare_tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(path_helpers, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step2_decision_builder, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step4_order_compiler, "repo_root", lambda: tmp_path)

    write_text(
        tmp_path / "prompts" / "strategy_b_audit_engine.txt",
        "RESEARCH\n{{ research_json }}\nPORTFOLIO\n{{ portfolio_snapshot }}\n"
        "TEMPLATE2\n{{ template2_output }}\nDECISION\n{{ decision_packet }}\n",
    )
    write_text(
        tmp_path / "prompts" / "strategy_c_order_compiler.txt",
        "RESEARCH\n{{ research_json }}\nPORTFOLIO\n{{ portfolio_snapshot }}\n"
        "SETTINGS\n{{ strategy_settings }}\nMARKET\n{{ market_data_snapshot }}\n"
        "AUDITED\n{{ audited_decision_packet }}\n",
    )
    write_text(tmp_path / "inputs" / "current" / "strategy_settings.yaml", "as_of: '2026-06-22'\n")
    write_text(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", "QQQ | 1 | 100\n")
    write_json(step1_research.step1_research_output_path(), {"schema_version": "1.0"})


def write_step2_normal_artifacts() -> None:
    write_text(step2_decision_builder.step2_prompt_path(), "STEP2 PROMPT\n")
    write_text(step2_decision_builder.step2_raw_output_path(), "STEP2 RAW\n")
    write_text(step2_decision_builder.step2_template2_output_path(), "TEMPLATE2 OUTPUT\n")
    write_json(step2_decision_builder.step2_decision_packet_path(), decision_packet())


def write_step1_strict_fresh_permission() -> None:
    """A STRICT_FRESH Step 1 permission, required for the Step 4 allowed path."""
    write_json(
        step1_research.step1_research_degraded_mode_decision_path(),
        {
            "state": "STRICT_FRESH",
            "research_availability": "strict_fresh",
            "allowed_actions": [
                "HOLD",
                "NO_TRADE",
                "SELL",
                "NEW_BUY",
                "ROTATION",
                "REBALANCE",
                "EXTENDED_ETF_ADMISSION",
                "ORDER_COMPILATION",
            ],
            "blocked_actions": [],
            "manual_review_required": False,
            "report_only": True,
        },
    )


def write_step3_normal_artifacts() -> None:
    write_text(step3_audit_engine.step3_prompt_path(), "STEP3 PROMPT\n")
    write_text(step3_audit_engine.step3_raw_output_path(), "STEP3 RAW\n")
    write_text(step3_audit_engine.step3_template3_audit_path(), "TEMPLATE3 AUDIT\n")
    write_json(step3_audit_engine.step3_audited_decision_packet_path(), audited_decision_packet())


def read_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def assert_common_blocked_payload(payload: dict[str, Any]) -> None:
    assert payload["blocked"] is True
    assert payload["recommended_result"] == "NO_TRADE"
    assert payload["report_only"] is False
    # E1: blocked artifacts always carry the permission-metadata fields.
    assert "upstream_permission" in payload
    assert "upstream_permission_read_errors" in payload


def write_step1_degraded_decision(
    *,
    state: str = "NO_OUTPUT",
    research_availability: str = "no_output",
    manual_review_required: bool = False,
) -> None:
    write_json(
        step1_research.step1_research_degraded_mode_decision_path(),
        {
            "state": state,
            "research_availability": research_availability,
            "allowed_actions": ["HOLD", "NO_TRADE"],
            "blocked_actions": ["NEW_BUY", "ORDER_COMPILATION"],
            "manual_review_required": manual_review_required,
            "blocker_reasons": ["step1 blocker reason"],
            "non_blocker_reasons": ["step1 non blocker note"],
            "report_only": True,
        },
    )


def write_step2_blocked_with_permission(
    *,
    state: str = "NO_OUTPUT",
    manual_review_required: bool = False,
    source_artifact: str = "artifacts/current/step1_research/research_degraded_mode_decision.json",
) -> None:
    write_json(
        step2_decision_builder.step2_blocked_by_research_gate_path(),
        {
            "blocked": True,
            "reason": "research_degraded_mode_gate",
            "state": state,
            "allowed_actions": ["HOLD", "NO_TRADE"],
            "blocked_actions": ["NEW_BUY", "ORDER_COMPILATION"],
            "manual_review_required": manual_review_required,
            "blocker_reasons": ["step2 blocker reason"],
            "source_artifact": source_artifact,
            "recommended_result": "NO_TRADE",
            "report_only": False,
        },
    )


def test_step3_blocks_before_prompt_render_when_step2_research_gate_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_json(step2_decision_builder.step2_blocked_by_research_gate_path(), {"blocked": True})
    write_json(step2_decision_builder.step2_decision_packet_path(), {"stale": "must_not_be_read"})
    monkeypatch.setattr(
        step3_audit_engine,
        "load_decision_packet_text",
        lambda: pytest.fail("Step 3 read a stale Step 2 decision packet"),
    )

    with pytest.raises(UpstreamArtifactGuardError):
        step3_audit_engine.render_step3_prompt()

    assert not step3_audit_engine.step3_prompt_path().exists()
    assert not step3_audit_engine.step3_raw_output_path().exists()
    payload = read_json_file(step3_audit_engine.step3_blocked_by_upstream_gate_path())
    assert_common_blocked_payload(payload)
    assert payload["reason"] == "upstream_research_gate_blocked"
    assert payload["manual_review_required"] is False
    assert (
        payload["blocked_by_artifact"]
        == "artifacts/current/step2_decision_builder/step2_blocked_by_research_gate.json"
    )
    assert payload["missing_required_artifacts"] == []
    # Minimal upstream blocked artifact carries no permission fields and no Step 1
    # decision exists: metadata is unresolved but the gate still failed closed.
    assert payload["upstream_permission"] is None
    assert payload["upstream_permission_read_errors"]
    assert any(
        "upstream_gate_already_blocked" in note
        for note in payload["stale_or_inconsistent_artifacts"]
    )


def test_step3_missing_decision_packet_blocks_with_missing_required_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_text(step2_decision_builder.step2_prompt_path(), "STEP2 PROMPT\n")
    write_text(step2_decision_builder.step2_raw_output_path(), "STEP2 RAW\n")
    write_text(step2_decision_builder.step2_template2_output_path(), "TEMPLATE2 OUTPUT\n")

    with pytest.raises(UpstreamArtifactGuardError):
        step3_audit_engine.render_step3_prompt()

    payload = read_json_file(step3_audit_engine.step3_blocked_by_upstream_gate_path())
    assert_common_blocked_payload(payload)
    assert payload["reason"] == "missing_required_upstream_artifact"
    assert payload["manual_review_required"] is False
    assert payload["blocked_by_artifact"] is None
    assert payload["missing_required_artifacts"] == [
        "artifacts/current/step2_decision_builder/decision_packet.json"
    ]
    # No upstream blocked artifact triggered this; stale note stays empty.
    assert payload["stale_or_inconsistent_artifacts"] == []
    assert payload["upstream_permission"] is None


def test_step3_allowed_upstream_artifacts_preserve_existing_render_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step2_normal_artifacts()

    result = step3_audit_engine.render_step3_prompt()

    assert result["prompt_path"] == str(step3_audit_engine.step3_prompt_path())
    assert step3_audit_engine.step3_prompt_path().exists()
    assert step3_audit_engine.step3_raw_output_path().read_text(encoding="utf-8") == ""
    assert not step3_audit_engine.step3_blocked_by_upstream_gate_path().exists()


def test_step4_blocks_before_prompt_render_when_step2_research_gate_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_json(step2_decision_builder.step2_blocked_by_research_gate_path(), {"blocked": True})
    monkeypatch.setattr(
        step4_order_compiler,
        "load_audited_decision_packet",
        lambda: pytest.fail("Step 4 attempted order-compiler input loading"),
    )

    with pytest.raises(UpstreamArtifactGuardError):
        step4_order_compiler.render_step4_prompt()

    assert not step4_order_compiler.step4_prompt_path().exists()
    payload = read_json_file(step4_order_compiler.step4_blocked_by_upstream_gate_path())
    assert_common_blocked_payload(payload)
    assert payload["reason"] == "upstream_research_gate_blocked"
    assert payload["manual_review_required"] is False
    assert (
        payload["blocked_by_artifact"]
        == "artifacts/current/step2_decision_builder/step2_blocked_by_research_gate.json"
    )
    assert payload["upstream_permission"] is None
    assert payload["upstream_permission_read_errors"]


def test_step4_blocks_when_step3_upstream_gate_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step2_normal_artifacts()
    write_json(step3_audit_engine.step3_blocked_by_upstream_gate_path(), {"blocked": True})

    with pytest.raises(UpstreamArtifactGuardError):
        step4_order_compiler.render_step4_prompt()

    payload = read_json_file(step4_order_compiler.step4_blocked_by_upstream_gate_path())
    assert_common_blocked_payload(payload)
    assert payload["reason"] == "upstream_research_gate_blocked"
    assert payload["manual_review_required"] is False
    assert (
        payload["blocked_by_artifact"]
        == "artifacts/current/step3_audit_engine/step3_blocked_by_upstream_gate.json"
    )
    assert any(
        "upstream_gate_already_blocked" in note
        for note in payload["stale_or_inconsistent_artifacts"]
    )


def test_step4_missing_audited_packet_blocks_with_missing_required_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step2_normal_artifacts()
    write_text(step3_audit_engine.step3_prompt_path(), "STEP3 PROMPT\n")
    write_text(step3_audit_engine.step3_raw_output_path(), "STEP3 RAW\n")
    write_text(step3_audit_engine.step3_template3_audit_path(), "TEMPLATE3 AUDIT\n")

    with pytest.raises(UpstreamArtifactGuardError):
        step4_order_compiler.render_step4_prompt()

    payload = read_json_file(step4_order_compiler.step4_blocked_by_upstream_gate_path())
    assert_common_blocked_payload(payload)
    assert payload["reason"] == "missing_required_upstream_artifact"
    assert payload["manual_review_required"] is False
    assert payload["blocked_by_artifact"] is None
    assert payload["missing_required_artifacts"] == [
        "artifacts/current/step3_audit_engine/audited_decision_packet.json"
    ]
    assert payload["stale_or_inconsistent_artifacts"] == []
    assert payload["upstream_permission"] is None


def test_step4_allowed_upstream_artifacts_preserve_existing_render_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step1_strict_fresh_permission()
    write_step2_normal_artifacts()
    write_step3_normal_artifacts()

    result = step4_order_compiler.render_step4_prompt()

    assert result["prompt_path"] == str(step4_order_compiler.step4_prompt_path())
    assert step4_order_compiler.step4_prompt_path().exists()
    assert step4_order_compiler.step4_raw_output_path().read_text(encoding="utf-8") == ""
    assert not step4_order_compiler.step4_blocked_by_upstream_gate_path().exists()
    assert not step4_order_compiler.step4_blocked_by_final_execution_safety_gate_path().exists()


# --- PR E1: upstream permission metadata propagation -------------------------


def test_step3_blocked_artifact_embeds_upstream_permission_from_step2_and_step1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step1_degraded_decision()
    write_step2_blocked_with_permission()

    with pytest.raises(UpstreamArtifactGuardError):
        step3_audit_engine.render_step3_prompt()

    assert not step3_audit_engine.step3_prompt_path().exists()
    payload = read_json_file(step3_audit_engine.step3_blocked_by_upstream_gate_path())
    permission = payload["upstream_permission"]
    assert permission is not None
    assert permission["state"] == "NO_OUTPUT"
    assert permission["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert permission["blocked_actions"] == ["NEW_BUY", "ORDER_COMPILATION"]
    assert permission["manual_review_required"] is False
    assert permission["recommended_result"] == "NO_TRADE"
    assert (
        permission["source_artifact"]
        == "artifacts/current/step1_research/research_degraded_mode_decision.json"
    )
    # Enriched from the Step 1 decision via the source_artifact pointer.
    assert permission["research_availability"] == "no_output"
    assert permission["non_blocker_reasons"] == ["step1 non blocker note"]
    assert payload["upstream_permission_read_errors"] == []


def test_step4_blocked_artifact_embeds_upstream_permission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step1_degraded_decision()
    write_step2_blocked_with_permission()

    with pytest.raises(UpstreamArtifactGuardError):
        step4_order_compiler.render_step4_prompt()

    payload = read_json_file(step4_order_compiler.step4_blocked_by_upstream_gate_path())
    permission = payload["upstream_permission"]
    assert permission is not None
    assert permission["state"] == "NO_OUTPUT"
    assert permission["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in permission["blocked_actions"]
    assert permission["research_availability"] == "no_output"
    assert payload["upstream_permission_read_errors"] == []


def test_manual_review_required_true_propagates_to_step3_top_level(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step1_degraded_decision(
        state="MANUAL_REVIEW_REQUIRED",
        research_availability="manual_review_required",
        manual_review_required=True,
    )
    write_step2_blocked_with_permission(
        state="MANUAL_REVIEW_REQUIRED",
        manual_review_required=True,
    )

    with pytest.raises(UpstreamArtifactGuardError):
        step3_audit_engine.render_step3_prompt()

    payload = read_json_file(step3_audit_engine.step3_blocked_by_upstream_gate_path())
    assert payload["manual_review_required"] is True
    assert payload["upstream_permission"]["manual_review_required"] is True
    # Still fails closed: no prompt rendered.
    assert not step3_audit_engine.step3_prompt_path().exists()


def test_manual_review_required_true_propagates_to_step4_top_level(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step1_degraded_decision(
        state="MANUAL_REVIEW_REQUIRED",
        research_availability="manual_review_required",
        manual_review_required=True,
    )
    write_step2_blocked_with_permission(
        state="MANUAL_REVIEW_REQUIRED",
        manual_review_required=True,
    )

    with pytest.raises(UpstreamArtifactGuardError):
        step4_order_compiler.render_step4_prompt()

    payload = read_json_file(step4_order_compiler.step4_blocked_by_upstream_gate_path())
    assert payload["manual_review_required"] is True
    assert not step4_order_compiler.step4_prompt_path().exists()


def test_metadata_enrichment_read_failure_records_error_and_still_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    # Step 2 blocked carries permission fields but points at a Step 1 decision
    # that does not exist: permission is still resolved from Step 2, the missing
    # enrichment source is recorded, and the gate still fails closed.
    write_step2_blocked_with_permission(
        source_artifact="artifacts/current/step1_research/research_degraded_mode_decision.json"
    )

    with pytest.raises(UpstreamArtifactGuardError):
        step3_audit_engine.render_step3_prompt()

    assert not step3_audit_engine.step3_prompt_path().exists()
    payload = read_json_file(step3_audit_engine.step3_blocked_by_upstream_gate_path())
    assert payload["upstream_permission"] is not None
    assert payload["upstream_permission"]["state"] == "NO_OUTPUT"
    assert payload["upstream_permission_read_errors"]
    assert any(
        "research_degraded_mode_decision.json" in err
        for err in payload["upstream_permission_read_errors"]
    )
