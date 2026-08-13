"""Focused tests for the persisted research-selection refusal-only reader.

This reader proves NOTHING about currentness. It answers exactly one
question: does the persisted ``research_availability.json`` artifact offer a
usable, authenticated positive mapped-H1 admission? It never returns one — a
structurally valid positive claim is reported UNAVAILABLE, not surfaced.
Only a structurally valid artifact recording NO mapped-H1 selection is usable
refusal evidence (``REFUSAL_ONLY``).

Scope: this suite targets the reader itself. Writer output correctness (the
h1_mapped_* key set the reader relies on) is pinned separately in
``test_h1_mapped_availability.py``.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

import pytest

import investment_orchestrator.state.research_availability as research_availability
from investment_orchestrator.state.research_availability import (
    ResearchSelectionRefusalReadResult,
    ResearchSelectionRefusalReadStatus,
    read_persisted_research_selection_refusal_only,
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


def _read_with_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> ResearchSelectionRefusalReadResult:
    monkeypatch.setattr(research_availability, "repo_root", lambda: root)
    return read_persisted_research_selection_refusal_only()


def _minimal_valid_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": "MANUAL_REVIEW_REQUIRED",
        "source": "raw_research_handoff",
    }
    base.update(overrides)
    return base


# --- A. exact current repository artifact -------------------------------------


def test_exact_current_artifact_is_refusal_only() -> None:
    """No monkeypatch: exercises the real committed artifact end-to-end."""
    result = read_persisted_research_selection_refusal_only()

    assert result.status is ResearchSelectionRefusalReadStatus.REFUSAL_ONLY
    assert result.reason_codes == ()
    assert result.authority_effect == "NONE"
    assert result.report_only is True
    assert result.not_authorization is True
    assert result.persisted_state == "MANUAL_REVIEW_REQUIRED"
    assert result.persisted_source == "raw_research_handoff"
    assert result.artifact_locator == (
        "artifacts/current/step1_research/research_availability.json"
    )
    assert result.artifact_observed_sha256 is not None
    assert result.artifact_observed_size_bytes is not None


# --- B. missing artifact -------------------------------------------------------


def test_missing_artifact_is_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.UNAVAILABLE
    assert result.reason_codes == ("RESEARCH_SELECTION_ARTIFACT_UNAVAILABLE",)
    assert result.persisted_state is None
    assert result.persisted_source is None
    assert result.artifact_observed_sha256 is None


def test_missing_parent_directory_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # tmp_path itself exists but nothing under it does.
    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.UNAVAILABLE


# --- C. malformed / duplicate-key artifact -------------------------------------


def test_malformed_json_is_invalid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_artifact(tmp_path, b"{not valid json")

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.INVALID
    assert result.reason_codes == ("RESEARCH_SELECTION_ARTIFACT_MALFORMED",)


def test_duplicate_json_key_is_invalid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_artifact(
        tmp_path,
        b'{"state": "MANUAL_REVIEW_REQUIRED", "source": "raw_research_handoff", '
        b'"state": "STRICT_FRESH"}',
    )

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.INVALID
    assert result.reason_codes == ("RESEARCH_SELECTION_ARTIFACT_MALFORMED",)


def test_non_dict_top_level_is_invalid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_artifact(tmp_path, b"[]")

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.INVALID


def test_invalid_state_enum_is_invalid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_artifact(tmp_path, _minimal_valid_payload(state="NOT_A_REAL_STATE"))

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.INVALID
    assert result.reason_codes == ("RESEARCH_SELECTION_ARTIFACT_STATE_INVALID",)


def test_invalid_source_enum_is_invalid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_artifact(tmp_path, _minimal_valid_payload(source="not_a_real_source"))

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.INVALID
    assert result.reason_codes == ("RESEARCH_SELECTION_ARTIFACT_SOURCE_INVALID",)


# --- D. internally contradictory mapped-H1 shape -------------------------------


def test_partial_h1_block_is_invalid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _minimal_valid_payload(
        state="H1_MAPPED_FRESH_NON_ACTIONABLE",
        source="H1_ROLE_MAPPED",
        h1_mapped_selected=True,
        h1_mapped_source_kind="H1_ROLE_MAPPED",
        # Missing the remaining required block keys.
    )
    _write_artifact(tmp_path, payload)

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.INVALID
    assert result.reason_codes == ("RESEARCH_SELECTION_ARTIFACT_H1_BLOCK_MALFORMED",)
    assert result.persisted_state is None
    assert result.persisted_source is None


def test_h1_block_present_but_selected_false_is_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _minimal_valid_payload(
        state="MANUAL_REVIEW_REQUIRED",
        source="raw_research_handoff",
        h1_mapped_selected=False,
        **{k: v for k, v in _VALID_H1_BLOCK.items() if k != "h1_mapped_selected"},
    )
    _write_artifact(tmp_path, payload)

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.INVALID
    assert result.reason_codes == ("RESEARCH_SELECTION_ARTIFACT_H1_BLOCK_MALFORMED",)


def test_source_claims_h1_without_block_is_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _minimal_valid_payload(
        state="MANUAL_REVIEW_REQUIRED", source="H1_ROLE_MAPPED"
    )
    _write_artifact(tmp_path, payload)

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.INVALID
    assert result.reason_codes == (
        "RESEARCH_SELECTION_ARTIFACT_SOURCE_STATE_INCONSISTENT",
    )


def test_state_claims_h1_without_block_is_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _minimal_valid_payload(
        state="H1_MAPPED_FRESH_NON_ACTIONABLE", source="raw_research_handoff"
    )
    _write_artifact(tmp_path, payload)

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.INVALID
    assert result.reason_codes == (
        "RESEARCH_SELECTION_ARTIFACT_SOURCE_STATE_INCONSISTENT",
    )


def test_complete_h1_block_with_mismatched_state_is_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _minimal_valid_payload(
        state="MANUAL_REVIEW_REQUIRED",  # should be H1_MAPPED_FRESH_NON_ACTIONABLE
        source="H1_ROLE_MAPPED",
        **_VALID_H1_BLOCK,
    )
    _write_artifact(tmp_path, payload)

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.INVALID
    assert result.reason_codes == (
        "RESEARCH_SELECTION_ARTIFACT_SOURCE_STATE_INCONSISTENT",
    )


# --- E. structurally valid positive mapped-H1 artifact -------------------------


def test_structurally_valid_positive_artifact_is_never_admitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _minimal_valid_payload(
        state="H1_MAPPED_FRESH_NON_ACTIONABLE",
        source="H1_ROLE_MAPPED",
        **_VALID_H1_BLOCK,
    )
    _write_artifact(tmp_path, payload)

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.UNAVAILABLE
    assert result.reason_codes == (
        "RESEARCH_SELECTION_POSITIVE_CURRENTNESS_UNAVAILABLE",
    )
    # Never a positive-admission result: no fact from the claim leaks through.
    assert result.persisted_state is None
    assert result.persisted_source is None
    assert result.status is not ResearchSelectionRefusalReadStatus.REFUSAL_ONLY
    assert result.status is not ResearchSelectionRefusalReadStatus.INVALID


# --- representative nonregular / unstable-read coverage ------------------------
# One case each, per existing stable-read convention; not a full matrix.


def test_symlink_leaf_is_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    directory = tmp_path / "artifacts" / "current" / "step1_research"
    directory.mkdir(parents=True)
    target = directory / "target.json"
    target.write_text(json.dumps(_minimal_valid_payload()), encoding="utf-8")
    os.symlink("target.json", directory / "research_availability.json")

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.UNAVAILABLE


# --- G. reader never invokes availability computation/refresh ------------------


def test_reader_never_calls_availability_evaluation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_artifact(tmp_path, _minimal_valid_payload())

    def _boom(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("reader must not recompute availability")

    for attribute in (
        "evaluate_research_availability",
        "_classify_current_valid",
        "_classify_fallback",
        "_evaluate_pending_gates_promotion",
        "_evaluate_h1_mapped_recognition",
    ):
        monkeypatch.setattr(research_availability, attribute, _boom)

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.REFUSAL_ONLY


# --- H. reader performs zero writes --------------------------------------------


def test_reader_performs_zero_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_artifact(tmp_path, _minimal_valid_payload())

    def _boom(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("reader must perform zero writes")

    monkeypatch.setattr(Path, "write_text", _boom)
    monkeypatch.setattr(Path, "write_bytes", _boom)
    monkeypatch.setattr(os, "replace", _boom)
    monkeypatch.setattr(os, "rename", _boom)

    result = _read_with_root(monkeypatch, tmp_path)

    assert result.status is ResearchSelectionRefusalReadStatus.REFUSAL_ONLY


# --- I. result field inventory excludes permission/sizing/disposition ---------


def test_result_surface_carries_no_forbidden_field() -> None:
    field_names = {f.name for f in dataclasses.fields(ResearchSelectionRefusalReadResult)}

    forbidden_substrings = (
        "allowed_action",
        "blocked_action",
        "manual_review_required",
        "permission",
        "disposition",
        "ticker",
        "evidence_status",
        "quantity",
        "order",
        "priority",
    )
    for name in field_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name, (name, forbidden)

    # And no sizing symbols anywhere on the type.
    for forbidden in ("x_", "_h_", "increment_cap", "budget"):
        for name in field_names:
            assert forbidden not in name.lower(), (name, forbidden)


# --- J. no Phase-1/Phase-2 sizing imports --------------------------------------


def test_module_does_not_import_sizing_observers() -> None:
    import sys

    module = sys.modules["investment_orchestrator.state.research_availability"]
    source = Path(module.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "report_only_budget_capacity",
        "report_only_holdings_exposure",
        "report_only_increment_capacity",
    ):
        assert forbidden not in source, forbidden


# --- K. fixed path / no caller override ----------------------------------------


def test_reader_accepts_no_arguments() -> None:
    import inspect

    signature = inspect.signature(read_persisted_research_selection_refusal_only)
    assert list(signature.parameters) == []


def test_artifact_locator_is_the_fixed_committed_path() -> None:
    result = read_persisted_research_selection_refusal_only()

    assert result.artifact_locator == (
        "artifacts/current/step1_research/research_availability.json"
    )
