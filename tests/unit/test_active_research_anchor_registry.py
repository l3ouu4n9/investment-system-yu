"""R2G-1: active research-anchor registry compiler tests (report-only).

Every test proves either a fail-closed path or that the compiled registry is a
non-authorizing, report-only artifact wrapping the existing anchor validator.
"""

from __future__ import annotations

import json
from typing import Any

from investment_orchestrator.common.io import write_text
from investment_orchestrator.research.active_research_anchor_registry import (
    BLOCKER_RESEARCH_ANCHORS_SOURCE_INVALID,
    BLOCKER_SOURCE_YAML_MALFORMED,
    COMPILER_VERSION,
    OPERATOR_APPROVAL_TYPE,
    OPERATOR_SOURCE_CATEGORY,
    OPERATOR_SOURCE_ID,
    OPERATOR_SOURCE_TYPE,
    SCHEMA_VERSION,
    SOURCE_PROBLEM_MISSING,
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_INVALID,
    compile_active_research_anchor_registry,
)

UNIVERSE = ["QQQ", "VOO", "VTI", "SMH"]
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


def _anchor(
    anchor_id: str = "AI_CAPEX_2026H2",
    *,
    ticker: str = "QQQ",
    valid_until: str = "2026-07-31",
    confidence: str = "medium",
) -> str:
    return (
        f"  - anchor_id: {anchor_id}\n"
        "    anchor_type: structural_theme\n"
        f"    applicable_tickers: [{ticker}]\n"
        '    anchor_date_et: "2026-06-15"\n'
        '    valid_from: "2026-06-01"\n'
        f'    valid_until: "{valid_until}"\n'
        "    source_type: operator\n"
        f"    confidence_floor: {confidence}\n"
    )


def _yaml(*anchor_blocks: str, is_llm: bool = False, schema: str = "research_anchors_v1") -> str:
    head = (
        f"schema_version: {schema}\n"
        f'as_of_date: "{TODAY}"\n'
        f"is_llm_generated: {'true' if is_llm else 'false'}\n"
        "anchors:\n"
    )
    return head + "".join(anchor_blocks)


def _write(tmp_path: Any, text: str) -> Any:
    path = tmp_path / "research_anchors.yaml"
    write_text(path, text)
    return path


def _compile(tmp_path: Any, text: str, *, today: str | None = TODAY, universe: Any = UNIVERSE):
    return compile_active_research_anchor_registry(
        anchors_path=_write(tmp_path, text), allowed_universe=universe, today=today
    )


def _iter_keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _iter_keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_keys(item)


# --- 1. happy path ------------------------------------------------------------


def test_happy_path_markers_and_active_anchor(tmp_path: Any) -> None:
    r = _compile(tmp_path, _yaml(_anchor()))

    assert r["schema_version"] == SCHEMA_VERSION
    assert r["compiler_version"] == COMPILER_VERSION
    assert r["is_llm_generated"] is False
    assert r["report_only"] is True
    assert r["permission_effect"] == "none"
    assert r["not_authorization"] is True
    assert r["not_execution_authorization"] is True
    assert r["consumed_by_availability"] is False
    assert r["consumed_by_step2"] is False
    assert r["consumed_by_gates"] is False
    assert r["consumed_by_step4"] is False
    assert r["as_of_date"] == TODAY

    assert r["registry_valid"] is True
    assert r["registry_blockers"] == []
    assert r["counts"] == {"active": 1, "expired": 0, "revoked": 0, "invalid": 0, "superseded": 0}

    src = r["source_manifest"][0]
    assert src["source_id"] == OPERATOR_SOURCE_ID
    assert src["source_category"] == OPERATOR_SOURCE_CATEGORY
    assert src["source_type"] == OPERATOR_SOURCE_TYPE
    assert src["present"] is True and src["valid"] is True and src["problems"] == []
    assert isinstance(src["sha256"], str) and len(src["sha256"]) == 64

    anchor = r["active_anchors"][0]
    assert anchor["anchor_id"] == "AI_CAPEX_2026H2"
    assert anchor["applicable_tickers"] == ["QQQ"]
    assert anchor["source_type"] == OPERATOR_SOURCE_TYPE
    assert anchor["source_id"] == OPERATOR_SOURCE_ID
    assert anchor["source_category"] == OPERATOR_SOURCE_CATEGORY
    assert anchor["approval_type"] == OPERATOR_APPROVAL_TYPE
    assert anchor["approval_id"] is None
    assert anchor["candidate_id"] is None
    assert anchor["candidate_sha256"] is None
    assert anchor["status"] == STATUS_ACTIVE
    assert isinstance(anchor["content_sha256"], str) and len(anchor["content_sha256"]) == 64
    assert anchor["validation"] == {"valid": True, "stale": False, "usable": True, "problems": []}

    # audit trail records activation
    assert any(e["event"] == "anchor_activated" and e["anchor_id"] == "AI_CAPEX_2026H2"
               for e in r["audit_trail"])


