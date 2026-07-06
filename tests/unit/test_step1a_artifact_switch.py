"""S1A-3 first artifact switch tests: active_research_anchor_registry.json.

The switched writer sources the payload from the narrow Step 1A accessor
(byte-identical to the legacy compile by construction), keeps the legacy writer
as runtime fallback, and records report-only provenance in
``step1a_artifact_switch_status.json``. Nothing here grants permissions, gates,
allowed_actions, or any order-path authority.
"""

from __future__ import annotations

import datetime
import inspect
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.research.active_research_anchor_registry import (
    write_active_research_anchor_registry,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    compute_operator_completed_anchor_sha256 as sha,
    write_research_anchor_approvals_validation,
)
from investment_orchestrator.workflow import step1_research
from investment_orchestrator.workflow.step1a_grounding_compile import (
    build_step1a_active_research_anchor_registry,
    build_step1a_grounding_compile_bundle,
    build_step1a_research_anchor_approvals_validation,
)

from test_step1a_shadow_run import _anchor, _approval, _read, _settings, _setup_repo


def _base_anchor(**overrides: Any) -> dict[str, Any]:
    anchor = _anchor("MATRIX_QQQ", "QQQ")
    anchor.update(overrides)
    return anchor


def _anchors_doc(anchors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "research_anchors_v1",
        "is_llm_generated": False,
        "as_of_date": "2026-06-28",
        "anchors": anchors,
    }


_MATRIX_SETTINGS: dict[str, Any] = {
    "as_of": "2026-06-28",
    "core_universe": ["QQQ", "VOO"],
    "satellite_universe": ["SMH"],
}

# scenario -> (anchors document | "missing" | raw text, strategy_settings)
_BYTE_IDENTITY_MATRIX: dict[str, tuple[Any, Any]] = {
    "valid_anchors": (_anchors_doc([_base_anchor()]), _MATRIX_SETTINGS),
    "empty_anchor_list": (_anchors_doc([]), _MATRIX_SETTINGS),
    "missing_file": ("missing", _MATRIX_SETTINGS),
    "malformed_yaml": ("::: not yaml {{{", _MATRIX_SETTINGS),
    "expired_anchor": (
        _anchors_doc([_base_anchor(valid_until="2026-02-28")]),
        _MATRIX_SETTINGS,
    ),
    "invalid_anchor_missing_fields": (
        _anchors_doc([{"anchor_id": "BROKEN_ONLY_ID"}]),
        _MATRIX_SETTINGS,
    ),
    "duplicate_anchor_ids": (
        _anchors_doc([_base_anchor(), _base_anchor(summary="duplicate copy")]),
        _MATRIX_SETTINGS,
    ),
    "out_of_universe_ticker": (
        _anchors_doc([_base_anchor(applicable_tickers=["ZZZT"])]),
        _MATRIX_SETTINGS,
    ),
    "settings_none": (_anchors_doc([_base_anchor()]), None),
    "settings_missing_universes": (_anchors_doc([_base_anchor()]), {"as_of": "2026-06-28"}),
}


@pytest.mark.parametrize("scenario", sorted(_BYTE_IDENTITY_MATRIX))
def test_accessor_output_byte_identical_to_legacy_writer(scenario: str, tmp_path: Path) -> None:
    source, settings = _BYTE_IDENTITY_MATRIX[scenario]
    anchors_path = tmp_path / "research_anchors.yaml"
    if source == "missing":
        pass
    elif isinstance(source, str):
        anchors_path.write_text(source, encoding="utf-8")
    else:
        anchors_path.write_text(json.dumps(source), encoding="utf-8")

    legacy_path = tmp_path / "legacy_active_research_anchor_registry.json"
    write_active_research_anchor_registry(
        output_path=legacy_path,
        anchors_path=anchors_path,
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(settings),
        today=settings.get("as_of") if isinstance(settings, dict) else None,
    )
    legacy_payload = json.loads(legacy_path.read_text(encoding="utf-8"))

    step1a_payload = build_step1a_active_research_anchor_registry(
        strategy_settings=settings,
        research_anchors_path=anchors_path,
    )

    assert json.loads(json.dumps(step1a_payload)) == legacy_payload


