"""Unit tests for the R2E.5b-5a REAL active-pointer writer (artifacts only, no consumers).

The writer creates `active_research_handoff_source.json` +
`research_handoff_candidate_effective.json` (+ validation) only when the
R2E.5b-4 preview says would_promote AND every fail-closed creation rule passes.
The pointer carries promotion_status=pending_gates and is NOT trading
authorization — nothing consumes it. These tests build the *real* chain so the
writer's hash / expiry / validation re-checks are faithful to what Step 1 writes.
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
from investment_orchestrator.research.actionable_promotion_pointer import (
    PERMISSION_EFFECT_PENDING_GATES,
    POINTER_SOURCE,
    POINTER_WRITE_BLOCKER_EFFECTIVE_HASH_MISMATCH,
    POINTER_WRITE_BLOCKER_EFFECTIVE_PREVIEW_MISSING,
    POINTER_WRITE_BLOCKER_EFFECTIVE_VALIDATION_FAILED,
    POINTER_WRITE_BLOCKER_PREVIEW_MALFORMED,
    POINTER_WRITE_BLOCKER_PREVIEW_MARKERS_INVALID,
    POINTER_WRITE_BLOCKER_PREVIEW_MISSING,
    POINTER_WRITE_BLOCKER_PROMOTION_EXPIRED,
    POINTER_WRITE_BLOCKER_WOULD_PROMOTE_FALSE,
    PROMOTION_STATUS_PENDING_GATES,
    SCHEMA_VERSION,
    WRITE_STATUS_SCHEMA_VERSION,
    write_actionable_promotion_pointer_if_eligible,
)
from investment_orchestrator.research.actionable_promotion_pointer_preview import (
    build_actionable_promotion_pointer_preview,
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


# --- builders (real chain) -------------------------------------------------------


def settings() -> dict[str, Any]:
    return {
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


def chain(*, valid_until: str = "2026-07-31", grounded: bool = True) -> dict[str, Any]:
    """Real chain through eligibility + pointer preview + effective preview inputs."""
    stgs = settings()
    anchors_payload = {
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
    anchors = summarize_research_anchors(
        validate_research_anchors(anchors_payload, allowed_universe=["QQQ", "VOO", "SMH"], today=TODAY)
    )
    packet = {
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
            "hard_cap_open_orders_budget": stgs["hard_cap_open_orders_budget"],
            "target_new_buy_budget_this_run": stgs["target_new_buy_budget_this_run"],
            "max_new_tickers_per_week": stgs["max_new_tickers_per_week"],
        },
        "research_anchors": anchors,
        "data_gaps": [],
        "report_only": True,
    }
    m = {
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
                "anchor_id_refs": ["AI_CAPEX_2026H2" if grounded else "DOES_NOT_EXIST"],
            }
        ],
        "avoid_or_deprioritize": [],
        "data_gaps": [],
        "source_notes": [{"claim": "AI capex", "source": "10-K", "source_quality": "official"}],
    }
    signals = build_compiled_support_signals(evidence_packet=packet, analyst_memo=m, compilation_mode=_MODE)
    preview = build_actionable_handoff_preview(
        evidence_packet=packet, analyst_memo=m, compiled_support_signals=signals
    )
    candidate = build_actionable_handoff_candidate(
        evidence_packet=packet, analyst_memo=m, actionable_handoff_preview=preview,
        base_candidate=None, strategy_settings=stgs,
    )
    validation = research_handoff_validation_result_to_dict(
        validate_research_handoff(candidate, strategy_settings=stgs)
    )
    metadata = build_actionable_handoff_metadata(
        candidate=candidate, validation=validation, actionable_handoff_preview=preview,
        compiled_support_signals=signals, evidence_packet=packet, base_candidate=None,
        used_active_compiled_handoff_as_base=False,
    )
    eligibility = evaluate_actionable_handoff_promotion_eligibility(
        evidence_packet=packet, compiled_support_signals=signals, actionable_preview=preview,
        actionable_candidate=candidate, actionable_candidate_validation=validation,
        actionable_candidate_metadata=metadata, strategy_settings=stgs, today=TODAY,
    )
    pointer_preview = build_actionable_promotion_pointer_preview(
        eligibility=eligibility, actionable_candidate=candidate,
        actionable_candidate_validation=validation, actionable_candidate_metadata=metadata,
        today=TODAY,
    )
    return {
        "pointer_preview": pointer_preview,
        "effective_preview": json.loads(json.dumps(candidate)),
        "effective_preview_validation": validation,
        "strategy_settings": stgs,
        "candidate": candidate,
    }


def write(tmp_path: Path, inputs: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "pointer_preview": inputs["pointer_preview"],
        "effective_preview": inputs["effective_preview"],
        "effective_preview_validation": inputs["effective_preview_validation"],
        "output_pointer_path": tmp_path / "active_research_handoff_source.json",
        "output_effective_path": tmp_path / "research_handoff_candidate_effective.json",
        "output_effective_validation_path": tmp_path / "research_handoff_candidate_effective_validation.json",
        "output_status_path": tmp_path / "active_research_handoff_source_write_status.json",
        "strategy_settings": inputs["strategy_settings"],
        "today": TODAY,
        "pointer_preview_path": "/x/compiled_actionable_handoff_promotion_pointer_preview.json",
    }
    kwargs.update(overrides)
    return write_actionable_promotion_pointer_if_eligible(**kwargs)


def _read(tmp_path: Path, name: str) -> dict[str, Any]:
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


# --- creation: happy path -----------------------------------------------------------


def test_eligible_chain_creates_real_pointer(tmp_path: Path) -> None:
    result = write(tmp_path, chain())
    assert result["pointer_blockers"] == []
    assert result["active_pointer_created"] == "True"
    assert (tmp_path / "active_research_handoff_source.json").is_file()
    assert (tmp_path / "research_handoff_candidate_effective.json").is_file()
    assert (tmp_path / "research_handoff_candidate_effective_validation.json").is_file()

    pointer = _read(tmp_path, "active_research_handoff_source.json")
    assert pointer["schema_version"] == SCHEMA_VERSION
    assert pointer["is_llm_generated"] is False
    assert pointer["source"] == POINTER_SOURCE
    assert pointer["promotion_status"] == PROMOTION_STATUS_PENDING_GATES
    assert pointer["permission_effect"] == PERMISSION_EFFECT_PENDING_GATES
    assert pointer["not_authorization"] is True
    assert pointer["future_pr_required"] is True
    assert pointer["active_pointer_created"] is True
    assert pointer["effective_handoff_created"] is True
    assert pointer["consumed_by_availability"] is False
    assert pointer["consumed_by_step2"] is False
    assert pointer["consumed_by_gates"] is False
    assert pointer["actionable_this_run_tickers"] == ["QQQ"]
    assert pointer["candidate_actionable_row_count"] == 1
    assert pointer["candidate_validation_passed"] is True
    assert pointer["promotion_expires_at"] == "2026-07-31"
    assert pointer["created_at"] == TODAY


def test_effective_handoff_is_unmutated_and_validates(tmp_path: Path) -> None:
    inputs = chain()
    write(tmp_path, inputs)
    effective = _read(tmp_path, "research_handoff_candidate_effective.json")
    # Byte-identical body: no wrapper metadata was added.
    assert effective == json.loads(json.dumps(inputs["candidate"]))
    validation = _read(tmp_path, "research_handoff_candidate_effective_validation.json")
    assert validation["valid"] is True
    assert (
        validate_research_handoff(effective, strategy_settings=inputs["strategy_settings"]).valid is True
    )


def test_pointer_hashes_are_consistent(tmp_path: Path) -> None:
    inputs = chain()
    write(tmp_path, inputs)
    pointer = _read(tmp_path, "active_research_handoff_source.json")
    # The effective handoff hash equals the approved candidate hash.
    assert pointer["effective_handoff_sha256"] == pointer["candidate_sha256"]
    assert pointer["candidate_sha256"] == inputs["pointer_preview"]["candidate_sha256"]
    assert pointer["eligibility_sha256"] == inputs["pointer_preview"]["eligibility_sha256"]
    assert pointer["pointer_preview_sha256"]
    assert pointer["source_chain_hashes"]["evidence_packet"]["match"] is True
    status = _read(tmp_path, "active_research_handoff_source_write_status.json")
    assert status["schema_version"] == WRITE_STATUS_SCHEMA_VERSION
    assert status["active_pointer_created"] is True
    assert status["promotion_status"] == PROMOTION_STATUS_PENDING_GATES


# --- fail closed ---------------------------------------------------------------------


def _assert_nothing_created(tmp_path: Path) -> None:
    assert not (tmp_path / "active_research_handoff_source.json").exists()
    assert not (tmp_path / "research_handoff_candidate_effective.json").exists()
    assert not (tmp_path / "research_handoff_candidate_effective_validation.json").exists()


def test_would_promote_false_creates_nothing(tmp_path: Path) -> None:
    result = write(tmp_path, chain(grounded=False))
    assert result["active_pointer_created"] == "False"
    assert POINTER_WRITE_BLOCKER_WOULD_PROMOTE_FALSE in result["pointer_blockers"]
    _assert_nothing_created(tmp_path)
    status = _read(tmp_path, "active_research_handoff_source_write_status.json")
    assert status["active_pointer_created"] is False
    assert status["promotion_status"] is None
    assert status["permission_effect"] == "none"


def test_expired_promotion_creates_nothing(tmp_path: Path) -> None:
    result = write(tmp_path, chain(valid_until="2026-06-29"), today="2026-06-30")
    assert result["active_pointer_created"] == "False"
    assert POINTER_WRITE_BLOCKER_PROMOTION_EXPIRED in result["pointer_blockers"]
    _assert_nothing_created(tmp_path)


def test_failed_effective_validation_creates_nothing(tmp_path: Path) -> None:
    result = write(tmp_path, chain(), effective_preview_validation={"valid": False})
    assert result["active_pointer_created"] == "False"
    assert POINTER_WRITE_BLOCKER_EFFECTIVE_VALIDATION_FAILED in result["pointer_blockers"]
    _assert_nothing_created(tmp_path)


def test_hash_mismatch_creates_nothing(tmp_path: Path) -> None:
    inputs = chain()
    mutated = json.loads(json.dumps(inputs["effective_preview"]))
    mutated["trade_universe"]["allowed_buy_tickers"] = ["QQQ", "VOO", "SMH", "VTI"]
    result = write(tmp_path, inputs, effective_preview=mutated)
    assert result["active_pointer_created"] == "False"
    assert POINTER_WRITE_BLOCKER_EFFECTIVE_HASH_MISMATCH in result["pointer_blockers"]
    _assert_nothing_created(tmp_path)


def test_malformed_preview_creates_nothing(tmp_path: Path) -> None:
    inputs = chain()
    bad = dict(inputs["pointer_preview"])
    bad["schema_version"] = "unexpected_v9"
    result = write(tmp_path, inputs, pointer_preview=bad)
    assert result["active_pointer_created"] == "False"
    assert POINTER_WRITE_BLOCKER_PREVIEW_MALFORMED in result["pointer_blockers"]
    _assert_nothing_created(tmp_path)


def test_tainted_preview_markers_create_nothing(tmp_path: Path) -> None:
    inputs = chain()
    tainted = dict(inputs["pointer_preview"])
    tainted["not_authorization"] = False
    result = write(tmp_path, inputs, pointer_preview=tainted)
    assert result["active_pointer_created"] == "False"
    assert POINTER_WRITE_BLOCKER_PREVIEW_MARKERS_INVALID in result["pointer_blockers"]
    _assert_nothing_created(tmp_path)


def test_missing_effective_preview_creates_nothing(tmp_path: Path) -> None:
    result = write(tmp_path, chain(), effective_preview=None)
    assert result["active_pointer_created"] == "False"
    assert POINTER_WRITE_BLOCKER_EFFECTIVE_PREVIEW_MISSING in result["pointer_blockers"]
    _assert_nothing_created(tmp_path)


def test_never_raises_on_all_none_inputs(tmp_path: Path) -> None:
    result = write_actionable_promotion_pointer_if_eligible(
        pointer_preview=None,
        effective_preview=None,
        effective_preview_validation=None,
        output_pointer_path=tmp_path / "active_research_handoff_source.json",
        output_effective_path=tmp_path / "research_handoff_candidate_effective.json",
        output_effective_validation_path=tmp_path / "validation.json",
        output_status_path=tmp_path / "status.json",
    )
    assert result["active_pointer_created"] == "False"
    assert POINTER_WRITE_BLOCKER_PREVIEW_MISSING in result["pointer_blockers"]
    assert not (tmp_path / "active_research_handoff_source.json").exists()
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["active_pointer_created"] is False
    assert status["not_authorization"] is True


def test_stale_pointer_files_removed_when_no_longer_promotable(tmp_path: Path) -> None:
    # A previous promotable run left real pointer files; this run is not
    # promotable → the stale files must be removed (pointer exists iff the
    # LATEST run was promotable).
    write(tmp_path, chain())
    assert (tmp_path / "active_research_handoff_source.json").is_file()

    result = write(tmp_path, chain(grounded=False))
    assert result["active_pointer_created"] == "False"
    _assert_nothing_created(tmp_path)
    status = _read(tmp_path, "active_research_handoff_source_write_status.json")
    assert str(tmp_path / "active_research_handoff_source.json") in status["removed_stale_artifacts"]
