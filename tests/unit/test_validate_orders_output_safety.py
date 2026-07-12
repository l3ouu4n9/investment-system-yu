from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.common.io import write_text
from investment_orchestrator.parsers.portfolio_snapshot_existing_orders import (
    parse_existing_buy_open_orders_summary,
)
from investment_orchestrator.validators.validate_orders_output import validate_orders_output


def settings() -> dict[str, Any]:
    return {
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
        "user_approved_extended_etf_static_list": ["GRID", "CIBR"],
    }


def write_step4(tmp_path: Path, orders_body: str) -> dict[str, Path]:
    template4 = tmp_path / "template4_orders.txt"
    state = tmp_path / "order_state_export.txt"
    summary = tmp_path / "exec_summary.txt"
    write_text(template4, "TEMPLATE4_ORDERS\nSELL_ORDERS\nNONE\nBUY_ORDERS\n" + orders_body)
    write_text(state, "ORDER_STATE_EXPORT\nNONE\n")
    write_text(summary, "TEMPLATE5_EXEC_SUMMARY\nno diagnostics\n")
    return {
        "template4_orders_path": template4,
        "order_state_export_path": state,
        "exec_summary_path": summary,
    }


def run(tmp_path: Path, orders_body: str, **kwargs: Any) -> dict[str, str]:
    paths = write_step4(tmp_path, orders_body)
    return validate_orders_output(**paths, **kwargs)


# --- required BUY row intent -------------------------------------------------


@pytest.mark.parametrize(
    "orders_body",
    [
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00\n",
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent\n",
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=\n",
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=   \n",
    ],
    ids=("missing", "malformed-no-equals", "empty", "whitespace-only"),
)
def test_blank_or_missing_buy_order_intent_fails_before_classification(
    tmp_path: Path,
    orders_body: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"^BUY_ORDERS row\(s\) missing required nonempty order_intent: 1\.$",
    ):
        run(tmp_path, orders_body)


def test_one_blank_intent_rejects_mixed_candidate(tmp_path: Path) -> None:
    body = (
        "ticker=QQQ | plan_type=new_limit_ladder | step_name=L1 | shares=1 | "
        "limit_price=10.00 | order_intent=NEW_ORDER\n"
        "ticker=SMH | plan_type=new_limit_ladder | step_name=L1 | shares=1 | "
        "limit_price=10.00 | order_intent=\n"
    )
    with pytest.raises(ValueError, match="required nonempty order_intent: 2"):
        run(tmp_path, body, strategy_settings=settings())


def test_blank_and_canonical_same_slot_rejects_blank_before_conflict_analysis(
    tmp_path: Path,
) -> None:
    body = (
        "ticker=QQQ | plan_type=new_limit_ladder | step_name=L1 | shares=1 | "
        "limit_price=10.00 | order_intent=NEW_ORDER\n"
        "ticker=QQQ | plan_type=new_limit_ladder | step_name=L1 | shares=1 | "
        "limit_price=10.00 | order_intent=   \n"
    )
    with pytest.raises(ValueError, match="required nonempty order_intent: 2"):
        run(tmp_path, body, strategy_settings=settings())


# --- universe allowlist ------------------------------------------------------


def test_buy_ticker_outside_universe_fails(tmp_path: Path) -> None:
    body = "ticker=ZZZZ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    with pytest.raises(ValueError, match="outside the allowed buy universe"):
        run(tmp_path, body, strategy_settings=settings())


