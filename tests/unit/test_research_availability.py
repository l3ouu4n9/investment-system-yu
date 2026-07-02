from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from investment_orchestrator.state.last_good_research_handoff import (
    decision_relevant_settings,
    last_good_research_handoff_metadata_path,
    last_good_research_handoff_path,
    read_last_good_research_handoff,
    strategy_settings_hash,
)
from investment_orchestrator.state.research_availability import (
    DEFAULT_STALE_POLICY,
    evaluate_research_availability,
    research_availability_result_to_dict,
    research_degraded_mode_decision_to_dict,
    research_freshness_report_to_dict,
)
from investment_orchestrator.validators.validate_research_handoff import (
    ResearchHandoffValidationResult,
    validate_research_handoff,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "step1_contract_failures"
NOW = "2026-06-22"


def read_json_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def settings(
    *,
    core_universe: list[str] | None = None,
    satellite_universe: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    base = {
        "core_universe": core_universe or ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": satellite_universe or ["SMH", "IGV"],
        "as_of": NOW,
    }
    base.update(extra)
    return base


def valid_candidate() -> dict[str, Any]:
    return read_json_fixture("minimal_valid_research_handoff.json")


def valid_result(strategy_settings: dict[str, Any]) -> ResearchHandoffValidationResult:
    result = validate_research_handoff(valid_candidate(), strategy_settings=strategy_settings)
    assert result.valid is True
    return result


def invalid_result() -> ResearchHandoffValidationResult:
    return ResearchHandoffValidationResult(
        valid=False,
        fail_reasons=["Missing execution handoff field: trade_universe"],
        missing_fields=["trade_universe"],
        blocker_reasons=["Missing execution handoff field: trade_universe"],
        non_blocker_reasons=[],
    )


def last_good_metadata(
    *,
    as_of: str,
    strategy_settings: dict[str, Any] | None = None,
    universe_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy_settings = strategy_settings or settings()
    universe = universe_override or {
        "core_universe": strategy_settings["core_universe"],
        "satellite_universe": strategy_settings["satellite_universe"],
        "allowed_buy_tickers": ["VOO", "VTI", "VT", "QQQ", "SMH", "IGV"],
    }
    return {
        "source_as_of_date": as_of,
        "strategy_settings_hash": strategy_settings_hash(decision_relevant_settings(strategy_settings)),
        "universe": universe,
    }


# --- state classification ----------------------------------------------------


def test_strict_fresh_allows_new_buy() -> None:
    s = settings()
    result = evaluate_research_availability(
        candidate_validation=valid_result(s),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date=NOW,
        now_date=NOW,
    )
    assert result.state == "STRICT_FRESH"
    assert result.fresh_research_available is True
    assert "NEW_BUY" in result.allowed_actions
    assert "ORDER_COMPILATION" in result.allowed_actions


def test_strict_stale_blocks_new_buy_but_allows_hold_no_trade_sell() -> None:
    s = settings()
    result = evaluate_research_availability(
        candidate_validation=valid_result(s),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date="2026-06-10",  # age 12
        now_date=NOW,
    )
    assert result.state == "STRICT_STALE"
    assert result.allowed_actions == ["HOLD", "NO_TRADE", "SELL"]
    assert "NEW_BUY" in result.blocked_actions


def test_invalid_with_usable_last_good_is_degraded_hold_no_trade_only() -> None:
    s = settings()
    result = evaluate_research_availability(
        candidate_validation=invalid_result(),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date=NOW,
        now_date=NOW,
        last_good_handoff=valid_candidate(),
        last_good_metadata=last_good_metadata(as_of="2026-06-12", strategy_settings=s),
    )
    assert result.state == "DEGRADED_WITH_LAST_GOOD"
    assert result.last_good_usable is True
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in result.blocked_actions


def test_invalid_without_last_good_is_invalid_contract() -> None:
    result = evaluate_research_availability(
        candidate_validation=invalid_result(),
        candidate=valid_candidate(),
        strategy_settings=settings(),
        source_as_of_date=NOW,
        now_date=NOW,
    )
    assert result.state == "INVALID_CONTRACT"
    assert "NEW_BUY" in result.blocked_actions


def test_no_candidate_no_output_is_no_output() -> None:
    result = evaluate_research_availability(
        candidate_validation=None,
        candidate=None,
        strategy_settings=settings(),
        source_as_of_date=None,
        now_date=NOW,
    )
    assert result.state == "NO_OUTPUT"
    assert "NEW_BUY" in result.blocked_actions


def test_no_candidate_but_output_present_is_degraded_no_research() -> None:
    result = evaluate_research_availability(
        candidate_validation=None,
        candidate=None,
        strategy_settings=settings(),
        source_as_of_date=None,
        now_date=NOW,
        parsed_output_available=True,
    )
    assert result.state == "DEGRADED_NO_RESEARCH"
    assert "NEW_BUY" in result.blocked_actions


def test_last_good_too_old_requires_manual_review() -> None:
    s = settings()
    result = evaluate_research_availability(
        candidate_validation=invalid_result(),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date=NOW,
        now_date=NOW,
        last_good_handoff=valid_candidate(),
        last_good_metadata=last_good_metadata(as_of="2026-05-20", strategy_settings=s),  # age 33
    )
    assert result.state == "MANUAL_REVIEW_REQUIRED"
    assert result.manual_review_required is True
    assert "NEW_BUY" in result.blocked_actions


def test_universe_mismatch_requires_manual_review() -> None:
    s = settings()
    mismatched = last_good_metadata(
        as_of="2026-06-15",
        strategy_settings=s,
        universe_override={
            "core_universe": ["QQQ", "VOO"],
            "satellite_universe": ["SMH"],
            "allowed_buy_tickers": ["QQQ", "VOO", "SMH"],
        },
    )
    result = evaluate_research_availability(
        candidate_validation=invalid_result(),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date=NOW,
        now_date=NOW,
        last_good_handoff=valid_candidate(),
        last_good_metadata=mismatched,
    )
    assert result.state == "MANUAL_REVIEW_REQUIRED"
    assert result.universe_match is False
    assert "NEW_BUY" in result.blocked_actions


def test_non_universe_settings_drift_is_usable_but_blocks_new_buy() -> None:
    base = settings()
    drifted = settings(hard_cap_open_orders_budget=99999.0)
    result = evaluate_research_availability(
        candidate_validation=invalid_result(),
        candidate=valid_candidate(),
        strategy_settings=drifted,
        source_as_of_date=NOW,
        now_date=NOW,
        last_good_handoff=valid_candidate(),
        last_good_metadata=last_good_metadata(as_of="2026-06-15", strategy_settings=base),
    )
    assert result.state == "DEGRADED_WITH_LAST_GOOD"
    assert result.settings_hash_match is False
    assert result.universe_match is True
    assert "NEW_BUY" in result.blocked_actions


def test_valid_but_too_old_requires_manual_review() -> None:
    s = settings()
    result = evaluate_research_availability(
        candidate_validation=valid_result(s),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date="2026-06-02",  # age 20
        now_date=NOW,
    )
    assert result.state == "MANUAL_REVIEW_REQUIRED"


# --- safety invariants -------------------------------------------------------


def test_new_buy_only_ever_allowed_in_strict_fresh() -> None:
    s = settings()
    scenarios = [
        # (state-producing inputs)
        dict(candidate_validation=valid_result(s), candidate=valid_candidate(), source_as_of_date="2026-06-10"),  # stale
        dict(candidate_validation=invalid_result(), candidate=valid_candidate(), source_as_of_date=NOW),  # invalid contract
        dict(candidate_validation=None, candidate=None, source_as_of_date=None),  # no output
    ]
    for kwargs in scenarios:
        result = evaluate_research_availability(
            strategy_settings=s, now_date=NOW, **kwargs
        )
        assert "NEW_BUY" not in result.allowed_actions, result.state


def test_no_trade_and_hold_always_allowed() -> None:
    s = settings()
    scenarios = [
        dict(candidate_validation=valid_result(s), candidate=valid_candidate(), source_as_of_date=NOW),
        dict(candidate_validation=valid_result(s), candidate=valid_candidate(), source_as_of_date="2026-06-10"),
        dict(candidate_validation=invalid_result(), candidate=valid_candidate(), source_as_of_date=NOW),
        dict(candidate_validation=None, candidate=None, source_as_of_date=None),
        dict(candidate_validation=valid_result(s), candidate=valid_candidate(), source_as_of_date="2026-06-02"),
    ]
    for kwargs in scenarios:
        result = evaluate_research_availability(strategy_settings=s, now_date=NOW, **kwargs)
        assert "HOLD" in result.allowed_actions
        assert "NO_TRADE" in result.allowed_actions


# --- stale policy boundaries -------------------------------------------------


def test_stale_policy_boundaries_day_8_9_16_17() -> None:
    s = settings()

    def label_for(as_of: str) -> tuple[str, str]:
        result = evaluate_research_availability(
            candidate_validation=valid_result(s),
            candidate=valid_candidate(),
            strategy_settings=s,
            source_as_of_date=as_of,
            now_date=NOW,
        )
        return result.state, result.stale_label

    assert label_for("2026-06-14") == ("STRICT_FRESH", "fresh")  # age 8
    assert label_for("2026-06-13") == ("STRICT_STALE", "stale")  # age 9
    assert label_for("2026-06-06") == ("STRICT_STALE", "stale")  # age 16
    assert label_for("2026-06-05")[1] == "too_old"  # age 17


def test_stale_policy_is_overridable() -> None:
    s = settings()
    result = evaluate_research_availability(
        candidate_validation=valid_result(s),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date="2026-06-19",  # age 3
        now_date=NOW,
        stale_policy={"fresh_days": 2, "stale_days": 5},
    )
    assert result.fresh_days == 2
    assert result.stale_days == 5
    assert result.state == "STRICT_STALE"  # age 3 > fresh_days 2


def test_default_stale_policy_constants() -> None:
    assert DEFAULT_STALE_POLICY == {"fresh_days": 8, "stale_days": 16}


# --- serialization -----------------------------------------------------------


def test_serialization_views_are_stable_and_json_safe() -> None:
    s = settings()
    result = evaluate_research_availability(
        candidate_validation=valid_result(s),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date=NOW,
        now_date=NOW,
    )
    full = research_availability_result_to_dict(result)
    freshness = research_freshness_report_to_dict(result)
    decision = research_degraded_mode_decision_to_dict(result)

    for view in (full, freshness, decision):
        assert view["report_only"] is True
        json.dumps(view, ensure_ascii=False)

    assert full["state"] == "STRICT_FRESH"
    assert full["allowed_actions"] == result.allowed_actions
    assert decision["allowed_actions"] == result.allowed_actions
    assert "handoff_age_days" in freshness


# --- last-good reader --------------------------------------------------------


def test_reader_missing_files_returns_unavailable_no_raise(tmp_path: Path) -> None:
    result = read_last_good_research_handoff(tmp_path)
    assert result.available is False
    assert result.handoff is None
    assert result.metadata is None
    assert result.read_errors


def test_reader_reads_valid_files(tmp_path: Path) -> None:
    handoff = valid_candidate()
    metadata = last_good_metadata(as_of="2026-06-15")
    last_good_research_handoff_path(tmp_path).write_text(
        json.dumps(handoff), encoding="utf-8"
    )
    last_good_research_handoff_metadata_path(tmp_path).write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    result = read_last_good_research_handoff(tmp_path)
    assert result.available is True
    assert result.handoff == handoff
    assert result.metadata == metadata
    assert result.read_errors == []


def test_reader_handles_malformed_files_no_raise(tmp_path: Path) -> None:
    last_good_research_handoff_path(tmp_path).write_text("{not valid json", encoding="utf-8")
    last_good_research_handoff_metadata_path(tmp_path).write_text("[]", encoding="utf-8")

    result = read_last_good_research_handoff(tmp_path)
    assert result.available is False
    assert result.read_errors


# --- R2E.1: compiled evidence-first handoff recognition (non-actionable) ------


COMPILED_VALID = {"valid": True}
COMPILED_INVALID = {"valid": False, "blocker_reasons": ["x"]}


def compiled_meta(mode: str, *, present: bool, valid: bool) -> dict[str, Any]:
    return {
        "compilation_mode": mode,
        "analyst_memo_present": present,
        "analyst_memo_valid": valid,
    }


def compiled_artifacts() -> dict[str, str]:
    return {
        "compiled_research_handoff_candidate": "artifacts/current/step1_research/compiled_research_handoff_candidate.json",
        "compiled_research_handoff_validation": "artifacts/current/step1_research/compiled_research_handoff_validation.json",
        "compiled_research_handoff_metadata": "artifacts/current/step1_research/compiled_research_handoff_metadata.json",
    }


def evaluate_with_compiled(
    *,
    candidate_validation: Any,
    compiled_candidate_validation: Any,
    compiled_metadata: dict[str, Any] | None,
    compiled_as_of: str | None = NOW,
    **kwargs: Any,
) -> Any:
    s = kwargs.pop("strategy_settings", settings())
    return evaluate_research_availability(
        candidate_validation=candidate_validation,
        candidate=kwargs.pop("candidate", valid_candidate()),
        strategy_settings=s,
        source_as_of_date=kwargs.pop("source_as_of_date", NOW),
        now_date=NOW,
        compiled_candidate_validation=compiled_candidate_validation,
        compiled_metadata=compiled_metadata,
        compiled_source_as_of_date=compiled_as_of,
        compiled_source_artifacts=compiled_artifacts(),
        **kwargs,
    )


def test_raw_strict_fresh_is_never_overridden_by_compiled() -> None:
    # Precedence A: a valid+fresh raw handoff keeps full actionable permissions
    # even when a compiled handoff is also present.
    s = settings()
    result = evaluate_research_availability(
        candidate_validation=valid_result(s),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date=NOW,
        now_date=NOW,
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_source_as_of_date=NOW,
    )
    assert result.state == "STRICT_FRESH"
    assert result.source == "raw_research_handoff"
    assert "NEW_BUY" in result.allowed_actions


def test_raw_invalid_compiled_evidence_only_is_strict_fresh_evidence_only() -> None:
    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_only", present=False, valid=False),
    )
    assert result.state == "STRICT_FRESH_EVIDENCE_ONLY"
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in result.blocked_actions
    assert "ORDER_COMPILATION" in result.blocked_actions
    assert result.fresh_research_available is False
    assert result.manual_review_required is False
    assert result.source == "compiled_research_handoff"
    assert result.compilation_mode == "evidence_only"
    assert result.source_artifacts == compiled_artifacts()


def test_raw_invalid_compiled_evidence_plus_memo_is_still_non_actionable() -> None:
    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
    )
    # R2E.1 stays non-actionable even with a valid memo (NEW_BUY is a future PR).
    # Without a support-signals artifact it also stays evidence-only (R2E.4 needs it).
    assert result.state == "STRICT_FRESH_EVIDENCE_ONLY"
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in result.blocked_actions
    assert result.analyst_memo_present is True
    assert result.analyst_memo_valid is True


# --- R2E.4: grounded memo support recognition (still non-actionable) ----------


def accepted_signals(
    *,
    present: bool = True,
    valid: bool = True,
    accepted: int = 1,
    permission_effect: str = "none",
    not_authorization: bool = True,
) -> dict[str, Any]:
    return {
        "analyst_memo_present": present,
        "analyst_memo_valid": valid,
        "accepted_support_signals": [{"ticker": "QQQ"} for _ in range(accepted)],
        "permission_effect": permission_effect,
        "not_authorization": not_authorization,
    }


def test_accepted_grounded_support_upgrades_state_but_stays_non_actionable() -> None:
    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_support_signals=accepted_signals(accepted=2),
    )
    assert result.state == "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
    # Permission set is IDENTICAL to evidence-only: HOLD / NO_TRADE only.
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]
    for blocked in ("SELL", "NEW_BUY", "ROTATION", "REBALANCE", "EXTENDED_ETF_ADMISSION", "ORDER_COMPILATION"):
        assert blocked in result.blocked_actions, blocked
    assert result.grounded_memo_support_present is True
    assert result.support_signals_present is True
    assert result.accepted_support_signal_count == 2
    assert result.support_signals_not_authorization is True
    assert result.fresh_research_available is False
    assert result.manual_review_required is False
    assert result.source == "compiled_research_handoff"
    # Explicit non-actionable blocker reasons.
    assert any("grounded_memo_support_non_actionable" in r for r in result.blocker_reasons)
    assert any("new_buy_requires_future_gate_pr" in r for r in result.blocker_reasons)


def test_grounded_state_maps_to_hold_no_trade_in_action_table() -> None:
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH,
        STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE,
        _ALLOWED_ACTIONS_BY_STATE,
    )

    assert STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE != STRICT_FRESH
    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE] == ("HOLD", "NO_TRADE")
    assert "NEW_BUY" not in _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE]


def test_empty_accepted_support_signals_stays_evidence_only() -> None:
    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_support_signals=accepted_signals(accepted=0),
    )
    assert result.state == "STRICT_FRESH_EVIDENCE_ONLY"
    assert result.grounded_memo_support_present is False
    assert result.accepted_support_signal_count == 0


def test_support_signals_not_authorization_false_fails_closed() -> None:
    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_support_signals=accepted_signals(not_authorization=False),
    )
    assert result.state == "STRICT_FRESH_EVIDENCE_ONLY"
    assert result.grounded_memo_support_present is False


def test_support_signals_permission_effect_not_none_fails_closed() -> None:
    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_support_signals=accepted_signals(permission_effect="actionable"),
    )
    assert result.state == "STRICT_FRESH_EVIDENCE_ONLY"


def test_malformed_support_signals_fails_closed_to_evidence_only() -> None:
    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_support_signals=["not", "a", "mapping"],
    )
    assert result.state == "STRICT_FRESH_EVIDENCE_ONLY"
    assert result.support_signals_present is False


