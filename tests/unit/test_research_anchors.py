"""Unit tests for the R2E.5a research-anchor parser / validator (report-only).

The parser is deterministic and never trusts its input. It surfaces valid /
stale / invalid anchors for a FUTURE actionable path; in this version anchors are
NOT consumed for support acceptance and never change permissions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from investment_orchestrator.research.research_anchors import (
    ANCHOR_TYPES,
    ANCHORS_MISSING_DATA_GAP,
    SCHEMA_VERSION,
    build_research_anchors_summary,
    load_research_anchors,
    summarize_research_anchors,
    validate_research_anchors,
)


ALLOWED = ["QQQ", "VOO", "VTI", "VT", "SMH", "IGV"]
TODAY = "2026-06-30"


def anchor(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": "AI_CAPEX_2026H2",
        "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ", "SMH"],
        "summary": "AI capex / semiconductor demand structural theme",
        "source_type": "operator",
        "source_note": "Operator-reviewed theme; not LLM-generated",
        "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01",
        "valid_until": "2026-07-15",
        "confidence_floor": "medium",
        "blocks_if_stale": True,
    }
    base.update(overrides)
    return base


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "as_of_date": "2026-06-30",
        "is_llm_generated": False,
        "anchors": [anchor()],
    }
    base.update(overrides)
    return base


def _validate(p: dict[str, Any]):
    return validate_research_anchors(p, allowed_universe=ALLOWED, today=TODAY)


# --- valid cases -------------------------------------------------------------


def test_valid_empty_anchors_passes() -> None:
    result = _validate(payload(anchors=[]))
    assert result.valid is True
    assert result.anchors == []


def test_valid_anchor_passes() -> None:
    result = _validate(payload())
    assert result.valid is True
    assert len(result.anchors) == 1
    a = result.anchors[0]
    assert a["valid"] is True
    assert a["stale"] is False
    assert a["usable"] is True
    assert a["applicable_tickers"] == ["QQQ", "SMH"]


def test_summary_counts_valid_fresh_anchor() -> None:
    summary = summarize_research_anchors(_validate(payload()), path="/x/research_anchors.yaml")
    assert summary["available"] is True
    assert summary["valid"] is True
    assert summary["anchor_count"] == 1
    assert summary["valid_anchor_count"] == 1
    assert summary["stale_anchor_count"] == 0
    assert summary["invalid_anchor_count"] == 0
    assert summary["consumed_for_support_acceptance"] is False
    assert summary["permission_effect"] == "none"


# --- structural rejections ---------------------------------------------------


def test_wrong_schema_version_fails() -> None:
    assert _validate(payload(schema_version="research_anchors_v2")).valid is False


def test_is_llm_generated_true_fails() -> None:
    result = _validate(payload(is_llm_generated=True))
    assert result.valid is False
    assert any("is_llm_generated" in e for e in result.errors)


def test_anchors_not_a_list_fails() -> None:
    result = _validate(payload(anchors={"not": "a list"}))
    assert result.valid is False
    assert any("anchors must be a list" in e for e in result.errors)


def test_duplicate_anchor_id_fails() -> None:
    result = _validate(payload(anchors=[anchor(), anchor()]))
    assert result.valid is False
    assert any("duplicate anchor_id" in e for e in result.errors)


def test_invalid_anchor_type_fails() -> None:
    result = _validate(payload(anchors=[anchor(anchor_type="hot_tip")]))
    assert result.valid is False
    assert any("anchor_type" in p for p in result.anchors[0]["problems"])


def test_invalid_source_type_fails() -> None:
    result = _validate(payload(anchors=[anchor(source_type="llm")]))
    assert result.valid is False
    assert any("source_type" in p for p in result.anchors[0]["problems"])


def test_invalid_confidence_floor_fails() -> None:
    result = _validate(payload(anchors=[anchor(confidence_floor="adequate")]))
    assert result.valid is False
    assert any("confidence_floor" in p for p in result.anchors[0]["problems"])


def test_invalid_date_fails() -> None:
    result = _validate(payload(anchors=[anchor(anchor_date_et="2026-13-40")]))
    assert result.valid is False
    assert any("anchor_date_et" in p for p in result.anchors[0]["problems"])


def test_missing_required_field_fails() -> None:
    bad = anchor()
    del bad["valid_until"]
    result = _validate(payload(anchors=[bad]))
    assert result.valid is False
    assert any("valid_until" in p for p in result.anchors[0]["problems"])


# --- universe scoping (v1 base-only) -----------------------------------------


def test_applicable_ticker_outside_universe_fails() -> None:
    result = _validate(payload(anchors=[anchor(applicable_tickers=["TSLA"])]))
    assert result.valid is False
    assert any("outside the deterministic allowed universe" in p for p in result.anchors[0]["problems"])


def test_extended_only_ticker_rejected_in_v1() -> None:
    # GRID is an approved-extended ETF, NOT in the base allowed universe.
    result = _validate(payload(anchors=[anchor(applicable_tickers=["GRID"])]))
    assert result.valid is False
    assert any("extended ETFs are not admissible" in p for p in result.anchors[0]["problems"])


def test_empty_applicable_tickers_fails() -> None:
    result = _validate(payload(anchors=[anchor(applicable_tickers=[])]))
    assert result.valid is False
    assert any("applicable_tickers must be a non-empty list" in p for p in result.anchors[0]["problems"])


# --- staleness ---------------------------------------------------------------


def test_stale_anchor_flagged_not_usable() -> None:
    result = _validate(payload(anchors=[anchor(valid_until="2026-06-01")]))
    # Structurally valid, but stale (valid_until < today) → not usable.
    a = result.anchors[0]
    assert a["valid"] is True
    assert a["stale"] is True
    assert a["usable"] is False
    summary = summarize_research_anchors(result)
    assert summary["stale_anchor_count"] == 1
    assert summary["valid_anchor_count"] == 0


def test_stale_but_blocks_if_stale_false_is_usable() -> None:
    result = _validate(payload(anchors=[anchor(valid_until="2026-06-01", blocks_if_stale=False)]))
    a = result.anchors[0]
    assert a["stale"] is True
    assert a["usable"] is True  # operator explicitly allows a stale anchor


# --- forbidden keys / tokens (recursive) -------------------------------------


def test_forbidden_budget_key_fails_recursively() -> None:
    result = _validate(payload(anchors=[anchor(target_new_buy_budget=100)]))
    assert result.valid is False
    assert any("budget/sizing key" in e for e in result.errors)


def test_forbidden_action_key_fails_recursively() -> None:
    result = _validate(payload(anchors=[anchor(order_intent="buy")]))
    assert result.valid is False
    assert any("execution-authority/order-intent key" in e for e in result.errors)


def test_forbidden_action_token_value_fails() -> None:
    result = _validate(payload(anchors=[anchor(source_note="NEW_BUY")]))
    assert result.valid is False
    assert any("authoritative action token" in e for e in result.errors)


def test_forbidden_cap_key_at_top_level_fails() -> None:
    result = _validate(payload(sleeve_cap_pct=0.3))
    assert result.valid is False


# --- non-mapping / defensive -------------------------------------------------


def test_non_mapping_payload_is_invalid() -> None:
    result = validate_research_anchors(["not", "a", "map"], allowed_universe=ALLOWED, today=TODAY)
    assert result.valid is False
    assert result.present is True


# --- disk load + summary -----------------------------------------------------


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_research_anchors(tmp_path / "nope.yaml", allowed_universe=ALLOWED) is None


def test_build_summary_missing_file_is_data_gap(tmp_path: Path) -> None:
    summary = build_research_anchors_summary(tmp_path / "nope.yaml", allowed_universe=ALLOWED, today=TODAY)
    assert summary["available"] is False
    assert summary["data_gap"] == ANCHORS_MISSING_DATA_GAP
    assert summary["permission_effect"] == "none"


def test_build_summary_valid_file(tmp_path: Path) -> None:
    p = tmp_path / "research_anchors.yaml"
    p.write_text(
        "schema_version: research_anchors_v1\n"
        "as_of_date: 2026-06-30\n"
        "is_llm_generated: false\n"
        "anchors: []\n",
        encoding="utf-8",
    )
    summary = build_research_anchors_summary(p, allowed_universe=ALLOWED, today=TODAY)
    assert summary["available"] is True
    assert summary["valid"] is True
    assert summary["anchor_count"] == 0


def test_build_summary_malformed_yaml_is_available_but_invalid(tmp_path: Path) -> None:
    p = tmp_path / "research_anchors.yaml"
    p.write_text("schema_version: research_anchors_v1\nanchors: [ : : :\n", encoding="utf-8")
    summary = build_research_anchors_summary(p, allowed_universe=ALLOWED, today=TODAY)
    assert summary["available"] is True
    assert summary["valid"] is False
    assert summary["parse_error"] is not None


def test_anchor_types_constant_is_complete() -> None:
    assert set(ANCHOR_TYPES) == {
        "structural_theme",
        "scheduled_macro_event",
        "scheduled_earnings_event",
        "scheduled_rebalance_event",
    }
