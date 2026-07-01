"""Docs-content tests for the Step 1 evidence-first research design (R2/R2A).

Pure text assertions on the committed design doc; no production code runs. One
test couples the doc to the live validator contract so the field-classification
table cannot silently drift from `REQUIRED_TOP_LEVEL_FIELDS`, and one couples it
to the live research-availability states.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from investment_orchestrator.validators.validate_research_handoff import (
    REQUIRED_TOP_LEVEL_FIELDS,
)
from investment_orchestrator.state.research_availability import (
    DEGRADED_NO_RESEARCH,
    DEGRADED_WITH_LAST_GOOD,
    INVALID_CONTRACT,
    NO_OUTPUT,
)


DOC_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "step1_evidence_first_research_design.md"
)


@pytest.fixture(scope="module")
def doc_text() -> str:
    assert DOC_PATH.exists(), f"missing design doc: {DOC_PATH}"
    return DOC_PATH.read_text(encoding="utf-8")


def test_doc_is_design_only(doc_text: str) -> None:
    assert "DESIGN / INSPECTION ONLY" in doc_text
    assert "## 11. Non-goals" in doc_text
    assert "## 12. Rollback" in doc_text


def test_doc_describes_four_stage_architecture(doc_text: str) -> None:
    for stage in ("Step 1A", "Step 1B", "Step 1C", "Step 1D"):
        assert stage in doc_text
    assert "evidence_packet.json" in doc_text
    assert "analyst_memo.json" in doc_text
    assert "deterministic handoff" in doc_text.lower() or "handoff compiler" in doc_text.lower()


def test_doc_field_classification_covers_every_validator_required_field(doc_text: str) -> None:
    # Coupling guard: the field-source table must mention every required top-level
    # field, so the design cannot drift away from the actual validator contract.
    for field_name in REQUIRED_TOP_LEVEL_FIELDS:
        assert field_name in doc_text, f"design doc missing required field classification: {field_name}"


def test_doc_evidence_packet_is_not_llm_generated(doc_text: str) -> None:
    assert "evidence_packet" in doc_text
    assert "is_llm_generated" in doc_text
    assert "no LLM-generated claim" in doc_text or "no** LLM-generated claim" in doc_text
    assert "DATA_GAP" in doc_text


def test_doc_memo_cannot_create_tickers_or_budgets(doc_text: str) -> None:
    assert "cannot create allowed tickers" in doc_text
    assert "cannot set budgets" in doc_text
    assert "cannot be the sole authority" in doc_text


def test_doc_evidence_only_does_not_permit_new_buy(doc_text: str) -> None:
    assert "STRICT_FRESH_EVIDENCE_ONLY" in doc_text
    assert "STRICT_FRESH_WITH_LLM_MEMO" in doc_text
    assert "evidence-only must NOT permit `NEW_BUY`" in doc_text
    # Existing states are reused, not replaced.
    for state in (DEGRADED_WITH_LAST_GOOD, DEGRADED_NO_RESEARCH, INVALID_CONTRACT, NO_OUTPUT):
        assert state in doc_text


def test_doc_lists_migration_pr_sequence(doc_text: str) -> None:
    for pr in ("R2A", "R2B", "R2C", "R2D", "R2E", "R2F"):
        assert pr in doc_text


def test_doc_has_risk_analysis_and_per_pr_tests(doc_text: str) -> None:
    assert "## 9. Proposed tests" in doc_text
    assert "## 10. Risk analysis" in doc_text
    assert "no memo ⇒ no NEW_BUY" in doc_text.lower() or "no memo ⇒ no new_buy" in doc_text.lower()


def test_doc_records_r2b_implemented_report_only(doc_text: str) -> None:
    assert "## 13. R2B implementation status" in doc_text
    assert "**R2B** ✅ implemented" in doc_text
    assert "artifacts/current/step1_research/evidence_packet.json" in doc_text
    assert "evidence_packet_v1" in doc_text
    # Explicit no-permission / no-NEW_BUY change.
    assert "no new action is allowed and evidence-only still cannot enter NEW_BUY" in doc_text
    assert "is_llm_generated:false" in doc_text or "is_llm_generated" in doc_text


def test_doc_records_r2c_implemented_report_only(doc_text: str) -> None:
    assert "## 14. R2C implementation status" in doc_text
    assert "**R2C** ✅ implemented" in doc_text
    # Module + prompt + schema version.
    assert "src/investment_orchestrator/research/analyst_memo.py" in doc_text
    assert "prompts/analyst_memo.txt" in doc_text
    assert "analyst_memo_v1" in doc_text
    # Report-only artifact names.
    for artifact in (
        "analyst_memo_prompt.txt",
        "analyst_memo_raw_output.txt",
        "analyst_memo.json",
        "analyst_memo_validation.json",
    ):
        assert artifact in doc_text, artifact
    # Safety rules: no budgets, no allowed universe / strict handoff, no orders,
    # in-universe tickers, confidence enum, and explicit no-NEW_BUY / report-only.
    assert "no budget keys" in doc_text
    assert "no allowed-universe / strict-handoff keys" in doc_text
    assert "no execution-authority / order-intent keys" in doc_text
    assert "low / medium / high" in doc_text
    assert "can never permit\n`NEW_BUY`" in doc_text or "can never permit `NEW_BUY`" in doc_text
    assert "not yet consumed by any gate" in doc_text


def test_doc_r2c_safety_rules_match_implemented_constants(doc_text: str) -> None:
    # Couple the doc's safety-rule list to the live parser constants so it cannot
    # silently drift from what the validator actually rejects.
    from investment_orchestrator.research.analyst_memo import (
        CONFIDENCE_VALUES,
        FORBIDDEN_BUDGET_KEYS,
        FORBIDDEN_UNIVERSE_KEYS,
        SCHEMA_VERSION,
    )

    assert SCHEMA_VERSION in doc_text
    for value in CONFIDENCE_VALUES:
        assert value in doc_text, value
    for key in FORBIDDEN_BUDGET_KEYS:
        assert key in doc_text, key
    for key in ("trade_universe", "allowed_buy_tickers", "buy_universe_scorecard", "strategy_a_research_handoff"):
        assert key in FORBIDDEN_UNIVERSE_KEYS  # the constant actually forbids it
        assert key in doc_text, key


def test_doc_records_r2d_implemented_report_only(doc_text: str) -> None:
    assert "## 15. R2D implementation status" in doc_text
    assert "**R2D** ✅ implemented" in doc_text
    assert "src/investment_orchestrator/research/handoff_compiler.py" in doc_text
    # Report-only artifact names.
    for artifact in (
        "compiled_research_handoff_candidate.json",
        "compiled_research_handoff_validation.json",
        "compiled_research_handoff_metadata.json",
    ):
        assert artifact in doc_text, artifact
    # Explicit no-behavior / no-NEW_BUY / not-fed-to-gate invariants.
    assert "is NOT fed into `research_degraded_mode_decision`" in doc_text
    assert "evidence-only / invalid-memo modes never support `NEW_BUY`" in doc_text
    assert "No fresh memo ⇒ no NEW_BUY support" in doc_text


def test_doc_r2d_compilation_modes_match_implemented_constants(doc_text: str) -> None:
    # Couple the doc to the live compiler constants (modes + emitted-fields contract).
    from investment_orchestrator.research.handoff_compiler import (
        COMPILATION_MODE_EVIDENCE_ONLY,
        COMPILATION_MODE_EVIDENCE_PLUS_MEMO,
        COMPILATION_MODE_INVALID_MEMO_IGNORED,
    )

    for mode in (
        COMPILATION_MODE_EVIDENCE_PLUS_MEMO,
        COMPILATION_MODE_EVIDENCE_ONLY,
        COMPILATION_MODE_INVALID_MEMO_IGNORED,
    ):
        assert mode in doc_text, mode
    assert "REQUIRED_TOP_LEVEL_FIELDS" in doc_text


def test_doc_records_r2e1_implemented_non_actionable(doc_text: str) -> None:
    assert "## 16. R2E.1 implementation status" in doc_text
    assert "**R2E.1** ✅ implemented" in doc_text
    # Non-actionable invariant + exact allowed/blocked actions.
    assert "STRICT_FRESH_EVIDENCE_ONLY" in doc_text
    assert '`["HOLD", "NO_TRADE"]`' in doc_text
    assert "non-actionable" in doc_text
    assert "requires a future explicit PR" in doc_text
    # The blocked order-generating actions are named.
    for action in ("NEW_BUY", "ORDER_COMPILATION", "EXTENDED_ETF_ADMISSION", "ROTATION", "REBALANCE", "SELL"):
        assert action in doc_text, action


def test_doc_r2e1_state_matches_implemented_constant_and_permissions(doc_text: str) -> None:
    # Couple the doc to the live availability state + its allowed-action mapping.
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH,
        STRICT_FRESH_EVIDENCE_ONLY,
        _ALLOWED_ACTIONS_BY_STATE,
    )

    assert STRICT_FRESH_EVIDENCE_ONLY in doc_text
    assert STRICT_FRESH_EVIDENCE_ONLY != STRICT_FRESH
    # The implemented state is HOLD/NO_TRADE only and never permits NEW_BUY.
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_EVIDENCE_ONLY] == ("HOLD", "NO_TRADE")
    assert "NEW_BUY" not in _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_EVIDENCE_ONLY]


# --- R2E.2 design section (§17) ---------------------------------------------


@pytest.fixture(scope="module")
def r2e2_text(doc_text: str) -> str:
    # Section-specific slice so §17 assertions cannot be satisfied by earlier text.
    marker = "## 17. R2E.2 design"
    assert marker in doc_text, "missing R2E.2 design section (§17)"
    return doc_text[doc_text.index(marker) :]


def test_doc_r2e2_is_design_only_and_adds_no_permission(r2e2_text: str) -> None:
    assert "DESIGN / INSPECTION ONLY" in r2e2_text
    assert "## 17.7 Non-goals (R2E.2)" in r2e2_text
    # Explicit: no new NEW_BUY / ORDER_COMPILATION permission is added.
    assert "add `NEW_BUY` / `ORDER_COMPILATION` permission" in r2e2_text
    assert "not\nimplemented" in r2e2_text or "not implemented" in r2e2_text
    # The future actionable state is named and distinguished from the live state.
    assert "STRICT_FRESH_WITH_LLM_MEMO" in r2e2_text
    assert "STRICT_FRESH_EVIDENCE_ONLY" in r2e2_text


def test_doc_r2e2_layer1_actionable_criteria_match_validator(r2e2_text: str) -> None:
    # Couple the doc's Layer-1 actionable description to the live validator: the
    # actionable status literal, the required scorecard fields the validator
    # promotes to blockers, and the DATA_GAP markers.
    from investment_orchestrator.validators.validate_research_handoff import (
        DATA_GAP_MARKERS,
        REQUIRED_BUY_SCORECARD_FIELDS,
    )

    assert "actionable_this_run" in r2e2_text
    assert "positive_delta_research_supported" in r2e2_text
    for field_name in (
        "thesis_12m_plus_supported",
        "thesis_linkage_quality",
        "primary_anchor_event_id",
        "primary_anchor_date_et",
        "compile_blocker_if_any",
        "event_id_refs",
        "structural_theme_refs",
    ):
        assert field_name in REQUIRED_BUY_SCORECARD_FIELDS  # still a real field
        assert field_name in r2e2_text, field_name
    # DATA_GAP markers are named as blockers on an actionable row.
    assert "DATA_GAP" in DATA_GAP_MARKERS
    assert "DATA_GAP" in r2e2_text


def test_doc_r2e2_names_both_fail_closed_gates(r2e2_text: str) -> None:
    # The pivotal finding: two gates hardcode STRICT_FRESH; both must change.
    from investment_orchestrator.state.research_degraded_mode_gate import (
        ACTIONABLE_REQUIRED_STATE as STEP2_STATE,
        REQUIRED_ACTIONS,
    )
    from investment_orchestrator.state.final_execution_safety_gate import (
        ACTIONABLE_REQUIRED_STATE as FINAL_STATE,
    )

    assert STEP2_STATE == FINAL_STATE == "STRICT_FRESH"
    assert REQUIRED_ACTIONS == ("NEW_BUY", "ORDER_COMPILATION")
    assert "research_degraded_mode_gate" in r2e2_text
    assert "final_execution_safety_gate" in r2e2_text
    assert "ACTIONABLE_REQUIRED_STATE" in r2e2_text
    # Deterministic caps stay downstream in Step 4.
    assert "max_new_tickers_per_week" in r2e2_text
    assert "target_new_buy_budget" in r2e2_text


def test_doc_r2e2_memo_allowlist_denylist_match_constants(r2e2_text: str) -> None:
    # Couple the memo influence split to the live parser constants.
    from investment_orchestrator.research.analyst_memo import (
        CONFIDENCE_VALUES,
        FORBIDDEN_BUDGET_KEYS,
        FORBIDDEN_UNIVERSE_KEYS,
        STANCE_VALUES,
    )

    for stance in STANCE_VALUES:
        assert stance in r2e2_text, stance
    for value in CONFIDENCE_VALUES:
        assert value in r2e2_text, value
    for key in FORBIDDEN_BUDGET_KEYS:
        assert key in r2e2_text, key
    for key in ("trade_universe", "allowed_buy_tickers", "buy_universe_scorecard", "strategy_a_research_handoff"):
        assert key in FORBIDDEN_UNIVERSE_KEYS
        assert key in r2e2_text, key
    # Allowed qualitative influences are named.
    for allowed in ("rationale_12m_plus", "regime_view", "preferred_exposures", "avoid_or_deprioritize", "source_notes"):
        assert allowed in r2e2_text, allowed
    # Extended-ETF admission stays separately gated / disabled in v1.
    assert "extended" in r2e2_text.lower()
    assert "source_notes" in r2e2_text  # flagged as not-yet-enforced


def test_doc_r2e2_actionable_criteria_and_confidence_floor(r2e2_text: str) -> None:
    assert "## 17.3 Proposed deterministic actionable criteria" in r2e2_text
    # Confidence floor: reject low; require present+valid memo (evidence_plus_memo).
    from investment_orchestrator.research.handoff_compiler import (
        COMPILATION_MODE_EVIDENCE_PLUS_MEMO,
    )

    assert COMPILATION_MODE_EVIDENCE_PLUS_MEMO in r2e2_text
    assert "not `low`" in r2e2_text or "not\n`low`" in r2e2_text
    assert 'stance == "prefer"' in r2e2_text
    # The blocking structural finding must be stated: no anchor source today.
    assert "anchor" in r2e2_text.lower()


def test_doc_r2e2_recommendation_is_conservative(r2e2_text: str) -> None:
    assert "## 17.4 Recommended first actionable version" in r2e2_text
    # Ship A now, C as the low-risk step, B as the target actionable version.
    assert "Ship A now" in r2e2_text
    assert "no extended etf" in r2e2_text.lower() or "sleeve stays disabled" in r2e2_text.lower()
    assert "never" in r2e2_text.lower()  # never out-of-universe / never low-confidence


def test_doc_r2e2_pr_sequence(r2e2_text: str) -> None:
    assert "## 17.6 Proposed PR sequence" in r2e2_text
    for pr in ("R2E.2", "R2E.3", "R2E.4", "R2E.5a", "R2E.5b", "R2F"):
        assert pr in r2e2_text, pr


def test_doc_r2e2_has_risk_analysis(r2e2_text: str) -> None:
    assert "## 17.5 Risk analysis" in r2e2_text
    for risk_kw in ("overconfiden", "stale", "market metrics", "over-interpret", "fragility"):
        assert risk_kw in r2e2_text.lower(), risk_kw


# --- R2E.3 implementation status (§18) --------------------------------------


@pytest.fixture(scope="module")
def r2e3_text(doc_text: str) -> str:
    marker = "## 18. R2E.3 implementation status"
    assert marker in doc_text, "missing R2E.3 implementation section (§18)"
    return doc_text[doc_text.index(marker) :]


def test_doc_records_r2e3_implemented_report_only(doc_text: str, r2e3_text: str) -> None:
    assert "**R2E.3** ✅ implemented" in doc_text
    assert "src/investment_orchestrator/research/support_signals.py" in r2e3_text
    assert "compiled_support_signals.json" in r2e3_text
    # Required artifact fields are documented.
    for field_name in (
        "candidate_ticker_signals",
        "accepted_support_signals",
        "rejected_support_signals",
        "global_blockers",
    ):
        assert field_name in r2e3_text, field_name


def test_doc_r2e3_explicit_no_permission_no_state_enable(r2e3_text: str) -> None:
    # Must state: no NEW_BUY/ORDER_COMPILATION and does NOT enable the actionable state.
    assert "no `NEW_BUY` / `ORDER_COMPILATION` permission" in r2e3_text
    assert "does\nNOT enable `STRICT_FRESH_WITH_LLM_MEMO`" in r2e3_text or "does NOT enable `STRICT_FRESH_WITH_LLM_MEMO`" in r2e3_text
    assert "accepted_support_signals` is **always empty**" in r2e3_text
    assert "STRICT_FRESH_EVIDENCE_ONLY" in r2e3_text


def test_doc_r2e3_rejection_reason_codes_match_constants(r2e3_text: str) -> None:
    # Couple the doc's reason-code list to the live extractor constants.
    from investment_orchestrator.research.support_signals import (
        REJECTION_REASON_CODES,
        SCHEMA_VERSION,
    )

    assert SCHEMA_VERSION in r2e3_text
    for code in REJECTION_REASON_CODES:
        assert code in r2e3_text, code


def test_doc_r2e3_schema_version_matches_constant(doc_text: str) -> None:
    from investment_orchestrator.research.support_signals import SCHEMA_VERSION

    assert SCHEMA_VERSION == "compiled_support_signals_v1"
    assert SCHEMA_VERSION in doc_text


# --- R2E.5a anchor-source design section (§19) ------------------------------


@pytest.fixture(scope="module")
def r2e5a_text(doc_text: str) -> str:
    marker = "## 19. R2E.5a design"
    assert marker in doc_text, "missing R2E.5a anchor-source design section (§19)"
    return doc_text[doc_text.index(marker) :]


def test_doc_r2e5a_is_design_only_and_adds_no_permission(r2e5a_text: str) -> None:
    assert "DESIGN / INSPECTION ONLY" in r2e5a_text
    assert "## 19.9 Non-goals (R2E.5a-design)" in r2e5a_text
    assert "not** implement it" in r2e5a_text
    # No new permission / does not enable the actionable state.
    assert "`ORDER_COMPILATION` permission" in r2e5a_text
    assert "STRICT_FRESH_WITH_LLM_MEMO" in r2e5a_text
    assert "STRICT_FRESH_EVIDENCE_ONLY" in r2e5a_text


def test_doc_r2e5a_defines_schema_and_operator_recommendation(r2e5a_text: str) -> None:
    assert "research_anchors_v1" in r2e5a_text
    assert "research_anchors.yaml" in r2e5a_text
    # Recommended option B (operator-controlled, deterministic, no LLM anchors).
    assert "Option B" in r2e5a_text
    assert "no LLM-generated anchors" in r2e5a_text or "no** LLM-generated anchors" in r2e5a_text
    # Required schema fields are named.
    for field_name in (
        "anchor_id",
        "anchor_type",
        "applicable_tickers",
        "anchor_date_et",
        "valid_from",
        "valid_until",
        "confidence_floor",
        "blocks_if_stale",
    ):
        assert field_name in r2e5a_text, field_name


def test_doc_r2e5a_anchor_fills_validator_actionable_fields(r2e5a_text: str) -> None:
    # Couple the anchor design to the validator's actionable-row contract: the
    # exact fields an anchor must supply.
    from investment_orchestrator.validators.validate_research_handoff import (
        REQUIRED_BUY_SCORECARD_FIELDS,
    )

    for field_name in (
        "primary_anchor_event_id",
        "primary_anchor_date_et",
        "event_id_refs",
        "structural_theme_refs",
    ):
        assert field_name in REQUIRED_BUY_SCORECARD_FIELDS
        assert field_name in r2e5a_text, field_name


def test_doc_r2e5a_ties_to_support_signal_blocker(r2e5a_text: str) -> None:
    # The design must be motivated by the live support-signal blocker code.
    from investment_orchestrator.research.support_signals import (
        REASON_MISSING_VALID_ANCHOR_SOURCE,
    )

    assert REASON_MISSING_VALID_ANCHOR_SOURCE in r2e5a_text
    assert "qualitative_support_only" in r2e5a_text
    assert "accepted_support_signals" in r2e5a_text


def test_doc_r2e5a_rejects_llm_and_lastgood_anchor_sources(r2e5a_text: str) -> None:
    # source_notes (LLM) and last-good themes must be rejected as anchor sources.
    assert "source_notes" in r2e5a_text
    assert "last-good" in r2e5a_text.lower()
    assert "reject" in r2e5a_text.lower()
    # Distinguish from the daily-execution price-baseline anchor.
    assert "anchor_baseline_last_close" in r2e5a_text
    assert "research anchor" in r2e5a_text.lower()


def test_doc_r2e5a_has_option_comparison_risks_and_tests(r2e5a_text: str) -> None:
    assert "### 19.3 Candidate anchor-source options" in r2e5a_text
    assert "### 19.7 Proposed tests" in r2e5a_text
    assert "### 19.8 Risks" in r2e5a_text
    for risk_kw in ("operator burden", "stale", "overfitting", "citation"):
        assert risk_kw in r2e5a_text.lower(), risk_kw


# --- R2E.5a-impl status (§20) -----------------------------------------------


@pytest.fixture(scope="module")
def r2e5a_impl_text(doc_text: str) -> str:
    marker = "## 20. R2E.5a-impl status"
    assert marker in doc_text, "missing R2E.5a-impl section (§20)"
    return doc_text[doc_text.index(marker) :]


def test_doc_records_r2e5a_impl_report_only(r2e5a_impl_text: str) -> None:
    assert "src/investment_orchestrator/research/research_anchors.py" in r2e5a_impl_text
    assert "inputs/current/research_anchors.yaml" in r2e5a_impl_text
    # Report-only / no permission / not consumed for acceptance.
    assert "adds no\n`NEW_BUY` / `ORDER_COMPILATION`" in r2e5a_impl_text or "adds no `NEW_BUY` / `ORDER_COMPILATION`" in r2e5a_impl_text
    assert "not yet consumed for support acceptance" in r2e5a_impl_text
    assert "STRICT_FRESH_EVIDENCE_ONLY" in r2e5a_impl_text
    assert "consumed_for_support_acceptance: false" in r2e5a_impl_text


def test_doc_r2e5a_impl_schema_and_enums_match_constants(r2e5a_impl_text: str) -> None:
    from investment_orchestrator.research.research_anchors import (
        ANCHOR_TYPES,
        CONFIDENCE_VALUES,
        REQUIRED_ANCHOR_FIELDS,
        SCHEMA_VERSION,
        SOURCE_TYPES,
    )

    assert SCHEMA_VERSION == "research_anchors_v1"
    assert SCHEMA_VERSION in r2e5a_impl_text
    for anchor_type in ANCHOR_TYPES:
        assert anchor_type in r2e5a_impl_text, anchor_type
    for source_type in SOURCE_TYPES:
        assert source_type in r2e5a_impl_text, source_type
    for value in CONFIDENCE_VALUES:
        assert value in r2e5a_impl_text, value
    for field_name in REQUIRED_ANCHOR_FIELDS:
        assert field_name in r2e5a_impl_text, field_name


def test_doc_r2e5a_impl_evidence_packet_field_documented(doc_text: str, r2e5a_impl_text: str) -> None:
    # The evidence packet gained a required research_anchors field.
    from investment_orchestrator.research.evidence_packet import (
        EVIDENCE_PACKET_REQUIRED_FIELDS,
    )

    assert "research_anchors" in EVIDENCE_PACKET_REQUIRED_FIELDS
    for summary_field in (
        "anchor_count",
        "valid_anchor_count",
        "stale_anchor_count",
        "invalid_anchor_count",
    ):
        assert summary_field in r2e5a_impl_text, summary_field


# --- R2E.5a-2 status (§21): anchors → support acceptance --------------------


@pytest.fixture(scope="module")
def r2e5a2_text(doc_text: str) -> str:
    marker = "## 21. R2E.5a-2 status"
    assert marker in doc_text, "missing R2E.5a-2 section (§21)"
    return doc_text[doc_text.index(marker) :]


def test_doc_records_r2e5a2_report_only_not_authorization(r2e5a2_text: str) -> None:
    assert "anchor_id_refs" in r2e5a2_text
    assert "accepted_support_signals" in r2e5a2_text
    assert "not_authorization" in r2e5a2_text
    assert "NOT authorization" in r2e5a2_text or "not** authorization" in r2e5a2_text or "not trade\nauthorization" in r2e5a2_text
    # Explicit no-permission / not-enabled statements.
    assert "no `NEW_BUY` / `ORDER_COMPILATION` permission" in r2e5a2_text
    assert "STRICT_FRESH_WITH_LLM_MEMO" in r2e5a2_text
    assert "STRICT_FRESH_EVIDENCE_ONLY" in r2e5a2_text
    # Compiled handoff invariants preserved.
    assert "positive_delta_research_supported=[]" in r2e5a2_text
    assert "primary_anchor_event_id" in r2e5a2_text


def test_doc_r2e5a2_reason_codes_match_constants(doc_text: str, r2e5a2_text: str) -> None:
    # Couple the documented new codes to the live extractor constants.
    from investment_orchestrator.research.support_signals import (
        REASON_ANCHOR_CONFIDENCE_FLOOR_NOT_MET,
        REASON_ANCHOR_NOT_APPLICABLE,
        REASON_ANCHOR_SOURCE_TYPE_NOT_ALLOWED,
        REASON_ANCHOR_TYPE_NOT_ALLOWED,
        REASON_MISSING_ANCHOR_ID_REFS,
        REASON_REFERENCED_ANCHOR_NOT_FOUND,
        REASON_REFERENCED_ANCHOR_STALE,
        REJECTION_REASON_CODES,
    )

    for code in (
        REASON_MISSING_ANCHOR_ID_REFS,
        REASON_REFERENCED_ANCHOR_NOT_FOUND,
        REASON_REFERENCED_ANCHOR_STALE,
        REASON_ANCHOR_NOT_APPLICABLE,
        REASON_ANCHOR_CONFIDENCE_FLOOR_NOT_MET,
        REASON_ANCHOR_SOURCE_TYPE_NOT_ALLOWED,
        REASON_ANCHOR_TYPE_NOT_ALLOWED,
    ):
        assert code in REJECTION_REASON_CODES
        assert code in r2e5a2_text, code


# --- R2E.4 status (§22): grounded memo state, non-actionable ----------------


@pytest.fixture(scope="module")
def r2e4_text(doc_text: str) -> str:
    marker = "## 22. R2E.4 status"
    assert marker in doc_text, "missing R2E.4 section (§22)"
    return doc_text[doc_text.index(marker) :]


def test_doc_records_r2e4_non_actionable_state(r2e4_text: str) -> None:
    assert "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE" in r2e4_text
    # HOLD/NO_TRADE only; explicit no-permission.
    assert "HOLD / NO_TRADE" in r2e4_text
    assert "No\n`NEW_BUY` / `ORDER_COMPILATION` permission is added" in r2e4_text or "No `NEW_BUY` / `ORDER_COMPILATION` permission is added" in r2e4_text
    assert "no gate is opened" in r2e4_text
    # Blocked order-generating actions named.
    for action in ("SELL", "NEW_BUY", "ROTATION", "REBALANCE", "EXTENDED_ETF_ADMISSION", "ORDER_COMPILATION"):
        assert action in r2e4_text, action
    # Trigger criteria + non-actionable invariant.
    assert "accepted_support_signals" in r2e4_text
    assert "not_authorization" in r2e4_text
    assert "positive_delta_research_supported=[]" in r2e4_text


def test_doc_r2e4_state_matches_constant_and_permissions(r2e4_text: str) -> None:
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH,
        STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE,
        _ALLOWED_ACTIONS_BY_STATE,
    )

    assert STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE in r2e4_text
    assert STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE != STRICT_FRESH
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE] == ("HOLD", "NO_TRADE")
    assert "NEW_BUY" not in _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE]
    # Step 2 gate still keys off STRICT_FRESH → grounded state is blocked.
    from investment_orchestrator.state.research_degraded_mode_gate import (
        ACTIONABLE_REQUIRED_STATE,
    )

    assert ACTIONABLE_REQUIRED_STATE == "STRICT_FRESH"
    assert ACTIONABLE_REQUIRED_STATE != STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE
