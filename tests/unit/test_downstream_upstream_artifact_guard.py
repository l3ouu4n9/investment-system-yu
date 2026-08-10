from __future__ import annotations

import hashlib
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
    # Current research admission is satisfied (STRICT_FRESH), so this test keeps
    # exercising what the generic upstream artifact guard actually owns: the
    # presence of an upstream block artifact.
    write_step1_strict_fresh_permission()
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
    # The minimal upstream blocked artifact carries no permission fields, so the
    # metadata falls back to the Step 1 decision and the miss is recorded.
    assert payload["upstream_permission"]["state"] == "STRICT_FRESH"
    assert any(
        "step2_blocked_by_research_gate.json" in err
        for err in payload["upstream_permission_read_errors"]
    )
    assert any(
        "upstream_gate_already_blocked" in note
        for note in payload["stale_or_inconsistent_artifacts"]
    )


def test_step3_missing_decision_packet_blocks_with_missing_required_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    # Admitted current state: the missing residual artifact — not the research
    # state — must be what blocks, preserving the failure classification.
    write_step1_strict_fresh_permission()
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
    assert payload["upstream_permission"]["state"] == "STRICT_FRESH"


def test_step3_allowed_upstream_artifacts_preserve_existing_render_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step1_strict_fresh_permission()
    write_step2_normal_artifacts()

    result = step3_audit_engine.render_step3_prompt()

    assert result["prompt_path"] == str(step3_audit_engine.step3_prompt_path())
    assert step3_audit_engine.step3_prompt_path().exists()
    assert step3_audit_engine.step3_raw_output_path().read_text(encoding="utf-8") == ""
    assert not step3_audit_engine.step3_blocked_by_upstream_gate_path().exists()


