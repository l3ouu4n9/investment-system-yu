"""Unit tests for R2E.3 deterministic support-signal extraction (report-only).

The extractor surfaces which analyst-memo opinions *would* be buy-support
candidates for a *future* actionable path, plus the exact deterministic reason
each is currently rejected. It NEVER authorizes a trade: in R2E.3 no candidate is
accepted (no deterministic anchor source exists) and every signal is
non-actionable by construction.
"""

from __future__ import annotations

from typing import Any

from investment_orchestrator.research.active_research_anchor_registry import (
    SCHEMA_VERSION as ACTIVE_REGISTRY_SCHEMA_VERSION,
    active_anchor_registry_from_research_anchors_summary,
)
from investment_orchestrator.research.support_signals import (
    ANCHOR_SOURCE_NONE,
    REASON_ANALYST_MEMO_ABSENT,
    REASON_ANALYST_MEMO_INVALID,
    REASON_ANCHOR_CONFIDENCE_FLOOR_NOT_MET,
    REASON_ANCHOR_NOT_APPLICABLE,
    REASON_ANCHOR_SOURCE_TYPE_NOT_ALLOWED,
    REASON_ANCHOR_TYPE_NOT_ALLOWED,
    REASON_BLOCKING_DATA_GAP,
    REASON_EXTENDED_ETF_NOT_ALLOWED,
    REASON_LISTED_IN_AVOID,
    REASON_MEMO_CONFIDENCE_LOW,
    REASON_MISSING_ANCHOR_ID_REFS,
    REASON_MISSING_RATIONALE,
    REASON_MISSING_SOURCE_NOTES,
    REASON_MISSING_VALID_ANCHOR_SOURCE,
    REASON_OUT_OF_UNIVERSE,
    REASON_REFERENCED_ANCHOR_NOT_FOUND,
    REASON_REFERENCED_ANCHOR_STALE,
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


def test_no_anchor_grounding_means_accepted_empty() -> None:
    # A perfect prefer+rationale+source_notes candidate is NOT accepted without a
    # referenced valid anchor (the memo() helper carries no anchor_id_refs).
    art = build_compiled_support_signals(
        evidence_packet=evidence_packet(), analyst_memo=memo(), compilation_mode=_MODE_EVIDENCE_PLUS_MEMO
    )
    assert art["accepted_support_signals"] == []
    assert art["not_authorization"] is True
    assert art["permission_effect"] == "none"
    # No usable anchor in the packet → run-level anchor blocker present.
    assert REASON_MISSING_VALID_ANCHOR_SOURCE in art["global_blockers"]


# --- valid prefer candidate without anchor refs: qualitative-support-only -----


def test_valid_prefer_candidate_without_refs_is_qualitative_only() -> None:
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
    # No anchor_id_refs → missing_anchor_id_refs + the umbrella missing_valid_anchor_source.
    assert REASON_MISSING_ANCHOR_ID_REFS in qqq["rejection_reasons"]
    assert REASON_MISSING_VALID_ANCHOR_SOURCE in qqq["rejection_reasons"]
    # Passed every qualitative gate → qualitative-support-only (never accepted).
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


# --- R2E.5a-2: anchor-grounded acceptance (report-only, NOT authorization) ----


def anchor_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": "AI_CAPEX_2026H2",
        "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"],
        "source_type": "operator",
        "confidence_floor": "medium",
        "valid": True,
        "stale": False,
        "usable": True,
    }
    base.update(overrides)
    return base


def _summary(anchors: list[dict[str, Any]], *, errors: list[str] | None = None, valid: bool = True) -> dict[str, Any]:
    return {
        "available": True,
        "valid": valid and not errors,
        "schema_version": "research_anchors_v1",
        "anchors": anchors,
        "errors": errors or [],
    }


