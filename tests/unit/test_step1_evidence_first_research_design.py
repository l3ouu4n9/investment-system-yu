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


# --- R2E.5b-0 status (§23): actionable-handoff PREVIEW, report-only ----------


@pytest.fixture(scope="module")
def r2e5b0_text(doc_text: str) -> str:
    marker = "## 23. R2E.5b-0 status"
    assert marker in doc_text, "missing R2E.5b-0 section (§23)"
    return doc_text[doc_text.index(marker) :]


def test_doc_records_r2e5b0_preview_is_not_authorization(r2e5b0_text: str) -> None:
    # Report-only / not authorization, and explicitly non-actionable active handoff.
    assert "PREVIEW" in r2e5b0_text
    assert "report-only" in r2e5b0_text
    assert "not_authorization: true" in r2e5b0_text
    assert "NOT authorization" in r2e5b0_text or "not authorization" in r2e5b0_text
    assert "No `NEW_BUY` / `ORDER_COMPILATION` permission is added" in r2e5b0_text
    assert "STRICT_FRESH_WITH_LLM_MEMO" in r2e5b0_text
    # Active compiled handoff stays non-actionable.
    assert "positive_delta_research_supported=[]" in r2e5b0_text
    assert "primary_anchor_event_id=null" in r2e5b0_text
    # Availability / gate / weekly unchanged.
    assert "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE" in r2e5b0_text
    assert "STRICT_FRESH_EVIDENCE_ONLY" in r2e5b0_text
    assert "Step 2 research gate still blocks" in r2e5b0_text
    assert "NO_TRADE" in r2e5b0_text
    # Separate artifact + schema, not passed downstream.
    assert "compiled_actionable_handoff_preview.json" in r2e5b0_text
    assert "compiled_actionable_handoff_preview_v1" in r2e5b0_text
    assert "not** passed into the availability evaluator" in r2e5b0_text or "not passed into the availability evaluator" in r2e5b0_text
    # Future promotion requires separate PRs.
    assert "future explicit PR" in r2e5b0_text
    assert "separate" in r2e5b0_text


def test_doc_r2e5b0_reason_codes_and_schema_match_constants(r2e5b0_text: str) -> None:
    # Couple the documented preview codes / schema / fields to the live module.
    from investment_orchestrator.research.actionable_handoff_preview import (
        EXTENDED_ETF_SLEEVE_PREVIEW_ENABLED,
        GLOBAL_BASE_NEW_TICKER_CAP_ZERO,
        GLOBAL_NO_ACCEPTED_SUPPORT_SIGNALS,
        PREVIEW_REJECTION_REASON_CODES,
        SCHEMA_VERSION,
    )

    assert SCHEMA_VERSION in r2e5b0_text
    assert EXTENDED_ETF_SLEEVE_PREVIEW_ENABLED is False
    assert "extended_etf_sleeve_preview_enabled: false" in r2e5b0_text
    for code in PREVIEW_REJECTION_REASON_CODES:
        assert code in r2e5b0_text, code
    assert GLOBAL_NO_ACCEPTED_SUPPORT_SIGNALS in r2e5b0_text
    assert GLOBAL_BASE_NEW_TICKER_CAP_ZERO in r2e5b0_text
    # Required schema fields are documented.
    for field_name in (
        "preview_actionable_rows",
        "preview_positive_delta_research_supported",
        "rejected_preview_rows",
        "global_blockers",
        "max_new_tickers_per_week_snapshot",
        "actionability_status_preview",
    ):
        assert field_name in r2e5b0_text, field_name


# --- R2E.5b-1 status (§24): actionable compiled-handoff CANDIDATE ------------


@pytest.fixture(scope="module")
def r2e5b1_text(doc_text: str) -> str:
    marker = "## 24. R2E.5b-1 status"
    assert marker in doc_text, "missing R2E.5b-1 section (§24)"
    return doc_text[doc_text.index(marker) :]


def test_doc_records_r2e5b1_separate_and_not_authorization(r2e5b1_text: str) -> None:
    assert "CANDIDATE" in r2e5b1_text
    assert "validate_research_handoff" in r2e5b1_text
    # Validates the shape only; not authorization.
    assert "actionable handoff shape only" in r2e5b1_text
    assert "NOT authorization" in r2e5b1_text or "not authorization" in r2e5b1_text
    assert "No `NEW_BUY` / `ORDER_COMPILATION` permission is added" in r2e5b1_text
    assert "STRICT_FRESH_WITH_LLM_MEMO" in r2e5b1_text
    # Separate from the active compiled handoff, not consumed downstream.
    assert "separate" in r2e5b1_text
    assert "never overwrite or\n  change the active `compiled_research_handoff_candidate.json`" in r2e5b1_text or "never overwrite or change the active `compiled_research_handoff_candidate.json`" in r2e5b1_text
    assert "consumed_by_availability: false" in r2e5b1_text
    assert "consumed_by_step2: false" in r2e5b1_text
    # Active handoff / availability / gate / weekly unchanged.
    assert "positive_delta_research_supported=[]" in r2e5b1_text
    assert "primary_anchor_event_id=null" in r2e5b1_text
    assert "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE" in r2e5b1_text
    assert "Step 2 research gate still blocks" in r2e5b1_text
    assert "NO_TRADE" in r2e5b1_text
    # Future promotion requires separate PRs.
    assert "**separate**" in r2e5b1_text
    assert "future explicit PR" in r2e5b1_text


def test_doc_r2e5b1_artifact_names_and_schema_match_constants(r2e5b1_text: str) -> None:
    from investment_orchestrator.research.actionable_handoff_candidate import (
        CANDIDATE_SCHEMA_VERSION,
        METADATA_SCHEMA_VERSION,
    )
    from investment_orchestrator.workflow.step1_research import (
        ACTIONABLE_HANDOFF_CANDIDATE_FILENAME,
        ACTIONABLE_HANDOFF_METADATA_FILENAME,
        ACTIONABLE_HANDOFF_VALIDATION_FILENAME,
    )

    assert CANDIDATE_SCHEMA_VERSION in r2e5b1_text
    assert METADATA_SCHEMA_VERSION in r2e5b1_text
    for filename in (
        ACTIONABLE_HANDOFF_CANDIDATE_FILENAME,
        ACTIONABLE_HANDOFF_VALIDATION_FILENAME,
        ACTIONABLE_HANDOFF_METADATA_FILENAME,
    ):
        assert filename in r2e5b1_text, filename
    # Metadata fields documented.
    for field_name in (
        "preview_actionable_row_count",
        "candidate_actionable_row_count",
        "validation_passed",
        "used_active_compiled_handoff_as_base",
    ):
        assert field_name in r2e5b1_text, field_name


# --- R2E.5b-2 design (§25): promotion path, design-only ----------------------


@pytest.fixture(scope="module")
def r2e5b2_text(doc_text: str) -> str:
    marker = "## 25. R2E.5b-2 design"
    assert marker in doc_text, "missing R2E.5b-2 promotion design section (§25)"
    return doc_text[doc_text.index(marker) :]


def test_doc_r2e5b2_is_design_only_and_adds_no_permission(r2e5b2_text: str) -> None:
    assert "DESIGN / INSPECTION ONLY" in r2e5b2_text
    assert "### 25.9 Non-goals (R2E.5b-2)" in r2e5b2_text
    assert "no `NEW_BUY` / `ORDER_COMPILATION` permission is added" in r2e5b2_text
    assert "Nothing here is implemented in R2E.5b-2" in r2e5b2_text
    # The live non-actionable posture is restated.
    assert "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE" in r2e5b2_text
    assert "STRICT_FRESH_EVIDENCE_ONLY" in r2e5b2_text
    assert "HOLD / NO_TRADE" in r2e5b2_text


def test_doc_r2e5b2_preconditions_cover_required_checks(r2e5b2_text: str) -> None:
    assert "### 25.2 Promotion preconditions" in r2e5b2_text
    for required in (
        "evidence_packet_v1",
        "valid_anchor_count",
        "evidence_plus_memo",
        "accepted_support_signals",
        "preview_actionable_rows",
        "validation_passed",
        "candidate_actionable_row_count",
        "max_new_tickers_per_week",
        "allowed_buy_tickers",
        "optional_extended_etf_sleeve",
        "valid_until",
        "blocking",
        "sha256",
        "strategy_settings_hash",
        "hard_cap_open_orders_budget",
    ):
        assert required in r2e5b2_text, required
    # Fail-closed language is explicit.
    assert "fail closed" in r2e5b2_text.lower()


def test_doc_r2e5b2_precondition_hash_keys_match_live_constants(r2e5b2_text: str) -> None:
    # The settings-hash precondition must be defined over the live key set.
    from investment_orchestrator.state.last_good_research_handoff import (
        DECISION_RELEVANT_SETTINGS_KEYS,
    )

    assert "DECISION_RELEVANT_SETTINGS_KEYS" in r2e5b2_text
    for key in ("hard_cap_open_orders_budget", "max_new_tickers_per_week"):
        assert key in DECISION_RELEVANT_SETTINGS_KEYS
        assert key in r2e5b2_text, key


def test_doc_r2e5b2_artifact_strategy_recommends_pointer_not_overwrite(r2e5b2_text: str) -> None:
    assert "### 25.3 Promotion artifact strategy" in r2e5b2_text
    # All four options assessed; overwrite and direct-consumption rejected.
    assert "active_research_handoff_source.json" in r2e5b2_text
    assert "research_handoff_candidate_effective.json" in r2e5b2_text
    assert "**rejected**" in r2e5b2_text
    assert "**recommended core**" in r2e5b2_text
    # Fail-closed pointer-resolution rule.
    assert "falls back to the active non-actionable" in r2e5b2_text
    assert "promoted_compiled_actionable_handoff" in r2e5b2_text


def test_doc_r2e5b2_future_states_are_designed_not_implemented(r2e5b2_text: str) -> None:
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH,
        STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
        STRICT_FRESH_EVIDENCE_ONLY,
        STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE,
        _ALLOWED_ACTIONS_BY_STATE,
    )

    assert "STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES" in r2e5b2_text
    assert "STRICT_FRESH_COMPILED_ACTIONABLE" in r2e5b2_text
    # R2E.5b-5b implements only the pending-gates state, still HOLD/NO_TRADE.
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES] == (
        "HOLD",
        "NO_TRADE",
    )
    # The truly actionable state must remain absent.
    assert "STRICT_FRESH_COMPILED_ACTIONABLE" not in _ALLOWED_ACTIONS_BY_STATE
    # Existing non-actionable states remain HOLD/NO_TRADE.
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_EVIDENCE_ONLY] == ("HOLD", "NO_TRADE")
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE] == ("HOLD", "NO_TRADE")
    # STRICT_FRESH must not be reused; the doc says so explicitly.
    assert "Do not reuse `STRICT_FRESH`" in r2e5b2_text
    assert STRICT_FRESH == "STRICT_FRESH"
    # The superseded historical name is acknowledged, not adopted.
    assert "STRICT_FRESH_WITH_LLM_MEMO" in r2e5b2_text
    assert "superseded" in r2e5b2_text


def test_doc_r2e5b2_gate_design_names_both_live_gate_constants(r2e5b2_text: str) -> None:
    from investment_orchestrator.state.final_execution_safety_gate import (
        ACTIONABLE_REQUIRED_STATE as FINAL_STATE,
        NEW_BUY_ACTION,
        REQUIRED_ALLOWED_ACTION,
    )
    from investment_orchestrator.state.research_degraded_mode_gate import (
        ACTIONABLE_REQUIRED_STATE as STEP2_STATE,
        REQUIRED_ACTIONS,
    )

    # Both gates still hardcode STRICT_FRESH today (the design depends on this).
    assert STEP2_STATE == FINAL_STATE == "STRICT_FRESH"
    assert REQUIRED_ACTIONS == ("NEW_BUY", "ORDER_COMPILATION")
    assert REQUIRED_ALLOWED_ACTION == "ORDER_COMPILATION"
    assert NEW_BUY_ACTION == "NEW_BUY"
    assert "### 25.5 Gate change design" in r2e5b2_text
    assert "ACTIONABLE_ALLOWED_STATES" in r2e5b2_text
    # Promotion-present-but-closed blocker code + source verification.
    assert "promotion_present_but_gates_closed" in r2e5b2_text
    assert "step2_blocked_by_research_gate.json" in r2e5b2_text
    # Final-gate additions: budget context + promoted-ticker subset.
    assert "target_new_buy_budget_this_run" in r2e5b2_text
    assert "actionable_this_run_tickers" in r2e5b2_text


def test_doc_r2e5b2_last_good_design_excludes_report_only_candidate(r2e5b2_text: str) -> None:
    from investment_orchestrator.research.actionable_handoff_candidate import (
        CANDIDATE_SCHEMA_VERSION,
    )

    assert "### 25.6 Last-good writer" in r2e5b2_text
    assert CANDIDATE_SCHEMA_VERSION in r2e5b2_text  # rejected by schema_version
    assert "last_good_promoted_handoff.json" in r2e5b2_text
    assert "promoted_last_good_valid_until" in r2e5b2_text
    assert "re-checked at read time" in r2e5b2_text


def test_doc_r2e5b2_artifact_names_match_live_constants(r2e5b2_text: str) -> None:
    from investment_orchestrator.workflow.step1_research import (
        ACTIONABLE_HANDOFF_CANDIDATE_FILENAME,
        ACTIONABLE_HANDOFF_PREVIEW_FILENAME,
    )

    assert ACTIONABLE_HANDOFF_CANDIDATE_FILENAME in r2e5b2_text
    assert ACTIONABLE_HANDOFF_PREVIEW_FILENAME in r2e5b2_text
    # Permission-effect markers used by the chain are named.
    for marker in ("permission_effect", "not_authorization", "report_only"):
        assert marker in r2e5b2_text, marker


def test_doc_r2e5b2_pr_sequence_and_first_permission_change(r2e5b2_text: str) -> None:
    assert "### 25.8 Recommended PR sequence" in r2e5b2_text
    for pr in ("R2E.5b-3", "R2E.5b-4", "R2E.5b-5", "R2E.5b-6", "R2E.5b-7", "R2F"):
        assert pr in r2e5b2_text, pr
    # The first true permission change is explicitly identified as R2E.5b-6.
    assert "first true permission change" in r2e5b2_text
    assert "promotion-eligibility artifact" in r2e5b2_text


def test_doc_r2e5b2_has_risk_analysis(r2e5b2_text: str) -> None:
    assert "### 25.7 Risk analysis" in r2e5b2_text
    for risk_kw in ("stale", "overconfiden", "hash mismatch", "rollback", "observability", "budget"):
        assert risk_kw in r2e5b2_text.lower(), risk_kw


# --- R2E.5b-3 status (§26): promotion-eligibility checker, report-only --------


@pytest.fixture(scope="module")
def r2e5b3_text(doc_text: str) -> str:
    marker = "## 26. R2E.5b-3 status"
    assert marker in doc_text, "missing R2E.5b-3 section (§26)"
    return doc_text[doc_text.index(marker) :]


def test_doc_records_r2e5b3_report_only_no_promotion(r2e5b3_text: str) -> None:
    assert "It never promotes" in r2e5b3_text
    assert "No `NEW_BUY` /\n`ORDER_COMPILATION` permission is added" in r2e5b3_text or "No `NEW_BUY` / `ORDER_COMPILATION` permission is added" in r2e5b3_text
    # No pointer / no effective handoff / no gate opening.
    assert "active_research_handoff_source.json" in r2e5b3_text
    assert "research_handoff_candidate_effective.json" in r2e5b3_text
    assert "R2E.5b-4" in r2e5b3_text
    # Future states remain not enabled; live posture unchanged.
    assert "STRICT_FRESH_COMPILED_ACTIONABLE" in r2e5b3_text
    assert "STRICT_FRESH_WITH_LLM_MEMO" in r2e5b3_text
    assert "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE" in r2e5b3_text
    assert "HOLD / NO_TRADE" in r2e5b3_text
    # Not consumed anywhere.
    assert "consumed_by_availability: false" in r2e5b3_text
    assert "consumed_by_step2: false" in r2e5b3_text
    assert "consumed_by_gates: false" in r2e5b3_text


def test_doc_r2e5b3_artifact_and_schema_match_constants(r2e5b3_text: str) -> None:
    from investment_orchestrator.research.actionable_promotion_eligibility import (
        SCHEMA_VERSION as ELIGIBILITY_SCHEMA_VERSION,
    )
    from investment_orchestrator.workflow.step1_research import (
        ACTIONABLE_PROMOTION_ELIGIBILITY_FILENAME,
    )

    assert ACTIONABLE_PROMOTION_ELIGIBILITY_FILENAME in r2e5b3_text
    assert ELIGIBILITY_SCHEMA_VERSION in r2e5b3_text
    assert "src/investment_orchestrator/research/actionable_promotion_eligibility.py" in r2e5b3_text
    # Required artifact fields documented.
    for field_name in (
        "eligible_for_promotion",
        "promotion_blockers",
        "promotion_warnings",
        "hash_chain_valid",
        "earliest_anchor_valid_until",
        "promotion_expires_at",
        "actionable_this_run_tickers",
        "strategy_settings_hash",
    ):
        assert field_name in r2e5b3_text, field_name


def test_doc_r2e5b3_reason_codes_match_constants(r2e5b3_text: str) -> None:
    from investment_orchestrator.research.actionable_promotion_eligibility import (
        PROMOTION_BLOCKER_REASON_CODES,
        PROMOTION_WARNING_REASON_CODES,
    )

    for code in PROMOTION_BLOCKER_REASON_CODES:
        assert code in r2e5b3_text, code
    for code in PROMOTION_WARNING_REASON_CODES:
        assert code in r2e5b3_text, code


def test_doc_r2e5b3_actionable_state_still_not_implemented() -> None:
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
        _ALLOWED_ACTIONS_BY_STATE,
    )
    from investment_orchestrator.state.research_degraded_mode_gate import (
        ACTIONABLE_REQUIRED_STATE as STEP2_STATE,
    )
    from investment_orchestrator.state.final_execution_safety_gate import (
        ACTIONABLE_REQUIRED_STATE as FINAL_STATE,
    )

    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES] == (
        "HOLD",
        "NO_TRADE",
    )
    assert "STRICT_FRESH_COMPILED_ACTIONABLE" not in _ALLOWED_ACTIONS_BY_STATE
    # Both gates still hardcode STRICT_FRESH.
    assert STEP2_STATE == FINAL_STATE == "STRICT_FRESH"


# --- R2E.5b-4 status (§27): pointer preview + effective preview, report-only --


@pytest.fixture(scope="module")
def r2e5b4_text(doc_text: str) -> str:
    marker = "## 27. R2E.5b-4 status"
    assert marker in doc_text, "missing R2E.5b-4 section (§27)"
    return doc_text[doc_text.index(marker) :]


def test_doc_records_r2e5b4_preview_only_nothing_promoted(r2e5b4_text: str) -> None:
    assert "strictly diagnostic" in r2e5b4_text
    assert "No `NEW_BUY` / `ORDER_COMPILATION` permission is\nadded" in r2e5b4_text or "No `NEW_BUY` / `ORDER_COMPILATION` permission is added" in r2e5b4_text
    # Reserved real-promotion names documented as NOT created.
    assert "active_research_handoff_source.json" in r2e5b4_text
    assert "research_handoff_candidate_effective.json" in r2e5b4_text
    assert "NOT created" in r2e5b4_text
    assert "reserved" in r2e5b4_text
    # Future R2E.5b-5 explicitly named for the real pointer / pending-gates state.
    assert "R2E.5b-5" in r2e5b4_text
    assert "STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES" in r2e5b4_text
    # Live posture unchanged; previews unconsumed.
    assert "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE" in r2e5b4_text
    assert "HOLD / NO_TRADE" in r2e5b4_text
    assert "consumed_by_availability: false" in r2e5b4_text
    assert "consumed_by_step2: false" in r2e5b4_text
    assert "consumed_by_gates: false" in r2e5b4_text
    # Loud no-promotion markers.
    assert "active_pointer_created: false" in r2e5b4_text
    assert "effective_handoff_created: false" in r2e5b4_text
    assert "future_pr_required: true" in r2e5b4_text


