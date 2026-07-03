"""R2E.5b-7c: rowless final-safety preflight tests.

Every test proves either a fail-closed path or that the report-only, rowless
output carries no order authority. The preflight opens no gate: on the promoted
path ``preflight_passed`` is false BY DESIGN (policy blockers present) while
``deterministic_prerequisites_ready`` reports the input diagnostics separately.
"""

from __future__ import annotations

from decimal import Decimal
import json
from typing import Any

import pytest

from investment_orchestrator.state.final_execution_safety_preflight import (
    BLOCKER_BUDGET_SETTINGS_INVALID,
    BLOCKER_BUDGET_SETTINGS_MISSING,
    BLOCKER_BUY_UNIVERSE_MISSING,
    BLOCKER_FINAL_GATE_STILL_CLOSED_BY_POLICY,
    BLOCKER_HARD_CAP_HEADROOM_NOT_COMPUTABLE,
    BLOCKER_ORDER_COMPILATION_NOT_ALLOWED,
    BLOCKER_PORTFOLIO_SNAPSHOT_MISSING,
    BLOCKER_PORTFOLIO_SNAPSHOT_UNPARSEABLE,
    BLOCKER_PREFLIGHT_INTERNAL_ERROR,
    BLOCKER_STATE_NOT_LITERAL_STRICT_FRESH,
    BLOCKER_STEP4_DRY_RUN_MISSING,
    BLOCKER_STEP4_DRY_RUN_NOT_REPORT_ONLY,
    BLOCKER_STEP4_DRY_RUN_POLICY_BLOCKER_MISSING,
    BLOCKER_STEP4_DRY_RUN_REAL_GATE_NOT_CLOSED,
    BLOCKER_STEP4_VERIFICATION_MISSING,
    BLOCKER_STEP4_VERIFICATION_NOT_REPORT_ONLY,
    EXPECTED_REAL_GATE_POLICY_BLOCKER,
    EXPECTED_STEP4_DRY_RUN_SCHEMA_VERSION,
    EXPECTED_STEP4_VERIFICATION_SCHEMA_VERSION,
    PREFLIGHT_SCHEMA_VERSION,
    _max_new_tickers_per_week_total,
    evaluate_promoted_final_safety_preflight,
)

STEP3_AUDIT_ONLY_STATE = "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY"
_DEFAULT = object()

# Every field that would betray an order-shaped artifact. The preflight schema
# must contain NONE of these (checked recursively over the full artifact).
_FORBIDDEN_ORDER_SHAPED_KEYS = frozenset(
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
        "order_rows",
        "preview_rows",
        "candidate_orders",
    }
)


# --- fixtures (mirror the real committed artifacts) ---------------------------


