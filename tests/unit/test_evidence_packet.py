"""Unit tests for the Step 1A deterministic evidence-packet builder (R2B).

Cover the pure builder, the invariant checker, DATA_GAP handling for a missing
snapshot, last-good summarization, the no-LLM-fields guarantee, and that no
analyst_memo opinion field leaks in. Report-only: none of this gates anything.
"""

from __future__ import annotations

from typing import Any

from investment_orchestrator.research.evidence_packet import (
    LLM_MEMO_FIELD_NAMES,
    build_evidence_packet,
    check_evidence_packet_invariants,
)


def settings(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "as_of": "2026-06-28",
        "run_timestamp_et": "2026-06-29 01:49 ET",
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
        "user_approved_extended_etf_static_list": ["GRID", "CIBR", "BOTZ"],
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.00,
        "max_new_tickers_per_week": {
            "base_universe_new_tickers_per_week": 0,
            "extended_etf_sleeve_new_tickers_per_week": 2,
        },
    }
    base.update(overrides)
    return base


def build(**kwargs: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "strategy_settings": settings(),
        "portfolio_snapshot_text": None,
        "now_date": "2026-06-28",
        "generated_at": "2026-06-28T00:00:00+00:00",
    }
    params.update(kwargs)
    return build_evidence_packet(**params)


# --- basic build + invariants ------------------------------------------------


def test_builds_from_minimal_settings_and_passes_invariants() -> None:
    packet = build_evidence_packet(
        strategy_settings={"core_universe": ["QQQ"], "satellite_universe": ["SMH"]},
        portfolio_snapshot_text=None,
        now_date=None,
        generated_at="t",
    )
    assert check_evidence_packet_invariants(packet) == []


def test_is_llm_generated_is_false() -> None:
    assert build()["is_llm_generated"] is False
    assert build()["source"] == "deterministic_inputs"


def test_universe_combines_core_satellite_and_approved_extended() -> None:
    universe = build()["universe"]
    assert universe["core_universe"] == ["QQQ", "VOO", "VTI", "VT"]
    assert universe["satellite_universe"] == ["SMH", "IGV"]
    assert universe["approved_extended_etf"] == ["GRID", "CIBR", "BOTZ"]
    # allowed_buy = core ∪ satellite (extended is NOT a buy-universe member).
    assert universe["allowed_buy_tickers"] == ["QQQ", "VOO", "VTI", "VT", "SMH", "IGV"]
    assert universe["role_source_by_ticker"]["QQQ"] == "core"
    assert universe["role_source_by_ticker"]["SMH"] == "satellite"
    assert universe["role_source_by_ticker"]["GRID"] == "approved_extended"


def test_universe_tickers_are_normalized_and_deduped() -> None:
    packet = build(strategy_settings=settings(core_universe=["qqq", " QQQ ", "voo"], satellite_universe=["smh"]))
    universe = packet["universe"]
    assert universe["core_universe"] == ["QQQ", "VOO"]  # uppercased + deduped
    assert universe["satellite_universe"] == ["SMH"]
    assert check_evidence_packet_invariants(packet) == []


def test_budget_settings_include_hard_cap_and_target_when_present() -> None:
    budget = build()["budget_settings"]
    assert budget["hard_cap_open_orders_budget"] == 38211.29
    assert budget["target_new_buy_budget_this_run"] == 12000.00
    assert budget["max_new_tickers_per_week"] == {
        "base_universe_new_tickers_per_week": 0,
        "extended_etf_sleeve_new_tickers_per_week": 2,
    }


def test_missing_budget_is_explicit_data_gap_not_crash() -> None:
    packet = build(strategy_settings=settings(hard_cap_open_orders_budget=None, target_new_buy_budget_this_run=None))
    budget = packet["budget_settings"]
    assert budget["hard_cap_open_orders_budget"] is None
    assert budget["target_new_buy_budget_this_run"] is None
    fields = {g["field"] for g in packet["data_gaps"]}
    assert "budget_settings.hard_cap_open_orders_budget" in fields
    assert "budget_settings.target_new_buy_budget_this_run" in fields


# --- portfolio snapshot summary ----------------------------------------------


def test_missing_snapshot_becomes_explicit_data_gap() -> None:
    packet = build(portfolio_snapshot_text=None)
    summary = packet["portfolio_snapshot_summary"]
    assert summary["available"] is False
    assert summary["sha256"] is None
    assert any(g["field"] == "portfolio_snapshot" for g in packet["data_gaps"])
    # No crash; invariants still hold.
    assert check_evidence_packet_invariants(packet) == []


def test_present_snapshot_summarizes_section_2a_and_hashes_bytes() -> None:
    snapshot = (
        "(1) current_holdings_base\nQQQ | 81 | 628.34\n"
        "(2a) existing_buy_open_orders_summary\n"
        "TICKER | budget | compiled | residual | tid | a | b | c | hi | lo | n | steps | qtys\n"
        "QQQ | 1277.14 | 692.24 | 584.90 | T4-E | 713.65 | 2026-06-23 |  | 692.24 | 692.24 | 1 | L1@692.24 | L1:1\n"
        "VOO | 7757.08 | 6436.08 | 1321.00 | T4-B | 697.30 | 2026-06-10 |  | 658.95 | 613.62 | 3 | L2@658.95 | L2:1\n"
        "(2b) sell_open_orders\nNONE\n"
    )
    summary = build(portfolio_snapshot_text=snapshot)["portfolio_snapshot_summary"]
    assert summary["available"] is True
    assert isinstance(summary["sha256"], str) and len(summary["sha256"]) == 64
    eb = summary["existing_buy_open_orders"]
    assert eb["section_present"] is True
    assert eb["ticker_count"] == 2
    assert eb["tickers"] == ["QQQ", "VOO"]
    assert eb["total_budget"] == "9034.22"  # 1277.14 + 7757.08
    # Holdings / sell / lots are explicit DATA_GAPs (no brittle parsing).
    assert summary["current_holdings"]["structured_parse_available"] is False
    assert summary["sell_open_orders"]["structured_parse_available"] is False
    assert summary["ltcg_sellable_lots"]["structured_parse_available"] is False