def test_doc_r2e5b4_artifacts_and_schema_match_constants(r2e5b4_text: str) -> None:
    from investment_orchestrator.research.actionable_promotion_pointer_preview import (
        PROMOTION_SOURCE,
        RESERVED_ACTIVE_POINTER_PATH,
        RESERVED_EFFECTIVE_HANDOFF_PATH,
        SCHEMA_VERSION as POINTER_PREVIEW_SCHEMA_VERSION,
    )
    from investment_orchestrator.workflow.step1_research import (
        ACTIONABLE_EFFECTIVE_HANDOFF_PREVIEW_FILENAME,
        ACTIONABLE_EFFECTIVE_HANDOFF_PREVIEW_VALIDATION_FILENAME,
        ACTIONABLE_PROMOTION_POINTER_PREVIEW_FILENAME,
    )

    assert POINTER_PREVIEW_SCHEMA_VERSION in r2e5b4_text
    assert PROMOTION_SOURCE in r2e5b4_text
    assert RESERVED_ACTIVE_POINTER_PATH in r2e5b4_text
    assert RESERVED_EFFECTIVE_HANDOFF_PATH in r2e5b4_text
    for filename in (
        ACTIONABLE_PROMOTION_POINTER_PREVIEW_FILENAME,
        ACTIONABLE_EFFECTIVE_HANDOFF_PREVIEW_FILENAME,
        ACTIONABLE_EFFECTIVE_HANDOFF_PREVIEW_VALIDATION_FILENAME,
    ):
        assert filename in r2e5b4_text, filename
    assert "src/investment_orchestrator/research/actionable_promotion_pointer_preview.py" in r2e5b4_text
    # would_promote semantics + key fields documented.
    for field_name in (
        "would_promote",
        "candidate_sha256",
        "eligibility_sha256",
        "eligibility_hash",
        "promotion_expires_at",
        "source_chain_hashes",
        "pointer_blockers",
        "pointer_warnings",
    ):
        assert field_name in r2e5b4_text, field_name


def test_doc_r2e5b4_blocker_codes_match_constants(r2e5b4_text: str) -> None:
    from investment_orchestrator.research.actionable_promotion_pointer_preview import (
        POINTER_BLOCKER_REASON_CODES,
        POINTER_WARNING_REASON_CODES,
    )

    for code in POINTER_BLOCKER_REASON_CODES:
        assert code in r2e5b4_text, code
    for code in POINTER_WARNING_REASON_CODES:
        assert code in r2e5b4_text, code


def test_doc_active_pointer_name_known_only_to_promotion_chain_and_step2() -> None:
    # Since R2E.5b-5a the real pointer name IS created — but only the report-only
    # promotion chain (and, since R2E.5b-6c, the Step 2 decision-only render,
    # which re-verifies the pointer live) may know it. Step 3/4, weekly, and the
    # order compiler still never mention the filename.
    import subprocess

    result = subprocess.run(
        [
            "grep",
            "-rl",
            "--include=*.py",
            "active_research_handoff_source.json",
            "src/investment_orchestrator",
        ],
        capture_output=True,
        text=True,
        cwd=str(DOC_PATH.parents[1]),
    )
    files = sorted(line for line in result.stdout.splitlines() if line.strip())
    assert files == [
        "src/investment_orchestrator/research/actionable_promotion_eligibility.py",
        "src/investment_orchestrator/research/actionable_promotion_pointer.py",
        "src/investment_orchestrator/research/actionable_promotion_pointer_preview.py",
        "src/investment_orchestrator/workflow/step1_research.py",
        "src/investment_orchestrator/workflow/step2_decision_builder.py",
    ]


def test_doc_r2e5b4_actionable_state_still_not_implemented() -> None:
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
        _ALLOWED_ACTIONS_BY_STATE,
    )
    from investment_orchestrator.state.research_degraded_mode_gate import (
        ACTIONABLE_REQUIRED_STATE as STEP2_STATE,
        REQUIRED_ACTIONS,
    )
    from investment_orchestrator.state.final_execution_safety_gate import (
        ACTIONABLE_REQUIRED_STATE as FINAL_STATE,
    )

    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES] == (
        "HOLD",
        "NO_TRADE",
    )
    assert "STRICT_FRESH_COMPILED_ACTIONABLE" not in _ALLOWED_ACTIONS_BY_STATE
    assert STEP2_STATE == FINAL_STATE == "STRICT_FRESH"
    assert REQUIRED_ACTIONS == ("NEW_BUY", "ORDER_COMPILATION")


# --- R2E.5b-5a status (§28): real pointer writer, no consumers -----------------


@pytest.fixture(scope="module")
def r2e5b5a_text(doc_text: str) -> str:
    marker = "## 28. R2E.5b-5a status"
    assert marker in doc_text, "missing R2E.5b-5a section (§28)"
    return doc_text[doc_text.index(marker) :]


def test_doc_records_r2e5b5a_pending_gates_unconsumed(r2e5b5a_text: str) -> None:
    assert "pending-gates artifacts" in r2e5b5a_text
    assert "does not make them trading authority" in r2e5b5a_text
    assert "In 5a itself, nothing read the\npointer" in r2e5b5a_text or "In 5a itself, nothing read the pointer" in r2e5b5a_text
    assert "No `NEW_BUY` / `ORDER_COMPILATION` permission is added" in r2e5b5a_text
    # Pointer exists but is pending gates, not authorization.
    assert 'promotion_status: "pending_gates"' in r2e5b5a_text
    assert 'permission_effect: "none_until_consumed_by_future_gate_pr"' in r2e5b5a_text
    assert "not** trading authorization" in r2e5b5a_text or "not trading authorization" in r2e5b5a_text
    # 5b availability-recognition PR named.
    assert "R2E.5b-5b" in r2e5b5a_text
    assert "§29" in r2e5b5a_text
    # Live posture unchanged.
    assert "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE" in r2e5b5a_text
    assert "HOLD / NO_TRADE" in r2e5b5a_text
    assert "consumed_by_availability: false" in r2e5b5a_text
    assert "consumed_by_step2: false" in r2e5b5a_text
    assert "consumed_by_gates: false" in r2e5b5a_text


def test_doc_r2e5b5a_artifacts_and_constants_match(r2e5b5a_text: str) -> None:
    from investment_orchestrator.research.actionable_promotion_pointer import (
        PERMISSION_EFFECT_PENDING_GATES,
        POINTER_SOURCE,
        PROMOTION_STATUS_PENDING_GATES,
        SCHEMA_VERSION as POINTER_SCHEMA_VERSION,
        WRITE_STATUS_SCHEMA_VERSION,
    )
    from investment_orchestrator.workflow.step1_research import (
        ACTIVE_POINTER_WRITE_STATUS_FILENAME,
        ACTIVE_RESEARCH_HANDOFF_SOURCE_FILENAME,
        EFFECTIVE_RESEARCH_HANDOFF_FILENAME,
        EFFECTIVE_RESEARCH_HANDOFF_VALIDATION_FILENAME,
    )

    assert POINTER_SCHEMA_VERSION in r2e5b5a_text
    assert WRITE_STATUS_SCHEMA_VERSION in r2e5b5a_text
    assert PROMOTION_STATUS_PENDING_GATES == "pending_gates"
    assert PERMISSION_EFFECT_PENDING_GATES in r2e5b5a_text
    assert POINTER_SOURCE in r2e5b5a_text
    for filename in (
        ACTIVE_RESEARCH_HANDOFF_SOURCE_FILENAME,
        EFFECTIVE_RESEARCH_HANDOFF_FILENAME,
        EFFECTIVE_RESEARCH_HANDOFF_VALIDATION_FILENAME,
        ACTIVE_POINTER_WRITE_STATUS_FILENAME,
    ):
        assert filename in r2e5b5a_text, filename
    assert "src/investment_orchestrator/research/actionable_promotion_pointer.py" in r2e5b5a_text


