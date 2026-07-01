from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.workflow import step1_research
from investment_orchestrator.state.last_good_research_handoff import (
    last_good_research_handoff_metadata_path,
    last_good_research_handoff_path,
)
from investment_orchestrator.validators.validate_research_handoff import (
    ResearchHandoffValidationResult,
    research_handoff_validation_result_to_dict,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "step1_contract_failures"


def read_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def read_json_fixture(name: str) -> dict[str, Any]:
    payload = json.loads(read_fixture(name))
    assert isinstance(payload, dict)
    return payload


def write_step1_raw_output(tmp_path: Path, raw_output: str) -> Path:
    artifact_dir = tmp_path / "artifacts" / "current" / "step1_research"
    artifact_dir.mkdir(parents=True)
    raw_path = artifact_dir / "raw_output.txt"
    raw_path.write_text(raw_output, encoding="utf-8")
    return raw_path


def write_strategy_settings(
    tmp_path: Path,
    *,
    core_universe: list[str] | None = None,
    satellite_universe: list[str] | None = None,
) -> Path:
    inputs_dir = tmp_path / "inputs" / "current"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    path = inputs_dir / "strategy_settings.yaml"
    core = core_universe or ["QQQ", "VOO", "VTI", "VT"]
    satellite = satellite_universe or ["SMH", "IGV"]
    path.write_text(
        "core_universe:\n"
        + "".join(f"  - {ticker}\n" for ticker in core)
        + "satellite_universe:\n"
        + "".join(f"  - {ticker}\n" for ticker in satellite),
        encoding="utf-8",
    )
    return path


def minimal_strategy_settings() -> dict[str, Any]:
    return {
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
    }


def parse_with_tmp_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_output: str,
    *,
    strategy_settings: dict[str, Any] | None = None,
) -> dict[str, str]:
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    write_step1_raw_output(tmp_path, raw_output)
    if strategy_settings is None:
        return step1_research.parse_step1_output()
    return step1_research.parse_step1_output(strategy_settings=strategy_settings)


def marked_research_json(payload: dict[str, Any]) -> str:
    return (
        "RESEARCH_JSON_START\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\nRESEARCH_JSON_END\n"
    )


def test_step1_parse_writes_report_only_handoff_validation_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        read_fixture("current_step1_raw_output_minimal.txt"),
    )

    validation_path = Path(result["research_handoff_validation_path"])
    validation = json.loads(validation_path.read_text(encoding="utf-8"))

    assert validation_path.name == "research_handoff_validation.json"
    assert validation["valid"] is False
    assert "trade_universe" in validation["missing_fields"]
    assert validation["fail_reasons"]
    assert Path(result["research_output_path"]).exists()


def test_invalid_handoff_does_not_fail_step1_report_only_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        read_fixture("current_step1_raw_output_minimal.txt"),
    )

    assert result["schema_version"] == "1.0"
    assert result["research_handoff_valid"] == "False"
    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_handoff_validation_path"]).exists()


def test_minimal_valid_handoff_writes_valid_true_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(read_json_fixture("minimal_valid_research_handoff.json")),
    )

    validation = json.loads(
        Path(result["research_handoff_validation_path"]).read_text(encoding="utf-8")
    )

    assert result["research_handoff_valid"] == "True"
    assert validation["valid"] is True
    assert validation["fail_reasons"] == []
    assert validation["missing_fields"] == []
    assert validation["blocker_reasons"] == []
    assert any(
        "strategy_settings not provided" in reason
        for reason in validation["non_blocker_reasons"]
    )


def test_step1_handoff_report_uses_strategy_settings_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_strategy_settings(tmp_path)

    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(read_json_fixture("minimal_valid_research_handoff.json")),
    )

    validation = json.loads(
        Path(result["research_handoff_validation_path"]).read_text(encoding="utf-8")
    )

    assert validation["valid"] is True
    assert not any(
        "strategy_settings not provided" in reason
        for reason in validation["non_blocker_reasons"]
    )


