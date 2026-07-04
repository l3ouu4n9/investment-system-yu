"""Unit tests for the R2E.5b-0 actionable-handoff preview (report-only artifact).

The preview answers "which tickers WOULD become actionable rows if a future PR
opened an actionable path?" from the already-compiled report-only inputs. It NEVER
authorizes a trade, never feeds the active compiled handoff, and never changes
allowed_actions. These tests build the *real* support-signal artifact and feed it
to the preview builder so the preview can never disagree with the extractor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from investment_orchestrator.research.actionable_handoff_preview import (
    ACTIONABILITY_STATUS_PREVIEW,
    GLOBAL_BASE_NEW_TICKER_CAP_ZERO,
    GLOBAL_NO_ACCEPTED_SUPPORT_SIGNALS,
    PREVIEW_EXTENDED_ETF_NOT_ALLOWED,
    PREVIEW_LIMIT_MAX_NEW_TICKERS_EXCEEDED,
    PREVIEW_LISTED_IN_AVOID,
    PREVIEW_LOW_CONFIDENCE,
    PREVIEW_MISSING_ANCHOR,
    PREVIEW_MISSING_PRIMARY_ANCHOR_DATE,
    PREVIEW_MISSING_SOURCE_NOTES,
    SCHEMA_VERSION,
    build_actionable_handoff_preview,
)
from investment_orchestrator.research.active_research_anchor_registry import (
    active_anchor_registry_from_research_anchors_summary,
)
from investment_orchestrator.research.research_anchors import build_research_anchors_summary
from investment_orchestrator.research.support_signals import build_compiled_support_signals

_MODE_EVIDENCE_PLUS_MEMO = "evidence_plus_memo"


# --- builders (mirror test_support_signals.py) -------------------------------


def evidence_packet(*, base_cap: int | None = 2, **overrides: Any) -> dict[str, Any]:
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
            "max_new_tickers_per_week": (
                None
                if base_cap is None
                else {
                    "base_universe_new_tickers_per_week": base_cap,
                    "extended_etf_sleeve_new_tickers_per_week": 2,
                }
            )
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


def packet_with_anchors(anchors: list[dict[str, Any]], *, base_cap: int | None = 2) -> dict[str, Any]:
    p = evidence_packet(base_cap=base_cap)
    p["research_anchors"] = {"available": True, "valid": True, "anchors": anchors}
    p["active_anchor_registry"] = active_anchor_registry_from_research_anchors_summary(
        p["research_anchors"]
    )
    return p


def memo(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "as_of_date": "2026-06-28",
        "confidence": "high",
        "ticker_relative_view": [
            {
                "ticker": "QQQ",
                "stance": "prefer",
                "rationale_12m_plus": "anchor thesis",
                "anchor_id_refs": ["AI_CAPEX_2026H2"],
            }
        ],
        "avoid_or_deprioritize": [],
        "data_gaps": [],
        "source_notes": [{"claim": "AI capex up", "source": "10-K", "source_quality": "official"}],
    }
    base.update(overrides)
    return base


def _preview(packet: dict[str, Any], m: dict[str, Any] | None, *, mode: str = _MODE_EVIDENCE_PLUS_MEMO):
    signals = build_compiled_support_signals(evidence_packet=packet, analyst_memo=m, compilation_mode=mode)
    return build_actionable_handoff_preview(
        evidence_packet=packet,
        analyst_memo=m,
        compiled_support_signals=signals,
        compiled_handoff_candidate={"schema_version": "research_handoff_compiled_v1"},
        evidence_packet_path="/evidence_packet.json",
        compiled_support_signals_path="/compiled_support_signals.json",
        compiled_handoff_candidate_path="/compiled_research_handoff_candidate.json",
    )


def _rejected_by_ticker(preview: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["ticker"]: r for r in preview["rejected_preview_rows"]}


# --- schema / non-authorization invariants -----------------------------------


def test_preview_is_report_only_and_non_authoritative() -> None:
    preview = _preview(packet_with_anchors([anchor_row()]), memo())
    assert preview["schema_version"] == SCHEMA_VERSION
    assert preview["is_llm_generated"] is False
    assert preview["report_only"] is True
    assert preview["permission_effect"] == "none"
    assert preview["not_authorization"] is True
    assert preview["extended_etf_sleeve_preview_enabled"] is False
    # Provenance: each source carries a path + a content hash.
    for key in (
        "source_compiled_support_signals",
        "source_compiled_handoff_candidate",
        "source_evidence_packet",
    ):
        ref = preview[key]
        assert ref["path"], key
        assert ref["sha256"], key


# --- accepted support signal + valid anchor produces a preview row -----------


def test_accepted_signal_with_valid_anchor_produces_preview_row() -> None:
    preview = _preview(packet_with_anchors([anchor_row()]), memo())
    assert {r["ticker"] for r in preview["preview_actionable_rows"]} == {"QQQ"}
    assert preview["preview_positive_delta_research_supported"] == ["QQQ"]
    row = preview["preview_actionable_rows"][0]
    assert row["source_anchor_id"] == "AI_CAPEX_2026H2"
    assert row["anchor_type"] == "structural_theme"
    assert row["structural_theme_refs"] == ["AI_CAPEX_2026H2"]
    assert row["event_id_refs"] == []
    assert row["primary_anchor_event_id"] is None
    assert row["primary_anchor_ref"] == "AI_CAPEX_2026H2"
    assert row["primary_anchor_date_et"] == "2026-06-15"
    assert row["thesis_12m_plus_supported_preview"] is True
    assert row["actionability_status_preview"] == ACTIONABILITY_STATUS_PREVIEW
    # Every actionable row remains explicitly non-authorization.
    assert row["not_authorization"] is True
    assert preview["rejected_preview_rows"] == []
    assert GLOBAL_NO_ACCEPTED_SUPPORT_SIGNALS not in preview["global_blockers"]


def test_scheduled_event_anchor_populates_event_id_refs() -> None:
    anchor = anchor_row(
        anchor_id="FOMC_2026_07",
        anchor_type="scheduled_macro_event",
        anchor_date_et="2026-07-29",
    )
    m = memo()
    m["ticker_relative_view"][0]["anchor_id_refs"] = ["FOMC_2026_07"]
    preview = _preview(packet_with_anchors([anchor]), m)
    row = preview["preview_actionable_rows"][0]
    assert row["anchor_type"] == "scheduled_macro_event"
    assert row["event_id_refs"] == ["FOMC_2026_07"]
    assert row["structural_theme_refs"] == []
    assert row["primary_anchor_event_id"] == "FOMC_2026_07"
    assert row["primary_anchor_date_et"] == "2026-07-29"


# --- no accepted support signals ---------------------------------------------


def test_no_accepted_support_signals_empty_preview_with_blocker() -> None:
    # memo() with no anchors present in the packet → nothing can be accepted.
    preview = _preview(evidence_packet(), memo())
    assert preview["preview_actionable_rows"] == []
    assert preview["preview_positive_delta_research_supported"] == []
    assert GLOBAL_NO_ACCEPTED_SUPPORT_SIGNALS in preview["global_blockers"]


def test_evidence_only_no_memo_empty_preview_with_blocker() -> None:
    preview = _preview(evidence_packet(), None, mode="evidence_only")
    assert preview["preview_actionable_rows"] == []
    assert GLOBAL_NO_ACCEPTED_SUPPORT_SIGNALS in preview["global_blockers"]


# --- extended ETF rejected ---------------------------------------------------


def test_extended_etf_rejected_in_preview() -> None:
    anchor = anchor_row(anchor_id="GRID_THEME", applicable_tickers=["GRID"])
    m = memo()
    m["ticker_relative_view"] = [
        {"ticker": "GRID", "stance": "prefer", "rationale_12m_plus": "grid", "anchor_id_refs": ["GRID_THEME"]}
    ]
    preview = _preview(packet_with_anchors([anchor]), m)
    assert preview["preview_actionable_rows"] == []
    rejected = _rejected_by_ticker(preview)
    assert "GRID" in rejected
    assert PREVIEW_EXTENDED_ETF_NOT_ALLOWED in rejected["GRID"]["preview_rejection_reasons"]


# --- weekly cap enforced ------------------------------------------------------


def test_max_new_tickers_per_week_cap_enforced() -> None:
    anchors = [
        anchor_row(anchor_id="A_QQQ", applicable_tickers=["QQQ"]),
        anchor_row(anchor_id="A_VOO", applicable_tickers=["VOO"]),
    ]
    m = memo()
    m["ticker_relative_view"] = [
        {"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "q", "anchor_id_refs": ["A_QQQ"]},
        {"ticker": "VOO", "stance": "prefer", "rationale_12m_plus": "v", "anchor_id_refs": ["A_VOO"]},
    ]
    preview = _preview(packet_with_anchors(anchors, base_cap=1), m)
    # Cap of 1: the first accepted candidate is previewed, the second is capped.
    assert [r["ticker"] for r in preview["preview_actionable_rows"]] == ["QQQ"]
    rejected = _rejected_by_ticker(preview)
    assert "VOO" in rejected
    assert PREVIEW_LIMIT_MAX_NEW_TICKERS_EXCEEDED in rejected["VOO"]["preview_rejection_reasons"]


def test_base_cap_zero_blocks_all_accepted_rows() -> None:
    # Even with an accepted signal, a base cap of 0 (the current production default)
    # yields no preview rows — an honest observation, not authorization.
    preview = _preview(packet_with_anchors([anchor_row()], base_cap=0), memo())
    assert preview["preview_actionable_rows"] == []
    assert GLOBAL_BASE_NEW_TICKER_CAP_ZERO in preview["global_blockers"]
    # The accepted signal still exists upstream, so the no-accepted blocker is absent.
    assert GLOBAL_NO_ACCEPTED_SUPPORT_SIGNALS not in preview["global_blockers"]
    rejected = _rejected_by_ticker(preview)
    assert PREVIEW_LIMIT_MAX_NEW_TICKERS_EXCEEDED in rejected["QQQ"]["preview_rejection_reasons"]


def test_missing_cap_config_fails_closed_to_zero() -> None:
    preview = _preview(packet_with_anchors([anchor_row()], base_cap=None), memo())
    assert preview["base_new_ticker_cap_applied"] == 0
    assert preview["preview_actionable_rows"] == []


# --- stale / missing anchor rejected -----------------------------------------


def test_stale_anchor_rejected_in_preview() -> None:
    stale = anchor_row(valid=True, stale=True, usable=False)
    preview = _preview(packet_with_anchors([stale]), memo())
    assert preview["preview_actionable_rows"] == []
    rejected = _rejected_by_ticker(preview)
    assert PREVIEW_MISSING_ANCHOR in rejected["QQQ"]["preview_rejection_reasons"]


def test_missing_referenced_anchor_rejected_in_preview() -> None:
    # memo references an anchor id that is not in the packet.
    m = memo()
    m["ticker_relative_view"][0]["anchor_id_refs"] = ["DOES_NOT_EXIST"]
    preview = _preview(packet_with_anchors([anchor_row()]), m)
    assert preview["preview_actionable_rows"] == []
    rejected = _rejected_by_ticker(preview)
    assert PREVIEW_MISSING_ANCHOR in rejected["QQQ"]["preview_rejection_reasons"]


def test_scheduled_event_missing_date_rejected_in_preview() -> None:
    # A scheduled-event anchor with no date is ACCEPTED by the support extractor
    # (it does not require a date) but the preview's actionable-row contract does.
    anchor = anchor_row(anchor_id="FOMC", anchor_type="scheduled_macro_event", anchor_date_et=None)
    m = memo()
    m["ticker_relative_view"][0]["anchor_id_refs"] = ["FOMC"]
    signals = build_compiled_support_signals(
        evidence_packet=packet_with_anchors([anchor]),
        analyst_memo=m,
        compilation_mode=_MODE_EVIDENCE_PLUS_MEMO,
    )
    assert {s["ticker"] for s in signals["accepted_support_signals"]} == {"QQQ"}
    preview = _preview(packet_with_anchors([anchor]), m)
    assert preview["preview_actionable_rows"] == []
    rejected = _rejected_by_ticker(preview)
    assert PREVIEW_MISSING_PRIMARY_ANCHOR_DATE in rejected["QQQ"]["preview_rejection_reasons"]


# --- avoid_or_deprioritize rejected ------------------------------------------


def test_avoid_or_deprioritize_rejected_in_preview() -> None:
    m = memo(avoid_or_deprioritize=["QQQ"])
    preview = _preview(packet_with_anchors([anchor_row()]), m)
    assert preview["preview_actionable_rows"] == []
    rejected = _rejected_by_ticker(preview)
    assert PREVIEW_LISTED_IN_AVOID in rejected["QQQ"]["preview_rejection_reasons"]


# --- low confidence rejected -------------------------------------------------


def test_low_confidence_rejected_in_preview() -> None:
    anchor = anchor_row(confidence_floor="low")
    m = memo(confidence="low")
    preview = _preview(packet_with_anchors([anchor]), m)
    assert preview["preview_actionable_rows"] == []
    rejected = _rejected_by_ticker(preview)
    assert PREVIEW_LOW_CONFIDENCE in rejected["QQQ"]["preview_rejection_reasons"]


# --- missing source_notes rejected -------------------------------------------


def test_missing_source_notes_rejected_in_preview() -> None:
    m = memo(source_notes=[])
    preview = _preview(packet_with_anchors([anchor_row()]), m)
    assert preview["preview_actionable_rows"] == []
    rejected = _rejected_by_ticker(preview)
    assert PREVIEW_MISSING_SOURCE_NOTES in rejected["QQQ"]["preview_rejection_reasons"]
    # The granular support-signal reason is retained for transparency.
    assert "missing_source_notes" in rejected["QQQ"]["source_rejection_reasons"]


# --- defensive: malformed / empty inputs never raise -------------------------


def test_builder_never_raises_on_empty_inputs() -> None:
    preview = build_actionable_handoff_preview(
        evidence_packet=None,
        analyst_memo=None,
        compiled_support_signals=None,
    )
    assert preview["schema_version"] == SCHEMA_VERSION
    assert preview["preview_actionable_rows"] == []
    assert GLOBAL_NO_ACCEPTED_SUPPORT_SIGNALS in preview["global_blockers"]
    assert preview["not_authorization"] is True


# --- regression: unquoted YAML dates flow through to a preview row -----------


def test_unquoted_yaml_anchor_dates_produce_preview_row(tmp_path: Path) -> None:
    # Regression (R2E.5a-date-normalization): an unquoted anchor_date_et in
    # research_anchors.yaml used to decode to a datetime.date and get dropped to
    # null by the parser, which fail-closed the preview row via
    # PREVIEW_MISSING_PRIMARY_ANCHOR_DATE. It must now flow through end-to-end.
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
        anchors_path, allowed_universe=["QQQ", "VOO", "SMH"], today="2026-06-30"
    )
    assert anchors_summary["available"] is True
    assert anchors_summary["valid"] is True

    packet = evidence_packet()
    packet["research_anchors"] = anchors_summary
    packet["active_anchor_registry"] = active_anchor_registry_from_research_anchors_summary(
        anchors_summary
    )
    m = memo()
    preview = _preview(packet, m)

    assert preview["preview_actionable_rows"] != []
    assert {r["ticker"] for r in preview["preview_actionable_rows"]} == {"QQQ"}
    row = preview["preview_actionable_rows"][0]
    assert row["primary_anchor_date_et"] == "2026-06-15"
    assert row["actionability_status_preview"] == ACTIONABILITY_STATUS_PREVIEW
    assert preview["rejected_preview_rows"] == []
