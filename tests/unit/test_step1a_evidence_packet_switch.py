"""S1A-11 evidence_packet disk-writer switch tests.

The seventh artifact switch — and the FIRST grounding-input switch. support_signals
grounds off a fresh read-back of the on-disk ``evidence_packet.json`` (through the
handoff compiler), so the disk writer routes the payload to the Step 1A accessor
ONLY behind a strict runtime-parity guard. Conservative first-switch policy: the
Step 1A candidate reaches disk only when the runtime-relevant subtree is
byte-identical (only the approved ``generated_at`` paths normalized), no unknown
runtime timestamp leaked, no unexpected path was normalized, AND there are zero
report-only differences. On any divergence the legacy/current payload is written
and the fallback is recorded. Nothing here grants permissions, gates,
allowed_actions, or any order-path authority.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.research.evidence_packet import (
    build_evidence_packet_and_selection,
    compare_evidence_packet_runtime_parity,
)
from investment_orchestrator.workflow import step1_research
from investment_orchestrator.workflow.step1_research import (
    _APPROVED_EVIDENCE_PACKET_NORMALIZED_PATHS,
)
from investment_orchestrator.workflow.step1a_grounding_compile import (
    build_step1a_evidence_packet,
)

from test_step1a_shadow_run import _anchor, _approval, _read, _settings, _setup_repo, _write_json


_GEN_AT = "2026-06-28T12:00:00+00:00"
_OTHER_GEN_AT = "2026-06-28T18:30:00+00:00"


def _write_inputs(root: Path) -> tuple[Path, Path]:
    """Write the same operator YAML inputs ``_setup_repo`` uses, standalone."""
    root.mkdir(parents=True, exist_ok=True)
    anchors_path = root / "research_anchors.yaml"
    approvals_path = root / "research_anchor_approvals.yaml"
    _write_json(
        anchors_path,
        {
            "schema_version": "research_anchors_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "anchors": [_anchor("BASE_QQQ", "QQQ")],
        },
    )
    approved = _anchor()
    _write_json(
        approvals_path,
        {
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "approvals": [_approval(approved)],
            "revocations": [],
        },
    )
    return anchors_path, approvals_path


def _norm_gen(obj: Any) -> Any:
    """Recursively replace every ``generated_at`` value with a fixed sentinel.

    The only run-varying field in the evidence packet / compiled support signals
    is the wall-clock ``generated_at`` stamp; normalizing it lets us assert the
    rest is byte-stable across two independent runs.
    """
    if isinstance(obj, dict):
        return {k: ("<gen>" if k == "generated_at" else _norm_gen(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm_gen(v) for v in obj]
    return obj


def _scrub(obj: Any, needle: str) -> Any:
    """Recursively replace a known repo-root path prefix with a fixed sentinel.

    Two independent temp repos embed different absolute input paths in
    ``source_manifest[].path`` (the content sha256 is identical); scrubbing the
    known root lets us assert the registry CONTENT is equivalent across lineages.
    """
    if isinstance(obj, dict):
        return {k: _scrub(v, needle) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v, needle) for v in obj]
    if isinstance(obj, str):
        return obj.replace(needle, "<root>")
    return obj


# --- accessor unit tests -----------------------------------------------------


def test_accessor_runtime_parity_matches_legacy(tmp_path: Path) -> None:
    """``build_step1a_evidence_packet`` is runtime-equivalent to the legacy build.

    Same inputs + same generated_at -> the comparator sees a byte-identical
    runtime subtree, zero report-only differences, and only the approved
    generated_at paths normalized. This is exactly the guard predicate the
    switched disk writer enforces per run.
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
    legacy = build_evidence_packet_and_selection(**common)
    step1a = build_step1a_evidence_packet(**common)

    parity = compare_evidence_packet_runtime_parity(legacy, step1a)
    assert parity["subtree_match"] is True
    assert parity["differences"] == []
    assert parity["report_only_differences"] == []
    assert parity["unknown_runtime_timestamp_fields"] == []
    assert set(parity["normalized_paths"]) <= set(_APPROVED_EVIDENCE_PACKET_NORMALIZED_PATHS)
    # The runtime-relevant fields support_signals consumes are byte-identical.
    assert step1a["universe"] == legacy["universe"]
    assert _norm_gen(step1a["active_anchor_registry"]) == _norm_gen(legacy["active_anchor_registry"])
    # Conservative first-switch guarantee: with subtree_match AND zero report-only
    # differences (and a shared generated_at), the whole packet is byte-identical
    # to the legacy build — the switch is a pure provenance change on disk.
    assert _norm_gen(step1a) == _norm_gen(legacy)