def test_parse_writes_registry_from_step1a_source_and_switch_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    registry = _read(step1_research.step1_active_research_anchor_registry_path())
    legacy_path = tmp_path / "independent_legacy_compile.json"
    write_active_research_anchor_registry(
        output_path=legacy_path,
        anchors_path=tmp_path / "inputs" / "current" / "research_anchors.yaml",
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(_settings()),
        today=_settings()["as_of"],
    )
    assert registry == json.loads(legacy_path.read_text(encoding="utf-8"))

    assert result["active_research_anchor_registry_writer_source"] == "step1a"
    assert result["step1a_artifact_switch_status_path"] == str(
        step1_research.step1a_artifact_switch_status_path()
    )

    status = _read(step1_research.step1a_artifact_switch_status_path())
    assert status["schema_version"] == "step1a_artifact_switch_status_v1"
    assert status["is_llm_generated"] is False
    assert status["report_only"] is True
    assert status["permission_effect"] == "none"
    assert status["not_authorization"] is True
    assert status["not_execution_authorization"] is True
    assert status["consumed_by_gates"] is False
    assert status["consumed_by_order_path"] is False
    assert status["consumed_by_downstream"] is False
    assert status["cannot_affect_allowed_actions"] is True
    assert status["cannot_affect_registry_selection"] is True
    assert status["not_registry_selection_input"] is True
    assert status["not_order_input"] is True
    assert status["safe_to_ignore"] is True
    assert "integrity" in status["shadow_comparison_note"]

    entry = status["switched_artifacts"]["active_research_anchor_registry"]
    assert entry["writer_source"] == "step1a"
    assert entry["fallback_used"] is False
    assert entry["error_summary"] == ""
    assert entry["output_path"] == str(step1_research.step1_active_research_anchor_registry_path())

    # Exactly the two S1A-3/S1A-4 switched artifacts — no third switch.
    assert sorted(status["switched_artifacts"]) == [
        "active_research_anchor_registry",
        "research_anchor_approvals_validation",
    ]


