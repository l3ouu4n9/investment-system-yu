from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.common.artifact_management import prepare_next_run
from investment_orchestrator.state.last_good_research_handoff import (
    LastGoodResearchHandoffWriteResult,
    decision_relevant_settings,
    last_good_research_handoff_metadata_path,
    last_good_research_handoff_path,
    last_good_research_handoff_write_result_to_dict,
    strategy_settings_hash,
    write_last_good_research_handoff_if_valid,
)
from investment_orchestrator.validators.validate_research_handoff import (
    ResearchHandoffValidationResult,
    validate_research_handoff,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "step1_contract_failures"
FIXED_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


def read_json_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def valid_candidate() -> dict[str, Any]:
    return read_json_fixture("minimal_valid_research_handoff.json")


def strategy_settings(
    *,
    core_universe: list[str] | None = None,
    satellite_universe: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "core_universe": core_universe or ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": satellite_universe or ["SMH", "IGV"],
    }


def invalid_result() -> ResearchHandoffValidationResult:
    return ResearchHandoffValidationResult(
        valid=False,
        fail_reasons=["Missing execution handoff field: trade_universe"],
        missing_fields=["trade_universe"],
        blocker_reasons=["Missing execution handoff field: trade_universe"],
        non_blocker_reasons=[],
    )


def write_valid(
    out_dir: Path,
    *,
    candidate: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> LastGoodResearchHandoffWriteResult:
    candidate = candidate if candidate is not None else valid_candidate()
    settings = settings if settings is not None else strategy_settings()
    result = validate_research_handoff(candidate, strategy_settings=settings)
    assert result.valid is True
    return write_last_good_research_handoff_if_valid(
        candidate=candidate,
        candidate_validation=result,
        strategy_settings=settings,
        source_run_id="20260622_120000",
        source_as_of_date=candidate.get("as_of"),
        output_dir=out_dir,
        now=FIXED_NOW,
    )


# --- write on valid ----------------------------------------------------------


def test_valid_candidate_writes_handoff_and_metadata(tmp_path: Path) -> None:
    result = write_valid(tmp_path)

    assert result.wrote is True
    assert result.handoff_path == last_good_research_handoff_path(tmp_path)
    assert result.metadata_path == last_good_research_handoff_metadata_path(tmp_path)
    assert result.handoff_path.exists()
    assert result.metadata_path.exists()
    assert result.skip_reasons == []

    written_handoff = json.loads(result.handoff_path.read_text(encoding="utf-8"))
    assert written_handoff == valid_candidate()


def test_metadata_includes_required_fields(tmp_path: Path) -> None:
    result = write_valid(tmp_path)
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))

    assert metadata["source_run_id"] == "20260622_120000"
    assert metadata["source_as_of_date"] == "2026-06-21"
    assert metadata["written_at"] == FIXED_NOW.isoformat()
    assert isinstance(metadata["strategy_settings_hash"], str) and metadata["strategy_settings_hash"]
    assert metadata["handoff_source"] == "research_handoff_candidate"
    assert metadata["report_only"] is True
    assert metadata["schema_version"] == "1.0"
    assert metadata["validation_result"]["valid"] is True
    assert metadata["universe"] == {
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
        "allowed_buy_tickers": ["VOO", "VTI", "VT", "QQQ", "SMH", "IGV"],
    }


def test_source_run_id_and_as_of_recorded_unknown_when_absent_not_fabricated(tmp_path: Path) -> None:
    candidate = valid_candidate()
    result = validate_research_handoff(candidate, strategy_settings=strategy_settings())
    write_result = write_last_good_research_handoff_if_valid(
        candidate=candidate,
        candidate_validation=result,
        strategy_settings=strategy_settings(),
        source_run_id=None,
        source_as_of_date=None,
        output_dir=tmp_path,
        now=FIXED_NOW,
    )

    assert write_result.metadata["source_run_id"] == "unknown"
    assert write_result.metadata["source_as_of_date"] == "unknown"


def test_strategy_settings_unavailable_is_marked_not_fabricated(tmp_path: Path) -> None:
    candidate = valid_candidate()
    result = validate_research_handoff(candidate, strategy_settings=None)
    write_result = write_last_good_research_handoff_if_valid(
        candidate=candidate,
        candidate_validation=result,
        strategy_settings=None,
        source_run_id="r1",
        source_as_of_date="2026-06-21",
        output_dir=tmp_path,
        now=FIXED_NOW,
    )

    assert write_result.wrote is True
    assert write_result.metadata["strategy_settings_available"] is False
    assert write_result.metadata["strategy_settings_hash"] is None
    assert write_result.metadata["strategy_settings_hash_inputs"] == {}
    assert set(write_result.metadata["missing_decision_relevant_settings_keys"]) == {
        "core_universe",
        "satellite_universe",
    }


# --- skip on invalid ---------------------------------------------------------


def test_invalid_candidate_does_not_write(tmp_path: Path) -> None:
    result = write_last_good_research_handoff_if_valid(
        candidate={"schema_version": "1.0"},
        candidate_validation=invalid_result(),
        strategy_settings=strategy_settings(),
        source_run_id="r1",
        source_as_of_date="2026-06-21",
        output_dir=tmp_path,
        now=FIXED_NOW,
    )

    assert result.wrote is False
    assert result.handoff_path is None
    assert result.metadata_path is None
    assert result.skip_reasons
    assert not last_good_research_handoff_path(tmp_path).exists()
    assert not last_good_research_handoff_metadata_path(tmp_path).exists()