def test_raw_strict_fresh_not_upgraded_by_accepted_support_signals() -> None:
    # Grounded state only sharpens the evidence-only compiled label; a valid+fresh
    # RAW handoff keeps full STRICT_FRESH actionable permissions regardless.
    s = settings()
    result = evaluate_research_availability(
        candidate_validation=valid_result(s),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date=NOW,
        now_date=NOW,
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_source_as_of_date=NOW,
        compiled_support_signals=accepted_signals(),
    )
    assert result.state == "STRICT_FRESH"
    assert "NEW_BUY" in result.allowed_actions


def test_grounded_state_permission_effect_is_none_in_decision_artifact() -> None:
    from investment_orchestrator.state.research_availability import (
        research_degraded_mode_decision_to_dict,
    )

    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_support_signals=accepted_signals(),
    )
    decision = research_degraded_mode_decision_to_dict(result)
    assert decision["state"] == "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
    assert decision["permission_effect"] == "none"
    assert decision["grounded_memo_support_present"] is True
    assert decision["accepted_support_signal_count"] == 1
    assert decision["not_authorization"] is True
    assert "NEW_BUY" not in decision["allowed_actions"]


# --- R2E.5b-5b: promoted handoff pending-gates recognition ------------------


PENDING_GATES_STATE = "STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES"


def effective_handoff() -> dict[str, Any]:
    return {
        "schema_version": "compiled_actionable_research_handoff_candidate_v1",
        "is_llm_generated": False,
        "report_only": True,
        "not_authorization": True,
        "buy_universe_scorecard": [
            {"ticker": "QQQ", "actionability_status": "actionable_this_run"}
        ],
    }


def sha256_of(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def pending_pointer(
    effective: dict[str, Any],
    *,
    promotion_expires_at: str = "2026-06-29",
    status: str = "pending_gates",
    permission_effect: str = "none_until_consumed_by_future_gate_pr",
    not_authorization: bool = True,
    future_pr_required: bool = True,
    consumed_by_availability: bool = False,
    consumed_by_step2: bool = False,
    consumed_by_gates: bool = False,
    row_count: int = 1,
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    digest = sha256_of(effective)
    return {
        "schema_version": "active_research_handoff_source_v1",
        "is_llm_generated": False,
        "source": "promoted_compiled_actionable_handoff",
        "promotion_status": status,
        "active_pointer_created": True,
        "effective_handoff_created": True,
        "permission_effect": permission_effect,
        "not_authorization": not_authorization,
        "future_pr_required": future_pr_required,
        "effective_handoff_path": "artifacts/current/step1_research/research_handoff_candidate_effective.json",
        "effective_validation_path": (
            "artifacts/current/step1_research/research_handoff_candidate_effective_validation.json"
        ),
        "candidate_sha256": digest,
        "effective_handoff_sha256": digest,
        "candidate_actionable_row_count": row_count,
        "actionable_this_run_tickers": tickers if tickers is not None else ["QQQ"],
        "promotion_expires_at": promotion_expires_at,
        "consumed_by_availability": consumed_by_availability,
        "consumed_by_step2": consumed_by_step2,
        "consumed_by_gates": consumed_by_gates,
    }


def pending_artifacts() -> dict[str, str]:
    return {
        "active_research_handoff_source": (
            "artifacts/current/step1_research/active_research_handoff_source.json"
        ),
        "research_handoff_candidate_effective": (
            "artifacts/current/step1_research/research_handoff_candidate_effective.json"
        ),
        "research_handoff_candidate_effective_validation": (
            "artifacts/current/step1_research/research_handoff_candidate_effective_validation.json"
        ),
    }


def evaluate_with_pending_pointer(
    *,
    pointer: dict[str, Any] | None = None,
    effective: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    compiled_support_signals: dict[str, Any] | None = None,
) -> Any:
    effective = effective if effective is not None else effective_handoff()
    pointer = pointer if pointer is not None else pending_pointer(effective)
    validation = validation if validation is not None else {"valid": True}
    return evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_support_signals=compiled_support_signals or accepted_signals(),
        promoted_pointer=pointer,
        promoted_effective_handoff=effective,
        promoted_effective_validation=validation,
        promoted_source_artifacts=pending_artifacts(),
    )


def test_pointer_absent_keeps_grounded_memo_non_actionable_state() -> None:
    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_support_signals=accepted_signals(),
    )
    assert result.state == "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]


