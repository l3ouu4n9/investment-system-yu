"""R2G-2: anchor-source dual-read equivalence oracle tests (report-only).

Proves the oracle correctly reports when the future registry-backed usable-anchor
view matches the authoritative evidence_packet.research_anchors view, and surfaces
divergences with the right safety direction — the registry must never be *more
permissive* than the authoritative path.
"""

from __future__ import annotations

import json
from typing import Any

from investment_orchestrator.common.io import write_text
from investment_orchestrator.research.active_research_anchor_registry import (
    compile_active_research_anchor_registry,
)
from investment_orchestrator.research.anchor_source_equivalence import (
    DIFF_AUTHORITATIVE_ONLY,
    DIFF_FIELD_MISMATCH,
    DIFF_REGISTRY_ONLY,
    DIRECTION_REGISTRY_MORE_PERMISSIVE,
    DIRECTION_REGISTRY_STRICTER,
    SCHEMA_VERSION,
    evaluate_anchor_source_equivalence,
)
from investment_orchestrator.research.research_anchors import build_research_anchors_summary

UNIVERSE = ["QQQ", "VOO", "SMH"]
TODAY = "2026-06-28"

_ORDER_SHAPED_KEYS = frozenset(
    {
        "account",
        "quantity",
        "shares",
        "order_type",
        "tif",
        "time_in_force",
        "limit_price",
        "stop_price",
        "venue",
        "routing",
        "broker",
        "new_buy",
        "order_compilation",
    }
)


def _anchor(anchor_id: str = "A", *, ticker: str = "QQQ", valid_until: str = "2026-07-31") -> str:
    return (
        f"  - anchor_id: {anchor_id}\n"
        "    anchor_type: structural_theme\n"
        f"    applicable_tickers: [{ticker}]\n"
        '    anchor_date_et: "2026-06-15"\n'
        '    valid_from: "2026-06-01"\n'
        f'    valid_until: "{valid_until}"\n'
        "    source_type: operator\n"
        "    confidence_floor: medium\n"
    )


def _yaml(*blocks: str, is_llm: bool = False, schema: str = "research_anchors_v1") -> str:
    head = (
        f"schema_version: {schema}\n"
        f'as_of_date: "{TODAY}"\n'
        f"is_llm_generated: {'true' if is_llm else 'false'}\n"
        "anchors:\n"
    )
    return head + "".join(blocks)


def _evidence_packet_and_registry(tmp_path: Any, text: str) -> tuple[dict, dict]:
    path = tmp_path / "research_anchors.yaml"
    write_text(path, text)
    evidence_packet = {
        "research_anchors": build_research_anchors_summary(
            path, allowed_universe=UNIVERSE, today=TODAY
        )
    }
    registry = compile_active_research_anchor_registry(
        anchors_path=path, allowed_universe=UNIVERSE, today=TODAY
    )
    return evidence_packet, registry


def _eq(tmp_path: Any, text: str) -> dict:
    ep, reg = _evidence_packet_and_registry(tmp_path, text)
    return evaluate_anchor_source_equivalence(evidence_packet=ep, active_registry=reg)


def _iter_keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _iter_keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_keys(item)


# --- markers / shape ----------------------------------------------------------


def test_markers_and_report_only(tmp_path: Any) -> None:
    r = _eq(tmp_path, _yaml(_anchor()))
    assert r["schema_version"] == SCHEMA_VERSION
    assert r["is_llm_generated"] is False
    assert r["report_only"] is True
    assert r["permission_effect"] == "none"
    assert r["not_authorization"] is True
    assert r["not_execution_authorization"] is True
    assert r["current_authoritative_source"] == "evidence_packet.research_anchors"
    assert r["future_candidate_source"] == "active_research_anchor_registry"
    assert r["authoritative_behavior_unchanged"] is True
    for key in (
        "consumed_by_support_signals",
        "consumed_by_compiler",
        "consumed_by_promotion_eligibility",
        "consumed_by_availability",
        "consumed_by_step2",
        "consumed_by_gates",
        "consumed_by_step4",
    ):
        assert r[key] is False


