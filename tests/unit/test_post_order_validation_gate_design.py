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


# --- §10: target_new_buy_budget_this_run source design (G2 budget / G5 impl) --


def test_doc_has_budget_source_design_section(doc_text: str) -> None:
    assert "## 10. `target_new_buy_budget_this_run` source design" in doc_text
    # The source is now implemented in G5.
    assert "IMPLEMENTED in G5" in doc_text


def test_doc_records_g5_implementation_status(doc_text: str) -> None:
    assert "### 10.8 G5 implementation status (implemented)" in doc_text
    # Net-new-only semantics, hard-cap-unchanged, and fail-closed are documented.
    assert "_net_new_buy_notional" in doc_text
    assert "Hard cap unchanged" in doc_text
    assert "fail closed" in doc_text
    # Source location + units recorded.
    assert "inputs/current/strategy_settings.yaml" in doc_text
    assert "non-negative USD" in doc_text
    # No production-semantics change asserted.
    assert "no new gate was added" in doc_text


def test_doc_records_g2_2_atomic_publish_implemented(doc_text: str) -> None:
    assert "Canonical publish atomicity (G2.2 — implemented)" in doc_text
    assert "atomic_write_text" in doc_text
    assert "os.replace" in doc_text
    # Per-file atomic but not group-atomic, and never-partial guarantee, documented.
    assert "Per-file atomic, not" in doc_text
    assert "group-atomic" in doc_text
    assert "never partially" in doc_text
    # No longer listed as deferred.
    assert "atomic publish / `os.replace`\n  (G2.2)" not in doc_text


def test_doc_records_g6_standalone_cli_gate(doc_text: str) -> None:
    assert "Standalone extractor CLI safety gate (G6 — implemented)" in doc_text
    assert "default it refuses" in doc_text
    assert "--unsafe-parse-only" in doc_text
    # The internal function API is explicitly preserved.
    assert "function API is unchanged" in doc_text
    # No longer listed as a deferred standalone-hardening item.
    assert "hardening the standalone `extract_orders_and_summary.main()` context coverage" not in doc_text


def test_doc_has_per_run_operator_review_note(doc_text: str) -> None:
    # G5.1 / UX4: operational note pointing at runbook/README review guidance.
    assert "### 10.9 Operational note" in doc_text
    assert "reviewed every" in doc_text  # per-run operator review
    # Step 2/3 LLM proposal must not be treated as the authority.
    assert "Do not treat the Step 2 / Step 3 LLM" in doc_text
    # This change is docs-only.
    assert "docs-only" in doc_text


def test_doc_budget_section_records_current_state(doc_text: str) -> None:
    # Validator already accepts/enforces it but it is never supplied a value.
    assert "already accepts and enforces" in doc_text
    assert "permissive" in doc_text  # settings validator ignores unknown keys
    # No operator source and no cash input exist today.
    assert "no operator-controlled, deterministic source" in doc_text.lower() or (
        "There is no operator-controlled, deterministic source" in doc_text
    )
    assert "No cash / account-balance input exists" in doc_text


def test_doc_budget_section_compares_all_options(doc_text: str) -> None:
    # Options A–E must all be present in the comparison.
    for label in ("**A**", "**B**", "**C**", "**D**", "**E**"):
        assert label in doc_text
    assert "weekly_budget.yaml" in doc_text  # Option B candidate file


def test_doc_budget_section_recommends_option_a(doc_text: str) -> None:
    assert "Recommended source — **Option A**" in doc_text
    assert "no schema change" in doc_text


def test_doc_budget_section_specifies_field(doc_text: str) -> None:
    assert "target_new_buy_budget_this_run" in doc_text
    assert "inputs/current/strategy_settings.yaml" in doc_text
    # Conditionally required and fail-closed under require_safety_context.
    assert "require_safety_context" in doc_text
    assert "fail closed" in doc_text


def test_doc_budget_section_covers_semantics_and_interactions(doc_text: str) -> None:
    # net-new vs replacement, hard cap, max tickers, no-trade, extended sleeve.
    assert "Net-new vs replacement" in doc_text
    assert "REPLACE_EXISTING" in doc_text
    assert "independent and additive" in doc_text
    assert "max_new_tickers_per_week" in doc_text
    assert "No-buy / no-order runs" in doc_text
    assert "sleeve_budget_cap_pct_of_total_open_orders" in doc_text


def test_doc_budget_section_proposes_g5_pr(doc_text: str) -> None:
    assert "G5" in doc_text
    assert "Why not B / C:" in doc_text
