"""Docs-content tests for the post-order validation inspection/design doc (PR G0).

Pure text assertions on the committed design document; no production code runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest


DOC_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "post_order_validation_gate_design.md"
)


@pytest.fixture(scope="module")
def doc_text() -> str:
    assert DOC_PATH.exists(), f"missing design doc: {DOC_PATH}"
    return DOC_PATH.read_text(encoding="utf-8")


def test_doc_exists_and_is_design_only(doc_text: str) -> None:
    assert "DESIGN / INSPECTION ONLY" in doc_text


def test_doc_describes_current_order_output_path(doc_text: str) -> None:
    assert "validate_orders_output" in doc_text
    assert "extract_orders_and_summary" in doc_text
    assert "template4_orders.txt" in doc_text


def test_doc_notes_validator_is_automatic_and_fail_closed(doc_text: str) -> None:
    assert "Automatic, not optional, fail-closed" in doc_text


def test_doc_notes_no_broker_live_path(doc_text: str) -> None:
    assert "no broker / live-order / automated execution path" in doc_text


def test_doc_has_coverage_matrix_classes(doc_text: str) -> None:
    assert "## 3. Validation coverage matrix" in doc_text
    for token in ("hard_cap_open_orders_budget", "max_new_tickers_per_week", "Duplicate ticker"):
        assert token in doc_text


def test_doc_recommends_option_c_strengthen_coverage(doc_text: str) -> None:
    assert "Option C" in doc_text
    assert "strengthen validator coverage" in doc_text


def test_doc_has_non_goals_and_rollback(doc_text: str) -> None:
    assert "## 7. Non-goals" in doc_text
    assert "## 8. Rollback" in doc_text
    assert "No broker / live-execution integration." in doc_text
