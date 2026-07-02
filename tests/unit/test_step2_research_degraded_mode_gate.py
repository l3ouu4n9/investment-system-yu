from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.common.io import write_json, write_text
from investment_orchestrator.state.research_degraded_mode_gate import (
    ResearchDegradedModeGateError,
)
from investment_orchestrator.workflow import step1_research, step2_decision_builder


SOURCE_ARTIFACT = "artifacts/current/step1_research/research_degraded_mode_decision.json"
BAD_RESEARCH_SENTINEL = "BAD_RESEARCH_SENTINEL_SHOULD_NOT_ENTER_PROMPT"


def strict_fresh_permission() -> dict[str, Any]:
    return {
        "state": "STRICT_FRESH",
        "research_availability": "strict_fresh",
        "fresh_research_available": True,
        "handoff_valid": True,
        "handoff_stale": False,
        "settings_hash_match": True,
        "universe_match": True,
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
        "blocker_reasons": [],
        "non_blocker_reasons": [],
        "report_only": True,
    }


def blocked_permission(state: str, *, manual_review_required: bool = False) -> dict[str, Any]:
    return {
        "state": state,
        "research_availability": state.lower(),
        "fresh_research_available": False,
        "handoff_valid": False,
        "handoff_stale": state == "STRICT_STALE",
        "settings_hash_match": None,
        "universe_match": None,
        "allowed_actions": ["HOLD", "NO_TRADE"],
        "blocked_actions": ["NEW_BUY", "ORDER_COMPILATION"],
        "manual_review_required": manual_review_required,
        "blocker_reasons": [f"{state} blocks order-generating Step 2."],
        "non_blocker_reasons": [],
        "report_only": True,
    }


def prepare_tmp_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    permission: dict[str, Any] | None = None,
    permission_text: str | None = None,
) -> None:
    monkeypatch.setattr(step2_decision_builder, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)

    write_text(
        tmp_path / "prompts" / "strategy_a_decision_builder.txt",
        "RESEARCH\n{{ research_json }}\nPORTFOLIO\n{{ portfolio_snapshot }}\nSETTINGS\n{{ strategy_settings }}\n",
    )
    write_text(tmp_path / "inputs" / "current" / "strategy_settings.yaml", "as_of: '2026-06-22'\n")
    write_text(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", "QQQ | 1 | 100\n")
    write_json(
        step1_research.step1_research_output_path(),
        {
            "schema_version": "1.0",
            "as_of": "2026-06-22",
            "sentinel": BAD_RESEARCH_SENTINEL,
        },
    )
    if permission is not None:
        write_json(step1_research.step1_research_degraded_mode_decision_path(), permission)
    if permission_text is not None:
        write_text(step1_research.step1_research_degraded_mode_decision_path(), permission_text)


def read_blocked_artifact() -> dict[str, Any]:
    payload = json.loads(
        step2_decision_builder.step2_blocked_by_research_gate_path().read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload


def assert_render_blocked() -> dict[str, Any]:
    with pytest.raises(ResearchDegradedModeGateError, match="Step 2 blocked"):
        step2_decision_builder.render_step2_prompt()
    assert not step2_decision_builder.step2_prompt_path().exists()
    assert not step2_decision_builder.step2_raw_output_path().exists()
    return read_blocked_artifact()


def test_strict_fresh_permission_allows_existing_step2_render_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=strict_fresh_permission())

    result = step2_decision_builder.render_step2_prompt()

    assert result["prompt_path"] == str(step2_decision_builder.step2_prompt_path())
    assert step2_decision_builder.step2_prompt_path().exists()
    assert step2_decision_builder.step2_raw_output_path().read_text(encoding="utf-8") == ""
    prompt = step2_decision_builder.step2_prompt_path().read_text(encoding="utf-8")
    assert BAD_RESEARCH_SENTINEL in prompt
    assert not step2_decision_builder.step2_blocked_by_research_gate_path().exists()


@pytest.mark.parametrize(
    ("state", "manual_review_required"),
    [
        ("NO_OUTPUT", False),
        ("INVALID_CONTRACT", False),
        ("DEGRADED_WITH_LAST_GOOD", False),
        ("STRICT_STALE", False),
        # R2E.1: the compiled evidence-first state is non-actionable and must be
        # blocked by the Step 2 gate exactly like the degraded states.
        ("STRICT_FRESH_EVIDENCE_ONLY", False),
        # R2E.4: the grounded-memo state is likewise non-actionable and must block.
        ("STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE", False),
        # R2E.5b-5b: promoted handoff recognized, but gates remain closed.
        ("STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES", False),
        ("MANUAL_REVIEW_REQUIRED", True),
    ],
)
def test_non_strict_fresh_permissions_block_before_step2_prompt_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    manual_review_required: bool,
) -> None:
    prepare_tmp_repo(
        tmp_path,
        monkeypatch,
        permission=blocked_permission(state, manual_review_required=manual_review_required),
    )

    blocked = assert_render_blocked()

    assert blocked["blocked"] is True
    assert blocked["reason"] == "research_degraded_mode_gate"
    assert blocked["state"] == state
    assert blocked["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in blocked["blocked_actions"]
    assert "ORDER_COMPILATION" in blocked["blocked_actions"]
    assert blocked["manual_review_required"] is manual_review_required
    assert blocked["source_artifact"] == SOURCE_ARTIFACT
    assert blocked["recommended_result"] == "NO_TRADE"
    assert blocked["report_only"] is False
    assert blocked["blocker_reasons"]


def test_missing_permission_artifact_fails_closed_and_writes_blocked_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=None)

    blocked = assert_render_blocked()

    assert blocked["state"] == "MISSING_RESEARCH_PERMISSION"
    assert blocked["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert blocked["blocked_actions"] == ["NEW_BUY", "ORDER_COMPILATION"]
    assert blocked["recommended_result"] == "NO_TRADE"
    assert blocked["source_artifact"] == SOURCE_ARTIFACT


def test_strict_fresh_without_order_compilation_permission_still_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    permission = strict_fresh_permission()
    permission["allowed_actions"] = ["HOLD", "NO_TRADE", "NEW_BUY"]

    prepare_tmp_repo(tmp_path, monkeypatch, permission=permission)

    blocked = assert_render_blocked()

    assert blocked["state"] == "STRICT_FRESH"
    assert "ORDER_COMPILATION" in blocked["blocked_actions"]
    assert any("ORDER_COMPILATION" in reason for reason in blocked["blocker_reasons"])


def test_malformed_permission_artifact_fails_closed_and_writes_blocked_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission_text="{not valid json")

    blocked = assert_render_blocked()

    assert blocked["state"] == "MALFORMED_RESEARCH_PERMISSION"
    assert blocked["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert blocked["blocked_actions"] == ["NEW_BUY", "ORDER_COMPILATION"]
    assert blocked["recommended_result"] == "NO_TRADE"
    assert blocked["blocker_reasons"]


def test_blocked_path_does_not_read_bad_research_into_step2_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=blocked_permission("NO_OUTPUT"))

    blocked = assert_render_blocked()

    assert blocked["state"] == "NO_OUTPUT"
    assert not step2_decision_builder.step2_prompt_path().exists()


