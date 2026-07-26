"""Unit tests for the Step 1C deterministic strict-handoff compiler (R2D).

Cover structural completeness (every REQUIRED_TOP_LEVEL_FIELDS emitted with the
right container types), validator pass, the three compilation modes
(evidence_only / evidence_plus_memo / invalid_memo_ignored), the evidence-only
no-NEW_BUY invariant, and that the analyst memo can only contribute qualitative
content — never widen the universe / budgets or authorize execution. Report-only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pathlib import Path
import json

from investment_orchestrator.research.handoff_compiler import (
    COMPILATION_MODE_EVIDENCE_ONLY,
    COMPILATION_MODE_EVIDENCE_PLUS_MEMO,
    COMPILATION_MODE_INVALID_MEMO_IGNORED,
    COMPILED_SCHEMA_VERSION,
    build_compiled_handoff_metadata,
    compile_research_handoff,
    write_compiled_research_handoff,
)
from investment_orchestrator.validators.validate_research_handoff import (
    BASE_ROLE_KEYS,
    REQUIRED_BUY_SCORECARD_FIELDS,
    REQUIRED_EXTENDED_GATE_FIELDS,
    REQUIRED_HANDOFF_FIELDS,
    REQUIRED_TOP_LEVEL_FIELDS,
    validate_research_handoff,
)


def evidence_packet(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "evidence_packet_v1",
        "is_llm_generated": False,
        "universe": {
            "core_universe": ["QQQ", "VOO", "VTI", "VT"],
            "satellite_universe": ["SMH", "IGV"],
            "approved_extended_etf": ["GRID", "CIBR"],
            "allowed_buy_tickers": ["QQQ", "VOO", "VTI", "VT", "SMH", "IGV"],
        },
    }
    base.update(overrides)
    return base


def settings() -> dict[str, Any]:
    return {
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
        "benchmark": "QQQ",
        "ticker_role_fallback": {
            "QQQ": "benchmark_carrier_core",
            "VOO": "diversified_core_buffer",
            "VTI": "diversified_core_buffer",
            "VT": "diversified_core_buffer",
            "SMH": "sector_alpha_tilt",
            "IGV": "sector_alpha_tilt",
            "GRID": "extended_etf_minority_sleeve",
            "CIBR": "extended_etf_minority_sleeve",
        },
    }


def valid_memo(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "as_of_date": "2026-06-28",
        "regime_view": "constructive but rate-sensitive",
        "key_risks": ["rates"],
        "opportunity_summary": "AI compute",
        "ticker_relative_view": [
            {"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "core AI anchor thesis"},
            {"ticker": "SMH", "stance": "deprioritize", "rationale_12m_plus": "semis extended"},
        ],
        "preferred_exposures": ["AI infrastructure"],
        "avoid_or_deprioritize": [],
        "scheduled_event_interpretation": [],
        "confidence": "medium",
        "data_gaps": [],
        "source_notes": [],
    }
    base.update(overrides)
    return base


# --- structural completeness -------------------------------------------------


def test_emits_all_required_top_level_fields() -> None:
    candidate = compile_research_handoff(evidence_packet(), None, strategy_settings=settings())
    for field_name in REQUIRED_TOP_LEVEL_FIELDS:
        assert field_name in candidate, field_name


def test_top_level_container_types_correct() -> None:
    candidate = compile_research_handoff(evidence_packet(), None, strategy_settings=settings())
    assert isinstance(candidate["schema_version"], str) and candidate["schema_version"] == COMPILED_SCHEMA_VERSION
    assert isinstance(candidate["trade_universe"], Mapping)
    assert isinstance(candidate["trade_universe"]["allowed_buy_tickers"], list)
    for list_field in (
        "buy_universe_scorecard",
        "scheduled_events",
        "structural_themes_6_18m",
        "policy_items",
        "top5_next_week",
        "user_approved_extended_etf_static_list",
        "proposed_extended_etf_candidates",
        "extended_etf_candidate_universe",
        "extended_etf_predecision_scorecard",
        "approved_static_list_screening_log",
    ):
        assert isinstance(candidate[list_field], list), list_field
    assert isinstance(candidate["regime_inputs"], Mapping)
    assert isinstance(candidate["optional_extended_etf_sleeve"], Mapping)
    assert isinstance(candidate["strategy_a_research_handoff"], Mapping)


def test_scorecard_rows_have_every_required_field_and_valid_role() -> None:
    candidate = compile_research_handoff(evidence_packet(), None, strategy_settings=settings())
    rows = candidate["buy_universe_scorecard"]
    assert {r["ticker"] for r in rows} == set(evidence_packet()["universe"]["allowed_buy_tickers"])
    for row in rows:
        for field_name in REQUIRED_BUY_SCORECARD_FIELDS:
            assert field_name in row, field_name
        assert row["role_layer"] in BASE_ROLE_KEYS
        assert isinstance(row["execution_priority_this_run"], int)
        assert isinstance(row["event_id_refs"], list)
        assert isinstance(row["structural_theme_refs"], list)
        # R2D never marks a row actionable.
        assert row["actionability_status"] != "actionable_this_run"


def test_handoff_and_gate_have_every_required_field() -> None:
    candidate = compile_research_handoff(evidence_packet(), None, strategy_settings=settings())
    handoff = candidate["strategy_a_research_handoff"]
    for field_name in REQUIRED_HANDOFF_FIELDS:
        assert field_name in handoff, field_name
    gate = handoff["extended_lane_downstream_gate"]
    for field_name in REQUIRED_EXTENDED_GATE_FIELDS:
        assert field_name in gate, field_name


def test_role_layer_derivation_uses_settings_role_map() -> None:
    candidate = compile_research_handoff(evidence_packet(), None, strategy_settings=settings())
    roles = {r["ticker"]: r["role_layer"] for r in candidate["buy_universe_scorecard"]}
    assert roles["QQQ"] == "benchmark_carrier_core"
    assert roles["VOO"] == "diversified_core_buffer"
    assert roles["SMH"] == "sector_alpha_tilt"


def test_role_layer_fallback_without_settings_is_structural_but_validation_blocks() -> None:
    candidate = compile_research_handoff(evidence_packet(), None)
    roles = {r["ticker"]: r["role_layer"] for r in candidate["buy_universe_scorecard"]}
    # Without a role map, core defaults to diversified_core_buffer; satellite to sector tilt.
    assert roles["QQQ"] == "diversified_core_buffer"
    assert roles["SMH"] == "sector_alpha_tilt"
    assert all(role in BASE_ROLE_KEYS for role in roles.values())
    validation = validate_research_handoff(candidate)
    assert validation.valid is False
    assert any(
        "strategy_settings are unavailable or unusable" in reason
        for reason in validation.blocker_reasons
    )


# --- validator pass ----------------------------------------------------------


def test_evidence_only_candidate_passes_validator() -> None:
    candidate = compile_research_handoff(evidence_packet(), None, strategy_settings=settings())
    result = validate_research_handoff(candidate, strategy_settings=settings())
    assert result.valid is True, result.blocker_reasons


def test_evidence_plus_memo_candidate_passes_validator() -> None:
    candidate = compile_research_handoff(evidence_packet(), valid_memo(), strategy_settings=settings())
    result = validate_research_handoff(candidate, strategy_settings=settings())
    assert result.valid is True, result.blocker_reasons


# --- evidence-only mode ------------------------------------------------------


def test_evidence_only_mode_when_no_memo() -> None:
    candidate = compile_research_handoff(evidence_packet(), None, strategy_settings=settings())
    assert candidate["compilation_mode"] == COMPILATION_MODE_EVIDENCE_ONLY


def test_evidence_only_has_no_new_buy_support() -> None:
    candidate = compile_research_handoff(evidence_packet(), None, strategy_settings=settings())
    handoff = candidate["strategy_a_research_handoff"]
    assert handoff["positive_delta_research_supported"] == []
    assert handoff["base_shortlist_eligible_by_role"] == {role: [] for role in BASE_ROLE_KEYS}
    assert handoff["compilation_non_actionable_reason"] == "missing_fresh_analyst_memo"
    # No row is actionable; explicit DATA_GAP reason on each row.
    for row in candidate["buy_universe_scorecard"]:
        assert row["actionability_status"] != "actionable_this_run"
        assert "DATA_GAP" in str(row["compile_blocker_if_any"])


def test_evidence_only_still_covers_allowed_universe_structurally() -> None:
    candidate = compile_research_handoff(evidence_packet(), None, strategy_settings=settings())
    covered = {r["ticker"] for r in candidate["buy_universe_scorecard"]}
    assert covered == set(evidence_packet()["universe"]["allowed_buy_tickers"])
    # Every base ticker is watch-only by role.
    watch = candidate["strategy_a_research_handoff"]["base_watch_only_by_role"]
    all_watch = {t for tickers in watch.values() for t in tickers}
    assert all_watch == set(evidence_packet()["universe"]["allowed_buy_tickers"])


# --- evidence + valid memo ---------------------------------------------------


def test_evidence_plus_memo_mode_and_qualitative_population() -> None:
    candidate = compile_research_handoff(evidence_packet(), valid_memo(), strategy_settings=settings())
    assert candidate["compilation_mode"] == COMPILATION_MODE_EVIDENCE_PLUS_MEMO
    # Memo rationale populates the scorecard thesis for the matching ticker only.
    qqq = next(r for r in candidate["buy_universe_scorecard"] if r["ticker"] == "QQQ")
    assert qqq["thesis_12m_plus_summary"] == "core AI anchor thesis"
    assert qqq["compile_blocker_if_any"] is None
    # Regime view comes from the memo (qualitative).
    assert candidate["regime_inputs"]["regime_view"] == "constructive but rate-sensitive"
    assert candidate["regime_inputs"]["source"] == "analyst_memo"
    # Qualitative context echoes the memo opinion (non-authoritative).
    assert candidate["analyst_memo_qualitative_context"]["preferred_exposures"] == ["AI infrastructure"]


def test_valid_memo_does_not_authorize_execution() -> None:
    candidate = compile_research_handoff(evidence_packet(), valid_memo(), strategy_settings=settings())
    handoff = candidate["strategy_a_research_handoff"]
    # Even with a valid memo, R2D never supports a NEW_BUY.
    assert handoff["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in candidate["buy_universe_scorecard"])


def test_memo_does_not_change_universe_or_extended_list() -> None:
    candidate = compile_research_handoff(evidence_packet(), valid_memo(), strategy_settings=settings())
    assert candidate["trade_universe"]["allowed_buy_tickers"] == ["QQQ", "VOO", "VTI", "VT", "SMH", "IGV"]
    assert candidate["user_approved_extended_etf_static_list"] == ["GRID", "CIBR"]
    # Sleeve stays disabled; memo can never admit an extended ETF.
    assert candidate["optional_extended_etf_sleeve"]["enabled"] is False
    assert candidate["optional_extended_etf_sleeve"]["allowed_extended_etf_tickers"] == []


def test_memo_stance_only_affects_ranking_hint_not_actionability() -> None:
    candidate = compile_research_handoff(evidence_packet(), valid_memo(), strategy_settings=settings())
    by_ticker = {r["ticker"]: r for r in candidate["buy_universe_scorecard"]}
    # prefer sorts earlier than deprioritize, but neither is actionable.
    assert by_ticker["QQQ"]["execution_priority_this_run"] < by_ticker["SMH"]["execution_priority_this_run"]


# --- invalid memo ------------------------------------------------------------


def test_out_of_universe_memo_is_ignored() -> None:
    bad = valid_memo(ticker_relative_view=[{"ticker": "TSLA", "stance": "prefer", "rationale_12m_plus": "x"}])
    candidate = compile_research_handoff(evidence_packet(), bad, strategy_settings=settings())
    assert candidate["compilation_mode"] == COMPILATION_MODE_INVALID_MEMO_IGNORED
    # TSLA never enters the universe or scorecard.
    assert "TSLA" not in candidate["trade_universe"]["allowed_buy_tickers"]
    assert "TSLA" not in {r["ticker"] for r in candidate["buy_universe_scorecard"]}


def test_budget_carrying_memo_is_ignored() -> None:
    bad = valid_memo(target_new_buy_budget_this_run=50000)
    candidate = compile_research_handoff(evidence_packet(), bad, strategy_settings=settings())
    assert candidate["compilation_mode"] == COMPILATION_MODE_INVALID_MEMO_IGNORED
    # No budget field leaks into the compiled handoff.
    assert "target_new_buy_budget_this_run" not in candidate


def test_invalid_memo_mode_has_no_new_buy_support() -> None:
    bad = valid_memo(confidence="adequate")  # invalid confidence -> ignored
    candidate = compile_research_handoff(evidence_packet(), bad, strategy_settings=settings())
    assert candidate["compilation_mode"] == COMPILATION_MODE_INVALID_MEMO_IGNORED
    assert candidate["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert "analyst_memo_qualitative_context" not in candidate


def test_non_mapping_memo_is_ignored() -> None:
    candidate = compile_research_handoff(evidence_packet(), ["not", "a", "memo"], strategy_settings=settings())  # type: ignore[arg-type]
    assert candidate["compilation_mode"] == COMPILATION_MODE_INVALID_MEMO_IGNORED


# --- defensive ---------------------------------------------------------------


def test_compiler_never_raises_on_empty_packet() -> None:
    candidate = compile_research_handoff({}, None)
    assert candidate["compilation_mode"] == COMPILATION_MODE_EVIDENCE_ONLY
    assert candidate["trade_universe"]["allowed_buy_tickers"] == []
    for field_name in REQUIRED_TOP_LEVEL_FIELDS:
        assert field_name in candidate


def test_is_llm_generated_false_on_candidate() -> None:
    candidate = compile_research_handoff(evidence_packet(), valid_memo(), strategy_settings=settings())
    assert candidate["is_llm_generated"] is False
    assert candidate["report_only"] is True


# --- metadata ----------------------------------------------------------------


def test_metadata_reports_mode_and_no_permission_effect() -> None:
    candidate = compile_research_handoff(evidence_packet(), None, strategy_settings=settings())
    validation = validate_research_handoff(candidate, strategy_settings=settings())
    meta = build_compiled_handoff_metadata(
        candidate=candidate,
        validation=validation,
        evidence_packet=evidence_packet(),
        analyst_memo=None,
        evidence_packet_path="/x/evidence_packet.json",
        generated_at="t",
    )
    assert meta["is_llm_generated"] is False
    assert meta["report_only"] is True
    assert meta["permission_effect"] == "none"
    assert meta["compilation_mode"] == COMPILATION_MODE_EVIDENCE_ONLY
    assert meta["analyst_memo_present"] is False
    assert meta["analyst_memo_valid"] is False
    assert meta["compiled_candidate_valid"] is True
    assert meta["missing_required_top_level_fields"] == []
    assert set(meta["required_top_level_fields_emitted"]) == set(REQUIRED_TOP_LEVEL_FIELDS)
    assert meta["source_evidence_packet"]["sha256"] is not None


def test_metadata_marks_valid_memo_present_and_valid() -> None:
    memo = valid_memo()
    candidate = compile_research_handoff(evidence_packet(), memo, strategy_settings=settings())
    meta = build_compiled_handoff_metadata(
        candidate=candidate,
        validation=validate_research_handoff(candidate, strategy_settings=settings()),
        evidence_packet=evidence_packet(),
        analyst_memo=memo,
        generated_at="t",
    )
    assert meta["analyst_memo_present"] is True
    assert meta["analyst_memo_valid"] is True
    assert meta["source_analyst_memo"]["sha256"] is not None


def test_metadata_marks_invalid_memo_present_but_not_valid() -> None:
    bad = valid_memo(confidence="adequate")
    candidate = compile_research_handoff(evidence_packet(), bad, strategy_settings=settings())
    meta = build_compiled_handoff_metadata(
        candidate=candidate,
        validation=validate_research_handoff(candidate, strategy_settings=settings()),
        evidence_packet=evidence_packet(),
        analyst_memo=bad,
        generated_at="t",
    )
    assert meta["analyst_memo_present"] is True
    assert meta["analyst_memo_valid"] is False
    assert meta["compilation_mode"] == COMPILATION_MODE_INVALID_MEMO_IGNORED


# --- R2E.3 support-signal artifact (write flow) ------------------------------


def test_write_flow_emits_support_signals_when_path_given(tmp_path: Path) -> None:
    signals_path = tmp_path / "compiled_support_signals.json"
    summary = write_compiled_research_handoff(
        candidate_path=tmp_path / "candidate.json",
        validation_path=tmp_path / "validation.json",
        metadata_path=tmp_path / "metadata.json",
        evidence_packet=evidence_packet(),
        analyst_memo=valid_memo(source_notes=[{"claim": "c", "source": "s", "source_quality": "official"}]),
        strategy_settings=settings(),
        support_signals_path=signals_path,
        now=None,
    )
    assert summary["compiled_support_signals_path"] == str(signals_path)
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    assert signals["report_only"] is True
    assert signals["permission_effect"] == "none"
    assert signals["compilation_mode"] == COMPILATION_MODE_EVIDENCE_PLUS_MEMO
    # QQQ prefers with rationale + source_notes → qualitative-support-only, never accepted.
    assert {s["ticker"] for s in signals["qualitative_support_only"]} == {"QQQ"}
    assert signals["accepted_support_signals"] == []


def test_write_flow_skips_support_signals_when_path_omitted(tmp_path: Path) -> None:
    summary = write_compiled_research_handoff(
        candidate_path=tmp_path / "candidate.json",
        validation_path=tmp_path / "validation.json",
        metadata_path=tmp_path / "metadata.json",
        evidence_packet=evidence_packet(),
        analyst_memo=None,
        strategy_settings=settings(),
        now=None,
    )
    assert "compiled_support_signals_path" not in summary
    assert not (tmp_path / "compiled_support_signals.json").exists()
