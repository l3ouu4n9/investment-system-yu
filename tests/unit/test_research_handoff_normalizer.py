from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from investment_orchestrator.normalizers.research_handoff_candidate import (
    ResearchHandoffNormalizationResult,
    normalize_research_handoff_candidate,
    research_handoff_normalization_result_to_dict,
)
from investment_orchestrator.parsers.extract_research_json import parse_research_output_text
from investment_orchestrator.validators.validate_research_handoff import validate_research_handoff


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "step1_contract_failures"


def read_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def read_json_fixture(name: str) -> dict[str, Any]:
    payload = json.loads(read_fixture(name))
    assert isinstance(payload, dict)
    return payload


def parsed_fixture(name: str) -> dict[str, Any]:
    return parse_research_output_text(read_fixture(name))


def strategy_settings() -> dict[str, Any]:
    return {
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
    }


def valid_handoff() -> dict[str, Any]:
    return read_json_fixture("minimal_valid_research_handoff.json")


# --- A. strict copy-through -------------------------------------------------


def test_strict_handoff_is_copy_through_and_candidate_validates() -> None:
    payload = valid_handoff()

    result = normalize_research_handoff_candidate(payload, strategy_settings=strategy_settings())

    assert result.source_shape == "strict"
    assert result.normalization_mode == "copy_through"
    assert result.applied_transforms == ["copy_through"]
    assert result.missing_or_unrecoverable_fields == []
    assert result.candidate == payload

    candidate_validation = validate_research_handoff(
        result.candidate, strategy_settings=strategy_settings()
    )
    assert candidate_validation.valid is True


def test_copy_through_candidate_is_independent_of_source_payload() -> None:
    payload = valid_handoff()

    result = normalize_research_handoff_candidate(payload)
    result.candidate["trade_universe"]["allowed_buy_tickers"].append("MUTATED")

    assert "MUTATED" not in payload["trade_universe"]["allowed_buy_tickers"]


# --- B. known wrapper unwrap ------------------------------------------------


def test_wrapped_research_json_is_unwrapped_and_transform_recorded() -> None:
    payload = parsed_fixture("wrapped_research_json_minimal.txt")

    result = normalize_research_handoff_candidate(payload)

    assert result.source_shape == "wrapped_RESEARCH_JSON"
    assert result.normalization_mode == "unwrap"
    assert result.applied_transforms == ["unwrap_RESEARCH_JSON"]
    # Candidate is the inner RESEARCH_JSON body, not the envelope.
    assert result.candidate == payload["RESEARCH_JSON"]
    assert "RESEARCH_JSON" not in result.candidate


def test_wrapped_research_json_still_invalid_when_inner_lacks_strict_fields() -> None:
    payload = parsed_fixture("wrapped_research_json_minimal.txt")

    result = normalize_research_handoff_candidate(payload)
    candidate_validation = validate_research_handoff(result.candidate)

    assert candidate_validation.valid is False
    assert "trade_universe" in result.missing_or_unrecoverable_fields
    assert "buy_universe_scorecard" in result.missing_or_unrecoverable_fields
    assert "strategy_a_research_handoff" in result.missing_or_unrecoverable_fields
    assert result.warnings


# --- C. legacy structured normalization -------------------------------------


def test_legacy_structured_renames_known_container_without_synthesizing() -> None:
    payload = read_json_fixture("legacy_structured_missing_handoff.json")

    result = normalize_research_handoff_candidate(payload, strategy_settings=strategy_settings())

    assert result.source_shape == "legacy_structured"
    assert result.normalization_mode == "legacy_normalization"
    assert (
        "rename_legacy_field:strategy_a_handoff->strategy_a_research_handoff"
        in result.applied_transforms
    )
    # Verbatim move only: the value is the legacy container, unchanged.
    assert "strategy_a_handoff" not in result.candidate
    assert result.candidate["strategy_a_research_handoff"] == payload["strategy_a_handoff"]
    # The incomplete scorecard is preserved exactly; rows are not back-filled.
    assert result.candidate["buy_universe_scorecard"] == payload["buy_universe_scorecard"]


def test_legacy_structured_candidate_is_invalid_with_clear_diagnostics() -> None:
    payload = read_json_fixture("legacy_structured_missing_handoff.json")

    result = normalize_research_handoff_candidate(payload, strategy_settings=strategy_settings())
    candidate_validation = validate_research_handoff(
        result.candidate, strategy_settings=strategy_settings()
    )

    assert candidate_validation.valid is False
    joined = "\n".join(candidate_validation.fail_reasons)
    # Renamed legacy container does not satisfy strict v1 markers.
    assert "strategy_a_research_handoff.handoff_version" in joined
    # Incomplete scorecard rows are reported, not synthesized.
    assert "buy_universe_scorecard[0].actionability_status" in candidate_validation.missing_fields
    # Normalization diagnostics enumerate what stayed absent.
    assert result.missing_or_unrecoverable_fields
    assert any("were not synthesized" in warning for warning in result.warnings)