def test_doc_r2e5b5a_blocker_codes_match_constants(r2e5b5a_text: str) -> None:
    from investment_orchestrator.research.actionable_promotion_pointer import (
        POINTER_WRITE_BLOCKER_REASON_CODES,
    )

    for code in POINTER_WRITE_BLOCKER_REASON_CODES:
        assert code in r2e5b5a_text, code


def test_doc_r2e5b5a_pending_state_hold_no_trade_and_gates_still_closed() -> None:
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
        STRICT_FRESH_EVIDENCE_ONLY,
        STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE,
        _ALLOWED_ACTIONS_BY_STATE,
    )
    from investment_orchestrator.state.research_degraded_mode_gate import (
        ACTIONABLE_REQUIRED_STATE as STEP2_STATE,
        REQUIRED_ACTIONS,
    )
    from investment_orchestrator.state.final_execution_safety_gate import (
        ACTIONABLE_REQUIRED_STATE as FINAL_STATE,
        REQUIRED_ALLOWED_ACTION,
    )

    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES] == (
        "HOLD",
        "NO_TRADE",
    )
    assert "STRICT_FRESH_COMPILED_ACTIONABLE" not in _ALLOWED_ACTIONS_BY_STATE
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_EVIDENCE_ONLY] == ("HOLD", "NO_TRADE")
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE] == ("HOLD", "NO_TRADE")
    assert STEP2_STATE == FINAL_STATE == "STRICT_FRESH"
    assert REQUIRED_ACTIONS == ("NEW_BUY", "ORDER_COMPILATION")
    assert REQUIRED_ALLOWED_ACTION == "ORDER_COMPILATION"


# --- R2E.5b-5b status (§29): pending-gates availability recognition ----------


@pytest.fixture(scope="module")
def r2e5b5b_text(doc_text: str) -> str:
    marker = "## 29. R2E.5b-5b status"
    assert marker in doc_text, "missing R2E.5b-5b section (§29)"
    return doc_text[doc_text.index(marker) :]


def test_doc_records_r2e5b5b_pending_gates_hold_no_trade(r2e5b5b_text: str) -> None:
    assert "STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES" in r2e5b5b_text
    assert '`["HOLD", "NO_TRADE"]`' in r2e5b5b_text
    assert "No production gate is\nopened" in r2e5b5b_text or "No production gate is opened" in r2e5b5b_text
    for action in ("SELL", "NEW_BUY", "ROTATION", "REBALANCE", "EXTENDED_ETF_ADMISSION", "ORDER_COMPILATION"):
        assert action in r2e5b5b_text, action
    assert "STRICT_FRESH_COMPILED_ACTIONABLE` remains absent" in r2e5b5b_text


def test_doc_r2e5b5b_state_matches_constant_permissions_and_gates(r2e5b5b_text: str) -> None:
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH,
        STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
        _ALLOWED_ACTIONS_BY_STATE,
    )
    from investment_orchestrator.state.research_degraded_mode_gate import (
        ACTIONABLE_REQUIRED_STATE as STEP2_STATE,
        REQUIRED_ACTIONS,
    )
    from investment_orchestrator.state.final_execution_safety_gate import (
        ACTIONABLE_REQUIRED_STATE as FINAL_STATE,
        REQUIRED_ALLOWED_ACTION,
    )

    assert STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES in r2e5b5b_text
    assert STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES != STRICT_FRESH
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES] == (
        "HOLD",
        "NO_TRADE",
    )
    assert "NEW_BUY" not in _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES]
    assert "ORDER_COMPILATION" not in _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES]
    assert "STRICT_FRESH_COMPILED_ACTIONABLE" not in _ALLOWED_ACTIONS_BY_STATE
    assert STEP2_STATE == FINAL_STATE == "STRICT_FRESH"
    assert REQUIRED_ACTIONS == ("NEW_BUY", "ORDER_COMPILATION")
    assert REQUIRED_ALLOWED_ACTION == "ORDER_COMPILATION"


def test_doc_r2e5b5b_recognition_criteria_and_artifact_fields(r2e5b5b_text: str) -> None:
    for required in (
        "active_research_handoff_source_v1",
        "pending_gates",
        "promoted_compiled_actionable_handoff",
        "not_authorization",
        "future_pr_required",
        "none_until_consumed_by_future_gate_pr",
        "consumed_by_availability",
        "consumed_by_step2",
        "consumed_by_gates",
        "promotion_expires_at",
        "candidate_actionable_row_count",
        "actionable_this_run_tickers",
        "promoted_pointer_present",
        "promoted_pointer_valid",
        "effective_handoff_valid",
        "source_artifacts.active_research_handoff_source",
        "source_artifacts.research_handoff_candidate_effective",
        "source_artifacts.research_handoff_candidate_effective_validation",
    ):
        assert required in r2e5b5b_text, required
    for reason in (
        "promoted_actionable_handoff_pending_gates",
        "new_buy_requires_future_gate_pr",
        "order_compilation_requires_future_gate_pr",
    ):
        assert reason in r2e5b5b_text, reason


# --- R2E.5b-6-design (§30): first permission-change design only --------------


@pytest.fixture(scope="module")
def r2e5b6_design_text(doc_text: str) -> str:
    marker = "## 30. R2E.5b-6-design"
    assert marker in doc_text, "missing R2E.5b-6-design section (§30)"
    return doc_text[doc_text.index(marker) :]


def test_doc_r2e5b6_design_only_no_live_permission_change(r2e5b6_design_text: str) -> None:
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
        _ALLOWED_ACTIONS_BY_STATE,
    )
    from investment_orchestrator.state.research_degraded_mode_gate import (
        ACTIONABLE_REQUIRED_STATE as STEP2_STATE,
        REQUIRED_ACTIONS,
    )
    from investment_orchestrator.state.final_execution_safety_gate import (
        ACTIONABLE_REQUIRED_STATE as FINAL_STATE,
        REQUIRED_ALLOWED_ACTION,
    )

    assert "DESIGN / INSPECTION ONLY" in r2e5b6_design_text
    assert "does not change `_ALLOWED_ACTIONS_BY_STATE`" in r2e5b6_design_text
    assert "does **not** add `NEW_BUY` / `ORDER_COMPILATION`" in r2e5b6_design_text
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES] == (
        "HOLD",
        "NO_TRADE",
    )
    assert "PROMOTED_RESEARCH_DECISION" not in _ALLOWED_ACTIONS_BY_STATE[
        STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES
    ]
    # The full order-eligible state remains absent. (The Step 2 decision-only
    # state designed here was later implemented by R2E.5b-6c — see the §33 tests.)
    assert "STRICT_FRESH_COMPILED_ACTIONABLE" not in _ALLOWED_ACTIONS_BY_STATE
    assert STEP2_STATE == FINAL_STATE == "STRICT_FRESH"
    assert REQUIRED_ACTIONS == ("NEW_BUY", "ORDER_COMPILATION")
    assert REQUIRED_ALLOWED_ACTION == "ORDER_COMPILATION"


