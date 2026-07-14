"""S1A-12 embedded-selection disk-writer switch tests.

Eighth artifact switch. ``embedded_active_registry_selection.json`` (filename
unchanged; canonical key ``embedded_active_anchor_registry_selection``) is now
written from the Step 1A selection capture behind a full-payload parity guard
that is lineage-coupled to the S1A-11 evidence-packet guard: the Step 1A
selection reaches disk only when the SAME pass's packet guard chose the Step 1A
packet AND ``compare_embedded_selection_parity`` reports a complete match. On
any other outcome the legacy capture (today's exact bytes) is written with
production provenance. The evidence-packet writer runs more than once per parse
(layer 0, then the handoff/compiler read-back preparation), so switch status
reports the invocation that produced the FINAL disk bytes. Nothing here grants
permissions, gates, allowed_actions, or any order-path authority.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.research.evidence_packet import (
    build_evidence_packet_and_selection,
    compare_evidence_packet_runtime_parity,
)
from investment_orchestrator.workflow import step1_research
from investment_orchestrator.workflow.step1a_grounding_compile import (
    STEP1A_WRITER_SOURCE_ARTIFACTS,
    build_step1a_evidence_packet,
)

from test_step1a_evidence_packet_switch import _norm_gen, _scrub, _write_inputs
from test_step1a_shadow_run import _read, _settings, _setup_repo


_GEN_AT = "2026-06-28T12:00:00+00:00"
_SELECTION_KEY = "embedded_active_anchor_registry_selection"

_WRAPPER_FIELDS = (
    "consumed_by_gates",
    "consumed_by_order_path",
    "consumed_by_downstream",
    "cannot_affect_allowed_actions",
    "cannot_affect_registry_selection",
    "not_registry_selection_input",
    "not_order_input",
    "production_source",
    "step1a_output",
    "safe_to_ignore",
)


def _selection_entry(status: dict[str, Any]) -> dict[str, Any]:
    return status["switched_artifacts"][_SELECTION_KEY]


def _mismatch_parity(_prod: Any, _step1a: Any) -> dict[str, Any]:
    """A comparator stub reporting a payload mismatch (content-free paths only)."""
    return {
        "payload_match": False,
        "differences": [{"path": "readiness.ready", "reason": "value_differs"}],
        "normalized_paths": ["generated_at"],
        "unknown_runtime_timestamp_fields": [],
    }


# --- capture reuse / accessor invariance ----------------------------------------


def test_out_param_does_not_change_step1a_packet_bytes_or_guard(tmp_path: Path) -> None:
    """Adding ``embedded_selection_out`` changes NOTHING about the candidate.

    Same inputs + same generated_at: the packet bytes are identical with and
    without the capture, the capture IS the selection the packet embedded, and
    the S1A-11 guard predicate (runtime parity vs the legacy build) is unchanged.
    """
    anchors_path, approvals_path = _write_inputs(tmp_path)
    settings = _settings()
    common: dict[str, Any] = dict(
        strategy_settings=settings,
        portfolio_snapshot_text="Cash: 1,000.00",
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=approvals_path,
        source_artifacts={"research_anchors": str(anchors_path)},
        generated_at=_GEN_AT,
        now_date=settings["as_of"],
    )
    without_capture = build_step1a_evidence_packet(**common)
    capture: dict[str, Any] = {}
    with_capture = build_step1a_evidence_packet(**common, embedded_selection_out=capture)

    assert with_capture == without_capture
    assert capture  # populated
    assert capture["selected_registry"] == with_capture["active_anchor_registry"]

    legacy = build_evidence_packet_and_selection(**common)
    parity = compare_evidence_packet_runtime_parity(legacy, with_capture)
    assert parity["subtree_match"] is True
    assert parity["report_only_differences"] == []


def test_legacy_and_step1a_captures_are_full_payload_equal(tmp_path: Path) -> None:
    """The two captures the guarded writer compares are byte-identical on clean inputs."""
    anchors_path, approvals_path = _write_inputs(tmp_path)
    settings = _settings()
    common: dict[str, Any] = dict(
        strategy_settings=settings,
        portfolio_snapshot_text=None,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=approvals_path,
        generated_at=_GEN_AT,
        now_date=settings["as_of"],
    )
    legacy_capture: dict[str, Any] = {}
    build_evidence_packet_and_selection(**common, embedded_selection_out=legacy_capture)
    step1a_capture: dict[str, Any] = {}
    build_step1a_evidence_packet(**common, embedded_selection_out=step1a_capture)

    assert json.loads(json.dumps(step1a_capture)) == json.loads(json.dumps(legacy_capture))


# --- guard-pass integration -------------------------------------------------------


def test_clean_run_writes_step1a_selection_with_truthful_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["evidence_packet_writer_source"] == "step1a"
    assert result["embedded_active_anchor_registry_selection_writer_source"] == "step1a"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = _selection_entry(status)
    assert entry["writer_source"] == "step1a"
    assert entry["fallback_used"] is False
    assert entry["error_summary"] == ""
    assert entry["output_path"] == str(
        step1_research.step1_embedded_active_registry_selection_path()
    )
    # Exactly eight switched artifacts; the constant matches; no ninth.
    assert len(status["switched_artifacts"]) == 8
    assert sorted(STEP1A_WRITER_SOURCE_ARTIFACTS) == sorted(status["switched_artifacts"])
    assert status["embedded_selection_uses_step1a_output"] is True
    assert status["evidence_packet_uses_step1a_output"] is True
    assert status["support_signals_uses_step1a_output"] is False
    assert status["readiness_uses_step1a_output"] is False
    assert status["order_path_uses_step1a_output"] is False
    assert status["runtime_authority_uses_step1a_output"] is False
    assert status["production_artifact_paths_switched"] is False

    # Disk artifact: Step 1A capture + truthful wrapper provenance, filename
    # unchanged, all non-authority markers intact.
    artifact = _read(step1_research.step1_embedded_active_registry_selection_path())
    assert artifact["production_source"] is False
    assert artifact["step1a_output"] is True
    for marker in ("consumed_by_gates", "consumed_by_order_path", "consumed_by_downstream"):
        assert artifact[marker] is False
    for marker in (
        "cannot_affect_allowed_actions",
        "cannot_affect_registry_selection",
        "not_registry_selection_input",
        "not_order_input",
        "safe_to_ignore",
    ):
        assert artifact[marker] is True
    packet = _read(step1_research.step1_evidence_packet_path())
    assert artifact["selected_registry"] == packet["active_anchor_registry"]

    # Diagnostic only — never opens an order path.
    decision = _read(Path(result["research_degraded_mode_decision_path"]))
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]


def test_clean_run_records_both_write_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parse flow writes twice (layer 0 + compiler read-back prep); the
    report-only multi-write diagnostics record both and the final entry is the
    second invocation's status."""
    _setup_repo(tmp_path, monkeypatch)

    step1_research.parse_step1_output(strategy_settings=_settings())

    status = _read(step1_research.step1a_artifact_switch_status_path())
    invocations = status["evidence_packet_write_invocations"]
    for artifact_key in ("evidence_packet", _SELECTION_KEY):
        diag = invocations[artifact_key]
        assert diag["invocation_count"] == 2
        assert diag["writer_sources"] == ["step1a", "step1a"]
        assert diag["first_writer_source"] == "step1a"
        assert diag["final_writer_source"] == "step1a"
        assert diag["final_disk_write_invocation"] == 2
        assert diag["first_and_final_statuses_differ"] is False


