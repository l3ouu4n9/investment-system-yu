"""Docs-content tests for per-run budget review guidance (G5.1 / UX4).

Pure text assertions on README.md and the weekly run operator runbook; no
production code runs. They assert the operator-facing reminder to review
`hard_cap_open_orders_budget` and `target_new_buy_budget_this_run` before each
weekly run, the hard-cap-vs-target semantics, the net-new-only meaning, the
operator-controlled (not LLM-generated) provenance, and the missing-field
fail-closed behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
README_PATH = REPO_ROOT / "README.md"
RUNBOOK_PATH = REPO_ROOT / "docs" / "weekly_run_operator_runbook.md"


@pytest.fixture(scope="module")
def readme_text() -> str:
    assert README_PATH.exists(), f"missing README: {README_PATH}"
    return README_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def runbook_text() -> str:
    assert RUNBOOK_PATH.exists(), f"missing runbook: {RUNBOOK_PATH}"
    return RUNBOOK_PATH.read_text(encoding="utf-8")


# --- README ------------------------------------------------------------------


def test_readme_mentions_both_budget_fields(readme_text: str) -> None:
    assert "target_new_buy_budget_this_run" in readme_text
    assert "hard_cap_open_orders_budget" in readme_text


def test_readme_explains_hard_cap_vs_target_budget(readme_text: str) -> None:
    assert "total** open-order exposure" in readme_text  # hard cap = total exposure
    assert "net-new" in readme_text  # target budget = this-run net-new deployment


def test_readme_states_budget_is_operator_controlled_not_llm(readme_text: str) -> None:
    assert "deterministic operator input, not LLM-generated" in readme_text
    assert "must **not** infer" in readme_text  # not inferred from Step 2/3 proposal


def test_readme_states_missing_target_with_net_new_fails_closed(readme_text: str) -> None:
    assert "net-new BUY orders exist" in readme_text
    assert "fails closed" in readme_text


# --- weekly runbook ----------------------------------------------------------


def test_runbook_checklist_includes_both_budget_fields(runbook_text: str) -> None:
    assert "[ ] Review `hard_cap_open_orders_budget`" in runbook_text
    assert "[ ] Review `target_new_buy_budget_this_run`" in runbook_text


def test_runbook_explains_hard_cap_vs_target_budget(runbook_text: str) -> None:
    assert "total open-order exposure" in runbook_text
    assert "net-new buy-side deployment" in runbook_text


def test_runbook_states_budget_is_operator_controlled_not_llm(runbook_text: str) -> None:
    assert "deterministic operator input, not LLM-generated" in runbook_text
    assert "Step 2 / Step 3 LLM proposed budget" in runbook_text


def test_runbook_states_net_new_only_semantics(runbook_text: str) -> None:
    assert "only to net-new buy orders" in runbook_text
    # Replacement / cancel-only / no-buy runs do not consume it.
    assert "cancel-only, and no-buy runs do" in runbook_text
    assert "consume it" in runbook_text


def test_runbook_states_missing_target_with_net_new_fails_closed(runbook_text: str) -> None:
    assert "missing and net-new BUY orders exist" in runbook_text
    assert "fails closed" in runbook_text


def test_runbook_states_stale_budget_over_allows_or_over_blocks(runbook_text: str) -> None:
    assert "over-allow or over-block" in runbook_text
