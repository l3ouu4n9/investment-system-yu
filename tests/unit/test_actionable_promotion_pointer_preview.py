"""Unit tests for the R2E.5b-4 promotion pointer PREVIEW (report-only, no promotion).

The pointer preview shows what the future active-pointer promotion WOULD look
like. It never promotes: the reserved `active_research_handoff_source.json` /
`research_handoff_candidate_effective.json` names are never written, no consumer
reads the previews, and no permission changes. These tests build the *real*
chain (anchors → signals → preview → candidate → validation → metadata →
eligibility) so the pointer verdict — including the candidate-hash re-check —
is faithful to what Step 1 writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from investment_orchestrator.research.actionable_handoff_candidate import (
    build_actionable_handoff_candidate,
    build_actionable_handoff_metadata,
)
from investment_orchestrator.research.actionable_handoff_preview import (
    build_actionable_handoff_preview,
)
from investment_orchestrator.research.actionable_promotion_eligibility import (
    evaluate_actionable_handoff_promotion_eligibility,
)
from investment_orchestrator.research.actionable_promotion_pointer_preview import (
    POINTER_BLOCKER_CANDIDATE_EXPIRED,
    POINTER_BLOCKER_CANDIDATE_HASH_MISMATCH,
    POINTER_BLOCKER_CANDIDATE_MISSING,
    POINTER_BLOCKER_CANDIDATE_VALIDATION_FAILED,
    POINTER_BLOCKER_ELIGIBILITY_MALFORMED,
    POINTER_BLOCKER_ELIGIBILITY_MISSING,
    POINTER_BLOCKER_ELIGIBILITY_NOT_ELIGIBLE,
    POINTER_BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS,
    POINTER_BLOCKER_PERMISSION_MARKERS_INVALID,
    POINTER_BLOCKER_SOURCE_CHAIN_MISSING,
    POINTER_WARNING_ACTIVE_BASE_UNVERIFIED,
    PROMOTION_SOURCE,
    RESERVED_ACTIVE_POINTER_PATH,
    RESERVED_EFFECTIVE_HANDOFF_PATH,
    SCHEMA_VERSION,
    build_actionable_promotion_pointer_preview,
    write_actionable_promotion_pointer_preview,
)
from investment_orchestrator.research.research_anchors import (
    summarize_research_anchors,
    validate_research_anchors,
)
from investment_orchestrator.research.support_signals import build_compiled_support_signals
from investment_orchestrator.state.last_good_research_handoff import (
    decision_relevant_settings,
    strategy_settings_hash,
)
from investment_orchestrator.validators.validate_research_handoff import (
    research_handoff_validation_result_to_dict,
    validate_research_handoff,
)


TODAY = "2026-06-28"
_MODE = "evidence_plus_memo"


# --- builders (real chain, mirrors test_actionable_promotion_eligibility) -----


def settings(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "as_of": TODAY,
        "benchmark": "QQQ",
        "core_universe": ["QQQ", "VOO"],
        "satellite_universe": ["SMH"],
        "user_approved_extended_etf_static_list": ["GRID"],
        "max_new_tickers_per_week": {
            "base_universe_new_tickers_per_week": 2,
            "extended_etf_sleeve_new_tickers_per_week": 2,
        },
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.0,
        "ticker_role_fallback": {
            "QQQ": "benchmark_carrier_core",
            "VOO": "diversified_core_buffer",
            "SMH": "sector_alpha_tilt",
        },
    }
    base.update(overrides)
    return base


def anchors_summary(*, valid_until: str = "2026-07-31") -> dict[str, Any]:
    payload = {
        "schema_version": "research_anchors_v1",
        "as_of_date": TODAY,
        "is_llm_generated": False,
        "anchors": [
            {
                "anchor_id": "AI_CAPEX_2026H2",
                "anchor_type": "structural_theme",
                "applicable_tickers": ["QQQ"],
                "anchor_date_et": "2026-06-15",
                "valid_from": "2026-06-01",
                "valid_until": valid_until,
                "source_type": "operator",
                "confidence_floor": "medium",
            }
        ],
    }
    result = validate_research_anchors(payload, allowed_universe=["QQQ", "VOO", "SMH"], today=TODAY)
    return summarize_research_anchors(result)


def evidence_packet(stgs: dict[str, Any], *, valid_until: str = "2026-07-31") -> dict[str, Any]:
    return {
        "schema_version": "evidence_packet_v1",
        "is_llm_generated": False,
        "strategy_settings_hash": strategy_settings_hash(decision_relevant_settings(stgs)),
        "universe": {
            "core_universe": ["QQQ", "VOO"],
            "satellite_universe": ["SMH"],
            "approved_extended_etf": ["GRID"],
            "allowed_buy_tickers": ["QQQ", "VOO", "SMH"],
        },
        "budget_settings": {
            "hard_cap_open_orders_budget": stgs.get("hard_cap_open_orders_budget"),
            "target_new_buy_budget_this_run": stgs.get("target_new_buy_budget_this_run"),
            "max_new_tickers_per_week": stgs.get("max_new_tickers_per_week"),
        },
        "research_anchors": anchors_summary(valid_until=valid_until),
        "data_gaps": [],
        "report_only": True,
    }


def memo() -> dict[str, Any]:
    return {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "as_of_date": TODAY,
        "regime_view": "constructive",
        "confidence": "high",
        "ticker_relative_view": [
            {
                "ticker": "QQQ",
                "stance": "prefer",
                "rationale_12m_plus": "AI capex structural growth",
                "anchor_id_refs": ["AI_CAPEX_2026H2"],
            }
        ],
        "avoid_or_deprioritize": [],
        "data_gaps": [],
        "source_notes": [{"claim": "AI capex", "source": "10-K", "source_quality": "official"}],
    }


def chain(*, valid_until: str = "2026-07-31", m: dict[str, Any] | None = None) -> dict[str, Any]:
    """Full real chain up to and including the eligibility verdict."""
    stgs = settings()
    packet = evidence_packet(stgs, valid_until=valid_until)
    m = m if m is not None else memo()
    signals = build_compiled_support_signals(evidence_packet=packet, analyst_memo=m, compilation_mode=_MODE)
    preview = build_actionable_handoff_preview(
        evidence_packet=packet, analyst_memo=m, compiled_support_signals=signals
    )
    candidate = build_actionable_handoff_candidate(
        evidence_packet=packet,
        analyst_memo=m,
        actionable_handoff_preview=preview,
        base_candidate=None,
        strategy_settings=stgs,
    )
    validation = research_handoff_validation_result_to_dict(
        validate_research_handoff(candidate, strategy_settings=stgs)
    )
    metadata = build_actionable_handoff_metadata(
        candidate=candidate,
        validation=validation,
        actionable_handoff_preview=preview,
        compiled_support_signals=signals,
        evidence_packet=packet,
        base_candidate=None,
        used_active_compiled_handoff_as_base=False,
    )
    eligibility = evaluate_actionable_handoff_promotion_eligibility(
        evidence_packet=packet,
        compiled_support_signals=signals,
        actionable_preview=preview,
        actionable_candidate=candidate,
        actionable_candidate_validation=validation,
        actionable_candidate_metadata=metadata,
        strategy_settings=stgs,
        today=TODAY,
    )
    return {
        "eligibility": eligibility,
        "actionable_candidate": candidate,
        "actionable_candidate_validation": validation,
        "actionable_candidate_metadata": metadata,
        "strategy_settings": stgs,
    }


def build(inputs: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "eligibility": inputs["eligibility"],
        "actionable_candidate": inputs["actionable_candidate"],
        "actionable_candidate_validation": inputs["actionable_candidate_validation"],
        "actionable_candidate_metadata": inputs["actionable_candidate_metadata"],
        "today": TODAY,
    }
    kwargs.update(overrides)
    return build_actionable_promotion_pointer_preview(**kwargs)


# --- happy path ----------------------------------------------------------------


def test_eligible_chain_would_promote() -> None:
    preview = build(chain())
    assert preview["pointer_blockers"] == []
    assert preview["would_promote"] is True
    assert preview["promotion_source"] == PROMOTION_SOURCE
    assert preview["candidate_actionable_row_count"] == 1
    assert preview["actionable_this_run_tickers"] == ["QQQ"]
    assert preview["candidate_validation_passed"] is True
    assert preview["earliest_anchor_valid_until"] == "2026-07-31"
    assert preview["promotion_expires_at"] == "2026-07-31"


def test_would_promote_true_is_still_diagnostic_only() -> None:
    preview = build(chain())
    assert preview["schema_version"] == SCHEMA_VERSION
    assert preview["is_llm_generated"] is False
    assert preview["report_only"] is True
    assert preview["permission_effect"] == "none"
    assert preview["not_authorization"] is True
    # Loud no-promotion markers.
    assert preview["active_pointer_created"] is False
    assert preview["effective_handoff_created"] is False
    assert preview["future_pr_required"] is True
    assert preview["consumed_by_availability"] is False
    assert preview["consumed_by_step2"] is False
    assert preview["consumed_by_gates"] is False
    # Reserved names documented, never written by this module.
    assert preview["reserved_active_pointer_path"] == RESERVED_ACTIVE_POINTER_PATH
    assert preview["reserved_effective_handoff_path"] == RESERVED_EFFECTIVE_HANDOFF_PATH


def test_candidate_and_eligibility_hashes_recorded() -> None:
    inputs = chain()
    preview = build(inputs)
    assert preview["candidate_sha256"] == inputs["eligibility"]["candidate_sha256"]
    assert isinstance(preview["eligibility_sha256"], str) and preview["eligibility_sha256"]
    assert preview["eligibility_hash"] == preview["eligibility_sha256"]
    assert preview["source_chain_hashes"]["evidence_packet"]["match"] is True
    # Recompiled base is carried through as an explicit warning, not a blocker.
    assert POINTER_WARNING_ACTIVE_BASE_UNVERIFIED in preview["pointer_warnings"]


# --- fail closed ------------------------------------------------------------------


def test_ineligible_chain_does_not_promote() -> None:
    m = memo()
    m["ticker_relative_view"][0]["anchor_id_refs"] = ["DOES_NOT_EXIST"]
    preview = build(chain(m=m))
    assert preview["would_promote"] is False
    assert POINTER_BLOCKER_ELIGIBILITY_NOT_ELIGIBLE in preview["pointer_blockers"]
    assert POINTER_BLOCKER_NO_CANDIDATE_ACTIONABLE_ROWS in preview["pointer_blockers"]


def test_missing_eligibility_fails_closed() -> None:
    preview = build(chain(), eligibility=None)
    assert preview["would_promote"] is False
    assert POINTER_BLOCKER_ELIGIBILITY_MISSING in preview["pointer_blockers"]


def test_malformed_eligibility_fails_closed() -> None:
    inputs = chain()
    bad = dict(inputs["eligibility"])
    bad["schema_version"] = "some_other_schema_v9"
    preview = build(inputs, eligibility=bad)
    assert preview["would_promote"] is False
    assert POINTER_BLOCKER_ELIGIBILITY_MALFORMED in preview["pointer_blockers"]


def test_permission_markers_invalid_fails_closed() -> None:
    inputs = chain()
    tainted = dict(inputs["eligibility"])
    tainted["report_only"] = False
    preview = build(inputs, eligibility=tainted)
    assert preview["would_promote"] is False
    assert POINTER_BLOCKER_PERMISSION_MARKERS_INVALID in preview["pointer_blockers"]


def test_candidate_permission_markers_invalid_fails_closed() -> None:
    inputs = chain()
    tainted = json.loads(json.dumps(inputs["actionable_candidate"]))
    tainted["not_authorization"] = False
    preview = build(inputs, actionable_candidate=tainted)
    assert preview["would_promote"] is False
    assert POINTER_BLOCKER_PERMISSION_MARKERS_INVALID in preview["pointer_blockers"]
    # A marker-tainted candidate also no longer matches the approved hash.
    assert POINTER_BLOCKER_CANDIDATE_HASH_MISMATCH in preview["pointer_blockers"]


def test_missing_candidate_fails_closed() -> None:
    preview = build(chain(), actionable_candidate=None)
    assert preview["would_promote"] is False
    assert POINTER_BLOCKER_CANDIDATE_MISSING in preview["pointer_blockers"]


def test_mutated_candidate_fails_hash_check() -> None:
    inputs = chain()
    mutated = json.loads(json.dumps(inputs["actionable_candidate"]))
    for row in mutated["buy_universe_scorecard"]:
        if row["actionability_status"] == "actionable_this_run":
            row["thesis_12m_plus_summary"] = "quietly edited after eligibility"
    preview = build(inputs, actionable_candidate=mutated)
    assert preview["would_promote"] is False
    assert POINTER_BLOCKER_CANDIDATE_HASH_MISMATCH in preview["pointer_blockers"]


def test_failed_validation_fails_closed() -> None:
    preview = build(chain(), actionable_candidate_validation={"valid": False})
    assert preview["would_promote"] is False
    assert POINTER_BLOCKER_CANDIDATE_VALIDATION_FAILED in preview["pointer_blockers"]


def test_expired_promotion_fails_closed() -> None:
    # Eligibility computed on TODAY, pointer preview evaluated after the anchor
    # window ended → expired, fail closed.
    preview = build(chain(valid_until="2026-06-29"), today="2026-06-30")
    assert preview["would_promote"] is False
    assert POINTER_BLOCKER_CANDIDATE_EXPIRED in preview["pointer_blockers"]


def test_unknown_today_falls_back_to_eligibility_today() -> None:
    preview = build(chain(), today=None)
    assert preview["today"] == TODAY
    assert preview["would_promote"] is True


def test_missing_source_chain_fails_closed() -> None:
    inputs = chain()
    gutted = dict(inputs["eligibility"])
    gutted.pop("source_hashes")
    preview = build(inputs, eligibility=gutted)
    assert preview["would_promote"] is False
    assert POINTER_BLOCKER_SOURCE_CHAIN_MISSING in preview["pointer_blockers"]


def test_never_raises_on_all_none_inputs() -> None:
    preview = build_actionable_promotion_pointer_preview(
        eligibility=None,
        actionable_candidate=None,
        actionable_candidate_validation=None,
        actionable_candidate_metadata=None,
    )
    assert preview["would_promote"] is False
    assert preview["schema_version"] == SCHEMA_VERSION
    assert preview["not_authorization"] is True
    assert preview["future_pr_required"] is True
    assert POINTER_BLOCKER_ELIGIBILITY_MISSING in preview["pointer_blockers"]
    assert POINTER_BLOCKER_CANDIDATE_MISSING in preview["pointer_blockers"]


# --- disk wrapper: effective preview ------------------------------------------------


def test_would_promote_writes_effective_preview_and_validation(tmp_path: Path) -> None:
    inputs = chain()
    pointer_path = tmp_path / "compiled_actionable_handoff_promotion_pointer_preview.json"
    effective_path = tmp_path / "compiled_actionable_research_handoff_effective_preview.json"
    effective_validation_path = (
        tmp_path / "compiled_actionable_research_handoff_effective_preview_validation.json"
    )
    result = write_actionable_promotion_pointer_preview(
        pointer_preview_path=pointer_path,
        effective_preview_path=effective_path,
        effective_preview_validation_path=effective_validation_path,
        eligibility=inputs["eligibility"],
        actionable_candidate=inputs["actionable_candidate"],
        actionable_candidate_validation=inputs["actionable_candidate_validation"],
        actionable_candidate_metadata=inputs["actionable_candidate_metadata"],
        strategy_settings=inputs["strategy_settings"],
        today=TODAY,
    )
    assert result["would_promote"] == "True"
    assert result["effective_preview_written"] == "True"
    assert pointer_path.is_file() and effective_path.is_file() and effective_validation_path.is_file()

    # The effective preview is the candidate, byte-for-byte unmutated.
    effective = json.loads(effective_path.read_text(encoding="utf-8"))
    assert effective == json.loads(json.dumps(inputs["actionable_candidate"]))
    # It re-validates with the existing strict validator.
    validation = json.loads(effective_validation_path.read_text(encoding="utf-8"))
    assert validation["valid"] is True
    assert (
        validate_research_handoff(effective, strategy_settings=inputs["strategy_settings"]).valid
        is True
    )
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["effective_preview_written"] is True
    assert pointer["effective_preview_valid"] is True
    assert pointer["active_pointer_created"] is False
    assert pointer["effective_handoff_created"] is False

    # The reserved REAL promotion names are never created.
    assert not (tmp_path / "active_research_handoff_source.json").exists()
    assert not (tmp_path / "research_handoff_candidate_effective.json").exists()


def test_not_promotable_writes_pointer_preview_only(tmp_path: Path) -> None:
    m = memo()
    m["ticker_relative_view"][0]["anchor_id_refs"] = ["DOES_NOT_EXIST"]
    inputs = chain(m=m)
    pointer_path = tmp_path / "pointer_preview.json"
    effective_path = tmp_path / "effective_preview.json"
    effective_validation_path = tmp_path / "effective_preview_validation.json"
    result = write_actionable_promotion_pointer_preview(
        pointer_preview_path=pointer_path,
        effective_preview_path=effective_path,
        effective_preview_validation_path=effective_validation_path,
        eligibility=inputs["eligibility"],
        actionable_candidate=inputs["actionable_candidate"],
        actionable_candidate_validation=inputs["actionable_candidate_validation"],
        actionable_candidate_metadata=inputs["actionable_candidate_metadata"],
        strategy_settings=inputs["strategy_settings"],
        today=TODAY,
    )
    assert result["would_promote"] == "False"
    assert result["effective_preview_written"] == "False"
    assert result["actionable_effective_handoff_preview_path"] == ""
    assert pointer_path.is_file()
    assert not effective_path.exists()
    assert not effective_validation_path.exists()
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["would_promote"] is False
    assert pointer["pointer_blockers"]
    assert pointer["effective_preview_written"] is False
    assert pointer["effective_preview_path"] is None