def test_doc_r2e5b6_recommends_step2_only_boundary(r2e5b6_design_text: str) -> None:
    assert "Option A — Step 2 decision-only" in r2e5b6_design_text
    assert "**Recommend**" in r2e5b6_design_text
    assert "Do **not** use as first permission PR" in r2e5b6_design_text
    for distinction in (
        "Step 2 render / LLM decision",
        "Step 3 audit",
        "Step 4 order compilation",
        "Final execution safety gate",
    ):
        assert distinction in r2e5b6_design_text, distinction
    assert "Step 3/4 remain blocked" in r2e5b6_design_text
    assert "final execution safety gate still requires literal `STRICT_FRESH`" in r2e5b6_design_text


def test_doc_r2e5b6_state_action_model_avoids_order_permissions(
    r2e5b6_design_text: str,
) -> None:
    assert "PROMOTED_RESEARCH_DECISION" in r2e5b6_design_text
    assert "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY" in r2e5b6_design_text
    assert '["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]' in r2e5b6_design_text
    assert "Do **not** reuse `NEW_BUY` or `ORDER_COMPILATION`" in r2e5b6_design_text
    assert "Reserve `STRICT_FRESH_COMPILED_ACTIONABLE` for the full order-eligible state" in r2e5b6_design_text


def test_doc_r2e5b6_effective_handoff_source_rules_match_live_constants(
    r2e5b6_design_text: str,
) -> None:
    from investment_orchestrator.research.actionable_promotion_pointer import (
        PERMISSION_EFFECT_PENDING_GATES,
        POINTER_SOURCE,
        SCHEMA_VERSION as POINTER_SCHEMA_VERSION,
    )
    from investment_orchestrator.workflow.step1_research import (
        ACTIVE_RESEARCH_HANDOFF_SOURCE_FILENAME,
        EFFECTIVE_RESEARCH_HANDOFF_FILENAME,
        EFFECTIVE_RESEARCH_HANDOFF_VALIDATION_FILENAME,
    )

    assert POINTER_SCHEMA_VERSION in r2e5b6_design_text
    assert POINTER_SOURCE in r2e5b6_design_text
    assert PERMISSION_EFFECT_PENDING_GATES in r2e5b6_design_text
    for filename in (
        ACTIVE_RESEARCH_HANDOFF_SOURCE_FILENAME,
        EFFECTIVE_RESEARCH_HANDOFF_FILENAME,
        EFFECTIVE_RESEARCH_HANDOFF_VALIDATION_FILENAME,
    ):
        assert filename in r2e5b6_design_text, filename
    for required in (
        "recomputes the effective handoff sha256",
        "re-checks `promotion_expires_at`",
        "not raw Deep Research",
        "research_degraded_mode_decision.json` remains required",
        "actionable_this_run_tickers",
    ):
        assert required in r2e5b6_design_text, required


def test_doc_r2e5b6_weekly_last_good_and_sequence_are_conservative(
    r2e5b6_design_text: str,
) -> None:
    assert "NO_TRADE_PENDING_FINAL_GATES" in r2e5b6_design_text
    assert "run_weekly` should remain a controlled non-order terminal" in r2e5b6_design_text
    assert "Do **not** write the Step 2-only promoted handoff to the existing last-good slot" in r2e5b6_design_text
    assert "first true permission change is **R2E.5b-6c**" in r2e5b6_design_text
    assert "It must not add\n`NEW_BUY` or `ORDER_COMPILATION`" in r2e5b6_design_text or "It must not add `NEW_BUY` or `ORDER_COMPILATION`" in r2e5b6_design_text
    for pr in ("R2E.5b-6a", "R2E.5b-6b", "R2E.5b-6c", "R2E.5b-6d", "R2E.5b-7"):
        assert pr in r2e5b6_design_text, pr


# --- R2E.5b-6a status (§31): promoted handoff verifier helper only ----------


@pytest.fixture(scope="module")
def r2e5b6a_text(doc_text: str) -> str:
    marker = "## 31. R2E.5b-6a status"
    assert marker in doc_text, "missing R2E.5b-6a section (§31)"
    return doc_text[doc_text.index(marker) :]


def test_doc_r2e5b6a_records_verifier_api_and_schema(r2e5b6a_text: str) -> None:
    from investment_orchestrator.research.promoted_handoff_verifier import (
        FUTURE_PERMISSION_REQUIRED,
        SCHEMA_VERSION,
    )

    assert "src/investment_orchestrator/research/promoted_handoff_verifier.py" in r2e5b6a_text
    assert "verify_promoted_handoff_for_step2_decision" in r2e5b6a_text
    assert SCHEMA_VERSION in r2e5b6a_text
    assert FUTURE_PERMISSION_REQUIRED in r2e5b6a_text
    for field_name in (
        "valid_for_step2_decision",
        "verification_blockers",
        "verification_warnings",
        "checks",
        "effective_handoff_sha256",
        "pointer_effective_handoff_sha256",
        "report_only: true",
        "is_llm_generated: false",
    ):
        assert field_name in r2e5b6a_text, field_name


def test_doc_r2e5b6a_blocker_codes_match_verifier_contract(r2e5b6a_text: str) -> None:
    from investment_orchestrator.research.promoted_handoff_verifier import (
        VERIFICATION_BLOCKER_REASON_CODES,
    )

    for reason_code in VERIFICATION_BLOCKER_REASON_CODES:
        assert reason_code in r2e5b6a_text, reason_code


def test_doc_r2e5b6a_criteria_match_pointer_and_candidate_constants(r2e5b6a_text: str) -> None:
    from investment_orchestrator.research.actionable_handoff_candidate import (
        CANDIDATE_SCHEMA_VERSION,
    )
    from investment_orchestrator.research.actionable_promotion_pointer import (
        PERMISSION_EFFECT_PENDING_GATES,
        POINTER_SOURCE,
        PROMOTION_STATUS_PENDING_GATES,
        SCHEMA_VERSION as POINTER_SCHEMA_VERSION,
    )

    for value in (
        CANDIDATE_SCHEMA_VERSION,
        POINTER_SCHEMA_VERSION,
        POINTER_SOURCE,
        PROMOTION_STATUS_PENDING_GATES,
        PERMISSION_EFFECT_PENDING_GATES,
    ):
        assert value in r2e5b6a_text, value
    for required in (
        "promotion_expires_at",
        "candidate_actionable_row_count > 0",
        "actionable_this_run_tickers",
        "positive_delta_research_supported",
        "trade_universe.allowed_buy_tickers",
        "optional extended ETF sleeve remains disabled",
    ):
        assert required in r2e5b6a_text, required


