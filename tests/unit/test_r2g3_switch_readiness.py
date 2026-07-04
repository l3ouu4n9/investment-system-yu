"""R2G-2.1: R2G-3 anchor-registry switch-readiness gate + fixture corpus.

Tests-only. This module encodes the explicit go/no-go condition that a FUTURE
R2G-3 PR (switching ``support_signals`` to consume the active registry instead of
``evidence_packet.research_anchors``) must satisfy for every fixture BEFORE the
switch is allowed.

The go/no-go condition is deliberately NOT strict ``equivalent: true``. As
R2G-2-post-audit established, the registry may be *stricter* (fail-closed) than
the current path on file-level integrity failures (``is_llm_generated: true``,
duplicate ``anchor_id``, forbidden budget/order/action keys). A stricter grounding
source can only REDUCE what the (report-only) support signal grounds, so it is
safe. The unsafe directions are the registry being *more permissive* or
*disagreeing on acceptance fields*.

    R2G-3 GO condition, per fixture:
        registry_no_more_permissive == True   AND   equivalence_blockers == []
        (registry-stricter WARNINGS are acceptable; blockers are not)

This module changes NO production code and switches NO consumer. It only asserts
properties of the report-only equivalence oracle over a fixed corpus.
"""

from __future__ import annotations

from typing import Any

import pytest

from investment_orchestrator.common.io import write_text
from investment_orchestrator.research.active_research_anchor_registry import (
    compile_active_research_anchor_registry,
)
from investment_orchestrator.research.anchor_source_equivalence import (
    DIFF_AUTHORITATIVE_ONLY,
    DIFF_FIELD_MISMATCH,
    DIFF_REGISTRY_ONLY,
    DIRECTION_REGISTRY_STRICTER,
    evaluate_anchor_source_equivalence,
)
from investment_orchestrator.research.research_anchors import build_research_anchors_summary

# Machine-readable statement of the gate policy (documents intent for R2G-3).
R2G3_SWITCH_GO_NO_GO_REQUIRES_NO_MORE_PERMISSIVE = True

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


# --- the R2G-3 readiness gate (reusable) --------------------------------------


def assert_registry_switch_readiness(result: dict[str, Any], *, label: str = "") -> None:
    """Fail unless the equivalence result clears the R2G-3 switch gate.

    GO iff the registry is no more permissive than the authoritative path AND
    there are no blockers. Registry-stricter warnings are allowed; every warning
    must correspond to a ``registry_stricter`` divergence. Raises AssertionError
    (usable directly in a corpus loop and via ``pytest.raises`` for NO-GO cases).
    """
    assert result["registry_no_more_permissive"] is True, (
        f"{label}: registry_no_more_permissive must be True; diffs={result['diffs']}"
    )
    assert result["equivalence_blockers"] == [], (
        f"{label}: equivalence_blockers must be empty; got {result['equivalence_blockers']}"
    )
    unsafe = [d for d in result["diffs"] if d["kind"] in (DIFF_REGISTRY_ONLY, DIFF_FIELD_MISMATCH)]
    assert not unsafe, f"{label}: unsafe (more-permissive / field-mismatch) diffs present: {unsafe}"
    for diff in result["diffs"]:
        if diff["kind"] == DIFF_AUTHORITATIVE_ONLY:
            assert diff["direction"] == DIRECTION_REGISTRY_STRICTER, (
                f"{label}: authoritative-only diff must be registry_stricter; got {diff}"
            )


# --- fixture builders ---------------------------------------------------------


def _anchor(
    anchor_id: str = "A",
    *,
    ticker: str = "QQQ",
    anchor_date: str = "2026-06-15",
    valid_from: str = "2026-06-01",
    valid_until: str = "2026-07-31",
    confidence: str = "medium",
) -> str:
    return (
        f"  - anchor_id: {anchor_id}\n"
        "    anchor_type: structural_theme\n"
        f"    applicable_tickers: [{ticker}]\n"
        f'    anchor_date_et: "{anchor_date}"\n'
        f'    valid_from: "{valid_from}"\n'
        f'    valid_until: "{valid_until}"\n'
        "    source_type: operator\n"
        f"    confidence_floor: {confidence}\n"
    )


def _yaml(*blocks: str, is_llm: bool = False, schema: str = "research_anchors_v1") -> str:
    head = (
        f"schema_version: {schema}\n"
        f'as_of_date: "{TODAY}"\n'
        f"is_llm_generated: {'true' if is_llm else 'false'}\n"
        "anchors:\n"
    )
    body = "".join(blocks)
    return head + (body if body else "  []\n")


