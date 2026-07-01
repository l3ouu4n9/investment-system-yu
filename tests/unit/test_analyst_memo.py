"""Unit tests for the Step 1B analyst-memo schema + parser + validator (R2C).

Cover the pure parser/validator: a valid memo passes; a missing memo is absent
(not a crash); malformed JSON fails; confidence is constrained to low/medium/high;
out-of-universe tickers are rejected; budget keys, allowed-universe / strict-handoff
keys, and execution-authority fields are all rejected; and the memo can never carry
an authoritative action token. Report-only: none of this gates anything.
"""

from __future__ import annotations

import json
from typing import Any

from investment_orchestrator.research.analyst_memo import (
    SCHEMA_VERSION,
    analyst_memo_parse_result_to_dict,
    evidence_universe_from_packet,
    parse_analyst_memo_text,
    validate_analyst_memo,
)


UNIVERSE = ["QQQ", "VOO", "VTI", "VT", "SMH", "IGV", "GRID", "CIBR"]


def valid_memo(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": True,
        "as_of_date": "2026-06-28",
        "regime_view": "constructive but rate-sensitive",
        "key_risks": ["rates", "AI capex digestion"],
        "opportunity_summary": "AI compute and infrastructure remain the strongest 12m+ theme.",
        "ticker_relative_view": [
            {"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "core anchor"},
            {"ticker": "GRID", "stance": "neutral", "rationale_12m_plus": "electrification"},
        ],
        "preferred_exposures": ["AI infrastructure", "semiconductors"],
        "avoid_or_deprioritize": ["long-duration speculative growth"],
        "scheduled_event_interpretation": ["FOMC read as neutral for the regime"],
        "confidence": "medium",
        "data_gaps": [],
        "source_notes": [{"claim": "x", "source": "official filing", "source_quality": "official"}],
    }
    base.update(overrides)
    return base


def parse(memo: Any) -> Any:
    return parse_analyst_memo_text(json.dumps(memo), evidence_universe=UNIVERSE)


# --- valid memo --------------------------------------------------------------


def test_valid_memo_passes() -> None:
    result = parse(valid_memo())
    assert result.present is True
    assert result.valid is True
    assert result.problems == []
    assert result.memo is not None
    assert result.memo["schema_version"] == SCHEMA_VERSION


def test_validate_returns_no_problems_for_valid_memo() -> None:
    assert validate_analyst_memo(valid_memo(), evidence_universe=UNIVERSE) == []


# --- absent / malformed ------------------------------------------------------


def test_missing_memo_is_absent_not_crash() -> None:
    for raw in (None, "", "   \n  "):
        result = parse_analyst_memo_text(raw, evidence_universe=UNIVERSE)
        assert result.present is False
        assert result.valid is False
        assert result.memo is None
        assert result.parse_error is None  # absence is not a parse error


def test_invalid_json_fails_validation() -> None:
    result = parse_analyst_memo_text("this is not json {{{", evidence_universe=UNIVERSE)
    assert result.present is True
    assert result.valid is False
    assert result.parse_error is not None
    assert result.memo is None


def test_non_object_top_level_fails() -> None:
    result = parse_analyst_memo_text(json.dumps(["a", "b"]), evidence_universe=UNIVERSE)
    assert result.present is True
    assert result.valid is False
    assert any("must be a JSON object" in p for p in result.problems)


def test_parser_handles_code_fenced_json() -> None:
    raw = "```json\n" + json.dumps(valid_memo()) + "\n```"
    result = parse_analyst_memo_text(raw, evidence_universe=UNIVERSE)
    assert result.valid is True


# --- schema_version / is_llm_generated ---------------------------------------


def test_wrong_schema_version_fails() -> None:
    problems = validate_analyst_memo(valid_memo(schema_version="research_handoff_v1"), evidence_universe=UNIVERSE)
    assert any("schema_version" in p for p in problems)


def test_is_llm_generated_must_be_true() -> None:
    assert any("is_llm_generated" in p for p in validate_analyst_memo(valid_memo(is_llm_generated=False), evidence_universe=UNIVERSE))
    assert any("is_llm_generated" in p for p in validate_analyst_memo(valid_memo(is_llm_generated="true"), evidence_universe=UNIVERSE))
    memo = valid_memo()
    del memo["is_llm_generated"]
    assert any("is_llm_generated" in p for p in validate_analyst_memo(memo, evidence_universe=UNIVERSE))


# --- confidence enum ---------------------------------------------------------


def test_confidence_outside_low_medium_high_fails() -> None:
    for bad in ("adequate", "weak", "very high", "", 0.5):
        problems = validate_analyst_memo(valid_memo(confidence=bad), evidence_universe=UNIVERSE)
        assert any("confidence" in p for p in problems), bad


def test_confidence_accepts_low_medium_high_case_insensitive() -> None:
    for good in ("low", "medium", "high", "HIGH", " Low "):
        assert validate_analyst_memo(valid_memo(confidence=good), evidence_universe=UNIVERSE) == []


# --- ticker universe membership ----------------------------------------------


def test_out_of_universe_ticker_in_ticker_relative_view_fails() -> None:
    memo = valid_memo(ticker_relative_view=[{"ticker": "TSLA", "stance": "prefer"}])
    problems = validate_analyst_memo(memo, evidence_universe=UNIVERSE)
    assert any("TSLA" in p and "outside" in p for p in problems)


def test_in_universe_tickers_pass() -> None:
    memo = valid_memo(
        ticker_relative_view=[
            {"ticker": "qqq", "stance": "prefer"},  # case-insensitive
            {"ticker": "CIBR", "stance": "deprioritize"},
        ]
    )
    assert validate_analyst_memo(memo, evidence_universe=UNIVERSE) == []


def test_invalid_stance_fails() -> None:
    memo = valid_memo(ticker_relative_view=[{"ticker": "QQQ", "stance": "BUY"}])
    assert any("stance" in p for p in validate_analyst_memo(memo, evidence_universe=UNIVERSE))


def test_ticker_relative_view_must_be_list() -> None:
    assert any("ticker_relative_view" in p for p in validate_analyst_memo(valid_memo(ticker_relative_view={"QQQ": "prefer"}), evidence_universe=UNIVERSE))


# --- anchor_id_refs (R2E.5a-2): type/format only; may reference, never create --


def test_valid_anchor_id_refs_list_passes() -> None:
    memo = valid_memo(
        ticker_relative_view=[
            {"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "x", "anchor_id_refs": ["AI_CAPEX_2026H2"]},
        ]
    )
    assert validate_analyst_memo(memo, evidence_universe=UNIVERSE) == []


def test_empty_anchor_id_refs_passes() -> None:
    memo = valid_memo(
        ticker_relative_view=[{"ticker": "QQQ", "stance": "prefer", "anchor_id_refs": []}]
    )
    assert validate_analyst_memo(memo, evidence_universe=UNIVERSE) == []


def test_non_list_anchor_id_refs_fails() -> None:
    memo = valid_memo(
        ticker_relative_view=[{"ticker": "QQQ", "stance": "prefer", "anchor_id_refs": "AI_CAPEX_2026H2"}]
    )
    assert any("anchor_id_refs must be a list" in p for p in validate_analyst_memo(memo, evidence_universe=UNIVERSE))


def test_non_string_anchor_id_ref_fails() -> None:
    memo = valid_memo(
        ticker_relative_view=[{"ticker": "QQQ", "stance": "prefer", "anchor_id_refs": ["ok", 123]}]
    )
    assert any("anchor_id_refs[1]" in p for p in validate_analyst_memo(memo, evidence_universe=UNIVERSE))


# --- forbidden budget keys ---------------------------------------------------


def test_named_budget_keys_fail() -> None:
    for key in ("hard_cap_open_orders_budget", "target_new_buy_budget_this_run"):
        problems = validate_analyst_memo(valid_memo(**{key: 12000}), evidence_universe=UNIVERSE)
        assert any(key in p for p in problems), key


def test_budget_substring_keys_fail_anywhere() -> None:
    # budget / cap / allocation substrings imply authority the memo cannot have.
    for key in ("suggested_budget", "max_allocation", "position_cap"):
        problems = validate_analyst_memo(valid_memo(**{key: 1}), evidence_universe=UNIVERSE)
        assert any(key in p for p in problems), key


def test_nested_budget_key_fails() -> None:
    memo = valid_memo(sleeve={"hard_cap_open_orders_budget": 1})
    assert any("hard_cap_open_orders_budget" in p for p in validate_analyst_memo(memo, evidence_universe=UNIVERSE))


# --- forbidden allowed-universe / strict-handoff keys ------------------------


def test_trade_universe_key_fails() -> None:
    assert any("trade_universe" in p for p in validate_analyst_memo(valid_memo(trade_universe={"allowed_buy_tickers": ["X"]}), evidence_universe=UNIVERSE))


def test_allowed_buy_tickers_key_fails() -> None:
    assert any("allowed_buy_tickers" in p for p in validate_analyst_memo(valid_memo(allowed_buy_tickers=["X"]), evidence_universe=UNIVERSE))


def test_buy_universe_scorecard_key_fails() -> None:
    assert any("buy_universe_scorecard" in p for p in validate_analyst_memo(valid_memo(buy_universe_scorecard=[]), evidence_universe=UNIVERSE))


def test_strategy_a_research_handoff_key_fails() -> None:
    assert any("strategy_a_research_handoff" in p for p in validate_analyst_memo(valid_memo(strategy_a_research_handoff={}), evidence_universe=UNIVERSE))


# --- forbidden execution-authority / action fields ---------------------------


def test_execution_authority_keys_fail() -> None:
    for key in ("allowed_actions", "final_action", "order_intent", "order_compilation", "buy_order", "execution_authorization"):
        problems = validate_analyst_memo(valid_memo(**{key: "anything"}), evidence_universe=UNIVERSE)
        assert any(key in p for p in problems), key


def test_authoritative_action_token_as_value_fails() -> None:
    for token in ("NEW_BUY", "ORDER_COMPILATION", "BUY_ORDER"):
        memo = valid_memo(recommendation=token)  # benign-looking key, authoritative value
        problems = validate_analyst_memo(memo, evidence_universe=UNIVERSE)
        assert any(token in p for p in problems), token


def test_action_token_inside_free_text_rationale_is_allowed() -> None:
    # A narrative sentence that merely mentions the words must NOT be rejected
    # (only an exact standalone authoritative value is forbidden).
    memo = valid_memo(
        opportunity_summary="We are not authorizing any new_buy here; deterministic gate decides.",
        ticker_relative_view=[{"ticker": "QQQ", "stance": "prefer", "rationale_12m_plus": "no order intent implied"}],
    )
    assert validate_analyst_memo(memo, evidence_universe=UNIVERSE) == []


# --- evidence universe extraction --------------------------------------------


def test_evidence_universe_from_packet_combines_allowed_and_approved() -> None:
    packet = {
        "universe": {
            "allowed_buy_tickers": ["QQQ", "voo", " QQQ "],
            "approved_extended_etf": ["grid", "CIBR"],
        }
    }
    assert evidence_universe_from_packet(packet) == ["QQQ", "VOO", "GRID", "CIBR"]


def test_evidence_universe_from_missing_packet_is_empty() -> None:
    assert evidence_universe_from_packet(None) == []
    assert evidence_universe_from_packet({}) == []


def test_empty_universe_rejects_any_ticker_view() -> None:
    memo = valid_memo(ticker_relative_view=[{"ticker": "QQQ", "stance": "prefer"}])
    problems = validate_analyst_memo(memo, evidence_universe=[])
    assert any("QQQ" in p and "outside" in p for p in problems)


# --- report-only artifact serialization --------------------------------------


def test_result_to_dict_marks_report_only_and_no_permission_effect() -> None:
    payload = analyst_memo_parse_result_to_dict(parse(valid_memo()))
    assert payload["report_only"] is True
    assert payload["valid"] is True
    assert payload["present"] is True
    assert payload["memo_confidence"] == "medium"
    assert "NEW_BUY" in payload["permission_effect"]
    assert "does not change allowed_actions" in payload["permission_effect"]


def test_result_to_dict_for_invalid_memo_reports_problems() -> None:
    payload = analyst_memo_parse_result_to_dict(parse(valid_memo(confidence="adequate")))
    assert payload["valid"] is False
    assert len(payload["problems"]) >= 1