def test_step1_handoff_report_can_reuse_loaded_strategy_settings_argument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        step1_research,
        "load_strategy_settings_for_handoff_validation",
        lambda: (_ for _ in ()).throw(AssertionError("should not reload settings")),
    )

    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(read_json_fixture("minimal_valid_research_handoff.json")),
        strategy_settings=minimal_strategy_settings(),
    )

    validation = json.loads(
        Path(result["research_handoff_validation_path"]).read_text(encoding="utf-8")
    )

    assert validation["valid"] is True
    assert not any(
        "strategy_settings not provided" in reason
        for reason in validation["non_blocker_reasons"]
    )


def test_step1_handoff_report_records_missing_derived_universe_ticker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = read_json_fixture("minimal_valid_research_handoff.json")
    payload["trade_universe"]["allowed_buy_tickers"] = ["QQQ", "VOO", "VTI", "VT", "SMH"]

    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(payload),
        strategy_settings=minimal_strategy_settings(),
    )

    validation = json.loads(
        Path(result["research_handoff_validation_path"]).read_text(encoding="utf-8")
    )

    assert result["schema_version"] == "1.0"
    assert result["research_handoff_valid"] == "False"
    assert Path(result["research_output_path"]).exists()
    assert (
        "trade_universe.allowed_buy_tickers must cover strategy_settings derived buy universe ticker IGV."
        in validation["blocker_reasons"]
    )


def test_research_handoff_validation_serialization_is_stable() -> None:
    result = ResearchHandoffValidationResult(
        valid=False,
        fail_reasons=["failed"],
        missing_fields=["trade_universe"],
        blocker_reasons=["blocked"],
        non_blocker_reasons=["watch only"],
    )

    assert research_handoff_validation_result_to_dict(result) == {
        "valid": False,
        "fail_reasons": ["failed"],
        "missing_fields": ["trade_universe"],
        "blocker_reasons": ["blocked"],
        "non_blocker_reasons": ["watch only"],
    }


def test_step1_report_only_does_not_change_research_output_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = read_json_fixture("current_research_output_minimal.json")

    result = parse_with_tmp_repo(tmp_path, monkeypatch, marked_research_json(payload))
    research_output = json.loads(Path(result["research_output_path"]).read_text(encoding="utf-8"))

    assert research_output == payload
    assert Path(result["research_handoff_validation_path"]).exists()


def test_step1_writes_candidate_normalization_and_candidate_validation_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        read_fixture("current_step1_raw_output_minimal.txt"),
    )

    candidate_path = Path(result["research_handoff_candidate_path"])
    normalization_path = Path(result["research_handoff_candidate_normalization_path"])
    candidate_validation_path = Path(result["research_handoff_candidate_validation_path"])

    assert candidate_path.name == "research_handoff_candidate.json"
    assert normalization_path.name == "research_handoff_candidate_normalization.json"
    assert candidate_validation_path.name == "research_handoff_candidate_validation.json"
    assert candidate_path.exists()
    assert normalization_path.exists()
    assert candidate_validation_path.exists()

    normalization = json.loads(normalization_path.read_text(encoding="utf-8"))
    assert normalization["source_shape"] == "narrative_lanes"
    assert normalization["normalization_mode"] == "unrecoverable"
    assert normalization["applied_transforms"] == ["copy_existing_fields"]
    assert "trade_universe" in normalization["missing_or_unrecoverable_fields"]
    assert normalization["warnings"]
    # Normalization metadata is the diagnostics layer only, not the candidate body.
    assert "candidate" not in normalization

    candidate_validation = json.loads(candidate_validation_path.read_text(encoding="utf-8"))
    assert candidate_validation["valid"] is False
    assert result["research_handoff_candidate_valid"] == "False"
    assert result["research_handoff_candidate_source_shape"] == "narrative_lanes"
    assert result["research_handoff_candidate_normalization_mode"] == "unrecoverable"


def test_step1_raw_and_candidate_validation_artifacts_are_distinct(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        read_fixture("current_step1_raw_output_minimal.txt"),
    )

    raw_validation_path = Path(result["research_handoff_validation_path"])
    candidate_validation_path = Path(result["research_handoff_candidate_validation_path"])

    # The raw handoff validation artifact is preserved and not replaced by the
    # candidate validation artifact.
    assert raw_validation_path.name == "research_handoff_validation.json"
    assert raw_validation_path.exists()
    assert raw_validation_path != candidate_validation_path
    assert raw_validation_path.read_text(encoding="utf-8") != ""