def test_valid_pending_pointer_upgrades_to_pending_gates_hold_no_trade_only() -> None:
    result = evaluate_with_pending_pointer()

    assert result.state == PENDING_GATES_STATE
    assert result.source == "promoted_compiled_actionable_handoff"
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]
    for blocked in ("SELL", "NEW_BUY", "ROTATION", "REBALANCE", "EXTENDED_ETF_ADMISSION", "ORDER_COMPILATION"):
        assert blocked in result.blocked_actions
    assert result.promoted_pointer_present is True
    assert result.promoted_pointer_valid is True
    assert result.promotion_status == "pending_gates"
    assert result.effective_handoff_present is True
    assert result.effective_handoff_valid is True
    assert result.candidate_actionable_row_count == 1
    assert result.actionable_this_run_tickers == ["QQQ"]
    assert result.promotion_expires_at == "2026-06-29"
    assert result.permission_effect == "none_until_consumed_by_future_gate_pr"
    assert result.not_authorization is True
    assert result.source_artifacts | pending_artifacts() == result.source_artifacts
    for reason in (
        "promoted_actionable_handoff_pending_gates",
        "new_buy_requires_future_gate_pr",
        "order_compilation_requires_future_gate_pr",
    ):
        assert reason in result.blocker_reasons


def test_pending_gates_state_serializes_diagnostics() -> None:
    result = evaluate_with_pending_pointer()
    availability = research_availability_result_to_dict(result)
    decision = research_degraded_mode_decision_to_dict(result)

    for artifact in (availability, decision):
        assert artifact["state"] == PENDING_GATES_STATE
        assert artifact["promoted_pointer_present"] is True
        assert artifact["promoted_pointer_valid"] is True
        assert artifact["promotion_status"] == "pending_gates"
        assert artifact["effective_handoff_present"] is True
        assert artifact["effective_handoff_valid"] is True
        assert artifact["candidate_actionable_row_count"] == 1
        assert artifact["actionable_this_run_tickers"] == ["QQQ"]
        assert artifact["permission_effect"] == "none_until_consumed_by_future_gate_pr"
        assert artifact["not_authorization"] is True
        assert artifact["source_artifacts"]["active_research_handoff_source"].endswith(
            "active_research_handoff_source.json"
        )
        assert "NEW_BUY" not in artifact["allowed_actions"]


