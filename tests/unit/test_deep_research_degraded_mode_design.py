"""Content tests for the Deep Research degraded-mode design doc (roadmap PR A).

Pure text assertions on the committed design document. They do not import or run
any production code, prompt, workflow, validator, normalizer, or order logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest


DOC_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "deep_research_degraded_mode_design.md"
)


@pytest.fixture(scope="module")
def doc_text() -> str:
    assert DOC_PATH.exists(), f"missing design doc: {DOC_PATH}"
    return DOC_PATH.read_text(encoding="utf-8")


def test_doc_exists(doc_text: str) -> None:
    assert doc_text.strip()
    assert "Degraded-Mode Investment System" in doc_text
    assert "DESIGN ONLY" in doc_text


def test_doc_includes_problem_statement_and_goals(doc_text: str) -> None:
    assert "## 1. Problem statement" in doc_text
    assert "## 4. Goals / non-goals" in doc_text


def test_doc_includes_current_failure_modes(doc_text: str) -> None:
    assert "## 2. Current failure modes" in doc_text
    for marker in ("F1", "F5", "F7", "F9"):
        assert marker in doc_text
    assert "validate_research_handoff" in doc_text


def test_doc_includes_step234_raw_research_output_anti_pattern(doc_text: str) -> None:
    assert "## 3. Current downstream anti-pattern" in doc_text
    assert "step2_decision_builder" in doc_text
    assert "step3_audit_engine" in doc_text
    assert "step4_order_compiler" in doc_text
    assert "research_output.json" in doc_text
    assert "Raw `research_output.json` is being treated as an execution handoff" in doc_text


def test_doc_includes_state_model_states(doc_text: str) -> None:
    assert "## 5. State model" in doc_text
    for state in (
        "STRICT_FRESH",
        "STRICT_STALE",
        "DEGRADED_WITH_LAST_GOOD",
        "DEGRADED_NO_RESEARCH",
        "INVALID_CONTRACT",
        "NO_OUTPUT",
        "MANUAL_REVIEW_REQUIRED",
    ):
        assert state in doc_text


def test_doc_includes_last_known_good_policy(doc_text: str) -> None:
    assert "## 6. Last-known-good (LKG) handoff policy" in doc_text
    assert "artifacts/state/last_good_research_handoff.json" in doc_text
    assert "artifacts/state/last_good_research_handoff_metadata.json" in doc_text
    assert "strategy_settings_hash" in doc_text
    assert "source_run_id" in doc_text


def test_doc_includes_stale_freshness_policy(doc_text: str) -> None:
    assert "## 7. Stale / freshness policy" in doc_text
    for label in ("fresh", "stale", "too_old"):
        assert label in doc_text


def test_doc_includes_degraded_mode_permission_model(doc_text: str) -> None:
    assert "## 8. Degraded-mode permission model" in doc_text
    assert "research_availability" in doc_text
    assert "allowed_actions" in doc_text
    assert "blocked_actions" in doc_text
    for action in (
        "HOLD",
        "NO_TRADE",
        "NEW_BUY",
        "SELL",
        "ROTATION",
        "REBALANCE",
        "EXTENDED_ETF_ADMISSION",
        "ORDER_COMPILATION",
    ):
        assert action in doc_text


def test_doc_includes_proposed_artifacts(doc_text: str) -> None:
    assert "## 9. Proposed artifacts" in doc_text
    for artifact in (
        "research_availability.json",
        "research_freshness_report.json",
        "research_degraded_mode_decision.json",
    ):
        assert artifact in doc_text


def test_doc_includes_roadmap_pr_a_through_f(doc_text: str) -> None:
    assert "## 10. Implementation roadmap" in doc_text
    assert "### PR A — design doc only" in doc_text
    assert "### PR B — last-known-good handoff state writer (report-only)" in doc_text
    assert "### PR C — research availability / freshness / degraded decision (report-only)" in doc_text
    assert "### PR D — Step 1 degraded-mode gate" in doc_text
    assert "### PR E — permission propagation to Step 2/3/4" in doc_text
    assert "### PR F — P1 execution safety gate" in doc_text
    # Each PR documents scope/files/risk/tests/behavior/rollback.
    for field in ("Scope:", "Files likely touched:", "Risk:", "Tests:", "Behavior change:", "Rollback:"):
        assert field in doc_text


def test_doc_includes_route_comparison(doc_text: str) -> None:
    assert "## 11. Route comparison" in doc_text
    assert "Prompt hardening" in doc_text
    assert "LLM extraction layer" in doc_text
    assert "last-good fallback" in doc_text


def test_doc_includes_safety_principles_section(doc_text: str) -> None:
    assert "## 12. Investment safety principles" in doc_text


def test_doc_includes_open_questions(doc_text: str) -> None:
    assert "## 13. Open questions" in doc_text


# --- explicit safety-principle statements (verbatim) -------------------------


def test_doc_states_no_trade_is_valid(doc_text: str) -> None:
    assert "No-trade is a valid investment decision." in doc_text


def test_doc_states_missing_stale_invalid_must_not_allow_new_buy(doc_text: str) -> None:
    assert (
        "Missing / invalid / stale research defaults to HOLD / NO_TRADE / manual review, not NEW_BUY."
        in doc_text
    )


def test_doc_states_order_compiler_must_not_trust_llm_bool_as_sole_gate(doc_text: str) -> None:
    assert (
        "Order compiler must not use LLM self-reported `audit_passed` / `order_compiler_ready` "
        "as the sole release gate." in doc_text
    )


def test_doc_states_deep_research_failure_not_repaired_by_downstream_llm(doc_text: str) -> None:
    assert "Deep Research failure must not be repaired by downstream LLMs." in doc_text


def test_doc_states_action_permission_must_be_deterministic(doc_text: str) -> None:
    assert "action permission must be deterministic." in doc_text


def test_doc_states_raw_research_output_is_not_handoff(doc_text: str) -> None:
    assert "Raw `research_output.json` is not an execution handoff." in doc_text


def test_doc_states_candidate_is_future_canonical_handoff(doc_text: str) -> None:
    assert (
        "Strict validated `research_handoff_candidate.json` is the future canonical handoff."
        in doc_text
    )