def test_doc_r2e5b6a_no_behavior_change_permissions_or_gate_opening(
    r2e5b6a_text: str,
) -> None:
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
        _ALLOWED_ACTIONS_BY_STATE,
    )
    from investment_orchestrator.state.research_degraded_mode_gate import (
        ACTIONABLE_REQUIRED_STATE as STEP2_STATE,
        REQUIRED_ACTIONS,
    )
    from investment_orchestrator.state.final_execution_safety_gate import (
        ACTIONABLE_REQUIRED_STATE as FINAL_STATE,
        REQUIRED_ALLOWED_ACTION,
    )

    assert "no\nStep 1 report-only artifact is added" in r2e5b6a_text or "no Step 1 report-only artifact is added" in r2e5b6a_text
    assert "no workflow consumes the helper" in r2e5b6a_text
    assert "no production behavior\nchanges" in r2e5b6a_text or "no production behavior changes" in r2e5b6a_text
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES] == (
        "HOLD",
        "NO_TRADE",
    )
    assert "PROMOTED_RESEARCH_DECISION" not in _ALLOWED_ACTIONS_BY_STATE[
        STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES
    ]
    assert "NEW_BUY" not in _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES]
    assert "ORDER_COMPILATION" not in _ALLOWED_ACTIONS_BY_STATE[
        STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES
    ]
    assert STEP2_STATE == FINAL_STATE == "STRICT_FRESH"
    assert REQUIRED_ACTIONS == ("NEW_BUY", "ORDER_COMPILATION")
    assert REQUIRED_ALLOWED_ACTION == "ORDER_COMPILATION"
    assert "still blocks before Step 2 render" in r2e5b6a_text
    assert "R2E.5b-6b" in r2e5b6a_text


# --- R2E.5b-6b status (§32): promoted Step 2 gate dry-run, report-only -------


@pytest.fixture(scope="module")
def r2e5b6b_text(doc_text: str) -> str:
    marker = "## 32. R2E.5b-6b status"
    assert marker in doc_text, "missing R2E.5b-6b section (§32)"
    return doc_text[doc_text.index(marker) :]


def test_doc_r2e5b6b_records_dry_run_module_schema_and_artifacts(r2e5b6b_text: str) -> None:
    from investment_orchestrator.research.promoted_step2_gate_dry_run import (
        FUTURE_STATE_REQUIRED,
        SCHEMA_VERSION,
    )

    assert "src/investment_orchestrator/research/promoted_step2_gate_dry_run.py" in r2e5b6b_text
    assert "evaluate_promoted_step2_gate_dry_run" in r2e5b6b_text
    assert SCHEMA_VERSION in r2e5b6b_text
    assert FUTURE_STATE_REQUIRED in r2e5b6b_text
    assert (
        "artifacts/current/step1_research/promoted_step2_gate_dry_run.json" in r2e5b6b_text
    )
    assert (
        "artifacts/current/step1_research/promoted_handoff_step2_verification.json"
        in r2e5b6b_text
    )
    for field_name in (
        "would_allow_step2_promoted_decision",
        "current_real_gate_allows: false",
        'future_permission_required: "PROMOTED_RESEARCH_DECISION"',
        "current_state",
        "current_allowed_actions",
        "verification_valid_for_step2_decision",
        "dry_run_blockers[]",
        "dry_run_warnings[]",
        "checks[]",
        "consumed_by_step2: false",
        "consumed_by_gates: false",
        "report_only: true",
        "is_llm_generated: false",
        "dry_run_only: true",
        'permission_effect: "none"',
        "not_authorization: true",
    ):
        assert field_name in r2e5b6b_text, field_name


def test_doc_r2e5b6b_blocker_codes_match_dry_run_contract(r2e5b6b_text: str) -> None:
    from investment_orchestrator.research.promoted_step2_gate_dry_run import (
        DRY_RUN_BLOCKER_REASON_CODES,
    )

    for reason_code in DRY_RUN_BLOCKER_REASON_CODES:
        assert reason_code in r2e5b6b_text, reason_code


def test_doc_r2e5b6b_dry_run_true_is_not_permission_and_gates_stay_closed(
    r2e5b6b_text: str,
) -> None:
    normalized = r2e5b6b_text.replace("\n", " ")
    assert "diagnostic only — it is NOT permission" in r2e5b6b_text
    assert "Step 2 gate was unchanged and remained closed" in normalized
    assert "No `PROMOTED_RESEARCH_DECISION` permission was added by R2E.5b-6b" in normalized
    assert "R2E.5b-6c was the designated first true permission change" in normalized
    assert "has since been implemented — see §33" in normalized
    assert "real_gate_still_closed_by_policy" in r2e5b6b_text
    assert "never be read as an actual Step 2 render permission" in r2e5b6b_text


def test_doc_r2e5b6b_era_invariants_now_reflect_6c_decision_only() -> None:
    from investment_orchestrator.research.promoted_step2_gate_dry_run import (
        FUTURE_STATE_REQUIRED,
    )
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES,
        STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY,
        STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY,
        _ALLOWED_ACTIONS_BY_STATE,
    )
    from investment_orchestrator.state.research_degraded_mode_gate import (
        ACTIONABLE_REQUIRED_STATE as STEP2_STATE,
        REQUIRED_ACTIONS,
    )
    from investment_orchestrator.state.final_execution_safety_gate import (
        ACTIONABLE_REQUIRED_STATE as FINAL_STATE,
        REQUIRED_ALLOWED_ACTION,
    )

    # Pending-gates state is still HOLD / NO_TRADE only.
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES] == (
        "HOLD",
        "NO_TRADE",
    )
    # R2E.5b-6c implemented the dry-run's target state; R2E.5b-6f adds only the
    # promoted audit-only state. No unrelated state gets promoted actions.
    assert FUTURE_STATE_REQUIRED == STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY
    assert FUTURE_STATE_REQUIRED in _ALLOWED_ACTIONS_BY_STATE
    for state, actions in _ALLOWED_ACTIONS_BY_STATE.items():
        if state == FUTURE_STATE_REQUIRED:
            assert actions == ("HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION")
        elif state == STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY:
            assert actions == (
                "HOLD",
                "NO_TRADE",
                "PROMOTED_RESEARCH_DECISION",
                "PROMOTED_RESEARCH_AUDIT",
            )
            assert "NEW_BUY" not in actions
            assert "ORDER_COMPILATION" not in actions
        else:
            assert "PROMOTED_RESEARCH_DECISION" not in actions, state
            assert "PROMOTED_RESEARCH_AUDIT" not in actions, state
    # The LEGACY Step 2 gate path still requires literal STRICT_FRESH + both
    # order actions (the promoted decision-only path is separate and additive).
    assert STEP2_STATE == "STRICT_FRESH"
    assert REQUIRED_ACTIONS == ("NEW_BUY", "ORDER_COMPILATION")
    # The final execution safety gate is unchanged.
    assert FINAL_STATE == "STRICT_FRESH"
    assert REQUIRED_ALLOWED_ACTION == "ORDER_COMPILATION"


# --- R2E.5b-6c status (§33): first true permission change (Step 2 decision-only)


@pytest.fixture(scope="module")
def r2e5b6c_text(doc_text: str) -> str:
    marker = "## 33. R2E.5b-6c status"
    assert marker in doc_text, "missing R2E.5b-6c section (§33)"
    return doc_text[doc_text.index(marker) :]


def test_doc_r2e5b6c_first_permission_change_is_step2_decision_only(r2e5b6c_text: str) -> None:
    assert "FIRST TRUE PERMISSION CHANGE" in r2e5b6c_text
    assert "first true permission change in the R2E.5b series" in r2e5b6c_text
    assert (
        "Step 2 may render and parse a research decision from the promoted effective handoff"
        in r2e5b6c_text
    )
    assert "No `NEW_BUY` permission is added" in r2e5b6c_text
    assert "No `ORDER_COMPILATION` permission is added" in r2e5b6c_text
    assert "Step 3 audit, Step 4 order compilation, and the order path stay blocked" in r2e5b6c_text
    normalized = r2e5b6c_text.replace("\n", " ")
    assert "final execution safety gate, which is unchanged" in normalized
    assert "STRICT_FRESH_COMPILED_ACTIONABLE` state remains absent / non-enabled" in normalized
    assert "Raw `STRICT_FRESH` behavior is unchanged" in r2e5b6c_text


def test_doc_r2e5b6c_state_action_and_table_match_constants(r2e5b6c_text: str) -> None:
    from investment_orchestrator.state.research_availability import (
        PERMISSION_EFFECT_STEP2_DECISION_ONLY,
        PROMOTED_RESEARCH_DECISION_ACTION,
        STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY,
        _ALLOWED_ACTIONS_BY_STATE,
    )

    assert STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY in r2e5b6c_text
    assert PROMOTED_RESEARCH_DECISION_ACTION in r2e5b6c_text
    assert PERMISSION_EFFECT_STEP2_DECISION_ONLY in r2e5b6c_text
    # Doc statement matches the live mapping exactly.
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY] == (
        "HOLD",
        "NO_TRADE",
        PROMOTED_RESEARCH_DECISION_ACTION,
    )
    assert '("HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION")' in r2e5b6c_text
    for blocked in ("SELL", "NEW_BUY", "ROTATION", "REBALANCE", "EXTENDED_ETF_ADMISSION", "ORDER_COMPILATION"):
        assert blocked in r2e5b6c_text
    for reason in (
        "promoted_step2_decision_only_enabled",
        "new_buy_requires_future_gate_pr",
        "order_compilation_requires_future_gate_pr",
        "final_execution_requires_future_gate_pr",
    ):
        assert reason in r2e5b6c_text
    for field_name in (
        "promoted_step2_decision_only: true",
        "order_compilation_allowed: false",
        "new_buy_permission: false",
        "not_authorization: true",
        "is_llm_generated: false",
    ):
        assert field_name in r2e5b6c_text


def test_doc_r2e5b6c_upgrade_criteria_match_evaluator_contract(r2e5b6c_text: str) -> None:
    from investment_orchestrator.research.promoted_handoff_verifier import (
        SCHEMA_VERSION as VERIFICATION_SCHEMA,
    )
    from investment_orchestrator.research.promoted_step2_gate_dry_run import (
        DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY,
        SCHEMA_VERSION as DRY_RUN_SCHEMA,
    )

    assert VERIFICATION_SCHEMA in r2e5b6c_text
    assert DRY_RUN_SCHEMA in r2e5b6c_text
    assert DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY in r2e5b6c_text
    for criterion in (
        "would_allow_step2_promoted_decision: true",
        "current_real_gate_allows: false",
        "valid_for_step2_decision: true",
        "consumed_by_step2: false",
        "hash re-check",
        "_step2_decision_only_upgrade_ok",
    ):
        assert criterion in r2e5b6c_text
    normalized = r2e5b6c_text.replace("\n", " ")
    assert "keeps the run at pending-gates HOLD / NO_TRADE" in normalized
    assert "Raw `STRICT_FRESH` is never upgraded or altered" in r2e5b6c_text


def test_doc_r2e5b6c_gate_modes_and_step2_source_match_constants(r2e5b6c_text: str) -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        MODE_PROMOTED_STEP2_DECISION_ONLY,
        MODE_STRICT_FRESH_ACTIONABLE,
        NO_TRADE_PENDING_FINAL_GATES,
        PROMOTED_SOURCE,
    )
    from investment_orchestrator.workflow.step2_decision_builder import (
        PROMOTED_VERIFICATION_FAILED_REASON,
        STEP2_PROMOTED_DECISION_ONLY_FILENAME,
        STEP2_PROMOTED_DECISION_ONLY_SCHEMA_VERSION,
    )

    assert MODE_PROMOTED_STEP2_DECISION_ONLY in r2e5b6c_text
    assert MODE_STRICT_FRESH_ACTIONABLE in r2e5b6c_text
    assert NO_TRADE_PENDING_FINAL_GATES in r2e5b6c_text
    assert PROMOTED_SOURCE in r2e5b6c_text
    assert STEP2_PROMOTED_DECISION_ONLY_FILENAME in r2e5b6c_text
    assert STEP2_PROMOTED_DECISION_ONLY_SCHEMA_VERSION in r2e5b6c_text
    assert PROMOTED_VERIFICATION_FAILED_REASON in r2e5b6c_text
    assert "research_handoff_candidate_effective.json" in r2e5b6c_text
    normalized = r2e5b6c_text.replace("\n", " ")
    assert "never from the raw Deep Research `research_output.json`" in normalized
    assert "re-runs `verify_promoted_handoff_for_step2_decision` live at render time" in normalized
    assert "The legacy conditions are not loosened" in r2e5b6c_text


def test_doc_r2e5b6c_step34_blocking_and_weekly_match_constants(r2e5b6c_text: str) -> None:
    from investment_orchestrator.workflow.step3_audit_engine import (
        PROMOTED_DECISION_ONLY_NO_AUDIT_REASON,
        STEP3_BLOCKED_BY_PROMOTED_DECISION_ONLY_GATE_FILENAME,
    )
    from investment_orchestrator.workflow.weekly_orchestrator import (
        PROMOTED_DECISION_ONLY_TERMINAL_REASON,
        TERMINAL_NO_TRADE_PENDING_FINAL_GATES,
    )

    assert STEP3_BLOCKED_BY_PROMOTED_DECISION_ONLY_GATE_FILENAME in r2e5b6c_text
    assert PROMOTED_DECISION_ONLY_NO_AUDIT_REASON in r2e5b6c_text
    assert TERMINAL_NO_TRADE_PENDING_FINAL_GATES in r2e5b6c_text
    assert PROMOTED_DECISION_ONLY_TERMINAL_REASON in r2e5b6c_text
    normalized = r2e5b6c_text.replace("\n", " ")
    assert "WITHOUT auto-running Step 2" in normalized
    assert "R2E.5b-6d" in r2e5b6c_text and "R2E.5b-7" in r2e5b6c_text
    assert "no promoted last-good slot is created" in normalized


def test_doc_r2e5b6c_final_gate_still_requires_strict_fresh_and_order_compilation() -> None:
    from investment_orchestrator.state.final_execution_safety_gate import (
        ACTIONABLE_REQUIRED_STATE,
        REQUIRED_ALLOWED_ACTION,
    )
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY,
        _ALLOWED_ACTIONS_BY_STATE,
    )

    assert ACTIONABLE_REQUIRED_STATE == "STRICT_FRESH"
    assert REQUIRED_ALLOWED_ACTION == "ORDER_COMPILATION"
    # ORDER_COMPILATION is absent from the decision-only state, so the unchanged
    # final gate can never pass for it.
    assert "ORDER_COMPILATION" not in _ALLOWED_ACTIONS_BY_STATE[
        STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY
    ]
    assert "NEW_BUY" not in _ALLOWED_ACTIONS_BY_STATE[
        STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY
    ]