def test_buy_ticker_inside_universe_passes(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    run(tmp_path, body, strategy_settings=settings())


def test_explicit_effective_universe_overrides_settings(tmp_path: Path) -> None:
    # QQQ is in settings but not in the explicit effective universe -> fail.
    body = "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    with pytest.raises(ValueError, match="outside the allowed buy universe"):
        run(tmp_path, body, effective_allowed_buy_universe=["VOO", "SMH"])


def test_cancel_leg_for_out_of_universe_ticker_is_allowed(tmp_path: Path) -> None:
    # Removing an out-of-universe ticker via CANCEL_EXISTING must not be blocked.
    body = "ticker=ZZZZ | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=CANCEL_EXISTING\n"
    run(tmp_path, body, strategy_settings=settings())


def test_no_universe_context_skips_allowlist(tmp_path: Path) -> None:
    # Standalone path (no settings / universe) -> allowlist not enforced.
    body = "ticker=ZZZZ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    run(tmp_path, body)


# --- numeric validity --------------------------------------------------------


def test_malformed_shares_fails(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=abc | limit_price=10.00 | order_intent=NEW_ORDER\n"
    with pytest.raises(ValueError, match="invalid shares"):
        run(tmp_path, body, strategy_settings=settings())


def test_negative_limit_price_fails(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=1 | limit_price=-5.00 | order_intent=NEW_ORDER\n"
    with pytest.raises(ValueError, match="invalid limit_price"):
        run(tmp_path, body, strategy_settings=settings())


def test_negative_shares_fails(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=-2 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    with pytest.raises(ValueError, match="invalid shares"):
        run(tmp_path, body, strategy_settings=settings())


def test_zero_shares_on_submit_fails(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=0 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    with pytest.raises(ValueError, match="non-positive shares"):
        run(tmp_path, body, strategy_settings=settings())


def test_zero_limit_price_fails(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=1 | limit_price=0 | order_intent=NEW_ORDER\n"
    with pytest.raises(ValueError, match="invalid limit_price"):
        run(tmp_path, body, strategy_settings=settings())


def test_valid_positive_numeric_passes(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=3 | limit_price=10.50 | order_intent=NEW_ORDER\n"
    run(tmp_path, body, strategy_settings=settings())


# --- budget ------------------------------------------------------------------


def test_submit_notional_over_hard_cap_fails(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=1000 | limit_price=500.00 | order_intent=NEW_ORDER\n"
    with pytest.raises(ValueError, match="exceeds hard_cap_open_orders_budget"):
        run(tmp_path, body, strategy_settings=settings(), hard_cap_open_orders_budget="38911.29")


def test_submit_notional_over_target_budget_fails(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=10 | limit_price=500.00 | order_intent=NEW_ORDER\n"
    with pytest.raises(ValueError, match="exceeds target_new_buy_budget_this_run"):
        run(tmp_path, body, strategy_settings=settings(), target_new_buy_budget_this_run="1000")


def test_submit_notional_within_budget_passes(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=2 | limit_price=500.00 | order_intent=NEW_ORDER\n"
    run(
        tmp_path,
        body,
        strategy_settings=settings(),
        hard_cap_open_orders_budget="38911.29",
        target_new_buy_budget_this_run="5000",
    )


def test_cancel_legs_do_not_count_toward_budget(tmp_path: Path) -> None:
    # Large cancel-leg notional must not trip the budget ceiling.
    body = "ticker=GRID | step_name=L1 | shares=1000 | limit_price=500.00 | order_intent=CANCEL_EXISTING\n"
    run(tmp_path, body, strategy_settings=settings(), hard_cap_open_orders_budget="1000")


def test_missing_budget_skips_budget_check(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=1000 | limit_price=500.00 | order_intent=NEW_ORDER\n"
    # No budget supplied -> budget check not run (standalone weaker path).
    run(tmp_path, body, strategy_settings=settings())


# --- max new tickers ---------------------------------------------------------


def test_distinct_new_tickers_over_max_fails(tmp_path: Path) -> None:
    body = (
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
        "ticker=SMH | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
        "ticker=IGV | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    )
    with pytest.raises(ValueError, match="max_new_tickers_per_week"):
        run(tmp_path, body, strategy_settings=settings(), max_new_tickers_per_week=2)


def test_same_new_ticker_multistep_counts_once(tmp_path: Path) -> None:
    body = (
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
        "ticker=QQQ | step_name=L2 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    )
    run(tmp_path, body, strategy_settings=settings(), max_new_tickers_per_week=1)


def test_replace_legs_do_not_count_as_new_tickers(tmp_path: Path) -> None:
    body = (
        "ticker=SMH | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=REPLACE_EXISTING_SUBMIT_LEG\n"
        "ticker=IGV | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=REPLACE_EXISTING_SUBMIT_LEG\n"
    )
    run(tmp_path, body, strategy_settings=settings(), max_new_tickers_per_week=0)


# --- duplicate / ladder ------------------------------------------------------


def test_exact_duplicate_row_fails(tmp_path: Path) -> None:
    row = "ticker=QQQ | plan_type=new_limit_ladder | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    with pytest.raises(ValueError, match="duplicate rows"):
        run(tmp_path, row + row, strategy_settings=settings())


def test_multistep_ladder_is_not_duplicate(tmp_path: Path) -> None:
    body = (
        "ticker=QQQ | plan_type=new_limit_ladder | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
        "ticker=QQQ | plan_type=new_limit_ladder | step_name=L2 | shares=1 | limit_price=9.00 | order_intent=NEW_ORDER\n"
    )
    run(tmp_path, body, strategy_settings=settings())


def test_replace_cancel_and_submit_legs_same_ticker_pass(tmp_path: Path) -> None:
    body = (
        "ticker=SMH | plan_type=cancel_existing_ladder_for_replace | step_name=L1 | shares=2 | limit_price=550.00 | order_intent=REPLACE_EXISTING_CANCEL_LEG\n"
        "ticker=SMH | plan_type=new_limit_ladder | step_name=L1 | shares=2 | limit_price=490.00 | order_intent=REPLACE_EXISTING_SUBMIT_LEG\n"
    )
    run(tmp_path, body, strategy_settings=settings())


# --- no-trade ----------------------------------------------------------------


def test_no_trade_none_body_passes(tmp_path: Path) -> None:
    run(tmp_path, "NONE\n", strategy_settings=settings(), hard_cap_open_orders_budget="38911.29", max_new_tickers_per_week=2)


# --- G3: total open-order exposure reconciliation ----------------------------


def plan(
    ticker: str,
    final_action: str,
    compiled: Any,
    target: Any,
    *,
    compile_ready: bool = True,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticker": ticker,
        "final_action": final_action,
        "compile_ready": compile_ready,
        "compiled_open_order_notional": compiled,
        "target_open_order_budget": target,
    }
    return row


def audited(plans: list[dict[str, Any]]) -> dict[str, Any]:
    # Only fields validate_orders_output consumes; no core_deployment_diagnostics
    # so the exec_summary diagnostic cross-check is skipped.
    return {"final_execution_plans": plans}


def test_blank_intent_cannot_evade_target_budget_or_aggregate_ticker_limit(
    tmp_path: Path,
) -> None:
    body = (
        "ticker=QQQ | step_name=L1 | shares=10 | limit_price=500.00 | "
        "order_intent=\n"
    )
    pkt = audited([plan("QQQ", "NEW_ORDER", 5000.00, 5000.00)])
    with pytest.raises(ValueError, match="required nonempty order_intent"):
        run(
            tmp_path,
            body,
            audited_decision_packet=pkt,
            strategy_settings=settings(),
            effective_allowed_buy_universe=["QQQ"],
            hard_cap_open_orders_budget="10000",
            target_new_buy_budget_this_run="1",
            max_new_tickers_per_week=0,
            require_safety_context=True,
        )


def test_blank_intent_cannot_evade_per_bucket_ticker_limit(tmp_path: Path) -> None:
    body = (
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | "
        "order_intent=   \n"
    )
    constrained_settings = settings() | {
        "max_new_tickers_per_week": {
            "base_universe_new_tickers_per_week": 0,
            "extended_etf_sleeve_new_tickers_per_week": 0,
        }
    }
    with pytest.raises(ValueError, match="required nonempty order_intent"):
        run(tmp_path, body, strategy_settings=constrained_settings)


@pytest.mark.parametrize(
    ("final_action", "row_intent"),
    [
        ("NEW_ORDER", "NEW_ORDER"),
        ("NEW_ORDER", "BUY"),
        ("NEW_ORDER", "SUBMIT_BUY"),
        ("NEW_ORDER", "EXECUTE_BUY"),
        ("REPLACE_EXISTING", "REPLACE_EXISTING"),
        ("REPLACE_EXISTING", "REPLACE"),
        ("REPLACE_EXISTING", "REPLACE_EXISTING_CANCEL_LEG"),
        ("REPLACE_EXISTING", "REPLACE_EXISTING_SUBMIT_LEG"),
        ("CANCEL_EXISTING", "CANCEL_EXISTING"),
    ],
)
def test_existing_nonblank_intent_compatibility_is_preserved(
    tmp_path: Path,
    final_action: str,
    row_intent: str,
) -> None:
    body = (
        "ticker=QQQ | plan_type=new_limit_ladder | step_name=L1 | shares=1 | "
        f"limit_price=10.00 | order_intent={row_intent}\n"
    )
    run(
        tmp_path,
        body,
        audited_decision_packet=audited([plan("QQQ", final_action, 10.00, 10.00)]),
    )


@pytest.mark.parametrize("row_intent", ["new_order", "  NEW_ORDER  "])
def test_existing_nonblank_case_and_whitespace_normalization_is_preserved(
    tmp_path: Path,
    row_intent: str,
) -> None:
    body = (
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | "
        f"order_intent={row_intent}\n"
    )
    run(
        tmp_path,
        body,
        audited_decision_packet=audited([plan("QQQ", "NEW_ORDER", 10.00, 10.00)]),
    )


@pytest.mark.parametrize("row_intent", ["UNKNOWN", "null"])
def test_existing_unknown_nonblank_intents_remain_rejected(
    tmp_path: Path,
    row_intent: str,
) -> None:
    body = (
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | "
        f"order_intent={row_intent}\n"
    )
    with pytest.raises(ValueError, match="order_intent values that do not match"):
        run(
            tmp_path,
            body,
            audited_decision_packet=audited([plan("QQQ", "NEW_ORDER", 10.00, 10.00)]),
        )


def test_keep_existing_plan_still_produces_no_buy_row(tmp_path: Path) -> None:
    run(
        tmp_path,
        "NONE\n",
        audited_decision_packet=audited([plan("QQQ", "KEEP_EXISTING", 10.00, 10.00)]),
    )


def test_total_exposure_within_cap_passes(tmp_path: Path) -> None:
    pkt = audited([plan("QQQ", "KEEP_EXISTING", 1000.0, 1200.0), plan("VOO", "KEEP_EXISTING", 2000.0, 2200.0)])
    run(tmp_path, "NONE\n", audited_decision_packet=pkt, hard_cap_open_orders_budget="5000")


def test_total_target_over_hard_cap_fails(tmp_path: Path) -> None:
    pkt = audited([plan("QQQ", "KEEP_EXISTING", 1000.0, 3000.0), plan("VOO", "KEEP_EXISTING", 1000.0, 3000.0)])
    with pytest.raises(ValueError, match="exceeds hard_cap_open_orders_budget"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, hard_cap_open_orders_budget="5000")


def test_total_compiled_over_total_target_fails(tmp_path: Path) -> None:
    pkt = audited([plan("QQQ", "KEEP_EXISTING", 3500.0, 3000.0)])
    with pytest.raises(ValueError, match="exceeds .*total target open-order budget"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, hard_cap_open_orders_budget="5000")


def test_keep_existing_notional_counted_pushes_over_cap_with_zero_buy_rows(tmp_path: Path) -> None:
    # The headline G1 gap: no BUY rows at all, but KEEP_EXISTING existing
    # notional alone exceeds the cap -> must fail (submit-side floor would pass).
    pkt = audited([plan("QQQ", "KEEP_EXISTING", 4000.0, 4000.0), plan("VOO", "KEEP_EXISTING", 4000.0, 4000.0)])
    with pytest.raises(ValueError, match="exceeds hard_cap_open_orders_budget"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, hard_cap_open_orders_budget="5000")


def test_cancel_contributes_zero(tmp_path: Path) -> None:
    pkt = audited([plan("QQQ", "KEEP_EXISTING", 1000.0, 1000.0), plan("CIBR", "CANCEL_EXISTING", 0.0, 0.0)])
    run(tmp_path, "NONE\n", audited_decision_packet=pkt, hard_cap_open_orders_budget="1000")


def test_new_order_submit_rows_cross_check_passes(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    pkt = audited([plan("QQQ", "NEW_ORDER", 20.00, 25.00)])
    run(tmp_path, body, strategy_settings=settings(), audited_decision_packet=pkt, hard_cap_open_orders_budget="1000")


def test_replace_existing_submit_rows_cross_check_passes(tmp_path: Path) -> None:
    body = "ticker=SMH | step_name=L1 | shares=2 | limit_price=9.98 | order_intent=REPLACE_EXISTING_SUBMIT_LEG\n"
    pkt = audited([plan("SMH", "REPLACE_EXISTING", 19.96, 25.00)])
    run(tmp_path, body, strategy_settings=settings(), audited_decision_packet=pkt, hard_cap_open_orders_budget="1000")


def test_ladder_submit_rows_summed_before_cross_check(tmp_path: Path) -> None:
    body = (
        "ticker=QQQ | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=NEW_ORDER\n"
        "ticker=QQQ | step_name=L2 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    )
    pkt = audited([plan("QQQ", "NEW_ORDER", 30.00, 35.00)])
    run(tmp_path, body, strategy_settings=settings(), audited_decision_packet=pkt, hard_cap_open_orders_budget="1000")


def test_submit_row_mismatch_fails(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    # Plan claims 50.00 compiled but the submit rows total only 20.00.
    pkt = audited([plan("QQQ", "NEW_ORDER", 50.00, 60.00)])
    with pytest.raises(ValueError, match="does not match .*compiled_open_order_notional"):
        run(tmp_path, body, strategy_settings=settings(), audited_decision_packet=pkt, hard_cap_open_orders_budget="1000")


def test_malformed_compiled_notional_fails_closed(tmp_path: Path) -> None:
    pkt = audited([plan("QQQ", "KEEP_EXISTING", "abc", 1000.0)])
    with pytest.raises(ValueError, match="compiled_open_order_notional is missing or non-numeric"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, hard_cap_open_orders_budget="5000")


def test_missing_target_budget_fails_closed(tmp_path: Path) -> None:
    pkt = audited([{"ticker": "QQQ", "final_action": "KEEP_EXISTING", "compile_ready": True, "compiled_open_order_notional": 1000.0}])
    with pytest.raises(ValueError, match="target_open_order_budget is missing or non-numeric"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, hard_cap_open_orders_budget="5000")


def test_negative_compiled_notional_fails_closed(tmp_path: Path) -> None:
    pkt = audited([plan("QQQ", "KEEP_EXISTING", -5.0, 1000.0)])
    with pytest.raises(ValueError, match="compiled_open_order_notional must be non-negative"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, hard_cap_open_orders_budget="5000")


def test_no_trade_empty_final_execution_plans_passes(tmp_path: Path) -> None:
    run(tmp_path, "NONE\n", audited_decision_packet=audited([]), hard_cap_open_orders_budget="1000")


def test_cross_check_within_cent_tolerance_passes(tmp_path: Path) -> None:
    # compiled 20.01 vs submit 20.00 -> diff 0.01 == tolerance -> passes.
    body = "ticker=QQQ | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    pkt = audited([plan("QQQ", "NEW_ORDER", 20.01, 25.00)])
    run(tmp_path, body, strategy_settings=settings(), audited_decision_packet=pkt, hard_cap_open_orders_budget="1000")


def test_cross_check_beyond_cent_tolerance_fails(tmp_path: Path) -> None:
    # compiled 20.02 vs submit 20.00 -> diff 0.02 > tolerance -> fails.
    body = "ticker=QQQ | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    pkt = audited([plan("QQQ", "NEW_ORDER", 20.02, 25.00)])
    with pytest.raises(ValueError, match="does not match"):
        run(tmp_path, body, strategy_settings=settings(), audited_decision_packet=pkt, hard_cap_open_orders_budget="1000")


def test_g3_skipped_without_hard_cap(tmp_path: Path) -> None:
    # Audited packet present but no hard cap -> G3 reconciliation is skipped
    # (over-target plan would otherwise fail); G1 checks still run.
    pkt = audited([plan("QQQ", "KEEP_EXISTING", 9999.0, 1.0)])
    run(tmp_path, "NONE\n", audited_decision_packet=pkt)


# --- G4: KEEP_EXISTING independent verification vs portfolio snapshot (2a) ----

_SNAPSHOT_HEADER = (
    "TICKER | budget | compiled_open_order_notional | residual | template_id | "
    "anchor | asof | refresh | hi | lo | steps | live_order_steps_summary | live_order_qtys_summary"
)


def snapshot_2a(rows: str) -> Any:
    text = (
        "(2a) existing_buy_open_orders_summary\n"
        + _SNAPSHOT_HEADER
        + "\n"
        + rows
        + "\n(2b) sell_open_orders\nNONE\n"
    )
    return parse_existing_buy_open_orders_summary(text)


def keep_plan(ticker: str, *, budget: Any, compiled: Any) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "final_action": "KEEP_EXISTING",
        "compile_ready": True,
        "existing_open_order_budget": budget,
        "compiled_open_order_notional": compiled,
        "target_open_order_budget": budget,
    }


# A snapshot row: QQQ budget 1983.65, stated 699.36, reconstructed L2@699.36 x1 = 699.36.
QQQ_ROW = "QQQ | 1983.65 | 699.36 | 1284.29 | T4-E | 744.00 | 2026-06-15 |  | 699.36 | 699.36 | 1 | L2@699.36 | L2:1"


def test_keep_existing_matching_snapshot_passes(tmp_path: Path) -> None:
    pkt = audited([keep_plan("QQQ", budget=1983.65, compiled=699.36)])
    run(
        tmp_path,
        "NONE\n",
        audited_decision_packet=pkt,
        existing_buy_open_orders=snapshot_2a(QQQ_ROW),
    )


def test_keep_existing_missing_from_snapshot_fails_closed(tmp_path: Path) -> None:
    pkt = audited([keep_plan("SMH", budget=100.0, compiled=100.0)])
    with pytest.raises(ValueError, match="not present in portfolio snapshot"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, existing_buy_open_orders=snapshot_2a(QQQ_ROW))


def test_keep_existing_section_absent_with_keep_plans_fails_closed(tmp_path: Path) -> None:
    pkt = audited([keep_plan("QQQ", budget=1983.65, compiled=699.36)])
    no_section = parse_existing_buy_open_orders_summary("no (2a) section present\n")
    with pytest.raises(ValueError, match="section \\(2a\\) .* is missing or empty"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, existing_buy_open_orders=no_section)


def test_keep_existing_compiled_notional_mismatch_fails(tmp_path: Path) -> None:
    pkt = audited([keep_plan("QQQ", budget=1983.65, compiled=500.00)])
    with pytest.raises(ValueError, match="compiled_open_order_notional .* does not match"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, existing_buy_open_orders=snapshot_2a(QQQ_ROW))


def test_keep_existing_budget_mismatch_fails(tmp_path: Path) -> None:
    pkt = audited([keep_plan("QQQ", budget=9999.00, compiled=699.36)])
    with pytest.raises(ValueError, match="existing_open_order_budget .* does not match"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, existing_buy_open_orders=snapshot_2a(QQQ_ROW))


def test_snapshot_stated_vs_reconstructed_mismatch_fails(tmp_path: Path) -> None:
    # stated col3 = 800.00 but reconstructed L2@699.36 x1 = 699.36 -> snapshot internal mismatch.
    bad_row = "QQQ | 1983.65 | 800.00 | 1284.29 | T4-E | 744.00 | 2026-06-15 |  | 699.36 | 699.36 | 1 | L2@699.36 | L2:1"
    pkt = audited([keep_plan("QQQ", budget=1983.65, compiled=800.00)])
    with pytest.raises(ValueError, match="stated compiled notional .* does not match reconstructed"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, existing_buy_open_orders=snapshot_2a(bad_row))


def test_non_keep_actions_not_forced_through_snapshot_check(tmp_path: Path) -> None:
    # NEW_ORDER + CANCEL plans must not require a (2a) snapshot row.
    pkt = audited(
        [
            plan("CIBR", "CANCEL_EXISTING", 0.0, 0.0),
            {
                "ticker": "QQQ",
                "final_action": "NEW_ORDER",
                "compile_ready": True,
                "compiled_open_order_notional": 20.00,
                "target_open_order_budget": 25.00,
            },
        ]
    )
    body = "ticker=QQQ | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    # Snapshot has no QQQ/CIBR existing rows; G4 must not fire for NEW/CANCEL.
    run(
        tmp_path,
        body,
        strategy_settings=settings(),
        audited_decision_packet=pkt,
        hard_cap_open_orders_budget="1000",
        existing_buy_open_orders=snapshot_2a("SMH | 100 | 50 | 50 | T | 1 | 2026-01-01 |  | 1 | 1 | 1 | L1@50.00 | L1:1"),
    )


def test_g4_skipped_when_existing_orders_context_none(tmp_path: Path) -> None:
    # Backward-compat: no existing_buy_open_orders context -> G4 skipped even with
    # a KEEP plan that has no snapshot to verify against.
    pkt = audited([keep_plan("QQQ", budget=1983.65, compiled=699.36)])
    run(tmp_path, "NONE\n", audited_decision_packet=pkt, hard_cap_open_orders_budget="5000")


def test_keep_existing_parse_blocked_snapshot_row_fails_closed(tmp_path: Path) -> None:
    # Wrong column count for QQQ -> data_gap -> KEEP verification fails closed.
    bad_row = "QQQ | 1983.65 | 699.36 | T4-E | L2@699.36 | L2:1"
    pkt = audited([keep_plan("QQQ", budget=1983.65, compiled=699.36)])
    with pytest.raises(ValueError, match="parse-blocked / data-gapped"):
        run(tmp_path, "NONE\n", audited_decision_packet=pkt, existing_buy_open_orders=snapshot_2a(bad_row))


# --- per-bucket max_new_tickers_per_week (base vs extended) -------------------


def bucket_settings(
    *,
    base_limit: int = 0,
    extended_limit: int = 2,
    core: list[str] | None = None,
    satellite: list[str] | None = None,
    approved: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "core_universe": core if core is not None else ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": satellite if satellite is not None else ["SMH", "IGV"],
        "user_approved_extended_etf_static_list": approved
        if approved is not None
        else ["GRID", "CIBR", "BOTZ"],
        "max_new_tickers_per_week": {
            "base_universe_new_tickers_per_week": base_limit,
            "extended_etf_sleeve_new_tickers_per_week": extended_limit,
        },
    }


def new_order_row(ticker: str, step: str = "L1") -> str:
    return f"ticker={ticker} | step_name={step} | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"


def test_per_bucket_new_base_ticker_fails_when_base_limit_zero_even_if_aggregate_passes(
    tmp_path: Path,
) -> None:
    # base=0, extended=2 -> aggregate sum 2 would pass for 1 new ticker, but the
    # per-bucket base ceiling (0) is violated.
    with pytest.raises(ValueError, match="base_universe_new_tickers_per_week=0"):
        run(
            tmp_path,
            new_order_row("QQQ"),
            strategy_settings=bucket_settings(base_limit=0, extended_limit=2),
            max_new_tickers_per_week=2,  # aggregate would pass (1 <= 2)
        )


def test_per_bucket_new_extended_within_limit_passes(tmp_path: Path) -> None:
    run(tmp_path, new_order_row("GRID"), strategy_settings=bucket_settings(base_limit=0, extended_limit=2))


def test_per_bucket_extended_over_limit_fails(tmp_path: Path) -> None:
    body = new_order_row("GRID") + new_order_row("CIBR") + new_order_row("BOTZ")
    with pytest.raises(ValueError, match="extended_etf_sleeve_new_tickers_per_week=2"):
        run(tmp_path, body, strategy_settings=bucket_settings(base_limit=0, extended_limit=2))


def test_per_bucket_base_within_nonzero_limit_passes(tmp_path: Path) -> None:
    run(tmp_path, new_order_row("QQQ"), strategy_settings=bucket_settings(base_limit=1, extended_limit=0))


def test_per_bucket_ticker_in_neither_list_fails_closed(tmp_path: Path) -> None:
    # ZZZZ passes the G1 universe allowlist via an explicit effective universe,
    # but is in neither base nor extended settings list -> per-bucket fails closed.
    with pytest.raises(ValueError, match="not classifiable as base or extended"):
        run(
            tmp_path,
            new_order_row("ZZZZ"),
            strategy_settings=bucket_settings(base_limit=5, extended_limit=5),
            effective_allowed_buy_universe=["ZZZZ"],
        )


def test_per_bucket_ticker_in_both_lists_classified_as_base(tmp_path: Path) -> None:
    # GRID in both core and approved; base=0 -> counted as base -> fails on base.
    with pytest.raises(ValueError, match="new base ticker"):
        run(
            tmp_path,
            new_order_row("GRID"),
            strategy_settings=bucket_settings(
                base_limit=0,
                extended_limit=5,
                core=["GRID", "QQQ"],
                approved=["GRID"],
            ),
        )


def test_per_bucket_ladder_rows_same_ticker_count_once(tmp_path: Path) -> None:
    body = new_order_row("GRID", "L1") + new_order_row("GRID", "L2")
    run(tmp_path, body, strategy_settings=bucket_settings(base_limit=0, extended_limit=1))


def test_per_bucket_replace_cancel_keep_do_not_count(tmp_path: Path) -> None:
    body = (
        "ticker=GRID | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=REPLACE_EXISTING_SUBMIT_LEG\n"
        "ticker=CIBR | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=CANCEL_EXISTING\n"
    )
    run(tmp_path, body, strategy_settings=bucket_settings(base_limit=0, extended_limit=0))


def test_per_bucket_no_trade_passes(tmp_path: Path) -> None:
    run(tmp_path, "NONE\n", strategy_settings=bucket_settings(base_limit=0, extended_limit=0))


def test_per_bucket_skipped_when_strategy_settings_none(tmp_path: Path) -> None:
    # No strategy_settings -> per-bucket skipped (and G1 universe also skipped),
    # so a would-be base violation passes (backward compatible).
    run(tmp_path, new_order_row("QQQ"))


def test_per_bucket_malformed_settings_fail_closed(tmp_path: Path) -> None:
    bad = bucket_settings(base_limit=0, extended_limit=2)
    bad["max_new_tickers_per_week"] = "oops"
    with pytest.raises(ValueError, match="max_new_tickers_per_week must be a mapping"):
        run(tmp_path, new_order_row("GRID"), strategy_settings=bad)


def test_per_bucket_missing_subkey_fails_closed(tmp_path: Path) -> None:
    bad = bucket_settings(base_limit=0, extended_limit=2)
    bad["max_new_tickers_per_week"] = {"base_universe_new_tickers_per_week": 0}  # extended missing
    with pytest.raises(ValueError, match="extended_etf_sleeve_new_tickers_per_week must be an integer"):
        run(tmp_path, new_order_row("GRID"), strategy_settings=bad)


def test_aggregate_check_still_works_for_legacy_int_only_callers(tmp_path: Path) -> None:
    # No strategy_settings -> per-bucket skipped; legacy aggregate int still enforced.
    body = new_order_row("QQQ") + new_order_row("SMH")
    with pytest.raises(ValueError, match="exceeding max_new_tickers_per_week=1"):
        run(tmp_path, body, max_new_tickers_per_week=1)


# --- conflicting-action detection (always-on, additive to exact-duplicate) ----


def test_net_new_and_standalone_cancel_same_ticker_fails(tmp_path: Path) -> None:
    body = (
        "ticker=QQQ | plan_type=new_limit_ladder | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
        "ticker=QQQ | plan_type=cancel_existing_ladder | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=CANCEL_EXISTING\n"
    )
    with pytest.raises(ValueError, match="conflicting actions for the same ticker"):
        run(tmp_path, body, strategy_settings=settings())


def test_same_slot_inconsistent_intent_fails(tmp_path: Path) -> None:
    # Same (ticker, plan_type, step) slot with two distinct intents -> conflict.
    body = (
        "ticker=QQQ | plan_type=new_limit_ladder | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
        "ticker=QQQ | plan_type=new_limit_ladder | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=SUBMIT_BUY\n"
    )
    with pytest.raises(ValueError, match="conflicting order_intent values for the same ladder slot"):
        run(tmp_path, body, strategy_settings=settings())


def test_replace_cancel_and_submit_legs_not_flagged_as_action_conflict(tmp_path: Path) -> None:
    # REPLACE_* legs are a coordinated pair (not a plain CANCEL_EXISTING) -> allowed.
    body = (
        "ticker=SMH | plan_type=cancel_existing_ladder_for_replace | step_name=L1 | shares=2 | limit_price=550.00 | order_intent=REPLACE_EXISTING_CANCEL_LEG\n"
        "ticker=SMH | plan_type=new_limit_ladder | step_name=L1 | shares=2 | limit_price=490.00 | order_intent=REPLACE_EXISTING_SUBMIT_LEG\n"
    )
    run(tmp_path, body, strategy_settings=settings())


def test_cancel_and_new_for_different_tickers_pass(tmp_path: Path) -> None:
    body = (
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER\n"
        "ticker=CIBR | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=CANCEL_EXISTING\n"
    )
    run(tmp_path, body, strategy_settings=settings())


def test_standalone_cancel_only_is_not_action_conflict(tmp_path: Path) -> None:
    body = "ticker=GRID | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=CANCEL_EXISTING\n"
    run(tmp_path, body, strategy_settings=settings())


# --- require_safety_context (opt-in fail-closed when context missing) ---------


def test_require_safety_context_missing_hard_cap_with_buy_rows_fails(tmp_path: Path) -> None:
    body = new_order_row("QQQ")
    # Universe resolves (QQQ in settings) but no hard cap supplied -> fail closed.
    with pytest.raises(ValueError, match="hard_cap_open_orders_budget was not supplied"):
        run(tmp_path, body, strategy_settings=settings(), require_safety_context=True)


def test_require_safety_context_missing_universe_with_buy_rows_fails(tmp_path: Path) -> None:
    body = new_order_row("QQQ")
    # No settings / explicit universe -> allowed universe unresolvable -> fail closed.
    with pytest.raises(ValueError, match="allowed buy universe could not be resolved"):
        run(tmp_path, body, hard_cap_open_orders_budget="5000", require_safety_context=True)


def test_require_safety_context_missing_max_tickers_with_net_new_fails(tmp_path: Path) -> None:
    body = new_order_row("QQQ")
    # Universe + hard cap present, but no aggregate int and no per-bucket mapping.
    with pytest.raises(ValueError, match="no max_new_tickers_per_week ceiling was supplied"):
        run(
            tmp_path,
            body,
            strategy_settings=settings(),
            hard_cap_open_orders_budget="5000",
            require_safety_context=True,
        )


def test_require_safety_context_full_context_passes(tmp_path: Path) -> None:
    body = "ticker=QQQ | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=NEW_ORDER\n"
    run(
        tmp_path,
        body,
        strategy_settings=settings(),
        hard_cap_open_orders_budget="5000",
        target_new_buy_budget_this_run="5000",
        max_new_tickers_per_week=2,
        require_safety_context=True,
    )


def test_require_safety_context_no_buy_rows_passes_without_budget(tmp_path: Path) -> None:
    # No submit rows at all -> no budget / universe / max-ticker context required.
    run(tmp_path, "NONE\n", require_safety_context=True)


def test_require_safety_context_cancel_only_passes_without_budget(tmp_path: Path) -> None:
    # Cancel-only output has no submit/new legs -> no budget/universe required.
    body = "ticker=GRID | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=CANCEL_EXISTING\n"
    run(tmp_path, body, strategy_settings=settings(), require_safety_context=True)


def test_without_require_safety_context_missing_budget_still_skips(tmp_path: Path) -> None:
    # Backward-compat: default (require_safety_context=False) keeps the lenient
    # skip-when-context-missing behavior for standalone callers.
    body = new_order_row("QQQ")
    run(tmp_path, body, strategy_settings=settings())


# --- G5: target_new_buy_budget_this_run = net-new-only ceiling ----------------


def replace_submit_row(ticker: str, shares: int, price: str, step: str = "L1") -> str:
    return (
        f"ticker={ticker} | step_name={step} | shares={shares} | limit_price={price} "
        "| order_intent=REPLACE_EXISTING_SUBMIT_LEG\n"
    )


def net_new_row(ticker: str, shares: int, price: str, step: str = "L1") -> str:
    return (
        f"ticker={ticker} | step_name={step} | shares={shares} | limit_price={price} "
        "| order_intent=NEW_ORDER\n"
    )


def test_net_new_notional_over_target_budget_fails(tmp_path: Path) -> None:
    # Net-new notional 10*500 = 5000 > target 1000 -> fail (net-new ceiling).
    body = net_new_row("QQQ", 10, "500.00")
    with pytest.raises(ValueError, match="net-new notional .* exceeds target_new_buy_budget_this_run"):
        run(tmp_path, body, strategy_settings=settings(), target_new_buy_budget_this_run="1000")


def test_net_new_notional_within_target_budget_passes(tmp_path: Path) -> None:
    # Net-new notional 2*500 = 1000 <= target 1000 -> pass.
    body = net_new_row("QQQ", 2, "500.00")
    run(tmp_path, body, strategy_settings=settings(), target_new_buy_budget_this_run="1000")


def test_replacement_only_notional_over_target_budget_passes(tmp_path: Path) -> None:
    # Large replacement notional (50*500 = 25000) but target budget tiny (100):
    # replacements are budget-neutral reanchors -> not counted toward target -> pass.
    body = replace_submit_row("SMH", 50, "500.00")
    run(tmp_path, body, strategy_settings=settings(), target_new_buy_budget_this_run="100")


def test_replacement_only_notional_still_subject_to_hard_cap(tmp_path: Path) -> None:
    # The SAME large replacement notional IS bounded by the hard cap (submit-side).
    body = replace_submit_row("SMH", 50, "500.00")
    with pytest.raises(ValueError, match="exceeds hard_cap_open_orders_budget"):
        run(tmp_path, body, strategy_settings=settings(), hard_cap_open_orders_budget="1000")


def test_mixed_replacement_and_net_new_counts_only_net_new_toward_target(tmp_path: Path) -> None:
    # Net-new QQQ 1*100 = 100; replacement SMH 100*100 = 10000.
    # Target 500: net-new (100) <= 500 passes; if replacement counted, 10100 > 500
    # would have failed. Hard cap set high so it does not bind here.
    body = net_new_row("QQQ", 1, "100.00") + replace_submit_row("SMH", 100, "100.00")
    run(
        tmp_path,
        body,
        strategy_settings=settings(),
        target_new_buy_budget_this_run="500",
        hard_cap_open_orders_budget="50000",
    )


def test_hard_cap_still_applies_independently_of_target(tmp_path: Path) -> None:
    # Net-new 100*500 = 50000: under target (60000) but over hard cap (1000).
    # Hard cap must still fire (checked against submit-side notional).
    body = net_new_row("QQQ", 100, "500.00")
    with pytest.raises(ValueError, match="exceeds hard_cap_open_orders_budget"):
        run(
            tmp_path,
            body,
            strategy_settings=settings(),
            target_new_buy_budget_this_run="60000",
            hard_cap_open_orders_budget="1000",
        )


def test_cancel_only_does_not_count_toward_target_budget(tmp_path: Path) -> None:
    body = "ticker=GRID | step_name=L1 | shares=1000 | limit_price=500.00 | order_intent=CANCEL_EXISTING\n"
    run(tmp_path, body, strategy_settings=settings(), target_new_buy_budget_this_run="1")


# --- G5: require_safety_context fail-closed on missing target budget ----------


def test_require_safety_context_missing_target_budget_with_net_new_fails(tmp_path: Path) -> None:
    # Universe + hard cap + max-tickers present, but no target budget and net-new rows.
    body = new_order_row("QQQ")
    with pytest.raises(ValueError, match="target_new_buy_budget_this_run was not supplied"):
        run(
            tmp_path,
            body,
            strategy_settings=settings(),
            hard_cap_open_orders_budget="5000",
            max_new_tickers_per_week=2,
            require_safety_context=True,
        )


def test_require_safety_context_no_buy_rows_passes_without_target_budget(tmp_path: Path) -> None:
    run(
        tmp_path,
        "NONE\n",
        strategy_settings=settings(),
        hard_cap_open_orders_budget="5000",
        require_safety_context=True,
    )


def test_require_safety_context_cancel_only_passes_without_target_budget(tmp_path: Path) -> None:
    body = "ticker=GRID | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=CANCEL_EXISTING\n"
    run(
        tmp_path,
        body,
        strategy_settings=settings(),
        hard_cap_open_orders_budget="5000",
        require_safety_context=True,
    )


def test_require_safety_context_replacement_only_passes_without_target_or_max_tickers(
    tmp_path: Path,
) -> None:
    # Replacement-only: submit rows present (needs universe + hard cap), but no
    # net-new rows -> neither max_new_tickers_per_week nor target budget required.
    body = replace_submit_row("SMH", 1, "10.00")
    run(
        tmp_path,
        body,
        strategy_settings=settings(),
        hard_cap_open_orders_budget="5000",
        require_safety_context=True,
    )


def test_without_require_safety_context_missing_target_budget_still_skips(tmp_path: Path) -> None:
    # Backward-compat: default lenient mode does not require target budget even with
    # net-new rows (the budget check is simply skipped when no budget is supplied).
    body = new_order_row("QQQ")
    run(tmp_path, body, strategy_settings=settings())