def test_pending_gates_state_maps_to_hold_no_trade_in_action_table() -> None:
    from investment_orchestrator.state.research_availability import _ALLOWED_ACTIONS_BY_STATE

    assert _ALLOWED_ACTIONS_BY_STATE[PENDING_GATES_STATE] == ("HOLD", "NO_TRADE")


def test_malformed_pending_pointer_fails_closed_to_grounded_state() -> None:
    effective = effective_handoff()
    pointer = pending_pointer(effective)
    pointer["schema_version"] = "unexpected"

    result = evaluate_with_pending_pointer(pointer=pointer, effective=effective)

    assert result.state == "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
    assert result.promoted_pointer_present is True
    assert result.promoted_pointer_valid is False
    assert "NEW_BUY" not in result.allowed_actions


def test_stale_pending_pointer_fails_closed_to_grounded_state() -> None:
    effective = effective_handoff()
    result = evaluate_with_pending_pointer(
        pointer=pending_pointer(effective, promotion_expires_at="2026-06-21"),
        effective=effective,
    )

    assert result.state == "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
    assert result.promoted_pointer_valid is False


def test_hash_mismatch_fails_closed_to_grounded_state() -> None:
    effective = effective_handoff()
    pointer = pending_pointer(effective)
    mutated = {**effective, "mutated": True}

    result = evaluate_with_pending_pointer(pointer=pointer, effective=mutated)

    assert result.state == "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
    assert result.effective_handoff_valid is False


def test_effective_validation_failure_fails_closed_to_grounded_state() -> None:
    result = evaluate_with_pending_pointer(validation={"valid": False})

    assert result.state == "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
    assert result.effective_handoff_valid is False


def test_pending_pointer_requires_unconsumed_gate_markers() -> None:
    effective = effective_handoff()
    result = evaluate_with_pending_pointer(
        pointer=pending_pointer(effective, consumed_by_step2=True),
        effective=effective,
    )

    assert result.state == "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
    assert result.promoted_pointer_valid is False


def test_pending_pointer_requires_actionable_rows_and_tickers() -> None:
    effective = effective_handoff()
    result = evaluate_with_pending_pointer(
        pointer=pending_pointer(effective, row_count=0, tickers=[]),
        effective=effective,
    )

    assert result.state == "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
    assert result.promoted_pointer_valid is False


def test_pending_pointer_does_not_upgrade_without_grounded_memo_state() -> None:
    result = evaluate_with_pending_pointer(compiled_support_signals=accepted_signals(accepted=0))

    assert result.state == "STRICT_FRESH_EVIDENCE_ONLY"
    assert result.promoted_pointer_valid is True


def test_raw_strict_fresh_still_wins_over_pending_pointer() -> None:
    s = settings()
    effective = effective_handoff()
    result = evaluate_research_availability(
        candidate_validation=valid_result(s),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date=NOW,
        now_date=NOW,
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_source_as_of_date=NOW,
        compiled_support_signals=accepted_signals(),
        promoted_pointer=pending_pointer(effective),
        promoted_effective_handoff=effective,
        promoted_effective_validation={"valid": True},
    )

    assert result.state == "STRICT_FRESH"
    assert "NEW_BUY" in result.allowed_actions
    assert "ORDER_COMPILATION" in result.allowed_actions


def test_raw_invalid_compiled_invalid_keeps_existing_behavior() -> None:
    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_INVALID,
        compiled_metadata=compiled_meta("evidence_only", present=False, valid=False),
    )
    assert result.state == "INVALID_CONTRACT"
    assert result.source == "raw_research_handoff"
    assert "NEW_BUY" in result.blocked_actions


def test_compiled_valid_but_metadata_malformed_fails_closed_to_existing_state() -> None:
    # Missing / unrecognized compilation_mode -> do NOT relabel (fail closed).
    for bad_meta in (None, {}, {"compilation_mode": "garbage"}, {"foo": "bar"}):
        result = evaluate_with_compiled(
            candidate_validation=invalid_result(),
            compiled_candidate_validation=COMPILED_VALID,
            compiled_metadata=bad_meta,
        )
        assert result.state == "INVALID_CONTRACT", bad_meta
        assert "NEW_BUY" in result.blocked_actions


def test_compiled_stale_does_not_relabel() -> None:
    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_only", present=False, valid=False),
        compiled_as_of="2026-05-01",  # age > fresh_days
    )
    assert result.state == "INVALID_CONTRACT"


def test_compiled_preferred_over_usable_last_good_but_still_hold_no_trade() -> None:
    s = settings()
    result = evaluate_research_availability(
        candidate_validation=invalid_result(),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date=NOW,
        now_date=NOW,
        last_good_handoff=valid_candidate(),
        last_good_metadata=last_good_metadata(as_of="2026-06-12", strategy_settings=s),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_only", present=False, valid=False),
        compiled_source_as_of_date=NOW,
    )
    assert result.state == "STRICT_FRESH_EVIDENCE_ONLY"
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in result.blocked_actions