def test_non_mapping_candidate_does_not_write_and_does_not_raise(tmp_path: Path) -> None:
    result = write_last_good_research_handoff_if_valid(
        candidate=["not", "a", "mapping"],  # type: ignore[arg-type]
        candidate_validation=invalid_result(),
        strategy_settings=strategy_settings(),
        source_run_id="r1",
        source_as_of_date="2026-06-21",
        output_dir=tmp_path,
        now=FIXED_NOW,
    )

    assert result.wrote is False
    assert any("not a JSON object" in reason for reason in result.skip_reasons)


def test_narrative_unrecoverable_candidate_does_not_write(tmp_path: Path) -> None:
    # A narrative/unrecoverable payload (no strict handoff fields) must never
    # be persisted as last-known-good.
    narrative = read_json_fixture("current_research_output_minimal.json")
    result = validate_research_handoff(narrative, strategy_settings=strategy_settings())
    assert result.valid is False

    write_result = write_last_good_research_handoff_if_valid(
        candidate=narrative,
        candidate_validation=result,
        strategy_settings=strategy_settings(),
        source_run_id="r1",
        source_as_of_date=narrative.get("as_of"),
        output_dir=tmp_path,
        now=FIXED_NOW,
    )

    assert write_result.wrote is False
    assert not last_good_research_handoff_path(tmp_path).exists()


def test_invalid_candidate_does_not_overwrite_existing_last_good(tmp_path: Path) -> None:
    first = write_valid(tmp_path)
    assert first.wrote is True
    handoff_before = last_good_research_handoff_path(tmp_path).read_text(encoding="utf-8")
    metadata_before = last_good_research_handoff_metadata_path(tmp_path).read_text(encoding="utf-8")

    second = write_last_good_research_handoff_if_valid(
        candidate={"schema_version": "1.0", "lane_a": "narrative"},
        candidate_validation=invalid_result(),
        strategy_settings=strategy_settings(),
        source_run_id="r2",
        source_as_of_date="2026-06-28",
        output_dir=tmp_path,
        now=FIXED_NOW,
    )

    assert second.wrote is False
    # Existing last-good preserved byte-for-byte.
    assert last_good_research_handoff_path(tmp_path).read_text(encoding="utf-8") == handoff_before
    assert last_good_research_handoff_metadata_path(tmp_path).read_text(encoding="utf-8") == metadata_before


# --- hashing -----------------------------------------------------------------


def test_hash_is_stable_across_key_order(tmp_path: Path) -> None:
    a = strategy_settings_hash(
        decision_relevant_settings({"core_universe": ["QQQ", "VOO"], "satellite_universe": ["SMH"]})
    )
    b = strategy_settings_hash(
        decision_relevant_settings({"satellite_universe": ["SMH"], "core_universe": ["QQQ", "VOO"]})
    )
    assert a == b


def test_hash_changes_when_universe_changes() -> None:
    base = strategy_settings_hash(decision_relevant_settings(strategy_settings()))
    changed = strategy_settings_hash(
        decision_relevant_settings(strategy_settings(satellite_universe=["SMH", "IGV", "SOXX"]))
    )
    assert base != changed


def test_non_decision_fields_do_not_affect_hash() -> None:
    base = strategy_settings_hash(decision_relevant_settings(strategy_settings()))
    with_noise = strategy_settings_hash(
        decision_relevant_settings(
            {
                **strategy_settings(),
                "as_of": "2026-06-22",
                "run_timestamp_et": "2026-06-22 16:33 ET",
                "benchmark": "QQQ",
                "prompt_text": "irrelevant",
            }
        )
    )
    assert base == with_noise


def test_hash_none_when_settings_unavailable() -> None:
    assert strategy_settings_hash(decision_relevant_settings(None)) is None


# --- state survives prepare_next_run ----------------------------------------


def test_artifacts_state_survives_prepare_next_run(tmp_path: Path) -> None:
    state_dir = tmp_path / "artifacts" / "state"
    write_valid(state_dir)
    assert last_good_research_handoff_path(state_dir).exists()

    # Seed a current run so prepare_next_run actually archives + resets it.
    current_step1 = tmp_path / "artifacts" / "current" / "step1_research"
    current_step1.mkdir(parents=True)
    (current_step1 / "research_output.json").write_text("{}", encoding="utf-8")

    prepare_next_run(root=tmp_path, label="20260629_000000")

    # current/ was reset, but artifacts/state/ last-good survives untouched.
    assert not (tmp_path / "artifacts" / "current" / "step1_research").exists()
    assert last_good_research_handoff_path(state_dir).exists()
    assert last_good_research_handoff_metadata_path(state_dir).exists()


# --- serialization -----------------------------------------------------------


def test_write_result_serialization_round_trips(tmp_path: Path) -> None:
    result = write_valid(tmp_path)
    serialized = last_good_research_handoff_write_result_to_dict(result)

    assert serialized["wrote"] is True
    assert serialized["handoff_path"] == str(result.handoff_path)
    assert serialized["metadata_path"] == str(result.metadata_path)
    assert serialized["skip_reasons"] == []
    assert serialized["metadata"]["handoff_source"] == "research_handoff_candidate"
    # JSON-serializable.
    json.dumps(serialized, ensure_ascii=False)
