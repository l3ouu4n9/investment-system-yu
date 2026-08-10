from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.parsers.extract_research_json import parse_research_output_text
from investment_orchestrator.validators.validate_research_handoff import (
    LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION,
    validate_research_handoff,
)
from investment_orchestrator.validators.validate_research_output import validate_research_output
from investment_orchestrator.workflow import step1_research


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "step1_contract_failures"
CORE_UNIVERSE_EMPTY = "strategy_settings core_universe must be a non-empty list."
SATELLITE_UNIVERSE_EMPTY = "strategy_settings satellite_universe must be a non-empty list."


def read_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def read_json_fixture(name: str) -> dict[str, Any]:
    payload = json.loads(read_fixture(name))
    assert isinstance(payload, dict)
    return payload


def valid_handoff() -> dict[str, Any]:
    return read_json_fixture("minimal_valid_research_handoff.json")


def strategy_settings(
    *,
    core_universe: Any = None,
    satellite_universe: Any = None,
) -> dict[str, Any]:
    return {
        "core_universe": (
            ["QQQ", "VOO", "VTI", "VT"] if core_universe is None else core_universe
        ),
        "satellite_universe": (
            ["SMH", "IGV"] if satellite_universe is None else satellite_universe
        ),
    }


def assert_invalid(payload: Any, expected_reason: str) -> None:
    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    assert expected_reason in "\n".join(result.fail_reasons)


def test_regression_fixture_parse_and_research_schema_pass_but_strict_handoff_fails() -> None:
    payload = parse_research_output_text(read_fixture("current_step1_raw_output_minimal.txt"))

    assert validate_research_output(payload) is payload
    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    assert "trade_universe" in result.missing_fields
    assert "buy_universe_scorecard" in result.missing_fields
    assert "strategy_a_research_handoff" in result.missing_fields


def test_validation_summary_passed_true_does_not_make_handoff_valid() -> None:
    payload = read_json_fixture("current_research_output_minimal.json")

    assert payload["validation_summary"]["passed"] is True
    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    assert "Missing execution handoff field: trade_universe" in result.blocker_reasons


def test_missing_trade_universe_allowed_buy_tickers_fails() -> None:
    payload = valid_handoff()
    del payload["trade_universe"]["allowed_buy_tickers"]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    assert "trade_universe.allowed_buy_tickers" in result.missing_fields


def test_missing_buy_universe_scorecard_fails() -> None:
    payload = valid_handoff()
    del payload["buy_universe_scorecard"]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    assert "buy_universe_scorecard" in result.missing_fields


def test_missing_scheduled_events_fails() -> None:
    payload = valid_handoff()
    del payload["scheduled_events"]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    assert "scheduled_events" in result.missing_fields


def test_markdown_lane_strings_are_not_structured_handoff() -> None:
    payload = parse_research_output_text(
        read_fixture("archived_deep_research_markdown_lanes_raw_output_minimal.txt")
    )

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    assert "buy_universe_scorecard" in result.missing_fields
    assert "strategy_a_research_handoff" in result.missing_fields


def test_minimal_valid_handoff_fixture_passes_with_valid_strategy_settings() -> None:
    result = validate_research_handoff(
        valid_handoff(),
        strategy_settings=strategy_settings(),
    )

    assert result.valid is True
    assert result.fail_reasons == []
    assert result.missing_fields == []
    assert result.blocker_reasons == []


def test_strict_validator_contract_version_is_pinned() -> None:
    assert (
        LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION
        == "legacy_research_handoff_strict_validator_v1"
    )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        pytest.param("handoff_version", "unexpected_handoff_v1", id="version"),
        pytest.param("handoff_scope", "unexpected_scope", id="scope"),
        pytest.param("not_order_instruction", False, id="not-order"),
    ],
)
def test_fixed_strategy_a_handoff_literals_fail_closed(
    field_name: str,
    invalid_value: Any,
) -> None:
    payload = valid_handoff()
    payload["strategy_a_research_handoff"][field_name] = invalid_value

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False