def test_degraded_with_last_good_unchanged_without_compiled() -> None:
    # Regression: no compiled inputs -> pre-R2E.1 DEGRADED_WITH_LAST_GOOD preserved.
    s = settings()
    result = evaluate_research_availability(
        candidate_validation=invalid_result(),
        candidate=valid_candidate(),
        strategy_settings=s,
        source_as_of_date=NOW,
        now_date=NOW,
        last_good_handoff=valid_candidate(),
        last_good_metadata=last_good_metadata(as_of="2026-06-12", strategy_settings=s),
    )
    assert result.state == "DEGRADED_WITH_LAST_GOOD"
    assert result.source == "raw_research_handoff"
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]


def test_strict_fresh_evidence_only_decision_dict_fields() -> None:
    result = evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_only", present=False, valid=False),
    )
    decision = research_degraded_mode_decision_to_dict(result)
    assert decision["state"] == "STRICT_FRESH_EVIDENCE_ONLY"
    assert decision["research_state"] == "STRICT_FRESH_EVIDENCE_ONLY"
    assert decision["source"] == "compiled_research_handoff"
    assert decision["compilation_mode"] == "evidence_only"
    assert decision["permission_effect"] == "none"
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert decision["source_artifacts"] == compiled_artifacts()
    assert any("evidence_only_no_new_buy" in r for r in decision["blocker_reasons"])
    assert any("compiled_handoff_non_actionable" in r for r in decision["blocker_reasons"])


# --- R2E.5b-6c: Step 2 decision-only upgrade (first true permission change) ----


STEP2_DECISION_ONLY_STATE = "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"


def valid_step2_verification(effective: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    digest = sha256_of(effective)
    verification = {
        "schema_version": "promoted_handoff_step2_verification_v1",
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "valid_for_step2_decision": True,
        "verification_blockers": [],
        "future_permission_required": "PROMOTED_RESEARCH_DECISION",
        "promotion_status": "pending_gates",
        "consumed_by_step2": False,
        "promotion_expires_at": "2026-06-29",
        "effective_handoff_sha256": digest,
        "pointer_effective_handoff_sha256": digest,
    }
    verification.update(overrides)
    return verification


def valid_step2_dry_run(**overrides: Any) -> dict[str, Any]:
    dry_run = {
        "schema_version": "promoted_step2_gate_dry_run_v1",
        "is_llm_generated": False,
        "report_only": True,
        "dry_run_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "would_allow_step2_promoted_decision": True,
        "current_real_gate_allows": False,
        "future_permission_required": "PROMOTED_RESEARCH_DECISION",
        "future_state_required": STEP2_DECISION_ONLY_STATE,
        "dry_run_blockers": ["real_gate_still_closed_by_policy"],
    }
    dry_run.update(overrides)
    return dry_run


def evaluate_step2_decision_only(
    *,
    effective: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
    dry_run: dict[str, Any] | None = None,
) -> Any:
    effective = effective if effective is not None else effective_handoff()
    pointer = pending_pointer(effective)
    return evaluate_with_compiled(
        candidate_validation=invalid_result(),
        compiled_candidate_validation=COMPILED_VALID,
        compiled_metadata=compiled_meta("evidence_plus_memo", present=True, valid=True),
        compiled_support_signals=accepted_signals(),
        promoted_pointer=pointer,
        promoted_effective_handoff=effective,
        promoted_effective_validation={"valid": True},
        promoted_source_artifacts=pending_artifacts(),
        promoted_step2_verification=(
            verification if verification is not None else valid_step2_verification(effective)
        ),
        promoted_step2_gate_dry_run=dry_run if dry_run is not None else valid_step2_dry_run(),
    )


def test_pending_gates_with_valid_verification_and_dry_run_upgrades_to_decision_only() -> None:
    result = evaluate_step2_decision_only()

    assert result.state == STEP2_DECISION_ONLY_STATE
    assert result.promoted_step2_decision_only is True
    assert result.source == "promoted_compiled_actionable_handoff"
    # Allowed actions are EXACTLY HOLD / NO_TRADE / PROMOTED_RESEARCH_DECISION.
    assert result.allowed_actions == ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]
    for blocked in (
        "SELL",
        "NEW_BUY",
        "ROTATION",
        "REBALANCE",
        "EXTENDED_ETF_ADMISSION",
        "ORDER_COMPILATION",
    ):
        assert blocked in result.blocked_actions
    assert result.permission_effect == "promoted_step2_decision_only"
    assert result.not_authorization is True
    for reason in (
        "promoted_step2_decision_only_enabled",
        "new_buy_requires_future_gate_pr",
        "order_compilation_requires_future_gate_pr",
        "final_execution_requires_future_gate_pr",
    ):
        assert reason in result.blocker_reasons
    assert "promoted_actionable_handoff_pending_gates" not in result.blocker_reasons


