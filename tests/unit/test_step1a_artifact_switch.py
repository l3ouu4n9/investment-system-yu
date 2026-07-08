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
    compile_active_research_anchor_registry,
    write_active_research_anchor_registry,
)
from investment_orchestrator.research.approval_registry_dual_read_diff import (
    build_approval_registry_dual_read_diff,
)
from investment_orchestrator.research.approval_registry_switch_readiness import (
    write_approval_registry_switch_readiness,
)
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    build_active_research_anchor_registry_with_approvals,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    compute_operator_completed_anchor_sha256 as sha,
    validate_research_anchor_approvals,
    write_research_anchor_approvals_validation,
)
from investment_orchestrator.research.research_anchor_revocation_manifest import (
    validate_research_anchor_revocations,
    write_research_anchor_revocations_validation,
)
from investment_orchestrator.workflow import step1_research
from investment_orchestrator.workflow.step1a_grounding_compile import (
    _ARTIFACT_KEYS,
    STEP1A_WRITER_SOURCE_ARTIFACTS,
    build_step1a_active_research_anchor_registry,
    build_step1a_active_research_anchor_registry_with_approvals,
    build_step1a_approval_registry_dual_read_diff,
    build_step1a_approval_registry_switch_readiness,
    build_step1a_grounding_compile_bundle,
    build_step1a_research_anchor_approvals_validation,
    build_step1a_research_anchor_revocations_validation,
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

    # S1A-5.1/S1A-11 boundary-scope markers: the switches change WHO compiles the
    # payloads — not artifact paths, not the embedded selection, not support_signals
    # grounding, not readiness, and not any order path or runtime authority. S1A-11
    # flips evidence_packet_uses_step1a_output to True (its disk writer is now
    # Step 1A-sourced behind the strict parity guard); the runtime-authority markers
    # stay False.
    assert status["production_artifact_paths_switched"] is False
    assert status["evidence_packet_uses_step1a_output"] is True
    assert status["embedded_selection_uses_step1a_output"] is False
    assert status["support_signals_uses_step1a_output"] is False
    assert status["readiness_uses_step1a_output"] is False
    assert status["order_path_uses_step1a_output"] is False
    assert status["runtime_authority_uses_step1a_output"] is False

    entry = status["switched_artifacts"]["active_research_anchor_registry"]
    assert entry["writer_source"] == "step1a"
    assert entry["fallback_used"] is False
    assert entry["error_summary"] == ""
    assert entry["output_path"] == str(step1_research.step1_active_research_anchor_registry_path())

    # Exactly the seven S1A-3/4/5/6/7/8/11 switched artifacts (S1A-11 adds
    # evidence_packet) — no eighth switch.
    assert sorted(status["switched_artifacts"]) == [
        "active_research_anchor_registry",
        "active_research_anchor_registry_with_approvals",
        "approval_registry_dual_read_diff",
        "approval_registry_switch_readiness",
        "evidence_packet",
        "research_anchor_approvals_validation",
        "research_anchor_revocations_validation",
    ]
    # S1A-5.1 drift guard: the code-level design-state constant the shadow diff
    # reports must match the per-run switch-status truth, and every switched
    # artifact must keep a shadow comparison entry. A seventh switch that skips
    # updating the constant fails here.
    assert sorted(STEP1A_WRITER_SOURCE_ARTIFACTS) == sorted(status["switched_artifacts"])
    assert set(STEP1A_WRITER_SOURCE_ARTIFACTS) <= set(_ARTIFACT_KEYS)


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
    assert "build_step1a_research_anchor_revocations_validation(" in src
    # S1A-6: the OVERLAY lineage moved verbatim into the with-approvals accessor,
    # so _build now contains no raw overlay-validator call at all.
    assert "build_step1a_active_research_anchor_registry_with_approvals(" in src
    assert src.count("validate_research_anchor_approvals(") == 0
    assert src.count("validate_research_anchor_revocations(") == 0

    overlay_src = inspect.getsource(
        step1a.build_step1a_active_research_anchor_registry_with_approvals
    )
    # The Step 1 dual-read writer's overlay flavor, preserved exactly inside the
    # accessor: baseline via the S1A-3 accessor; ONE overlay approvals validation
    # WITHOUT as_of_date (the settings-anchored S1A-4 REPORT accessor is not
    # reused); ONE overlay revocations validation keeping the baseline-coupled
    # as-of binding (the settings-anchored S1A-5 REPORT accessor is not reused).
    assert "build_step1a_active_research_anchor_registry(" in overlay_src
    assert overlay_src.count("validate_research_anchor_approvals(") == 1
    assert overlay_src.count("validate_research_anchor_revocations(") == 1
    approvals_call_args = overlay_src.split("validate_research_anchor_approvals(")[-1].split(")")[0]
    assert "as_of_date" not in approvals_call_args
    revocations_call_args = overlay_src.split("validate_research_anchor_revocations(")[-1]
    assert 'as_of_date=baseline.get("as_of_date")' in revocations_call_args
    # The never-raise compile helper is deliberately NOT used: the accessor must
    # raise so the switched writer can fall back to the legacy payload.
    code_only = overlay_src.replace(
        "``compile_active_research_anchor_registry_with_approvals``", ""
    )
    assert "compile_active_research_anchor_registry_with_approvals" not in code_only


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


def _revocation(anchor: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    base = {
        "revocation_id": "REV-1",
        "target_type": "approval_anchor",
        "approval_id": "APR-1",
        "anchor_id": anchor["anchor_id"],
        "operator_completed_anchor_sha256": sha(anchor),
        "effective_as_of": "2026-06-20",
        "reason": "Thesis invalidated.",
        "revoked_by": "operator",
    }
    base.update(overrides)
    return base


def _revocations_doc(
    revocations: list[dict[str, Any]] | None,
    *,
    include_approval: bool = True,
    **overrides: Any,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": "2026-06-28",
        "approvals": [_approval(_base_anchor())] if include_approval else [],
    }
    if revocations is not None:
        doc["revocations"] = revocations
    doc.update(overrides)
    return doc


def _no_as_of_doc() -> dict[str, Any]:
    doc = _revocations_doc([_revocation(_base_anchor(), effective_as_of="2026-12-01")])
    del doc["as_of_date"]
    return doc


# scenario -> (manifest document | "missing" | raw text, strategy_settings)
_REVOCATIONS_BYTE_IDENTITY_MATRIX: dict[str, tuple[Any, Any]] = {
    "valid_effective_revocation": (
        _revocations_doc([_revocation(_base_anchor())]),
        _MATRIX_SETTINGS,
    ),
    "future_effective_pending": (
        _revocations_doc([_revocation(_base_anchor(), effective_as_of="2026-12-01")]),
        _MATRIX_SETTINGS,
    ),
    "no_revocations_section": (_revocations_doc(None), _MATRIX_SETTINGS),
    "empty_revocations_list": (_revocations_doc([]), _MATRIX_SETTINGS),
    "missing_manifest": ("missing", _MATRIX_SETTINGS),
    "malformed_yaml": ("::: not yaml {{{", _MATRIX_SETTINGS),
    "unknown_target_fails_closed": (
        _revocations_doc(
            [_revocation(_base_anchor(), approval_id="APR-MISSING", anchor_id="NO_SUCH_ANCHOR")]
        ),
        _MATRIX_SETTINGS,
    ),
    "sha256_mismatch": (
        _revocations_doc([_revocation(_base_anchor(), operator_completed_anchor_sha256="0" * 64)]),
        _MATRIX_SETTINGS,
    ),
    "inconsistent_triple": (
        # approval_id matches an approval whose anchor differs from anchor_id+sha.
        _revocations_doc([_revocation(_base_anchor(anchor_id="OTHER_ANCHOR"))]),
        _MATRIX_SETTINGS,
    ),
    "duplicate_revocation_ids": (
        _revocations_doc(
            [_revocation(_base_anchor()), _revocation(_base_anchor(), reason="duplicate copy")]
        ),
        _MATRIX_SETTINGS,
    ),
    "unsupported_target_type": (
        _revocations_doc([_revocation(_base_anchor(), target_type="portfolio_wide")]),
        _MATRIX_SETTINGS,
    ),
    "forbidden_anchor_defining_fields": (
        _revocations_doc(
            [_revocation(_base_anchor(), valid_until="2027-12-31", applicable_tickers=["QQQ"])]
        ),
        _MATRIX_SETTINGS,
    ),
    "llm_generated_true_fails_closed": (
        _revocations_doc([_revocation(_base_anchor())], is_llm_generated=True),
        _MATRIX_SETTINGS,
    ),
    "missing_reason": (
        _revocations_doc([{k: v for k, v in _revocation(_base_anchor()).items() if k != "reason"}]),
        _MATRIX_SETTINGS,
    ),
    # A manifest WITHOUT its own as_of_date makes the today/as_of_date parameters
    # matter for effective-date evaluation — the highest-value byte case.
    "manifest_without_as_of_date": (_no_as_of_doc(), _MATRIX_SETTINGS),
    "settings_none": (_revocations_doc([_revocation(_base_anchor())]), None),
    "settings_missing_universes": (
        _revocations_doc([_revocation(_base_anchor())]),
        {"as_of": "2026-06-28"},
    ),
    "missing_as_of": (
        _revocations_doc([_revocation(_base_anchor())]),
        {"core_universe": ["QQQ", "VOO"], "satellite_universe": ["SMH"]},
    ),
}


@pytest.mark.parametrize("scenario", sorted(_REVOCATIONS_BYTE_IDENTITY_MATRIX))
def test_revocations_accessor_output_byte_identical_to_legacy_writer(
    scenario: str, tmp_path: Path
) -> None:
    source, settings = _REVOCATIONS_BYTE_IDENTITY_MATRIX[scenario]
    manifest_path = tmp_path / "research_anchor_approvals.yaml"
    if source == "missing":
        pass
    elif isinstance(source, str):
        manifest_path.write_text(source, encoding="utf-8")
    else:
        manifest_path.write_text(json.dumps(source), encoding="utf-8")

    settings_as_of = settings.get("as_of") if isinstance(settings, dict) else None
    legacy_path = tmp_path / "legacy_research_anchor_revocations_validation.json"
    write_research_anchor_revocations_validation(
        output_path=legacy_path,
        manifest_path=manifest_path,
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(settings),
        today=settings_as_of,
        as_of_date=settings_as_of if isinstance(settings_as_of, str) else None,
    )
    legacy_payload = json.loads(legacy_path.read_text(encoding="utf-8"))

    step1a_payload = build_step1a_research_anchor_revocations_validation(
        strategy_settings=settings,
        research_anchor_approvals_path=manifest_path,
    )

    assert json.loads(json.dumps(step1a_payload)) == legacy_payload


def test_revocations_non_string_as_of_normalizes_to_none_and_matches_bundle(
    tmp_path: Path,
) -> None:
    """Pin the documented S1A-5 normalization edge.

    Legacy passes a raw non-string ``as_of`` (e.g. an unquoted YAML date) through
    as ``today`` while Step 1A's ``_first_str`` normalizes it to None — so byte
    identity vs legacy is deliberately NOT asserted for this input class. The
    accessor must behave exactly as if ``as_of`` were absent and must equal the
    Step 1A bundle's report variant. The manifest here deliberately omits its own
    ``as_of_date``: a dated manifest anchors effective-date evaluation itself and
    would mask the parameter divergence entirely.
    """
    universes = {"core_universe": ["QQQ", "VOO"], "satellite_universe": ["SMH"]}
    date_settings = {"as_of": datetime.date(2026, 6, 28), **universes}
    no_asof_settings = dict(universes)
    anchors_path = tmp_path / "research_anchors.yaml"
    anchors_path.write_text(json.dumps(_anchors_doc([_base_anchor()])), encoding="utf-8")
    manifest_path = tmp_path / "research_anchor_approvals.yaml"
    manifest_path.write_text(json.dumps(_no_as_of_doc()), encoding="utf-8")

    accessor_with_date = build_step1a_research_anchor_revocations_validation(
        strategy_settings=date_settings,
        research_anchor_approvals_path=manifest_path,
    )
    accessor_without_asof = build_step1a_research_anchor_revocations_validation(
        strategy_settings=no_asof_settings,
        research_anchor_approvals_path=manifest_path,
    )
    assert accessor_with_date == accessor_without_asof

    bundle = build_step1a_grounding_compile_bundle(
        strategy_settings=date_settings,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=manifest_path,
    )
    assert accessor_with_date == bundle["artifacts"]["research_anchor_revocations_validation"]


def test_parse_writes_revocations_validation_from_step1a_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    artifact_path = step1_research.step1_research_anchor_revocations_validation_path()
    legacy_path = tmp_path / "independent_legacy_revocations_validation.json"
    write_research_anchor_revocations_validation(
        output_path=legacy_path,
        manifest_path=tmp_path / "inputs" / "current" / "research_anchor_approvals.yaml",
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(_settings()),
        today=_settings()["as_of"],
        as_of_date=_settings()["as_of"],
    )
    assert artifact_path.read_bytes() == legacy_path.read_bytes()

    assert result["research_anchor_revocations_validation_writer_source"] == "step1a"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["research_anchor_revocations_validation"]
    assert entry["writer_source"] == "step1a"
    assert entry["fallback_used"] is False
    assert entry["error_summary"] == ""
    assert entry["output_path"] == str(artifact_path)


def test_revocations_accessor_failure_falls_back_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_accessor(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("revocations accessor exploded")

    monkeypatch.setattr(
        step1_research, "build_step1a_research_anchor_revocations_validation", broken_accessor
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Per-artifact independence: the other two switches are unaffected.
    assert result["active_research_anchor_registry_writer_source"] == "step1a"
    assert result["research_anchor_approvals_validation_writer_source"] == "step1a"
    assert result["research_anchor_revocations_validation_writer_source"] == "legacy_fallback"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    assert status["switched_artifacts"]["active_research_anchor_registry"]["writer_source"] == "step1a"
    assert (
        status["switched_artifacts"]["research_anchor_approvals_validation"]["writer_source"]
        == "step1a"
    )
    entry = status["switched_artifacts"]["research_anchor_revocations_validation"]
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_accessor_failed" in entry["error_summary"]

    # The legacy fallback wrote the byte-identical payload; shadow stays green.
    legacy_path = tmp_path / "independent_legacy_revocations_validation.json"
    write_research_anchor_revocations_validation(
        output_path=legacy_path,
        manifest_path=tmp_path / "inputs" / "current" / "research_anchor_approvals.yaml",
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(_settings()),
        today=_settings()["as_of"],
        as_of_date=_settings()["as_of"],
    )
    artifact_path = step1_research.step1_research_anchor_revocations_validation_path()
    assert artifact_path.read_bytes() == legacy_path.read_bytes()
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "pass"
    assert diff["comparisons"]["research_anchor_revocations_validation"]["semantic_match"] is True


def test_revocations_double_failure_preserves_swallowed_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_accessor(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("revocations accessor exploded")

    def broken_legacy(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("legacy revocations writer exploded")

    monkeypatch.setattr(
        step1_research, "build_step1a_research_anchor_revocations_validation", broken_accessor
    )
    monkeypatch.setattr(
        step1_research, "write_research_anchor_revocations_validation", broken_legacy
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Pre-switch behavior preserved: artifact absent, parse continues.
    assert Path(result["research_output_path"]).is_file()
    assert not step1_research.step1_research_anchor_revocations_validation_path().is_file()
    assert result["research_anchor_revocations_validation_writer_source"] == "unwritten"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    assert status["switched_artifacts"]["active_research_anchor_registry"]["writer_source"] == "step1a"
    assert (
        status["switched_artifacts"]["research_anchor_approvals_validation"]["writer_source"]
        == "step1a"
    )
    entry = status["switched_artifacts"]["research_anchor_revocations_validation"]
    assert entry["writer_source"] == "unwritten"
    assert entry["fallback_used"] is True
    assert "step1a_accessor_failed" in entry["error_summary"]
    assert "legacy_writer_failed" in entry["error_summary"]

    # Explicit skip in the shadow diff — never a false complete pass.
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    revocations_cmp = diff["comparisons"]["research_anchor_revocations_validation"]
    assert revocations_cmp["comparison_skipped"] is True
    assert revocations_cmp["skip_reason"] == "current_step1_artifact_unavailable_or_malformed"
    assert diff["comparison_status"] in ("mismatch", "pass_with_skips")
    assert diff["comparison_complete"] is False
    assert diff["parity_passed"] is False
    assert diff["production_artifacts_unchanged"] is True

    decision = _read(Path(result["research_degraded_mode_decision_path"]))
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]


def _legacy_with_approvals_compile(
    anchors_path: Path, approvals_path: Path, settings: Any
) -> dict[str, Any]:
    """Replicate the legacy Step 1 dual-read writer's overlay derivation verbatim.

    Baseline compile, overlay approvals validation WITHOUT ``as_of_date``, overlay
    revocations validation with the baseline-coupled ``as_of_date``, then the
    shared with-approvals builder — the exact lineage
    ``_write_approval_registry_dual_read_report_only`` retains for the dual-read
    diff and as the switched write's fallback payload.
    """
    allowed_universe = step1_research._allowed_buy_universe_for_anchor_registry(settings)
    settings_as_of = settings.get("as_of") if isinstance(settings, dict) else None
    baseline = compile_active_research_anchor_registry(
        anchors_path=anchors_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
    )
    approvals_validation = validate_research_anchor_approvals(
        manifest_path=approvals_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
    )
    revocations_validation = validate_research_anchor_revocations(
        manifest_path=approvals_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
        as_of_date=baseline.get("as_of_date") if isinstance(baseline, dict) else None,
    )
    return build_active_research_anchor_registry_with_approvals(
        baseline=baseline,
        approvals_validation=approvals_validation,
        revocations_validation=revocations_validation,
    )


_BASELINE_ANCHORS_DOC = _anchors_doc([_anchor("BASE_QQQ", "QQQ")])

# scenario -> (anchors doc | "missing" | raw text,
#              approvals doc | "missing" | raw text, strategy_settings)
_WITH_APPROVALS_BYTE_IDENTITY_MATRIX: dict[str, tuple[Any, Any, Any]] = {
    "no_approvals": (_BASELINE_ANCHORS_DOC, _approvals_doc([]), _MATRIX_SETTINGS),
    "valid_approval_derived_anchor": (
        _BASELINE_ANCHORS_DOC,
        _approvals_doc([_approval(_base_anchor())]),
        _MATRIX_SETTINGS,
    ),
    "expired_approval": (
        _BASELINE_ANCHORS_DOC,
        _approvals_doc([_approval(_base_anchor(valid_until="2026-02-28"))]),
        _MATRIX_SETTINGS,
    ),
    "out_of_universe_approval": (
        _BASELINE_ANCHORS_DOC,
        _approvals_doc([_approval(_base_anchor(applicable_tickers=["ZZZT"]))]),
        _MATRIX_SETTINGS,
    ),
    "approval_sha256_mismatch": (
        _BASELINE_ANCHORS_DOC,
        _approvals_doc([_mismatched_sha_approval()]),
        _MATRIX_SETTINGS,
    ),
    "duplicate_approval_ids": (
        _BASELINE_ANCHORS_DOC,
        _approvals_doc([_approval(_base_anchor()), _approval(_base_anchor(summary="dup"))]),
        _MATRIX_SETTINGS,
    ),
    "no_revocations_section": (_BASELINE_ANCHORS_DOC, _revocations_doc(None), _MATRIX_SETTINGS),
    "future_revocation_pending": (
        _BASELINE_ANCHORS_DOC,
        _revocations_doc([_revocation(_base_anchor(), effective_as_of="2026-12-01")]),
        _MATRIX_SETTINGS,
    ),
    "effective_revocation_applied": (
        _BASELINE_ANCHORS_DOC,
        _revocations_doc([_revocation(_base_anchor())]),
        _MATRIX_SETTINGS,
    ),
    "unknown_revocation_target_fails_closed": (
        _BASELINE_ANCHORS_DOC,
        _revocations_doc(
            [_revocation(_base_anchor(), approval_id="APR-MISSING", anchor_id="NO_SUCH_ANCHOR")]
        ),
        _MATRIX_SETTINGS,
    ),
    "triple_binding_mismatch": (
        _BASELINE_ANCHORS_DOC,
        _revocations_doc([_revocation(_base_anchor(anchor_id="OTHER_ANCHOR"))]),
        _MATRIX_SETTINGS,
    ),
    "malformed_approvals_yaml": (_BASELINE_ANCHORS_DOC, "::: not yaml {{{", _MATRIX_SETTINGS),
    "missing_approvals_manifest": (_BASELINE_ANCHORS_DOC, "missing", _MATRIX_SETTINGS),
    "malformed_anchors_yaml": (
        "::: not yaml {{{",
        _approvals_doc([_approval(_base_anchor())]),
        _MATRIX_SETTINGS,
    ),
    "missing_anchors_yaml": (
        "missing",
        _approvals_doc([_approval(_base_anchor())]),
        _MATRIX_SETTINGS,
    ),
    "empty_baseline_anchor_list": (
        _anchors_doc([]),
        _approvals_doc([_approval(_base_anchor())]),
        _MATRIX_SETTINGS,
    ),
    "invalid_baseline_anchor": (
        _anchors_doc([{"anchor_id": "BROKEN_ONLY_ID"}]),
        _approvals_doc([_approval(_base_anchor())]),
        _MATRIX_SETTINGS,
    ),
    "settings_none": (
        _BASELINE_ANCHORS_DOC,
        _approvals_doc([_approval(_base_anchor())]),
        None,
    ),
    "settings_missing_universes": (
        _BASELINE_ANCHORS_DOC,
        _approvals_doc([_approval(_base_anchor())]),
        {"as_of": "2026-06-28"},
    ),
    "missing_as_of": (
        _BASELINE_ANCHORS_DOC,
        _approvals_doc([_approval(_base_anchor())]),
        {"core_universe": ["QQQ", "VOO"], "satellite_universe": ["SMH"]},
    ),
}


@pytest.mark.parametrize("scenario", sorted(_WITH_APPROVALS_BYTE_IDENTITY_MATRIX))
def test_with_approvals_accessor_output_byte_identical_to_legacy_writer(
    scenario: str, tmp_path: Path
) -> None:
    anchors_source, approvals_source, settings = _WITH_APPROVALS_BYTE_IDENTITY_MATRIX[scenario]
    anchors_path = tmp_path / "research_anchors.yaml"
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    for path, source in ((anchors_path, anchors_source), (approvals_path, approvals_source)):
        if source == "missing":
            continue
        path.write_text(
            source if isinstance(source, str) else json.dumps(source), encoding="utf-8"
        )

    legacy_payload = json.loads(
        json.dumps(_legacy_with_approvals_compile(anchors_path, approvals_path, settings))
    )
    step1a_payload = build_step1a_active_research_anchor_registry_with_approvals(
        strategy_settings=settings,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=approvals_path,
    )

    assert json.loads(json.dumps(step1a_payload)) == legacy_payload


def test_with_approvals_non_string_as_of_guard_falls_back_to_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the S1A-6 handling of the non-string ``as_of`` normalization edge.

    Legacy passes a raw non-string ``as_of`` (e.g. an unquoted YAML date)
    through as ``today`` while the Step 1A accessor's ``_first_str`` normalizes
    it to None — the established S1A-3/4/5 convention. Unlike those switches,
    the with-approvals artifact is written by the SAME function that builds the
    dual-read diff from the legacy in-memory objects, so silently writing the
    normalized Step 1A candidate could make the artifact/diff pair internally
    inconsistent. The run-time parity guard therefore keeps the LEGACY bytes and
    records a flagged fallback whenever the candidate differs — including this
    edge. The anchors doc here is deliberately UNDATED so ``today`` matters: the
    baseline as-of resolution prefers ``today`` and only falls back to the
    file's ``as_of_date``, which would otherwise mask the divergence.
    """
    _setup_repo(tmp_path, monkeypatch)
    inputs = tmp_path / "inputs" / "current"
    undated = _anchors_doc([_base_anchor()])
    del undated["as_of_date"]
    (inputs / "research_anchors.yaml").write_text(json.dumps(undated), encoding="utf-8")

    date_settings: dict[str, Any] = {**_settings(), "as_of": datetime.date(2026, 6, 28)}
    entry, diff_entry = step1_research._write_approval_registry_dual_read_report_only(
        strategy_settings=date_settings
    )

    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_overlay_parity_mismatch" in entry["error_summary"]
    # S1A-8: the non-string as_of edge also drives the dual-read diff guard to
    # fall back, since its Step 1A diff is composed from the same as_of-normalized
    # accessors while the legacy diff uses the raw-date lineage.
    assert diff_entry["writer_source"] == "legacy_fallback"
    assert diff_entry["fallback_used"] is True
    assert "step1a_dual_read_diff_parity_mismatch" in diff_entry["error_summary"]

    # Disk bytes are the LEGACY payload (raw date passed through and normalized
    # by the compilers), not the Step 1A candidate (as_of normalized to None).
    artifact = _read(step1_research.step1_active_research_anchor_registry_with_approvals_path())
    legacy_payload = json.loads(
        json.dumps(
            _legacy_with_approvals_compile(
                inputs / "research_anchors.yaml",
                inputs / "research_anchor_approvals.yaml",
                date_settings,
            )
        )
    )
    assert artifact == legacy_payload
    assert artifact["as_of_date"] == "2026-06-28"

    # The dual-read diff was still written from the same legacy lineage.
    assert step1_research.step1_approval_registry_dual_read_diff_path().is_file()

    # The accessor alone still normalizes and matches the Step 1A bundle.
    accessor_payload = build_step1a_active_research_anchor_registry_with_approvals(
        strategy_settings=date_settings,
        research_anchors_path=inputs / "research_anchors.yaml",
        research_anchor_approvals_path=inputs / "research_anchor_approvals.yaml",
    )
    assert accessor_payload["as_of_date"] is None
    bundle = build_step1a_grounding_compile_bundle(
        strategy_settings=date_settings,
        research_anchors_path=inputs / "research_anchors.yaml",
        research_anchor_approvals_path=inputs / "research_anchor_approvals.yaml",
    )
    assert accessor_payload == bundle["artifacts"]["active_research_anchor_registry_with_approvals"]


def test_bundle_with_approvals_artifact_equals_accessor_output(tmp_path: Path) -> None:
    anchors_path = tmp_path / "research_anchors.yaml"
    anchors_path.write_text(json.dumps(_BASELINE_ANCHORS_DOC), encoding="utf-8")
    manifest_path = tmp_path / "research_anchor_approvals.yaml"
    manifest_path.write_text(
        json.dumps(_revocations_doc([_revocation(_base_anchor())])), encoding="utf-8"
    )

    accessor_payload = build_step1a_active_research_anchor_registry_with_approvals(
        strategy_settings=_MATRIX_SETTINGS,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=manifest_path,
        generated_at="2026-06-28T00:00:00+00:00",
    )
    bundle = build_step1a_grounding_compile_bundle(
        strategy_settings=_MATRIX_SETTINGS,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=manifest_path,
        generated_at="2026-06-28T00:00:00+00:00",
    )
    assert accessor_payload == bundle["artifacts"]["active_research_anchor_registry_with_approvals"]
    assert accessor_payload["generated_at"] == "2026-06-28T00:00:00+00:00"


def test_parse_writes_with_approvals_from_step1a_source_and_dual_read_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    inputs = tmp_path / "inputs" / "current"
    legacy_payload = json.loads(
        json.dumps(
            _legacy_with_approvals_compile(
                inputs / "research_anchors.yaml",
                inputs / "research_anchor_approvals.yaml",
                _settings(),
            )
        )
    )
    artifact_path = step1_research.step1_active_research_anchor_registry_with_approvals_path()
    assert _read(artifact_path) == legacy_payload

    assert result["active_research_anchor_registry_with_approvals_writer_source"] == "step1a"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["active_research_anchor_registry_with_approvals"]
    assert entry["writer_source"] == "step1a"
    assert entry["fallback_used"] is False
    assert entry["error_summary"] == ""
    assert entry["output_path"] == str(artifact_path)
    # All seven switched writers report step1a in a normal run (S1A-11 adds
    # evidence_packet: on a clean run its strict parity guard passes).
    assert {k: v["writer_source"] for k, v in status["switched_artifacts"].items()} == {
        "active_research_anchor_registry": "step1a",
        "research_anchor_approvals_validation": "step1a",
        "research_anchor_revocations_validation": "step1a",
        "active_research_anchor_registry_with_approvals": "step1a",
        "approval_registry_switch_readiness": "step1a",
        "approval_registry_dual_read_diff": "step1a",
        "evidence_packet": "step1a",
    }

    # S1A-8: the dual-read diff is now switched but its guard writes the Step 1A
    # diff only when it byte-matches the pure legacy lineage (legacy baseline +
    # legacy with-approvals objects), so on-disk content is unchanged.
    assert result["approval_registry_dual_read_diff_writer_source"] == "step1a"
    diff_entry = status["switched_artifacts"]["approval_registry_dual_read_diff"]
    assert diff_entry["writer_source"] == "step1a"
    assert diff_entry["fallback_used"] is False
    assert diff_entry["error_summary"] == ""
    assert diff_entry["output_path"] == str(
        step1_research.step1_approval_registry_dual_read_diff_path()
    )
    legacy_baseline = compile_active_research_anchor_registry(
        anchors_path=inputs / "research_anchors.yaml",
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(_settings()),
        today=_settings()["as_of"],
    )
    expected_diff = json.loads(
        json.dumps(
            build_approval_registry_dual_read_diff(
                baseline_registry=legacy_baseline,
                approvals_registry=legacy_payload,
                baseline_registry_path=str(
                    step1_research.step1_active_research_anchor_registry_path()
                ),
                approvals_registry_path=str(artifact_path),
            )
        )
    )
    assert _read(step1_research.step1_approval_registry_dual_read_diff_path()) == expected_diff

    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "pass"
    assert diff["comparison_complete"] is True
    assert (
        diff["comparisons"]["active_research_anchor_registry_with_approvals"]["semantic_match"]
        is True
    )
    assert (
        diff["comparisons"]["approval_registry_dual_read_diff"]["semantic_match"] is True
    )


def test_with_approvals_accessor_failure_falls_back_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_accessor(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("with-approvals accessor exploded")

    monkeypatch.setattr(
        step1_research,
        "build_step1a_active_research_anchor_registry_with_approvals",
        broken_accessor,
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Per-artifact independence: the other three switches are unaffected.
    assert result["active_research_anchor_registry_writer_source"] == "step1a"
    assert result["research_anchor_approvals_validation_writer_source"] == "step1a"
    assert result["research_anchor_revocations_validation_writer_source"] == "step1a"
    assert result["active_research_anchor_registry_with_approvals_writer_source"] == "legacy_fallback"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["active_research_anchor_registry_with_approvals"]
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_accessor_failed" in entry["error_summary"]

    # The retained legacy derivation wrote the byte-identical payload and the
    # dual-read diff; shadow stays green.
    inputs = tmp_path / "inputs" / "current"
    legacy_payload = json.loads(
        json.dumps(
            _legacy_with_approvals_compile(
                inputs / "research_anchors.yaml",
                inputs / "research_anchor_approvals.yaml",
                _settings(),
            )
        )
    )
    assert (
        _read(step1_research.step1_active_research_anchor_registry_with_approvals_path())
        == legacy_payload
    )
    assert step1_research.step1_approval_registry_dual_read_diff_path().is_file()
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "pass"
    assert (
        diff["comparisons"]["active_research_anchor_registry_with_approvals"]["semantic_match"]
        is True
    )


def test_with_approvals_parity_mismatch_falls_back_to_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    original = step1_research.build_step1a_active_research_anchor_registry_with_approvals

    def mutated_accessor(**kwargs: Any) -> dict[str, Any]:
        payload = original(**kwargs)
        payload["counts"] = dict(payload.get("counts") or {}, mutated=1)
        return payload

    monkeypatch.setattr(
        step1_research,
        "build_step1a_active_research_anchor_registry_with_approvals",
        mutated_accessor,
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["active_research_anchor_registry_with_approvals_writer_source"] == "legacy_fallback"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["active_research_anchor_registry_with_approvals"]
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_overlay_parity_mismatch" in entry["error_summary"]

    # The guard kept the legacy bytes: the mutated candidate never reached disk.
    inputs = tmp_path / "inputs" / "current"
    legacy_payload = json.loads(
        json.dumps(
            _legacy_with_approvals_compile(
                inputs / "research_anchors.yaml",
                inputs / "research_anchor_approvals.yaml",
                _settings(),
            )
        )
    )
    artifact = _read(step1_research.step1_active_research_anchor_registry_with_approvals_path())
    assert artifact == legacy_payload
    assert "mutated" not in artifact["counts"]
    assert step1_research.step1_approval_registry_dual_read_diff_path().is_file()

    # Legacy bytes match the (unpatched) bundle, so the shadow comparison stays
    # green — the divergence is surfaced via the flagged fallback, and no
    # divergent byte shipped.
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert (
        diff["comparisons"]["active_research_anchor_registry_with_approvals"]["semantic_match"]
        is True
    )


def test_with_approvals_double_failure_preserves_swallowed_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_legacy_compile(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("legacy overlay derivation exploded")

    # Breaking the legacy baseline compile kills the whole legacy derivation the
    # dual-read PAIR depends on; the accessor is never consulted, preserving the
    # pre-switch all-or-nothing presence semantics for this writer.
    monkeypatch.setattr(
        step1_research, "compile_active_research_anchor_registry", broken_legacy_compile
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Pre-switch behavior preserved: BOTH artifacts absent, parse continues.
    assert Path(result["research_output_path"]).is_file()
    assert not step1_research.step1_active_research_anchor_registry_with_approvals_path().is_file()
    assert not step1_research.step1_approval_registry_dual_read_diff_path().is_file()
    assert result["active_research_anchor_registry_with_approvals_writer_source"] == "unwritten"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["active_research_anchor_registry_with_approvals"]
    assert entry["writer_source"] == "unwritten"
    assert "legacy_derivation_or_write_failed" in entry["error_summary"]
    # The other three switches are unaffected.
    assert status["switched_artifacts"]["active_research_anchor_registry"]["writer_source"] == "step1a"
    assert (
        status["switched_artifacts"]["research_anchor_approvals_validation"]["writer_source"]
        == "step1a"
    )
    assert (
        status["switched_artifacts"]["research_anchor_revocations_validation"]["writer_source"]
        == "step1a"
    )

    # Explicit skips in the shadow diff — never a false complete pass.
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    for key in (
        "active_research_anchor_registry_with_approvals",
        "approval_registry_dual_read_diff",
    ):
        comparison = diff["comparisons"][key]
        assert comparison["comparison_skipped"] is True
        assert comparison["skip_reason"] == "current_step1_artifact_unavailable_or_malformed"
    assert diff["comparison_status"] in ("mismatch", "pass_with_skips")
    assert diff["comparison_complete"] is False
    assert diff["parity_passed"] is False

    decision = _read(Path(result["research_degraded_mode_decision_path"]))
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]


# S1A-7 reuses the S1A-6 scenario matrix verbatim: the readiness evaluation
# recomputes the same baseline/approvals/revocations lineage internally, so the
# same anchors/approvals/settings variants exercise its full behavior surface
# (valid/expired/out-of-universe/sha-mismatch/duplicate approvals, effective/
# future/unknown-target/triple-binding revocations, malformed/missing inputs,
# baseline empty/invalid, settings variants).
@pytest.mark.parametrize("scenario", sorted(_WITH_APPROVALS_BYTE_IDENTITY_MATRIX))
def test_readiness_accessor_output_byte_identical_to_legacy_writer(
    scenario: str, tmp_path: Path
) -> None:
    anchors_source, approvals_source, settings = _WITH_APPROVALS_BYTE_IDENTITY_MATRIX[scenario]
    anchors_path = tmp_path / "research_anchors.yaml"
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    for path, source in ((anchors_path, anchors_source), (approvals_path, approvals_source)):
        if source == "missing":
            continue
        path.write_text(
            source if isinstance(source, str) else json.dumps(source), encoding="utf-8"
        )

    settings_as_of = settings.get("as_of") if isinstance(settings, dict) else None
    legacy_path = tmp_path / "legacy_approval_registry_switch_readiness.json"
    write_approval_registry_switch_readiness(
        output_path=legacy_path,
        anchors_path=anchors_path,
        approvals_path=approvals_path,
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(settings),
        today=settings_as_of,
    )
    legacy_payload = json.loads(legacy_path.read_text(encoding="utf-8"))

    step1a_payload = build_step1a_approval_registry_switch_readiness(
        strategy_settings=settings,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=approvals_path,
    )

    assert json.loads(json.dumps(step1a_payload)) == legacy_payload


def test_readiness_non_string_as_of_normalizes_to_none_and_matches_bundle(
    tmp_path: Path,
) -> None:
    """Pin the documented S1A-7 normalization edge.

    Legacy passes a raw non-string ``as_of`` (e.g. an unquoted YAML date)
    through as ``today`` while the Step 1A accessor's ``_first_str`` normalizes
    it to None — the established S1A-3/4/5/6 convention — so byte identity vs
    legacy is deliberately NOT asserted for this input class. The accessor must
    behave exactly as if ``as_of`` were absent and must equal the Step 1A
    bundle's readiness payload. The anchors doc is deliberately UNDATED so
    ``today`` matters for the internally recomputed baseline. Unlike S1A-6
    there is no run-time parity guard: readiness has no paired sibling artifact
    whose consistency a divergence could break, and both sides share one
    deterministic builder.
    """
    universes = {"core_universe": ["QQQ", "VOO"], "satellite_universe": ["SMH"]}
    date_settings: dict[str, Any] = {"as_of": datetime.date(2026, 6, 28), **universes}
    no_asof_settings = dict(universes)
    anchors_path = tmp_path / "research_anchors.yaml"
    undated = _anchors_doc([_base_anchor()])
    del undated["as_of_date"]
    anchors_path.write_text(json.dumps(undated), encoding="utf-8")
    manifest_path = tmp_path / "research_anchor_approvals.yaml"
    manifest_path.write_text(json.dumps(_no_as_of_doc()), encoding="utf-8")

    accessor_with_date = build_step1a_approval_registry_switch_readiness(
        strategy_settings=date_settings,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=manifest_path,
    )
    accessor_without_asof = build_step1a_approval_registry_switch_readiness(
        strategy_settings=no_asof_settings,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=manifest_path,
    )
    assert accessor_with_date == accessor_without_asof

    bundle = build_step1a_grounding_compile_bundle(
        strategy_settings=date_settings,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=manifest_path,
    )
    assert accessor_with_date == bundle["artifacts"]["approval_registry_switch_readiness"]


def test_bundle_readiness_uses_accessor() -> None:
    import investment_orchestrator.workflow.step1a_grounding_compile as step1a

    src = inspect.getsource(step1a._build)
    # S1A-7: the readiness payload moved behind the shared accessor, so _build
    # contains no raw readiness-builder call at all.
    assert "build_step1a_approval_registry_switch_readiness(" in src
    assert src.count("build_approval_registry_switch_readiness(") == 0

    accessor_src = inspect.getsource(step1a.build_step1a_approval_registry_switch_readiness)
    assert accessor_src.count("build_approval_registry_switch_readiness(") == 1


def test_parse_writes_readiness_from_step1a_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    inputs = tmp_path / "inputs" / "current"
    artifact_path = step1_research.step1_approval_registry_switch_readiness_path()
    legacy_path = tmp_path / "independent_legacy_switch_readiness.json"
    write_approval_registry_switch_readiness(
        output_path=legacy_path,
        anchors_path=inputs / "research_anchors.yaml",
        approvals_path=inputs / "research_anchor_approvals.yaml",
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(_settings()),
        today=_settings()["as_of"],
    )
    assert artifact_path.read_bytes() == legacy_path.read_bytes()

    assert result["approval_registry_switch_readiness_writer_source"] == "step1a"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["approval_registry_switch_readiness"]
    assert entry["writer_source"] == "step1a"
    assert entry["fallback_used"] is False
    assert entry["error_summary"] == ""
    assert entry["output_path"] == str(artifact_path)

    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "pass"
    assert diff["parity_passed"] is True
    assert diff["comparison_complete"] is True
    assert diff["diagnostics"]["diagnostics_incomplete"] is False
    assert diff["skipped_artifacts"] == []
    assert diff["mismatch_artifacts"] == []
    assert diff["comparisons"]["approval_registry_switch_readiness"]["semantic_match"] is True


def test_readiness_accessor_failure_falls_back_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_accessor(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("readiness accessor exploded")

    monkeypatch.setattr(
        step1_research, "build_step1a_approval_registry_switch_readiness", broken_accessor
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Per-artifact independence: the other four switches are unaffected.
    assert result["active_research_anchor_registry_writer_source"] == "step1a"
    assert result["research_anchor_approvals_validation_writer_source"] == "step1a"
    assert result["research_anchor_revocations_validation_writer_source"] == "step1a"
    assert result["active_research_anchor_registry_with_approvals_writer_source"] == "step1a"
    assert result["approval_registry_switch_readiness_writer_source"] == "legacy_fallback"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["approval_registry_switch_readiness"]
    assert entry["writer_source"] == "legacy_fallback"
    assert entry["fallback_used"] is True
    assert "step1a_accessor_failed" in entry["error_summary"]

    # The legacy write wrapper produced the byte-identical payload (same shared
    # builder); shadow stays green.
    inputs = tmp_path / "inputs" / "current"
    legacy_path = tmp_path / "independent_legacy_switch_readiness.json"
    write_approval_registry_switch_readiness(
        output_path=legacy_path,
        anchors_path=inputs / "research_anchors.yaml",
        approvals_path=inputs / "research_anchor_approvals.yaml",
        allowed_universe=step1_research._allowed_buy_universe_for_anchor_registry(_settings()),
        today=_settings()["as_of"],
    )
    artifact_path = step1_research.step1_approval_registry_switch_readiness_path()
    assert artifact_path.read_bytes() == legacy_path.read_bytes()
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "pass"
    assert diff["comparisons"]["approval_registry_switch_readiness"]["semantic_match"] is True


def test_readiness_double_failure_preserves_swallowed_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_accessor(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("readiness accessor exploded")

    def broken_legacy(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("legacy readiness writer exploded")

    monkeypatch.setattr(
        step1_research, "build_step1a_approval_registry_switch_readiness", broken_accessor
    )
    monkeypatch.setattr(
        step1_research, "write_approval_registry_switch_readiness", broken_legacy
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # Pre-switch behavior preserved: artifact absent, parse continues.
    assert Path(result["research_output_path"]).is_file()
    assert not step1_research.step1_approval_registry_switch_readiness_path().is_file()
    assert result["approval_registry_switch_readiness_writer_source"] == "unwritten"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    entry = status["switched_artifacts"]["approval_registry_switch_readiness"]
    assert entry["writer_source"] == "unwritten"
    assert entry["fallback_used"] is True
    assert "step1a_accessor_failed" in entry["error_summary"]
    assert "legacy_writer_failed" in entry["error_summary"]
    # The other four switches are unaffected.
    assert status["switched_artifacts"]["active_research_anchor_registry"]["writer_source"] == "step1a"
    assert (
        status["switched_artifacts"]["active_research_anchor_registry_with_approvals"]["writer_source"]
        == "step1a"
    )

    # Explicit skip in the shadow diff — never a false complete pass.
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    readiness_cmp = diff["comparisons"]["approval_registry_switch_readiness"]
    assert readiness_cmp["comparison_skipped"] is True
    assert readiness_cmp["skip_reason"] == "current_step1_artifact_unavailable_or_malformed"
    assert diff["comparison_status"] in ("mismatch", "pass_with_skips")
    assert diff["comparison_complete"] is False
    assert diff["parity_passed"] is False
    assert diff["production_artifacts_unchanged"] is True

    decision = _read(Path(result["research_degraded_mode_decision_path"]))
    assert decision["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]


def _legacy_dual_read_diff_compile(
    anchors_path: Path,
    approvals_path: Path,
    settings: Any,
    *,
    baseline_registry_path: str | None = None,
    approvals_registry_path: str | None = None,
) -> dict[str, Any]:
    """Replicate the legacy Step 1 dual-read diff lineage verbatim.

    Legacy baseline compile + legacy overlay with-approvals + the shared
    ``build_approval_registry_dual_read_diff`` — the exact objects
    ``_write_approval_registry_dual_read_report_only`` feeds the diff builder and
    the S1A-8 parity guard's reference payload.
    """
    with_approvals = _legacy_with_approvals_compile(anchors_path, approvals_path, settings)
    allowed_universe = step1_research._allowed_buy_universe_for_anchor_registry(settings)
    settings_as_of = settings.get("as_of") if isinstance(settings, dict) else None
    baseline = compile_active_research_anchor_registry(
        anchors_path=anchors_path, allowed_universe=allowed_universe, today=settings_as_of
    )
    return build_approval_registry_dual_read_diff(
        baseline_registry=baseline,
        approvals_registry=with_approvals,
        baseline_registry_path=baseline_registry_path,
        approvals_registry_path=approvals_registry_path,
    )


@pytest.mark.parametrize("scenario", sorted(_WITH_APPROVALS_BYTE_IDENTITY_MATRIX))
def test_dual_read_diff_accessor_output_byte_identical_to_legacy(
    scenario: str, tmp_path: Path
) -> None:
    anchors_source, approvals_source, settings = _WITH_APPROVALS_BYTE_IDENTITY_MATRIX[scenario]
    anchors_path = tmp_path / "research_anchors.yaml"
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    for path, source in ((anchors_path, anchors_source), (approvals_path, approvals_source)):
        if source == "missing":
            continue
        path.write_text(
            source if isinstance(source, str) else json.dumps(source), encoding="utf-8"
        )

    legacy_diff = json.loads(
        json.dumps(
            _legacy_dual_read_diff_compile(
                anchors_path,
                approvals_path,
                settings,
                baseline_registry_path="/x/baseline.json",
                approvals_registry_path="/x/with_approvals.json",
            )
        )
    )
    step1a_diff = build_step1a_approval_registry_dual_read_diff(
        strategy_settings=settings,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=approvals_path,
        baseline_registry_artifact_path="/x/baseline.json",
        approvals_registry_artifact_path="/x/with_approvals.json",
    )

    assert json.loads(json.dumps(step1a_diff)) == legacy_diff


def test_dual_read_diff_non_string_as_of_normalizes_and_matches_bundle(
    tmp_path: Path,
) -> None:
    """Pin the documented S1A-8 normalization edge.

    Legacy passes a raw non-string ``as_of`` (unquoted YAML date) through as
    ``today`` while the Step 1A accessor's ``_first_str`` normalizes it to None —
    the established S1A-3/4/5/6/7 convention — so byte identity vs the raw-date
    legacy diff is deliberately NOT asserted for this input class. The accessor
    must behave as if ``as_of`` were absent and must equal the Step 1A bundle
    dual-read diff. The anchors doc is deliberately UNDATED so ``today`` matters
    for the internally recomposed baseline/with-approvals.
    """
    universes = {"core_universe": ["QQQ", "VOO"], "satellite_universe": ["SMH"]}
    date_settings: dict[str, Any] = {"as_of": datetime.date(2026, 6, 28), **universes}
    no_asof_settings = dict(universes)
    anchors_path = tmp_path / "research_anchors.yaml"
    undated = _anchors_doc([_base_anchor()])
    del undated["as_of_date"]
    anchors_path.write_text(json.dumps(undated), encoding="utf-8")
    manifest_path = tmp_path / "research_anchor_approvals.yaml"
    manifest_path.write_text(json.dumps(_no_as_of_doc()), encoding="utf-8")

    accessor_with_date = build_step1a_approval_registry_dual_read_diff(
        strategy_settings=date_settings,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=manifest_path,
    )
    accessor_without_asof = build_step1a_approval_registry_dual_read_diff(
        strategy_settings=no_asof_settings,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=manifest_path,
    )
    assert accessor_with_date == accessor_without_asof

    bundle = build_step1a_grounding_compile_bundle(
        strategy_settings=date_settings,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=manifest_path,
    )
    assert accessor_with_date == bundle["artifacts"]["approval_registry_dual_read_diff"]


def test_bundle_dual_read_diff_uses_accessor() -> None:
    import investment_orchestrator.workflow.step1a_grounding_compile as step1a

    src = inspect.getsource(step1a._build)
    # S1A-8: the diff moved behind the shared accessor, so _build has no raw
    # dual-read-diff builder call at all.
    assert "build_step1a_approval_registry_dual_read_diff(" in src
    assert src.count("build_approval_registry_dual_read_diff(") == 0

    accessor_src = inspect.getsource(step1a.build_step1a_approval_registry_dual_read_diff)
    assert accessor_src.count("build_approval_registry_dual_read_diff(") == 1
    # The never-raise compile helper must NOT be used — the accessor composes the
    # already-proven S1A-3 and S1A-6 accessors so failures raise into the guard.
    # The docstring names the helper only to say it is deliberately avoided, so
    # strip that backtick-wrapped mention before asserting on the code.
    code_only = accessor_src.replace(
        "``compile_active_research_anchor_registry_with_approvals``", ""
    )
    assert "compile_active_research_anchor_registry_with_approvals" not in code_only
    assert "build_step1a_active_research_anchor_registry(" in accessor_src
    assert "build_step1a_active_research_anchor_registry_with_approvals(" in accessor_src


def test_dual_read_diff_accessor_failure_falls_back_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_accessor(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("dual-read diff accessor exploded")

    monkeypatch.setattr(
        step1_research, "build_step1a_approval_registry_dual_read_diff", broken_accessor
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    # The dual-read diff fell back; the with-approvals switch is UNAFFECTED.
    assert result["approval_registry_dual_read_diff_writer_source"] == "legacy_fallback"
    assert result["active_research_anchor_registry_with_approvals_writer_source"] == "step1a"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    assert (
        status["switched_artifacts"]["active_research_anchor_registry_with_approvals"]["writer_source"]
        == "step1a"
    )
    diff_entry = status["switched_artifacts"]["approval_registry_dual_read_diff"]
    assert diff_entry["writer_source"] == "legacy_fallback"
    assert diff_entry["fallback_used"] is True
    assert "step1a_accessor_failed" in diff_entry["error_summary"]

    # The legacy diff (byte-identical) was written; shadow stays green.
    inputs = tmp_path / "inputs" / "current"
    artifact_path = step1_research.step1_active_research_anchor_registry_with_approvals_path()
    expected_diff = json.loads(
        json.dumps(
            _legacy_dual_read_diff_compile(
                inputs / "research_anchors.yaml",
                inputs / "research_anchor_approvals.yaml",
                _settings(),
                baseline_registry_path=str(step1_research.step1_active_research_anchor_registry_path()),
                approvals_registry_path=str(artifact_path),
            )
        )
    )
    assert _read(step1_research.step1_approval_registry_dual_read_diff_path()) == expected_diff
    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    assert diff["comparison_status"] == "pass"
    assert diff["comparisons"]["approval_registry_dual_read_diff"]["semantic_match"] is True


def test_dual_read_diff_parity_mismatch_falls_back_to_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    original = step1_research.build_step1a_approval_registry_dual_read_diff

    def mutated_accessor(**kwargs: Any) -> dict[str, Any]:
        payload = original(**kwargs)
        payload["warnings"] = list(payload.get("warnings") or []) + ["mutated"]
        return payload

    monkeypatch.setattr(
        step1_research, "build_step1a_approval_registry_dual_read_diff", mutated_accessor
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert result["approval_registry_dual_read_diff_writer_source"] == "legacy_fallback"
    # With-approvals is unaffected by the diff guard.
    assert result["active_research_anchor_registry_with_approvals_writer_source"] == "step1a"
    status = _read(step1_research.step1a_artifact_switch_status_path())
    diff_entry = status["switched_artifacts"]["approval_registry_dual_read_diff"]
    assert diff_entry["writer_source"] == "legacy_fallback"
    assert diff_entry["fallback_used"] is True
    assert "step1a_dual_read_diff_parity_mismatch" in diff_entry["error_summary"]

    # The guard kept the legacy bytes: the mutated candidate never reached disk.
    on_disk = _read(step1_research.step1_approval_registry_dual_read_diff_path())
    assert "mutated" not in (on_disk.get("warnings") or [])


def test_dual_read_diff_double_failure_preserves_swallowed_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    def broken_legacy_compile(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("legacy overlay derivation exploded")

    # Breaking the legacy baseline compile kills the whole legacy derivation the
    # paired writer depends on; both artifacts follow the pre-switch all-or-
    # nothing presence semantics.
    monkeypatch.setattr(
        step1_research, "compile_active_research_anchor_registry", broken_legacy_compile
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings())

    assert Path(result["research_output_path"]).is_file()
    assert not step1_research.step1_active_research_anchor_registry_with_approvals_path().is_file()
    assert not step1_research.step1_approval_registry_dual_read_diff_path().is_file()
    assert result["active_research_anchor_registry_with_approvals_writer_source"] == "unwritten"
    assert result["approval_registry_dual_read_diff_writer_source"] == "unwritten"

    status = _read(step1_research.step1a_artifact_switch_status_path())
    for key in ("active_research_anchor_registry_with_approvals", "approval_registry_dual_read_diff"):
        entry = status["switched_artifacts"][key]
        assert entry["writer_source"] == "unwritten"
        assert "legacy_derivation_or_write_failed" in entry["error_summary"]
    # The other five switches are unaffected (this monkeypatch replaces only
    # step1_research.compile_active_research_anchor_registry, which the evidence_packet
    # writer does not use — it compiles via the evidence_packet module).
    assert status["switched_artifacts"]["active_research_anchor_registry"]["writer_source"] == "step1a"
    assert (
        status["switched_artifacts"]["approval_registry_switch_readiness"]["writer_source"] == "step1a"
    )
    assert status["switched_artifacts"]["evidence_packet"]["writer_source"] == "step1a"

    diff = _read(step1_research.step1a_grounding_compile_shadow_diff_path())
    for key in ("active_research_anchor_registry_with_approvals", "approval_registry_dual_read_diff"):
        comparison = diff["comparisons"][key]
        assert comparison["comparison_skipped"] is True
        assert comparison["skip_reason"] == "current_step1_artifact_unavailable_or_malformed"
    assert diff["comparison_status"] in ("mismatch", "pass_with_skips")
    assert diff["comparison_complete"] is False
    assert diff["parity_passed"] is False

    decision = _read(Path(result["research_degraded_mode_decision_path"]))
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
    """Readers and sibling artifacts of ALL switched writers are invariant.

    Both variants run in the SAME repo root (wiped between runs) so embedded
    absolute paths and their hashes are comparable exactly; only timestamps are
    stripped. Forcing every accessor (including the S1A-6 with-approvals
    accessor) onto the legacy fallback must change nothing: the switched
    artifacts themselves, their readers (equivalence, candidates, observatory),
    and the untouched sibling artifact (dual-read diff) stay identical.
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
                mp.setattr(
                    step1_research,
                    "build_step1a_research_anchor_revocations_validation",
                    broken_accessor,
                )
                mp.setattr(
                    step1_research,
                    "build_step1a_active_research_anchor_registry_with_approvals",
                    broken_accessor,
                )
                mp.setattr(
                    step1_research,
                    "build_step1a_approval_registry_switch_readiness",
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
                "readiness": _read(step1_research.step1_approval_registry_switch_readiness_path()),
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
        "readiness",
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
        assert "build_step1a_research_anchor_revocations_validation" not in source
        # S1A-6: covered by the prefix assert above, but kept explicit — no
        # downstream module may reference the with-approvals accessor either.
        assert "build_step1a_active_research_anchor_registry_with_approvals" not in source
        # S1A-7: NOT covered by any prefix above — must be asserted explicitly.
        # The readiness DISK artifact and its accessor are invisible to every
        # runtime module; runtime readiness recomputes in memory.
        assert "build_step1a_approval_registry_switch_readiness" not in source
        assert "step1_approval_registry_switch_readiness_path" not in source
        # S1A-8: the dual-read diff accessor is likewise invisible to every
        # downstream module; the disk diff is consumed only by the shadow.
        assert "build_step1a_approval_registry_dual_read_diff" not in source

    _setup_repo(tmp_path, monkeypatch)
    step1_research.parse_step1_output(strategy_settings=_settings())

    # The embedded selection artifact remains production-sourced (S1A-2), not
    # Step 1A output — the switch touched only the baseline registry writer.
    selection = _read(step1_research.step1_embedded_active_registry_selection_path())
    assert selection["production_source"] is True
    assert selection["step1a_output"] is False
    packet = _read(step1_research.step1_evidence_packet_path())
    assert selection["selected_registry"] == packet["active_anchor_registry"]