def test_step1_candidate_invalid_does_not_fail_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        read_fixture("current_step1_raw_output_minimal.txt"),
    )

    # Candidate is unrecoverable/invalid, yet parse returns normally and all
    # artifacts exist (report-only, never blocking).
    assert result["research_handoff_candidate_valid"] == "False"
    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_handoff_candidate_path"]).exists()
    assert Path(result["research_handoff_candidate_validation_path"]).exists()


def test_step1_strict_payload_candidate_is_copy_through_and_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = read_json_fixture("minimal_valid_research_handoff.json")

    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(payload),
        strategy_settings=minimal_strategy_settings(),
    )

    candidate = json.loads(
        Path(result["research_handoff_candidate_path"]).read_text(encoding="utf-8")
    )
    candidate_validation = json.loads(
        Path(result["research_handoff_candidate_validation_path"]).read_text(encoding="utf-8")
    )

    assert candidate == payload
    assert result["research_handoff_candidate_source_shape"] == "strict"
    assert result["research_handoff_candidate_normalization_mode"] == "copy_through"
    assert result["research_handoff_candidate_valid"] == "True"
    assert candidate_validation["valid"] is True


def test_step1_candidate_validation_uses_strategy_settings_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_strategy_settings(tmp_path)

    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(read_json_fixture("minimal_valid_research_handoff.json")),
    )

    candidate_validation = json.loads(
        Path(result["research_handoff_candidate_validation_path"]).read_text(encoding="utf-8")
    )

    assert candidate_validation["valid"] is True
    # Settings-aware context flows into the candidate validator: with settings
    # present the backward-compatible "strategy_settings not provided" note is
    # absent.
    assert not any(
        "strategy_settings not provided" in reason
        for reason in candidate_validation["non_blocker_reasons"]
    )


def test_step1_candidate_artifacts_do_not_change_research_output_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = read_json_fixture("current_research_output_minimal.json")

    result = parse_with_tmp_repo(tmp_path, monkeypatch, marked_research_json(payload))
    research_output = json.loads(Path(result["research_output_path"]).read_text(encoding="utf-8"))

    # research_output.json is untouched by the candidate layer, and the
    # candidate is a separate artifact from research_output.json.
    assert research_output == payload
    candidate_path = Path(result["research_handoff_candidate_path"])
    assert candidate_path.exists()
    assert candidate_path != Path(result["research_output_path"])


# --- PR B: last-known-good handoff state writer (report-only) ----------------


def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts" / "state"


def overwrite_step1_raw_output(tmp_path: Path, raw_output: str) -> None:
    raw_path = tmp_path / "artifacts" / "current" / "step1_research" / "raw_output.txt"
    raw_path.write_text(raw_output, encoding="utf-8")


def test_step1_writes_last_good_when_candidate_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(read_json_fixture("minimal_valid_research_handoff.json")),
        strategy_settings=minimal_strategy_settings(),
    )

    assert result["research_handoff_candidate_valid"] == "True"
    assert result["last_good_research_handoff_written"] == "True"

    handoff_path = last_good_research_handoff_path(state_dir(tmp_path))
    metadata_path = last_good_research_handoff_metadata_path(state_dir(tmp_path))
    assert handoff_path.exists()
    assert metadata_path.exists()
    assert result["last_good_research_handoff_path"] == str(handoff_path)

    written = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert written == read_json_fixture("minimal_valid_research_handoff.json")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["report_only"] is True
    assert metadata["handoff_source"] == "research_handoff_candidate"
    assert metadata["validation_result"]["valid"] is True
    assert metadata["universe"]["allowed_buy_tickers"]

    # Per-run report-only write-result artifact is emitted.
    write_result = json.loads(
        Path(result["last_good_research_handoff_write_result_path"]).read_text(encoding="utf-8")
    )
    assert write_result["wrote"] is True


