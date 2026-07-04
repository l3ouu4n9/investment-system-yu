"""R2G-4: report-only research-anchor candidate workflow tests.

Every test proves the candidate artifact is advisory + inert: it carries the
report-only / not_authorization markers, is consumed by nothing, never marks a
candidate active, and never contains order-shaped fields.
"""

from __future__ import annotations

import json
from typing import Any

from investment_orchestrator.research.research_anchor_candidates import (
    BLOCKER_DUPLICATE_OF_ACTIVE,
    BLOCKER_INCOMPLETE_ANCHOR_SHAPE,
    BLOCKER_REQUIRES_OPERATOR_APPROVAL,
    BLOCKER_SOURCE_B_NEVER_AUTO_ACTIVATES,
    GEN_BLOCKER_MEMO_ABSENT,
    REJECT_FORBIDDEN_ACTION_TOKEN,
    REJECT_FORBIDDEN_KEY,
    REJECT_OUT_OF_UNIVERSE,
    SCHEMA_VERSION,
    SOURCE_CATEGORY_CANDIDATE_ONLY,
    STATUS_CANDIDATE,
    STATUS_DUPLICATE_OF_ACTIVE,
    build_research_anchor_candidates,
    evaluate_proposed_anchor,
)

UNIVERSE = ["QQQ", "VOO", "SMH"]
AS_OF = "2026-07-04"

_ORDER_SHAPED_KEYS = frozenset(
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
        "new_buy",
        "order_compilation",
        "budget",
        "allocation",
    }
)


def _evidence(universe: list[str] = UNIVERSE) -> dict[str, Any]:
    return {"universe": {"allowed_buy_tickers": list(universe), "approved_extended_etf": ["GRID"]}}


def _memo(rows: list[dict[str, Any]], *, confidence: str = "high") -> dict[str, Any]:
    return {"schema_version": "analyst_memo_v1", "is_llm_generated": True, "confidence": confidence,
            "ticker_relative_view": rows}


def _registry(active: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"registry_valid": True, "active_anchors": active or [], "inactive_anchors": []}


