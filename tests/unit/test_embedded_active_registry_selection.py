"""R2G-5c-2 embedded active-registry selection tests.

The selected registry is the support_signals grounding authority, but still only
for report-only support signals. These tests keep permissions, gates, and order
paths out of scope and prove the switch is readiness-coupled to fresh in-memory
registry objects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from investment_orchestrator.research.approval_registry_switch_readiness import (
    SWITCH_TARGET_APPROVALS,
    SWITCH_TARGET_BASELINE,
    SWITCH_TARGET_FAIL_CLOSED,
)
from investment_orchestrator.research.evidence_packet import (
    build_embedded_active_anchor_registry_selection,
    build_evidence_packet,
    write_evidence_packet,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    compute_operator_completed_anchor_sha256 as sha,
)
from investment_orchestrator.research.support_signals import build_compiled_support_signals

AS_OF = "2026-07-04"
UNIVERSE = ["QQQ", "VOO", "SMH"]
SETTINGS = {"as_of": AS_OF, "core_universe": ["QQQ", "VOO"], "satellite_universe": ["SMH"]}


def _anchor(anchor_id: str, ticker: str = "QQQ", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": anchor_id,
        "anchor_type": "structural_theme",
        "applicable_tickers": [ticker],
        "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01",
        "valid_until": "2026-07-31",
        "source_type": "operator",
        "confidence_floor": "medium",
        "summary": "operator anchor",
    }
    base.update(overrides)
    return base


def _approval(anchor: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    base = {
        "approval_id": "APR-1",
        "decision": "approve",
        "operator_completed_anchor": anchor,
        "operator_completed_anchor_sha256": sha(anchor),
    }
    base.update(overrides)
    return base


def _write_anchors(path: Path, anchors: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "research_anchors_v1",
                "as_of_date": AS_OF,
                "is_llm_generated": False,
                "anchors": anchors,
            }
        ),
        encoding="utf-8",
    )


def _revocation(anchor: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    base = {
        "revocation_id": "REV-1",
        "target_type": "approval_anchor",
        "approval_id": "APR-1",
        "anchor_id": anchor["anchor_id"],
        "operator_completed_anchor_sha256": sha(anchor),
        "effective_as_of": AS_OF,
        "reason": "Thesis invalidated.",
        "revoked_by": "operator",
    }
    base.update(overrides)
    return base


def _write_approvals(
    path: Path,
    approvals: list[dict[str, Any]],
    *,
    revocations: Any = None,
    **manifest_overrides: Any,
) -> None:
    manifest = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": AS_OF,
        "approvals": approvals,
    }
    if revocations is not None:
        manifest["revocations"] = revocations
    manifest.update(manifest_overrides)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    anchors_path = tmp_path / "research_anchors.yaml"
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    return anchors_path, approvals_path


def _selection(anchors_path: Path, approvals_path: Path) -> dict[str, Any]:
    return build_embedded_active_anchor_registry_selection(
        anchors_path=anchors_path,
        approvals_path=approvals_path,
        allowed_universe=UNIVERSE,
        today=AS_OF,
        generated_at="t",
    )


def _memo(anchor_id: str, ticker: str = "QQQ") -> dict[str, Any]:
    return {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "confidence": "high",
        "source_notes": [{"claim": "grounded", "source": "operator"}],
        "ticker_relative_view": [
            {
                "ticker": ticker,
                "stance": "prefer",
                "rationale_12m_plus": "grounded thesis",
                "anchor_id_refs": [anchor_id],
            }
        ],
    }


def _signals(registry: dict[str, Any], anchor_id: str, ticker: str = "QQQ") -> dict[str, Any]:
    packet = build_evidence_packet(
        strategy_settings=SETTINGS,
        portfolio_snapshot_text=None,
        now_date=AS_OF,
        generated_at="t",
        research_anchors_summary={"available": True, "valid": True, "anchors": []},
        active_anchor_registry=registry,
    )
    return build_compiled_support_signals(
        evidence_packet=packet,
        analyst_memo=_memo(anchor_id, ticker=ticker),
        compilation_mode="evidence_plus_memo",
    )


def test_write_evidence_packet_embeds_ready_approvals_and_support_signals_ground(
    tmp_path: Path,
) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    approved_anchor = _anchor("APPROVED_QQQ")
    _write_anchors(anchors_path, [_anchor("VOO_BASE", "VOO")])
    _write_approvals(approvals_path, [_approval(approved_anchor)])

    packet = write_evidence_packet(
        output_path=tmp_path / "evidence_packet.json",
        strategy_settings=SETTINGS,
        portfolio_snapshot_text=None,
        now_date=AS_OF,
        research_anchors_path=anchors_path,
        research_anchor_approvals_path=approvals_path,
    )

    registry = packet["active_anchor_registry"]
    assert registry["schema_version"] == "active_research_anchor_registry_with_approvals_v1"
    art = build_compiled_support_signals(
        evidence_packet=packet,
        analyst_memo=_memo("APPROVED_QQQ"),
        compilation_mode="evidence_plus_memo",
    )
    accepted = art["accepted_support_signals"][0]
    assert accepted["ticker"] == "QQQ"
    assert accepted["operator_completed_anchor_sha256"] == sha(approved_anchor)
    assert art["permission_effect"] == "none"
    assert art["not_authorization"] is True


def test_valid_active_revocation_is_embedded_and_not_groundable(tmp_path: Path) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    approved_anchor = _anchor("APPROVED_QQQ")
    _write_anchors(anchors_path, [_anchor("VOO_BASE", "VOO")])
    _write_approvals(approvals_path, [_approval(approved_anchor)], revocations=[_revocation(approved_anchor)])

    selection = _selection(anchors_path, approvals_path)

    assert selection["selected_source"] == SWITCH_TARGET_APPROVALS
    registry = selection["selected_registry"]
    assert registry["schema_version"] == "active_research_anchor_registry_with_approvals_v1"
    assert "APPROVED_QQQ" not in [a["anchor_id"] for a in registry["active_anchors"]]
    revoked = [a for a in registry["inactive_anchors"] if a.get("anchor_id") == "APPROVED_QQQ"][0]
    assert revoked["status"] == "revoked"
    assert revoked["revocation_id"] == "REV-1"
    assert revoked["approval_id"] == "APR-1"
    assert revoked["effective_as_of"] == AS_OF
    assert revoked["reason"] == "Thesis invalidated."
    assert registry["counts"]["revoked"] == 1
    assert registry["revocations_applied"][0]["revocation_id"] == "REV-1"
    assert any(e["event"] == "anchor_revoked" for e in registry["audit_trail"])

    art = _signals(registry, "APPROVED_QQQ")
    assert art["accepted_support_signals"] == []
    assert art["permission_effect"] == "none"
    assert art["not_authorization"] is True


def test_future_revocation_embeds_pending_and_keeps_anchor_groundable(tmp_path: Path) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    approved_anchor = _anchor("APPROVED_QQQ")
    _write_anchors(anchors_path, [_anchor("VOO_BASE", "VOO")])
    _write_approvals(
        approvals_path,
        [_approval(approved_anchor)],
        revocations=[_revocation(approved_anchor, effective_as_of="2026-12-31")],
    )

    selection = _selection(anchors_path, approvals_path)

    assert selection["selected_source"] == SWITCH_TARGET_APPROVALS
    registry = selection["selected_registry"]
    assert "APPROVED_QQQ" in [a["anchor_id"] for a in registry["active_anchors"]]
    assert registry["counts"]["revoked"] == 0
    assert registry["revocations_applied"] == []
    assert registry["revocations_pending"][0]["revocation_id"] == "REV-1"
    assert any(e["event"] == "revocation_pending_future" for e in registry["audit_trail"])
    assert _signals(registry, "APPROVED_QQQ")["accepted_support_signals"]


def test_embedded_source_hash_binds_approvals_and_revocations(tmp_path: Path) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    approved_anchor = _anchor("APPROVED_QQQ")
    _write_anchors(anchors_path, [_anchor("VOO_BASE", "VOO")])
    _write_approvals(approvals_path, [_approval(approved_anchor)], revocations=[_revocation(approved_anchor)])

    registry = _selection(anchors_path, approvals_path)["selected_registry"]
    manifest = {entry["source_id"]: entry for entry in registry["source_manifest"]}

    approvals_source = manifest["operator_research_anchor_approvals_yaml"]
    revocations_source = manifest["operator_research_anchor_revocations_yaml"]
    assert approvals_source["path"] == revocations_source["path"] == str(approvals_path)
    assert approvals_source["sha256"] == revocations_source["sha256"]
    assert approvals_source["present"] is True
    assert revocations_source["present"] is True


def test_support_signals_does_not_read_revocation_files_or_artifacts() -> None:
    import inspect

    import investment_orchestrator.research.support_signals as ss

    source = inspect.getsource(ss)
    assert "research_anchor_revocation_manifest" not in source
    assert "validate_research_anchor_revocations" not in source
    assert "research_anchor_revocations_validation" not in source


def test_empty_approvals_selects_approvals_schema_but_matches_baseline(
    tmp_path: Path,
) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    _write_anchors(anchors_path, [_anchor("BASE_QQQ")])
    _write_approvals(approvals_path, [])

    selection = _selection(anchors_path, approvals_path)

    assert selection["selected_source"] == SWITCH_TARGET_APPROVALS
    selected = _signals(selection["selected_registry"], "BASE_QQQ")
    baseline = _signals(selection["baseline_registry"], "BASE_QQQ")
    assert selected["accepted_support_signals"] == baseline["accepted_support_signals"]


def test_malformed_approvals_falls_back_to_safe_baseline(tmp_path: Path) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    _write_anchors(anchors_path, [_anchor("BASE_QQQ")])
    approvals_path.write_text("schema_version: wrong\napprovals: []\n", encoding="utf-8")

    selection = _selection(anchors_path, approvals_path)

    assert selection["selected_source"] == SWITCH_TARGET_BASELINE
    assert selection["selected_registry"] == selection["baseline_registry"]
    selected = _signals(selection["selected_registry"], "BASE_QQQ")
    baseline = _signals(selection["baseline_registry"], "BASE_QQQ")
    assert selected["accepted_support_signals"] == baseline["accepted_support_signals"]


def test_invalid_revocation_state_falls_back_to_safe_baseline(tmp_path: Path) -> None:
    invalid_revocations = [
        [_revocation(_anchor("APPROVED_QQQ"), approval_id="APR-DOES-NOT-EXIST")],
        [_revocation(_anchor("APPROVED_QQQ"), operator_completed_anchor_sha256="0" * 64)],
        [_revocation(_anchor("APPROVED_QQQ"), anchor_id="WRONG_ANCHOR")],
        [_revocation(_anchor("APPROVED_QQQ"), revocation_id="REV-DUP"),
         _revocation(_anchor("APPROVED_QQQ"), revocation_id="REV-DUP")],
        [_revocation(_anchor("APPROVED_QQQ"), target_type="baseline_anchor")],
        [{**_revocation(_anchor("APPROVED_QQQ")), "order_intent": "buy"}],
        [{**_revocation(_anchor("APPROVED_QQQ")), "candidate_sha256": "candidate-cannot-bind"}],
        [_revocation(_anchor("APPROVED_QQQ"), reason="NEW_BUY")],
    ]

    for index, revocations in enumerate(invalid_revocations):
        anchors_path = tmp_path / f"research_anchors_{index}.yaml"
        approvals_path = tmp_path / f"research_anchor_approvals_{index}.yaml"
        approved_anchor = _anchor("APPROVED_QQQ")
        _write_anchors(anchors_path, [_anchor("BASE_QQQ")])
        _write_approvals(approvals_path, [_approval(approved_anchor)], revocations=revocations)

        selection = _selection(anchors_path, approvals_path)

        assert selection["approvals_registry"]["registry_valid"] is False
        assert selection["selected_source"] == SWITCH_TARGET_BASELINE
        assert selection["selected_registry"] == selection["baseline_registry"]
        assert [a["anchor_id"] for a in selection["selected_registry"]["active_anchors"]] == ["BASE_QQQ"]
        assert _signals(selection["selected_registry"], "APPROVED_QQQ")["accepted_support_signals"] == []


def test_duplicate_target_revocations_fall_back_to_safe_baseline(tmp_path: Path) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    approved_anchor = _anchor("APPROVED_QQQ")
    _write_anchors(anchors_path, [_anchor("BASE_QQQ")])
    _write_approvals(
        approvals_path,
        [_approval(approved_anchor)],
        revocations=[
            _revocation(approved_anchor, revocation_id="REV-1"),
            _revocation(approved_anchor, revocation_id="REV-2"),
        ],
    )

    selection = _selection(anchors_path, approvals_path)

    assert selection["approvals_registry"]["registry_valid"] is False
    assert "duplicate_target_revocation" in selection["approvals_registry"]["registry_blockers"]
    assert selection["selected_source"] == SWITCH_TARGET_BASELINE
    assert selection["selected_registry"] == selection["baseline_registry"]


def test_non_list_revocations_fall_back_to_safe_baseline(tmp_path: Path) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    approved_anchor = _anchor("APPROVED_QQQ")
    _write_anchors(anchors_path, [_anchor("BASE_QQQ")])
    _write_approvals(
        approvals_path,
        [_approval(approved_anchor)],
        revocations={"not": "a-list"},
    )

    selection = _selection(anchors_path, approvals_path)

    assert selection["approvals_registry"]["registry_valid"] is False
    assert selection["selected_source"] == SWITCH_TARGET_BASELINE
    assert selection["selected_registry"] == selection["baseline_registry"]


def test_invalid_revocation_state_with_invalid_baseline_selects_fail_closed_empty(tmp_path: Path) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    approved_anchor = _anchor("APPROVED_QQQ")
    _write_anchors(anchors_path, [_anchor("DUP"), _anchor("DUP")])
    _write_approvals(
        approvals_path,
        [_approval(approved_anchor)],
        revocations=[_revocation(approved_anchor, approval_id="APR-DOES-NOT-EXIST")],
    )

    selection = _selection(anchors_path, approvals_path)

    assert selection["approvals_registry"]["registry_valid"] is False
    assert selection["selected_source"] == SWITCH_TARGET_FAIL_CLOSED
    assert selection["selected_registry"]["active_anchors"] == []
    assert _signals(selection["selected_registry"], "APPROVED_QQQ")["accepted_support_signals"] == []


def test_invalid_baseline_selects_fail_closed_empty_no_partial_read(tmp_path: Path) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    approved_anchor = _anchor("APPROVED_QQQ")
    _write_anchors(anchors_path, [_anchor("DUP"), _anchor("DUP")])
    _write_approvals(approvals_path, [_approval(approved_anchor)])

    selection = _selection(anchors_path, approvals_path)

    assert selection["selected_source"] == SWITCH_TARGET_FAIL_CLOSED
    registry = selection["selected_registry"]
    assert registry["registry_valid"] is True
    assert registry["active_anchors"] == []
    assert "approval_registry_switch_fail_closed_empty" in registry["registry_blockers"]
    art = _signals(registry, "APPROVED_QQQ")
    assert art["accepted_support_signals"] == []
    assert art["anchor_source_available"] is False


def test_cross_source_duplicate_falls_back_to_baseline_without_precedence(
    tmp_path: Path,
) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    _write_anchors(anchors_path, [_anchor("SHARED")])
    _write_approvals(approvals_path, [_approval(_anchor("SHARED"))])

    selection = _selection(anchors_path, approvals_path)

    assert selection["readiness"]["switch_target"] == SWITCH_TARGET_BASELINE
    assert selection["selected_source"] == SWITCH_TARGET_BASELINE
    accepted = _signals(selection["selected_registry"], "SHARED")["accepted_support_signals"]
    assert accepted and "operator_completed_anchor_sha256" not in accepted[0]


def test_cross_source_duplicate_with_invalid_baseline_fails_closed(
    tmp_path: Path,
) -> None:
    anchors_path, approvals_path = _paths(tmp_path)
    _write_anchors(anchors_path, [_anchor("SHARED"), _anchor("SHARED")])
    _write_approvals(approvals_path, [_approval(_anchor("SHARED"))])

    selection = _selection(anchors_path, approvals_path)

    assert selection["selected_source"] == SWITCH_TARGET_FAIL_CLOSED
    assert selection["selected_registry"]["active_anchors"] == []