def _eq_from_yaml(tmp_path: Any, text: str) -> dict[str, Any]:
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
    return evaluate_anchor_source_equivalence(
        evidence_packet=evidence_packet, active_registry=registry
    )


def _eq_missing(tmp_path: Any) -> dict[str, Any]:
    missing = tmp_path / "nope.yaml"
    evidence_packet = {
        "research_anchors": build_research_anchors_summary(
            missing, allowed_universe=UNIVERSE, today=TODAY
        )
    }
    registry = compile_active_research_anchor_registry(
        anchors_path=missing, allowed_universe=UNIVERSE, today=TODAY
    )
    return evaluate_anchor_source_equivalence(
        evidence_packet=evidence_packet, active_registry=registry
    )


_STALE = _anchor("OLD", anchor_date="2026-01-15", valid_from="2026-01-01", valid_until="2026-02-01")
_INVALID_DATE = (
    "  - anchor_id: BADDATE\n"
    "    anchor_type: structural_theme\n"
    "    applicable_tickers: [QQQ]\n"
    '    anchor_date_et: "not-a-date"\n'
    '    valid_from: "2026-06-01"\n'
    '    valid_until: "2026-07-31"\n'
    "    source_type: operator\n"
    "    confidence_floor: medium\n"
)
_BACKWARDS = _anchor("BACKWARDS", valid_from="2026-08-01", valid_until="2026-07-31")


# (label, yaml_text_or_None, expect_equivalent, expect_stricter_warnings)
# yaml_text None => missing-file fixture.
_GO_CORPUS: list[tuple[str, str | None, bool, bool]] = [
    ("happy_single_valid", _yaml(_anchor("A")), True, False),
    ("happy_multi_valid", _yaml(_anchor("A"), _anchor("B", ticker="VOO")), True, False),
    ("missing_file", None, True, False),
    ("empty_anchors_list", _yaml(), True, False),
    ("stale_expired", _yaml(_STALE), True, False),
    ("out_of_universe", _yaml(_anchor("BAD", ticker="TSLA")), True, False),
    ("invalid_date", _yaml(_INVALID_DATE), True, False),
    ("valid_from_after_valid_until", _yaml(_BACKWARDS), True, False),
    ("mixed_valid_and_invalid", _yaml(_anchor("GOOD"), _anchor("BAD", ticker="TSLA")), True, False),
    # File-level integrity failures: registry is STRICTER (fail-closed).
    ("is_llm_generated_true", _yaml(_anchor("A"), is_llm=True), False, True),
    ("forbidden_budget_key", _yaml(_anchor("A")) + "hard_cap_open_orders_budget: 1000\n", False, True),
    ("forbidden_action_key", _yaml(_anchor("A")) + "order_compilation: true\n", False, True),
    # Duplicate anchor_id: both views drop it (registry file-level; old marks the
    # duplicate invalid) -> both empty -> equivalent, still no-more-permissive.
    ("duplicate_anchor_id", _yaml(_anchor("DUP"), _anchor("DUP", ticker="VOO")), True, False),
]


# --- 1-5. the GO corpus: every fixture must clear the switch gate -------------


@pytest.mark.parametrize("label,text,expect_equivalent,expect_warnings", _GO_CORPUS, ids=[c[0] for c in _GO_CORPUS])
def test_r2g3_switch_readiness_registry_no_more_permissive_corpus(
    tmp_path: Any, label: str, text: str | None, expect_equivalent: bool, expect_warnings: bool
) -> None:
    result = _eq_missing(tmp_path) if text is None else _eq_from_yaml(tmp_path, text)

    # The R2G-3 hard gate must pass for EVERY fixture in the GO corpus.
    assert_registry_switch_readiness(result, label=label)

    # Direction-specific expectations documenting each case.
    assert result["equivalent"] is expect_equivalent, (
        f"{label}: equivalent expected {expect_equivalent}, got {result['equivalent']}; "
        f"diffs={result['diffs']}"
    )
    if expect_warnings:
        assert result["equivalence_warnings"], f"{label}: expected registry-stricter warnings"
    else:
        assert result["equivalence_warnings"] == [], (
            f"{label}: unexpected warnings {result['equivalence_warnings']}"
        )

    # Registry never activates an anchor the authoritative view would not ground.
    reg_active = set(result["registry_anchor_summary"]["active_anchor_ids"])
    old_usable = set(result["old_anchor_summary"]["usable_anchor_ids"])
    assert reg_active.issubset(old_usable), (
        f"{label}: registry active set {reg_active} not a subset of authoritative usable "
        f"set {old_usable} — registry broadened grounding"
    )