def test_validator_derives_required_universe_from_strategy_settings() -> None:
    payload = valid_handoff()

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is True
    assert not any("strategy_settings not provided" in reason for reason in result.non_blocker_reasons)


def test_trade_universe_missing_derived_strategy_settings_ticker_fails() -> None:
    payload = valid_handoff()
    payload["trade_universe"]["allowed_buy_tickers"] = ["QQQ", "VOO", "VTI", "VT", "SMH"]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    assert (
        "trade_universe.allowed_buy_tickers must cover strategy_settings derived buy universe ticker IGV."
        in result.blocker_reasons
    )


def test_trade_universe_may_cover_more_than_derived_strategy_settings_universe() -> None:
    payload = valid_handoff()
    payload["trade_universe"]["allowed_buy_tickers"].append("EXTRA")

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is True
    assert any("includes tickers outside strategy_settings derived buy universe" in reason for reason in result.non_blocker_reasons)


def test_missing_strategy_settings_are_blocking_and_payload_universe_cannot_replace_policy() -> None:
    result = validate_research_handoff(valid_handoff())

    assert result.valid is False
    assert (
        "strategy_settings are unavailable or unusable; deterministic core_universe and "
        "satellite_universe lists are required for research handoff validation."
        in result.blocker_reasons
    )
    assert not any(
        "using RESEARCH_JSON.trade_universe.allowed_buy_tickers" in reason
        for reason in result.non_blocker_reasons
    )


def _assert_actual_strategy_settings_source_failure_is_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(step1_research, "current_inputs_dir", lambda: tmp_path)
    loaded_settings = step1_research.load_strategy_settings_for_handoff_validation()

    assert loaded_settings is None
    result = validate_research_handoff(
        valid_handoff(),
        strategy_settings=loaded_settings,
    )

    assert result.valid is False
    assert any(
        "strategy_settings are unavailable or unusable" in reason
        for reason in result.blocker_reasons
    )


def test_missing_strategy_settings_source_is_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_actual_strategy_settings_source_failure_is_blocking(tmp_path, monkeypatch)


def test_nonregular_strategy_settings_source_is_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "strategy_settings.yaml").mkdir()

    _assert_actual_strategy_settings_source_failure_is_blocking(tmp_path, monkeypatch)


def test_malformed_strategy_settings_source_is_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "strategy_settings.yaml").write_text(
        "core_universe: [QQQ\n",
        encoding="utf-8",
    )

    _assert_actual_strategy_settings_source_failure_is_blocking(tmp_path, monkeypatch)


def test_strategy_settings_source_validation_failure_is_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "strategy_settings.yaml").write_text(
        "daily_execution_drift_policy: []\n",
        encoding="utf-8",
    )

    _assert_actual_strategy_settings_source_failure_is_blocking(tmp_path, monkeypatch)


def test_missing_core_universe_policy_is_blocking() -> None:
    result = validate_research_handoff(
        valid_handoff(),
        strategy_settings={"satellite_universe": ["SMH", "IGV"]},
    )

    assert result.valid is False
    assert (
        "strategy_settings core_universe and satellite_universe must both be lists."
        in result.blocker_reasons
    )


def test_missing_satellite_universe_policy_is_blocking() -> None:
    result = validate_research_handoff(
        valid_handoff(),
        strategy_settings={"core_universe": ["QQQ", "VOO", "VTI", "VT"]},
    )

    assert result.valid is False
    assert (
        "strategy_settings core_universe and satellite_universe must both be lists."
        in result.blocker_reasons
    )