def test_step1_does_not_write_last_good_when_candidate_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        read_fixture("current_step1_raw_output_minimal.txt"),
    )

    # Narrative/unrecoverable candidate -> report-only, no last-good written.
    assert result["research_handoff_candidate_valid"] == "False"
    assert result["last_good_research_handoff_written"] == "False"
    assert result["last_good_research_handoff_path"] == ""
    assert not last_good_research_handoff_path(state_dir(tmp_path)).exists()
    assert not last_good_research_handoff_metadata_path(state_dir(tmp_path)).exists()

    # Step 1 parse still succeeded and existing artifacts are intact.
    assert Path(result["research_output_path"]).exists()
    assert Path(result["research_handoff_validation_path"]).exists()

    write_result = json.loads(
        Path(result["last_good_research_handoff_write_result_path"]).read_text(encoding="utf-8")
    )
    assert write_result["wrote"] is False
    assert write_result["skip_reasons"]


def test_step1_invalid_run_does_not_overwrite_existing_last_good(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First run: valid strict handoff writes last-good.
    parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(read_json_fixture("minimal_valid_research_handoff.json")),
        strategy_settings=minimal_strategy_settings(),
    )
    handoff_path = last_good_research_handoff_path(state_dir(tmp_path))
    handoff_before = handoff_path.read_text(encoding="utf-8")
    metadata_before = last_good_research_handoff_metadata_path(state_dir(tmp_path)).read_text(
        encoding="utf-8"
    )

    # Second run in the same repo: a narrative/invalid output must not clear or
    # overwrite the existing last-good.
    overwrite_step1_raw_output(tmp_path, read_fixture("current_step1_raw_output_minimal.txt"))
    second = step1_research.parse_step1_output(strategy_settings=minimal_strategy_settings())

    assert second["last_good_research_handoff_written"] == "False"
    assert handoff_path.read_text(encoding="utf-8") == handoff_before
    assert last_good_research_handoff_metadata_path(state_dir(tmp_path)).read_text(
        encoding="utf-8"
    ) == metadata_before


def test_step1_last_good_layer_does_not_change_research_output_or_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = read_json_fixture("minimal_valid_research_handoff.json")

    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(payload),
        strategy_settings=minimal_strategy_settings(),
    )

    # research_output.json equals the parsed payload, and the raw handoff
    # validation artifact still exists and is distinct from the last-good state.
    research_output = json.loads(Path(result["research_output_path"]).read_text(encoding="utf-8"))
    assert research_output == payload
    assert Path(result["research_handoff_validation_path"]).exists()
    assert last_good_research_handoff_path(state_dir(tmp_path)) != Path(result["research_output_path"])


# --- PR C: research availability / freshness / degraded decision (report-only) ---


def settings_with_as_of(as_of: str) -> dict[str, Any]:
    return {**minimal_strategy_settings(), "as_of": as_of}


def test_step1_valid_candidate_writes_availability_artifacts_strict_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = read_json_fixture("minimal_valid_research_handoff.json")  # as_of 2026-06-21
    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(payload),
        strategy_settings=settings_with_as_of("2026-06-22"),
    )

    availability_path = Path(result["research_availability_path"])
    freshness_path = Path(result["research_freshness_report_path"])
    decision_path = Path(result["research_degraded_mode_decision_path"])
    assert availability_path.name == "research_availability.json"
    assert freshness_path.name == "research_freshness_report.json"
    assert decision_path.name == "research_degraded_mode_decision.json"
    assert availability_path.exists() and freshness_path.exists() and decision_path.exists()

    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert result["research_availability_state"] == "STRICT_FRESH"
    assert result["research_availability_fresh"] == "True"
    assert decision["state"] == "STRICT_FRESH"
    assert "NEW_BUY" in decision["allowed_actions"]
    assert decision["report_only"] is True

    freshness = json.loads(freshness_path.read_text(encoding="utf-8"))
    assert freshness["stale_label"] == "fresh"
    assert freshness["handoff_age_days"] == 1