def audit_only_research_decision(**overrides: Any) -> dict[str, Any]:
    decision = {
        "state": STEP3_AUDIT_ONLY_STATE,
        "allowed_actions": [
            "HOLD",
            "NO_TRADE",
            "PROMOTED_RESEARCH_DECISION",
            "PROMOTED_RESEARCH_AUDIT",
        ],
        "blocked_actions": [
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "promoted_step2_decision_only": True,
        "promoted_step3_audit_only": True,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
    }
    decision.update(overrides)
    return decision


def readiness_verification(**overrides: Any) -> dict[str, Any]:
    verification = {
        "schema_version": EXPECTED_STEP4_VERIFICATION_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "valid_for_promoted_step4_preview": True,
        "would_allow_promoted_step4_preview": True,
        "current_real_gate_allows": False,
        "verification_blockers": [],
        "verification_warnings": [],
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "step4_allowed": False,
        "final_execution_allowed": False,
        "broker_automation_allowed": False,
        "raw_deep_research_source_used": False,
        "consumed_by_availability": False,
        "consumed_by_step4": False,
        "consumed_by_gates": False,
    }
    verification.update(overrides)
    return verification


def preview_gate_dry_run(**overrides: Any) -> dict[str, Any]:
    dry_run = {
        "schema_version": EXPECTED_STEP4_DRY_RUN_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "dry_run_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "would_allow_promoted_step4_preview": True,
        "current_real_gate_allows": False,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "step4_allowed": False,
        "final_execution_allowed": False,
        "broker_automation_allowed": False,
        "dry_run_blockers": [EXPECTED_REAL_GATE_POLICY_BLOCKER],
        "dry_run_warnings": [],
        "consumed_by_availability": False,
        "consumed_by_step4": False,
        "consumed_by_gates": False,
    }
    dry_run.update(overrides)
    return dry_run


def readiness_settings(**overrides: Any) -> dict[str, Any]:
    settings = {
        "as_of": "2026-06-28",
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.00,
        "max_new_tickers_per_week": {
            "base_universe_new_tickers_per_week": 2,
            "extended_etf_sleeve_new_tickers_per_week": 2,
        },
    }
    settings.update(overrides)
    return settings


def decision_packet(**overrides: Any) -> dict[str, Any]:
    packet = {"effective_allowed_buy_universe": ["QQQ", "VOO"]}
    packet.update(overrides)
    return packet


def portfolio_snapshot_text() -> str:
    # Empty live-structure columns (11/12) parse cleanly with no data gap, so
    # the (2a) budget total — and thus hard-cap headroom — is deterministic.
    return (
        "(2a) existing_buy_open_orders_summary\n"
        "QQQ | 1000.00 | 900.00 | 100.00 | T4-E | - | - | - | - | - | - |  | \n"
    )


def run_preflight(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        research_decision=audit_only_research_decision(),
        step4_readiness_verification=readiness_verification(),
        step4_preview_gate_dry_run=preview_gate_dry_run(),
        strategy_settings=readiness_settings(),
        portfolio_snapshot_text=portfolio_snapshot_text(),
        step2_decision_packet=decision_packet(),
    )
    kwargs.update(overrides)
    return evaluate_promoted_final_safety_preflight(**kwargs)


def _iter_keys(value: Any):
    if isinstance(value, dict):
        for key, sub in value.items():
            yield key
            yield from _iter_keys(sub)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_keys(item)


# --- 1. happy path ------------------------------------------------------------


def test_happy_path_report_only_rowless_and_diagnostics_present() -> None:
    result = run_preflight()

    assert result["schema_version"] == PREFLIGHT_SCHEMA_VERSION
    assert result["is_llm_generated"] is False
    assert result["report_only"] is True
    assert result["dry_run_only"] is True
    assert result["rowless"] is True
    assert result["permission_effect"] == "none"
    assert result["not_authorization"] is True
    assert result["not_execution_authorization"] is True
    assert result["contains_order_rows"] is False
    assert result["contains_preview_rows"] is False
    assert result["current_real_gate_allows"] is False
    assert result["order_compilation_allowed"] is False
    assert result["new_buy_permission"] is False
    assert result["step4_allowed"] is False
    assert result["final_execution_allowed"] is False
    assert result["broker_automation_allowed"] is False
    assert result["consumed_by_availability"] is False
    assert result["consumed_by_step4"] is False
    assert result["consumed_by_gates"] is False

    # Inputs are all healthy, so deterministic prerequisites are ready...
    assert result["deterministic_prerequisites_ready"] is True
    # ...but the gate stays closed by policy, so the preflight never "passes".
    assert result["preflight_passed"] is False
    assert BLOCKER_FINAL_GATE_STILL_CLOSED_BY_POLICY in result["preflight_blockers"]
    assert BLOCKER_STATE_NOT_LITERAL_STRICT_FRESH in result["preflight_blockers"]
    assert BLOCKER_ORDER_COMPILATION_NOT_ALLOWED in result["preflight_blockers"]

    budget = result["budget_cap_readiness"]
    assert budget["rowless"] is True
    assert budget["contains_order_rows"] is False
    assert budget["contains_preview_rows"] is False
    assert Decimal(budget["hard_cap_open_orders_budget"]) == Decimal("38211.29")
    assert Decimal(budget["target_new_buy_budget_this_run"]) == Decimal("12000")
    assert budget["max_new_tickers_per_week_total"] == 4
    assert budget["existing_open_buy_orders_ticker_count"] == 1
    assert budget["hard_cap_headroom_computable"] is True
    # 38211.29 - 1000.00 existing target budget
    assert Decimal(budget["hard_cap_headroom"]) == Decimal("37211.29")
    # full target headroom: rowless run compiles ZERO net-new notional
    assert Decimal(budget["target_new_buy_budget_headroom"]) == Decimal("12000")
    assert Decimal(budget["net_new_notional_this_run"]) == Decimal("0")
    assert budget["remaining_new_ticker_slots"] == 4
    assert budget["effective_allowed_buy_universe_present"] is True
    assert budget["effective_allowed_buy_universe_size"] == 2


def test_happy_path_step4_summary_reports_but_does_not_consume() -> None:
    result = run_preflight()
    summary = result["step4_readiness_summary"]
    assert summary["verification_present"] is True
    assert summary["verification_report_only_boundary_ok"] is True
    assert summary["verification_valid_for_promoted_step4_preview"] is True
    assert summary["dry_run_present"] is True
    assert summary["dry_run_would_allow_promoted_step4_preview"] is True
    assert summary["dry_run_current_real_gate_allows"] is False
    assert summary["dry_run_real_gate_policy_blocker_present"] is True
    assert summary["consumed_as_gate_authority"] is False


def test_artifact_contains_no_order_shaped_fields() -> None:
    result = run_preflight()
    present = {k for k in _iter_keys(result) if k.lower() in _FORBIDDEN_ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked into preflight: {present}"
    # And no ticker-level structure: the universe is a size, never a row/list.
    assert isinstance(result["budget_cap_readiness"]["effective_allowed_buy_universe_size"], int)


# --- 2. final gate remains blocked --------------------------------------------


def test_final_gate_diagnostics_report_closed_for_promoted_path() -> None:
    result = run_preflight()
    diag = result["final_gate_diagnostics"]
    assert diag["evaluator_behavior_modified"] is False
    assert diag["required_state"] == "STRICT_FRESH"
    assert diag["required_allowed_action"] == "ORDER_COMPILATION"
    assert diag["state_is_literal_strict_fresh"] is False
    assert diag["order_compilation_in_allowed_actions"] is False
    assert diag["current_state_is_promoted_step3_audit_only"] is True
    assert diag["ready_for_order_compilation"] is False
    assert diag["blocked"] is True


def test_preflight_does_not_alter_final_gate_evaluator() -> None:
    """Running the preflight leaves evaluate_final_execution_safety unchanged."""
    from investment_orchestrator.state.final_execution_safety_gate import (
        evaluate_final_execution_safety,
    )

    before = evaluate_final_execution_safety(
        step2_decision_packet=None,
        step3_audited_packet=None,
        step1_permission=audit_only_research_decision(),
    )
    run_preflight()
    after = evaluate_final_execution_safety(
        step2_decision_packet=None,
        step3_audited_packet=None,
        step1_permission=audit_only_research_decision(),
    )
    assert before.ready_for_order_compilation is False
    assert after.ready_for_order_compilation is False
    assert before.checked_conditions == after.checked_conditions


def test_literal_strict_fresh_without_order_compilation_still_blocks_by_policy() -> None:
    """Even a literal STRICT_FRESH state without ORDER_COMPILATION stays closed."""
    result = run_preflight(
        research_decision=audit_only_research_decision(
            state="STRICT_FRESH",
            allowed_actions=["HOLD", "NO_TRADE"],
        )
    )
    assert result["preflight_passed"] is False
    assert BLOCKER_ORDER_COMPILATION_NOT_ALLOWED in result["preflight_blockers"]
    assert BLOCKER_FINAL_GATE_STILL_CLOSED_BY_POLICY in result["preflight_blockers"]
    assert BLOCKER_STATE_NOT_LITERAL_STRICT_FRESH not in result["preflight_blockers"]


# --- 3. 7b artifact handling --------------------------------------------------


def test_missing_readiness_dry_run_fails_closed() -> None:
    result = run_preflight(step4_preview_gate_dry_run=None)
    assert result["deterministic_prerequisites_ready"] is False
    assert result["preflight_passed"] is False
    assert BLOCKER_STEP4_DRY_RUN_MISSING in result["preflight_blockers"]
    assert result["current_real_gate_allows"] is False


def test_missing_readiness_verification_fails_closed() -> None:
    result = run_preflight(step4_readiness_verification=None)
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_STEP4_VERIFICATION_MISSING in result["preflight_blockers"]


def test_malformed_dry_run_fails_closed() -> None:
    result = run_preflight(step4_preview_gate_dry_run={"schema_version": "bogus"})
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_STEP4_DRY_RUN_NOT_REPORT_ONLY in result["preflight_blockers"]


def test_dry_run_with_real_gate_allowed_rejected() -> None:
    result = run_preflight(
        step4_preview_gate_dry_run=preview_gate_dry_run(current_real_gate_allows=True)
    )
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_STEP4_DRY_RUN_REAL_GATE_NOT_CLOSED in result["preflight_blockers"]


def test_dry_run_missing_policy_blocker_rejected() -> None:
    result = run_preflight(step4_preview_gate_dry_run=preview_gate_dry_run(dry_run_blockers=[]))
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_STEP4_DRY_RUN_POLICY_BLOCKER_MISSING in result["preflight_blockers"]


def test_non_report_only_verification_rejected() -> None:
    result = run_preflight(step4_readiness_verification=readiness_verification(report_only=False))
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_STEP4_VERIFICATION_NOT_REPORT_ONLY in result["preflight_blockers"]


def test_verification_claiming_order_permission_rejected() -> None:
    result = run_preflight(
        step4_readiness_verification=readiness_verification(order_compilation_allowed=True)
    )
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_STEP4_VERIFICATION_NOT_REPORT_ONLY in result["preflight_blockers"]


def test_verification_claiming_consumption_rejected() -> None:
    result = run_preflight(
        step4_readiness_verification=readiness_verification(consumed_by_gates=True)
    )
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_STEP4_VERIFICATION_NOT_REPORT_ONLY in result["preflight_blockers"]


# --- 4. budget / cap diagnostics ----------------------------------------------


def test_missing_settings_fails_closed() -> None:
    result = run_preflight(strategy_settings=None)
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_BUDGET_SETTINGS_MISSING in result["preflight_blockers"]


def test_non_finite_budget_fails_closed() -> None:
    result = run_preflight(
        strategy_settings=readiness_settings(hard_cap_open_orders_budget=float("inf"))
    )
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_BUDGET_SETTINGS_INVALID in result["preflight_blockers"]


def test_negative_budget_fails_closed() -> None:
    result = run_preflight(
        strategy_settings=readiness_settings(target_new_buy_budget_this_run=-1.0)
    )
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_BUDGET_SETTINGS_INVALID in result["preflight_blockers"]


def test_boolean_budget_rejected() -> None:
    result = run_preflight(
        strategy_settings=readiness_settings(hard_cap_open_orders_budget=True)
    )
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_BUDGET_SETTINGS_INVALID in result["preflight_blockers"]


def test_missing_portfolio_snapshot_fails_closed() -> None:
    result = run_preflight(portfolio_snapshot_text=None)
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_PORTFOLIO_SNAPSHOT_MISSING in result["preflight_blockers"]
    assert result["budget_cap_readiness"]["hard_cap_headroom_computable"] is False


def test_snapshot_without_section_2a_fails_closed() -> None:
    result = run_preflight(portfolio_snapshot_text="no section here\n")
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_PORTFOLIO_SNAPSHOT_UNPARSEABLE in result["preflight_blockers"]


def test_snapshot_with_malformed_row_blocks_headroom() -> None:
    """A row with a parse gap makes headroom non-computable (fail closed)."""
    text = (
        "(2a) existing_buy_open_orders_summary\n"
        "QQQ | 1000.00 | 900.00\n"  # too few columns -> data_gap
    )
    result = run_preflight(portfolio_snapshot_text=text)
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_HARD_CAP_HEADROOM_NOT_COMPUTABLE in result["preflight_blockers"]
    assert result["budget_cap_readiness"]["existing_open_buy_orders_rows_with_data_gaps"] >= 1


def test_missing_effective_allowed_buy_universe_fails_closed() -> None:
    result = run_preflight(step2_decision_packet={"effective_allowed_buy_universe": []})
    assert result["deterministic_prerequisites_ready"] is False
    assert BLOCKER_BUY_UNIVERSE_MISSING in result["preflight_blockers"]


def test_valid_inputs_produce_deterministic_headroom() -> None:
    a = run_preflight()["budget_cap_readiness"]
    b = run_preflight()["budget_cap_readiness"]
    assert a == b
    assert Decimal(a["hard_cap_headroom"]) == Decimal("37211.29")
    # effective headroom = min(target headroom 12000, hard-cap headroom 37211.29)
    assert Decimal(a["effective_new_buy_headroom"]) == Decimal("12000")


# --- 5. never-raise / internal error ------------------------------------------


class _EvilMapping(dict):
    def get(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102
        raise RuntimeError("boom")


def test_preflight_never_raises_and_falls_back_closed() -> None:
    result = evaluate_promoted_final_safety_preflight(
        research_decision=_EvilMapping({"state": "x"}),
        step4_readiness_verification=readiness_verification(),
        step4_preview_gate_dry_run=preview_gate_dry_run(),
        strategy_settings=readiness_settings(),
        portfolio_snapshot_text=portfolio_snapshot_text(),
        step2_decision_packet=decision_packet(),
    )
    assert result["schema_version"] == PREFLIGHT_SCHEMA_VERSION
    assert result["preflight_passed"] is False
    assert result["deterministic_prerequisites_ready"] is False
    assert result["current_real_gate_allows"] is False
    assert BLOCKER_PREFLIGHT_INTERNAL_ERROR in result["preflight_blockers"]
    assert BLOCKER_FINAL_GATE_STILL_CLOSED_BY_POLICY in result["preflight_blockers"]


# --- 6. drift guards ----------------------------------------------------------


def test_mirrored_step4_schema_constants_match_source() -> None:
    from investment_orchestrator.research.promoted_step4_readiness_dry_run import (
        DRY_RUN_SCHEMA_VERSION,
        VERIFICATION_SCHEMA_VERSION,
    )

    assert EXPECTED_STEP4_VERIFICATION_SCHEMA_VERSION == VERIFICATION_SCHEMA_VERSION
    assert EXPECTED_STEP4_DRY_RUN_SCHEMA_VERSION == DRY_RUN_SCHEMA_VERSION


def test_mirrored_policy_blocker_matches_source() -> None:
    from investment_orchestrator.research.promoted_step3_audit_dry_run import (
        DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY,
    )

    assert EXPECTED_REAL_GATE_POLICY_BLOCKER == DRY_RUN_BLOCKER_REAL_GATE_STILL_CLOSED_BY_POLICY


@pytest.mark.parametrize(
    "value,expected",
    [
        (5, 5),
        ({"a": 2, "b": 3}, 5),
        (True, None),
        ({"a": True, "b": 3}, 3),
        ("x", None),
        (None, None),
        ({}, None),
    ],
)
def test_max_new_tickers_mirror_matches_step4_compiler(value: Any, expected: Any) -> None:
    from investment_orchestrator.workflow.step4_order_compiler import (
        _max_new_tickers_per_week_total as compiler_total,
    )

    assert _max_new_tickers_per_week_total(value) == expected
    # The compiler variant takes the whole settings mapping.
    assert compiler_total({"max_new_tickers_per_week": value}) == expected


def test_preflight_json_serializable() -> None:
    """The artifact must round-trip through JSON exactly as it will be written."""
    result = run_preflight()
    dumped = json.loads(json.dumps(result))
    assert dumped["schema_version"] == PREFLIGHT_SCHEMA_VERSION
