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


# --- R2E.5b-0 actionable-handoff preview (report-only, separate artifact) -----


def _anchor_grounded_memo() -> dict[str, Any]:
    return {
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


def _write_anchor_yaml(tmp_path: Path) -> None:
    inputs_dir = tmp_path / "inputs" / "current"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    # Quoted and unquoted ISO dates are equivalent since the R2E.5a date
    # normalization; quoting kept here only for explicitness.
    (inputs_dir / "research_anchors.yaml").write_text(
        "schema_version: research_anchors_v1\n"
        'as_of_date: "2026-06-28"\n'
        "is_llm_generated: false\n"
        "anchors:\n"
        "  - anchor_id: AI_CAPEX_2026H2\n"
        "    anchor_type: structural_theme\n"
        "    applicable_tickers: [QQQ]\n"
        '    anchor_date_et: "2026-06-15"\n'
        '    valid_from: "2026-06-01"\n'
        '    valid_until: "2026-07-31"\n'
        "    source_type: operator\n"
        "    confidence_floor: medium\n",
        encoding="utf-8",
    )


def test_parse_writes_actionable_handoff_preview_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    preview_path = Path(result["actionable_handoff_preview_path"])
    assert preview_path.name == "compiled_actionable_handoff_preview.json"
    assert preview_path.is_file()
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    # Report-only / non-authorization invariants.
    assert preview["schema_version"] == "compiled_actionable_handoff_preview_v1"
    assert preview["is_llm_generated"] is False
    assert preview["report_only"] is True
    assert preview["permission_effect"] == "none"
    assert preview["not_authorization"] is True
    assert preview["extended_etf_sleeve_preview_enabled"] is False
    # Evidence-only run has no accepted support signals → empty preview + blocker.
    assert preview["preview_actionable_rows"] == []
    assert "no_accepted_support_signals" in preview["global_blockers"]
    # Provenance references the report-only source artifacts.
    assert preview["source_compiled_support_signals"]["sha256"]
    assert preview["source_evidence_packet"]["sha256"]


def test_preview_row_produced_but_active_handoff_and_gates_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    # Settings whose base-universe weekly cap is > 0 so the preview can surface a row.
    settings = _settings()
    settings["max_new_tickers_per_week"] = {
        "base_universe_new_tickers_per_week": 2,
        "extended_etf_sleeve_new_tickers_per_week": 2,
    }
    result = step1_research.parse_step1_output(strategy_settings=settings)

    # The PREVIEW surfaces QQQ as a hypothetical future actionable row...
    preview = json.loads(Path(result["actionable_handoff_preview_path"]).read_text(encoding="utf-8"))
    assert preview["preview_positive_delta_research_supported"] == ["QQQ"]
    assert preview["preview_actionable_rows"][0]["actionability_status_preview"] == "actionable_this_run"
    assert preview["preview_actionable_rows"][0]["not_authorization"] is True

    # ...but the ACTIVE compiled handoff stays non-actionable (unchanged).
    candidate = json.loads(Path(result["compiled_research_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert candidate["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in candidate["buy_universe_scorecard"])
    assert all(r["primary_anchor_event_id"] is None for r in candidate["buy_universe_scorecard"])

    # ...availability upgrades this fully-eligible run to Step 2 decision-only
    # (R2E.5b-6c) and does NOT reference the preview artifact anywhere.
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    _assert_step2_decision_only_decision(decision)
    assert "compiled_actionable_handoff_preview" not in json.dumps(decision)

    # ...and the Step 2 gate opens ONLY the decision-only mode; the order path
    # (NEW_BUY / ORDER_COMPILATION / Step 3/4) stays blocked.
    _assert_gate_promoted_decision_only(evaluate_step2_research_gate(decision))


def test_preview_failure_does_not_break_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated preview failure")

    monkeypatch.setattr(step1_research, "write_actionable_handoff_preview", boom)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Parse still succeeds; the compiled artifacts and degraded-mode decision exist.
    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_degraded_mode_decision_path"]).exists()
    assert Path(result["compiled_support_signals_path"]).exists()
    assert result["actionable_handoff_preview_path"] == ""


# --- R2E.5b-1 actionable compiled-handoff candidate (report-only, separate) ---


def _settings_with_cap() -> dict[str, Any]:
    settings = _settings()
    settings["max_new_tickers_per_week"] = {
        "base_universe_new_tickers_per_week": 2,
        "extended_etf_sleeve_new_tickers_per_week": 2,
    }
    return settings


def _assert_step2_decision_only_decision(decision: dict[str, Any]) -> None:
    """R2E.5b-6c posture: Step 2 decision-only permitted, order path closed."""
    assert decision["state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]
    for blocked in (
        "SELL",
        "NEW_BUY",
        "ROTATION",
        "REBALANCE",
        "EXTENDED_ETF_ADMISSION",
        "ORDER_COMPILATION",
    ):
        assert blocked in decision["blocked_actions"]
    assert decision["promoted_step2_decision_only"] is True
    assert decision["order_compilation_allowed"] is False
    assert decision["new_buy_permission"] is False
    assert decision["source"] == "promoted_compiled_actionable_handoff"
    assert decision["promoted_pointer_present"] is True
    assert decision["promoted_pointer_valid"] is True
    assert decision["promotion_status"] == "pending_gates"
    assert decision["effective_handoff_present"] is True
    assert decision["effective_handoff_valid"] is True
    assert decision["candidate_actionable_row_count"] == 1
    assert decision["actionable_this_run_tickers"] == ["QQQ"]
    assert decision["permission_effect"] == "promoted_step2_decision_only"
    assert decision["not_authorization"] is True
    for reason in (
        "promoted_step2_decision_only_enabled",
        "new_buy_requires_future_gate_pr",
        "order_compilation_requires_future_gate_pr",
        "final_execution_requires_future_gate_pr",
    ):
        assert reason in decision["blocker_reasons"]
    assert "promoted_actionable_handoff_pending_gates" not in decision["blocker_reasons"]
    for key in (
        "active_research_handoff_source",
        "research_handoff_candidate_effective",
        "promoted_handoff_step2_verification",
        "promoted_step2_gate_dry_run",
    ):
        assert key in decision["source_artifacts"]


def _assert_gate_promoted_decision_only(gate: Any) -> None:
    """The Step 2 gate allows decision-only and nothing else."""
    assert gate.allowed is True
    assert gate.mode == "promoted_step2_decision_only"
    assert gate.order_compilation_allowed is False
    assert gate.new_buy_permission is False
    assert gate.step3_allowed is False
    assert gate.step4_allowed is False
    assert gate.recommended_terminal_result_after_step2 == "NO_TRADE_PENDING_FINAL_GATES"
    assert "NEW_BUY" in gate.blocked_actions and "ORDER_COMPILATION" in gate.blocked_actions


def _assert_pending_gates_decision(decision: dict[str, Any]) -> None:
    assert decision["state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES"
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]
    assert "NEW_BUY" in decision["blocked_actions"]
    assert "ORDER_COMPILATION" in decision["blocked_actions"]
    assert decision["promoted_pointer_present"] is True
    assert decision["promoted_pointer_valid"] is True
    assert decision["promotion_status"] == "pending_gates"
    assert decision["effective_handoff_present"] is True
    assert decision["effective_handoff_valid"] is True
    assert decision["candidate_actionable_row_count"] == 1
    assert decision["actionable_this_run_tickers"] == ["QQQ"]
    assert decision["permission_effect"] == "none_until_consumed_by_future_gate_pr"
    assert decision["not_authorization"] is True
    assert "promoted_actionable_handoff_pending_gates" in decision["blocker_reasons"]
    assert "new_buy_requires_future_gate_pr" in decision["blocker_reasons"]
    assert "order_compilation_requires_future_gate_pr" in decision["blocker_reasons"]
    assert "active_research_handoff_source" in decision["source_artifacts"]
    assert "research_handoff_candidate_effective" in decision["source_artifacts"]


def test_parse_writes_actionable_candidate_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    candidate_path = Path(result["actionable_handoff_candidate_path"])
    validation_path = Path(result["actionable_handoff_validation_path"])
    metadata_path = Path(result["actionable_handoff_metadata_path"])
    assert candidate_path.name == "compiled_actionable_research_handoff_candidate.json"
    assert validation_path.name == "compiled_actionable_research_handoff_validation.json"
    assert metadata_path.name == "compiled_actionable_research_handoff_metadata.json"
    assert candidate_path.is_file() and validation_path.is_file() and metadata_path.is_file()

    # Evidence-only run: no preview rows → non-actionable candidate, still valid.
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert candidate["is_llm_generated"] is False
    assert candidate["report_only"] is True
    assert candidate["permission_effect"] == "none"
    assert candidate["not_authorization"] is True
    assert candidate["actionable_this_run_tickers"] == []
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    assert validation["valid"] is True
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["candidate_actionable_row_count"] == 0
    assert metadata["consumed_by_availability"] is False
    assert metadata["consumed_by_step2"] is False


def test_actionable_candidate_validates_but_active_handoff_and_gates_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())

    # The SEPARATE actionable candidate validates with QQQ promoted to actionable...
    candidate = json.loads(Path(result["actionable_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert candidate["actionable_this_run_tickers"] == ["QQQ"]
    assert candidate["strategy_a_research_handoff"]["positive_delta_research_supported"] == ["QQQ"]
    assert result["actionable_handoff_validation_passed"] == "True"
    validation = json.loads(Path(result["actionable_handoff_validation_path"]).read_text(encoding="utf-8"))
    assert validation["valid"] is True

    # ...but the ACTIVE compiled handoff is untouched and non-actionable.
    active = json.loads(Path(result["compiled_research_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert active["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in active["buy_universe_scorecard"])
    assert all(r["primary_anchor_event_id"] is None for r in active["buy_universe_scorecard"])
    assert active["schema_version"] == "research_handoff_compiled_v1"

    # ...availability upgrades the promoted effective handoff to Step 2
    # decision-only (R2E.5b-6c); the actionable candidate itself is never the
    # Step 2 source (the promoted EFFECTIVE handoff is).
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    _assert_step2_decision_only_decision(decision)
    assert "compiled_actionable_research_handoff_candidate" not in json.dumps(decision)

    # ...and the Step 2 gate opens ONLY decision-only; order path stays blocked.
    _assert_gate_promoted_decision_only(evaluate_step2_research_gate(decision))


def test_actionable_candidate_failure_does_not_break_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated actionable-candidate failure")

    monkeypatch.setattr(step1_research, "write_actionable_handoff_candidate", boom)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_degraded_mode_decision_path"]).exists()
    assert Path(result["actionable_handoff_preview_path"]).exists()
    assert result["actionable_handoff_candidate_path"] == ""


# --- R2E.5b-3 promotion-eligibility checker (report-only, no promotion) --------


def test_parse_writes_promotion_eligibility_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    eligibility_path = Path(result["actionable_promotion_eligibility_path"])
    assert eligibility_path.name == "compiled_actionable_handoff_promotion_eligibility.json"
    assert eligibility_path.is_file()
    eligibility = json.loads(eligibility_path.read_text(encoding="utf-8"))
    # Report-only / non-authorization invariants.
    assert eligibility["schema_version"] == "compiled_actionable_handoff_promotion_eligibility_v1"
    assert eligibility["is_llm_generated"] is False
    assert eligibility["report_only"] is True
    assert eligibility["permission_effect"] == "none"
    assert eligibility["not_authorization"] is True
    assert eligibility["consumed_by_availability"] is False
    assert eligibility["consumed_by_step2"] is False
    assert eligibility["consumed_by_gates"] is False
    # Evidence-only run: nothing accepted upstream → not eligible, fail closed.
    assert eligibility["eligible_for_promotion"] is False
    assert "no_accepted_support_signals" in eligibility["promotion_blockers"]
    assert result["actionable_promotion_eligible"] == "False"


def test_promotion_eligible_true_but_no_promotion_and_gates_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )
    from investment_orchestrator.workflow.weekly_orchestrator import run_weekly

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())

    # The eligibility checker reports the full chain WOULD be promotable...
    eligibility = json.loads(
        Path(result["actionable_promotion_eligibility_path"]).read_text(encoding="utf-8")
    )
    assert eligibility["eligible_for_promotion"] is True
    assert eligibility["promotion_blockers"] == []
    assert eligibility["actionable_this_run_tickers"] == ["QQQ"]
    assert eligibility["hash_chain_valid"] is True
    assert eligibility["candidate_validation_passed"] is True
    assert eligibility["earliest_anchor_valid_until"] == "2026-07-31"
    assert eligibility["promotion_expires_at"] == "2026-07-31"
    assert result["actionable_promotion_eligible"] == "True"

    # ...the eligibility artifact itself promotes nothing (the R2E.5b-5a pointer
    # layer downstream may create pending-gates pointer files, but they are never
    # consumed — see the R2E.5b-5a tests below)...

    # ...the ACTIVE compiled handoff is untouched and non-actionable...
    active = json.loads(Path(result["compiled_research_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert active["schema_version"] == "research_handoff_compiled_v1"
    assert active["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in active["buy_universe_scorecard"])

    # ...availability / allowed_actions upgrade to Step 2 decision-only and never
    # reference the eligibility artifact...
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    _assert_step2_decision_only_decision(decision)
    assert "promotion_eligibility" not in json.dumps(decision)

    # ...the Step 2 gate opens ONLY decision-only; order path stays blocked...
    _assert_gate_promoted_decision_only(evaluate_step2_research_gate(decision))

    # ...and the weekly run terminates as a controlled non-order
    # NO_TRADE_PENDING_FINAL_GATES without rendering Step 2 or compiling orders.
    weekly = run_weekly(
        decision_path=Path(result["research_degraded_mode_decision_path"]),
        step2_block_path=tmp_path / "artifacts" / "current" / "step2_block.json",
        step3_block_path=tmp_path / "artifacts" / "current" / "step3_block.json",
        step4_block_path=tmp_path / "artifacts" / "current" / "step4_block.json",
        step4_final_safety_block_path=tmp_path / "artifacts" / "current" / "step4_final_block.json",
        run_summary_output_path=tmp_path / "artifacts" / "current" / "run_summary.json",
        weekly_outcome_output_path=tmp_path / "artifacts" / "current" / "weekly_outcome.json",
        repo_root_path=tmp_path,
    )
    assert weekly.actionable is False
    assert weekly.terminal_result == "NO_TRADE_PENDING_FINAL_GATES"


def test_promotion_eligibility_failure_does_not_break_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated eligibility failure")

    monkeypatch.setattr(step1_research, "write_actionable_promotion_eligibility", boom)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_degraded_mode_decision_path"]).exists()
    assert Path(result["actionable_handoff_candidate_path"]).exists()
    assert result["actionable_promotion_eligibility_path"] == ""


# --- R2E.5b-4 promotion pointer PREVIEW + effective preview (report-only) ------


def test_parse_writes_pointer_and_effective_previews_on_eligible_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())

    # The pointer preview says the chain WOULD promote...
    pointer_path = Path(result["actionable_promotion_pointer_preview_path"])
    assert pointer_path.name == "compiled_actionable_handoff_promotion_pointer_preview.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["would_promote"] is True
    assert pointer["pointer_blockers"] == []
    assert pointer["actionable_this_run_tickers"] == ["QQQ"]
    assert pointer["promotion_expires_at"] == "2026-07-31"
    assert result["actionable_promotion_would_promote"] == "True"
    # ...loud no-promotion markers everywhere...
    assert pointer["report_only"] is True
    assert pointer["permission_effect"] == "none"
    assert pointer["not_authorization"] is True
    assert pointer["active_pointer_created"] is False
    assert pointer["effective_handoff_created"] is False
    assert pointer["future_pr_required"] is True

    # ...the effective PREVIEW exists, equals the candidate, and re-validates...
    effective_path = Path(result["actionable_effective_handoff_preview_path"])
    assert effective_path.name == "compiled_actionable_research_handoff_effective_preview.json"
    effective = json.loads(effective_path.read_text(encoding="utf-8"))
    candidate = json.loads(Path(result["actionable_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert effective == candidate
    validation = json.loads(
        Path(result["actionable_effective_handoff_preview_validation_path"]).read_text(encoding="utf-8")
    )
    assert validation["valid"] is True

    # ...the preview itself creates no real pointer (the R2E.5b-5a writer layer
    # does that separately, still unconsumed — see its tests below)...
    assert pointer["active_pointer_created"] is False
    assert pointer["effective_handoff_created"] is False

    # ...the ACTIVE compiled handoff stays non-actionable and the effective preview
    # is a distinct file from it...
    active = json.loads(Path(result["compiled_research_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert active["schema_version"] == "research_handoff_compiled_v1"
    assert active["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in active["buy_universe_scorecard"])
    assert effective_path != Path(result["compiled_research_handoff_candidate_path"])

    # ...availability / allowed_actions upgrade to Step 2 decision-only; the
    # previews are never referenced...
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    _assert_step2_decision_only_decision(decision)
    decision_text = json.dumps(decision)
    assert "pointer_preview" not in decision_text
    assert "effective_preview" not in decision_text

    # ...and the Step 2 gate opens ONLY decision-only; order path stays blocked.
    _assert_gate_promoted_decision_only(evaluate_step2_research_gate(decision))


def test_parse_pointer_preview_not_promotable_on_evidence_only_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    pointer = json.loads(
        Path(result["actionable_promotion_pointer_preview_path"]).read_text(encoding="utf-8")
    )
    assert pointer["would_promote"] is False
    assert "eligibility_not_eligible" in pointer["pointer_blockers"]
    assert pointer["effective_preview_written"] is False
    # No effective preview files are written on a non-promotable run.
    assert result["actionable_effective_handoff_preview_path"] == ""
    artifact_dir = tmp_path / "artifacts" / "current" / "step1_research"
    assert not (artifact_dir / "compiled_actionable_research_handoff_effective_preview.json").exists()
    assert not (artifact_dir / "active_research_handoff_source.json").exists()


def test_pointer_preview_failure_does_not_break_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated pointer-preview failure")

    monkeypatch.setattr(step1_research, "write_actionable_promotion_pointer_preview", boom)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_degraded_mode_decision_path"]).exists()
    assert Path(result["actionable_promotion_eligibility_path"]).exists()
    assert result["actionable_promotion_pointer_preview_path"] == ""


# --- R2E.5b-5a/5b REAL active pointer + pending-gates recognition --------------


def test_parse_creates_real_pointer_pending_gates_but_gates_stay_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )
    from investment_orchestrator.workflow.weekly_orchestrator import run_weekly

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())

    # The REAL pointer + effective handoff + validation now exist...
    pointer_path = Path(result["active_research_handoff_source_path"])
    assert pointer_path.name == "active_research_handoff_source.json"
    assert pointer_path.is_file()
    assert result["active_pointer_created"] == "True"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["schema_version"] == "active_research_handoff_source_v1"
    assert pointer["source"] == "promoted_compiled_actionable_handoff"
    # ...but explicitly PENDING GATES and not authorization...
    assert pointer["promotion_status"] == "pending_gates"
    assert pointer["permission_effect"] == "none_until_consumed_by_future_gate_pr"
    assert pointer["not_authorization"] is True
    assert pointer["future_pr_required"] is True
    assert pointer["consumed_by_availability"] is False
    assert pointer["consumed_by_step2"] is False
    assert pointer["consumed_by_gates"] is False
    assert pointer["actionable_this_run_tickers"] == ["QQQ"]

    effective_path = Path(result["effective_research_handoff_path"])
    assert effective_path.name == "research_handoff_candidate_effective.json"
    effective = json.loads(effective_path.read_text(encoding="utf-8"))
    candidate = json.loads(Path(result["actionable_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert effective == candidate
    validation = json.loads(
        (artifact_dir / "research_handoff_candidate_effective_validation.json").read_text(encoding="utf-8")
    )
    assert validation["valid"] is True

    # ...the ACTIVE compiled handoff remains the non-actionable source of record...
    active = json.loads(Path(result["compiled_research_handoff_candidate_path"]).read_text(encoding="utf-8"))
    assert active["schema_version"] == "research_handoff_compiled_v1"
    assert active["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in active["buy_universe_scorecard"])

    # ...availability recognizes the pointer/effective handoff and — R2E.5b-6c —
    # upgrades the fully-verified run to Step 2 decision-only (still no order
    # actions)...
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    _assert_step2_decision_only_decision(decision)
    decision_text = json.dumps(decision)
    assert "active_research_handoff_source" in decision_text
    assert "candidate_effective" in decision_text
    availability = json.loads(Path(result["research_availability_path"]).read_text(encoding="utf-8"))
    assert availability["state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    assert availability["allowed_actions"] == ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]
    assert availability["promoted_pointer_valid"] is True
    assert availability["order_compilation_allowed"] is False
    assert availability["new_buy_permission"] is False

    # ...the Step 2 gate opens ONLY decision-only; order path stays blocked...
    _assert_gate_promoted_decision_only(evaluate_step2_research_gate(decision))

    # ...and the weekly run terminates as a controlled NO_TRADE_PENDING_FINAL_GATES
    # without entering Step 2/3/4 or compiling orders.
    weekly = run_weekly(
        decision_path=Path(result["research_degraded_mode_decision_path"]),
        step2_block_path=tmp_path / "artifacts" / "current" / "step2_block.json",
        step3_block_path=tmp_path / "artifacts" / "current" / "step3_block.json",
        step4_block_path=tmp_path / "artifacts" / "current" / "step4_block.json",
        step4_final_safety_block_path=tmp_path / "artifacts" / "current" / "step4_final_block.json",
        run_summary_output_path=tmp_path / "artifacts" / "current" / "run_summary.json",
        weekly_outcome_output_path=tmp_path / "artifacts" / "current" / "weekly_outcome.json",
        repo_root_path=tmp_path,
    )
    assert weekly.actionable is False
    assert weekly.terminal_result == "NO_TRADE_PENDING_FINAL_GATES"
    assert weekly.run_summary_path is not None
    run_summary = json.loads(Path(weekly.run_summary_path).read_text(encoding="utf-8"))
    assert run_summary["research_state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    assert run_summary["run_blocked"] is True
    assert run_summary["recommended_result"] == "NO_TRADE"


def test_parse_writes_pointer_status_but_no_pointer_on_evidence_only_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["active_pointer_created"] == "False"
    assert result["active_research_handoff_source_path"] == ""
    artifact_dir = tmp_path / "artifacts" / "current" / "step1_research"
    assert not (artifact_dir / "active_research_handoff_source.json").exists()
    assert not (artifact_dir / "research_handoff_candidate_effective.json").exists()
    status = json.loads(
        Path(result["active_pointer_write_status_path"]).read_text(encoding="utf-8")
    )
    assert status["active_pointer_created"] is False
    assert status["pointer_blockers"]
    assert status["not_authorization"] is True


def test_active_pointer_writer_failure_does_not_break_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated pointer-writer failure")

    monkeypatch.setattr(step1_research, "write_actionable_promotion_pointer_if_eligible", boom)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_degraded_mode_decision_path"]).exists()
    assert Path(result["actionable_promotion_pointer_preview_path"]).exists()
    assert result["active_research_handoff_source_path"] == ""


# --- R2E.5b-6b/6c promoted verification + dry-run + decision-only upgrade ------


def test_parse_writes_verification_and_dry_run_and_upgrades_to_decision_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )
    from investment_orchestrator.workflow.weekly_orchestrator import run_weekly

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())

    # The R2E.5b-6a verification artifact exists and passes on this eligible run...
    verification_path = Path(result["promoted_handoff_step2_verification_path"])
    assert verification_path.name == "promoted_handoff_step2_verification.json"
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    assert verification["schema_version"] == "promoted_handoff_step2_verification_v1"
    assert verification["valid_for_step2_decision"] is True
    assert verification["verification_blockers"] == []
    assert verification["report_only"] is True
    assert verification["permission_effect"] == "none"

    # ...the dry-run (evaluated against the PRE-UPGRADE pending-gates posture)
    # says the promoted decision-only gate would pass...
    dry_run_path = Path(result["promoted_step2_gate_dry_run_path"])
    assert dry_run_path.name == "promoted_step2_gate_dry_run.json"
    dry_run = json.loads(dry_run_path.read_text(encoding="utf-8"))
    assert dry_run["schema_version"] == "promoted_step2_gate_dry_run_v1"
    assert dry_run["would_allow_step2_promoted_decision"] is True
    assert result["promoted_step2_gate_dry_run_would_allow"] == "True"
    assert dry_run["report_only"] is True
    assert dry_run["dry_run_only"] is True
    assert dry_run["permission_effect"] == "none"
    assert dry_run["not_authorization"] is True
    # The recorded posture is the preliminary pending-gates one: the real gate
    # was still closed for it, and the policy blocker is recorded.
    assert dry_run["current_state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES"
    assert dry_run["current_allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert dry_run["current_real_gate_allows"] is False
    assert dry_run["future_permission_required"] == "PROMOTED_RESEARCH_DECISION"
    assert dry_run["future_state_required"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    assert "real_gate_still_closed_by_policy" in dry_run["dry_run_blockers"]
    assert dry_run["consumed_by_step2"] is False
    assert dry_run["consumed_by_gates"] is False

    # ...R2E.5b-6c: the FINAL decision consumes verification + dry-run and
    # upgrades to Step 2 decision-only, referencing both artifacts as sources...
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    _assert_step2_decision_only_decision(decision)
    availability = json.loads(Path(result["research_availability_path"]).read_text(encoding="utf-8"))
    assert availability["state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    assert availability["allowed_actions"] == ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]

    # ...the Step 2 gate now allows ONLY the decision-only mode (order path and
    # Step 3/4 remain blocked)...
    _assert_gate_promoted_decision_only(evaluate_step2_research_gate(decision))

    # ...weekly terminates as a controlled non-order NO_TRADE_PENDING_FINAL_GATES
    # and never auto-renders Step 2...
    weekly = run_weekly(
        decision_path=Path(result["research_degraded_mode_decision_path"]),
        step2_block_path=tmp_path / "artifacts" / "current" / "step2_block.json",
        step3_block_path=tmp_path / "artifacts" / "current" / "step3_block.json",
        step4_block_path=tmp_path / "artifacts" / "current" / "step4_block.json",
        step4_final_safety_block_path=tmp_path / "artifacts" / "current" / "step4_final_block.json",
        run_summary_output_path=tmp_path / "artifacts" / "current" / "run_summary.json",
        weekly_outcome_output_path=tmp_path / "artifacts" / "current" / "weekly_outcome.json",
        repo_root_path=tmp_path,
    )
    assert weekly.actionable is False
    assert weekly.terminal_result == "NO_TRADE_PENDING_FINAL_GATES"
    assert not (tmp_path / "artifacts" / "current" / "step2_decision_builder").exists()


def test_parse_dry_run_false_on_evidence_only_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    verification = json.loads(
        Path(result["promoted_handoff_step2_verification_path"]).read_text(encoding="utf-8")
    )
    assert verification["valid_for_step2_decision"] is False
    assert "pointer_missing" in verification["verification_blockers"]

    dry_run = json.loads(
        Path(result["promoted_step2_gate_dry_run_path"]).read_text(encoding="utf-8")
    )
    assert dry_run["would_allow_step2_promoted_decision"] is False
    assert result["promoted_step2_gate_dry_run_would_allow"] == "False"
    assert dry_run["current_real_gate_allows"] is False
    assert "decision_state_not_pending_gates" in dry_run["dry_run_blockers"]
    assert "verification_invalid" in dry_run["dry_run_blockers"]
    assert "real_gate_still_closed_by_policy" in dry_run["dry_run_blockers"]
    assert dry_run["report_only"] is True and dry_run["dry_run_only"] is True
    # No upgrade on an ineligible run.
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    assert decision["state"] != "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    assert "PROMOTED_RESEARCH_DECISION" not in decision["allowed_actions"]


def test_promoted_dry_run_failure_fails_closed_to_pending_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated dry-run failure")

    monkeypatch.setattr(step1_research, "evaluate_promoted_step2_gate_dry_run", boom)
    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())

    # Parse still succeeds, but with no dry-run there is NO upgrade: the run
    # fails closed to the pending-gates HOLD / NO_TRADE posture and the Step 2
    # gate still blocks.
    assert Path(result["research_output_path"]).exists()
    assert result["promoted_step2_gate_dry_run_path"] == ""
    assert result["promoted_handoff_step2_verification_path"] == ""
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    _assert_pending_gates_decision(decision)
    gate = evaluate_step2_research_gate(decision)
    assert gate.allowed is False
    assert "NEW_BUY" in gate.blocked_actions and "ORDER_COMPILATION" in gate.blocked_actions


def test_promoted_dry_run_failure_does_not_break_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated dry-run failure")

    monkeypatch.setattr(step1_research, "evaluate_promoted_step2_gate_dry_run", boom)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_degraded_mode_decision_path"]).exists()
    assert result["promoted_step2_gate_dry_run_path"] == ""
    assert result["promoted_handoff_step2_verification_path"] == ""


# --- R2E.5b-6c promoted Step 2 decision-only render + Step 3/4 blocking --------


def _prepare_step2_render_inputs(tmp_path: Path) -> None:
    """Operator inputs required by the Step 2 render (written after Step 1 parse)."""
    from investment_orchestrator.common.io import write_text as _write_text

    _write_text(
        tmp_path / "prompts" / "strategy_a_decision_builder.txt",
        "RESEARCH\n{{ research_json }}\nPORTFOLIO\n{{ portfolio_snapshot }}\n"
        "SETTINGS\n{{ strategy_settings }}\n",
    )
    settings_yaml = (
        "as_of: '2026-06-28'\n"
        "core_universe: [QQQ, VOO, VTI, VT]\n"
        "satellite_universe: [SMH, IGV]\n"
    )
    _write_text(tmp_path / "inputs" / "current" / "strategy_settings.yaml", settings_yaml)
    _write_text(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", "QQQ | 1 | 100\n")


def _valid_step2_decision_packet() -> dict[str, Any]:
    return {
        "effective_allowed_buy_universe": ["QQQ"],
        "MARKET_DATA_SNAPSHOT": {
            "schema_version": "1.0",
            "snapshot_type": "MARKET_DATA_SNAPSHOT",
            "run_timestamp_et": "2026-06-28 16:00 ET",
            "execution_date_et": "2026-06-28",
            "market_data_target_close_date_et": "2026-06-28",
            "close_time_zone": "America/New_York",
            "display_time_zone": "America/Los_Angeles",
            "primary_source": "fixture",
            "fallback_source_for_last_close_and_price_asof_only": "fixture",
            "holiday_aware_close_resolution": True,
            "tickers": [
                {
                    "ticker": "QQQ",
                    "last_close": 420.0,
                    "price_asof": "2026-06-28",
                    "atr_20_30d_pct": 2.0,
                    "ma50": 410.0,
                    "ma200": 390.0,
                    "avg_volume_3m": 50000000,
                    "last_close_source": "fixture",
                    "price_asof_source": "fixture",
                    "technicals_source": "fixture",
                    "retrieved_at_utc": None,
                    "same_day_close_required": False,
                    "freshness_ok": True,
                    "data_gap": False,
                    "data_gap_reason": None,
                    "notes": [],
                }
            ],
        },
        "active_shortlist": [],
        "buy_side_delta_table": [],
        "rotation_decision_layer_8_15": [],
        "sell_side_delta_table_8_2": [],
        "execution_plan_drafts_8_5": [
            {"ticker": "QQQ", "action_draft": "KEEP_EXISTING", "why": "audit-only fixture"}
        ],
        "sell_execution_plan_drafts_8_6": [],
        "assumptions_and_data_gaps": [],
        "decision_builder_ready_for_audit": True,
    }


def test_promoted_step2_renders_from_effective_handoff_and_step34_stay_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.final_execution_safety_gate import (
        evaluate_final_execution_safety,
    )
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )
    from investment_orchestrator.state.upstream_artifact_guard import UpstreamArtifactGuardError
    from investment_orchestrator.workflow import (
        step2_decision_builder,
        step3_audit_engine,
        step4_order_compiler,
    )

    monkeypatch.setattr(step2_decision_builder, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step4_order_compiler, "repo_root", lambda: tmp_path)

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())
    _prepare_step2_render_inputs(tmp_path)
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    _assert_step2_decision_only_decision(decision)
    gate = evaluate_step2_research_gate(decision)
    _assert_gate_promoted_decision_only(gate)

    raw_only_marker = "RAW_DEEP_RESEARCH_FIXTURE_ONLY_MARKER_DO_NOT_RENDER_PROMOTED_STEP2"
    raw_path = Path(result["research_output_path"])
    raw_research = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_research["fixture_only_raw_deep_research_marker"] = raw_only_marker
    raw_path.write_text(json.dumps(raw_research), encoding="utf-8")

    # The promoted Step 2 render succeeds from the EFFECTIVE handoff, not from
    # the raw Deep Research output...
    render = step2_decision_builder.render_step2_prompt()
    assert render["mode"] == "promoted_step2_decision_only"
    assert render["order_compilation_allowed"] == "False"
    assert render["new_buy_permission"] == "False"
    assert render["recommended_terminal_result_after_step2"] == "NO_TRADE_PENDING_FINAL_GATES"
    prompt = Path(render["prompt_path"]).read_text(encoding="utf-8")
    effective = json.loads(Path(result["effective_research_handoff_path"]).read_text(encoding="utf-8"))
    pointer = json.loads(Path(result["active_research_handoff_source_path"]).read_text(encoding="utf-8"))
    assert effective["schema_version"] in prompt  # effective handoff body is embedded
    assert effective["actionable_this_run_tickers"] == ["QQQ"]
    assert '"actionable_this_run_tickers": [\n    "QQQ"\n  ]' in prompt
    # The raw parsed research body is NOT embedded (its fixture-unique summary is absent).
    assert "Minimal fixture derived from" in json.dumps(raw_research)
    assert "Minimal fixture derived from" not in prompt
    assert raw_only_marker in json.dumps(raw_research)
    assert raw_only_marker not in prompt
    assert "PROMOTED RESEARCH SOURCE" in prompt
    assert "source: promoted_compiled_actionable_handoff" in prompt
    assert "promotion_status: pending_gates" in prompt
    assert f"effective_handoff_sha256: {pointer['effective_handoff_sha256']}" in prompt
    assert "active_pointer_sha256:" in prompt
    assert "NOT order authorization" in prompt
    assert "NOT execution authorization" in prompt
    assert "PROMOTED_RESEARCH_DECISION" in prompt
    assert "ORDER_COMPILATION and NEW_BUY are NOT allowed" in prompt
    assert "NO_TRADE_PENDING_FINAL_GATES" in prompt

    # ...the deterministic decision-only marker records no order permission...
    marker = json.loads(Path(render["step2_promoted_decision_only_path"]).read_text(encoding="utf-8"))
    assert marker["schema_version"] == "step2_promoted_decision_only_v1"
    assert marker["mode"] == "promoted_step2_decision_only"
    assert marker["research_state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    assert marker["source"] == "promoted_compiled_actionable_handoff"
    assert marker["promoted_step2_decision_only"] is True
    assert marker["decision_only"] is True
    assert marker["allowed_actions"] == ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]
    assert "NEW_BUY" not in marker["allowed_actions"]
    assert "ORDER_COMPILATION" not in marker["allowed_actions"]
    assert "NEW_BUY" in marker["blocked_actions"]
    assert "ORDER_COMPILATION" in marker["blocked_actions"]
    assert marker["order_compilation_allowed"] is False
    assert marker["new_buy_permission"] is False
    assert marker["step3_allowed"] is False and marker["step4_allowed"] is False
    assert marker["not_execution_authorization"] is True
    assert marker["is_llm_generated"] is False
    assert marker["recommended_terminal_result_after_step2"] == "NO_TRADE_PENDING_FINAL_GATES"
    assert marker["promotion_status"] == "pending_gates"
    assert marker["effective_handoff_sha256"] == pointer["effective_handoff_sha256"]
    assert marker["source_artifacts"]["research_handoff_candidate_effective"].endswith(
        "research_handoff_candidate_effective.json"
    )

    # Rendering Step 2 decision-only creates no Step 3 / Step 4 / order compiler artifacts.
    step3_dir = tmp_path / "artifacts" / "current" / "step3_audit_engine"
    step4_dir = tmp_path / "artifacts" / "current" / "step4_order_compiler"
    assert not step3_dir.exists()
    assert not step4_dir.exists()
    for order_artifact in (
        step4_dir / "template4_orders.txt",
        step4_dir / "order_state_export.txt",
        step4_dir / "exec_summary.txt",
    ):
        assert not order_artifact.exists()

    # ...Step 3 deterministically blocks with the promoted decision-only reason...
    with pytest.raises(UpstreamArtifactGuardError, match="promoted decision-only gate"):
        step3_audit_engine.enforce_step3_upstream_guard()
    step3_block = json.loads(
        step3_audit_engine.step3_blocked_by_promoted_decision_only_gate_path().read_text(
            encoding="utf-8"
        )
    )
    assert step3_block["reason"] == "promoted_step2_decision_only_no_audit_permission"
    assert step3_block["blocked"] is True

    # ...and the final execution safety gate rejects the promoted state outright.
    final = evaluate_final_execution_safety(
        step2_decision_packet=None,
        step3_audited_packet=None,
        step1_permission=decision,
    )
    assert final.ready_for_order_compilation is False
    assert final.checked_conditions["step1_state_strict_fresh"] is False
    assert final.checked_conditions["order_compilation_allowed"] is False


def test_promoted_step2_render_fails_closed_when_effective_handoff_tampered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        ResearchDegradedModeGateError,
    )
    from investment_orchestrator.workflow import step2_decision_builder

    monkeypatch.setattr(step2_decision_builder, "repo_root", lambda: tmp_path)

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())
    _prepare_step2_render_inputs(tmp_path)

    # Tamper with the effective handoff after Step 1: the render-time live
    # verification must fail closed (hash mismatch) and render nothing.
    effective_path = Path(result["effective_research_handoff_path"])
    tampered = json.loads(effective_path.read_text(encoding="utf-8"))
    tampered["tampered"] = True
    effective_path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(ResearchDegradedModeGateError, match="promoted decision-only verification"):
        step2_decision_builder.render_step2_prompt()
    assert not step2_decision_builder.step2_prompt_path().exists()
    blocked = json.loads(
        step2_decision_builder.step2_blocked_by_research_gate_path().read_text(encoding="utf-8")
    )
    assert blocked["reason"] == "promoted_step2_verification_failed"
    assert "effective_handoff_hash_mismatch" in blocked["blocker_reasons"]


# --- R2E.5b-6e report-only promoted Step 3 audit verifier / dry-run -----------


def test_promoted_step3_audit_dry_run_happy_path_is_report_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.final_execution_safety_gate import (
        evaluate_final_execution_safety,
    )
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )
    from investment_orchestrator.state.upstream_artifact_guard import UpstreamArtifactGuardError
    from investment_orchestrator.workflow import (
        step2_decision_builder,
        step3_audit_engine,
        step4_order_compiler,
    )

    monkeypatch.setattr(step2_decision_builder, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step4_order_compiler, "repo_root", lambda: tmp_path)

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())
    _prepare_step2_render_inputs(tmp_path)
    render = step2_decision_builder.render_step2_prompt()
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    _assert_step2_decision_only_decision(decision)
    _assert_gate_promoted_decision_only(evaluate_step2_research_gate(decision))
    assert Path(render["step2_promoted_decision_only_path"]).exists()

    packet_path = step2_decision_builder.step2_decision_packet_path()
    packet_path.write_text(json.dumps(_valid_step2_decision_packet()), encoding="utf-8")

    summary = step1_research._write_promoted_step3_audit_dry_run_report_only(  # noqa: SLF001
        strategy_settings=_settings_with_cap(),
        research_decision=decision,
    )

    verification_path = Path(summary["promoted_handoff_step3_audit_verification_path"])
    assert verification_path.name == "promoted_handoff_step3_audit_verification.json"
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    assert verification["schema_version"] == "promoted_handoff_step3_audit_verification_v1"
    assert verification["is_llm_generated"] is False
    assert verification["report_only"] is True
    assert verification["permission_effect"] == "none"
    assert verification["not_authorization"] is True
    assert verification["not_execution_authorization"] is True
    assert verification["valid_for_promoted_step3_audit"] is True
    assert verification["verification_blockers"] == []
    assert verification["future_state_required"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY"
    assert verification["future_action_required"] == "PROMOTED_RESEARCH_AUDIT"
    assert verification["future_step3_source_artifact"] == "research_handoff_candidate_effective.json"
    assert verification["raw_deep_research_source_used"] is False
    assert verification["step2_decision_packet_valid"] is True
    assert verification["source_artifacts"]["step2_promoted_decision_only"].endswith(
        "step2_promoted_decision_only.json"
    )
    assert verification["source_artifacts"]["research_handoff_candidate_effective"].endswith(
        "research_handoff_candidate_effective.json"
    )
    assert "research_output.json" not in json.dumps(verification["source_artifacts"])
    assert verification["order_compilation_allowed"] is False
    assert verification["new_buy_permission"] is False
    assert verification["step4_allowed"] is False
    assert verification["final_execution_allowed"] is False
    assert verification["broker_automation_allowed"] is False

    dry_run_path = Path(summary["promoted_step3_audit_gate_dry_run_path"])
    assert dry_run_path.name == "promoted_step3_audit_gate_dry_run.json"
    dry_run = json.loads(dry_run_path.read_text(encoding="utf-8"))
    assert dry_run["schema_version"] == "promoted_step3_audit_gate_dry_run_v1"
    assert dry_run["is_llm_generated"] is False
    assert dry_run["report_only"] is True
    assert dry_run["dry_run_only"] is True
    assert dry_run["permission_effect"] == "none"
    assert dry_run["not_authorization"] is True
    assert dry_run["not_execution_authorization"] is True
    assert dry_run["would_allow_promoted_step3_audit"] is True
    assert summary["promoted_step3_audit_gate_dry_run_would_allow"] == "True"
    assert dry_run["current_real_gate_allows"] is False
    assert "real_gate_still_closed_by_policy" in dry_run["dry_run_blockers"]
    assert dry_run["current_state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    assert dry_run["current_allowed_actions"] == ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]
    assert "NEW_BUY" not in dry_run["current_allowed_actions"]
    assert "ORDER_COMPILATION" not in dry_run["current_allowed_actions"]
    assert dry_run["order_compilation_allowed"] is False
    assert dry_run["new_buy_permission"] is False
    assert dry_run["step4_allowed"] is False
    assert dry_run["final_execution_allowed"] is False
    assert dry_run["broker_automation_allowed"] is False
    assert dry_run["consumed_by_availability"] is False
    assert dry_run["consumed_by_step3"] is False
    assert dry_run["consumed_by_gates"] is False

    with pytest.raises(UpstreamArtifactGuardError, match="promoted decision-only gate"):
        step3_audit_engine.enforce_step3_upstream_guard()
    assert step3_audit_engine.step3_blocked_by_promoted_decision_only_gate_path().exists()
    assert not step3_audit_engine.step3_prompt_path().exists()

    with pytest.raises(UpstreamArtifactGuardError):
        step4_order_compiler.enforce_step4_upstream_guard()
    assert step4_order_compiler.step4_blocked_by_upstream_gate_path().exists()
    assert not step4_order_compiler.step4_prompt_path().exists()
    assert not step4_order_compiler.step4_template4_orders_path().exists()
    assert not step4_order_compiler.step4_order_state_export_path().exists()
    assert not step4_order_compiler.step4_exec_summary_path().exists()

    final = evaluate_final_execution_safety(
        step1_permission=decision,
        step2_decision_packet=_valid_step2_decision_packet(),
        step3_audited_packet=None,
    )
    assert final.ready_for_order_compilation is False
    assert final.checked_conditions["step1_state_strict_fresh"] is False
    assert final.checked_conditions["order_compilation_allowed"] is False


def test_promoted_step3_audit_dry_run_fails_closed_without_step2_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())

    verification = json.loads(
        Path(result["promoted_handoff_step3_audit_verification_path"]).read_text(
            encoding="utf-8"
        )
    )
    assert verification["valid_for_promoted_step3_audit"] is False
    assert "step2_promoted_marker_missing" in verification["verification_blockers"]
    assert "step2_decision_packet_missing" in verification["verification_blockers"]
    assert verification["report_only"] is True
    assert verification["permission_effect"] == "none"

    dry_run = json.loads(
        Path(result["promoted_step3_audit_gate_dry_run_path"]).read_text(encoding="utf-8")
    )
    assert dry_run["would_allow_promoted_step3_audit"] is False
    assert result["promoted_step3_audit_gate_dry_run_would_allow"] == "False"
    assert dry_run["current_real_gate_allows"] is False
    assert dry_run["order_compilation_allowed"] is False
    assert dry_run["new_buy_permission"] is False


@pytest.mark.parametrize(
    ("widened_action", "expected_blocker"),
    [
        ("NEW_BUY", "step2_promoted_marker_widened_new_buy"),
        ("ORDER_COMPILATION", "step2_promoted_marker_widened_order_compilation"),
    ],
)
def test_promoted_step3_audit_verifier_rejects_widened_step2_marker_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    widened_action: str,
    expected_blocker: str,
) -> None:
    from investment_orchestrator.workflow import step2_decision_builder

    monkeypatch.setattr(step2_decision_builder, "repo_root", lambda: tmp_path)

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())
    _prepare_step2_render_inputs(tmp_path)
    render = step2_decision_builder.render_step2_prompt()
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))

    marker_path = Path(render["step2_promoted_decision_only_path"])
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["allowed_actions"] = [*marker["allowed_actions"], widened_action]
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    step2_decision_builder.step2_decision_packet_path().write_text(
        json.dumps(_valid_step2_decision_packet()), encoding="utf-8"
    )

    summary = step1_research._write_promoted_step3_audit_dry_run_report_only(  # noqa: SLF001
        strategy_settings=_settings_with_cap(),
        research_decision=decision,
    )
    verification = json.loads(
        Path(summary["promoted_handoff_step3_audit_verification_path"]).read_text(
            encoding="utf-8"
        )
    )
    assert verification["valid_for_promoted_step3_audit"] is False
    assert expected_blocker in verification["verification_blockers"]
    dry_run = json.loads(
        Path(summary["promoted_step3_audit_gate_dry_run_path"]).read_text(encoding="utf-8")
    )
    assert dry_run["would_allow_promoted_step3_audit"] is False
    assert dry_run["current_real_gate_allows"] is False


def test_promoted_step3_audit_verifier_fails_closed_on_effective_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.workflow import step2_decision_builder

    monkeypatch.setattr(step2_decision_builder, "repo_root", lambda: tmp_path)

    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())
    _prepare_step2_render_inputs(tmp_path)
    step2_decision_builder.render_step2_prompt()
    decision = json.loads(Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8"))
    step2_decision_builder.step2_decision_packet_path().write_text(
        json.dumps(_valid_step2_decision_packet()), encoding="utf-8"
    )

    effective_path = Path(result["effective_research_handoff_path"])
    tampered = json.loads(effective_path.read_text(encoding="utf-8"))
    tampered["tampered_after_marker"] = True
    effective_path.write_text(json.dumps(tampered), encoding="utf-8")

    summary = step1_research._write_promoted_step3_audit_dry_run_report_only(  # noqa: SLF001
        strategy_settings=_settings_with_cap(),
        research_decision=decision,
    )
    verification = json.loads(
        Path(summary["promoted_handoff_step3_audit_verification_path"]).read_text(
            encoding="utf-8"
        )
    )
    assert verification["valid_for_promoted_step3_audit"] is False
    assert "promoted_handoff_verification_invalid" in verification["verification_blockers"]
    assert "step2_promoted_marker_hash_mismatch" in verification["verification_blockers"]
    assert "effective_handoff_hash_mismatch" in verification["live_step2_verification_blockers"]


# --- R2E.5b-6f promoted Step 3 audit-only manual path ------------------------


def _write_step2_raw_output_for_parse(packet: dict[str, Any]) -> None:
    from investment_orchestrator.workflow import step2_decision_builder

    step2_decision_builder.step2_raw_output_path().write_text(
        "TEMPLATE2_OUTPUT_START\n"
        "Fixture promoted Step 2 decision-only output.\n"
        "TEMPLATE2_OUTPUT_END\n"
        "DECISION_PACKET_START\n"
        + json.dumps(packet)
        + "\nDECISION_PACKET_END\n",
        encoding="utf-8",
    )


def _write_step3_prompt_inputs(tmp_path: Path) -> None:
    (tmp_path / "prompts" / "strategy_b_audit_engine.txt").write_text(
        "RESEARCH\n{{ research_json }}\nPORTFOLIO\n{{ portfolio_snapshot }}\n"
        "TEMPLATE2\n{{ template2_output }}\nDECISION\n{{ decision_packet }}\n",
        encoding="utf-8",
    )


def _promote_to_step3_audit_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    from investment_orchestrator.workflow import step2_decision_builder

    monkeypatch.setattr(step2_decision_builder, "repo_root", lambda: tmp_path)
    artifact_dir = _setup_repo(tmp_path, monkeypatch)
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_anchor_grounded_memo()), encoding="utf-8"
    )
    _write_anchor_yaml(tmp_path)
    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())
    _prepare_step2_render_inputs(tmp_path)
    step2_decision_builder.render_step2_prompt()
    _write_step2_raw_output_for_parse(_valid_step2_decision_packet())
    parse = step2_decision_builder.parse_step2_output()
    assert parse["promoted_step3_audit_only"] == "True"
    decision = json.loads(
        Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8")
    )
    assert decision["state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY"
    assert decision["allowed_actions"] == [
        "HOLD",
        "NO_TRADE",
        "PROMOTED_RESEARCH_DECISION",
        "PROMOTED_RESEARCH_AUDIT",
    ]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]
    assert decision["promoted_step3_audit_only"] is True
    assert decision["order_compilation_allowed"] is False
    assert decision["new_buy_permission"] is False
    return {"step1_result": result, "decision": decision, "step2_parse": parse}


def test_promoted_step3_audit_only_render_parse_blocks_downstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.final_execution_safety_gate import (
        evaluate_final_execution_safety,
    )
    from investment_orchestrator.state.upstream_artifact_guard import UpstreamArtifactGuardError
    from investment_orchestrator.workflow import (
        step3_audit_engine,
        step4_order_compiler,
    )

    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step4_order_compiler, "repo_root", lambda: tmp_path)
    setup = _promote_to_step3_audit_only(tmp_path, monkeypatch)
    result = setup["step1_result"]
    decision = setup["decision"]
    _write_step3_prompt_inputs(tmp_path)

    raw_only_marker = "RAW_DEEP_RESEARCH_FIXTURE_ONLY_MARKER_DO_NOT_RENDER_PROMOTED_STEP3"
    raw_path = Path(result["research_output_path"])
    raw_research = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_research["fixture_only_raw_deep_research_marker"] = raw_only_marker
    raw_path.write_text(json.dumps(raw_research), encoding="utf-8")

    render = step3_audit_engine.render_step3_prompt()
    assert render["mode"] == "promoted_step3_audit_only"
    prompt = Path(render["prompt_path"]).read_text(encoding="utf-8")
    effective = json.loads(Path(result["effective_research_handoff_path"]).read_text(encoding="utf-8"))
    assert effective["schema_version"] in prompt
    assert raw_only_marker in json.dumps(raw_research)
    assert raw_only_marker not in prompt
    assert "research_handoff_candidate_effective.json" in prompt
    assert "NOT raw Deep Research output" in prompt
    assert "PROMOTED_RESEARCH_AUDIT" in prompt
    assert "NOT order authorization" in prompt
    assert "NEW_BUY and ORDER_COMPILATION are NOT allowed" in prompt

    marker = json.loads(
        step3_audit_engine.step3_promoted_audit_only_path().read_text(encoding="utf-8")
    )
    assert marker["schema_version"] == "step3_promoted_audit_only_v1"
    assert marker["is_llm_generated"] is False
    assert marker["mode"] == "promoted_step3_audit_only"
    assert marker["state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY"
    assert marker["allowed_actions"] == [
        "HOLD",
        "NO_TRADE",
        "PROMOTED_RESEARCH_DECISION",
        "PROMOTED_RESEARCH_AUDIT",
    ]
    assert "NEW_BUY" not in marker["allowed_actions"]
    assert "ORDER_COMPILATION" not in marker["allowed_actions"]
    assert marker["audit_only"] is True
    assert marker["permission_effect"] == "step3_audit_only"
    assert marker["not_authorization"] is True
    assert marker["not_execution_authorization"] is True
    assert marker["order_compilation_allowed"] is False
    assert marker["new_buy_permission"] is False
    assert marker["step4_allowed"] is False
    assert marker["final_execution_allowed"] is False
    assert marker["broker_automation_allowed"] is False
    assert marker["future_step3_source_artifact"] == "research_handoff_candidate_effective.json"
    assert "research_output.json" not in json.dumps(marker["source_artifacts"])

    downstream_block = json.loads(
        step3_audit_engine.step3_promoted_audit_only_downstream_block_path().read_text(
            encoding="utf-8"
        )
    )
    assert downstream_block["blocked"] is True
    assert downstream_block["reason"] == "promoted_step3_audit_only_no_order_compilation_permission"
    assert downstream_block["order_compilation_allowed"] is False

    step3_audit_engine.step3_raw_output_path().write_text(
        "Promoted Step 3 audit-only findings. No order readiness asserted.\n",
        encoding="utf-8",
    )
    parsed = step3_audit_engine.parse_step3_output()
    assert parsed["mode"] == "promoted_step3_audit_only"
    assert step3_audit_engine.step3_template3_audit_path().exists()
    assert step3_audit_engine.step3_template2_patch_path().exists()
    assert not step3_audit_engine.step3_audited_decision_packet_path().exists()

    with pytest.raises(UpstreamArtifactGuardError):
        step4_order_compiler.render_step4_prompt()
    step4_block = json.loads(
        step4_order_compiler.step4_blocked_by_upstream_gate_path().read_text(encoding="utf-8")
    )
    assert step4_block["blocked_by_artifact"].endswith(
        "step3_promoted_audit_only_downstream_block.json"
    )
    assert not step4_order_compiler.step4_prompt_path().exists()
    assert not step4_order_compiler.step4_template4_orders_path().exists()
    assert not step4_order_compiler.step4_order_state_export_path().exists()
    assert not step4_order_compiler.step4_exec_summary_path().exists()

    final = evaluate_final_execution_safety(
        step1_permission=decision,
        step2_decision_packet=_valid_step2_decision_packet(),
        step3_audited_packet=None,
    )
    assert final.ready_for_order_compilation is False
    assert final.checked_conditions["step1_state_strict_fresh"] is False
    assert final.checked_conditions["order_compilation_allowed"] is False


def test_promoted_step3_audit_only_fails_closed_without_step2_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.upstream_artifact_guard import UpstreamArtifactGuardError
    from investment_orchestrator.workflow import step2_decision_builder, step3_audit_engine

    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    _promote_to_step3_audit_only(tmp_path, monkeypatch)
    _write_step3_prompt_inputs(tmp_path)
    step2_decision_builder.step2_promoted_decision_only_path().unlink()

    with pytest.raises(UpstreamArtifactGuardError, match="promoted audit-only verification"):
        step3_audit_engine.render_step3_prompt()
    blocked = json.loads(
        step3_audit_engine.step3_blocked_by_upstream_gate_path().read_text(encoding="utf-8")
    )
    assert blocked["reason"] == "promoted_step3_audit_only_verification_failed"
    assert "step2_promoted_marker_missing" in blocked["blocker_reasons"]
    assert not step3_audit_engine.step3_prompt_path().exists()


def test_promoted_step3_audit_only_fails_closed_on_effective_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.state.upstream_artifact_guard import UpstreamArtifactGuardError
    from investment_orchestrator.workflow import step3_audit_engine

    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    setup = _promote_to_step3_audit_only(tmp_path, monkeypatch)
    _write_step3_prompt_inputs(tmp_path)
    effective_path = Path(setup["step1_result"]["effective_research_handoff_path"])
    tampered = json.loads(effective_path.read_text(encoding="utf-8"))
    tampered["tampered_before_step3"] = True
    effective_path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(UpstreamArtifactGuardError, match="promoted audit-only verification"):
        step3_audit_engine.render_step3_prompt()
    blocked = json.loads(
        step3_audit_engine.step3_blocked_by_upstream_gate_path().read_text(encoding="utf-8")
    )
    assert "promoted_handoff_verification_invalid" in blocked["blocker_reasons"]
    assert "step2_promoted_marker_hash_mismatch" in blocked["blocker_reasons"]
    assert not step3_audit_engine.step3_prompt_path().exists()


# --- R2E.5b-7b: report-only promoted Step 4 readiness verifier / dry-run ------


def _write_step4_readiness_operator_inputs(tmp_path: Path) -> None:
    """Deterministic budget/cap operator inputs the 7b verifier requires."""
    from investment_orchestrator.common.io import write_text as _write_text

    _write_text(
        tmp_path / "inputs" / "current" / "strategy_settings.yaml",
        "as_of: '2026-06-28'\n"
        "core_universe: [QQQ, VOO, VTI, VT]\n"
        "satellite_universe: [SMH, IGV]\n"
        "hard_cap_open_orders_budget: 38211.29\n"
        "target_new_buy_budget_this_run: 12000.00\n"
        "max_new_tickers_per_week:\n"
        "  base_universe_new_tickers_per_week: 2\n"
        "  extended_etf_sleeve_new_tickers_per_week: 2\n",
    )
    _write_text(
        tmp_path / "inputs" / "current" / "portfolio_snapshot.txt",
        # Empty live-structure columns (11/12) parse cleanly with no data gap so
        # the R2E.5b-7c preflight can compute deterministic hard-cap headroom.
        "(2a) existing_buy_open_orders_summary\n"
        "QQQ | 1000.00 | 900.00 | 100.00 | T4-E | - | - | - | - | - | - |  | \n",
    )


def test_promoted_step4_readiness_fails_closed_before_step3_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 1/2-level artifacts alone must never report Step 4 preview readiness."""
    setup = _promote_to_step3_audit_only(tmp_path, monkeypatch)
    result = setup["step1_result"]

    assert result["promoted_step4_readiness_verification_path"]
    assert result["promoted_step4_preview_gate_dry_run_path"]
    verification = json.loads(
        step1_research.step1_promoted_step4_readiness_verification_path().read_text(
            encoding="utf-8"
        )
    )
    assert verification["valid_for_promoted_step4_preview"] is False
    assert "step3_promoted_marker_missing" in verification["verification_blockers"]
    dry_run = json.loads(
        step1_research.step1_promoted_step4_preview_gate_dry_run_path().read_text(
            encoding="utf-8"
        )
    )
    assert dry_run["would_allow_promoted_step4_preview"] is False
    assert dry_run["current_real_gate_allows"] is False


def test_promoted_step4_readiness_happy_path_after_step3_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.workflow import step3_audit_engine

    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    _promote_to_step3_audit_only(tmp_path, monkeypatch)
    _write_step3_prompt_inputs(tmp_path)
    _write_step4_readiness_operator_inputs(tmp_path)

    step3_audit_engine.render_step3_prompt()
    step3_audit_engine.step3_raw_output_path().write_text(
        "Promoted Step 3 audit-only findings. No order readiness asserted.\n",
        encoding="utf-8",
    )
    parsed = step3_audit_engine.parse_step3_output()

    assert parsed["promoted_step4_preview_gate_dry_run_would_allow"] == "True"
    assert parsed["promoted_step4_readiness_verification_path"]
    assert parsed["promoted_step4_preview_gate_dry_run_path"]

    verification = json.loads(
        step1_research.step1_promoted_step4_readiness_verification_path().read_text(
            encoding="utf-8"
        )
    )
    assert verification["schema_version"] == "promoted_step4_readiness_verification_v1"
    assert verification["valid_for_promoted_step4_preview"] is True
    assert verification["verification_blockers"] == []
    assert verification["is_llm_generated"] is False
    assert verification["report_only"] is True
    assert verification["permission_effect"] == "none"
    assert verification["not_authorization"] is True
    assert verification["not_execution_authorization"] is True
    assert verification["current_real_gate_allows"] is False
    assert verification["order_compilation_allowed"] is False
    assert verification["new_buy_permission"] is False
    assert verification["step4_allowed"] is False
    assert verification["final_execution_allowed"] is False
    assert verification["broker_automation_allowed"] is False
    assert verification["raw_deep_research_source_used"] is False
    assert verification["consumed_by_availability"] is False
    assert verification["consumed_by_step4"] is False
    assert verification["consumed_by_gates"] is False
    assert verification["source_artifact_hashes"]["step3_promoted_audit_only"]
    assert verification["source_artifact_hashes"]["step3_template3_audit"]

    dry_run = json.loads(
        step1_research.step1_promoted_step4_preview_gate_dry_run_path().read_text(
            encoding="utf-8"
        )
    )
    assert dry_run["schema_version"] == "promoted_step4_preview_gate_dry_run_v1"
    assert dry_run["would_allow_promoted_step4_preview"] is True
    assert dry_run["current_real_gate_allows"] is False
    assert "real_gate_still_closed_by_policy" in dry_run["dry_run_blockers"]
    assert dry_run["dry_run_only"] is True
    assert dry_run["permission_effect"] == "none"
    assert (
        dry_run["future_state_required"]
        == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP4_PREVIEW_ONLY"
    )
    assert dry_run["future_action_required"] == "PROMOTED_ORDER_PREVIEW"
    assert dry_run["order_compilation_allowed"] is False
    assert dry_run["new_buy_permission"] is False
    assert dry_run["step4_allowed"] is False


def test_promoted_step4_readiness_artifacts_are_not_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """7b non-consumption: no upgrade, no Step 4, no new state/action anywhere."""
    import inspect

    from investment_orchestrator.state.research_availability import (
        evaluate_research_availability,
    )
    from investment_orchestrator.state.upstream_artifact_guard import UpstreamArtifactGuardError
    from investment_orchestrator.workflow import step3_audit_engine, step4_order_compiler

    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step4_order_compiler, "repo_root", lambda: tmp_path)
    _promote_to_step3_audit_only(tmp_path, monkeypatch)
    _write_step3_prompt_inputs(tmp_path)
    _write_step4_readiness_operator_inputs(tmp_path)
    step3_audit_engine.render_step3_prompt()
    step3_audit_engine.step3_raw_output_path().write_text(
        "Promoted Step 3 audit-only findings.\n", encoding="utf-8"
    )
    parsed = step3_audit_engine.parse_step3_output()
    assert parsed["promoted_step4_preview_gate_dry_run_would_allow"] == "True"

    # Availability has no Step 4 inputs at all and the on-disk permission is
    # unchanged: still the Step 3 audit-only state with the exact 4-action set.
    assert not any(
        "step4" in name for name in inspect.signature(evaluate_research_availability).parameters
    )
    decision = json.loads(
        step1_research.step1_research_degraded_mode_decision_path().read_text(encoding="utf-8")
    )
    assert decision["state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY"
    assert decision["allowed_actions"] == [
        "HOLD",
        "NO_TRADE",
        "PROMOTED_RESEARCH_DECISION",
        "PROMOTED_RESEARCH_AUDIT",
    ]
    assert "PROMOTED_ORDER_PREVIEW" not in json.dumps(decision)
    assert decision["order_compilation_allowed"] is False
    assert decision["new_buy_permission"] is False

    # Step 4 stays blocked even with a would_allow=true dry-run on disk.
    with pytest.raises(UpstreamArtifactGuardError):
        step4_order_compiler.render_step4_prompt()
    assert not step4_order_compiler.step4_prompt_path().exists()
    assert not step4_order_compiler.step4_template4_orders_path().exists()
    assert not step4_order_compiler.step4_order_state_export_path().exists()
    assert not step4_order_compiler.step4_exec_summary_path().exists()


def test_promoted_step4_readiness_stale_legacy_audited_packet_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.common.io import write_json as _write_json
    from investment_orchestrator.workflow import step3_audit_engine

    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    _promote_to_step3_audit_only(tmp_path, monkeypatch)
    _write_step3_prompt_inputs(tmp_path)
    _write_step4_readiness_operator_inputs(tmp_path)
    step3_audit_engine.render_step3_prompt()
    step3_audit_engine.step3_raw_output_path().write_text(
        "Promoted Step 3 audit-only findings.\n", encoding="utf-8"
    )
    step3_audit_engine.parse_step3_output()

    # A leftover legacy audited packet must flip readiness back to fail-closed.
    _write_json(
        step3_audit_engine.step3_audited_decision_packet_path(),
        {"audit_passed": True, "order_compiler_ready": True},
    )
    refreshed = step1_research.refresh_promoted_step4_readiness_after_step3()
    assert refreshed["promoted_step4_preview_gate_dry_run_would_allow"] == "False"
    verification = json.loads(
        step1_research.step1_promoted_step4_readiness_verification_path().read_text(
            encoding="utf-8"
        )
    )
    assert verification["valid_for_promoted_step4_preview"] is False
    assert "stale_legacy_audited_packet_present" in verification["verification_blockers"]


# --- R2E.5b-7c: rowless final-safety preflight integration --------------------


def _load_preflight() -> dict[str, Any]:
    return json.loads(
        step1_research.step1_promoted_final_safety_preflight_path().read_text(encoding="utf-8")
    )


_FORBIDDEN_ORDER_SHAPED_KEYS = frozenset(
    {
        "account",
        "quantity",
        "shares",
        "order_type",
        "tif",
        "time_in_force",
        "limit_price",
        "stop_price",
        "venue",
        "routing",
        "broker",
        "order_rows",
        "preview_rows",
        "candidate_orders",
    }
)


def _iter_keys(value: Any):
    if isinstance(value, dict):
        for key, sub in value.items():
            yield key
            yield from _iter_keys(sub)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_keys(item)


def test_promoted_final_safety_preflight_happy_path_after_step3_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rowless preflight is written, report-only, and computes headroom."""
    from decimal import Decimal

    from investment_orchestrator.workflow import step3_audit_engine

    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    _promote_to_step3_audit_only(tmp_path, monkeypatch)
    _write_step3_prompt_inputs(tmp_path)
    _write_step4_readiness_operator_inputs(tmp_path)
    step3_audit_engine.render_step3_prompt()
    step3_audit_engine.step3_raw_output_path().write_text(
        "Promoted Step 3 audit-only findings.\n", encoding="utf-8"
    )
    parsed = step3_audit_engine.parse_step3_output()

    assert parsed["promoted_final_safety_preflight_path"]
    # Real gate is closed by policy, so the preflight never "passes".
    assert parsed["promoted_final_safety_preflight_passed"] == "False"

    preflight = _load_preflight()
    assert preflight["schema_version"] == "promoted_final_safety_preflight_v1"
    assert preflight["is_llm_generated"] is False
    assert preflight["report_only"] is True
    assert preflight["dry_run_only"] is True
    assert preflight["rowless"] is True
    assert preflight["permission_effect"] == "none"
    assert preflight["not_authorization"] is True
    assert preflight["not_execution_authorization"] is True
    assert preflight["contains_order_rows"] is False
    assert preflight["contains_preview_rows"] is False
    assert preflight["current_real_gate_allows"] is False
    assert preflight["order_compilation_allowed"] is False
    assert preflight["new_buy_permission"] is False
    assert preflight["step4_allowed"] is False
    assert preflight["final_execution_allowed"] is False
    assert preflight["broker_automation_allowed"] is False
    assert preflight["consumed_by_availability"] is False
    assert preflight["consumed_by_step4"] is False
    assert preflight["consumed_by_gates"] is False

    # Inputs healthy -> deterministic prerequisites ready; gate stays closed.
    assert preflight["deterministic_prerequisites_ready"] is True
    assert preflight["preflight_passed"] is False
    assert "final_gate_still_closed_by_policy" in preflight["preflight_blockers"]

    budget = preflight["budget_cap_readiness"]
    assert budget["rowless"] is True
    assert budget["hard_cap_headroom_computable"] is True
    assert Decimal(budget["hard_cap_headroom"]) == Decimal("37211.29")
    assert Decimal(budget["net_new_notional_this_run"]) == Decimal("0")
    assert budget["remaining_new_ticker_slots"] == 4

    # The preflight summarizes the 7b dry-run but does not consume it as gate
    # authority; the dry-run's own policy blocker is observed.
    summary = preflight["step4_readiness_summary"]
    assert summary["dry_run_real_gate_policy_blocker_present"] is True
    assert summary["consumed_as_gate_authority"] is False


def test_promoted_final_safety_preflight_has_no_order_shaped_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_orchestrator.workflow import step3_audit_engine

    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    _promote_to_step3_audit_only(tmp_path, monkeypatch)
    _write_step3_prompt_inputs(tmp_path)
    _write_step4_readiness_operator_inputs(tmp_path)
    step3_audit_engine.render_step3_prompt()
    step3_audit_engine.step3_raw_output_path().write_text(
        "Promoted Step 3 audit-only findings.\n", encoding="utf-8"
    )
    step3_audit_engine.parse_step3_output()

    preflight = _load_preflight()
    present = {k for k in _iter_keys(preflight) if k.lower() in _FORBIDDEN_ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked into preflight: {present}"
    # No order-shaped sidecar artifacts were produced either.
    step1_dir = step1_research.step1_promoted_final_safety_preflight_path().parent
    order_shaped = [
        p.name
        for p in step1_dir.glob("*")
        if any(tok in p.name.lower() for tok in ("order", "preview_row", "broker"))
    ]
    assert order_shaped == [], f"unexpected order-shaped artifact(s): {order_shaped}"


def test_promoted_final_safety_preflight_not_consumed_and_gate_stays_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """7c non-consumption: no upgrade, final gate unchanged, Step 4 blocked."""
    import inspect

    from investment_orchestrator.state.final_execution_safety_gate import (
        evaluate_final_execution_safety,
    )
    from investment_orchestrator.state.research_availability import (
        evaluate_research_availability,
    )
    from investment_orchestrator.state.upstream_artifact_guard import UpstreamArtifactGuardError
    from investment_orchestrator.workflow import step3_audit_engine, step4_order_compiler

    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step4_order_compiler, "repo_root", lambda: tmp_path)
    _promote_to_step3_audit_only(tmp_path, monkeypatch)
    _write_step3_prompt_inputs(tmp_path)
    _write_step4_readiness_operator_inputs(tmp_path)
    step3_audit_engine.render_step3_prompt()
    step3_audit_engine.step3_raw_output_path().write_text(
        "Promoted Step 3 audit-only findings.\n", encoding="utf-8"
    )
    step3_audit_engine.parse_step3_output()
    assert step1_research.step1_promoted_final_safety_preflight_path().exists()

    # Availability signature never gained a preflight input.
    assert not any(
        "preflight" in name
        for name in inspect.signature(evaluate_research_availability).parameters
    )

    # On-disk permission unchanged by the preflight write.
    decision = json.loads(
        step1_research.step1_research_degraded_mode_decision_path().read_text(encoding="utf-8")
    )
    assert decision["state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY"
    assert decision["allowed_actions"] == [
        "HOLD",
        "NO_TRADE",
        "PROMOTED_RESEARCH_DECISION",
        "PROMOTED_RESEARCH_AUDIT",
    ]
    assert decision["order_compilation_allowed"] is False
    assert decision["new_buy_permission"] is False

    # The real final gate still rejects the promoted decision (needs literal
    # STRICT_FRESH + ORDER_COMPILATION) — the preflight changed nothing.
    gate = evaluate_final_execution_safety(
        step2_decision_packet=None,
        step3_audited_packet=None,
        step1_permission=decision,
    )
    assert gate.ready_for_order_compilation is False
    assert gate.checked_conditions["step1_state_strict_fresh"] is False
    assert gate.checked_conditions["order_compilation_allowed"] is False

    # Step 4 still blocked despite the preflight sitting on disk.
    with pytest.raises(UpstreamArtifactGuardError):
        step4_order_compiler.render_step4_prompt()
    assert not step4_order_compiler.step4_prompt_path().exists()
    assert not step4_order_compiler.step4_template4_orders_path().exists()
    assert not step4_order_compiler.step4_order_state_export_path().exists()


def test_promoted_final_safety_preflight_fails_closed_when_7b_dry_run_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting the 7b dry-run before the refresh flips the preflight closed."""
    from investment_orchestrator.workflow import step3_audit_engine

    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    _promote_to_step3_audit_only(tmp_path, monkeypatch)
    _write_step3_prompt_inputs(tmp_path)
    _write_step4_readiness_operator_inputs(tmp_path)
    step3_audit_engine.render_step3_prompt()
    step3_audit_engine.step3_raw_output_path().write_text(
        "Promoted Step 3 audit-only findings.\n", encoding="utf-8"
    )
    step3_audit_engine.parse_step3_output()
    assert _load_preflight()["deterministic_prerequisites_ready"] is True

    # Simulate a missing 7b dry-run artifact, then rebuild ONLY the preflight.
    step1_research.step1_promoted_step4_preview_gate_dry_run_path().unlink()
    summary = step1_research._write_promoted_final_safety_preflight_report_only(
        strategy_settings=step1_research.load_strategy_settings_for_handoff_validation(),
        research_decision=json.loads(
            step1_research.step1_research_degraded_mode_decision_path().read_text(
                encoding="utf-8"
            )
        ),
    )
    assert summary["promoted_final_safety_preflight_passed"] == "False"
    preflight = _load_preflight()
    assert preflight["deterministic_prerequisites_ready"] is False
    assert "step4_readiness_dry_run_missing" in preflight["preflight_blockers"]
    assert preflight["current_real_gate_allows"] is False
