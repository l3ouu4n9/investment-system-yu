"""Phase 1A per-run Step 1A retirement-observation tests."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from investment_orchestrator.research.step1a_retirement_observation import (
    build_step1a_retirement_observation,
    canonical_sha256,
    extract_canonical_error_token,
    recompute_observation_identity,
)
from investment_orchestrator.workflow import step1_research

from test_step1a_shadow_run import _read, _settings, _setup_repo


def _builder_inputs() -> dict[str, Any]:
    source_manifest = [
        {
            "source_id": "operator_research_anchors_yaml",
            "sha256": "a" * 64,
            "path": "/absolute/path/that-must-not-leak/research_anchors.yaml",
            "present": True,
            "valid": True,
        },
        {
            "source_id": "operator_research_anchor_approvals_yaml",
            "sha256": "b" * 64,
            "path": "/absolute/path/that-must-not-leak/research_anchor_approvals.yaml",
            "present": True,
            "valid": True,
        },
        {
            "source_id": "operator_research_anchor_revocations_yaml",
            "sha256": "c" * 64,
            "path": "/absolute/path/that-must-not-leak/research_anchor_approvals.yaml",
            "present": True,
            "valid": True,
        },
    ]
    return {
        "generated_at": "2026-07-10T12:00:00+00:00",
        "code_identity": {
            "git_commit": "1" * 40,
            "git_state": "clean",
            "code_version_usable_for_evidence": True,
        },
        "switch_status": {
            "schema_version": "step1a_artifact_switch_status_v1",
            "switched_artifacts": {
                "evidence_packet": {
                    "writer_source": "legacy_fallback",
                    "fallback_used": True,
                    "error_summary": "step1a_evidence_packet_parity_mismatch: diff_paths=/secret/path",
                },
                "embedded_active_anchor_registry_selection": {
                    "writer_source": "step1a",
                    "fallback_used": False,
                    "error_summary": "",
                },
            },
            "evidence_packet_write_invocations": {
                "evidence_packet": {
                    "invocation_count": 2,
                    "first_and_final_statuses_differ": True,
                    "final_disk_write_invocation": 2,
                },
                "embedded_active_anchor_registry_selection": {
                    "invocation_count": 2,
                    "first_and_final_statuses_differ": False,
                    "final_disk_write_invocation": 2,
                },
            },
        },
        "shadow_diff": {
            "schema_version": "step1a_grounding_compile_shadow_diff_v1",
            "comparison_status": "pass",
            "parity_passed": True,
            "comparison_complete": True,
            "skipped_artifacts": [],
            "mismatch_artifacts": [],
            "comparisons": {
                "evidence_packet": {
                    "semantic_match": True,
                    "differences": [],
                    "current_summary": {"parity_unknown_runtime_timestamp_fields": []},
                },
                "embedded_active_anchor_registry_selection": {
                    "semantic_match": True,
                    "differences": [],
                    "current_summary": {"parity_unknown_runtime_timestamp_fields": []},
                },
            },
        },
        "evidence_packet": {
            "schema_version": "evidence_packet_v1",
            "strategy_settings_hash": "d" * 64,
            "strategy_settings_summary": {"as_of": "2026-07-10"},
            "portfolio_snapshot_summary": {"available": True},
            "active_anchor_registry": {"source_manifest": source_manifest},
            "source_artifacts": {"strategy_settings": "/absolute/path/that-must-not-leak"},
        },
        "embedded_selection": {
            "schema_version": "embedded_active_anchor_registry_selection_v1",
            "selected_source": "approvals_inclusive",
        },
        "compiled_support_signals": {
            "schema_version": "compiled_support_signals_v1",
            "accepted_support_signals": [{"sensitive": "not copied"}],
        },
        "grounding_status_observatory": {
            "schema_version": "grounding_status_observatory_v1",
        },
        "research_availability": {
            "state": "STRICT_FRESH",
            "research_availability": "strict_fresh",
            "allowed_actions": ["HOLD", "NEW_BUY", "ORDER_COMPILATION"],
            "new_buy_permission": True,
            "order_compilation_allowed": True,
            "grounded_memo_support_present": False,
            "last_good_available": True,
        },
        "evidence_packet_artifact_present": True,
        "evidence_packet_artifact_parseable": True,
        "compiled_support_signals_artifact_present": True,
        "compiled_support_signals_artifact_parseable": True,
        "observatory_integration_result": "production_sourced",
    }


def _build(**overrides: Any) -> dict[str, Any]:
    values = _builder_inputs()
    values.update(overrides)
    return build_step1a_retirement_observation(**values)


def test_builder_is_pure_deterministic_and_does_not_mutate_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _builder_inputs()
    before = deepcopy(values)

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("pure builder attempted external access")

    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    observation = build_step1a_retirement_observation(**values)
    repeated = build_step1a_retirement_observation(**deepcopy(values))

    assert values == before
    assert observation == repeated
    assert observation["observation_completeness"] == "complete"
    assert observation["assessment_state"] == "observation_only"
    assert "retirement_ready" not in observation
    assert observation["permission_context_observation"]["allowed_actions"] == [
        "HOLD",
        "NEW_BUY",
        "ORDER_COMPILATION",
    ]
    assert observation["permission_context_observation"]["new_buy_allowed"] is True
    assert observation["permission_context_observation"]["order_compilation_allowed"] is True
    # The raw known error suffix and source paths never enter the output.
    serialized = json.dumps(observation, sort_keys=True)
    assert "/secret/path" not in serialized
    assert "/absolute/path/that-must-not-leak" not in serialized
    assert "step1a_evidence_packet_parity_mismatch" in observation["fallback_error_tokens"]


def test_physical_observation_identity_changes_but_logical_coverage_is_stable() -> None:
    first = _build(generated_at="2026-07-10T12:00:00+00:00")
    second = _build(generated_at="2026-07-10T12:00:01+00:00")

    assert first["observation_identity"]["observation_id"] != second["observation_identity"][
        "observation_id"
    ]
    assert first["coverage_identity"] == second["coverage_identity"]


def test_coverage_partitions_clean_code_and_configuration_changes() -> None:
    baseline = _build()
    changed_code = _build(
        code_identity={
            "git_commit": "2" * 40,
            "git_state": "clean",
            "code_version_usable_for_evidence": True,
        }
    )
    changed_config = _builder_inputs()
    changed_config["evidence_packet"] = deepcopy(changed_config["evidence_packet"])
    changed_config["evidence_packet"]["strategy_settings_hash"] = "e" * 64
    changed_configuration_observation = build_step1a_retirement_observation(**changed_config)

    assert baseline["coverage_identity"]["coverage_key"] != changed_code["coverage_identity"][
        "coverage_key"
    ]
    assert baseline["coverage_identity"]["coverage_key"] != changed_configuration_observation[
        "coverage_identity"
    ]["coverage_key"]


@pytest.mark.parametrize(
    "summary, token",
    [
        ("step1a_evidence_packet_parity_result_unavailable", "step1a_evidence_packet_parity_result_unavailable"),
        ("step1a_evidence_packet_unknown_runtime_timestamp: /tmp/a", "step1a_evidence_packet_unknown_runtime_timestamp"),
        ("step1a_evidence_packet_parity_mismatch: diff_paths=/tmp/a", "step1a_evidence_packet_parity_mismatch"),
        ("step1a_evidence_packet_unexpected_normalized_path: /tmp/a", "step1a_evidence_packet_unexpected_normalized_path"),
        ("step1a_evidence_packet_report_only_difference: /tmp/a", "step1a_evidence_packet_report_only_difference"),
        ("legacy_evidence_packet_build_failed: detail", "legacy_evidence_packet_build_failed"),
        ("legacy_evidence_packet_build_failed_upstream: detail", "legacy_evidence_packet_build_failed_upstream"),
        ("evidence_packet_write_failed: detail", "evidence_packet_write_failed"),
        ("evidence_packet_write_failed_upstream: detail", "evidence_packet_write_failed_upstream"),
        ("step1a_embedded_selection_parity_result_unavailable", "step1a_embedded_selection_parity_result_unavailable"),
        ("step1a_embedded_selection_unknown_runtime_timestamp: /tmp/a", "step1a_embedded_selection_unknown_runtime_timestamp"),
        ("step1a_embedded_selection_parity_mismatch: diff_paths=/tmp/a", "step1a_embedded_selection_parity_mismatch"),
        ("step1a_embedded_selection_unexpected_normalized_path: /tmp/a", "step1a_embedded_selection_unexpected_normalized_path"),
        ("step1a_embedded_selection_guard_failed: detail", "step1a_embedded_selection_guard_failed"),
        ("step1a_embedded_selection_skipped_evidence_packet_fallback", "step1a_embedded_selection_skipped_evidence_packet_fallback"),
        ("legacy_selection_capture_empty: detail", "legacy_selection_capture_empty"),
        ("step1a_accessor_failed: detail", "step1a_accessor_failed"),
        ("embedded_selection_write_failed: detail", "embedded_selection_write_failed"),
        ("no_write_invocation_recorded: detail", "no_write_invocation_recorded"),
    ],
)
def test_error_token_extraction_never_keeps_dynamic_suffix(summary: str, token: str) -> None:
    result = extract_canonical_error_token(summary)
    assert result == {
        "canonical_error_token": token,
        "unknown_error_present": False,
        "error_summary_sha256": None,
    }


def test_unknown_error_token_hashes_without_copying_raw_error() -> None:
    raw = "unknown diagnostic includes /very/private/path and operator text"
    result = extract_canonical_error_token(raw)

    assert result["canonical_error_token"] == "unknown_error_token"
    assert result["unknown_error_present"] is True
    assert result["error_summary_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert len(result["error_summary_sha256"]) == 64
    assert result["error_summary_sha256"].islower()
    assert raw not in json.dumps(result)
    assert extract_canonical_error_token("")["canonical_error_token"] == ""


@pytest.mark.parametrize(
    "field, malformed",
    [
        ("switch_status", None),
        ("shadow_diff", []),
        ("evidence_packet", None),
        ("embedded_selection", None),
        ("compiled_support_signals", None),
    ],
)
def test_missing_or_malformed_required_source_fails_closed(field: str, malformed: Any) -> None:
    observation = _build(**{field: malformed})

    assert observation["observation_completeness"] == "incomplete"
    diagnostics = (
        observation["missing_observation_fields"]
        if malformed is None
        else observation["malformed_observation_fields"]
    )
    assert field in diagnostics
    assert "retirement_ready" not in observation


def test_missing_hash_and_unexpected_version_are_incomplete_without_raw_hashing() -> None:
    packet = deepcopy(_builder_inputs()["evidence_packet"])
    packet.pop("strategy_settings_hash")
    switch = deepcopy(_builder_inputs()["switch_status"])
    switch["schema_version"] = "unexpected_version"

    observation = _build(evidence_packet=packet, switch_status=switch)

    assert observation["configuration_hashes"]["strategy_settings_hash"] is None
    assert observation["observation_completeness"] == "incomplete"
    assert "evidence_packet.strategy_settings_hash" in observation["missing_observation_fields"]
    assert "schema_version_unexpected:switch_status" in observation["compatibility_blockers"]


@pytest.mark.parametrize(
    "value, valid",
    [
        ("a" * 64, True),
        ("/secret/raw-strategy-content", False),
        ("", False),
        ("a" * 63, False),
        ("a" * 65, False),
        ("g" * 64, False),
        (42, False),
    ],
)
def test_strategy_hash_must_be_canonical_sha256_and_never_leaks_raw_value(
    value: Any,
    valid: bool,
) -> None:
    packet = deepcopy(_builder_inputs()["evidence_packet"])
    packet["strategy_settings_hash"] = value

    observation = _build(evidence_packet=packet)
    serialized = json.dumps(observation, sort_keys=True)

    if valid:
        assert observation["observation_completeness"] == "complete"
        assert observation["configuration_hashes"]["strategy_settings_hash"] == value
    else:
        assert observation["observation_completeness"] == "incomplete"
        assert observation["configuration_hashes"]["strategy_settings_hash"] is None
        assert "evidence_packet.strategy_settings_hash" in observation[
            "malformed_observation_fields"
        ]
        if value == "/secret/raw-strategy-content":
            assert value not in serialized


@pytest.mark.parametrize(
    "source_id, hash_field",
    [
        ("operator_research_anchors_yaml", "research_anchors_sha256"),
        ("operator_research_anchor_approvals_yaml", "research_anchor_approvals_sha256"),
        ("operator_research_anchor_revocations_yaml", "research_anchor_revocations_sha256"),
    ],
)
def test_source_manifest_hashes_must_be_canonical_sha256(
    source_id: str,
    hash_field: str,
) -> None:
    packet = deepcopy(_builder_inputs()["evidence_packet"])
    entry = next(
        item
        for item in packet["active_anchor_registry"]["source_manifest"]
        if item["source_id"] == source_id
    )
    entry["sha256"] = "/secret/raw-source-content"

    observation = _build(evidence_packet=packet)

    assert observation["observation_completeness"] == "incomplete"
    assert observation["configuration_hashes"][hash_field] is None
    assert "/secret/raw-source-content" not in json.dumps(observation, sort_keys=True)
    assert observation["coverage_identity"]["composite_config_fingerprint"] is None


@pytest.mark.parametrize(
    "summary, expected_diagnostic",
    [
        pytest.param(None, "malformed", id="null"),
        pytest.param([], "malformed", id="list"),
        pytest.param({}, "malformed", id="mapping"),
        pytest.param(7, "malformed", id="integer"),
        pytest.param(True, "malformed", id="boolean"),
    ],
)
def test_malformed_error_summary_is_not_a_valid_empty_summary(
    summary: Any,
    expected_diagnostic: str,
) -> None:
    switch = deepcopy(_builder_inputs()["switch_status"])
    switch["switched_artifacts"]["evidence_packet"]["error_summary"] = summary

    observation = _build(switch_status=switch)
    writer = observation["writer_outcomes"]["evidence_packet"]

    assert observation["observation_completeness"] == "incomplete"
    assert writer["canonical_error_token"] is None
    assert writer["unknown_error_present"] is None
    assert "switch_status.evidence_packet.error_summary" in observation[
        f"{expected_diagnostic}_observation_fields"
    ]


def test_missing_error_summary_is_incomplete_not_a_valid_empty_summary() -> None:
    switch = deepcopy(_builder_inputs()["switch_status"])
    del switch["switched_artifacts"]["evidence_packet"]["error_summary"]

    observation = _build(switch_status=switch)

    assert observation["observation_completeness"] == "incomplete"
    assert observation["writer_outcomes"]["evidence_packet"]["canonical_error_token"] is None
    assert "switch_status.evidence_packet.error_summary" in observation[
        "missing_observation_fields"
    ]


def test_valid_empty_error_summary_is_observed_as_empty_token() -> None:
    switch = deepcopy(_builder_inputs()["switch_status"])
    switch["switched_artifacts"]["evidence_packet"]["fallback_used"] = False
    switch["switched_artifacts"]["evidence_packet"]["error_summary"] = ""

    observation = _build(switch_status=switch)
    writer = observation["writer_outcomes"]["evidence_packet"]

    assert observation["observation_completeness"] == "complete"
    assert writer["canonical_error_token"] == ""
    assert writer["unknown_error_present"] is False
    assert writer["error_summary_sha256"] is None


@pytest.mark.parametrize(
    "value, diagnostics_key",
    [
        ("not-a-timestamp", "malformed_observation_fields"),
        ("", "malformed_observation_fields"),
        (7, "malformed_observation_fields"),
        ("2026-07-10T12:00:00Z", "malformed_observation_fields"),
        ("2026-07-10T12:00:00+01:00", "malformed_observation_fields"),
        ("2026-07-10T12:00:00", "malformed_observation_fields"),
        ("2026-07-10T12:00:00.123+00:00", "malformed_observation_fields"),
        (None, "missing_observation_fields"),
    ],
)
def test_generated_at_must_match_the_production_utc_isoformat_contract(
    value: Any,
    diagnostics_key: str,
) -> None:
    observation = _build(generated_at=value)

    assert observation["observation_completeness"] == "incomplete"
    assert observation["observation_identity"]["generated_at"] is None
    assert observation["observation_identity"]["observation_id"] is None
    assert "observation_identity.generated_at" in observation[diagnostics_key]


def test_production_generated_at_is_valid_and_identifies_observation() -> None:
    observation = _build(generated_at="2026-07-10T12:00:00.123456+00:00")

    assert observation["observation_completeness"] == "complete"
    assert observation["observation_identity"]["generated_at"] == "2026-07-10T12:00:00.123456+00:00"
    assert isinstance(observation["observation_identity"]["observation_id"], str)


def test_actual_grounding_and_permission_observations_do_not_infer_from_writer_status() -> None:
    observation = _build(
        evidence_packet_artifact_present=False,
        evidence_packet_artifact_parseable=False,
        compiled_support_signals_artifact_present=True,
        compiled_support_signals_artifact_parseable=True,
    )

    grounding = observation["grounding_observation"]
    permission = observation["permission_context_observation"]
    assert grounding["evidence_packet_final_artifact_present"] is False
    assert grounding["evidence_packet_final_artifact_parseable"] is False
    assert grounding["compiled_support_signals_present"] is True
    assert grounding["accepted_support_signal_count"] == 1
    # Existing STRICT_FRESH permission facts are copied, not reevaluated or gated
    # by this degraded grounding observation.
    assert permission["research_state"] == "STRICT_FRESH"
    assert permission["new_buy_allowed"] is True
    assert permission["order_compilation_allowed"] is True
    assert observation["observation_completeness"] == "incomplete"


@pytest.mark.parametrize(
    "actions, new_buy_allowed, order_compilation_allowed",
    [
        (["HOLD"], False, False),
        (["HOLD", "NEW_BUY"], True, False),
        (["HOLD", "ORDER_COMPILATION"], False, True),
    ],
)
def test_permission_context_consistency_preserves_existing_observed_values(
    actions: list[str],
    new_buy_allowed: bool,
    order_compilation_allowed: bool,
) -> None:
    availability = deepcopy(_builder_inputs()["research_availability"])
    availability["allowed_actions"] = actions
    availability["new_buy_permission"] = new_buy_allowed
    availability["order_compilation_allowed"] = order_compilation_allowed

    observation = _build(research_availability=availability)
    permission = observation["permission_context_observation"]

    assert observation["observation_completeness"] == "complete"
    assert permission["allowed_actions"] == actions
    assert permission["new_buy_allowed"] is new_buy_allowed
    assert permission["order_compilation_allowed"] is order_compilation_allowed
    assert permission["permission_context_consistent"] is True
    assert observation["permission_context_inconsistencies"] == []


@pytest.mark.parametrize(
    "actions, new_buy_allowed, order_compilation_allowed, expected",
    [
        (
            ["HOLD"],
            True,
            True,
            {
                "permission_context.new_buy_allowed_mismatch_allowed_actions",
                "permission_context.order_compilation_allowed_mismatch_allowed_actions",
            },
        ),
        (
            ["HOLD", "NEW_BUY", "ORDER_COMPILATION"],
            False,
            False,
            {
                "permission_context.new_buy_allowed_mismatch_allowed_actions",
                "permission_context.order_compilation_allowed_mismatch_allowed_actions",
            },
        ),
    ],
)
def test_permission_context_contradictions_are_incomplete_observations_only(
    actions: list[str],
    new_buy_allowed: bool,
    order_compilation_allowed: bool,
    expected: set[str],
) -> None:
    availability = deepcopy(_builder_inputs()["research_availability"])
    availability["allowed_actions"] = actions
    availability["new_buy_permission"] = new_buy_allowed
    availability["order_compilation_allowed"] = order_compilation_allowed

    observation = _build(research_availability=availability)

    assert observation["observation_completeness"] == "incomplete"
    assert observation["permission_context_observation"]["permission_context_consistent"] is False
    assert set(observation["permission_context_inconsistencies"]) == expected
    # These are copied diagnostic facts, never recalculated or changed.
    assert observation["permission_context_observation"]["new_buy_allowed"] is new_buy_allowed
    assert observation["permission_context_observation"]["order_compilation_allowed"] is order_compilation_allowed


@pytest.mark.parametrize(
    "actions",
    [
        ["HOLD", "HOLD"],
        ["HOLD", 1],
        ["HOLD", "UNKNOWN_ACTION"],
        "HOLD",
    ],
)
def test_malformed_allowed_actions_are_incomplete(actions: Any) -> None:
    availability = deepcopy(_builder_inputs()["research_availability"])
    availability["allowed_actions"] = actions

    observation = _build(research_availability=availability)

    assert observation["observation_completeness"] == "incomplete"
    assert observation["permission_context_observation"]["allowed_actions"] is None
    assert observation["permission_context_observation"]["permission_context_consistent"] is None
    assert "research_availability.allowed_actions" in observation["malformed_observation_fields"]


@pytest.mark.parametrize(
    "mutator, field",
    [
        (
            lambda values: values["switch_status"]["switched_artifacts"]["evidence_packet"].update(
                {"writer_source": "raw/path"}
            ),
            "switch_status.evidence_packet.writer_source",
        ),
        (
            lambda values: values["embedded_selection"].update({"selected_source": "raw/path"}),
            "embedded_selection.selected_source",
        ),
        (
            lambda values: values["shadow_diff"].update({"comparison_status": "raw/path"}),
            "shadow_diff.comparison_status",
        ),
        (
            lambda values: values["research_availability"].update({"state": "RAW_STATE"}),
            "research_availability.state",
        ),
    ],
)
def test_bounded_nested_token_validation_fails_closed(
    mutator: Any,
    field: str,
) -> None:
    values = _builder_inputs()
    mutator(values)
    observation = build_step1a_retirement_observation(**values)

    assert observation["observation_completeness"] == "incomplete"
    assert field in observation["malformed_observation_fields"]
    assert "raw/path" not in json.dumps(observation, sort_keys=True)


def test_code_identity_clean_dirty_and_unavailable_semantics() -> None:
    clean = _build()["code_identity"]
    dirty = _build(
        code_identity={"git_commit": "1" * 40, "git_state": "dirty"}
    )["code_identity"]
    unavailable = _build(
        code_identity={"git_commit": None, "git_state": "unavailable"}
    )["code_identity"]

    assert clean["code_version_usable_for_evidence"] is True
    assert dirty["git_state"] == "dirty"
    assert dirty["code_version_usable_for_evidence"] is False
    assert unavailable == {
        "git_commit": None,
        "git_state": "unavailable",
        "code_version_usable_for_evidence": False,
    }


def test_best_effort_code_identity_resolver_is_clean_dirty_or_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="abc123\n"),
            SimpleNamespace(returncode=0, stdout=""),
        ]
    )
    monkeypatch.setattr(step1_research.subprocess, "run", lambda *_args, **_kwargs: next(responses))
    assert step1_research._resolve_step1a_retirement_observation_code_identity(
        repo_root_path=tmp_path
    ) == {
        "git_commit": "abc123",
        "git_state": "clean",
        "code_version_usable_for_evidence": True,
    }

    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="abc123\n"),
            SimpleNamespace(returncode=0, stdout=" M source.py\n"),
        ]
    )
    monkeypatch.setattr(step1_research.subprocess, "run", lambda *_args, **_kwargs: next(responses))
    dirty = step1_research._resolve_step1a_retirement_observation_code_identity(
        repo_root_path=tmp_path
    )
    assert dirty["git_state"] == "dirty"
    assert dirty["code_version_usable_for_evidence"] is False

    monkeypatch.setattr(
        step1_research.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    unavailable = step1_research._resolve_step1a_retirement_observation_code_identity(
        repo_root_path=tmp_path
    )
    assert unavailable["git_state"] == "unavailable"
    assert unavailable["git_state"] != "dirty"

    def broken_run(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("identity resolver failure")

    monkeypatch.setattr(step1_research.subprocess, "run", broken_run)
    assert step1_research._resolve_step1a_retirement_observation_code_identity(
        repo_root_path=tmp_path
    )["git_state"] == "unavailable"


def test_parse_writes_one_final_report_only_observation(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        step1_research,
        "_resolve_step1a_retirement_observation_code_identity",
        lambda: {
            "git_commit": "1" * 40,
            "git_state": "clean",
            "code_version_usable_for_evidence": True,
        },
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())
    observation = _read(step1_research.step1a_retirement_observation_path())

    assert result["step1a_retirement_observation_path"] == str(
        step1_research.step1a_retirement_observation_path()
    )
    assert observation["schema_version"] == "step1a_retirement_observation_v1"
    assert observation["report_only"] is True
    assert observation["not_authorization"] is True
    assert observation["not_execution_authorization"] is True
    assert observation["permission_effect"] == "none"
    assert observation["consumed_by_gates"] is False
    assert observation["consumed_by_order_path"] is False
    assert observation["consumed_by_downstream"] is False
    assert observation["safe_to_ignore"] is True
    assert observation["assessment_state"] == "observation_only"
    assert observation["observation_completeness"] == "complete"
    assert observation["shadow_and_observatory_observation"]["observatory_integration_result"] == (
        "production_sourced"
    )
    assert observation["writer_outcomes"]["evidence_packet"]["final_disk_write_invocation"] == 2
    assert observation["grounding_observation"]["evidence_packet_final_artifact_present"] is True
    assert observation["permission_context_observation"]["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert observation["permission_context_observation"]["new_buy_allowed"] is False
    assert observation["permission_context_observation"]["order_compilation_allowed"] is False
    assert "retirement_ready" not in observation


def _stored_identity(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "composite_config_fingerprint": observation["coverage_identity"][
            "composite_config_fingerprint"
        ],
        "coverage_key": observation["coverage_identity"]["coverage_key"],
        "observation_id": observation["observation_identity"]["observation_id"],
    }


def test_recompute_identity_matches_writer_for_complete_observation() -> None:
    observation = _build()
    assert recompute_observation_identity(observation) == _stored_identity(observation)


@pytest.mark.parametrize(
    "overrides",
    [
        {"generated_at": "not-a-timestamp"},
        {"code_identity": {"git_commit": "1" * 40, "git_state": "dirty"}},
        {"code_identity": {"git_commit": None, "git_state": "unavailable"}},
    ],
)
def test_recompute_identity_matches_writer_for_incomplete_or_degraded(
    overrides: dict[str, Any],
) -> None:
    observation = _build(**overrides)
    assert recompute_observation_identity(observation) == _stored_identity(observation)


def test_recompute_identity_matches_writer_when_config_hash_missing() -> None:
    packet = deepcopy(_builder_inputs()["evidence_packet"])
    packet.pop("strategy_settings_hash")
    observation = _build(evidence_packet=packet)
    recomputed = recompute_observation_identity(observation)
    assert recomputed == _stored_identity(observation)
    assert recomputed["composite_config_fingerprint"] is None


def test_recompute_identity_detects_tampered_configuration_hash() -> None:
    observation = _build()
    tampered = deepcopy(observation)
    tampered["configuration_hashes"]["strategy_settings_hash"] = "f" * 64
    recomputed = recompute_observation_identity(tampered)
    assert recomputed["composite_config_fingerprint"] != tampered["coverage_identity"][
        "composite_config_fingerprint"
    ]


def test_recompute_identity_is_safe_on_non_mapping_input() -> None:
    assert recompute_observation_identity(None) == {
        "composite_config_fingerprint": None,
        "coverage_key": None,
        "observation_id": None,
    }


def test_canonical_sha256_is_order_independent_and_none_for_unserializable() -> None:
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})
    assert len(canonical_sha256({"a": 1})) == 64
    assert canonical_sha256({"x": {1, 2, 3}}) is None
