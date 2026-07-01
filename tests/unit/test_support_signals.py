"""Unit tests for R2E.3 deterministic support-signal extraction (report-only).

The extractor surfaces which analyst-memo opinions *would* be buy-support
candidates for a *future* actionable path, plus the exact deterministic reason
each is currently rejected. It NEVER authorizes a trade: in R2E.3 no candidate is
accepted (no deterministic anchor source exists) and every signal is
non-actionable by construction.
"""

from __future__ import annotations

from typing import Any

from investment_orchestrator.research.support_signals import (
    ANCHOR_SOURCE_NONE,
    REASON_ANALYST_MEMO_ABSENT,
    REASON_ANALYST_MEMO_INVALID,
    REASON_BLOCKING_DATA_GAP,
    REASON_EXTENDED_ETF_NOT_ALLOWED,
    REASON_LISTED_IN_AVOID,
    REASON_MEMO_CONFIDENCE_LOW,
    REASON_MISSING_RATIONALE,
    REASON_MISSING_SOURCE_NOTES,
    REASON_MISSING_VALID_ANCHOR_SOURCE,
    REASON_OUT_OF_UNIVERSE,
    REASON_STANCE_NOT_PREFER,
    SCHEMA_VERSION,
    build_compiled_support_signals,
)

_MODE_EVIDENCE_ONLY = "evidence_only"
_MODE_EVIDENCE_PLUS_MEMO = "evidence_plus_memo"
_MODE_INVALID_MEMO_IGNORED = "invalid_memo_ignored"


def evidence_packet(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "evidence_packet_v1",
        "is_llm_generated": False,
        "universe": {
            "core_universe": ["QQQ", "VOO"],
            "satellite_universe": ["SMH"],
            "approved_extended_etf": ["GRID"],
            "allowed_buy_tickers": ["QQQ", "VOO", "SMH"],
        },
    }
    base.update(overrides)
    return base


def memo(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "as_of_date": "2026-06-28",
        "regime_view": "constructive",
        "confidence": "medium",
        "ticker_relative_view": [
            {"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "core AI anchor"},
        ],
        "avoid_or_deprioritize": [],
        "data_gaps": [],
        "source_notes": [{"claim": "AI capex up", "source": "official", "source_quality": "official"}],
    }
    base.update(overrides)
    return base


def _by_ticker(signals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["ticker"]: s for s in signals}


# --- schema / non-authorization invariants -----------------------------------


def test_artifact_is_report_only_and_non_authoritative() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(), analyst_memo=memo(), compilation_mode=_MODE_EVIDENCE_PLUS_MEMO
    )
    assert art["schema_version"] == SCHEMA_VERSION
    assert art["is_llm_generated"] is False
    assert art["report_only"] is True
    assert art["permission_effect"] == "none"
    assert art["anchor_source_available"] is False
    assert art["actionable_signals_possible"] is False


def test_accepted_support_signals_always_empty_in_v1() -> None:
    # Even a perfect prefer+rationale+source_notes candidate is not accepted.
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(), analyst_memo=memo(), compilation_mode=_MODE_EVIDENCE_PLUS_MEMO
    )
    assert art["accepted_support_signals"] == []
    assert REASON_MISSING_VALID_ANCHOR_SOURCE in art["global_blockers"]


# --- valid prefer candidate: rejected only by missing anchor ------------------


def test_valid_prefer_candidate_rejected_only_by_missing_anchor() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(), analyst_memo=memo(), compilation_mode=_MODE_EVIDENCE_PLUS_MEMO
    )
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert qqq["stance"] == "prefer"
    assert qqq["rationale_present"] is True
    assert qqq["source_notes_present"] is True
    assert qqq["in_allowed_universe"] is True
    assert qqq["has_valid_anchor_source"] is False
    assert qqq["anchor_source_type"] == ANCHOR_SOURCE_NONE
    assert qqq["accepted_for_future_actionability"] is False
    # The ONLY rejection reason is the missing deterministic anchor source.
    assert qqq["rejection_reasons"] == [REASON_MISSING_VALID_ANCHOR_SOURCE]
    # Such a candidate is surfaced as qualitative-support-only (never accepted).
    assert {s["ticker"] for s in art["qualitative_support_only"]} == {"QQQ"}
    assert art["accepted_support_signals"] == []


# --- per-ticker rejection reasons --------------------------------------------


def test_low_confidence_rejects_support_signal() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(),
        analyst_memo=memo(confidence="low"),
        compilation_mode=_MODE_EVIDENCE_PLUS_MEMO,
    )
    assert REASON_MEMO_CONFIDENCE_LOW in art["global_blockers"]
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_MEMO_CONFIDENCE_LOW in qqq["rejection_reasons"]
    assert art["qualitative_support_only"] == []


def test_avoid_or_deprioritize_rejects_signal() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(),
        analyst_memo=memo(avoid_or_deprioritize=["QQQ"]),
        compilation_mode=_MODE_EVIDENCE_PLUS_MEMO,
    )
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert qqq["listed_in_avoid_or_deprioritize"] is True
    assert REASON_LISTED_IN_AVOID in qqq["rejection_reasons"]
    assert art["qualitative_support_only"] == []