@pytest.mark.parametrize(
    ("invalid_settings", "expected_empty_blockers"),
    [
        pytest.param(
            strategy_settings(core_universe=[]),
            [CORE_UNIVERSE_EMPTY],
            id="empty-core",
        ),
        pytest.param(
            strategy_settings(satellite_universe=[]),
            [SATELLITE_UNIVERSE_EMPTY],
            id="empty-satellite",
        ),
        pytest.param(
            strategy_settings(core_universe=[], satellite_universe=[]),
            [CORE_UNIVERSE_EMPTY, SATELLITE_UNIVERSE_EMPTY],
            id="both-empty",
        ),
    ],
)
def test_empty_strategy_universe_lists_are_blocking_and_payload_cannot_replace_them(
    invalid_settings: dict[str, Any],
    expected_empty_blockers: list[str],
) -> None:
    payload = valid_handoff()
    assert payload["trade_universe"]["allowed_buy_tickers"]

    result = validate_research_handoff(
        payload,
        strategy_settings=invalid_settings,
    )

    assert result.valid is False
    assert [
        reason
        for reason in result.blocker_reasons
        if reason in {CORE_UNIVERSE_EMPTY, SATELLITE_UNIVERSE_EMPTY}
    ] == expected_empty_blockers
    for reason in expected_empty_blockers:
        assert reason in result.fail_reasons
    assert not any(
        "using RESEARCH_JSON.trade_universe.allowed_buy_tickers" in reason
        for reason in result.non_blocker_reasons
    )


@pytest.mark.parametrize(
    "invalid_settings",
    [
        pytest.param(
            {"core_universe": "QQQ", "satellite_universe": ["SMH"]},
            id="core-not-list",
        ),
        pytest.param(
            {"core_universe": ["QQQ"], "satellite_universe": {"SMH": True}},
            id="satellite-not-list",
        ),
        pytest.param(
            {"core_universe": ["QQQ", 7], "satellite_universe": ["SMH"]},
            id="core-item-not-string",
        ),
        pytest.param(
            {"core_universe": ["QQQ"], "satellite_universe": ["SMH", 7]},
            id="satellite-item-not-string",
        ),
        pytest.param(
            {"core_universe": ["QQQ", ""], "satellite_universe": ["SMH"]},
            id="core-item-empty",
        ),
        pytest.param(
            {"core_universe": ["QQQ"], "satellite_universe": ["SMH", " "]},
            id="satellite-item-empty",
        ),
        pytest.param(["not", "a", "mapping"], id="settings-not-mapping"),
    ],
)
def test_invalid_strategy_universe_structure_or_type_is_blocking(
    invalid_settings: Any,
) -> None:
    result = validate_research_handoff(
        valid_handoff(),
        strategy_settings=invalid_settings,
    )

    assert result.valid is False
    assert any("strategy_settings" in reason for reason in result.blocker_reasons)


def test_allowed_buy_tickers_string_type_fails() -> None:
    payload = valid_handoff()
    payload["trade_universe"]["allowed_buy_tickers"] = "VOO"

    assert_invalid(payload, "trade_universe.allowed_buy_tickers must be a list")


def test_empty_allowed_buy_tickers_fails() -> None:
    payload = valid_handoff()
    payload["trade_universe"]["allowed_buy_tickers"] = []

    assert_invalid(payload, "trade_universe.allowed_buy_tickers must be non-empty")


def test_scorecard_must_cover_allowed_buy_universe() -> None:
    payload = valid_handoff()
    payload["buy_universe_scorecard"] = [
        row for row in payload["buy_universe_scorecard"] if row["ticker"] != "IGV"
    ]

    assert_invalid(payload, "buy_universe_scorecard must cover allowed buy ticker IGV")


def test_optional_extended_etf_sleeve_requires_explicit_disabled_gate() -> None:
    payload = valid_handoff()
    payload["optional_extended_etf_sleeve"]["disable_reason"] = ""

    assert_invalid(payload, "optional_extended_etf_sleeve.disable_reason must explain the disabled gate")


