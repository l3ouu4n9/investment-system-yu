"""Step 1 integration tests for the report-only analyst memo (R2C).

Assert that `render_step1_analyst_memo_prompt` renders the small memo prompt from
the deterministic evidence packet, that `parse_step1_output` writes the memo
validation artifacts only when a raw memo output exists, and that the memo —
valid or invalid — never changes the degraded-mode decision or allowed actions
(never NEW_BUY). Report-only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.workflow import step1_research


# A raw research output that parses but yields an INVALID strict handoff ->
# degraded decision (HOLD/NO_TRADE only). Same fixture used by the R2B test.
FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "step1_contract_failures"
    / "current_step1_raw_output_minimal.txt"
)


def _minimal_settings() -> dict[str, Any]:
    return {
        "as_of": "2026-06-28",
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
        "user_approved_extended_etf_static_list": ["GRID", "CIBR"],
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.00,
    }


def _valid_memo() -> dict[str, Any]:
    return {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "as_of_date": "2026-06-28",
        "regime_view": "constructive",
        "key_risks": ["rates"],
        "opportunity_summary": "AI compute",
        "ticker_relative_view": [{"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "anchor"}],
        "preferred_exposures": ["AI infrastructure"],
        "avoid_or_deprioritize": [],
        "scheduled_event_interpretation": [],
        "confidence": "medium",
        "data_gaps": [],
        "source_notes": [],
    }


def _setup_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    artifact_dir = tmp_path / "artifacts" / "current" / "step1_research"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "raw_output.txt").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return artifact_dir


# --- render ------------------------------------------------------------------


def test_render_writes_memo_prompt_from_evidence_packet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_repo(tmp_path, monkeypatch)

    result = step1_research.render_step1_analyst_memo_prompt(strategy_settings=_minimal_settings())

    prompt_path = Path(result["analyst_memo_prompt_path"])
    assert prompt_path.name == "analyst_memo_prompt.txt"
    assert prompt_path.is_file()
    text = prompt_path.read_text(encoding="utf-8")
    # The evidence packet (with the operator universe) is injected; no placeholder left.
    assert "QQQ" in text
    assert "{{ evidence_packet_json }}" not in text
    assert "analyst_memo_v1" in text
    # The evidence packet artifact and a blank raw-output stub are prepared.
    assert Path(result["evidence_packet_path"]).is_file()
    raw_path = Path(result["analyst_memo_raw_output_path"])
    assert raw_path.is_file() and raw_path.read_text(encoding="utf-8") == ""


# --- parse: writes validation artifacts when raw memo exists -----------------


def test_parse_writes_memo_validation_artifacts_for_valid_memo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_valid_memo()), encoding="utf-8"
    )

    result = step1_research.parse_step1_output(strategy_settings=_minimal_settings())

    assert result["analyst_memo_present"] == "True"
    assert result["analyst_memo_valid"] == "True"
    validation = json.loads(Path(result["analyst_memo_validation_path"]).read_text(encoding="utf-8"))
    assert validation["valid"] is True
    assert validation["report_only"] is True
    assert validation["problems"] == []
    memo = json.loads(Path(result["analyst_memo_path"]).read_text(encoding="utf-8"))
    assert memo["schema_version"] == "analyst_memo_v1"


def test_parse_without_raw_memo_writes_no_memo_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_dir = _setup_repo(tmp_path, monkeypatch)

    result = step1_research.parse_step1_output(strategy_settings=_minimal_settings())

    assert result["analyst_memo_present"] == "False"
    assert result["analyst_memo_valid"] == "False"
    # No memo => no memo artifacts written (only when a raw memo output exists).
    assert not (artifact_dir / "analyst_memo.json").exists()
    assert not (artifact_dir / "analyst_memo_validation.json").exists()
    # The existing degraded-mode artifact is still produced normally.
    assert Path(result["research_degraded_mode_decision_path"]).exists()


# --- invalid memo does not change Step 1 degraded-mode artifacts -------------


def test_invalid_memo_does_not_change_degraded_mode_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    invalid_memo = _valid_memo()
    invalid_memo["ticker_relative_view"] = [{"ticker": "TSLA", "stance": "prefer"}]  # out-of-universe
    invalid_memo["target_new_buy_budget_this_run"] = 50000  # forbidden budget
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(json.dumps(invalid_memo), encoding="utf-8")

    result = step1_research.parse_step1_output(strategy_settings=_minimal_settings())

    # Memo is marked invalid...
    assert result["analyst_memo_present"] == "True"
    assert result["analyst_memo_valid"] == "False"
    validation = json.loads(Path(result["analyst_memo_validation_path"]).read_text(encoding="utf-8"))
    assert validation["valid"] is False
    assert any("TSLA" in p for p in validation["problems"])
    assert any("target_new_buy_budget_this_run" in p for p in validation["problems"])

    # ...but the degraded-mode decision is unchanged: HOLD/NO_TRADE only, no NEW_BUY.
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert decision["fresh_research_available"] is False
    assert "analyst_memo" not in decision  # fully independent


def test_valid_memo_does_not_permit_new_buy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(json.dumps(_valid_memo()), encoding="utf-8")

    result = step1_research.parse_step1_output(strategy_settings=_minimal_settings())

    # Even a fully valid memo is report-only: it cannot enable NEW_BUY.
    assert result["analyst_memo_valid"] == "True"
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]


# --- report-only isolation ---------------------------------------------------


def test_memo_parse_failure_does_not_break_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(json.dumps(_valid_memo()), encoding="utf-8")

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated analyst memo parse failure")

    monkeypatch.setattr(step1_research, "parse_analyst_memo_text", boom)
    result = step1_research.parse_step1_output(strategy_settings=_minimal_settings())

    # Parse still succeeds and the degraded-mode decision is still produced.
    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_degraded_mode_decision_path"]).exists()
    assert result["analyst_memo_present"] == "False"