def test_missing_rationale_rejects_signal() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(),
        analyst_memo=memo(ticker_relative_view=[{"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "  "}]),
        compilation_mode=_MODE_EVIDENCE_PLUS_MEMO,
    )
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert qqq["rationale_present"] is False
    assert REASON_MISSING_RATIONALE in qqq["rejection_reasons"]


def test_missing_source_notes_rejects_signal() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(),
        analyst_memo=memo(source_notes=[]),
        compilation_mode=_MODE_EVIDENCE_PLUS_MEMO,
    )
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert qqq["source_notes_present"] is False
    assert REASON_MISSING_SOURCE_NOTES in qqq["rejection_reasons"]


def test_non_prefer_stance_rejects_signal() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(),
        analyst_memo=memo(ticker_relative_view=[{"ticker": "SMH", "stance": "deprioritize", "rationale_12m_plus": "x"}]),
        compilation_mode=_MODE_EVIDENCE_PLUS_MEMO,
    )
    smh = _by_ticker(art["candidate_ticker_signals"])["SMH"]
    assert REASON_STANCE_NOT_PREFER in smh["rejection_reasons"]


def test_blocking_data_gap_rejects_signal() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(),
        analyst_memo=memo(data_gaps=["QQQ earnings date unknown"]),
        compilation_mode=_MODE_EVIDENCE_PLUS_MEMO,
    )
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert qqq["has_blocking_data_gap"] is True
    assert REASON_BLOCKING_DATA_GAP in qqq["rejection_reasons"]


def test_extended_etf_candidate_rejected_in_v1() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(),
        analyst_memo=memo(ticker_relative_view=[{"ticker": "GRID", "stance": "prefer", "rationale_12m_plus": "grid theme"}]),
        compilation_mode=_MODE_EVIDENCE_PLUS_MEMO,
    )
    grid = _by_ticker(art["candidate_ticker_signals"])["GRID"]
    assert REASON_EXTENDED_ETF_NOT_ALLOWED in grid["rejection_reasons"]
    # An approved-extended ticker is NOT flagged out_of_universe (it is recognized).
    assert REASON_OUT_OF_UNIVERSE not in grid["rejection_reasons"]
    assert art["qualitative_support_only"] == []


# --- defensive: out-of-universe / invalid / absent memo -----------------------


def test_out_of_universe_ticker_handled_defensively() -> None:
    # A real out-of-universe ticker fails memo validation upstream (mode is
    # invalid_memo_ignored), but the extractor still surfaces it defensively.
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(),
        analyst_memo=memo(ticker_relative_view=[{"ticker": "TSLA", "stance": "prefer", "rationale_12m_plus": "x"}]),
        compilation_mode=_MODE_INVALID_MEMO_IGNORED,
    )
    tsla = _by_ticker(art["candidate_ticker_signals"])["TSLA"]
    assert tsla["in_allowed_universe"] is False
    assert REASON_OUT_OF_UNIVERSE in tsla["rejection_reasons"]
    assert REASON_ANALYST_MEMO_INVALID in tsla["rejection_reasons"]
    assert art["qualitative_support_only"] == []


def test_absent_memo_produces_global_blocker() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(), analyst_memo=None, compilation_mode=_MODE_EVIDENCE_ONLY
    )
    assert art["analyst_memo_present"] is False
    assert art["analyst_memo_valid"] is False
    assert REASON_ANALYST_MEMO_ABSENT in art["global_blockers"]
    assert art["candidate_ticker_signals"] == []
    assert art["accepted_support_signals"] == []


def test_invalid_memo_produces_global_blocker() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(),
        analyst_memo=memo(confidence="adequate"),  # invalid confidence upstream
        compilation_mode=_MODE_INVALID_MEMO_IGNORED,
    )
    assert art["analyst_memo_present"] is True
    assert art["analyst_memo_valid"] is False
    assert REASON_ANALYST_MEMO_INVALID in art["global_blockers"]
    # No memo_confidence_low blocker: an invalid memo is never trusted for confidence.
    assert REASON_MEMO_CONFIDENCE_LOW not in art["global_blockers"]


def test_non_mapping_memo_is_defensive() -> None:
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(),
        analyst_memo=["not", "a", "memo"],  # type: ignore[arg-type]
        compilation_mode=_MODE_INVALID_MEMO_IGNORED,
    )
    assert art["analyst_memo_present"] is True
    assert art["analyst_memo_valid"] is False
    assert art["candidate_ticker_signals"] == []


def test_empty_packet_never_raises() -> None:
    art = build_compiled_support_signals(
        evidence_packet={}, analyst_memo=None, compilation_mode=_MODE_EVIDENCE_ONLY
    )
    assert art["candidate_ticker_signals"] == []
    assert REASON_MISSING_VALID_ANCHOR_SOURCE in art["global_blockers"]
