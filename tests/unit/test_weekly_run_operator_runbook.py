"""Docs-content test for the weekly run operator runbook.

Pure text assertions on the committed runbook; no production code runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest


DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "weekly_run_operator_runbook.md"


@pytest.fixture(scope="module")
def doc_text() -> str:
    assert DOC_PATH.exists(), f"missing operator runbook: {DOC_PATH}"
    return DOC_PATH.read_text(encoding="utf-8")


def test_runbook_contains_critical_operator_phrases(doc_text: str) -> None:
    for phrase in (
        "manual-order v1",
        "not live-trading",
        "run_summary.json",
        "run_blocked",
        "recommended_result",
        "SELL_ORDERS",
        "NO_TRADE",
        "quarantine",
        "final execution safety gate",
    ):
        assert phrase in doc_text, f"runbook missing critical phrase: {phrase!r}"


def test_runbook_has_acceptance_and_no_trade_and_decision_rule_sections(doc_text: str) -> None:
    assert "## 4. Acceptance checklist" in doc_text
    assert "## 5. NO_TRADE / blocked checklist" in doc_text
    assert "## 9. Ready vs NO_TRADE decision rule" in doc_text


def test_runbook_states_llm_booleans_not_sufficient(doc_text: str) -> None:
    assert "necessary but not sufficient" in doc_text


def test_runbook_documents_weekly_level_controlled_no_trade(doc_text: str) -> None:
    for phrase in (
        "run_weekly",
        "weekly_outcome.json",
        "terminal_result",
        "exits `0`",
        "not LLM generated",
    ):
        assert phrase in doc_text, f"runbook missing weekly-command phrase: {phrase!r}"


def test_runbook_warns_standalone_extractor_is_not_the_safe_step4_path(doc_text: str) -> None:
    # G6: run_step4 parse is the only safe Step 4 path; standalone extractor /
    # --unsafe-parse-only must not be used to approve orders.
    assert "Step 4 has exactly one safe path: `run_step4 parse`" in doc_text
    assert "--unsafe-parse-only" in doc_text
    assert "automation must\nnever call it to approve trades" in doc_text or (
        "automation must never call it to approve trades" in doc_text
    )


def test_runbook_lists_canonical_step4_files_and_block_artifacts(doc_text: str) -> None:
    for artifact in (
        "template4_orders.txt",
        "order_state_export.txt",
        "exec_summary.txt",
        "step2_blocked_by_research_gate.json",
        "step3_blocked_by_upstream_gate.json",
        "step4_blocked_by_upstream_gate.json",
        "step4_blocked_by_final_execution_safety_gate.json",
    ):
        assert artifact in doc_text