def test_step3_residual_step2_artifacts_alone_no_longer_admit_step3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Previously this exact setup rendered a Step 3 prompt; it must now fail closed.

    The residual Step 2 files are byte-identical to the ones the admitted
    ``STRICT_FRESH`` case above renders from, and no Step 2 block artifact
    exists — the *only* difference is the absent current permission decision.
    """
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step2_normal_artifacts()
    monkeypatch.setattr(
        step3_audit_engine,
        "load_decision_packet_text",
        lambda: pytest.fail("Step 3 consumed residual Step 2 content without admission"),
    )

    with pytest.raises(UpstreamArtifactGuardError, match="current research admission"):
        step3_audit_engine.render_step3_prompt()

    assert not step3_audit_engine.step3_prompt_path().exists()
    assert not step3_audit_engine.step3_raw_output_path().exists()
    payload = read_json_file(step3_audit_engine.step3_blocked_by_upstream_gate_path())
    assert_common_blocked_payload(payload)
    assert payload["reason"] == "step3_research_admission_denied"
    assert payload["state"] == "MISSING_RESEARCH_PERMISSION"
    assert payload["step3_allowed"] is False
    assert payload["step4_allowed"] is False
    assert payload["order_compilation_allowed"] is False
    assert payload["new_buy_permission"] is False


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


# --- Step 3 current-state admission ------------------------------------------
#
# The nine currently recognized normal states that must never enter Step 3 are
# declared here independently of the production gate/availability modules: this
# matrix is the authority the implementation is checked against, not a mirror of
# it. ``STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY`` and
# ``STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY`` are deliberately absent:
# they own separate, already-covered promoted paths.

STEP3_BLOCKED_RECOGNIZED_STATES = (
    "STRICT_STALE",
    "STRICT_FRESH_EVIDENCE_ONLY",
    "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE",
    "STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES",
    "DEGRADED_WITH_LAST_GOOD",
    "DEGRADED_NO_RESEARCH",
    "INVALID_CONTRACT",
    "NO_OUTPUT",
    "MANUAL_REVIEW_REQUIRED",
)

STEP3_ADMISSION_DENIED_REASON = "step3_research_admission_denied"


def write_step1_permission_for_state(state: str) -> None:
    """Write a well-formed Step 1 permission artifact for a recognized state."""
    write_json(
        step1_research.step1_research_degraded_mode_decision_path(),
        {
            "state": state,
            "research_availability": state.lower(),
            "allowed_actions": ["HOLD", "NO_TRADE"],
            "blocked_actions": ["NEW_BUY", "ORDER_COMPILATION"],
            "manual_review_required": state == "MANUAL_REVIEW_REQUIRED",
            "blocker_reasons": [f"{state} blocks actionable work"],
            "non_blocker_reasons": [],
            "recommended_result": "NO_TRADE",
            "report_only": True,
        },
    )


def step3_raw_output_text() -> str:
    """A Step 3 raw response the reusable content parser accepts."""
    return (
        "TEMPLATE3_AUDIT_START\nAUDIT BODY\nTEMPLATE3_AUDIT_END\n"
        "AUDITED_DECISION_PACKET_START\n"
        + json.dumps(audited_decision_packet())
        + "\nAUDITED_DECISION_PACKET_END\n"
    )


def seed_prior_step3_outputs() -> dict[Path, bytes]:
    """Seed preexisting Step 3 prompt/raw/audit/patch/packet artifacts."""
    write_text(step3_audit_engine.step3_prompt_path(), "PRIOR STEP3 PROMPT\n")
    write_text(step3_audit_engine.step3_raw_output_path(), "PRIOR STEP3 RAW\n")
    write_text(step3_audit_engine.step3_template3_audit_path(), "PRIOR TEMPLATE3 AUDIT\n")
    write_text(step3_audit_engine.step3_template2_patch_path(), "PRIOR TEMPLATE2 PATCH\n")
    write_json(
        step3_audit_engine.step3_audited_decision_packet_path(),
        {"audit_passed": True, "prior": "must_not_be_repaired"},
    )
    return {
        path: path.read_bytes()
        for path in (
            step3_audit_engine.step3_prompt_path(),
            step3_audit_engine.step3_raw_output_path(),
            step3_audit_engine.step3_template3_audit_path(),
            step3_audit_engine.step3_template2_patch_path(),
            step3_audit_engine.step3_audited_decision_packet_path(),
        )
    }


def assert_prior_step3_outputs_unchanged(prior: dict[Path, bytes]) -> None:
    """Old Step 3 bytes must survive a denial exactly — never repaired or deleted."""
    for path, original in prior.items():
        assert path.is_file(), f"denied Step 3 invocation deleted {path.name}"
        current = path.read_bytes()
        assert hashlib.sha256(current).hexdigest() == hashlib.sha256(original).hexdigest()
        assert current == original


def step3_dir_filenames() -> set[str]:
    return {path.name for path in step3_audit_engine.step3_artifact_dir().iterdir()}


def forbid_step3_content_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tripwires on every residual-content read and Step 3 content write."""
    for attribute, label in (
        ("load_template2_output_text", "Step 2 template2 output"),
        ("load_decision_packet_text", "Step 2 decision packet"),
        ("build_step3_prompt_text", "Step 3 prompt body"),
    ):
        monkeypatch.setattr(
            step3_audit_engine,
            attribute,
            lambda *_a, _label=label, **_k: pytest.fail(
                f"denied Step 3 invocation consumed {_label}"
            ),
        )
    monkeypatch.setattr(
        step3_audit_engine,
        "extract_audit_and_audited_packet",
        lambda **_kwargs: pytest.fail("denied Step 3 invocation reached the Step 3 extractor"),
    )