def test_step1_invalid_raw_candidate_with_compiled_handoff_is_strict_fresh_evidence_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # R2E.1: pre-R2E this run was INVALID_CONTRACT. Now the deterministic compiled
    # evidence-first handoff (Step 1C, valid + fresh) is recognized as
    # STRICT_FRESH_EVIDENCE_ONLY — still HOLD / NO_TRADE only, never NEW_BUY.
    result = parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        read_fixture("current_step1_raw_output_minimal.txt"),
        strategy_settings=settings_with_as_of("2026-06-22"),
    )

    decision = json.loads(
        Path(result["research_degraded_mode_decision_path"]).read_text(encoding="utf-8")
    )
    assert result["research_availability_state"] == "STRICT_FRESH_EVIDENCE_ONLY"
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in decision["blocked_actions"]
    assert "ORDER_COMPILATION" in decision["blocked_actions"]
    assert decision["fresh_research_available"] is False
    assert decision["source"] == "compiled_research_handoff"
    assert decision["compilation_mode"] == "evidence_only"
    assert decision["permission_effect"] == "none"
    assert any("evidence_only_no_new_buy" in r for r in decision["blocker_reasons"])


def test_step1_invalid_candidate_with_last_good_prefers_compiled_evidence_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First run: valid strict handoff writes last-good (as_of 2026-06-21).
    parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(read_json_fixture("minimal_valid_research_handoff.json")),
        strategy_settings=settings_with_as_of("2026-06-21"),
    )
    assert last_good_research_handoff_path(state_dir(tmp_path)).exists()

    # Second run: narrative/invalid output. Pre-R2E this was DEGRADED_WITH_LAST_GOOD;
    # R2E.1 prefers the valid+fresh compiled handoff -> STRICT_FRESH_EVIDENCE_ONLY.
    # Permissions remain HOLD / NO_TRADE either way.
    overwrite_step1_raw_output(tmp_path, read_fixture("current_step1_raw_output_minimal.txt"))
    second = step1_research.parse_step1_output(strategy_settings=settings_with_as_of("2026-06-22"))

    decision = json.loads(
        Path(second["research_degraded_mode_decision_path"]).read_text(encoding="utf-8")
    )
    assert second["research_availability_state"] == "STRICT_FRESH_EVIDENCE_ONLY"
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in decision["blocked_actions"]
    assert decision["source"] == "compiled_research_handoff"


def test_step1_availability_layer_is_report_only_and_does_not_fail_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = read_json_fixture("current_research_output_minimal.json")

    result = parse_with_tmp_repo(tmp_path, monkeypatch, marked_research_json(payload))

    # Parse succeeded; existing artifacts unchanged; availability artifact is report-only.
    research_output = json.loads(Path(result["research_output_path"]).read_text(encoding="utf-8"))
    assert research_output == payload
    assert Path(result["research_handoff_validation_path"]).exists()
    assert Path(result["research_handoff_candidate_path"]).exists()
    availability = json.loads(
        Path(result["research_availability_path"]).read_text(encoding="utf-8")
    )
    assert availability["report_only"] is True


# --- PR C.1: no-output / parse-failure degraded artifacts (report-only) -------


def assert_degraded_artifacts_hold_no_trade_only(
    tmp_path: Path,
    *,
    expected_state: str,
) -> dict[str, Any]:
    artifact_dir = tmp_path / "artifacts" / "current" / "step1_research"
    availability_path = artifact_dir / "research_availability.json"
    freshness_path = artifact_dir / "research_freshness_report.json"
    decision_path = artifact_dir / "research_degraded_mode_decision.json"

    assert availability_path.exists()
    assert freshness_path.exists()
    assert decision_path.exists()

    availability = json.loads(availability_path.read_text(encoding="utf-8"))
    freshness = json.loads(freshness_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))

    assert availability["state"] == expected_state
    assert freshness["state"] == expected_state
    assert decision["state"] == expected_state
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in decision["blocked_actions"]
    assert "ORDER_COMPILATION" in decision["blocked_actions"]
    assert decision["report_only"] is True
    assert decision["diagnostic_reason"] == (
        "step1 parse failed before research_output.json was produced."
    )
    return decision


