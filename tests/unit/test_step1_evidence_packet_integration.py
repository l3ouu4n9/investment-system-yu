"""Step 1 integration tests for the report-only evidence packet (R2B).

Assert that `parse_step1_output` writes `evidence_packet.json` in report-only
mode and that its presence does NOT change the degraded-mode decision / allowed
actions (no new action, never NEW_BUY).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.workflow import step1_research
from investment_orchestrator.research.evidence_packet import check_evidence_packet_invariants


# A raw output that parses (valid research_output schema) but yields an INVALID
# strict handoff -> a degraded decision with HOLD/NO_TRADE only.
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


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    artifact_dir = tmp_path / "artifacts" / "current" / "step1_research"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "raw_output.txt").write_text(
        FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return step1_research.parse_step1_output(strategy_settings=_minimal_settings())


def test_parse_writes_evidence_packet_report_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(tmp_path, monkeypatch)

    packet_path = Path(result["evidence_packet_path"])
    assert packet_path.name == "evidence_packet.json"
    assert packet_path.is_file()
    packet = json.loads(packet_path.read_text(encoding="utf-8"))

    assert packet["is_llm_generated"] is False
    assert packet["report_only"] is True
    assert check_evidence_packet_invariants(packet) == []
    # Built from operator settings (not the parsed LLM payload).
    assert packet["universe"]["allowed_buy_tickers"] == ["QQQ", "VOO", "VTI", "VT", "SMH", "IGV"]
    assert packet["budget_settings"]["target_new_buy_budget_this_run"] == 12000.00
    # No portfolio snapshot in the tmp repo -> explicit DATA_GAP, not a crash.
    assert any(g["field"] == "portfolio_snapshot" for g in packet["data_gaps"])


def test_evidence_packet_does_not_change_degraded_mode_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(tmp_path, monkeypatch)

    decision = json.loads(
        Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8")
    )
    # Invalid handoff + no last-good -> degraded; only HOLD / NO_TRADE permitted.
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert decision["fresh_research_available"] is False
    # The decision artifact carries no evidence-packet field (fully independent).
    assert "evidence_packet" not in decision


def test_evidence_packet_failure_does_not_break_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the legacy/current evidence-packet build (the S1A-11 comparison
    # reference AND fallback payload) to raise; parse must still succeed, the
    # degraded-mode decision must still be produced (report-only isolation), and
    # the writer must record ``unwritten`` provenance.
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated evidence packet failure")

    monkeypatch.setattr(step1_research, "build_evidence_packet_and_selection", boom)
    result = _run(tmp_path, monkeypatch)

    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_degraded_mode_decision_path"]).exists()
    assert result["evidence_packet_writer_source"] == "unwritten"