def test_step1a_accessor_failure_falls_back_to_legacy_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_accessor(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("accessor exploded")

    monkeypatch.setattr(
        step1_research, "build_step1a_active_research_anchor_registry", broken_accessor
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Legacy fallback wrote the identical payload; parse completed.
    assert Path(result["research_output_path"]).is_file()
    registry = _read(step1_research.step1_active_research_anchor_registry_path())
    legacy_path = tmp_path / "independent_legacy_compile.json"
    write_active_research_anchor_registry(
        output_path=legacy_path,
        anchors_path=tmp_path / "inputs" / "current" / "research_anchors.yaml",
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(_settings()),
        today=_settings()["as_of"],
    )
    assert registry == json.loads(legacy_path.read_text(encoding="utf-8"))

    assert result["active_research_anchor_registry_writer_source"] == "legacy_fallback"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["active_research_anchor_registry"]
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_accessor_failed" in entry["error_summary"]

    # Byte-identical fallback content keeps the shadow comparison green.
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "pass"
    assert diff["comparisons"]["active_research_anchor_registry"]["semantic_match"] is True


def test_double_failure_preserves_swallowed_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_accessor(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("accessor exploded")

    def broken_legacy(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("legacy writer exploded")

    monkeypatch.setattr(
        step1_research, "build_step1a_active_research_anchor_registry", broken_accessor
    )
    monkeypatch.setattr(step1_research, "write_active_research_anchor_registry", broken_legacy)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Pre-switch behavior preserved: artifact absent, parse continues.
    assert Path(result["research_output_path"]).is_file()
    assert not step1_research.step1_active_research_anchor_registry_path().is_file()
    assert result["active_research_anchor_registry_writer_source"] == "unwritten"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["active_research_anchor_registry"]
    assert entry["writer_source"] == "unwritten"
    assert entry["fallback_used"] is True
    assert "step1a_accessor_failed" in entry["error_summary"]
    assert "legacy_writer_failed" in entry["error_summary"]

    # The shadow diff flags the absent artifact explicitly (skip) and, because the
    # on-disk observatory degrades while the bundle's does not, a diagnostic-only
    # mismatch. Never a false complete pass.
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    registry_cmp = diff["comparisons"]["active_research_anchor_registry"]
    assert registry_cmp["comparison_skipped"] is True
    assert registry_cmp["skip_reason"] == "current_step1_artifact_unavailable_or_malformed"
    assert diff["comparison_status"] in ("mismatch", "pass_with_skips")
    assert diff["comparison_complete"] is False
    assert diff["parity_passed"] is False
    assert diff["production_artifacts_unchanged"] is True

    # No gate or order path opens.
    decision = _read(Path(result["research_degraded_mode_decision_path"]))
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]


def _approvals_doc(approvals: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": "2026-06-28",
        "approvals": approvals,
    }
    doc.update(overrides)
    return doc


def _mismatched_sha_approval() -> dict[str, Any]:
    approval = _approval(_base_anchor())
    approval["operator_completed_anchor_sha256"] = "0" * 64
    return approval


def _unknown_fields_approval() -> dict[str, Any]:
    approval = _approval(_base_anchor())
    approval["unsupported_target"] = "portfolio_wide"
    approval["mystery_field"] = {"nested": True}
    return approval


# scenario -> (approvals document | "missing" | raw text, strategy_settings)
_APPROVALS_BYTE_IDENTITY_MATRIX: dict[str, tuple[Any, Any]] = {
    "valid_would_activate": (_approvals_doc([_approval(_base_anchor())]), _MATRIX_SETTINGS),
    "empty_approvals": (_approvals_doc([]), _MATRIX_SETTINGS),
    "missing_manifest": ("missing", _MATRIX_SETTINGS),
    "malformed_yaml": ("::: not yaml {{{", _MATRIX_SETTINGS),
    "expired_anchor_approval": (
        _approvals_doc([_approval(_base_anchor(valid_until="2026-02-28"))]),
        _MATRIX_SETTINGS,
    ),
    "sha256_mismatch": (_approvals_doc([_mismatched_sha_approval()]), _MATRIX_SETTINGS),
    "duplicate_approval_ids": (
        _approvals_doc([_approval(_base_anchor()), _approval(_base_anchor(summary="dup"))]),
        _MATRIX_SETTINGS,
    ),
    "unknown_fields_extra_keys": (
        _approvals_doc([_unknown_fields_approval()], mystery_top_level="x"),
        _MATRIX_SETTINGS,
    ),
    "unsupported_decision": (
        _approvals_doc([dict(_approval(_base_anchor()), decision="maybe")]),
        _MATRIX_SETTINGS,
    ),
    "out_of_universe_ticker": (
        _approvals_doc([_approval(_base_anchor(applicable_tickers=["ZZZT"]))]),
        _MATRIX_SETTINGS,
    ),
    "llm_generated_true_fails_closed": (
        _approvals_doc([_approval(_base_anchor())], is_llm_generated=True),
        _MATRIX_SETTINGS,
    ),
    "revocations_section_present_ignored": (
        _approvals_doc(
            [_approval(_base_anchor())],
            revocations=[{"revocation_id": "REV-1", "target_type": "approval_anchor"}],
        ),
        _MATRIX_SETTINGS,
    ),
    "settings_none": (_approvals_doc([_approval(_base_anchor())]), None),
    "settings_missing_universes": (_approvals_doc([_approval(_base_anchor())]), {"as_of": "2026-06-28"}),
    "missing_as_of": (
        _approvals_doc([_approval(_base_anchor())]),
        {"core_universe": ["QQQ", "VOO"], "satellite_universe": ["SMH"]},
    ),
}


@pytest.mark.parametrize("scenario", sorted(_APPROVALS_BYTE_IDENTITY_MATRIX))
def test_approvals_accessor_output_byte_identical_to_legacy_writer(
    scenario: str, tmp_path: Path
) -> None:
    source, settings = _APPROVALS_BYTE_IDENTITY_MATRIX[scenario]
    manifest_path = tmp_path / "research_anchor_approvals.yaml"
    if source == "missing":
        pass
    elif isinstance(source, str):
        manifest_path.write_text(source, encoding="utf-8")
    else:
        manifest_path.write_text(json.dumps(source), encoding="utf-8")

    settings_as_of = settings.get("as_of") if isinstance(settings, dict) else None
    legacy_path = tmp_path / "legacy_research_anchor_approvals_validation.json"
    write_research_anchor_approvals_validation(
        output_path=legacy_path,
        manifest_path=manifest_path,
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(settings),
        today=settings_as_of,
        as_of_date=settings_as_of if isinstance(settings_as_of, str) else None,
    )
    legacy_payload = json.loads(legacy_path.read_text(encoding="utf-8"))

    step1a_payload = build_step1a_research_anchor_approvals_validation(
        strategy_settings=settings,
        research_anchor_approvals_path=manifest_path,
    )

    assert json.loads(json.dumps(step1a_payload)) == legacy_payload


def test_non_string_as_of_normalizes_to_none_and_matches_bundle(tmp_path: Path) -> None:
    """Pin the documented S1A-4 normalization edge.

    Legacy passes a raw non-string ``as_of`` (e.g. an unquoted YAML date) through
    as ``today`` while Step 1A's ``_first_str`` normalizes it to None — the
    established S1A-3 convention — so byte identity vs legacy is deliberately NOT
    asserted for this input class. The accessor must instead behave exactly as if
    ``as_of`` were absent and must equal the Step 1A bundle's report variant.
    """
    universes = {"core_universe": ["QQQ", "VOO"], "satellite_universe": ["SMH"]}
    date_settings = {"as_of": datetime.date(2026, 6, 28), **universes}
    no_asof_settings = dict(universes)
    anchors_path = tmp_path / "research_anchors.yaml"
    anchors_path.write_text(json.dumps(_anchors_doc([_base_anchor()])), encoding="utf-8")
    manifest_path = tmp_path / "research_anchor_approvals.yaml"
    manifest_path.write_text(
        json.dumps(_approvals_doc([_approval(_base_anchor())])), encoding="utf-8"
    )

    accessor_with_date = build_step1a_research_anchor_approvals_validation(
        strategy_settings=date_settings,
        research_anchor_approvals_path=manifest_path,
    )
    accessor_without_asof = build_step1a_research_anchor_approvals_validation(
        strategy_settings=no_asof_settings,
        research_anchor_approvals_path=manifest_path,
    )
    assert accessor_with_date == accessor_without_asof

    bundle = build_step1a_grounding_compile_bundle(
        strategy_settings=date_settings,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=manifest_path,
    )
    assert accessor_with_date == bundle["artifacts"]["research_anchor_approvals_validation"]


def test_bundle_report_variant_uses_accessor_and_overlay_untouched() -> None:
    import investment_orchestrator.workflow.step1a_grounding_compile as step1a

    src = inspect.getsource(step1a._build)
    assert "build_step1a_research_anchor_approvals_validation(" in src
    # Exactly one raw approvals-validator call remains in _build: the OVERLAY
    # variant feeding the with-approvals registry, deliberately NOT switched.
    assert src.count("validate_research_anchor_approvals(") == 1
    assert "overlay_approvals_validation = validate_research_anchor_approvals(" in src
    # Revocations validation (report + overlay variants) is untouched by S1A-4.
    assert src.count("validate_research_anchor_revocations(") == 2


def test_parse_writes_approvals_validation_from_step1a_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    artifact_path = step1_research.step1_research_anchor_approvals_validation_path()
    legacy_path = tmp_path / "independent_legacy_approvals_validation.json"
    write_research_anchor_approvals_validation(
        output_path=legacy_path,
        manifest_path=tmp_path / "inputs" / "current" / "research_anchor_approvals.yaml",
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(_settings()),
        today=_settings()["as_of"],
        as_of_date=_settings()["as_of"],
    )
    assert artifact_path.read_bytes() == legacy_path.read_bytes()

    assert result["research_anchor_approvals_validation_writer_source"] == "step1a"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["research_anchor_approvals_validation"]
    assert entry["writer_source"] == "step1a"
    assert entry["fallback_used"] is False
    assert entry["error_summary"] == ""
    assert entry["output_path"] == str(artifact_path)


def test_approvals_accessor_failure_falls_back_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_accessor(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("approvals accessor exploded")

    monkeypatch.setattr(
        step1_research, "build_step1a_research_anchor_approvals_validation", broken_accessor
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Per-artifact independence: the registry switch is unaffected.
    assert result["active_research_anchor_registry_writer_source"] == "step1a"
    assert result["research_anchor_approvals_validation_writer_source"] == "legacy_fallback"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    assert status["switched_artifacts"]["active_research_anchor_registry"]["writer_source"] == "step1a"
    entry = status["switched_artifacts"]["research_anchor_approvals_validation"]
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_accessor_failed" in entry["error_summary"]

    # The legacy fallback wrote the byte-identical payload; shadow stays green.
    legacy_path = tmp_path / "independent_legacy_approvals_validation.json"
    write_research_anchor_approvals_validation(
        output_path=legacy_path,
        manifest_path=tmp_path / "inputs" / "current" / "research_anchor_approvals.yaml",
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(_settings()),
        today=_settings()["as_of"],
        as_of_date=_settings()["as_of"],
    )
    artifact_path = step1_research.step1_research_anchor_approvals_validation_path()
    assert artifact_path.read_bytes() == legacy_path.read_bytes()
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "pass"
    assert diff["comparisons"]["research_anchor_approvals_validation"]["semantic_match"] is True


def test_approvals_double_failure_preserves_swallowed_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_accessor(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("approvals accessor exploded")

    def broken_legacy(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("legacy approvals writer exploded")

    monkeypatch.setattr(
        step1_research, "build_step1a_research_anchor_approvals_validation", broken_accessor
    )
    monkeypatch.setattr(
        step1_research, "write_research_anchor_approvals_validation", broken_legacy
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Pre-switch behavior preserved: artifact absent, parse continues.
    assert Path(result["research_output_path"]).is_file()
    assert not step1_research.step1_research_anchor_approvals_validation_path().is_file()
    assert result["research_anchor_approvals_validation_writer_source"] == "unwritten"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    assert status["switched_artifacts"]["active_research_anchor_registry"]["writer_source"] == "step1a"
    entry = status["switched_artifacts"]["research_anchor_approvals_validation"]
    assert entry["writer_source"] == "unwritten"
    assert entry["fallback_used"] is True
    assert "step1a_accessor_failed" in entry["error_summary"]
    assert "legacy_writer_failed" in entry["error_summary"]

    # Explicit skip in the shadow diff — never a false complete pass.
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    approvals_cmp = diff["comparisons"]["research_anchor_approvals_validation"]
    assert approvals_cmp["comparison_skipped"] is True
    assert approvals_cmp["skip_reason"] == "current_step1_artifact_unavailable_or_malformed"
    assert diff["comparison_status"] in ("mismatch", "pass_with_skips")
    assert diff["comparison_complete"] is False
    assert diff["parity_passed"] is False
    assert diff["production_artifacts_unchanged"] is True

    decision = _read(Path(result["research_degraded_mode_decision_path"]))
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]


# evidence_packet_registry_sha256 hashes the packet's embedded registry INCLUDING
# its wall-clock generated_at, so it differs between any two runs regardless of
# which writer produced the baseline registry artifact.
_RUN_VARYING_KEYS = ("generated_at", "evidence_packet_registry_sha256")


def _strip_generated_at(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_generated_at(v) for k, v in value.items() if k not in _RUN_VARYING_KEYS
        }
    if isinstance(value, list):
        return [_strip_generated_at(item) for item in value]
    return value


def test_reader_invariance_between_step1a_and_legacy_fallback_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readers and sibling artifacts of BOTH switched writers are invariant.

    Both variants run in the SAME repo root (wiped between runs) so embedded
    absolute paths and their hashes are comparable exactly; only timestamps are
    stripped. Forcing both accessors onto the legacy fallback must change
    nothing: the switched artifacts themselves, their readers (equivalence,
    candidates, observatory), and the untouched sibling artifacts (revocations
    validation, with-approvals registry, dual-read diff) stay identical.
    """
    repo = tmp_path / "repo"

    def run(force_legacy: bool) -> dict[str, Any]:
        if repo.exists():
            shutil.rmtree(repo)
        with pytest.MonkeyPatch.context() as mp:
            _setup_repo(repo, mp)
            if force_legacy:
                def broken_accessor(**_kwargs: Any) -> dict[str, Any]:
                    raise RuntimeError("force legacy")

                mp.setattr(
                    step1_research,
                    "build_step1a_active_research_anchor_registry",
                    broken_accessor,
                )
                mp.setattr(
                    step1_research,
                    "build_step1a_research_anchor_approvals_validation",
                    broken_accessor,
                )
            step1_research.parse_step1_output(strategy_settings=_settings())
            return {
                "registry": _read(step1_research.step1_active_research_anchor_registry_path()),
                "approvals_validation": _read(
                    step1_research.step1_research_anchor_approvals_validation_path()
                ),
                "revocations_validation": _read(
                    step1_research.step1_research_anchor_revocations_validation_path()
                ),
                "with_approvals": _read(
                    step1_research.step1_active_research_anchor_registry_with_approvals_path()
                ),
                "dual_read_diff": _read(step1_research.step1_approval_registry_dual_read_diff_path()),
                "equivalence": _read(step1_research.step1_anchor_source_equivalence_path()),
                "candidates": _read(step1_research.step1_research_anchor_candidates_path()),
                "observatory": _read(step1_research.step1_grounding_status_observatory_path()),
            }

    switched = run(force_legacy=False)
    legacy = run(force_legacy=True)

    for key in (
        "registry",
        "approvals_validation",
        "revocations_validation",
        "with_approvals",
        "dual_read_diff",
        "equivalence",
        "candidates",
        "observatory",
    ):
        assert _strip_generated_at(switched[key]) == _strip_generated_at(legacy[key]), key


def test_switch_boundaries_no_new_consumer_and_embedded_selection_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import investment_orchestrator.research.evidence_packet as evidence_packet
    import investment_orchestrator.research.support_signals as support_signals
    import investment_orchestrator.state.final_execution_safety_gate as final_gate
    import investment_orchestrator.state.research_availability as availability
    import investment_orchestrator.workflow.step2_decision_builder as step2
    import investment_orchestrator.workflow.step3_audit_engine as step3
    import investment_orchestrator.workflow.step4_order_compiler as step4
    import investment_orchestrator.workflow.weekly_orchestrator as weekly

    for module in (evidence_packet, support_signals, availability, step2, step3, step4, final_gate, weekly):
        source = inspect.getsource(module)
        assert "step1a_artifact_switch_status" not in source
        assert "build_step1a_active_research_anchor_registry" not in source
        assert "build_step1a_research_anchor_approvals_validation" not in source

    _setup_repo(tmp_path, monkeypatch)
    step1_research.parse_step1_output(strategy_settings=_settings())

    # The embedded selection artifact remains production-sourced (S1A-2), not
    # Step 1A output — the switch touched only the baseline registry writer.
    selection = _read(step1_research.step1_embedded_active_registry_selection_path())
    assert selection["production_source"] is True
    assert selection["step1a_output"] is False
    packet = _read(step1_research.step1_evidence_packet_path())
    assert selection["selected_registry"] == packet["active_anchor_registry"]
