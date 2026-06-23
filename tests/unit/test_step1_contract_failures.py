from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from investment_orchestrator.parsers.extract_research_json import parse_research_output_text
from investment_orchestrator.validators.validate_research_output import validate_research_output


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "step1_contract_failures"

DOWNSTREAM_REQUIRED_HANDOFF_FIELDS = {
    "trade_universe",
    "buy_universe_scorecard",
    "scheduled_events",
    "structural_themes_6_18m",
    "optional_extended_etf_sleeve",
}


def read_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def read_json_fixture(name: str) -> dict[str, Any]:
    payload = json.loads(read_fixture(name))
    assert isinstance(payload, dict)
    return payload


def missing_handoff_fields(payload: dict[str, Any]) -> set[str]:
    return {field for field in DOWNSTREAM_REQUIRED_HANDOFF_FIELDS if field not in payload}


def classify_step1_failure(payload: dict[str, Any]) -> str:
    if missing_handoff_fields(payload):
        return "contract_level_failure"
    return "handoff_contract_satisfied"


def test_parse_success_does_not_imply_handoff_valid() -> None:
    payload = parse_research_output_text(read_fixture("current_step1_raw_output_minimal.txt"))

    assert payload["schema_version"] == "1.0"
    assert payload["validation_summary"]["passed"] is True
    assert missing_handoff_fields(payload) == DOWNSTREAM_REQUIRED_HANDOFF_FIELDS
    assert classify_step1_failure(payload) == "contract_level_failure"


def test_current_research_schema_is_too_weak_for_downstream_contract() -> None:
    payload = read_json_fixture("current_research_output_minimal.json")

    assert validate_research_output(payload) is payload
    assert payload["validation_summary"]["passed"] is True
    assert missing_handoff_fields(payload) == DOWNSTREAM_REQUIRED_HANDOFF_FIELDS


def test_missing_trade_universe_is_contract_failure_not_parser_failure() -> None:
    payload = parse_research_output_text(read_fixture("current_step1_raw_output_minimal.txt"))

    assert "trade_universe" not in payload
    assert "base_universe" not in payload
    assert classify_step1_failure(payload) == "contract_level_failure"


def test_archived_deep_research_markdown_lanes_are_not_strict_handoff() -> None:
    payload = parse_research_output_text(
        read_fixture("archived_deep_research_markdown_lanes_raw_output_minimal.txt")
    )

    assert isinstance(payload["lane_A"], str)
    assert isinstance(payload["lane_B"], str)
    assert payload["base_universe"] == ["VOO", "VTI", "VT", "QQQ", "SMH", "IGV"]
    assert missing_handoff_fields(payload) == {
        "trade_universe",
        "buy_universe_scorecard",
        "scheduled_events",
        "structural_themes_6_18m",
        "optional_extended_etf_sleeve",
    }
    assert classify_step1_failure(payload) == "contract_level_failure"


def test_step2_fallback_data_gap_proves_downstream_degradation() -> None:
    template2_excerpt = read_fixture("archived_step2_template2_degradation_excerpt.txt")

    assert (
        "RESEARCH_JSON.base_universe_fallback_due_missing_trade_universe_allowed_buy_tickers"
        in template2_excerpt
    )
    assert "ASSUMPTIONS_AND_DATA_GAPS" in template2_excerpt
    assert "RESEARCH_JSON lacks trade_universe.allowed_buy_tickers" in template2_excerpt
    assert (
        "RESEARCH_JSON lacks optional_extended_etf_sleeve.enabled and allowed_extended_etf_tickers"
        in template2_excerpt
    )
    assert "structured actionability/linkage fields are insufficient" in template2_excerpt
