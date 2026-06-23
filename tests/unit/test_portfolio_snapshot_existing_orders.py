from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from investment_orchestrator.parsers.portfolio_snapshot_existing_orders import (
    parse_existing_buy_open_orders_summary,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "portfolio_snapshots"


HEADER = (
    "TICKER | budget | compiled_open_order_notional(optional) | residual_cash_not_allocated(optional) | "
    "template_id | anchor_baseline_last_close | anchor_price_asof | last_refresh_date_et(optional) | "
    "highest_live_limit(optional) | lowest_live_limit(optional) | live_step_count(optional) | "
    "live_order_steps_summary(optional) | live_order_qtys_summary(optional)"
)


def section(rows: str) -> str:
    return (
        "(2a) existing_buy_open_orders_summary\n"
        "- some prose rule line that must be ignored\n"
        + HEADER + "\n"
        + rows
        + "\n(2b) sell_open_orders\nNONE\n"
    )


def test_parser_extracts_valid_rows() -> None:
    text = section(
        "QQQ | 1983.65 | 699.36 | 1284.29 | T4-E | 744.00 | 2026-06-15 |  | 699.36 | 699.36 | 1 | L2@699.36 | L2:1\n"
        "VOO | 7757.08 | 6436.08 | 1321.00 | T4-B | 697.30 | 2026-06-10 | | 658.95 | 613.62 | 3 | L2@658.95;L3@638.03;L4@613.62 | L2:5;L3:3;L4:2"
    )
    result = parse_existing_buy_open_orders_summary(text)

    assert result.section_present is True
    assert set(result.orders) == {"QQQ", "VOO"}
    qqq = result.orders["QQQ"]
    assert qqq.budget == Decimal("1983.65")
    assert qqq.stated_compiled_notional == Decimal("699.36")
    assert qqq.reconstructed_notional == Decimal("699.36")
    assert qqq.data_gap is False


def test_parser_ignores_header_and_prose_and_section_scope() -> None:
    text = section("QQQ | 1983.65 | 699.36 | 1.0 | T4-E | 744 | 2026-06-15 |  | 1 | 1 | 1 | L2@699.36 | L2:1")
    # A ticker-like row after (2b) must not be picked up.
    text += "ZZZZ | 1 | 1 | 1 | T | 1 | 2026-01-01 |  | 1 | 1 | 1 | L1@1 | L1:1\n"
    result = parse_existing_buy_open_orders_summary(text)
    assert set(result.orders) == {"QQQ"}  # TICKER header + ZZZZ-after-(2b) excluded


def test_parser_strips_comma_formatted_budgets() -> None:
    text = section("QQQ | 2,500.00 | 1,000.00 | 0 | T4-E | 744 | 2026-06-15 |  | 1 | 1 | 1 | L1@500.00 | L1:2")
    qqq = parse_existing_buy_open_orders_summary(text).orders["QQQ"]
    assert qqq.budget == Decimal("2500.00")
    assert qqq.stated_compiled_notional == Decimal("1000.00")
    assert qqq.reconstructed_notional == Decimal("1000.00")  # 500.00 * 2


def test_parser_handles_dynamic_step_names_starter_and_ladders() -> None:
    text = section(
        "QQQ | 14132.17 | 11910.86 | 2221.31 | T4-E | 730.28 | 2026-05-26 | | 722.98 | 635.34 | 5 | "
        "starter@722.98;L1@708.37;L2@686.46;L3@660.90;L4@635.34 | starter:7;L1:4;L2:3;L3:2;L4:1"
    )
    qqq = parse_existing_buy_open_orders_summary(text).orders["QQQ"]
    expected = (
        Decimal("722.98") * 7
        + Decimal("708.37") * 4
        + Decimal("686.46") * 3
        + Decimal("660.90") * 2
        + Decimal("635.34") * 1
    )
    assert qqq.reconstructed_notional == expected
    assert qqq.stated_compiled_notional == Decimal("11910.86")
    assert qqq.data_gap is False


def test_parser_flags_wrong_column_count_as_data_gap() -> None:
    text = section("QQQ | 100 | 50 | T4-E | 744 | 2026-06-15 | L1@50 | L1:1")  # too few columns
    qqq = parse_existing_buy_open_orders_summary(text).orders["QQQ"]
    assert qqq.data_gap is True
    assert any("PARSE_BLOCKED" in d for d in qqq.diagnostics)


def test_parser_flags_step_qty_name_mismatch_as_data_gap() -> None:
    text = section("QQQ | 100 | 50 | 0 | T4-E | 744 | 2026-06-15 |  | 1 | 1 | 1 | L1@50.00 | L2:1")
    qqq = parse_existing_buy_open_orders_summary(text).orders["QQQ"]
    assert qqq.reconstructed_notional is None
    assert qqq.data_gap is True


def test_parser_missing_section_returns_not_present() -> None:
    result = parse_existing_buy_open_orders_summary("no relevant section here\n")
    assert result.section_present is False
    assert result.orders == {}


def test_parser_omitted_live_structure_is_not_data_gap() -> None:
    # Optional step/qty columns empty: reconstructed=None but NOT a parse failure.
    text = section("QQQ | 100 | 50 | 50 | T4-E | 744 | 2026-06-15 |  |  |  |  |  | ")
    qqq = parse_existing_buy_open_orders_summary(text).orders["QQQ"]
    assert qqq.reconstructed_notional is None
    assert qqq.data_gap is False
    assert qqq.stated_compiled_notional == Decimal("50")


# Representative historical (2a) sections, captured from real portfolio snapshot
# revisions into checked-in fixtures (hermetic — no git history required at test time).
_HISTORICAL_FIXTURES = (
    ("snapshot_2a_base_only.txt", {"QQQ", "VOO", "SMH"}),
    ("snapshot_2a_base_and_cibr.txt", {"QQQ", "VOO", "SMH", "CIBR"}),
    ("snapshot_2a_starter_and_extended.txt", {"QQQ", "VOO", "SMH", "CIBR", "GRID"}),
    ("snapshot_2a_empty.txt", set()),
)


@pytest.mark.parametrize("filename, expected_tickers", _HISTORICAL_FIXTURES)
def test_parser_validates_representative_historical_fixtures(
    filename: str,
    expected_tickers: set[str],
) -> None:
    text = (FIXTURE_DIR / filename).read_text(encoding="utf-8")
    result = parse_existing_buy_open_orders_summary(text)

    assert result.section_present is True
    assert set(result.orders) == expected_tickers
    for ticker, order in result.orders.items():
        # No row in a real historical snapshot should be a parse failure.
        assert not order.data_gap, f"{filename}:{ticker} unexpectedly data-gapped: {order.diagnostics}"
        # Where both are present, the stated and reconstructed notional must agree.
        if order.stated_compiled_notional is not None and order.reconstructed_notional is not None:
            assert order.stated_compiled_notional == order.reconstructed_notional, (
                f"{filename}:{ticker} stated != reconstructed"
            )


def test_historical_fixtures_cover_dynamic_steps_empty_and_extended() -> None:
    # (comma-formatted budgets are covered hermetically by
    # test_parser_strips_comma_formatted_budgets above.)
    # starter + L1-L4 dynamic step names and base+extended tickers.
    starter = parse_existing_buy_open_orders_summary(
        (FIXTURE_DIR / "snapshot_2a_starter_and_extended.txt").read_text(encoding="utf-8")
    )
    assert "starter" in starter.orders["QQQ"].steps
    assert {"CIBR", "GRID"} <= set(starter.orders)
    # Empty (2a): section present, zero data rows.
    empty = parse_existing_buy_open_orders_summary(
        (FIXTURE_DIR / "snapshot_2a_empty.txt").read_text(encoding="utf-8")
    )
    assert empty.section_present is True
    assert empty.orders == {}
