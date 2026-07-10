"""S1A-12 embedded-selection full-payload parity comparator + guard tests.

``compare_embedded_selection_parity`` compares the CANONICAL selection captures
(the raw ``build_embedded_active_anchor_registry_selection`` payloads) before
the disk writer adds wrapper/provenance fields. ONE comparison tier: every
canonical payload difference blocks the S1A-12 switch — there is no
report-only/non-blocking category. Only ``generated_at`` is normalized; the
guard additionally pins the normalized paths to the approved six-path allowlist
and fails closed on any unknown ISO-datetime anywhere in either payload.
Nothing here grants permissions, gates, allowed_actions, or any order-path
authority.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from investment_orchestrator.research.evidence_packet import (
    build_embedded_active_anchor_registry_selection,
    build_evidence_packet_and_selection,
    compare_embedded_selection_parity,
)
from investment_orchestrator.workflow.step1_research import (
    _APPROVED_EMBEDDED_SELECTION_NORMALIZED_PATHS,
    _evaluate_step1a_embedded_selection_guard,
)

from test_step1a_shadow_run import _anchor, _approval, _write_json


_GEN_AT = "2026-06-28T12:00:00+00:00"
_OTHER_GEN_AT = "2026-06-28T18:30:00+00:00"
_UNIVERSE = ["QQQ", "VOO", "VTI", "VT", "SMH", "IGV"]


def _write_inputs(root: Path) -> tuple[Path, Path]:
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


def _selection(root: Path, generated_at: str = _GEN_AT) -> dict[str, Any]:
    """One canonical selection capture (approvals-inclusive, one active anchor)."""
    anchors_path, approvals_path = _write_inputs(root)
    selection = build_embedded_active_anchor_registry_selection(
        anchors_path=anchors_path,
        approvals_path=approvals_path,
        allowed_universe=list(_UNIVERSE),
        today="2026-06-28",
        generated_at=generated_at,
    )
    # The success branch populates every canonical sub-object.
    assert selection["selected_source"] == "approvals_inclusive"
    assert selection["readiness"]["ready"] is True
    return selection


def _copy(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload))


# --- clean pass ----------------------------------------------------------------


def test_identical_payloads_pass(tmp_path: Path) -> None:
    a = _selection(tmp_path / "a")
    b = _selection(tmp_path / "b", generated_at=_GEN_AT)
    # Same inputs + same stamp -> byte-identical canonical payloads (the paths
    # embedded in source_manifest come from each root, so compare one root's
    # payload against its own independent rebuild instead).
    b = _copy(a)

    parity = compare_embedded_selection_parity(a, b)
    assert parity["schema_version"] == "embedded_selection_parity_v1"
    assert parity["payload_match"] is True
    assert parity["differences"] == []
    assert parity["unknown_runtime_timestamp_fields"] == []
    # All six approved generated_at sites exist on the success branch.
    assert set(parity["normalized_paths"]) == set(_APPROVED_EMBEDDED_SELECTION_NORMALIZED_PATHS)
    assert parity["normalization_allowlist"] == ["generated_at"]

    guard = _evaluate_step1a_embedded_selection_guard(parity)
    assert guard == {"ok": True, "error_summary": ""}

    # Report-only / non-authority envelope on the comparator result.
    assert parity["report_only"] is True
    assert parity["is_llm_generated"] is False
    assert parity["permission_effect"] == "none"
    assert parity["not_authorization"] is True
    assert parity["not_order_input"] is True
    assert parity["consumed_by_gates"] is False
    assert parity["consumed_by_order_path"] is False
    assert parity["safe_to_ignore"] is True


def test_generated_at_only_difference_normalizes_and_passes(tmp_path: Path) -> None:
    a = _selection(tmp_path)
    b = _copy(a)
    # A fresh stamp at every approved site (the builder threads one stamp into
    # all six) must be absorbed by normalization.
    b["generated_at"] = _OTHER_GEN_AT
    for key in ("selected_registry", "baseline_registry", "approvals_registry", "dual_read_diff", "readiness"):
        b[key]["generated_at"] = _OTHER_GEN_AT

    parity = compare_embedded_selection_parity(a, b)
    assert parity["payload_match"] is True
    assert set(parity["normalized_paths"]) == set(_APPROVED_EMBEDDED_SELECTION_NORMALIZED_PATHS)
    assert _evaluate_step1a_embedded_selection_guard(parity)["ok"] is True


@pytest.mark.parametrize(
    "path",
    sorted(_APPROVED_EMBEDDED_SELECTION_NORMALIZED_PATHS),
)
def test_each_approved_generated_at_path_normalizes_individually(
    tmp_path: Path, path: str
) -> None:
    a = _selection(tmp_path)
    b = _copy(a)
    node: dict[str, Any] = b
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = _OTHER_GEN_AT

    parity = compare_embedded_selection_parity(a, b)
    assert parity["payload_match"] is True
    assert path in parity["normalized_paths"]
    assert _evaluate_step1a_embedded_selection_guard(parity)["ok"] is True


# --- every canonical field class blocks ----------------------------------------


def _mutators() -> dict[str, Callable[[dict[str, Any]], None]]:
    def selected_source(s: dict[str, Any]) -> None:
        s["selected_source"] = "baseline_fallback"

    def selected_registry_anchor_content(s: dict[str, Any]) -> None:
        anchors = s["selected_registry"]["active_anchors"]
        anchors[0] = {**anchors[0], "summary": "mutated content, same count"}

    def selected_registry_validity(s: dict[str, Any]) -> None:
        s["selected_registry"]["registry_valid"] = False

    def selected_registry_blockers(s: dict[str, Any]) -> None:
        s["selected_registry"]["registry_blockers"] = ["injected_blocker"]

    def baseline_registry_counts(s: dict[str, Any]) -> None:
        s["baseline_registry"]["counts"] = {**s["baseline_registry"]["counts"], "active": 99}

    def approvals_registry_revocations(s: dict[str, Any]) -> None:
        s["approvals_registry"]["revocations_applied"] = [{"revocation_id": "REV-X"}]

    def dual_read_diff_hash(s: dict[str, Any]) -> None:
        s["dual_read_diff"]["approvals_registry_sha256"] = "f" * 64

    def readiness_ready(s: dict[str, Any]) -> None:
        s["readiness"]["ready"] = False

    def readiness_switch_target(s: dict[str, Any]) -> None:
        s["readiness"]["switch_target"] = "baseline_fallback"

    def readiness_baseline_fallback_safe(s: dict[str, Any]) -> None:
        s["readiness"]["baseline_fallback_safe"] = False

    def readiness_fail_closed_flag(s: dict[str, Any]) -> None:
        s["readiness"]["fail_closed_empty_required"] = True

    def readiness_condition_detail(s: dict[str, Any]) -> None:
        conditions = s["readiness"]["conditions"]
        conditions[0] = {**conditions[0], "passed": False}

    def readiness_failed_conditions(s: dict[str, Any]) -> None:
        s["readiness"]["failed_conditions"] = ["baseline_registry_present"]

    def readiness_source_hashes(s: dict[str, Any]) -> None:
        hashes = json.loads(json.dumps(s["readiness"]["source_hashes"]))
        hashes["research_anchors_yaml"]["baseline_source_manifest"] = "0" * 64
        s["readiness"]["source_hashes"] = hashes

    def schema_version(s: dict[str, Any]) -> None:
        s["schema_version"] = "embedded_active_anchor_registry_selection_v2"

    def safety_marker(s: dict[str, Any]) -> None:
        s["not_authorization"] = False

    return {
        "selected_source": selected_source,
        "selected_registry_anchor_content": selected_registry_anchor_content,
        "selected_registry_validity": selected_registry_validity,
        "selected_registry_blockers": selected_registry_blockers,
        "baseline_registry_counts": baseline_registry_counts,
        "approvals_registry_revocations": approvals_registry_revocations,
        "dual_read_diff_hash": dual_read_diff_hash,
        "readiness_ready": readiness_ready,
        "readiness_switch_target": readiness_switch_target,
        "readiness_baseline_fallback_safe": readiness_baseline_fallback_safe,
        "readiness_fail_closed_flag": readiness_fail_closed_flag,
        "readiness_condition_detail": readiness_condition_detail,
        "readiness_failed_conditions": readiness_failed_conditions,
        "readiness_source_hashes": readiness_source_hashes,
        "schema_version": schema_version,
        "safety_marker": safety_marker,
    }


@pytest.mark.parametrize("field_class", sorted(_mutators()))
def test_every_canonical_field_class_mismatch_blocks(tmp_path: Path, field_class: str) -> None:
    a = _selection(tmp_path)
    b = _copy(a)
    _mutators()[field_class](b)

    parity = compare_embedded_selection_parity(a, b)
    assert parity["payload_match"] is False
    assert parity["differences"], field_class

    guard = _evaluate_step1a_embedded_selection_guard(parity)
    assert guard["ok"] is False
    assert "step1a_embedded_selection_parity_mismatch" in guard["error_summary"]
    # Compact diagnostics carry dotted paths / tokens only — never anchor content.
    assert "mutated content" not in guard["error_summary"]
    assert "Operator-dated thesis" not in guard["error_summary"]


# --- fail-closed timestamp handling ---------------------------------------------


def test_unknown_iso_timestamp_fails_closed_even_when_equal(tmp_path: Path) -> None:
    a = _selection(tmp_path)
    b = _copy(a)
    # Injected identically on BOTH sides: no difference exists, yet an unknown
    # run-varying timestamp anywhere in the canonical payload must fail closed.
    a["readiness"]["evaluated_at"] = "2026-01-02T03:04:05+00:00"
    b["readiness"]["evaluated_at"] = "2026-01-02T03:04:05+00:00"

    parity = compare_embedded_selection_parity(a, b)
    assert parity["differences"] == []
    assert parity["unknown_runtime_timestamp_fields"] == ["readiness.evaluated_at"]
    assert parity["payload_match"] is False

    guard = _evaluate_step1a_embedded_selection_guard(parity)
    assert guard["ok"] is False
    assert "step1a_embedded_selection_unknown_runtime_timestamp" in guard["error_summary"]
    assert "readiness.evaluated_at" in guard["error_summary"]


def test_one_sided_unknown_timestamp_reports_unknown_token_first(tmp_path: Path) -> None:
    a = _selection(tmp_path)
    b = _copy(a)
    b["selected_registry"]["stamped_at"] = "2026-01-02T03:04:05+00:00"

    parity = compare_embedded_selection_parity(a, b)
    assert parity["payload_match"] is False
    assert "selected_registry.stamped_at" in parity["unknown_runtime_timestamp_fields"]

    guard = _evaluate_step1a_embedded_selection_guard(parity)
    assert guard["ok"] is False
    # Unknown timestamp outranks the accompanying structural difference.
    assert "step1a_embedded_selection_unknown_runtime_timestamp" in guard["error_summary"]


def test_bare_domain_dates_are_not_flagged_or_normalized(tmp_path: Path) -> None:
    a = _selection(tmp_path)
    b = _copy(a)
    # Natural bare dates (as_of_date, anchor_date_et, valid_from/valid_until)
    # are already present; add one more equal date field on both sides.
    a["review_date"] = "2026-07-01"
    b["review_date"] = "2026-07-01"

    parity = compare_embedded_selection_parity(a, b)
    assert parity["payload_match"] is True
    assert parity["unknown_runtime_timestamp_fields"] == []
    assert "review_date" not in parity["normalized_paths"]
    assert _evaluate_step1a_embedded_selection_guard(parity)["ok"] is True


def test_unexpected_normalized_path_fails_guard(tmp_path: Path) -> None:
    a = _selection(tmp_path)
    b = _copy(a)
    # Equal on both sides so the payloads match — but a generated_at appearing at
    # a NON-approved site means the payload shape changed: guard fails closed.
    a["nested_extra"] = {"generated_at": _GEN_AT}
    b["nested_extra"] = {"generated_at": _GEN_AT}

    parity = compare_embedded_selection_parity(a, b)
    assert parity["payload_match"] is True
    assert "nested_extra.generated_at" in parity["normalized_paths"]

    guard = _evaluate_step1a_embedded_selection_guard(parity)
    assert guard["ok"] is False
    assert "step1a_embedded_selection_unexpected_normalized_path" in guard["error_summary"]
    assert "nested_extra.generated_at" in guard["error_summary"]


# --- structural strictness -------------------------------------------------------


def test_absent_vs_null_stays_distinct(tmp_path: Path) -> None:
    a = _selection(tmp_path)
    b = _copy(a)
    a["optional_note"] = None  # present-with-null vs absent must block

    parity = compare_embedded_selection_parity(a, b)
    assert parity["payload_match"] is False
    assert any(
        d.get("path") == "optional_note" and d.get("reason") == "absent_in_step1a"
        for d in parity["differences"]
    )
    assert _evaluate_step1a_embedded_selection_guard(parity)["ok"] is False


def test_list_ordering_and_length_are_significant(tmp_path: Path) -> None:
    a = _selection(tmp_path)

    reordered = _copy(a)
    reordered["readiness"]["conditions"] = list(reversed(reordered["readiness"]["conditions"]))
    parity = compare_embedded_selection_parity(a, reordered)
    assert parity["payload_match"] is False

    shortened = _copy(a)
    shortened["readiness"]["conditions"] = shortened["readiness"]["conditions"][:-1]
    parity = compare_embedded_selection_parity(a, shortened)
    assert parity["payload_match"] is False
    assert any(
        "list_length_differs" in str(d.get("reason", ""))
        for d in parity["differences"]
    )


def test_unavailable_or_empty_inputs_fail_closed(tmp_path: Path) -> None:
    selection = _selection(tmp_path)

    parity = compare_embedded_selection_parity(None, selection)
    assert parity["payload_match"] is False
    assert any(
        d.get("reason") == "production_selection_unavailable" for d in parity["differences"]
    )

    parity = compare_embedded_selection_parity(selection, {})
    assert parity["payload_match"] is False
    assert any(d.get("reason") == "step1a_selection_unavailable" for d in parity["differences"])

    parity = compare_embedded_selection_parity({}, {})
    assert parity["payload_match"] is False
    reasons = {d.get("reason") for d in parity["differences"]}
    assert "production_selection_unavailable" in reasons
    assert "step1a_selection_unavailable" in reasons


def test_inputs_are_never_mutated(tmp_path: Path) -> None:
    a = _selection(tmp_path)
    b = _copy(a)
    b["generated_at"] = _OTHER_GEN_AT
    snapshot_a = json.dumps(a, sort_keys=True)
    snapshot_b = json.dumps(b, sort_keys=True)

    compare_embedded_selection_parity(a, b)

    assert json.dumps(a, sort_keys=True) == snapshot_a
    assert json.dumps(b, sort_keys=True) == snapshot_b


def test_guard_fails_closed_on_non_mapping_parity_result() -> None:
    guard = _evaluate_step1a_embedded_selection_guard(None)  # type: ignore[arg-type]
    assert guard["ok"] is False
    assert "step1a_embedded_selection_parity_result_unavailable" in guard["error_summary"]


# --- provenance exclusion contract ----------------------------------------------


def test_canonical_captures_exclude_wrapper_provenance(tmp_path: Path) -> None:
    """The compared captures are pre-wrapper: provenance is excluded by construction.

    Both lineages' ``embedded_selection_out`` captures carry ONLY the canonical
    builder payload — no ``production_source`` / ``step1a_output`` / consumed_by_*
    wrapper fields (the writer stamps those after the guard decision). Comparing
    a capture against a wrapper-annotated artifact would fail, proving the
    comparison must happen before wrapping.
    """
    anchors_path, approvals_path = _write_inputs(tmp_path)
    capture: dict[str, Any] = {}
    build_evidence_packet_and_selection(
        strategy_settings={
            "as_of": "2026-06-28",
            "core_universe": ["QQQ", "VOO", "VTI", "VT"],
            "satellite_universe": ["SMH", "IGV"],
        },
        portfolio_snapshot_text=None,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=approvals_path,
        generated_at=_GEN_AT,
        embedded_selection_out=capture,
    )

    for wrapper_field in (
        "production_source",
        "step1a_output",
        "consumed_by_gates",
        "consumed_by_order_path",
        "consumed_by_downstream",
        "safe_to_ignore",
    ):
        assert wrapper_field not in capture

    wrapped = _copy(capture)
    wrapped.update({"production_source": True, "step1a_output": False})
    parity = compare_embedded_selection_parity(capture, wrapped)
    assert parity["payload_match"] is False
    paths = {d.get("path") for d in parity["differences"]}
    assert "production_source" in paths
    assert "step1a_output" in paths
