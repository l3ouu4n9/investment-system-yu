"""Step 1 integration tests for the report-only handoff compiler (R2D).

Assert that `parse_step1_output` writes the compiled_* artifacts, that the
compiled candidate is validated with the existing validator, and that the
compiler — in any mode — never changes the degraded-mode decision / allowed
actions and never feeds the availability evaluator. Report-only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.workflow import step1_research
from investment_orchestrator.research.handoff_compiler import (
    COMPILATION_MODE_EVIDENCE_ONLY,
    COMPILATION_MODE_EVIDENCE_PLUS_MEMO,
    COMPILATION_MODE_INVALID_MEMO_IGNORED,
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


def _valid_memo() -> dict[str, Any]:
    return {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "as_of_date": "2026-06-28",
        "regime_view": "constructive",
        "key_risks": [],
        "opportunity_summary": "AI compute",
        "ticker_relative_view": [{"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "anchor"}],
        "preferred_exposures": [],
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


# --- writes compiled artifacts -----------------------------------------------


def test_parse_writes_compiled_artifacts_evidence_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    candidate_path = Path(result["compiled_research_handoff_candidate_path"])
    validation_path = Path(result["compiled_research_handoff_validation_path"])
    metadata_path = Path(result["compiled_research_handoff_metadata_path"])
    assert candidate_path.name == "compiled_research_handoff_candidate.json"
    assert validation_path.name == "compiled_research_handoff_validation.json"
    assert metadata_path.name == "compiled_research_handoff_metadata.json"
    assert candidate_path.is_file() and validation_path.is_file() and metadata_path.is_file()

    assert result["compiled_research_handoff_mode"] == COMPILATION_MODE_EVIDENCE_ONLY
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert candidate["is_llm_generated"] is False
    # The compiled candidate is structurally valid per the existing validator.
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    assert validation["valid"] is True
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["permission_effect"] == "none"
    assert metadata["analyst_memo_present"] is False


def test_parse_writes_compiled_artifacts_evidence_plus_memo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(json.dumps(_valid_memo()), encoding="utf-8")

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["compiled_research_handoff_mode"] == COMPILATION_MODE_EVIDENCE_PLUS_MEMO
    metadata = json.loads(Path(result["compiled_research_handoff_metadata_path"]).read_text(encoding="utf-8"))
    assert metadata["analyst_memo_present"] is True
    assert metadata["analyst_memo_valid"] is True
    candidate = json.loads(Path(result["compiled_research_handoff_candidate_path"]).read_text(encoding="utf-8"))
    # Memo is qualitative only: no NEW_BUY support even in evidence_plus_memo.
    assert candidate["strategy_a_research_handoff"]["positive_delta_research_supported"] == []


def test_parse_invalid_memo_compiles_invalid_memo_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    bad = _valid_memo()
    bad["ticker_relative_view"] = [{"ticker": "TSLA", "stance": "prefer"}]  # out-of-universe
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(json.dumps(bad), encoding="utf-8")

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["compiled_research_handoff_mode"] == COMPILATION_MODE_INVALID_MEMO_IGNORED
    candidate = json.loads(Path(result["compiled_research_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert "TSLA" not in candidate["trade_universe"]["allowed_buy_tickers"]


# --- does not change the degraded-mode decision / allowed actions ------------


def test_compiled_handoff_does_not_change_allowed_actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    # R2E.1: the invalid raw handoff + valid+fresh compiled handoff is now recognized
    # as STRICT_FRESH_EVIDENCE_ONLY, but it is non-actionable — HOLD/NO_TRADE only and
    # never NEW_BUY. The compiled handoff changes the *label/observability*, never the
    # permission set.
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]
    assert decision["fresh_research_available"] is False
    assert decision["state"] == "STRICT_FRESH_EVIDENCE_ONLY"
    assert decision["source"] == "compiled_research_handoff"
    assert decision["permission_effect"] == "none"


def test_valid_memo_compiled_handoff_still_no_new_buy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(json.dumps(_valid_memo()), encoding="utf-8")

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]


# --- report-only isolation ---------------------------------------------------


def test_compiler_failure_does_not_break_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated compiler failure")

    monkeypatch.setattr(step1_research, "write_compiled_research_handoff", boom)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Parse still succeeds and the degraded-mode decision is still produced.
    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_degraded_mode_decision_path"]).exists()
    assert result["compiled_research_handoff_mode"] == ""


# --- R2E.3 support-signal artifact (report-only) -----------------------------


def test_parse_writes_support_signals_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    signals_path = Path(result["compiled_support_signals_path"])
    assert signals_path.name == "compiled_support_signals.json"
    assert signals_path.is_file()
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    # Report-only / non-authoritative invariants.
    assert signals["is_llm_generated"] is False
    assert signals["report_only"] is True
    assert signals["permission_effect"] == "none"
    assert signals["accepted_support_signals"] == []
    assert "missing_valid_anchor_source" in signals["global_blockers"]
    # Evidence-only: absent memo global blocker.
    assert "analyst_memo_absent" in signals["global_blockers"]


def test_support_signals_do_not_change_actionability_or_allowed_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(json.dumps(_valid_memo()), encoding="utf-8")

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # A valid memo produces candidate signals, but the compiled handoff stays
    # non-actionable and the permission set is unchanged.
    signals = json.loads(Path(result["compiled_support_signals_path"]).read_text(encoding="utf-8"))
    assert signals["compilation_mode"] == COMPILATION_MODE_EVIDENCE_PLUS_MEMO
    assert signals["accepted_support_signals"] == []
    assert {s["ticker"] for s in signals["candidate_ticker_signals"]} == {"QQQ"}

    candidate = json.loads(Path(result["compiled_research_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert candidate["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in candidate["buy_universe_scorecard"])

    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    assert decision["state"] == "STRICT_FRESH_EVIDENCE_ONLY"
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]


# --- R2E.5a research anchors in the evidence packet (report-only) ------------


def test_evidence_packet_includes_research_anchors_missing_data_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    packet = json.loads(Path(result["evidence_packet_path"]).read_text(encoding="utf-8"))
    anchors = packet["research_anchors"]
    # No inputs/current/research_anchors.yaml in the tmp repo → available:false + DATA_GAP.
    assert anchors["available"] is False
    assert "research_anchors_missing" in anchors["data_gap"]
    assert anchors["permission_effect"] == "none"
    assert packet["is_llm_generated"] is False


def test_present_research_anchors_flow_through_but_no_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(json.dumps(_valid_memo()), encoding="utf-8")
    inputs_dir = tmp_path / "inputs" / "current"
    inputs_dir.mkdir(parents=True)
    (inputs_dir / "research_anchors.yaml").write_text(
        "schema_version: research_anchors_v1\n"
        "as_of_date: 2026-06-28\n"
        "is_llm_generated: false\n"
        "anchors:\n"
        "  - anchor_id: AI_CAPEX_2026H2\n"
        "    anchor_type: structural_theme\n"
        "    applicable_tickers: [QQQ]\n"
        "    anchor_date_et: 2026-06-15\n"
        "    valid_from: 2026-06-01\n"
        "    valid_until: 2026-07-31\n"
        "    source_type: operator\n"
        "    confidence_floor: medium\n",
        encoding="utf-8",
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # The anchor flows into the evidence packet as a valid, fresh anchor...
    packet = json.loads(Path(result["evidence_packet_path"]).read_text(encoding="utf-8"))
    anchors = packet["research_anchors"]
    assert anchors["available"] is True
    assert anchors["valid"] is True
    assert anchors["valid_anchor_count"] == 1
    assert anchors["consumed_for_support_acceptance"] is False

    # ...but it does NOT change support-signal acceptance or actionability.
    signals = json.loads(Path(result["compiled_support_signals_path"]).read_text(encoding="utf-8"))
    assert signals["accepted_support_signals"] == []
    candidate = json.loads(Path(result["compiled_research_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert candidate["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in candidate["buy_universe_scorecard"])

    # ...and it does NOT change permissions / availability state.
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    assert decision["state"] == "STRICT_FRESH_EVIDENCE_ONLY"
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]


def test_step1_parse_accepts_anchor_grounded_signal_but_no_permission_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    memo = {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "as_of_date": "2026-06-28",
        "regime_view": "constructive",
        "confidence": "high",
        "ticker_relative_view": [
            {"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "AI anchor", "anchor_id_refs": ["AI_CAPEX_2026H2"]}
        ],
        "avoid_or_deprioritize": [],
        "data_gaps": [],
        "source_notes": [{"claim": "AI capex", "source": "10-K", "source_quality": "official"}],
    }
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(json.dumps(memo), encoding="utf-8")
    inputs_dir = tmp_path / "inputs" / "current"
    inputs_dir.mkdir(parents=True)
    (inputs_dir / "research_anchors.yaml").write_text(
        "schema_version: research_anchors_v1\n"
        "as_of_date: 2026-06-28\n"
        "is_llm_generated: false\n"
        "anchors:\n"
        "  - anchor_id: AI_CAPEX_2026H2\n"
        "    anchor_type: structural_theme\n"
        "    applicable_tickers: [QQQ]\n"
        "    anchor_date_et: 2026-06-15\n"
        "    valid_from: 2026-06-01\n"
        "    valid_until: 2026-07-31\n"
        "    source_type: operator\n"
        "    confidence_floor: medium\n",
        encoding="utf-8",
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Acceptance flows through the full parse path (report-only)...
    signals = json.loads(Path(result["compiled_support_signals_path"]).read_text(encoding="utf-8"))
    assert {s["ticker"] for s in signals["accepted_support_signals"]} == {"QQQ"}
    assert signals["not_authorization"] is True
    assert signals["permission_effect"] == "none"

    # ...but the compiled handoff is still non-actionable...
    candidate = json.loads(Path(result["compiled_research_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert candidate["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in candidate["buy_universe_scorecard"])

    # ...availability recognizes the grounded memo state (R2E.4) but the permission
    # set is unchanged: HOLD / NO_TRADE only, still non-actionable.
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    assert decision["state"] == "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
    assert decision["grounded_memo_support_present"] is True
    assert decision["accepted_support_signal_count"] >= 1
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert decision["permission_effect"] == "none"

    # ...and the Step 2 research gate still blocks the actionable path.
    gate = evaluate_step2_research_gate(decision)
    assert gate.allowed is False
    assert "NEW_BUY" in gate.blocked_actions and "ORDER_COMPILATION" in gate.blocked_actions


# --- standalone CLI workflow function ----------------------------------------


def test_standalone_compile_writes_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.compile_step1_research_handoff(strategy_settings=_settings())
    assert result["compilation_mode"] == COMPILATION_MODE_EVIDENCE_ONLY
    assert Path(result["candidate_path"]).is_file()
    assert Path(result["metadata_path"]).is_file()
    assert Path(result["support_signals_path"]).is_file()