def packet_with_anchors(anchors: list[dict[str, Any]], *, errors: list[str] | None = None) -> dict[str, Any]:
    """Build a packet that embeds the active_anchor_registry (R2G-3 source of truth).

    ``research_anchors`` is kept for diagnostics only; support_signals grounds on the
    embedded registry. ``errors`` simulate a file-level integrity failure (the
    registry then reports ``registry_valid: false`` and support_signals fails closed).
    """
    p = evidence_packet()
    summary = _summary(anchors, errors=errors)
    p["research_anchors"] = summary
    p["active_anchor_registry"] = active_anchor_registry_from_research_anchors_summary(summary)
    return p


def memo_with_refs(refs: tuple[str, ...] = ("AI_CAPEX_2026H2",), **overrides: Any) -> dict[str, Any]:
    m = memo()
    m["ticker_relative_view"] = [
        {"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "anchor thesis", "anchor_id_refs": list(refs)}
    ]
    m.update(overrides)
    return m


def _accept(packet: dict[str, Any], m: dict[str, Any]):
    return build_compiled_support_signals(
        evidence_packet=packet, analyst_memo=m, compilation_mode=_MODE_EVIDENCE_PLUS_MEMO
    )


def test_valid_anchor_ref_accepts_but_is_not_authorization() -> None:
    art = _accept(packet_with_anchors([anchor_row()]), memo_with_refs(confidence="high"))
    assert {s["ticker"] for s in art["accepted_support_signals"]} == {"QQQ"}
    accepted = art["accepted_support_signals"][0]
    assert accepted["anchor_id"] == "AI_CAPEX_2026H2"
    assert accepted["anchor_type"] == "structural_theme"
    assert accepted["not_authorization"] is True
    # Report-only invariants hold even with an accepted signal.
    assert art["permission_effect"] == "none"
    assert art["not_authorization"] is True
    assert art["anchor_source_available"] is True
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert qqq["has_valid_anchor_source"] is True
    assert qqq["matched_anchor_id"] == "AI_CAPEX_2026H2"
    assert qqq["accepted_for_future_actionability"] is True
    assert qqq["rejection_reasons"] == []
    # Run-level anchor blocker is absent once a usable anchor exists.
    assert REASON_MISSING_VALID_ANCHOR_SOURCE not in art["global_blockers"]


def test_confidence_meets_floor_medium_accepts() -> None:
    art = _accept(packet_with_anchors([anchor_row(confidence_floor="medium")]), memo_with_refs(confidence="medium"))
    assert {s["ticker"] for s in art["accepted_support_signals"]} == {"QQQ"}


def test_missing_anchor_refs_rejected_not_accepted() -> None:
    art = _accept(packet_with_anchors([anchor_row()]), memo_with_refs(refs=()))
    assert art["accepted_support_signals"] == []
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_MISSING_ANCHOR_ID_REFS in qqq["rejection_reasons"]
    assert {s["ticker"] for s in art["qualitative_support_only"]} == {"QQQ"}


def test_referenced_anchor_not_found_rejected() -> None:
    art = _accept(packet_with_anchors([anchor_row()]), memo_with_refs(refs=("NOPE",)))
    assert art["accepted_support_signals"] == []
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_REFERENCED_ANCHOR_NOT_FOUND in qqq["rejection_reasons"]


def test_invented_anchor_id_not_accepted() -> None:
    # An LLM-invented anchor id (absent from the deterministic packet) never accepts.
    art = _accept(packet_with_anchors([anchor_row()]), memo_with_refs(refs=("FABRICATED_2027",)))
    assert art["accepted_support_signals"] == []


def test_stale_anchor_rejected() -> None:
    art = _accept(packet_with_anchors([anchor_row(stale=True, usable=False)]), memo_with_refs())
    assert art["accepted_support_signals"] == []
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_REFERENCED_ANCHOR_STALE in qqq["rejection_reasons"]


def test_anchor_not_applicable_to_ticker_rejected() -> None:
    art = _accept(packet_with_anchors([anchor_row(applicable_tickers=["SMH"])]), memo_with_refs())
    assert art["accepted_support_signals"] == []
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_ANCHOR_NOT_APPLICABLE in qqq["rejection_reasons"]


def test_confidence_below_anchor_floor_rejected() -> None:
    art = _accept(packet_with_anchors([anchor_row(confidence_floor="high")]), memo_with_refs(confidence="medium"))
    assert art["accepted_support_signals"] == []
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_ANCHOR_CONFIDENCE_FLOOR_NOT_MET in qqq["rejection_reasons"]


def test_anchor_source_type_not_operator_rejected() -> None:
    art = _accept(packet_with_anchors([anchor_row(source_type="deterministic_feed")]), memo_with_refs())
    assert art["accepted_support_signals"] == []
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_ANCHOR_SOURCE_TYPE_NOT_ALLOWED in qqq["rejection_reasons"]


def test_anchor_type_not_allowed_rejected() -> None:
    art = _accept(packet_with_anchors([anchor_row(anchor_type="hot_tip")]), memo_with_refs())
    assert art["accepted_support_signals"] == []
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_ANCHOR_TYPE_NOT_ALLOWED in qqq["rejection_reasons"]


def test_avoid_veto_still_rejects_even_with_valid_anchor() -> None:
    art = _accept(packet_with_anchors([anchor_row()]), memo_with_refs(avoid_or_deprioritize=["QQQ"]))
    assert art["accepted_support_signals"] == []
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_LISTED_IN_AVOID in qqq["rejection_reasons"]
    # A non-anchor rejection ⇒ rejected, not qualitative-support-only.
    assert art["qualitative_support_only"] == []


def test_low_confidence_still_rejects_even_with_valid_anchor() -> None:
    art = _accept(packet_with_anchors([anchor_row(confidence_floor="low")]), memo_with_refs(confidence="low"))
    assert art["accepted_support_signals"] == []
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_MEMO_CONFIDENCE_LOW in qqq["rejection_reasons"]


def test_source_notes_alone_cannot_create_anchor() -> None:
    # Anchor exists in the packet and is cited only in source_notes text, not in
    # anchor_id_refs → not accepted (source_notes is never an anchor creator/ref).
    m = memo_with_refs(refs=())
    m["source_notes"] = [{"claim": "see AI_CAPEX_2026H2", "source": "op", "source_quality": "official"}]
    art = _accept(packet_with_anchors([anchor_row()]), m)
    assert art["accepted_support_signals"] == []


def test_accepted_signal_does_not_make_compiled_handoff_actionable() -> None:
    from investment_orchestrator.research.handoff_compiler import compile_research_handoff

    packet = packet_with_anchors([anchor_row()])
    m = memo_with_refs(confidence="high")
    art = _accept(packet, m)
    assert art["accepted_support_signals"]  # non-empty acceptance
    # The compiler ignores support signals entirely: still non-actionable.
    candidate = compile_research_handoff(packet, m, strategy_settings={"benchmark": "QQQ"})
    assert candidate["strategy_a_research_handoff"]["positive_delta_research_supported"] == []
    assert all(r["actionability_status"] != "actionable_this_run" for r in candidate["buy_universe_scorecard"])
    assert candidate["buy_universe_scorecard"][0]["primary_anchor_event_id"] is None


# --- R2G-3: grounding is consumed from evidence_packet.active_anchor_registry -----


def _consumable_registry(active: list[dict[str, Any]], inactive: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A hand-built, consumable active-registry section (report-only markers + valid)."""
    return {
        "schema_version": ACTIVE_REGISTRY_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "registry_valid": True,
        "active_anchors": active,
        "inactive_anchors": inactive or [],
    }


def _registry_active_anchor(**overrides: Any) -> dict[str, Any]:
    base = {
        "anchor_id": "AI_CAPEX_2026H2",
        "anchor_type": "structural_theme",
        "source_type": "operator",
        "applicable_tickers": ["QQQ"],
        "confidence_floor": "medium",
        "status": "active",
        "validation": {"valid": True, "stale": False, "usable": True, "problems": []},
    }
    base.update(overrides)
    return base


def test_r2g3_happy_path_grounds_from_registry() -> None:
    """Valid embedded registry: grounding accepts exactly as before."""
    art = _accept(packet_with_anchors([anchor_row()]), memo_with_refs(confidence="high"))
    assert {s["ticker"] for s in art["accepted_support_signals"]} == {"QQQ"}
    assert art["anchor_source_available"] is True
    assert REASON_MISSING_VALID_ANCHOR_SOURCE not in art["global_blockers"]


def test_r2g3_grounds_from_registry_even_when_legacy_research_anchors_absent() -> None:
    """Registry is the source of truth: grounding works from the embedded registry
    even when the legacy research_anchors section is missing/empty."""
    packet = evidence_packet()  # no research_anchors key
    packet["active_anchor_registry"] = _consumable_registry([_registry_active_anchor()])
    art = _accept(packet, memo_with_refs(confidence="high"))
    assert {s["ticker"] for s in art["accepted_support_signals"]} == {"QQQ"}
    assert art["anchor_source_available"] is True


def test_r2g3_missing_registry_fails_closed() -> None:
    """No active_anchor_registry at all -> no usable anchors, fail closed."""
    packet = evidence_packet()  # neither research_anchors nor active_anchor_registry
    art = _accept(packet, memo_with_refs())
    assert art["accepted_support_signals"] == []
    assert art["anchor_source_available"] is False
    assert REASON_MISSING_VALID_ANCHOR_SOURCE in art["global_blockers"]


def test_r2g3_legacy_research_anchors_present_but_registry_missing_fails_closed() -> None:
    """A valid legacy research_anchors section is NOT sufficient — support grounding
    now depends on the embedded registry, which is absent here."""
    packet = evidence_packet()
    packet["research_anchors"] = _summary([anchor_row()])  # legacy present + valid
    # deliberately NO active_anchor_registry
    art = _accept(packet, memo_with_refs(confidence="high"))
    assert art["accepted_support_signals"] == []
    assert art["anchor_source_available"] is False


def test_r2g3_malformed_registry_fails_closed_no_crash() -> None:
    packet = evidence_packet()
    packet["active_anchor_registry"] = {"active_anchors": "not-a-list", "registry_valid": True}
    art = _accept(packet, memo_with_refs(confidence="high"))
    assert art["accepted_support_signals"] == []
    assert art["anchor_source_available"] is False


def test_r2g3_registry_wrong_schema_fails_closed() -> None:
    packet = evidence_packet()
    reg = _consumable_registry([_registry_active_anchor()])
    reg["schema_version"] = "some_other_schema_v9"
    packet["active_anchor_registry"] = reg
    art = _accept(packet, memo_with_refs(confidence="high"))
    assert art["accepted_support_signals"] == []
    assert art["anchor_source_available"] is False


def test_r2g3_registry_valid_false_fails_closed() -> None:
    """registry_valid:false (incl. every file-level integrity failure) -> fail closed,
    even if active_anchors were somehow populated."""
    packet = evidence_packet()
    reg = _consumable_registry([_registry_active_anchor()])
    reg["registry_valid"] = False
    packet["active_anchor_registry"] = reg
    art = _accept(packet, memo_with_refs(confidence="high"))
    assert art["accepted_support_signals"] == []
    assert art["anchor_source_available"] is False


def test_r2g3_registry_missing_report_only_marker_fails_closed() -> None:
    packet = evidence_packet()
    reg = _consumable_registry([_registry_active_anchor()])
    reg["report_only"] = False  # not a report-only registry -> not consumable
    packet["active_anchor_registry"] = reg
    art = _accept(packet, memo_with_refs(confidence="high"))
    assert art["accepted_support_signals"] == []


def test_r2g3_file_level_failure_tightens_rejects_where_old_accepted() -> None:
    """Headline tightening: a structurally-valid anchor in a file with a top-level
    integrity failure (is_llm_generated:true) flips the registry to registry_valid:false
    -> now REJECTED, where the legacy per-anchor view would have accepted it."""
    packet = packet_with_anchors(
        [anchor_row()],
        errors=["is_llm_generated must be exactly false (anchors are operator-authored)."],
    )
    # sanity: the embedded registry indeed failed closed
    assert packet["active_anchor_registry"]["registry_valid"] is False
    art = _accept(packet, memo_with_refs(confidence="high"))
    assert art["accepted_support_signals"] == [], "file-level failure must not ground"
    assert art["anchor_source_available"] is False
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_REFERENCED_ANCHOR_NOT_FOUND in qqq["rejection_reasons"]
    assert REASON_MISSING_VALID_ANCHOR_SOURCE in art["global_blockers"]


def test_r2g3_duplicate_id_file_level_failure_tightens() -> None:
    packet = packet_with_anchors(
        [anchor_row(), anchor_row()],
        errors=["duplicate anchor_id: 'AI_CAPEX_2026H2'."],
    )
    art = _accept(packet, memo_with_refs(confidence="high"))
    assert art["accepted_support_signals"] == []
    assert art["anchor_source_available"] is False


def test_r2g3_more_permissive_registry_does_not_broaden() -> None:
    """Defense in depth: even if a registry marks an anchor ACTIVE that should not
    ground (e.g. a non-operator source), support_signals re-applies its acceptance
    gates and refuses to broaden."""
    packet = evidence_packet()
    packet["active_anchor_registry"] = _consumable_registry(
        [_registry_active_anchor(source_type="deterministic_feed")]
    )
    art = _accept(packet, memo_with_refs(confidence="high"))
    assert art["accepted_support_signals"] == []
    qqq = _by_ticker(art["candidate_ticker_signals"])["QQQ"]
    assert REASON_ANCHOR_SOURCE_TYPE_NOT_ALLOWED in qqq["rejection_reasons"]


def test_r2g3_never_broadens_across_corpus() -> None:
    """Corpus regression: registry-backed grounding never ACCEPTS a ticker the legacy
    per-anchor view would have rejected (happy accepts in both; everything else in
    neither)."""
    cases = [
        ("happy", [anchor_row()], None, {"QQQ"}),
        ("stale", [anchor_row(stale=True, usable=False)], None, set()),
        ("out_of_universe_anchor", [anchor_row(applicable_tickers=["SMH"])], None, set()),
        ("bad_source_type", [anchor_row(source_type="deterministic_feed")], None, set()),
        ("bad_anchor_type", [anchor_row(anchor_type="hot_tip")], None, set()),
        ("file_level_llm", [anchor_row()], ["is_llm_generated must be exactly false ..."], set()),
        ("file_level_forbidden", [anchor_row()], ["forbidden budget/sizing key present ..."], set()),
    ]
    for label, anchors, errors, expected in cases:
        art = _accept(packet_with_anchors(anchors, errors=errors), memo_with_refs(confidence="high"))
        got = {s["ticker"] for s in art["accepted_support_signals"]}
        assert got == expected, f"{label}: accepted {got}, expected {expected}"


def test_r2g3_registry_does_not_reference_permission_or_order_tokens() -> None:
    """The switch introduces no permission/order tokens into the accepted output."""
    art = _accept(packet_with_anchors([anchor_row()]), memo_with_refs(confidence="high"))
    import json as _json

    blob = _json.dumps(art)
    assert "NEW_BUY" not in blob
    assert "ORDER_COMPILATION" not in blob
    assert art["permission_effect"] == "none"
    assert art["not_authorization"] is True
    for accepted in art["accepted_support_signals"]:
        assert accepted["not_authorization"] is True