# --- fallback branches -------------------------------------------------------------


def test_accessor_failure_selection_falls_back_with_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("accessor exploded")

    monkeypatch.setattr(
        step1_research,
        "_build_step1a_evidence_packet_from_sanitized_inputs",
        _boom,
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["evidence_packet_writer_source"] == "legacy_fallback"
    assert result["embedded_active_anchor_registry_selection_writer_source"] == "legacy_fallback"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = _selection_entry(status)
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_accessor_failed" in entry["error_summary"]

    # The legacy capture reached disk with production provenance and still
    # matches the (legacy) packet's embedded registry.
    artifact = _read(step1_research.step1_embedded_active_registry_selection_path())
    assert artifact["production_source"] is True
    assert artifact["step1a_output"] is False
    packet = _read(step1_research.step1_evidence_packet_path())
    assert artifact["selected_registry"] == packet["active_anchor_registry"]


def test_packet_guard_fallback_forces_selection_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lineage coupling: a packet-only divergence (the selection captures are
    IDENTICAL) still forces the selection to the legacy capture."""
    _setup_repo(tmp_path, monkeypatch)
    real_accessor = step1_research._build_step1a_evidence_packet_from_sanitized_inputs

    def _diff_report_only(**kwargs: Any) -> dict[str, Any]:
        packet = real_accessor(**kwargs)  # populates embedded_selection_out untouched
        source_artifacts = packet.get("source_artifacts")
        base = dict(source_artifacts) if isinstance(source_artifacts, dict) else {}
        base["injected_report_only_field"] = "diagnostic-only"
        packet["source_artifacts"] = base
        return packet

    monkeypatch.setattr(
        step1_research,
        "_build_step1a_evidence_packet_from_sanitized_inputs",
        _diff_report_only,
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["evidence_packet_writer_source"] == "legacy_fallback"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    packet_entry = status["switched_artifacts"]["evidence_packet"]
    assert "step1a_evidence_packet_report_only_difference" in packet_entry["error_summary"]

    entry = _selection_entry(status)
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert entry["error_summary"] == "step1a_embedded_selection_skipped_evidence_packet_fallback"

    artifact = _read(step1_research.step1_embedded_active_registry_selection_path())
    assert artifact["production_source"] is True
    assert artifact["step1a_output"] is False
    packet = _read(step1_research.step1_evidence_packet_path())
    assert artifact["selected_registry"] == packet["active_anchor_registry"]


def test_selection_parity_mismatch_writes_legacy_and_leaves_packet_step1a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(step1_research, "compare_embedded_selection_parity", _mismatch_parity)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Packet status independence: the packet stays Step 1A-sourced.
    assert result["evidence_packet_writer_source"] == "step1a"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    assert status["switched_artifacts"]["evidence_packet"]["writer_source"] == "step1a"
    assert status["switched_artifacts"]["evidence_packet"]["error_summary"] == ""

    entry = _selection_entry(status)
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_embedded_selection_parity_mismatch" in entry["error_summary"]
    assert "readiness.ready" in entry["error_summary"]

    # Legacy bytes on disk; the packet guard already proved registry equality,
    # so the selection still matches the (Step 1A) packet's embedded registry.
    artifact = _read(step1_research.step1_embedded_active_registry_selection_path())
    assert artifact["production_source"] is True
    assert artifact["step1a_output"] is False
    packet = _read(step1_research.step1_evidence_packet_path())
    assert artifact["selected_registry"] == packet["active_anchor_registry"]


def test_unknown_timestamp_and_unexpected_path_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def _unknown_ts(_prod: Any, _step1a: Any) -> dict[str, Any]:
        return {
            "payload_match": False,
            "differences": [],
            "normalized_paths": ["generated_at"],
            "unknown_runtime_timestamp_fields": ["readiness.evaluated_at"],
        }

    monkeypatch.setattr(step1_research, "compare_embedded_selection_parity", _unknown_ts)
    step1_research.parse_step1_output(strategy_settings=_settings())
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = _selection_entry(status)
    assert entry["writer_source"] == "legacy_fallback"
    assert "step1a_embedded_selection_unknown_runtime_timestamp" in entry["error_summary"]
    artifact = _read(step1_research.step1_embedded_active_registry_selection_path())
    assert artifact["production_source"] is True

    def _unexpected_path(_prod: Any, _step1a: Any) -> dict[str, Any]:
        return {
            "payload_match": True,
            "differences": [],
            "normalized_paths": ["generated_at", "nested_extra.generated_at"],
            "unknown_runtime_timestamp_fields": [],
        }

    monkeypatch.setattr(step1_research, "compare_embedded_selection_parity", _unexpected_path)
    step1_research.parse_step1_output(strategy_settings=_settings())
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = _selection_entry(status)
    assert entry["writer_source"] == "legacy_fallback"
    assert "step1a_embedded_selection_unexpected_normalized_path" in entry["error_summary"]
    assert status["switched_artifacts"]["evidence_packet"]["writer_source"] == "step1a"


def test_selection_write_failure_leaves_packet_status_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    selection_path = step1_research.step1_embedded_active_registry_selection_path()
    real_write_json = step1_research.write_json

    def _selective_write(path: Any, payload: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(path) == selection_path:
            raise RuntimeError("selection write exploded")
        return real_write_json(path, payload, *args, **kwargs)

    monkeypatch.setattr(step1_research, "write_json", _selective_write)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Packet unaffected — Step 1A payload written and recorded.
    assert result["evidence_packet_writer_source"] == "step1a"
    assert not selection_path.is_file()
    assert result["embedded_active_anchor_registry_selection_writer_source"] == "unwritten"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    assert status["switched_artifacts"]["evidence_packet"]["writer_source"] == "step1a"
    entry = _selection_entry(status)
    assert entry["writer_source"] == "unwritten"
    assert "embedded_selection_write_failed" in entry["error_summary"]

    # No false pass: the shadow reports the absent artifact as an explicit skip.
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    embedded = diff["comparisons"][_SELECTION_KEY]
    assert embedded["comparison_skipped"] is True
    assert diff["comparison_status"] == "pass_with_skips"
    assert diff["parity_passed"] is False


def test_empty_legacy_capture_leaves_artifact_unwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No legacy capture -> nothing written (no unverified Step 1A payload)."""
    _setup_repo(tmp_path, monkeypatch)
    real_legacy = step1_research._build_evidence_packet_and_selection_from_sanitized_source

    def _no_capture(**kwargs: Any) -> dict[str, Any]:
        kwargs.pop("embedded_selection_out", None)  # suppress the capture only
        return real_legacy(**kwargs)

    monkeypatch.setattr(
        step1_research,
        "_build_evidence_packet_and_selection_from_sanitized_source",
        _no_capture,
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # The packet decision is unaffected (identical payloads either way).
    assert result["evidence_packet_writer_source"] == "step1a"
    assert result["embedded_active_anchor_registry_selection_writer_source"] == "unwritten"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = _selection_entry(status)
    assert entry["writer_source"] == "unwritten"
    assert "legacy_selection_capture_empty" in entry["error_summary"]
    assert not step1_research.step1_embedded_active_registry_selection_path().is_file()


# --- double-invocation / final-status truthfulness --------------------------------


def test_first_step1a_second_fallback_final_status_is_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invocation 2 overwrites invocation 1: switch status must report the final
    (legacy-fallback) disk bytes, never the first write's step1a provenance."""
    _setup_repo(tmp_path, monkeypatch)
    real_accessor = step1_research._build_step1a_evidence_packet_from_sanitized_inputs
    calls = {"n": 0}

    def _second_call_fails(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("accessor exploded on the second invocation")
        return real_accessor(**kwargs)

    monkeypatch.setattr(
        step1_research,
        "_build_step1a_evidence_packet_from_sanitized_inputs",
        _second_call_fails,
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert calls["n"] >= 2
    assert result["evidence_packet_writer_source"] == "legacy_fallback"
    assert result["embedded_active_anchor_registry_selection_writer_source"] == "legacy_fallback"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    for artifact_key in ("evidence_packet", _SELECTION_KEY):
        entry = status["switched_artifacts"][artifact_key]
        assert entry["writer_source"] == "legacy_fallback", artifact_key
        assert entry["fallback_used"] is True
        assert "step1a_accessor_failed" in entry["error_summary"]
        diag = status["evidence_packet_write_invocations"][artifact_key]
        assert diag["invocation_count"] == 2
        assert diag["writer_sources"] == ["step1a", "legacy_fallback"]
        assert diag["first_writer_source"] == "step1a"
        assert diag["final_writer_source"] == "legacy_fallback"
        assert diag["final_disk_write_invocation"] == 2
        assert diag["first_and_final_statuses_differ"] is True

    # Final disk bytes and wrapper provenance match the final status.
    artifact = _read(step1_research.step1_embedded_active_registry_selection_path())
    assert artifact["production_source"] is True
    assert artifact["step1a_output"] is False


def test_first_fallback_second_step1a_final_status_is_step1a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status write happens AFTER the final invocation: a first-write
    fallback followed by a clean second write reports step1a — proving the
    switch status describes final disk contents, not first-write provenance."""
    _setup_repo(tmp_path, monkeypatch)
    real_accessor = step1_research._build_step1a_evidence_packet_from_sanitized_inputs
    calls = {"n": 0}

    def _first_call_fails(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("accessor exploded on the first invocation")
        return real_accessor(**kwargs)

    monkeypatch.setattr(
        step1_research,
        "_build_step1a_evidence_packet_from_sanitized_inputs",
        _first_call_fails,
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert calls["n"] >= 2
    assert result["evidence_packet_writer_source"] == "step1a"
    assert result["embedded_active_anchor_registry_selection_writer_source"] == "step1a"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    for artifact_key in ("evidence_packet", _SELECTION_KEY):
        entry = status["switched_artifacts"][artifact_key]
        assert entry["writer_source"] == "step1a", artifact_key
        assert entry["fallback_used"] is False
        assert entry["error_summary"] == ""
        diag = status["evidence_packet_write_invocations"][artifact_key]
        assert diag["invocation_count"] == 2
        assert diag["writer_sources"] == ["legacy_fallback", "step1a"]
        assert diag["final_disk_write_invocation"] == 2
        assert diag["first_and_final_statuses_differ"] is True

    artifact = _read(step1_research.step1_embedded_active_registry_selection_path())
    assert artifact["production_source"] is False
    assert artifact["step1a_output"] is True


def test_second_invocation_write_failure_retains_first_artifact_and_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed second packet write leaves invocation 1's files on disk; the
    final status must describe those retained bytes — NOT ``unwritten``."""
    _setup_repo(tmp_path, monkeypatch)

    packet_path = step1_research.step1_evidence_packet_path()
    real_write_json = step1_research.write_json
    packet_writes = {"n": 0}

    def _second_packet_write_fails(path: Any, payload: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(path) == packet_path:
            packet_writes["n"] += 1
            if packet_writes["n"] >= 2:
                raise RuntimeError("second packet write exploded")
        return real_write_json(path, payload, *args, **kwargs)

    monkeypatch.setattr(step1_research, "write_json", _second_packet_write_fails)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert packet_writes["n"] >= 2
    # Both artifacts still on disk from invocation 1.
    assert packet_path.is_file()
    assert step1_research.step1_embedded_active_registry_selection_path().is_file()
    assert result["evidence_packet_writer_source"] == "step1a"
    assert result["embedded_active_anchor_registry_selection_writer_source"] == "step1a"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    packet_entry = status["switched_artifacts"]["evidence_packet"]
    assert packet_entry["writer_source"] == "step1a"  # retained first-write truth
    selection_entry = _selection_entry(status)
    assert selection_entry["writer_source"] == "step1a"

    packet_diag = status["evidence_packet_write_invocations"]["evidence_packet"]
    assert packet_diag["invocation_count"] == 2
    assert packet_diag["writer_sources"] == ["step1a", "unwritten"]
    assert packet_diag["final_disk_write_invocation"] == 1
    # First and FINAL statuses agree (both are invocation 1); the failed second
    # attempt is visible in writer_sources.
    assert packet_diag["first_and_final_statuses_differ"] is False

    selection_diag = status["evidence_packet_write_invocations"][_SELECTION_KEY]
    assert selection_diag["writer_sources"] == ["step1a", "unwritten"]
    assert selection_diag["final_disk_write_invocation"] == 1

    # The disk artifacts really are invocation 1's step1a-provenance bytes.
    artifact = _read(step1_research.step1_embedded_active_registry_selection_path())
    assert artifact["production_source"] is False
    assert artifact["step1a_output"] is True


def test_resolver_handles_arbitrary_logs() -> None:
    """Unit coverage for N-invocation logs and the defensive empty-log path."""
    entry = lambda source, error="": {  # noqa: E731 - concise fixture builder
        "artifact": "evidence_packet",
        "output_path": "/x/evidence_packet.json",
        "writer_source": source,
        "fallback_used": source == "legacy_fallback",
        "error_summary": error,
    }
    sel_entry = lambda source, error="": {  # noqa: E731
        **entry(source, error),
        "artifact": "embedded_active_anchor_registry_selection",
    }

    log = [
        {"evidence_packet": entry("step1a"), "embedded_active_anchor_registry_selection": sel_entry("step1a")},
        {"evidence_packet": entry("legacy_fallback", "step1a_accessor_failed: x"), "embedded_active_anchor_registry_selection": sel_entry("legacy_fallback", "step1a_accessor_failed: x")},
        {"evidence_packet": entry("unwritten", "evidence_packet_write_failed: y"), "embedded_active_anchor_registry_selection": sel_entry("unwritten", "evidence_packet_write_failed_upstream")},
    ]
    packet_final, selection_final, diagnostics = (
        step1_research._resolve_final_evidence_packet_write_statuses(log)
    )
    # Final = last invocation that WROTE (invocation 2), not the unwritten third.
    assert packet_final["writer_source"] == "legacy_fallback"
    assert selection_final["writer_source"] == "legacy_fallback"
    diag = diagnostics["evidence_packet"]
    assert diag["invocation_count"] == 3
    assert diag["writer_sources"] == ["step1a", "legacy_fallback", "unwritten"]
    assert diag["final_disk_write_invocation"] == 2
    assert diag["first_and_final_statuses_differ"] is True

    # Defensive empty log: unwritten with an explicit token; never a false pass.
    packet_final, selection_final, diagnostics = (
        step1_research._resolve_final_evidence_packet_write_statuses([])
    )
    assert packet_final["writer_source"] == "unwritten"
    assert packet_final["error_summary"] == "no_write_invocation_recorded"
    assert selection_final["artifact"] == "embedded_active_anchor_registry_selection"
    assert diagnostics["evidence_packet"]["invocation_count"] == 0
    assert diagnostics["evidence_packet"]["final_disk_write_invocation"] == 0


# --- shadow behavior ----------------------------------------------------------------


def test_selection_fallback_is_status_visible_even_when_shadow_summary_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documented expectation: a comparator-level fallback whose divergence is
    invisible to the 8-field shadow summary still surfaces in switch status —
    the status file, not the shadow, is the designated fallback signal."""
    _setup_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(step1_research, "compare_embedded_selection_parity", _mismatch_parity)

    step1_research.parse_step1_output(strategy_settings=_settings())

    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = _selection_entry(status)
    assert entry["writer_source"] == "legacy_fallback"
    assert "step1a_embedded_selection_parity_mismatch" in entry["error_summary"]

    # The legacy bytes equal the Step 1A bytes on clean inputs, so the shadow's
    # semantic summary still matches — expected, and never a false COMPLETE
    # pass hiding an absent artifact.
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    embedded = diff["comparisons"][_SELECTION_KEY]
    assert embedded["comparison_skipped"] is False
    assert embedded["semantic_match"] is True
    assert diff["comparison_status"] == "pass"
    assert diff["comparison_complete"] is True


# --- observatory / support-signals / boundary ----------------------------------------


def _strip_run_varying_hashes(obj: Any) -> Any:
    """Drop sha256 fields computed over artifacts that embed generated_at/paths.

    ``artifact_sha256`` / ``evidence_packet_registry_sha256`` /
    ``selection_artifact_sha256`` hash whole on-disk payloads containing the
    wall-clock stamp and absolute temp-repo paths, so they differ between ANY
    two runs (including two clean runs) — they carry no S1A-12 signal. Source
    CONTENT hashes (``source_sha256`` etc.) are kept: identical inputs must
    stay identical.
    """
    dropped = {"artifact_sha256", "evidence_packet_registry_sha256", "selection_artifact_sha256"}
    if isinstance(obj, dict):
        return {k: _strip_run_varying_hashes(v) for k, v in obj.items() if k not in dropped}
    if isinstance(obj, list):
        return [_strip_run_varying_hashes(v) for v in obj]
    return obj


def test_observatory_unchanged_between_clean_and_selection_fallback_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observatory never reads the selection artifact (its production call
    site passes None), so the S1A-12 switch cannot change its payload or its
    pre-existing warning semantics."""
    clean_root = tmp_path / "clean_run"
    _setup_repo(clean_root, monkeypatch)
    step1_research.parse_step1_output(strategy_settings=_settings())
    observatory_clean = _read(step1_research.step1_grounding_status_observatory_path())

    fallback_root = tmp_path / "fallback_run"
    _setup_repo(fallback_root, monkeypatch)
    monkeypatch.setattr(step1_research, "compare_embedded_selection_parity", _mismatch_parity)
    step1_research.parse_step1_output(strategy_settings=_settings())
    observatory_fallback = _read(step1_research.step1_grounding_status_observatory_path())

    assert _strip_run_varying_hashes(
        _norm_gen(_scrub(observatory_clean, str(clean_root)))
    ) == _strip_run_varying_hashes(_norm_gen(_scrub(observatory_fallback, str(fallback_root))))
    # The pre-existing by-design warnings are untouched by S1A-12.
    for observatory in (observatory_clean, observatory_fallback):
        assert "missing_or_malformed_embedded_registry_selection" in observatory["warnings"]

    # Code-level: the observatory module knows nothing about the S1A-12 symbols.
    import investment_orchestrator.research.grounding_status_observatory as observatory_module

    source = inspect.getsource(observatory_module)
    assert "compare_embedded_selection_parity" not in source
    assert "embedded_active_registry_selection.json" not in source
    assert "step1a" not in source.lower()


def test_compiled_support_signals_unchanged_under_selection_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """support_signals grounds off the evidence packet, never the selection
    artifact: forcing a selection-only fallback changes neither the packet nor
    the compiled support signals."""
    clean_root = tmp_path / "clean_run"
    _setup_repo(clean_root, monkeypatch)
    result_clean = step1_research.parse_step1_output(strategy_settings=_settings())
    assert result_clean["evidence_packet_writer_source"] == "step1a"
    support_clean = _read(step1_research.step1_compiled_support_signals_path())
    packet_clean = _read(step1_research.step1_evidence_packet_path())

    fallback_root = tmp_path / "fallback_run"
    _setup_repo(fallback_root, monkeypatch)
    monkeypatch.setattr(step1_research, "compare_embedded_selection_parity", _mismatch_parity)
    result_fallback = step1_research.parse_step1_output(strategy_settings=_settings())
    assert result_fallback["evidence_packet_writer_source"] == "step1a"
    assert (
        result_fallback["embedded_active_anchor_registry_selection_writer_source"]
        == "legacy_fallback"
    )
    support_fallback = _read(step1_research.step1_compiled_support_signals_path())
    packet_fallback = _read(step1_research.step1_evidence_packet_path())

    assert _norm_gen(_scrub(support_clean, str(clean_root))) == _norm_gen(
        _scrub(support_fallback, str(fallback_root))
    )
    assert packet_clean["universe"] == packet_fallback["universe"]
    assert _norm_gen(_scrub(packet_clean["active_anchor_registry"], str(clean_root))) == _norm_gen(
        _scrub(packet_fallback["active_anchor_registry"], str(fallback_root))
    )


def test_no_downstream_consumer_of_s1a12_symbols() -> None:
    """No runtime/order-path module consumes the S1A-12 comparator, the selection
    disk artifact, or the multi-write status machinery."""
    import investment_orchestrator.research.support_signals as support_signals
    import investment_orchestrator.state.final_execution_safety_gate as final_gate
    import investment_orchestrator.state.research_availability as availability
    import investment_orchestrator.workflow.step2_decision_builder as step2
    import investment_orchestrator.workflow.step3_audit_engine as step3
    import investment_orchestrator.workflow.step4_order_compiler as step4
    import investment_orchestrator.workflow.weekly_orchestrator as weekly

    for module in (support_signals, availability, step2, step3, step4, final_gate, weekly):
        source = inspect.getsource(module)
        assert "compare_embedded_selection_parity" not in source
        assert "embedded_active_registry_selection" not in source
        assert "embedded_active_anchor_registry_selection" not in source
        assert "_resolve_final_evidence_packet_write_statuses" not in source
        assert "evidence_packet_write_invocations" not in source
        assert "_evaluate_step1a_embedded_selection_guard" not in source
