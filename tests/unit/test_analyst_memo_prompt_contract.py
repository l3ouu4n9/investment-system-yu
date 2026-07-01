"""Content tests for the small Step 1B analyst-memo prompt (R2C).

Pure text assertions on the committed prompt template: it asks for an
analyst_memo_v1 memo only, forbids budgets / allowed universe / strict handoff /
orders / execution authorization, references the evidence_packet input, and is
much shorter than the monolithic Deep Research dual-lane prompt. They do not run
the model or change any gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "analyst_memo.txt"
RESEARCH_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "research_dual_lane.txt"


@pytest.fixture(scope="module")
def prompt_text() -> str:
    assert PROMPT_PATH.exists(), f"missing prompt template: {PROMPT_PATH}"
    return PROMPT_PATH.read_text(encoding="utf-8")


# --- analyst memo only -------------------------------------------------------


def test_prompt_requests_analyst_memo_only(prompt_text: str) -> None:
    assert "analyst_memo_v1" in prompt_text
    assert "ANALYST MEMO ONLY" in prompt_text
    # It is explicitly NOT the strict research handoff.
    assert "qualitative opinion only" in prompt_text or "qualitative" in prompt_text


def test_prompt_references_evidence_packet_input(prompt_text: str) -> None:
    assert "evidence_packet" in prompt_text
    assert "{{ evidence_packet_json }}" in prompt_text
    assert "EVIDENCE_PACKET_START" in prompt_text


# --- forbids budgets ---------------------------------------------------------


def test_prompt_forbids_budgets(prompt_text: str) -> None:
    assert "no_budgets_rule" in prompt_text
    assert "budget" in prompt_text
    assert "hard_cap_open_orders_budget" in prompt_text
    assert "target_new_buy_budget_this_run" in prompt_text


# --- forbids allowed universe / strict handoff -------------------------------


def test_prompt_forbids_allowed_universe_and_strict_handoff(prompt_text: str) -> None:
    assert "no_allowed_universe_rule" in prompt_text
    assert "trade_universe" in prompt_text
    assert "allowed_buy_tickers" in prompt_text
    assert "buy_universe_scorecard" in prompt_text
    assert "strategy_a_research_handoff" in prompt_text


# --- forbids orders / execution authorization --------------------------------


def test_prompt_forbids_orders_and_execution_authorization(prompt_text: str) -> None:
    assert "no_orders_rule" in prompt_text
    assert "NEW_BUY" in prompt_text
    assert "ORDER_COMPILATION" in prompt_text
    assert "execution authorization" in prompt_text or "execution_authorization" in prompt_text


def test_prompt_restricts_to_in_universe_tickers(prompt_text: str) -> None:
    assert "in_universe_only_rule" in prompt_text
    assert "out-of-universe" in prompt_text


def test_prompt_constrains_confidence(prompt_text: str) -> None:
    assert "confidence_rule" in prompt_text
    assert "low / medium / high" in prompt_text or "low|medium|high" in prompt_text


# --- much shorter than the monolithic Deep Research prompt -------------------


def test_prompt_is_much_shorter_than_deep_research_prompt(prompt_text: str) -> None:
    research_text = RESEARCH_PROMPT_PATH.read_text(encoding="utf-8")
    # The whole point of R2C is a small contract: the memo prompt must be a small
    # fraction of the ~127 KB single-shot strict handoff prompt.
    assert len(prompt_text) < len(research_text) / 5