# --- D. narrative / markdown lanes ------------------------------------------


def test_narrative_lane_dicts_are_unrecoverable() -> None:
    payload = parsed_fixture("current_step1_raw_output_minimal.txt")

    result = normalize_research_handoff_candidate(payload)
    candidate_validation = validate_research_handoff(result.candidate)

    assert result.source_shape == "narrative_lanes"
    assert result.normalization_mode == "unrecoverable"
    assert candidate_validation.valid is False
    assert "trade_universe" in result.missing_or_unrecoverable_fields


def test_markdown_lane_strings_are_unrecoverable_without_hallucination() -> None:
    payload = parsed_fixture("archived_deep_research_markdown_lanes_raw_output_minimal.txt")

    result = normalize_research_handoff_candidate(payload)
    candidate_validation = validate_research_handoff(result.candidate)

    assert result.source_shape == "narrative_lanes"
    assert result.normalization_mode == "unrecoverable"
    assert candidate_validation.valid is False
    # base_universe is preserved verbatim but never promoted into trade_universe.
    assert result.candidate["base_universe"] == payload["base_universe"]
    assert any("base_universe" in warning for warning in result.warnings)


# --- No-hallucination guarantees --------------------------------------------


def test_normalizer_does_not_generate_trade_universe_from_prose() -> None:
    for fixture in (
        "current_step1_raw_output_minimal.txt",
        "archived_deep_research_markdown_lanes_raw_output_minimal.txt",
    ):
        result = normalize_research_handoff_candidate(parsed_fixture(fixture))
        assert "trade_universe" not in result.candidate


def test_normalizer_does_not_generate_buy_universe_scorecard() -> None:
    for fixture in (
        "current_step1_raw_output_minimal.txt",
        "archived_deep_research_markdown_lanes_raw_output_minimal.txt",
    ):
        result = normalize_research_handoff_candidate(parsed_fixture(fixture))
        assert "buy_universe_scorecard" not in result.candidate


def test_normalizer_does_not_generate_scheduled_events() -> None:
    for fixture in (
        "current_step1_raw_output_minimal.txt",
        "archived_deep_research_markdown_lanes_raw_output_minimal.txt",
    ):
        result = normalize_research_handoff_candidate(parsed_fixture(fixture))
        assert "scheduled_events" not in result.candidate


def test_unwrap_does_not_synthesize_missing_investment_content() -> None:
    payload = parsed_fixture("wrapped_research_json_minimal.txt")

    result = normalize_research_handoff_candidate(payload)

    for fabricated in ("trade_universe", "buy_universe_scorecard", "scheduled_events"):
        assert fabricated not in result.candidate


def test_legacy_normalization_preserves_explicit_empty_arrays() -> None:
    payload = read_json_fixture("legacy_structured_missing_handoff.json")

    result = normalize_research_handoff_candidate(payload)

    assert result.candidate["scheduled_events"] == []


# --- shape / error handling -------------------------------------------------


def test_non_mapping_payload_is_unknown_and_unrecoverable() -> None:
    result = normalize_research_handoff_candidate([1, 2, 3])  # type: ignore[arg-type]

    assert result.source_shape == "unknown"
    assert result.normalization_mode == "unrecoverable"
    assert result.candidate == {}
    assert result.missing_or_unrecoverable_fields
    validate_research_handoff(result.candidate)  # report-only, must not raise


def test_unknown_object_shape_is_unrecoverable() -> None:
    payload = {"schema_version": "1.0", "some_unrelated_field": {"a": 1}}

    result = normalize_research_handoff_candidate(payload)

    assert result.source_shape == "unknown"
    assert result.normalization_mode == "unrecoverable"
    assert result.warnings


# --- serialization ----------------------------------------------------------


def test_normalization_metadata_serialization_excludes_candidate_body() -> None:
    result = ResearchHandoffNormalizationResult(
        candidate={"schema_version": "1.0"},
        source_shape="narrative_lanes",
        normalization_mode="unrecoverable",
        applied_transforms=["copy_existing_fields"],
        missing_or_unrecoverable_fields=["trade_universe"],
        warnings=["narrative"],
    )

    serialized = research_handoff_normalization_result_to_dict(result)

    assert serialized == {
        "source_shape": "narrative_lanes",
        "normalization_mode": "unrecoverable",
        "applied_transforms": ["copy_existing_fields"],
        "missing_or_unrecoverable_fields": ["trade_universe"],
        "warnings": ["narrative"],
    }
    assert "candidate" not in serialized