@pytest.mark.parametrize("state", STEP3_BLOCKED_RECOGNIZED_STATES)
@pytest.mark.parametrize("command", ["render", "parse"])
def test_step3_guard_rejects_every_blocked_recognized_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    command: str,
) -> None:
    """Every blocked recognized state fails closed before any Step 3 work.

    Residual Step 2 artifacts are complete and valid and no Step 2 block artifact
    exists, so nothing except the current permission decision denies the run.
    """
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step2_normal_artifacts()
    write_step1_permission_for_state(state)
    prior = seed_prior_step3_outputs()
    seeded_filenames = step3_dir_filenames()
    forbid_step3_content_work(monkeypatch)

    invoke = (
        step3_audit_engine.render_step3_prompt
        if command == "render"
        else step3_audit_engine.parse_step3_output
    )
    with pytest.raises(UpstreamArtifactGuardError, match="current research admission"):
        invoke()

    payload = read_json_file(step3_audit_engine.step3_blocked_by_upstream_gate_path())
    assert_common_blocked_payload(payload)
    assert payload["reason"] == STEP3_ADMISSION_DENIED_REASON
    assert payload["state"] == state
    assert payload["mode"] == "blocked"
    assert payload["step3_allowed"] is False
    assert payload["step4_allowed"] is False
    assert payload["order_compilation_allowed"] is False
    assert payload["new_buy_permission"] is False
    assert payload["manual_review_required"] is (state == "MANUAL_REVIEW_REQUIRED")

    # No new Step 3 artifact beyond the block itself, and old bytes are intact.
    assert step3_dir_filenames() == seeded_filenames | {
        "step3_blocked_by_upstream_gate.json"
    }
    assert_prior_step3_outputs_unchanged(prior)
    assert not step3_audit_engine.step3_promoted_audit_only_path().exists()
    assert not step3_audit_engine.step3_blocked_by_promoted_decision_only_gate_path().exists()


@pytest.mark.parametrize("command", ["render", "parse"])
def test_step3_missing_permission_decision_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step2_normal_artifacts()
    forbid_step3_content_work(monkeypatch)
    assert not step1_research.step1_research_degraded_mode_decision_path().exists()

    invoke = (
        step3_audit_engine.render_step3_prompt
        if command == "render"
        else step3_audit_engine.parse_step3_output
    )
    with pytest.raises(UpstreamArtifactGuardError, match="current research admission"):
        invoke()

    payload = read_json_file(step3_audit_engine.step3_blocked_by_upstream_gate_path())
    assert payload["reason"] == STEP3_ADMISSION_DENIED_REASON
    assert payload["state"] == "MISSING_RESEARCH_PERMISSION"
    assert not step3_audit_engine.step3_prompt_path().exists()
    assert not step3_audit_engine.step3_audited_decision_packet_path().exists()


@pytest.mark.parametrize(
    ("label", "decision_text"),
    [
        ("malformed_json", "{not json"),
        ("non_object_root", "[]"),
        ("state_not_a_string", json.dumps({"state": 7, "allowed_actions": [], "manual_review_required": False})),
        (
            "unknown_state",
            json.dumps(
                {
                    "state": "H1_SOME_FUTURE_STATE",
                    "research_availability": "h1_some_future_state",
                    "allowed_actions": ["HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION"],
                    "blocked_actions": [],
                    "manual_review_required": False,
                }
            ),
        ),
    ],
)
def test_step3_malformed_or_unknown_permission_decision_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    decision_text: str,
) -> None:
    """Malformed and unknown current decisions fail closed before Step 3 work.

    ``unknown_state`` deliberately claims the full actionable action set: an
    unrecognized state can never buy Step 3 admission by widening its own
    ``allowed_actions``. This is also why a future availability state needs no
    Step 3-specific branch — it is simply not the admitted normal state.
    """
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step2_normal_artifacts()
    write_text(step1_research.step1_research_degraded_mode_decision_path(), decision_text)
    prior = seed_prior_step3_outputs()
    forbid_step3_content_work(monkeypatch)

    with pytest.raises(UpstreamArtifactGuardError, match="current research admission"):
        step3_audit_engine.render_step3_prompt()

    payload = read_json_file(step3_audit_engine.step3_blocked_by_upstream_gate_path())
    assert payload["reason"] == STEP3_ADMISSION_DENIED_REASON
    if label == "unknown_state":
        assert payload["state"] == "H1_SOME_FUTURE_STATE"
    else:
        assert payload["state"] == "MALFORMED_RESEARCH_PERMISSION"
        assert payload["malformed_reasons"]
    assert_prior_step3_outputs_unchanged(prior)