def test_enabled_extended_etf_requires_scorecard_coverage_and_gate_consistency() -> None:
    payload = valid_handoff()
    payload["optional_extended_etf_sleeve"]["enabled"] = True
    payload["optional_extended_etf_sleeve"]["allowed_extended_etf_tickers"] = ["GRID"]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    joined = "\n".join(result.fail_reasons)
    assert "extended_etf_scorecard must cover enabled extended ETF ticker GRID" in joined
    assert (
        "effective_allowed_extended_etf_tickers_this_run must match "
        "optional_extended_etf_sleeve.allowed_extended_etf_tickers"
    ) in joined


def test_sleeve_disabled_allows_missing_extended_etf_scorecard_with_gate_reason() -> None:
    payload = valid_handoff()
    del payload["extended_etf_scorecard"]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is True
    assert any("extended_etf_scorecard missing while extended ETF sleeve is disabled" in reason for reason in result.non_blocker_reasons)


def test_sleeve_enabled_requires_extended_etf_scorecard_row_detail() -> None:
    payload = valid_handoff()
    payload["optional_extended_etf_sleeve"]["enabled"] = True
    payload["optional_extended_etf_sleeve"]["disable_reason"] = ""
    payload["optional_extended_etf_sleeve"]["why_not_enabled"] = ""
    payload["optional_extended_etf_sleeve"]["allowed_extended_etf_tickers"] = ["GRID"]
    payload["strategy_a_research_handoff"]["extended_lane_downstream_gate"][
        "effective_allowed_extended_etf_tickers_this_run"
    ] = ["GRID"]
    payload["strategy_a_research_handoff"]["extended_lane_downstream_gate"]["disable_reason"] = ""
    payload["strategy_a_research_handoff"]["extended_lane_downstream_gate"]["why_not_enabled"] = ""
    payload["extended_etf_scorecard"] = [{"ticker": "GRID"}]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    joined = "\n".join(result.fail_reasons)
    assert "extended_etf_scorecard[0].event_id_refs" in joined
    assert "extended_etf_scorecard[0].structural_theme_refs" in joined


def test_sleeve_enabled_passes_with_extended_etf_scorecard_coverage_and_detail() -> None:
    payload = valid_handoff()
    payload["optional_extended_etf_sleeve"]["enabled"] = True
    payload["optional_extended_etf_sleeve"]["disable_reason"] = ""
    payload["optional_extended_etf_sleeve"]["why_not_enabled"] = ""
    payload["optional_extended_etf_sleeve"]["allowed_extended_etf_tickers"] = ["GRID"]
    payload["strategy_a_research_handoff"]["extended_lane_downstream_gate"][
        "effective_allowed_extended_etf_tickers_this_run"
    ] = ["GRID"]
    payload["strategy_a_research_handoff"]["extended_lane_downstream_gate"]["disable_reason"] = ""
    payload["strategy_a_research_handoff"]["extended_lane_downstream_gate"]["why_not_enabled"] = ""
    payload["extended_etf_scorecard"] = [
        {
            "ticker": "GRID",
            "event_id_refs": [],
            "structural_theme_refs": ["theme_ai_infrastructure_12m"],
            "llm_evidence_tier": "adequate_evidence",
            "llm_model_risk_notes": "Evidence is adequate for report-only validation.",
        }
    ]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is True


def test_approved_static_list_empty_allows_empty_screening_log() -> None:
    payload = valid_handoff()

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is True


def test_approved_static_list_nonempty_requires_screening_coverage() -> None:
    payload = valid_handoff()
    payload["user_approved_extended_etf_static_list"] = ["GRID"]

    assert_invalid(payload, "approved_static_list_screening_log must cover approved static-list ticker GRID")


def test_proposed_candidates_empty_allows_empty_candidate_and_predecision_scorecards() -> None:
    payload = valid_handoff()

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is True