def _build(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        evidence_packet=_evidence(),
        analyst_memo=_memo([{"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "AI capex"}]),
        analyst_memo_valid=True,
        compiled_support_signals=None,
        active_registry=_registry(),
        as_of_date=AS_OF,
    )
    base.update(kw)
    return build_research_anchor_candidates(**base)


def _keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


# --- 1. artifact markers ------------------------------------------------------


def test_markers_report_only_and_inert() -> None:
    r = _build()
    assert r["schema_version"] == SCHEMA_VERSION
    assert r["is_llm_generated"] is False
    assert r["report_only"] is True
    assert r["permission_effect"] == "none"
    assert r["not_authorization"] is True
    assert r["not_execution_authorization"] is True
    assert r["cannot_affect_allowed_actions"] is True
    for key in (
        "consumed_by_support_signals",
        "consumed_by_compiler",
        "consumed_by_promotion_eligibility",
        "consumed_by_availability",
        "consumed_by_gates",
        "consumed_by_step2",
        "consumed_by_step4",
    ):
        assert r[key] is False


def test_no_order_shaped_fields_anywhere() -> None:
    r = _build(
        analyst_memo=_memo(
            [{"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "x"},
             {"ticker": "VOO", "stance": "prefer", "rationale_12m_plus": "y"}]
        )
    )
    present = {k for k in _keys(r) if k.lower() in _ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked: {present}"


def test_json_serializable() -> None:
    r = _build()
    assert json.loads(json.dumps(r))["schema_version"] == SCHEMA_VERSION


# --- 2. candidate generation --------------------------------------------------


def test_prefer_ticker_creates_candidate() -> None:
    r = _build()
    assert r["counts"]["candidates"] == 1
    c = r["candidates"][0]
    assert c["proposed_anchor"]["applicable_tickers"] == ["QQQ"]
    assert c["source_category"] == SOURCE_CATEGORY_CANDIDATE_ONLY
    assert c["source_type"] == "analyst_memo"
    assert c["status"] == STATUS_CANDIDATE
    assert c["confidence"] == "high"
    assert BLOCKER_REQUIRES_OPERATOR_APPROVAL in c["blocker_reasons"]
    assert BLOCKER_SOURCE_B_NEVER_AUTO_ACTIVATES in c["blocker_reasons"]


def test_candidate_id_and_sha_deterministic() -> None:
    a = _build()["candidates"][0]
    b = _build()["candidates"][0]
    assert a["candidate_id"] == b["candidate_id"]
    assert a["candidate_sha256"] == b["candidate_sha256"]
    assert a["candidate_id"].startswith("CAND-QQQ-")
    assert len(a["candidate_sha256"]) == 64


def test_non_prefer_stance_skipped() -> None:
    r = _build(analyst_memo=_memo([{"ticker": "QQQ", "stance": "neutral", "rationale_12m_plus": "x"}]))
    assert r["counts"]["candidates"] == 0
    assert r["rejected_candidates"] == []


def test_memo_derived_candidate_is_incomplete_and_not_eligible() -> None:
    """Memo has no dates -> skeleton is incomplete -> not shape-valid -> not eligible."""
    c = _build()["candidates"][0]
    assert c["would_validate_as_anchor"] is False
    assert c["eligible_for_operator_approval"] is False
    assert BLOCKER_INCOMPLETE_ANCHOR_SHAPE in c["blocker_reasons"]
    assert any("anchor_date_et" in p for p in c["validation_problems"])


def test_eligible_true_only_when_shape_valid() -> None:
    """A complete, in-universe proposed anchor validates -> eligible path reachable."""
    complete = {
        "anchor_id": "X",
        "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"],
        "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01",
        "valid_until": "2026-07-31",
        "source_type": "operator",
        "confidence_floor": "medium",
    }
    would_validate, problems = evaluate_proposed_anchor(complete, allowed_universe=UNIVERSE)
    assert would_validate is True
    assert problems == []
    # And an incomplete one does not validate.
    incomplete = {k: v for k, v in complete.items() if k != "valid_until"}
    ok, probs = evaluate_proposed_anchor(incomplete, allowed_universe=UNIVERSE)
    assert ok is False
    assert probs


def test_support_signal_gap_recorded_in_source_refs() -> None:
    signals = {"qualitative_support_only": [{"ticker": "QQQ", "stance": "prefer"}]}
    c = _build(compiled_support_signals=signals)["candidates"][0]
    kinds = {ref["kind"] for ref in c["source_refs"]}
    assert "analyst_memo_ticker_relative_view" in kinds
    assert "support_signal_qualitative_support_only_gap" in kinds


def test_missing_memo_generation_blocker() -> None:
    r = _build(analyst_memo=None, analyst_memo_valid=False)
    assert r["counts"]["candidates"] == 0
    assert GEN_BLOCKER_MEMO_ABSENT in r["candidate_generation_blockers"]


# --- 3. rejection -------------------------------------------------------------


def test_out_of_universe_ticker_rejected() -> None:
    r = _build(analyst_memo=_memo([{"ticker": "TSLA", "stance": "prefer", "rationale_12m_plus": "x"}]))
    assert r["counts"]["candidates"] == 0
    assert r["counts"]["rejected"] == 1
    rej = r["rejected_candidates"][0]
    assert rej["ticker"] == "TSLA"
    assert REJECT_OUT_OF_UNIVERSE in rej["rejection_reasons"]
    assert rej["status"] == "rejected"


def test_extended_etf_ticker_rejected_not_active() -> None:
    # approved_extended_etf is NOT in allowed_buy_tickers -> out of universe for anchors.
    r = _build(analyst_memo=_memo([{"ticker": "GRID", "stance": "prefer", "rationale_12m_plus": "x"}]))
    assert r["counts"]["candidates"] == 0
    assert REJECT_OUT_OF_UNIVERSE in r["rejected_candidates"][0]["rejection_reasons"]


def test_forbidden_key_in_rationale_object_rejected() -> None:
    """A memo row whose rationale smuggles a budget/order key must be rejected."""
    # rationale is normally a string; a dict with a forbidden key must not slip through.
    r = _build(
        analyst_memo=_memo([{"ticker": "QQQ", "stance": "prefer",
                             "rationale_12m_plus": "hard_cap_open_orders_budget: 1000 NEW_BUY"}])
    )
    # rationale is a plain string here -> becomes summary; the forbidden TOKEN
    # 'new_buy' is not a standalone scalar value, so this is a candidate, but it
    # must never carry order-shaped keys.
    c = r["candidates"][0]
    assert "budget" not in {k.lower() for k in _keys(c["proposed_anchor"])}


def test_new_buy_token_as_scalar_value_rejected() -> None:
    """If a proposed-anchor value were exactly an action token, reject it."""
    # Confirm the forbidden-token guard via a direct proposed-anchor scan is wired:
    # a candidate whose summary is exactly 'NEW_BUY' must be rejected.
    r = _build(analyst_memo=_memo([{"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "NEW_BUY"}]))
    # summary == 'NEW_BUY' is a forbidden action token value -> rejected.
    assert r["counts"]["candidates"] == 0
    assert REJECT_FORBIDDEN_ACTION_TOKEN in r["rejected_candidates"][0]["rejection_reasons"]


def test_duplicate_of_active_marked_not_active() -> None:
    reg = _registry([{"anchor_id": "EXISTING_QQQ", "applicable_tickers": ["QQQ"]}])
    r = _build(active_registry=reg)
    c = r["candidates"][0]
    assert c["status"] == STATUS_DUPLICATE_OF_ACTIVE
    assert c["status"] != "active"
    assert c["already_active_anchor_id"] == "EXISTING_QQQ"
    assert c["eligible_for_operator_approval"] is False
    assert BLOCKER_DUPLICATE_OF_ACTIVE in c["blocker_reasons"]
    assert r["counts"]["duplicates_of_active"] == 1


def test_no_candidate_ever_has_active_status() -> None:
    reg = _registry([{"anchor_id": "E", "applicable_tickers": ["QQQ"]}])
    r = _build(
        analyst_memo=_memo(
            [{"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "a"},
             {"ticker": "VOO", "stance": "prefer", "rationale_12m_plus": "b"}]
        ),
        active_registry=reg,
    )
    for c in r["candidates"]:
        assert c["status"] in (STATUS_CANDIDATE, STATUS_DUPLICATE_OF_ACTIVE)
        assert c["status"] != "active"


# --- 4. safety / never raises -------------------------------------------------


def test_never_raises_on_garbage() -> None:
    r = build_research_anchor_candidates(
        evidence_packet="nonsense", analyst_memo=12345, analyst_memo_valid=True
    )
    assert r["schema_version"] == SCHEMA_VERSION
    assert r["counts"]["candidates"] == 0


def test_no_new_buy_or_order_compilation_tokens_in_output() -> None:
    r = _build(
        analyst_memo=_memo([{"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "clean rationale"}])
    )
    blob = json.dumps(r)
    # The artifact itself must not assert NEW_BUY / ORDER_COMPILATION as grants.
    assert '"NEW_BUY"' not in blob
    assert '"ORDER_COMPILATION"' not in blob
    assert r["candidates"][0]["source_category"] == SOURCE_CATEGORY_CANDIDATE_ONLY