def test_step1_missing_raw_output_writes_no_output_artifacts_and_preserves_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)

    with pytest.raises(FileNotFoundError):
        step1_research.parse_step1_output(strategy_settings=settings_with_as_of("2026-06-22"))

    decision = assert_degraded_artifacts_hold_no_trade_only(
        tmp_path,
        expected_state="NO_OUTPUT",
    )
    assert "no research output and no last-known-good available." in decision["blocker_reasons"]
    assert not (tmp_path / "artifacts" / "current" / "step1_research" / "research_output.json").exists()
    assert not last_good_research_handoff_path(state_dir(tmp_path)).exists()


def test_step1_empty_raw_output_writes_no_output_artifacts_and_preserves_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    write_step1_raw_output(tmp_path, "")

    with pytest.raises(Exception, match="Could not find RESEARCH_JSON_START/END"):
        step1_research.parse_step1_output(strategy_settings=settings_with_as_of("2026-06-22"))

    decision = assert_degraded_artifacts_hold_no_trade_only(
        tmp_path,
        expected_state="NO_OUTPUT",
    )
    assert "Could not find RESEARCH_JSON_START/END" in decision["parse_error"]


def test_step1_whitespace_only_raw_output_writes_no_output_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    write_step1_raw_output(tmp_path, " \n\t\n")

    with pytest.raises(Exception, match="Could not find RESEARCH_JSON_START/END"):
        step1_research.parse_step1_output(strategy_settings=settings_with_as_of("2026-06-22"))

    assert_degraded_artifacts_hold_no_trade_only(tmp_path, expected_state="NO_OUTPUT")


def test_step1_malformed_raw_output_writes_degraded_artifacts_before_reraising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    write_step1_raw_output(
        tmp_path,
        "RESEARCH_JSON_START\n{\"schema_version\": \nRESEARCH_JSON_END\n",
    )

    with pytest.raises(Exception):
        step1_research.parse_step1_output(strategy_settings=settings_with_as_of("2026-06-22"))

    decision = assert_degraded_artifacts_hold_no_trade_only(
        tmp_path,
        expected_state="NO_OUTPUT",
    )
    assert decision["parse_error"]


def test_step1_no_output_with_usable_last_good_is_degraded_with_last_good(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parse_with_tmp_repo(
        tmp_path,
        monkeypatch,
        marked_research_json(read_json_fixture("minimal_valid_research_handoff.json")),
        strategy_settings=settings_with_as_of("2026-06-21"),
    )
    handoff_path = last_good_research_handoff_path(state_dir(tmp_path))
    metadata_path = last_good_research_handoff_metadata_path(state_dir(tmp_path))
    handoff_before = handoff_path.read_text(encoding="utf-8")
    metadata_before = metadata_path.read_text(encoding="utf-8")

    overwrite_step1_raw_output(tmp_path, "")
    with pytest.raises(Exception, match="Could not find RESEARCH_JSON_START/END"):
        step1_research.parse_step1_output(strategy_settings=settings_with_as_of("2026-06-22"))

    decision = assert_degraded_artifacts_hold_no_trade_only(
        tmp_path,
        expected_state="DEGRADED_WITH_LAST_GOOD",
    )
    assert decision["non_blocker_reasons"]
    assert handoff_path.read_text(encoding="utf-8") == handoff_before
    assert metadata_path.read_text(encoding="utf-8") == metadata_before


def test_step34_do_not_read_step1_degraded_mode_artifacts() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow_paths = [
        root / "src" / "investment_orchestrator" / "workflow" / "step3_audit_engine.py",
        root / "src" / "investment_orchestrator" / "workflow" / "step4_order_compiler.py",
    ]
    forbidden = (
        "research_availability.json",
        "research_freshness_report.json",
        "research_degraded_mode_decision.json",
        "read_last_good_research_handoff",
    )

    for path in workflow_paths:
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text


def test_step2_reads_only_research_permission_for_degraded_mode_gate() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "src" / "investment_orchestrator" / "workflow" / "step2_decision_builder.py"
    text = path.read_text(encoding="utf-8")

    assert "step1_research_degraded_mode_decision_path" in text
    assert "research_availability.json" not in text
    assert "research_freshness_report.json" not in text
    assert "read_last_good_research_handoff" not in text