# --- R2E.5b-6c promoted Step 2 decision-only path -----------------------------


def promoted_decision_only_permission(**overrides: Any) -> dict[str, Any]:
    permission = {
        "state": "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY",
        "research_availability": "strict_fresh_compiled_actionable_step2_decision_only",
        "fresh_research_available": False,
        "handoff_valid": False,
        "handoff_stale": False,
        "settings_hash_match": None,
        "universe_match": None,
        "allowed_actions": ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"],
        "blocked_actions": [
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "manual_review_required": False,
        "blocker_reasons": [
            "promoted_step2_decision_only_enabled",
            "new_buy_requires_future_gate_pr",
            "order_compilation_requires_future_gate_pr",
            "final_execution_requires_future_gate_pr",
        ],
        "non_blocker_reasons": [],
        "source": "promoted_compiled_actionable_handoff",
        "promoted_step2_decision_only": True,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "permission_effect": "promoted_step2_decision_only",
        "not_authorization": True,
        "report_only": True,
    }
    permission.update(overrides)
    return permission


def test_promoted_decision_only_gate_allows_decision_only_mode() -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    gate = evaluate_step2_research_gate(promoted_decision_only_permission())

    assert gate.allowed is True
    assert gate.mode == "promoted_step2_decision_only"
    assert gate.order_compilation_allowed is False
    assert gate.new_buy_permission is False
    assert gate.step3_allowed is False
    assert gate.step4_allowed is False
    assert gate.recommended_terminal_result_after_step2 == "NO_TRADE_PENDING_FINAL_GATES"
    # The order actions stay explicitly blocked.
    assert "NEW_BUY" in gate.blocked_actions
    assert "ORDER_COMPILATION" in gate.blocked_actions


def test_promoted_decision_only_gate_blocks_without_promoted_action() -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    gate = evaluate_step2_research_gate(
        promoted_decision_only_permission(allowed_actions=["HOLD", "NO_TRADE"])
    )
    assert gate.allowed is False
    assert gate.mode == "blocked"
    assert any("PROMOTED_RESEARCH_DECISION" in reason for reason in gate.blocker_reasons)


def test_promoted_decision_only_gate_blocks_widened_order_actions() -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    for widened in (
        ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION", "NEW_BUY"],
        ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION", "ORDER_COMPILATION"],
    ):
        gate = evaluate_step2_research_gate(
            promoted_decision_only_permission(allowed_actions=widened)
        )
        assert gate.allowed is False, widened
        assert gate.mode == "blocked"
        assert any("must not allow NEW_BUY / ORDER_COMPILATION" in r for r in gate.blocker_reasons)


def test_promoted_decision_only_gate_blocks_on_marker_or_source_mismatch() -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    for overrides in (
        {"source": "raw_research_handoff"},
        {"promoted_step2_decision_only": False},
        {"manual_review_required": True},
    ):
        gate = evaluate_step2_research_gate(promoted_decision_only_permission(**overrides))
        assert gate.allowed is False, overrides
        assert gate.mode == "blocked"


def test_legacy_strict_fresh_gate_fields_report_full_permissions() -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    gate = evaluate_step2_research_gate(strict_fresh_permission())

    assert gate.allowed is True
    assert gate.mode == "strict_fresh_actionable"
    assert gate.order_compilation_allowed is True
    assert gate.new_buy_permission is True
    assert gate.step3_allowed is True
    assert gate.step4_allowed is True
    assert gate.recommended_terminal_result_after_step2 is None


def test_promoted_decision_only_render_fails_closed_without_pointer_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The gate allows decision-only, but the promoted render re-verifies the
    # pointer / effective handoff / validation on disk; with none present the
    # render must fail closed and write the blocked artifact.
    prepare_tmp_repo(tmp_path, monkeypatch, permission=promoted_decision_only_permission())

    with pytest.raises(ResearchDegradedModeGateError, match="promoted decision-only verification"):
        step2_decision_builder.render_step2_prompt()

    assert not step2_decision_builder.step2_prompt_path().exists()
    blocked = read_blocked_artifact()
    assert blocked["reason"] == "promoted_step2_verification_failed"
    assert blocked["state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    assert blocked["order_compilation_allowed"] is False
    assert "pointer_missing" in blocked["blocker_reasons"]
    assert blocked["recommended_result"] == "NO_TRADE"