def test_no_order_shaped_fields(tmp_path: Any) -> None:
    r = _eq(tmp_path, _yaml(_anchor()))
    present = {k for k in _iter_keys(r) if k.lower() in _ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked: {present}"


# --- 1. happy path equivalence ------------------------------------------------


def test_happy_path_equivalent(tmp_path: Any) -> None:
    r = _eq(tmp_path, _yaml(_anchor("A"), _anchor("B", ticker="VOO")))
    assert r["equivalent"] is True
    assert r["registry_no_more_permissive"] is True
    assert r["equivalence_blockers"] == []
    assert r["equivalence_warnings"] == []
    assert r["diffs"] == []
    assert r["old_anchor_summary"]["usable_anchor_ids"] == ["A", "B"]
    assert r["registry_anchor_summary"]["active_anchor_ids"] == ["A", "B"]
    assert r["checked_anchor_ids"] == ["A", "B"]
    assert set(r["checked_tickers"]) == {"QQQ", "VOO"}


# --- 2. missing anchors -------------------------------------------------------


def test_missing_anchors_both_empty_equivalent(tmp_path: Any) -> None:
    # No research_anchors.yaml at all.
    ep = {"research_anchors": build_research_anchors_summary(
        tmp_path / "nope.yaml", allowed_universe=UNIVERSE, today=TODAY
    )}
    reg = compile_active_research_anchor_registry(
        anchors_path=tmp_path / "nope.yaml", allowed_universe=UNIVERSE, today=TODAY
    )
    r = evaluate_anchor_source_equivalence(evidence_packet=ep, active_registry=reg)
    assert r["equivalent"] is True
    assert r["registry_no_more_permissive"] is True
    assert r["old_anchor_summary"]["usable_anchor_count"] == 0
    assert r["registry_anchor_summary"]["active_anchor_count"] == 0


# --- 3. stale / expired -------------------------------------------------------


def test_stale_expired_equivalent_both_exclude(tmp_path: Any) -> None:
    stale = (
        "  - anchor_id: OLD\n"
        "    anchor_type: structural_theme\n"
        "    applicable_tickers: [QQQ]\n"
        '    anchor_date_et: "2026-01-15"\n'
        '    valid_from: "2026-01-01"\n'
        '    valid_until: "2026-02-01"\n'
        "    source_type: operator\n"
        "    confidence_floor: medium\n"
    )
    r = _eq(tmp_path, _yaml(stale))
    # Both the authoritative view (stale -> not usable) and the registry (expired
    # -> inactive) exclude it, so the usable sets match.
    assert r["equivalent"] is True
    assert r["old_anchor_summary"]["usable_anchor_count"] == 0
    assert r["registry_anchor_summary"]["active_anchor_count"] == 0


# --- 4. per-anchor invalid: both exclude -> equivalent ------------------------


def test_out_of_universe_equivalent_both_exclude(tmp_path: Any) -> None:
    r = _eq(tmp_path, _yaml(_anchor("BAD", ticker="TSLA")))
    assert r["equivalent"] is True
    assert r["old_anchor_summary"]["usable_anchor_count"] == 0
    assert r["registry_anchor_summary"]["active_anchor_count"] == 0


def test_invalid_dates_equivalent_both_exclude(tmp_path: Any) -> None:
    bad = (
        "  - anchor_id: BADDATE\n"
        "    anchor_type: structural_theme\n"
        "    applicable_tickers: [QQQ]\n"
        '    anchor_date_et: "not-a-date"\n'
        '    valid_from: "2026-06-01"\n'
        '    valid_until: "2026-07-31"\n'
        "    source_type: operator\n"
        "    confidence_floor: medium\n"
    )
    r = _eq(tmp_path, _yaml(bad))
    assert r["equivalent"] is True
    assert r["registry_anchor_summary"]["active_anchor_count"] == 0


def test_mixed_valid_and_invalid_only_valid_in_both(tmp_path: Any) -> None:
    r = _eq(tmp_path, _yaml(_anchor("GOOD", ticker="QQQ"), _anchor("BAD", ticker="TSLA")))
    assert r["equivalent"] is True
    assert r["old_anchor_summary"]["usable_anchor_ids"] == ["GOOD"]
    assert r["registry_anchor_summary"]["active_anchor_ids"] == ["GOOD"]


# --- 4b. file-level failure: registry STRICTER (safety-positive divergence) ---


def test_is_llm_generated_registry_stricter_warning(tmp_path: Any) -> None:
    """LLM-tampered file: old per-anchor view keeps the structurally-valid anchor,
    the registry fails closed. Divergence is a WARNING (registry stricter), never a
    blocker, and registry_no_more_permissive stays true."""
    r = _eq(tmp_path, _yaml(_anchor("A"), is_llm=True))
    assert r["equivalent"] is False
    assert r["registry_no_more_permissive"] is True
    assert r["equivalence_blockers"] == []
    assert any(w.startswith(DIFF_AUTHORITATIVE_ONLY) for w in r["equivalence_warnings"])
    assert r["old_anchor_summary"]["usable_anchor_count"] == 1
    assert r["registry_anchor_summary"]["active_anchor_count"] == 0
    diff = next(d for d in r["diffs"] if d["anchor_id"] == "A")
    assert diff["kind"] == DIFF_AUTHORITATIVE_ONLY
    assert diff["direction"] == DIRECTION_REGISTRY_STRICTER


def test_duplicate_anchor_id_registry_stricter(tmp_path: Any) -> None:
    r = _eq(tmp_path, _yaml(_anchor("DUP", ticker="QQQ"), _anchor("DUP", ticker="VOO")))
    # Registry file-level fail-closed -> zero active. Authoritative view: the
    # duplicate is marked invalid too, so it is excluded there as well.
    assert r["registry_anchor_summary"]["active_anchor_count"] == 0
    assert r["registry_no_more_permissive"] is True
    assert r["equivalence_blockers"] == []


def test_forbidden_budget_key_registry_stricter(tmp_path: Any) -> None:
    text = _yaml(_anchor("A")) + "hard_cap_open_orders_budget: 1000\n"
    r = _eq(tmp_path, text)
    # Old view keeps the structurally-valid anchor; registry fails closed.
    assert r["registry_no_more_permissive"] is True
    assert r["equivalence_blockers"] == []
    assert r["registry_anchor_summary"]["active_anchor_count"] == 0


# --- 5. diff detection: registry MORE permissive -> BLOCKER -------------------


def test_registry_more_permissive_is_blocker(tmp_path: Any) -> None:
    """A mocked registry that activates an anchor the authoritative view does not
    have must produce a hard blocker and registry_no_more_permissive=false."""
    ep = {"research_anchors": {"available": True, "anchors": []}}
    rogue_registry = {
        "registry_valid": True,
        "source_manifest": [{"sha256": "deadbeef"}],
        "active_anchors": [
            {
                "anchor_id": "GHOST",
                "anchor_type": "structural_theme",
                "source_type": "operator",
                "applicable_tickers": ["QQQ"],
                "confidence_floor": "medium",
                "validation": {"valid": True, "stale": False, "usable": True, "problems": []},
            }
        ],
    }
    r = evaluate_anchor_source_equivalence(evidence_packet=ep, active_registry=rogue_registry)
    assert r["equivalent"] is False
    assert r["registry_no_more_permissive"] is False
    assert any(b.startswith(DIFF_REGISTRY_ONLY) for b in r["equivalence_blockers"])
    diff = next(d for d in r["diffs"] if d["anchor_id"] == "GHOST")
    assert diff["kind"] == DIFF_REGISTRY_ONLY
    assert diff["direction"] == DIRECTION_REGISTRY_MORE_PERMISSIVE


def test_field_mismatch_is_blocker(tmp_path: Any) -> None:
    """Shared anchor_id but a mismatched acceptance field -> blocker."""
    ep = {
        "research_anchors": {
            "available": True,
            "anchors": [
                {
                    "anchor_id": "A",
                    "anchor_type": "structural_theme",
                    "source_type": "operator",
                    "applicable_tickers": ["QQQ"],
                    "confidence_floor": "medium",
                    "valid": True,
                    "stale": False,
                    "usable": True,
                }
            ],
        }
    }
    registry = {
        "registry_valid": True,
        "source_manifest": [{"sha256": "x"}],
        "active_anchors": [
            {
                "anchor_id": "A",
                "anchor_type": "structural_theme",
                "source_type": "operator",
                "applicable_tickers": ["VOO"],  # mismatched ticker set
                "confidence_floor": "medium",
                "validation": {"valid": True, "stale": False, "usable": True, "problems": []},
            }
        ],
    }
    r = evaluate_anchor_source_equivalence(evidence_packet=ep, active_registry=registry)
    assert r["equivalent"] is False
    assert r["registry_no_more_permissive"] is False
    assert any(b.startswith(DIFF_FIELD_MISMATCH) for b in r["equivalence_blockers"])
    diff = next(d for d in r["diffs"] if d["anchor_id"] == "A")
    assert diff["kind"] == DIFF_FIELD_MISMATCH
    assert "applicable_tickers" in diff["details"]


# --- determinism / never-raises -----------------------------------------------


def test_deterministic(tmp_path: Any) -> None:
    text = _yaml(_anchor())
    a = _eq(tmp_path, text)
    b = _eq(tmp_path, text)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_never_raises_on_garbage_inputs() -> None:
    r = evaluate_anchor_source_equivalence(evidence_packet="nonsense", active_registry=12345)
    assert r["schema_version"] == SCHEMA_VERSION
    assert r["equivalent"] is True  # both views empty -> trivially equal
    assert r["registry_no_more_permissive"] is True


def test_none_inputs_safe() -> None:
    r = evaluate_anchor_source_equivalence(evidence_packet=None, active_registry=None)
    assert r["equivalent"] is True
    assert r["old_anchor_summary"]["usable_anchor_count"] == 0
    assert r["registry_anchor_summary"]["active_anchor_count"] == 0