def test_registry_has_no_order_shaped_fields(tmp_path: Any) -> None:
    r = _compile(tmp_path, _yaml(_anchor()))
    present = {k for k in _iter_keys(r) if k.lower() in _ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked: {present}"


def test_multiple_anchors_all_active(tmp_path: Any) -> None:
    r = _compile(tmp_path, _yaml(_anchor("A", ticker="QQQ"), _anchor("B", ticker="VOO")))
    assert r["counts"]["active"] == 2
    assert {a["anchor_id"] for a in r["active_anchors"]} == {"A", "B"}


# --- 2. fail-closed cases -----------------------------------------------------


def test_missing_file_is_valid_empty_registry(tmp_path: Any) -> None:
    r = compile_active_research_anchor_registry(
        anchors_path=tmp_path / "nope.yaml", allowed_universe=UNIVERSE, today=TODAY
    )
    assert r["registry_valid"] is True
    assert r["counts"]["active"] == 0
    assert r["active_anchors"] == []
    src = r["source_manifest"][0]
    assert src["present"] is False and src["valid"] is False
    assert SOURCE_PROBLEM_MISSING in src["problems"]


def test_malformed_yaml_fails_closed(tmp_path: Any) -> None:
    r = _compile(tmp_path, "schema_version: research_anchors_v1\nanchors: [oops\n")
    assert r["registry_valid"] is False
    assert BLOCKER_SOURCE_YAML_MALFORMED in r["registry_blockers"]
    assert r["counts"]["active"] == 0


def test_wrong_schema_version_fails_closed(tmp_path: Any) -> None:
    r = _compile(tmp_path, _yaml(_anchor(), schema="research_anchors_v999"))
    assert r["registry_valid"] is False
    assert BLOCKER_RESEARCH_ANCHORS_SOURCE_INVALID in r["registry_blockers"]
    assert r["counts"]["active"] == 0


def test_is_llm_generated_true_fails_closed(tmp_path: Any) -> None:
    r = _compile(tmp_path, _yaml(_anchor(), is_llm=True))
    assert r["registry_valid"] is False
    assert BLOCKER_RESEARCH_ANCHORS_SOURCE_INVALID in r["registry_blockers"]
    assert r["counts"]["active"] == 0
    # LLM-generated source never yields an active anchor.
    assert r["active_anchors"] == []


def test_out_of_universe_ticker_not_active(tmp_path: Any) -> None:
    r = _compile(tmp_path, _yaml(_anchor(ticker="TSLA")))
    assert r["counts"]["active"] == 0
    assert r["counts"]["invalid"] == 1
    assert r["inactive_anchors"][0]["status"] == STATUS_INVALID
    # registry stays structurally valid (per-anchor rejection, not a file failure)
    assert r["registry_valid"] is True


def test_stale_expired_anchor_not_active(tmp_path: Any) -> None:
    # A correctly-ordered range wholly in the past → stale/expired (not invalid).
    stale = (
        "  - anchor_id: OLD_THEME\n"
        "    anchor_type: structural_theme\n"
        "    applicable_tickers: [QQQ]\n"
        '    anchor_date_et: "2026-01-15"\n'
        '    valid_from: "2026-01-01"\n'
        '    valid_until: "2026-02-01"\n'
        "    source_type: operator\n"
        "    confidence_floor: medium\n"
    )
    r = _compile(tmp_path, _yaml(stale))
    assert r["counts"]["active"] == 0
    assert r["counts"]["expired"] == 1
    inactive = r["inactive_anchors"][0]
    assert inactive["status"] == STATUS_EXPIRED
    assert inactive["validation"]["stale"] is True
    assert any(e["event"] == "anchor_expired" for e in r["audit_trail"])


def test_duplicate_anchor_id_fails_closed(tmp_path: Any) -> None:
    r = _compile(tmp_path, _yaml(_anchor("DUP", ticker="QQQ"), _anchor("DUP", ticker="VOO")))
    assert r["registry_valid"] is False
    assert BLOCKER_RESEARCH_ANCHORS_SOURCE_INVALID in r["registry_blockers"]
    assert r["counts"]["active"] == 0


def test_forbidden_budget_key_fails_closed(tmp_path: Any) -> None:
    text = _yaml(_anchor()) + "hard_cap_open_orders_budget: 1000\n"
    r = _compile(tmp_path, text)
    assert r["registry_valid"] is False
    assert BLOCKER_RESEARCH_ANCHORS_SOURCE_INVALID in r["registry_blockers"]
    assert r["counts"]["active"] == 0


def test_forbidden_action_key_fails_closed(tmp_path: Any) -> None:
    text = _yaml(_anchor()) + "order_compilation: true\n"
    r = _compile(tmp_path, text)
    assert r["registry_valid"] is False
    assert r["counts"]["active"] == 0


def test_invalid_date_not_active(tmp_path: Any) -> None:
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
    r = _compile(tmp_path, _yaml(bad))
    assert r["counts"]["active"] == 0
    assert r["counts"]["invalid"] == 1


def test_valid_from_after_valid_until_not_active(tmp_path: Any) -> None:
    bad = (
        "  - anchor_id: BACKWARDS\n"
        "    anchor_type: structural_theme\n"
        "    applicable_tickers: [QQQ]\n"
        '    anchor_date_et: "2026-06-15"\n'
        '    valid_from: "2026-08-01"\n'
        '    valid_until: "2026-07-31"\n'
        "    source_type: operator\n"
        "    confidence_floor: medium\n"
    )
    r = _compile(tmp_path, _yaml(bad))
    assert r["counts"]["active"] == 0
    assert r["counts"]["invalid"] == 1


def test_mixed_valid_and_invalid_only_valid_active(tmp_path: Any) -> None:
    r = _compile(tmp_path, _yaml(_anchor("GOOD", ticker="QQQ"), _anchor("BAD", ticker="TSLA")))
    assert r["counts"]["active"] == 1
    assert r["counts"]["invalid"] == 1
    assert r["active_anchors"][0]["anchor_id"] == "GOOD"
    assert r["registry_valid"] is True


# --- 4. determinism -----------------------------------------------------------


def test_deterministic_same_input(tmp_path: Any) -> None:
    text = _yaml(_anchor())
    a = _compile(tmp_path, text)
    b = _compile(tmp_path, text)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_content_sha256_stable_across_runs(tmp_path: Any) -> None:
    text = _yaml(_anchor())
    a = _compile(tmp_path, text)["active_anchors"][0]["content_sha256"]
    b = _compile(tmp_path, text)["active_anchors"][0]["content_sha256"]
    assert a == b


def test_source_sha256_changes_when_source_changes(tmp_path: Any) -> None:
    s1 = _compile(tmp_path, _yaml(_anchor(valid_until="2026-07-31")))["source_manifest"][0]["sha256"]
    s2 = _compile(tmp_path, _yaml(_anchor(valid_until="2026-08-31")))["source_manifest"][0]["sha256"]
    assert s1 != s2


def test_content_sha256_changes_when_anchor_definition_changes(tmp_path: Any) -> None:
    c1 = _compile(tmp_path, _yaml(_anchor(valid_until="2026-07-31")))["active_anchors"][0][
        "content_sha256"
    ]
    c2 = _compile(tmp_path, _yaml(_anchor(valid_until="2026-08-31")))["active_anchors"][0][
        "content_sha256"
    ]
    assert c1 != c2


def test_never_raises_on_garbage_universe(tmp_path: Any) -> None:
    # allowed_universe of a wrong type must not crash the compiler.
    r = compile_active_research_anchor_registry(
        anchors_path=_write(tmp_path, _yaml(_anchor())),
        allowed_universe=None,
        today=TODAY,
    )
    assert r["schema_version"] == SCHEMA_VERSION
    # With an empty/None universe every ticker is out-of-universe -> not active.
    assert r["counts"]["active"] == 0