def test_step2_decision_only_serializes_permission_fields() -> None:
    result = evaluate_step2_decision_only()
    availability = research_availability_result_to_dict(result)
    decision = research_degraded_mode_decision_to_dict(result)

    for artifact in (availability, decision):
        assert artifact["state"] == STEP2_DECISION_ONLY_STATE
        assert artifact["allowed_actions"] == ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]
        assert artifact["promoted_step2_decision_only"] is True
        assert artifact["order_compilation_allowed"] is False
        assert artifact["new_buy_permission"] is False
        assert artifact["permission_effect"] == "promoted_step2_decision_only"
        assert artifact["not_authorization"] is True
        assert "NEW_BUY" in artifact["blocked_actions"]
        assert "ORDER_COMPILATION" in artifact["blocked_actions"]


def test_step2_decision_only_action_table_is_exact() -> None:
    from investment_orchestrator.state.research_availability import (
        _ALLOWED_ACTIONS_BY_STATE,
    )

    assert _ALLOWED_ACTIONS_BY_STATE[STEP2_DECISION_ONLY_STATE] == (
        "HOLD",
        "NO_TRADE",
        "PROMOTED_RESEARCH_DECISION",
    )
    # The full order-eligible state remains absent / disabled.
    assert "STRICT_FRESH_COMPILED_ACTIONABLE" not in _ALLOWED_ACTIONS_BY_STATE
    # PROMOTED_RESEARCH_DECISION exists ONLY on the decision-only state.
    for state, actions in _ALLOWED_ACTIONS_BY_STATE.items():
        if state != STEP2_DECISION_ONLY_STATE:
            assert "PROMOTED_RESEARCH_DECISION" not in actions, state


def test_missing_dry_run_stays_pending_gates() -> None:
    effective = effective_handoff()
    result = evaluate_step2_decision_only(
        effective=effective,
        verification=valid_step2_verification(effective),
        dry_run={},
    )
    assert result.state == PENDING_GATES_STATE
    assert result.promoted_step2_decision_only is False
    assert result.allowed_actions == ["HOLD", "NO_TRADE"]

    result_none = evaluate_with_pending_pointer()
    assert result_none.state == PENDING_GATES_STATE


def test_dry_run_false_or_malformed_stays_pending_gates() -> None:
    effective = effective_handoff()
    for overrides in (
        {"would_allow_step2_promoted_decision": False},
        {"current_real_gate_allows": True},
        {"schema_version": "unexpected"},
        {"dry_run_only": False},
        {"report_only": False},
        {"not_authorization": False},
        {"permission_effect": "actionable"},
        {"future_permission_required": "NEW_BUY"},
        {"future_state_required": "STRICT_FRESH"},
        {"dry_run_blockers": []},
        {"dry_run_blockers": "not-a-list"},
    ):
        result = evaluate_step2_decision_only(
            effective=effective, dry_run=valid_step2_dry_run(**overrides)
        )
        assert result.state == PENDING_GATES_STATE, overrides
        assert result.promoted_step2_decision_only is False
        assert "PROMOTED_RESEARCH_DECISION" not in result.allowed_actions


def test_verification_invalid_or_stale_stays_pending_gates() -> None:
    effective = effective_handoff()
    for overrides in (
        {"valid_for_step2_decision": False},
        {"verification_blockers": ["promotion_expired"]},
        {"future_permission_required": "NEW_BUY"},
        {"promotion_status": "consumed"},
        {"consumed_by_step2": True},
        {"promotion_expires_at": "2026-06-21"},  # stale vs NOW=2026-06-22
        {"promotion_expires_at": None},
        {"schema_version": "unexpected"},
        {"report_only": False},
        {"not_authorization": False},
    ):
        result = evaluate_step2_decision_only(
            effective=effective, verification=valid_step2_verification(effective, **overrides)
        )
        assert result.state == PENDING_GATES_STATE, overrides
        assert "PROMOTED_RESEARCH_DECISION" not in result.allowed_actions


def test_verification_hash_mismatch_stays_pending_gates() -> None:
    effective = effective_handoff()
    mismatched = valid_step2_verification(effective)
    mismatched["effective_handoff_sha256"] = "0" * 64
    mismatched["pointer_effective_handoff_sha256"] = "0" * 64

    result = evaluate_step2_decision_only(effective=effective, verification=mismatched)

    assert result.state == PENDING_GATES_STATE
    assert result.promoted_step2_decision_only is False


def test_raw_strict_fresh_unchanged_by_step2_decision_only_inputs() -> None:
    effective = effective_handoff()
    strategy_settings = settings()
    result = evaluate_research_availability(
        candidate_validation=valid_result(strategy_settings),
        candidate=valid_candidate(),
        strategy_settings=strategy_settings,
        source_as_of_date=NOW,
        now_date=NOW,
        promoted_pointer=pending_pointer(effective),
        promoted_effective_handoff=effective,
        promoted_effective_validation={"valid": True},
        promoted_step2_verification=valid_step2_verification(effective),
        promoted_step2_gate_dry_run=valid_step2_dry_run(),
    )
    assert result.state == "STRICT_FRESH"
    assert result.promoted_step2_decision_only is False
    assert "PROMOTED_RESEARCH_DECISION" not in result.allowed_actions
    assert result.allowed_actions[:2] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in result.allowed_actions and "ORDER_COMPILATION" in result.allowed_actions
