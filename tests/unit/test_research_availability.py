from __future__ import annotations

import json
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
