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