def test_r2g3_go_corpus_all_no_more_permissive(tmp_path: Any) -> None:
    """Aggregate assertion: the entire GO corpus is switch-ready."""
    results = []
    for label, text, _, _ in _GO_CORPUS:
        result = _eq_missing(tmp_path) if text is None else _eq_from_yaml(tmp_path, text)
        assert_registry_switch_readiness(result, label=label)
        results.append((label, result["registry_no_more_permissive"], result["equivalence_blockers"]))
    assert all(no_more_perm for _, no_more_perm, _ in results)
    assert all(blockers == [] for _, _, blockers in results)
    assert R2G3_SWITCH_GO_NO_GO_REQUIRES_NO_MORE_PERMISSIVE is True


# --- 6. NO-GO: registry MORE permissive must fail the gate --------------------


def test_r2g3_gate_rejects_registry_more_permissive() -> None:
    """A registry that activates an anchor the authoritative view lacks must fail
    the switch gate (proves the gate has teeth)."""
    evidence_packet = {"research_anchors": {"available": True, "anchors": []}}
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
    result = evaluate_anchor_source_equivalence(
        evidence_packet=evidence_packet, active_registry=rogue_registry
    )
    assert result["registry_no_more_permissive"] is False
    assert result["equivalence_blockers"], "expected a machine-readable blocker"
    assert any(b.startswith(DIFF_REGISTRY_ONLY) for b in result["equivalence_blockers"])
    with pytest.raises(AssertionError):
        assert_registry_switch_readiness(result, label="rogue_more_permissive")


# --- 7. NO-GO: field mismatch must fail the gate ------------------------------


def test_r2g3_gate_rejects_field_mismatch() -> None:
    """A shared anchor_id that disagrees on a grounding-relevant field must fail
    the switch gate."""
    evidence_packet = {
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
                "applicable_tickers": ["VOO"],  # broader/different ticker set
                "confidence_floor": "medium",
                "validation": {"valid": True, "stale": False, "usable": True, "problems": []},
            }
        ],
    }
    result = evaluate_anchor_source_equivalence(
        evidence_packet=evidence_packet, active_registry=registry
    )
    assert result["registry_no_more_permissive"] is False
    assert any(b.startswith(DIFF_FIELD_MISMATCH) for b in result["equivalence_blockers"])
    with pytest.raises(AssertionError):
        assert_registry_switch_readiness(result, label="field_mismatch")


# --- 8. post-switch consumption invariant (source-level guard) ----------------


def test_r2g3_support_signals_consumes_registry_not_equivalence() -> None:
    """Structural guard (post-R2G-3): support_signals now grounds on the active
    registry compiler (over the evidence packet's research_anchors summary) and must
    NEVER consume the equivalence oracle (which is a diagnostic, not a source)."""
    import inspect

    from investment_orchestrator.research import support_signals

    src = inspect.getsource(support_signals)
    # R2G-3: grounding is consumed from the embedded active_anchor_registry section...
    assert "active_anchor_registry" in src, (
        "R2G-3: support_signals must consume the embedded active_anchor_registry"
    )
    # ...as an already-built source of truth — it must NOT rebuild the registry itself.
    assert "build_active_research_anchor_registry" not in src, (
        "R2G-3: support_signals must consume the embedded registry, not rebuild it"
    )
    # The equivalence oracle is a diagnostic and must never be a grounding source.
    assert "anchor_source_equivalence" not in src, (
        "support_signals must not consume the equivalence oracle"
    )


# --- 9. safety invariants: no order-shaped fields in either artifact ----------


def test_r2g3_registry_and_equivalence_have_no_order_shaped_fields(tmp_path: Any) -> None:
    path = tmp_path / "research_anchors.yaml"
    write_text(path, _yaml(_anchor("A")))
    registry = compile_active_research_anchor_registry(
        anchors_path=path, allowed_universe=UNIVERSE, today=TODAY
    )
    evidence_packet = {
        "research_anchors": build_research_anchors_summary(
            path, allowed_universe=UNIVERSE, today=TODAY
        )
    }
    equivalence = evaluate_anchor_source_equivalence(
        evidence_packet=evidence_packet, active_registry=registry
    )

    def keys(value: Any):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from keys(v)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    for name, artifact in (("registry", registry), ("equivalence", equivalence)):
        present = {k for k in keys(artifact) if k.lower() in _ORDER_SHAPED_KEYS}
        assert present == set(), f"{name}: order-shaped keys leaked: {present}"
        # Neither artifact grants permission.
        assert artifact.get("permission_effect") == "none"
        assert artifact.get("not_authorization") is True