def test_accessor_generated_at_only_difference_passes(tmp_path: Path) -> None:
    """A differing generated_at alone never breaks parity (it is normalized)."""
    anchors_path, approvals_path = _write_inputs(tmp_path)
    settings = _settings()
    base: dict[str, Any] = dict(
        strategy_settings=settings,
        portfolio_snapshot_text=None,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=approvals_path,
        now_date=settings["as_of"],
    )
    legacy = build_evidence_packet_and_selection(generated_at=_GEN_AT, **base)
    step1a = build_step1a_evidence_packet(generated_at=_OTHER_GEN_AT, **base)

    parity = compare_evidence_packet_runtime_parity(legacy, step1a)
    assert parity["subtree_match"] is True
    assert parity["report_only_differences"] == []
    assert set(parity["normalized_paths"]) == set(_APPROVED_EVIDENCE_PACKET_NORMALIZED_PATHS)


def test_accessor_is_pure_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The accessor reads its two operator YAMLs and writes NO file (pure)."""
    anchors_path, approvals_path = _write_inputs(tmp_path)

    # Any attempt to persist JSON through the shared IO helper is a bug.
    import investment_orchestrator.common.io as io_mod

    def _no_write(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("build_step1a_evidence_packet attempted a file write")

    monkeypatch.setattr(io_mod, "write_json", _no_write)

    before = sorted(p.name for p in tmp_path.rglob("*"))
    result = build_step1a_evidence_packet(
        strategy_settings=_settings(),
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=approvals_path,
        generated_at=_GEN_AT,
        now_date=_settings()["as_of"],
    )
    after = sorted(p.name for p in tmp_path.rglob("*"))

    assert before == after  # no new files created anywhere under tmp_path
    assert isinstance(result, dict)
    assert result["schema_version"]  # it really produced a packet
    assert "active_anchor_registry" in result


# --- guard-pass integration --------------------------------------------------


def test_guard_pass_writes_step1a_and_records_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clean run: the strict guard passes and the Step 1A candidate reaches disk."""
    _setup_repo(tmp_path, monkeypatch)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["evidence_packet_writer_source"] == "step1a"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["evidence_packet"]
    assert entry["writer_source"] == "step1a"
    assert entry["fallback_used"] is False
    assert entry["error_summary"] == ""
    assert entry["output_path"] == str(step1_research.step1_evidence_packet_path())

    # Exactly seven switched artifacts — evidence_packet is the seventh, no eighth.
    assert len(status["switched_artifacts"]) == 7
    assert "evidence_packet" in status["switched_artifacts"]

    # The writer flips evidence_packet_uses_step1a_output True; runtime authority
    # markers stay False (guard proves the runtime subtree is byte-identical).
    assert status["evidence_packet_uses_step1a_output"] is True
    assert status["embedded_selection_uses_step1a_output"] is False
    assert status["support_signals_uses_step1a_output"] is False
    assert status["readiness_uses_step1a_output"] is False
    assert status["order_path_uses_step1a_output"] is False
    assert status["runtime_authority_uses_step1a_output"] is False
    assert status["production_artifact_paths_switched"] is False

    # Report-only / no-authority envelope is intact.
    assert status["report_only"] is True
    assert status["permission_effect"] == "none"
    assert status["consumed_by_gates"] is False
    assert status["consumed_by_order_path"] is False
    assert status["consumed_by_downstream"] is False
    assert status["safe_to_ignore"] is True

    # Diagnostic only — never opens an order path.
    decision = _read(Path(result["research_degraded_mode_decision_path"]))
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]


# --- fallback branches -------------------------------------------------------


def test_accessor_failure_falls_back_to_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising Step 1A accessor -> legacy/current payload, fallback recorded."""
    _setup_repo(tmp_path, monkeypatch)

    def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("accessor exploded")

    monkeypatch.setattr(step1_research, "build_step1a_evidence_packet", _boom)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert Path(result["research_output_path"]).is_file()
    assert result["evidence_packet_writer_source"] == "legacy_fallback"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["evidence_packet"]
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_accessor_failed" in entry["error_summary"]

    # The legacy/current packet was written and is a valid, complete packet.
    packet = _read(step1_research.step1_evidence_packet_path())
    assert packet["schema_version"]
    assert "active_anchor_registry" in packet


def test_runtime_subtree_mismatch_falls_back_to_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mutated active_anchor_registry (same count) -> parity_mismatch fallback."""
    _setup_repo(tmp_path, monkeypatch)
    real_accessor = step1_research.build_step1a_evidence_packet

    def _mutated(**kwargs: Any) -> dict[str, Any]:
        packet = real_accessor(**kwargs)
        registry = packet.get("active_anchor_registry")
        if isinstance(registry, dict):
            anchors = registry.get("active_anchors")
            if isinstance(anchors, list) and anchors and isinstance(anchors[0], dict):
                # Same anchor count, different content -> exactly the class the
                # old count-only summary false-passed.
                anchors[0] = {**anchors[0], "content_sha256": "f" * 64}
            else:
                registry["registry_valid"] = not registry.get("registry_valid", True)
        return packet

    monkeypatch.setattr(step1_research, "build_step1a_evidence_packet", _mutated)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["evidence_packet_writer_source"] == "legacy_fallback"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["evidence_packet"]
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_evidence_packet_parity_mismatch" in entry["error_summary"]

    # The legacy payload (WITHOUT the mutation) reached disk.
    packet = _read(step1_research.step1_evidence_packet_path())
    registry = packet.get("active_anchor_registry") or {}
    anchors = registry.get("active_anchors") or []
    assert all(a.get("content_sha256") != "f" * 64 for a in anchors if isinstance(a, dict))