# --- last-good summary -------------------------------------------------------


def test_last_good_summary_included_when_present() -> None:
    metadata = {
        "source_as_of_date": "2026-06-23",
        "strategy_settings_hash": "deadbeef",
        "source_run_id": "unknown",
        "universe": {"core_universe": ["QQQ", "VOO", "VTI", "VT"], "satellite_universe": ["SMH", "IGV"]},
    }
    summary = build(last_good_available=True, last_good_metadata=metadata)["last_good_research_summary"]
    assert summary["available"] is True
    assert summary["source_as_of_date"] == "2026-06-23"
    assert summary["age_days"] == 5  # 2026-06-28 - 2026-06-23
    assert summary["universe_match"] is True
    assert summary["strategy_settings_hash_match"] is False  # current hash != "deadbeef"


def test_last_good_absent_summary() -> None:
    assert build(last_good_available=False)["last_good_research_summary"] == {"available": False}


# --- market metrics / scheduled events are deterministic DATA_GAPs -----------


def test_market_metrics_and_events_unavailable_without_feed() -> None:
    packet = build()
    assert packet["market_metrics"]["available"] is False
    assert "DATA_GAP" in packet["market_metrics"]["data_gap"]
    assert packet["scheduled_events_deterministic"]["available"] is False
    assert "DATA_GAP" in packet["scheduled_events_deterministic"]["data_gap"]


# --- no LLM opinion fields ---------------------------------------------------


def test_no_llm_memo_fields_appear_at_top_level() -> None:
    packet = build()
    for memo_field in LLM_MEMO_FIELD_NAMES:
        assert memo_field not in packet
    assert check_evidence_packet_invariants(packet) == []


def test_invariant_checker_flags_injected_llm_field() -> None:
    packet = build()
    packet["regime_view"] = "bullish"  # simulate contamination
    problems = check_evidence_packet_invariants(packet)
    assert any("regime_view" in p for p in problems)


def test_invariant_checker_flags_llm_generated_true() -> None:
    packet = build()
    packet["is_llm_generated"] = True
    assert any("is_llm_generated" in p for p in check_evidence_packet_invariants(packet))


def test_builder_never_raises_on_none_settings() -> None:
    packet = build_evidence_packet(strategy_settings=None, portfolio_snapshot_text=None, generated_at="t")
    assert packet["strategy_settings_summary"] == {"available": False}
    assert packet["universe"]["allowed_buy_tickers"] == []
    assert any(g["field"] == "strategy_settings" for g in packet["data_gaps"])
    assert check_evidence_packet_invariants(packet) == []


# --- research anchors integration (R2E.5a, report-only) ----------------------


def test_research_anchors_absent_becomes_data_gap() -> None:
    packet = build()  # no research_anchors_summary passed
    anchors = packet["research_anchors"]
    assert anchors["available"] is False
    assert "research_anchors_missing" in anchors["data_gap"]
    assert anchors["permission_effect"] == "none"
    assert any(g["field"] == "research_anchors" for g in packet["data_gaps"])
    # Still deterministic; invariants hold.
    assert packet["is_llm_generated"] is False
    assert check_evidence_packet_invariants(packet) == []


def test_research_anchors_valid_summary_embedded() -> None:
    summary = {
        "available": True,
        "valid": True,
        "schema_version": "research_anchors_v1",
        "anchor_count": 0,
        "valid_anchor_count": 0,
        "stale_anchor_count": 0,
        "invalid_anchor_count": 0,
        "anchors": [],
        "consumed_for_support_acceptance": False,
        "permission_effect": "none",
    }
    packet = build(research_anchors_summary=summary)
    assert packet["research_anchors"] == summary
    # A present (available) anchor summary is NOT a data_gap.
    assert not any(g["field"] == "research_anchors" for g in packet["data_gaps"])
    assert packet["is_llm_generated"] is False
    assert check_evidence_packet_invariants(packet) == []


def test_research_anchors_invalid_summary_included_still_builds() -> None:
    summary = {
        "available": True,
        "valid": False,
        "schema_version": "research_anchors_v1",
        "anchor_count": 1,
        "valid_anchor_count": 0,
        "invalid_anchor_count": 1,
        "anchors": [{"anchor_id": "X", "valid": False, "problems": ["bad"]}],
        "errors": ["is_llm_generated must be exactly false."],
        "consumed_for_support_acceptance": False,
        "permission_effect": "none",
    }
    packet = build(research_anchors_summary=summary)
    assert packet["research_anchors"]["valid"] is False
    assert packet["research_anchors"]["errors"]
    # Invalid anchors never make the packet LLM-generated and never crash the build.
    assert packet["is_llm_generated"] is False
    assert check_evidence_packet_invariants(packet) == []


def test_research_anchors_field_is_required_and_always_present() -> None:
    from investment_orchestrator.research.evidence_packet import EVIDENCE_PACKET_REQUIRED_FIELDS

    assert "research_anchors" in EVIDENCE_PACKET_REQUIRED_FIELDS
    assert "research_anchors" in build()
