"""Docs-content tests for the run_status / blocked-run operating guidance (UX2).

Pure text assertions on README.md and the degraded-mode design doc. They run no
production code and assert only the operator-facing guidance for discovering and
reading `run_status` / `run_summary.json`.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
README_PATH = REPO_ROOT / "README.md"
DEGRADED_DOC_PATH = REPO_ROOT / "docs" / "deep_research_degraded_mode_design.md"


@pytest.fixture(scope="module")
def readme_text() -> str:
    assert README_PATH.exists(), f"missing README: {README_PATH}"
    return README_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def degraded_doc_text() -> str:
    assert DEGRADED_DOC_PATH.exists(), f"missing degraded-mode doc: {DEGRADED_DOC_PATH}"
    return DEGRADED_DOC_PATH.read_text(encoding="utf-8")


# --- README ------------------------------------------------------------------


def test_readme_documents_run_status_command(readme_text: str) -> None:
    assert "## Run Status / Blocked-Run Summary" in readme_text
    assert "investment_orchestrator.cli.run_status" in readme_text


def test_readme_points_at_run_summary_artifact(readme_text: str) -> None:
    assert "artifacts/current/run_summary.json" in readme_text


def test_readme_says_summary_is_not_llm_decision_output(readme_text: str) -> None:
    assert "is_llm_generated" in readme_text
    assert "not** an" in readme_text or "not an" in readme_text  # "not an LLM decision packet"


def test_readme_lists_key_summary_fields(readme_text: str) -> None:
    for field in ("run_blocked", "recommended_result", "blocked_stages", "source_artifacts"):
        assert field in readme_text


# --- degraded-mode design doc ------------------------------------------------


def test_degraded_doc_has_blocked_run_inspection_section(degraded_doc_text: str) -> None:
    assert "Operating: inspect a blocked or degraded run" in degraded_doc_text
    assert "investment_orchestrator.cli.run_status" in degraded_doc_text


def test_degraded_doc_states_no_downstream_llm_repair(degraded_doc_text: str) -> None:
    assert "must **not** be repaired by a downstream LLM" in degraded_doc_text


def test_degraded_doc_explains_no_trade_is_deterministic_safety_outcome(
    degraded_doc_text: str,
) -> None:
    assert "deterministic safety outcome, not a silent failure" in degraded_doc_text
    assert "No-trade is a valid investment decision." in degraded_doc_text


def test_degraded_doc_states_summary_is_not_llm_generated(degraded_doc_text: str) -> None:
    assert "is_llm_generated=false" in degraded_doc_text


def test_degraded_doc_traces_source_artifacts(degraded_doc_text: str) -> None:
    assert "source_artifacts" in degraded_doc_text


def test_degraded_doc_describes_final_execution_safety_gate(degraded_doc_text: str) -> None:
    assert "final execution safety gate" in degraded_doc_text
    assert "step4_blocked_by_final_execution_safety_gate.json" in degraded_doc_text
    assert "necessary\nbut not sufficient" in degraded_doc_text or "necessary but not sufficient" in degraded_doc_text
