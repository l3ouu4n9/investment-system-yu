"""Unit tests for the R2E.5b-1 actionable compiled-handoff candidate (report-only).

The builder overlays the R2E.5b-0 preview's actionable rows onto a full strict
handoff and proves the shape passes `validate_research_handoff` — WITHOUT touching
the active compiled handoff, availability, gates, or permissions. Tests build the
*real* support-signals + preview + base compiler output so the chain is faithful.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from investment_orchestrator.research.actionable_handoff_candidate import (
    CANDIDATE_SCHEMA_VERSION,
    build_actionable_handoff_candidate,
    build_actionable_handoff_metadata,
)
from investment_orchestrator.research.actionable_handoff_preview import (
    build_actionable_handoff_preview,
)
from investment_orchestrator.research.handoff_compiler import compile_research_handoff
from investment_orchestrator.research.research_anchors import build_research_anchors_summary
from investment_orchestrator.research.support_signals import build_compiled_support_signals
from investment_orchestrator.validators.validate_research_handoff import validate_research_handoff

_MODE = "evidence_plus_memo"


# --- builders ----------------------------------------------------------------


def settings(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "as_of": "2026-06-28",
        "benchmark": "QQQ",
        "core_universe": ["QQQ", "VOO"],
        "satellite_universe": ["SMH"],
        "user_approved_extended_etf_static_list": ["GRID"],
        "ticker_role_fallback": {
            "QQQ": "benchmark_carrier_core",
            "VOO": "diversified_core_buffer",
            "SMH": "sector_alpha_tilt",
        },
    }
    base.update(overrides)
    return base


def evidence_packet(*, base_cap: int = 2, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "evidence_packet_v1",
        "is_llm_generated": False,
        "universe": {
            "core_universe": ["QQQ", "VOO"],
            "satellite_universe": ["SMH"],
            "approved_extended_etf": ["GRID"],
            "allowed_buy_tickers": ["QQQ", "VOO", "SMH"],
        },
        "budget_settings": {
            "max_new_tickers_per_week": {
                "base_universe_new_tickers_per_week": base_cap,
                "extended_etf_sleeve_new_tickers_per_week": 2,
            }
        },
    }
    base.update(overrides)
    return base


def anchor_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": "AI_CAPEX_2026H2",
        "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"],
        "anchor_date_et": "2026-06-15",
        "source_type": "operator",
        "confidence_floor": "medium",
        "valid": True,
        "stale": False,
        "usable": True,
    }
    base.update(overrides)
    return base


def packet_with_anchors(anchors: list[dict[str, Any]], *, base_cap: int = 2) -> dict[str, Any]:
    p = evidence_packet(base_cap=base_cap)
    p["research_anchors"] = {"available": True, "valid": True, "anchors": anchors}
    return p


def memo(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "as_of_date": "2026-06-28",
        "regime_view": "constructive",
        "confidence": "high",
        "ticker_relative_view": [
            {
                "ticker": "QQQ",
                "stance": "prefer",
                "rationale_12m_plus": "AI capex structural growth",
                "anchor_id_refs": ["AI_CAPEX_2026H2"],
            }
        ],
        "avoid_or_deprioritize": [],
        "data_gaps": [],
        "source_notes": [{"claim": "AI capex", "source": "10-K", "source_quality": "official"}],
    }
    base.update(overrides)
    return base


def _chain(packet: dict[str, Any], m: dict[str, Any] | None, *, mode: str = _MODE):
    """Return (base_candidate, preview, support_signals) for a packet + memo."""
    sig = build_compiled_support_signals(evidence_packet=packet, analyst_memo=m, compilation_mode=mode)
    preview = build_actionable_handoff_preview(evidence_packet=packet, analyst_memo=m, compiled_support_signals=sig)
    base = compile_research_handoff(packet, m, strategy_settings=settings())
    return base, preview, sig


def _build(packet: dict[str, Any], m: dict[str, Any] | None, *, mode: str = _MODE):
    base, preview, _ = _chain(packet, m, mode=mode)
    return build_actionable_handoff_candidate(
        evidence_packet=packet,
        analyst_memo=m,
        actionable_handoff_preview=preview,
        base_candidate=base,
        strategy_settings=settings(),
    )


def _row(candidate: dict[str, Any], ticker: str) -> dict[str, Any]:
    return {r["ticker"]: r for r in candidate["buy_universe_scorecard"]}[ticker]


# --- builder: happy path -----------------------------------------------------


def test_preview_row_produces_validator_compatible_actionable_row() -> None:
    candidate = _build(packet_with_anchors([anchor_row()]), memo())
    result = validate_research_handoff(candidate, strategy_settings=settings())
    assert result.valid is True, result.fail_reasons

    row = _row(candidate, "QQQ")
    assert row["actionability_status"] == "actionable_this_run"
    assert row["thesis_12m_plus_supported"] is True
    assert row["thesis_linkage_quality"] in {"strong", "adequate"}
    assert row["structural_theme_refs"] == ["AI_CAPEX_2026H2"]
    assert row["primary_anchor_event_id"] == "AI_CAPEX_2026H2"  # falls back to ref for a theme
    assert row["primary_anchor_date_et"] == "2026-06-15"
    assert row["compile_blocker_if_any"] is None
    assert row["primary_anchor_type"] == "structural_theme"


def test_scheduled_event_row_uses_event_id_refs() -> None:
    anchor = anchor_row(anchor_id="FOMC_2026_07", anchor_type="scheduled_macro_event", anchor_date_et="2026-07-29")
    m = memo()
    m["ticker_relative_view"][0]["anchor_id_refs"] = ["FOMC_2026_07"]
    candidate = _build(packet_with_anchors([anchor]), m)
    assert validate_research_handoff(candidate, strategy_settings=settings()).valid is True
    row = _row(candidate, "QQQ")
    assert row["event_id_refs"] == ["FOMC_2026_07"]
    assert row["structural_theme_refs"] == []
    assert row["primary_anchor_event_id"] == "FOMC_2026_07"


def test_positive_delta_populated_only_in_separate_candidate() -> None:
    base, preview, _ = _chain(packet_with_anchors([anchor_row()]), memo())
    candidate = build_actionable_handoff_candidate(
        evidence_packet=packet_with_anchors([anchor_row()]),
        analyst_memo=memo(),
        actionable_handoff_preview=preview,
        base_candidate=base,
        strategy_settings=settings(),
    )
    # The separate candidate populates positive_delta...
    assert candidate["strategy_a_research_handoff"]["positive_delta_research_supported"] == ["QQQ"]
    assert candidate["actionable_this_run_tickers"] == ["QQQ"]
    # ...while the base (active) candidate it was built from stays non-actionable.
    assert base["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in base["buy_universe_scorecard"])


def test_report_only_markers_present() -> None:
    candidate = _build(packet_with_anchors([anchor_row()]), memo())
    assert candidate["schema_version"] == CANDIDATE_SCHEMA_VERSION
    assert candidate["is_llm_generated"] is False
    assert candidate["report_only"] is True
    assert candidate["permission_effect"] == "none"
    assert candidate["not_authorization"] is True


# --- builder: extended ETF stays disabled ------------------------------------


def test_extended_etf_sleeve_remains_disabled() -> None:
    candidate = _build(packet_with_anchors([anchor_row()]), memo())
    sleeve = candidate["optional_extended_etf_sleeve"]
    assert sleeve["enabled"] is False
    assert sleeve["allowed_extended_etf_tickers"] == []
    # GRID (approved extended) never enters the actionable base universe.
    assert "GRID" not in candidate["trade_universe"]["allowed_buy_tickers"]
    assert "GRID" not in candidate["strategy_a_research_handoff"]["positive_delta_research_supported"]


# --- builder: cap respected --------------------------------------------------


def test_cap_respected_in_candidate() -> None:
    anchors = [
        anchor_row(anchor_id="A_QQQ", applicable_tickers=["QQQ"]),
        anchor_row(anchor_id="A_VOO", applicable_tickers=["VOO"]),
    ]
    m = memo()
    m["ticker_relative_view"] = [
        {"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "q", "anchor_id_refs": ["A_QQQ"]},
        {"ticker": "VOO", "stance": "prefer", "rationale_12m_plus": "v", "anchor_id_refs": ["A_VOO"]},
    ]
    candidate = _build(packet_with_anchors(anchors, base_cap=1), m)
    assert validate_research_handoff(candidate, strategy_settings=settings()).valid is True
    # Cap of 1: exactly one actionable row; the other stays watch-only.
    assert candidate["actionable_this_run_tickers"] == ["QQQ"]
    assert _row(candidate, "VOO")["actionability_status"] == "ranking_hold_watch_only"


# --- builder: rejected / non-preview tickers stay watch-only -----------------


def test_rejected_preview_tickers_do_not_become_actionable() -> None:
    # VOO has no anchor and no prefer view → not in preview_actionable_rows.
    candidate = _build(packet_with_anchors([anchor_row()]), memo())
    assert _row(candidate, "VOO")["actionability_status"] == "ranking_hold_watch_only"
    assert _row(candidate, "SMH")["actionability_status"] == "ranking_hold_watch_only"
    assert "VOO" not in candidate["strategy_a_research_handoff"]["positive_delta_research_supported"]


# --- builder: no preview rows → non-actionable candidate ---------------------


def test_no_preview_rows_yields_non_actionable_candidate() -> None:
    # base cap 0 → preview surfaces no actionable rows.
    candidate = _build(packet_with_anchors([anchor_row()], base_cap=0), memo())
    assert validate_research_handoff(candidate, strategy_settings=settings()).valid is True
    assert candidate["actionable_this_run_tickers"] == []
    assert candidate["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in candidate["buy_universe_scorecard"])


def test_metadata_explicit_zero_when_no_preview_rows() -> None:
    packet = packet_with_anchors([anchor_row()], base_cap=0)
    base, preview, sig = _chain(packet, memo())
    candidate = build_actionable_handoff_candidate(
        evidence_packet=packet, analyst_memo=memo(), actionable_handoff_preview=preview,
        base_candidate=base, strategy_settings=settings(),
    )
    validation = validate_research_handoff(candidate, strategy_settings=settings())
    metadata = build_actionable_handoff_metadata(
        candidate=candidate, validation=validation, actionable_handoff_preview=preview,
        compiled_support_signals=sig, evidence_packet=packet, base_candidate=base,
        used_active_compiled_handoff_as_base=True,
    )
    assert metadata["candidate_actionable_row_count"] == 0
    assert metadata["preview_actionable_row_count"] == 0
    assert metadata["validation_passed"] is True


# --- builder: primary_anchor fields sourced from preview ---------------------


def test_primary_anchor_fields_and_refs_come_from_preview() -> None:
    anchor = anchor_row(anchor_id="THEME_X", anchor_type="structural_theme", anchor_date_et="2026-05-01")
    m = memo()
    m["ticker_relative_view"][0]["anchor_id_refs"] = ["THEME_X"]
    candidate = _build(packet_with_anchors([anchor]), m)
    row = _row(candidate, "QQQ")
    assert row["structural_theme_refs"] == ["THEME_X"]
    assert row["primary_anchor_event_id"] == "THEME_X"
    assert row["primary_anchor_date_et"] == "2026-05-01"


def test_data_gap_tainted_summary_is_sanitized_for_actionable_row() -> None:
    # A memo rationale containing a DATA_GAP marker must not taint an actionable row.
    m = memo()
    m["ticker_relative_view"][0]["rationale_12m_plus"] = "thesis with missing data caveat"
    candidate = _build(packet_with_anchors([anchor_row()]), m)
    result = validate_research_handoff(candidate, strategy_settings=settings())
    assert result.valid is True, result.fail_reasons
    row = _row(candidate, "QQQ")
    assert "missing" not in row["thesis_12m_plus_summary"].lower()


# --- metadata fields ---------------------------------------------------------


def test_metadata_reports_sources_and_non_authorization() -> None:
    packet = packet_with_anchors([anchor_row()])
    base, preview, sig = _chain(packet, memo())
    candidate = build_actionable_handoff_candidate(
        evidence_packet=packet, analyst_memo=memo(), actionable_handoff_preview=preview,
        base_candidate=base, strategy_settings=settings(),
    )
    validation = validate_research_handoff(candidate, strategy_settings=settings())
    metadata = build_actionable_handoff_metadata(
        candidate=candidate, validation=validation, actionable_handoff_preview=preview,
        compiled_support_signals=sig, evidence_packet=packet, base_candidate=base,
        used_active_compiled_handoff_as_base=True,
        actionable_handoff_preview_path="/preview.json",
        compiled_support_signals_path="/signals.json",
        evidence_packet_path="/evidence.json",
        base_candidate_path="/active.json",
    )
    assert metadata["report_only"] is True
    assert metadata["permission_effect"] == "none"
    assert metadata["not_authorization"] is True
    assert metadata["consumed_by_availability"] is False
    assert metadata["consumed_by_step2"] is False
    assert metadata["preview_actionable_row_count"] == 1
    assert metadata["candidate_actionable_row_count"] == 1
    assert metadata["validation_passed"] is True
    assert metadata["used_active_compiled_handoff_as_base"] is True
    for key in (
        "source_actionable_handoff_preview",
        "source_compiled_support_signals",
        "source_evidence_packet",
        "source_active_compiled_handoff",
    ):
        assert metadata[key]["path"], key
        assert metadata[key]["sha256"], key


# --- builder: fallback re-compile when no base provided ----------------------


def test_builder_recompiles_base_when_absent() -> None:
    packet = packet_with_anchors([anchor_row()])
    _, preview, _ = _chain(packet, memo())
    candidate = build_actionable_handoff_candidate(
        evidence_packet=packet, analyst_memo=memo(), actionable_handoff_preview=preview,
        base_candidate=None, strategy_settings=settings(),
    )
    assert validate_research_handoff(candidate, strategy_settings=settings()).valid is True
    assert candidate["actionable_this_run_tickers"] == ["QQQ"]


def test_builder_never_raises_on_empty_inputs() -> None:
    candidate = build_actionable_handoff_candidate(
        evidence_packet=None, analyst_memo=None, actionable_handoff_preview=None,
    )
    assert candidate["schema_version"] == CANDIDATE_SCHEMA_VERSION
    assert candidate["actionable_this_run_tickers"] == []


def test_unquoted_yaml_anchor_dates_produce_actionable_candidate(tmp_path: Path) -> None:
    # Regression (R2E.5a-date-normalization): the actionable-candidate happy path
    # must work end-to-end when research_anchors.yaml leaves anchor_date_et /
    # valid_from / valid_until unquoted (PyYAML decodes them as datetime.date).
    anchors_path = tmp_path / "research_anchors.yaml"
    anchors_path.write_text(
        "schema_version: research_anchors_v1\n"
        "as_of_date: 2026-06-30\n"
        "is_llm_generated: false\n"
        "anchors:\n"
        "  - anchor_id: AI_CAPEX_2026H2\n"
        "    anchor_type: structural_theme\n"
        "    applicable_tickers: [QQQ]\n"
        "    source_type: operator\n"
        "    anchor_date_et: 2026-06-15\n"
        "    valid_from: 2026-06-01\n"
        "    valid_until: 2026-07-15\n"
        "    confidence_floor: medium\n",
        encoding="utf-8",
    )
    anchors_summary = build_research_anchors_summary(
        anchors_path, allowed_universe=["QQQ", "VOO", "SMH"], today="2026-06-28"
    )
    assert anchors_summary["available"] is True
    assert anchors_summary["valid"] is True

    packet = evidence_packet()
    packet["research_anchors"] = anchors_summary
    m = memo()
    candidate = _build(packet, m)

    result = validate_research_handoff(candidate, strategy_settings=settings())
    assert result.valid is True, result.fail_reasons
    assert candidate["actionable_this_run_tickers"] == ["QQQ"]
    row = _row(candidate, "QQQ")
    assert row["actionability_status"] == "actionable_this_run"
    assert row["primary_anchor_date_et"] == "2026-06-15"


def test_preview_row_without_anchor_date_is_not_promotable() -> None:
    # A structural-theme anchor with no date string is accepted by support signals
    # and appears in the preview, but the strict validator requires a date for an
    # actionable row → the builder leaves it watch-only (fail-closed), still valid.
    anchor = anchor_row(anchor_id="NO_DATE", anchor_date_et=None)
    m = memo()
    m["ticker_relative_view"][0]["anchor_id_refs"] = ["NO_DATE"]
    candidate = _build(packet_with_anchors([anchor]), m)
    assert validate_research_handoff(candidate, strategy_settings=settings()).valid is True
    assert candidate["actionable_this_run_tickers"] == []
    assert _row(candidate, "QQQ")["actionability_status"] == "ranking_hold_watch_only"