def test_strict_fresh_render_admits_before_reading_residual_step2_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step1_strict_fresh_permission()
    write_step2_normal_artifacts()

    calls: list[str] = []
    real_guard = step3_audit_engine.enforce_step3_upstream_guard
    real_loader = step3_audit_engine.load_decision_packet_text

    def tracked_guard() -> Any:
        calls.append("admission")
        return real_guard()

    def tracked_loader() -> str:
        calls.append("decision_packet")
        return real_loader()

    monkeypatch.setattr(step3_audit_engine, "enforce_step3_upstream_guard", tracked_guard)
    monkeypatch.setattr(step3_audit_engine, "load_decision_packet_text", tracked_loader)

    result = step3_audit_engine.render_step3_prompt()

    assert calls == ["admission", "decision_packet"]
    assert result["prompt_path"] == str(step3_audit_engine.step3_prompt_path())
    assert step3_audit_engine.step3_prompt_path().is_file()
    assert not step3_audit_engine.step3_blocked_by_upstream_gate_path().exists()


def test_strict_fresh_parse_admits_before_extracting_step3_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step1_strict_fresh_permission()
    write_step2_normal_artifacts()
    write_text(step3_audit_engine.step3_raw_output_path(), step3_raw_output_text())

    calls: list[str] = []
    real_guard = step3_audit_engine.enforce_step3_upstream_guard
    real_extract = step3_audit_engine.extract_audit_and_audited_packet

    def tracked_guard() -> Any:
        calls.append("admission")
        return real_guard()

    def tracked_extract(**kwargs: Any) -> Any:
        calls.append("extract")
        return real_extract(**kwargs)

    monkeypatch.setattr(step3_audit_engine, "enforce_step3_upstream_guard", tracked_guard)
    monkeypatch.setattr(step3_audit_engine, "extract_audit_and_audited_packet", tracked_extract)

    result = step3_audit_engine.parse_step3_output()

    assert calls == ["admission", "extract"]
    assert result["audited_decision_packet_path"] == str(
        step3_audit_engine.step3_audited_decision_packet_path()
    )
    assert result["audit_passed"] == "True"
    assert step3_audit_engine.step3_template3_audit_path().is_file()
    assert not step3_audit_engine.step3_blocked_by_upstream_gate_path().exists()


def test_denied_step3_block_propagates_to_step4_upstream_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Step 3 block artifact is the shared invariant Step 4 refuses on."""
    prepare_tmp_repo(tmp_path, monkeypatch)
    write_step2_normal_artifacts()
    write_step3_normal_artifacts()
    write_step1_permission_for_state("DEGRADED_NO_RESEARCH")

    with pytest.raises(UpstreamArtifactGuardError, match="current research admission"):
        step3_audit_engine.render_step3_prompt()
    assert step3_audit_engine.step3_blocked_by_upstream_gate_path().is_file()

    monkeypatch.setattr(
        step4_order_compiler,
        "load_audited_decision_packet",
        lambda: pytest.fail("Step 4 consumed residual artifacts after a Step 3 block"),
    )
    with pytest.raises(UpstreamArtifactGuardError):
        step4_order_compiler.render_step4_prompt()

    payload = read_json_file(step4_order_compiler.step4_blocked_by_upstream_gate_path())
    assert_common_blocked_payload(payload)
    assert payload["reason"] == "upstream_research_gate_blocked"
    assert (
        payload["blocked_by_artifact"]
        == "artifacts/current/step3_audit_engine/step3_blocked_by_upstream_gate.json"
    )
    assert not step4_order_compiler.step4_prompt_path().exists()
