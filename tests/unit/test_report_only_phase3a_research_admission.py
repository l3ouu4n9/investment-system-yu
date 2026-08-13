"""Focused tests for the Phase-3A.1 report-only research-admission observer.

This observer proves NOTHING about positive research admission. It maps the
committed refusal-only reader's closed three-state result
(``UNAVAILABLE`` / ``INVALID`` / ``REFUSAL_ONLY``) onto its own closed
three-state result (``UNAVAILABLE`` / ``INVALID`` / ``MANUAL_REVIEW``).
``MANUAL_REVIEW`` here means only "no authenticated current positive research
admission is available, therefore Phase 3 fails closed" — never a successful
admission.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

import investment_orchestrator.observability.report_only_phase3a_research_admission as phase3a
import investment_orchestrator.state.research_availability as research_availability
from investment_orchestrator.observability.report_only_phase3a_research_admission import (
    Phase3AResearchAdmissionObservationResult,
    Phase3AResearchAdmissionObservationStatus,
    observe_current_report_only_phase3a_research_admission,
)


_VALID_H1_BLOCK: dict[str, Any] = {
    "h1_mapped_selected": True,
    "h1_mapped_source_kind": "H1_ROLE_MAPPED",
    "h1_mapped_freshness": "fresh",
    "h1_mapped_age_days": 1,
    "h1_mapped_identity": {"mapping_report_identity_sha256": "a" * 64},
    "h1_mapped_current_source_identities": {"policy_projection_identity_sha256": "b" * 64},
    "h1_mapped_temporal_evidence": {"policy_as_of_date": "2026-06-25"},
}


def _write_artifact(root: Path, payload: dict[str, Any] | bytes) -> Path:
    directory = root / "artifacts" / "current" / "step1_research"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "research_availability.json"
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _observe_with_root(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> Phase3AResearchAdmissionObservationResult:
    monkeypatch.setattr(research_availability, "repo_root", lambda: root)
    return observe_current_report_only_phase3a_research_admission()


# --- A. upstream REFUSAL_ONLY --------------------------------------------------


def test_refusal_only_maps_to_manual_review_not_proven(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_artifact(
        tmp_path, {"state": "MANUAL_REVIEW_REQUIRED", "source": "raw_research_handoff"}
    )

    result = _observe_with_root(monkeypatch, tmp_path)

    assert result.status is Phase3AResearchAdmissionObservationStatus.MANUAL_REVIEW
    assert result.reason_codes == ("PHASE3_RESEARCH_ADMISSION_NOT_PROVEN",)
    assert result.authority_effect == "NONE"
    assert result.report_only is True
    assert result.not_authorization is True


# --- B. upstream INVALID -------------------------------------------------------


def test_invalid_artifact_preserves_upstream_reason_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_artifact(tmp_path, b"{not valid json")

    result = _observe_with_root(monkeypatch, tmp_path)

    assert result.status is Phase3AResearchAdmissionObservationStatus.INVALID
    assert result.reason_codes == ("RESEARCH_SELECTION_ARTIFACT_MALFORMED",)
    assert result.authority_effect == "NONE"


# --- C. upstream UNAVAILABLE (missing artifact) --------------------------------


def test_missing_artifact_preserves_upstream_reason_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _observe_with_root(monkeypatch, tmp_path)

    assert result.status is Phase3AResearchAdmissionObservationStatus.UNAVAILABLE
    assert result.reason_codes == ("RESEARCH_SELECTION_ARTIFACT_UNAVAILABLE",)


# --- D. structurally valid positive artifact stays UNAVAILABLE ----------------


def test_positive_currentness_unavailable_never_becomes_admission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = {
        "state": "H1_MAPPED_FRESH_NON_ACTIONABLE",
        "source": "H1_ROLE_MAPPED",
        **_VALID_H1_BLOCK,
    }
    _write_artifact(tmp_path, payload)

    result = _observe_with_root(monkeypatch, tmp_path)

    assert result.status is Phase3AResearchAdmissionObservationStatus.UNAVAILABLE
    assert result.reason_codes == (
        "RESEARCH_SELECTION_POSITIVE_CURRENTNESS_UNAVAILABLE",
    )
    assert result.status is not Phase3AResearchAdmissionObservationStatus.MANUAL_REVIEW
    for member in Phase3AResearchAdmissionObservationStatus:
        assert member.value not in (
            "VALID",
            "ADMITTED",
            "SELECTED",
            "POSITIVE",
        )


# --- E. persisted_state / persisted_source are not authority-bearing ----------


def test_observer_result_is_identical_across_different_persisted_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_artifact(
        tmp_path, {"state": "MANUAL_REVIEW_REQUIRED", "source": "raw_research_handoff"}
    )
    first = _observe_with_root(monkeypatch, tmp_path)

    other_root = tmp_path / "other"
    other_root.mkdir()
    _write_artifact(
        other_root, {"state": "STRICT_STALE", "source": "compiled_research_handoff"}
    )
    second = _observe_with_root(monkeypatch, other_root)

    assert first.status is second.status
    assert first.reason_codes == second.reason_codes
    assert first.authority_effect == second.authority_effect


# --- F. result surface carries no forbidden field ------------------------------


def test_result_surface_carries_no_forbidden_field() -> None:
    field_names = {field.name for field in dataclasses.fields(Phase3AResearchAdmissionObservationResult)}

    assert field_names == {
        "schema_version",
        "status",
        "reason_codes",
        "authority_effect",
        "report_only",
        "not_authorization",
        "artifact_locator",
        "artifact_observed_sha256",
        "artifact_observed_size_bytes",
    }
    assert not field_names & {
        "persisted_state",
        "persisted_source",
        "allowed_actions",
        "permission",
        "ticker",
        "disposition",
        "priority",
        "quantity",
        "order",
        "candidate",
        "evidence_status",
        "research_selected",
        "research_admitted",
        "h1_selected",
        "current_research_source",
        "selected_source",
        "admission_valid",
    }


def test_public_observer_accepts_no_arguments() -> None:
    parameters = inspect.signature(
        observe_current_report_only_phase3a_research_admission
    ).parameters
    assert parameters == {}


# --- G. observer calls only the public refusal reader --------------------------


def test_module_does_not_open_availability_artifact_directly() -> None:
    module_text = Path(phase3a.__file__).read_text(encoding="utf-8")
    assert "research_availability.json" not in module_text
    assert "open(" not in module_text
    assert "os.open" not in module_text


# --- H. no availability recomputation ------------------------------------------


def test_module_does_not_import_availability_classification_helpers() -> None:
    module_text = Path(phase3a.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "evaluate_research_availability",
        "refresh_research_availability_for_h1_replacement",
        "_classify_current_valid",
        "_classify_fallback",
        "_evaluate_pending_gates_promotion",
        "_evaluate_h1_mapped_recognition",
    ):
        assert forbidden not in module_text


def test_reader_never_recomputes_availability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("availability must not be recomputed")

    for attribute in (
        "evaluate_research_availability",
        "_classify_current_valid",
        "_classify_fallback",
        "_evaluate_pending_gates_promotion",
        "_evaluate_h1_mapped_recognition",
    ):
        monkeypatch.setattr(research_availability, attribute, _boom)

    _write_artifact(
        tmp_path, {"state": "MANUAL_REVIEW_REQUIRED", "source": "raw_research_handoff"}
    )
    result = _observe_with_root(monkeypatch, tmp_path)

    assert result.status is Phase3AResearchAdmissionObservationStatus.MANUAL_REVIEW


# --- I. no Phase-1/2 sizing imports ---------------------------------------------


def test_module_does_not_import_phase1_or_phase2_sizing() -> None:
    module_text = Path(phase3a.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "report_only_budget_capacity",
        "report_only_holdings_exposure",
        "report_only_increment_capacity",
        "budget_ceiling",
        "total_holdings_exposure",
        "increment_cap_basis",
    ):
        assert forbidden not in module_text


# --- J. no production downstream consumer ---------------------------------------


def test_module_has_no_production_consumer() -> None:
    root = Path(__file__).resolve().parents[2] / "src/investment_orchestrator"
    module_file = root / "observability/report_only_phase3a_research_admission.py"
    assert all(
        "report_only_phase3a_research_admission" not in candidate.read_text(
            encoding="utf-8"
        )
        for candidate in root.rglob("*.py")
        if candidate != module_file
    )


# --- K. genuine current observation ---------------------------------------------


def test_exact_current_observation_is_manual_review_not_proven() -> None:
    """No monkeypatch: exercises the real committed artifact end-to-end."""
    result = observe_current_report_only_phase3a_research_admission()

    assert result.status is Phase3AResearchAdmissionObservationStatus.MANUAL_REVIEW
    assert result.reason_codes == ("PHASE3_RESEARCH_ADMISSION_NOT_PROVEN",)
    assert result.authority_effect == "NONE"
    assert result.report_only is True
    assert result.not_authorization is True
    assert result.artifact_locator == (
        "artifacts/current/step1_research/research_availability.json"
    )
    assert result.artifact_observed_sha256 is not None
    assert result.artifact_observed_size_bytes is not None
