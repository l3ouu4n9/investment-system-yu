from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.common.io import write_text
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