def test_unknown_runtime_timestamp_falls_back_to_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-generated_at ISO datetime in the runtime subtree fails closed."""
    _setup_repo(tmp_path, monkeypatch)
    real_accessor = step1_research.build_step1a_evidence_packet

    def _inject_timestamp(**kwargs: Any) -> dict[str, Any]:
        packet = real_accessor(**kwargs)
        registry = packet.get("active_anchor_registry")
        if isinstance(registry, dict):
            registry["injected_runtime_timestamp"] = "2026-01-02T03:04:05+00:00"
        return packet

    monkeypatch.setattr(step1_research, "build_step1a_evidence_packet", _inject_timestamp)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["evidence_packet_writer_source"] == "legacy_fallback"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["evidence_packet"]
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_evidence_packet_unknown_runtime_timestamp" in entry["error_summary"]

    packet = _read(step1_research.step1_evidence_packet_path())
    assert "injected_runtime_timestamp" not in (packet.get("active_anchor_registry") or {})


def test_unexpected_normalized_path_falls_back_to_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normalized path outside the approved generated_at set fails closed.

    In a real run the normalizer only ever touches the two approved generated_at
    paths, so the comparator result is monkeypatched to include an extra
    normalized path while otherwise reporting a clean match.
    """
    _setup_repo(tmp_path, monkeypatch)

    def _fake_parity(_prod: Any, _step1a: Any) -> dict[str, Any]:
        return {
            "subtree_match": True,
            "differences": [],
            "report_only_differences": [],
            "unknown_runtime_timestamp_fields": [],
            "normalized_paths": [
                "generated_at",
                "active_anchor_registry.generated_at",
                "research_anchors.generated_at",  # NOT approved
            ],
        }

    monkeypatch.setattr(step1_research, "compare_evidence_packet_runtime_parity", _fake_parity)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["evidence_packet_writer_source"] == "legacy_fallback"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["evidence_packet"]
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_evidence_packet_unexpected_normalized_path" in entry["error_summary"]

    # A valid packet still reached disk (the legacy/current payload).
    packet = _read(step1_research.step1_evidence_packet_path())
    assert packet["schema_version"]


def test_report_only_difference_falls_back_to_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report-only-only divergence still falls back (conservative policy)."""
    _setup_repo(tmp_path, monkeypatch)
    real_accessor = step1_research.build_step1a_evidence_packet

    def _diff_report_only(**kwargs: Any) -> dict[str, Any]:
        packet = real_accessor(**kwargs)
        source_artifacts = packet.get("source_artifacts")
        base = dict(source_artifacts) if isinstance(source_artifacts, dict) else {}
        base["injected_report_only_field"] = "diagnostic-only"
        packet["source_artifacts"] = base
        return packet

    monkeypatch.setattr(step1_research, "build_step1a_evidence_packet", _diff_report_only)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["evidence_packet_writer_source"] == "legacy_fallback"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["evidence_packet"]
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_evidence_packet_report_only_difference" in entry["error_summary"]

    # The legacy/current payload reached disk WITHOUT the injected report-only key.
    packet = _read(step1_research.step1_evidence_packet_path())
    assert "injected_report_only_field" not in (packet.get("source_artifacts") or {})


def test_accessor_and_write_failure_records_unwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Double failure: accessor raises AND the evidence_packet write fails.

    The writer records ``unwritten`` provenance, parse still completes (pre-switch
    swallowed behavior preserved), and there is no false pass.
    """
    _setup_repo(tmp_path, monkeypatch)

    def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("accessor exploded")

    monkeypatch.setattr(step1_research, "build_step1a_evidence_packet", _boom)

    # Fail ONLY the evidence_packet write; every other writer keeps working so the
    # rest of the parse behaves normally.
    evidence_packet_path = step1_research.step1_evidence_packet_path()
    real_write_json = step1_research.write_json

    def _selective_write(path: Any, payload: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(path) == evidence_packet_path:
            raise RuntimeError("evidence packet write exploded")
        return real_write_json(path, payload, *args, **kwargs)

    monkeypatch.setattr(step1_research, "write_json", _selective_write)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Parse continues; the evidence packet is simply absent (tolerant readers degrade).
    assert Path(result["research_output_path"]).is_file()
    assert not evidence_packet_path.is_file()
    assert result["evidence_packet_writer_source"] == "unwritten"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["evidence_packet"]
    assert entry["writer_source"] == "unwritten"
    assert "step1a_accessor_failed" in entry["error_summary"]
    assert "evidence_packet_write_failed" in entry["error_summary"]

    # No false pass: the degraded decision opens no order path.
    decision = _read(Path(result["research_degraded_mode_decision_path"]))
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]


