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
    extract_canonical_error_token,
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
        ("evidence_packet_write_failed: detail", "evidence_packet_write_failed"),
        ("step1a_embedded_selection_parity_result_unavailable", "step1a_embedded_selection_parity_result_unavailable"),
        ("step1a_embedded_selection_unknown_runtime_timestamp: /tmp/a", "step1a_embedded_selection_unknown_runtime_timestamp"),
        ("step1a_embedded_selection_parity_mismatch: diff_paths=/tmp/a", "step1a_embedded_selection_parity_mismatch"),
        ("step1a_embedded_selection_unexpected_normalized_path: /tmp/a", "step1a_embedded_selection_unexpected_normalized_path"),
        ("step1a_embedded_selection_guard_failed: detail", "step1a_embedded_selection_guard_failed"),
        ("step1a_embedded_selection_skipped_evidence_packet_fallback", "step1a_embedded_selection_skipped_evidence_packet_fallback"),
        ("step1a_accessor_failed: detail", "step1a_accessor_failed"),
        ("embedded_selection_write_failed: detail", "embedded_selection_write_failed"),
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
    assert field in observation["missing_observation_fields"]
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
