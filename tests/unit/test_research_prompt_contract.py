"""Content tests for the Step 1 Deep Research prompt output contract.

These guard the prompt against the observed output drift: narrative / markdown
lane payloads, `RESEARCH_JSON` envelope wrappers, and omission of the strict
machine-readable handoff fields. They are pure text assertions on the committed
prompt template; they do not run the model, change any gate, or touch
Step 2/3/4, the validator, the normalizer, or any order logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from investment_orchestrator.validators.validate_research_handoff import (
    REQUIRED_TOP_LEVEL_FIELDS,
)


PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "research_dual_lane.txt"
)


@pytest.fixture(scope="module")
def prompt_text() -> str:
    assert PROMPT_PATH.exists(), f"missing prompt template: {PROMPT_PATH}"
    return PROMPT_PATH.read_text(encoding="utf-8")


# --- markers -----------------------------------------------------------------


def test_prompt_requires_research_json_markers(prompt_text: str) -> None:
    assert "RESEARCH_JSON_START" in prompt_text
    assert "RESEARCH_JSON_END" in prompt_text
    # The markers must wrap a single top-level JSON object.
    assert "單一 top-level JSON object" in prompt_text


# --- strict handoff field coverage (coupled to the validator contract) -------


def test_prompt_lists_all_validator_required_handoff_fields(prompt_text: str) -> None:
    for field_name in REQUIRED_TOP_LEVEL_FIELDS:
        assert field_name in prompt_text, f"prompt missing required handoff field: {field_name}"


def test_prompt_requires_strategy_a_research_handoff(prompt_text: str) -> None:
    assert "strategy_a_research_handoff" in prompt_text
    assert "strategy_a_research_handoff_v1" in prompt_text


def test_prompt_canonical_skeleton_requires_allowed_buy_tickers(prompt_text: str) -> None:
    assert "trade_universe.allowed_buy_tickers" in prompt_text
    assert "strict_machine_readable_handoff_contract_rule" in prompt_text


# --- forbidden output shapes -------------------------------------------------


def test_prompt_forbids_top_level_narrative_lanes(prompt_text: str) -> None:
    assert "forbidden_output_shapes_rule" in prompt_text
    # The exact drift shape (narrative markdown lanes at top level) is named.
    assert "禁止 narrative lanes 作 top-level payload" in prompt_text
    for lane_key in ("lane_a", "lane_b", "lane_A", "lane_B"):
        assert lane_key in prompt_text


def test_prompt_forbids_research_json_envelope_wrapper(prompt_text: str) -> None:
    assert "禁止 envelope 包裝" in prompt_text
    assert "不可巢狀在 RESEARCH_JSON / STRATEGY_SETTINGS_YAML" in prompt_text


def test_prompt_forbids_base_universe_markdown_substitution(prompt_text: str) -> None:
    assert "base_universe" in prompt_text
    assert (
        "禁止用 base_universe + 一段 markdown 報告" in prompt_text
    )


# --- passed=true cannot replace required fields ------------------------------


def test_prompt_states_passed_true_cannot_omit_handoff_fields(prompt_text: str) -> None:
    assert "passed=true 不代表可以少輸出欄位" in prompt_text


# --- DATA_GAP must be explicit, not omission ---------------------------------


def test_prompt_requires_explicit_data_gap_not_omission(prompt_text: str) -> None:
    assert "data_gap_must_be_explicit_rule" in prompt_text
    assert "不得用省略欄位、刪減 handoff 欄位、或改輸出 narrative，來規避 DATA_GAP 標記" in prompt_text


def test_prompt_requires_fallback_strict_json_on_failure(prompt_text: str) -> None:
    assert "cannot_complete_strict_handoff_fallback_rule" in prompt_text
    assert "無法完成的正確表達是 strict JSON + explicit failure，不是退回散文" in prompt_text


# --- disabled Lane B / extended ETF gate needs explicit reason ---------------


def test_prompt_requires_disabled_gate_explicit_reason(prompt_text: str) -> None:
    assert "optional_extended_etf_sleeve.enabled=false" in prompt_text
    assert "必須附明確 disable_reason 與 why_not_enabled，不可留空" in prompt_text


# --- scorecard rows must be structured objects -------------------------------


def test_prompt_requires_scorecard_rows_as_structured_objects(prompt_text: str) -> None:
    assert "scorecard rows 必須是 structured objects，不是 prose paragraphs" in prompt_text


# --- non-order boundary (Step 1 does not produce orders) ---------------------


def test_prompt_states_research_handoff_is_not_order_instruction(prompt_text: str) -> None:
    assert "not_order_instruction" in prompt_text
    assert "research_to_decision_builder_only" in prompt_text
    assert "不得在此輸出任何 order、order sizing、或 compile-ready 下單指令" in prompt_text


# --- self-check checklist covers the new contract ----------------------------


def test_prompt_self_check_covers_strict_shape_and_drift(prompt_text: str) -> None:
    # The pre-output self-check enumerates the anti-drift items.
    assert "未被包進任何 wrapper" in prompt_text
    assert "作 top-level payload，或取代 machine-readable 欄位" in prompt_text