# --- support_signals boundary ------------------------------------------------


def test_compiled_support_signals_unchanged_legacy_vs_step1a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """compiled_support_signals.json is identical under forced-legacy vs guard-pass.

    support_signals grounds off the read-back of the on-disk evidence_packet, so a
    Step 1A guard-pass run and a forced-legacy run under identical inputs must
    produce the same compiled support signals (modulo the run-varying generated_at
    stamp), and the packet fields support_signals consumes — ``universe`` and
    ``active_anchor_registry`` — must be equivalent.
    """
    real_accessor = step1_research.build_step1a_evidence_packet

    # Run A: force the legacy/current payload onto disk.
    legacy_root = tmp_path / "legacy_run"
    _setup_repo(legacy_root, monkeypatch)

    def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("force legacy fallback")

    monkeypatch.setattr(step1_research, "build_step1a_evidence_packet", _boom)
    result_a = step1_research.parse_step1_output(strategy_settings=_settings())
    assert result_a["evidence_packet_writer_source"] == "legacy_fallback"
    support_a = _read(step1_research.step1_compiled_support_signals_path())
    packet_a = _read(step1_research.step1_evidence_packet_path())

    # Run B: clean guard-pass run in a fresh repo with identical inputs.
    monkeypatch.setattr(step1_research, "build_step1a_evidence_packet", real_accessor)
    step1a_root = tmp_path / "step1a_run"
    _setup_repo(step1a_root, monkeypatch)
    result_b = step1_research.parse_step1_output(strategy_settings=_settings())
    assert result_b["evidence_packet_writer_source"] == "step1a"
    support_b = _read(step1_research.step1_compiled_support_signals_path())
    packet_b = _read(step1_research.step1_evidence_packet_path())

    # The compiled support signals are byte-stable across the two lineages.
    assert _norm_gen(support_a) == _norm_gen(support_b)

    # The exact packet fields support_signals consumes are equivalent. The only
    # cross-run variation is generated_at and the embedded absolute input path
    # (which carries the differing temp-repo root but an identical content sha256).
    assert packet_a["universe"] == packet_b["universe"]
    assert _norm_gen(_scrub(packet_a["active_anchor_registry"], str(legacy_root))) == _norm_gen(
        _scrub(packet_b["active_anchor_registry"], str(step1a_root))
    )


# --- boundary / no new consumer ----------------------------------------------


def test_no_downstream_consumer_of_step1a_evidence_packet_symbols() -> None:
    """No runtime/order-path module consumes the S1A-11 accessor or comparator.

    The evidence_packet accessor is consumed ONLY by the switched Step 1 writer;
    the comparator is defined in the evidence_packet module and used only by that
    writer's guard. Neither may leak into any downstream/order-path module.
    """
    import investment_orchestrator.research.support_signals as support_signals
    import investment_orchestrator.state.final_execution_safety_gate as final_gate
    import investment_orchestrator.state.research_availability as availability
    import investment_orchestrator.workflow.step2_decision_builder as step2
    import investment_orchestrator.workflow.step3_audit_engine as step3
    import investment_orchestrator.workflow.step4_order_compiler as step4
    import investment_orchestrator.workflow.weekly_orchestrator as weekly

    for module in (support_signals, availability, step2, step3, step4, final_gate, weekly):
        source = inspect.getsource(module)
        assert "build_step1a_evidence_packet" not in source
        assert "build_evidence_packet_and_selection" not in source
        assert "compare_evidence_packet_runtime_parity" not in source
        assert "step1a_artifact_switch_status" not in source
        assert "step1a_grounding_compile_shadow_diff" not in source