def test_proposed_candidates_nonempty_requires_candidate_and_predecision_coverage() -> None:
    payload = valid_handoff()
    payload["proposed_extended_etf_candidates"] = [
        {"ticker": "GRID", "admitted_to_candidate_universe": True}
    ]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    joined = "\n".join(result.fail_reasons)
    assert "extended_etf_candidate_universe must cover proposed candidate ticker GRID" in joined
    assert "extended_etf_predecision_scorecard must cover proposed candidate ticker GRID" in joined


def test_proposed_candidates_nonempty_passes_with_candidate_and_predecision_coverage() -> None:
    payload = valid_handoff()
    payload["proposed_extended_etf_candidates"] = [
        {"ticker": "GRID", "admitted_to_candidate_universe": True}
    ]
    payload["extended_etf_candidate_universe"] = [{"ticker": "GRID"}]
    payload["extended_etf_predecision_scorecard"] = [{"ticker": "GRID"}]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is True


def test_proposed_only_candidates_do_not_require_candidate_or_predecision_coverage() -> None:
    payload = valid_handoff()
    payload["proposed_extended_etf_candidates"] = [
        {"ticker": "GRID", "lane_b_status": "proposed_only"}
    ]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is True


def test_scheduled_events_validation_is_shallow_for_now() -> None:
    payload = valid_handoff()
    payload["scheduled_events"] = [{"date_et": "not-a-date", "id": "event_without_deep_validation"}]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is True


def test_scheduled_events_rows_must_be_objects() -> None:
    payload = valid_handoff()
    payload["scheduled_events"] = ["event_without_object_shape"]

    assert_invalid(payload, "scheduled_events[0] must be an object")


def test_actionable_data_gap_marker_is_blocker() -> None:
    payload = valid_handoff()
    payload = deepcopy(payload)
    first = payload["buy_universe_scorecard"][0]
    first["actionability_status"] = "actionable_this_run"
    first["event_id_refs"] = ["event_ai_capex"]
    first["primary_anchor_event_id"] = "event_ai_capex"
    first["primary_anchor_date_et"] = "2026-06-24"
    first["thesis_12m_plus_summary"] = "DATA_GAP: no linkage"
    payload["strategy_a_research_handoff"]["positive_delta_research_supported"] = [first["ticker"]]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    assert any("contains handoff uncertainty marker" in reason for reason in result.blocker_reasons)


def test_actionable_unknown_marker_is_blocker() -> None:
    payload = valid_handoff()
    payload = deepcopy(payload)
    first = payload["buy_universe_scorecard"][0]
    first["actionability_status"] = "actionable_this_run"
    first["event_id_refs"] = ["event_ai_capex"]
    first["primary_anchor_event_id"] = "event_ai_capex"
    first["primary_anchor_date_et"] = "2026-06-24"
    first["thesis_12m_plus_summary"] = "unknown thesis support"
    payload["strategy_a_research_handoff"]["positive_delta_research_supported"] = [first["ticker"]]

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is False
    assert any("contains handoff uncertainty marker" in reason for reason in result.blocker_reasons)


def test_watch_only_data_gap_marker_is_classified_non_blocker() -> None:
    payload = valid_handoff()
    payload["buy_universe_scorecard"][0]["thesis_12m_plus_summary"] = "DATA_GAP: watch only"

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is True
    assert any("contains handoff uncertainty marker" in reason for reason in result.non_blocker_reasons)


def test_compile_blocked_data_gap_marker_is_classified_non_blocker() -> None:
    payload = valid_handoff()
    payload["buy_universe_scorecard"][0]["actionability_status"] = "compile_blocked"
    payload["buy_universe_scorecard"][0]["compile_blocker_if_any"] = "DATA_GAP: compile blocked"

    result = validate_research_handoff(payload, strategy_settings=strategy_settings())

    assert result.valid is True
    assert any("contains handoff uncertainty marker" in reason for reason in result.non_blocker_reasons)
