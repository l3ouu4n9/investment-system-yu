"""Pure parser for portfolio snapshot section (2a) existing_buy_open_orders_summary.

Section (2a) is the operator-maintained SSOT for buy-side existing open orders.
It is a strict pipe-delimited 13-column block embedded in the otherwise free-text
``inputs/current/portfolio_snapshot.txt``. This module extracts it deterministically
so a downstream validator can independently verify KEEP_EXISTING open-order
notional (PR G4) instead of trusting only the audited decision packet.

The parser is pure (text in, structured data out), never raises, and flags
malformed rows as data gaps rather than guessing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any


SECTION_2A_START_RE = re.compile(r"^\(2a\)\s*existing_buy_open_orders_summary", re.MULTILINE)
SECTION_2B_START_RE = re.compile(r"^\(2b\)\s*", re.MULTILINE)
# A data row begins with an UPPERCASE ticker token (letters/dot) followed by " | ".
_TICKER_ROW_RE = re.compile(r"^[A-Z][A-Z.]*\s*\|")
EXPECTED_COLUMN_COUNT = 13
# Positional column indices (0-based) within a (2a) data row.
_COL_TICKER = 0
_COL_BUDGET = 1
_COL_COMPILED_NOTIONAL = 2
_COL_RESIDUAL = 3
_COL_TEMPLATE_ID = 4
_COL_STEPS_SUMMARY = 11
_COL_QTYS_SUMMARY = 12


@dataclass(frozen=True)
class ExistingBuyOpenOrder:
    """One parsed (2a) ticker row."""

    ticker: str
    budget: Decimal | None
    stated_compiled_notional: Decimal | None
    reconstructed_notional: Decimal | None
    steps: dict[str, Decimal] = field(default_factory=dict)
    qtys: dict[str, Decimal] = field(default_factory=dict)
    data_gap: bool = False
    diagnostics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExistingBuyOpenOrdersParseResult:
    """Result of parsing section (2a)."""

    section_present: bool
    orders: dict[str, ExistingBuyOpenOrder] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)

    def get(self, ticker: str) -> ExistingBuyOpenOrder | None:
        return self.orders.get(_normalize_ticker(ticker))


def parse_existing_buy_open_orders_summary(
    portfolio_snapshot_text: str,
) -> ExistingBuyOpenOrdersParseResult:
    """Parse section (2a) of a portfolio snapshot into structured open-order rows."""
    if not isinstance(portfolio_snapshot_text, str):
        return ExistingBuyOpenOrdersParseResult(
            section_present=False,
            diagnostics=["portfolio snapshot text is not a string."],
        )

    section = _extract_section_2a(portfolio_snapshot_text)
    if section is None:
        return ExistingBuyOpenOrdersParseResult(
            section_present=False,
            diagnostics=["section (2a) existing_buy_open_orders_summary not found."],
        )

    orders: dict[str, ExistingBuyOpenOrder] = {}
    diagnostics: list[str] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not _TICKER_ROW_RE.match(line):
            continue
        fields = [part.strip() for part in line.split("|")]
        # Ignore the literal format/header row "TICKER | budget | ...".
        if fields and fields[_COL_TICKER].upper() == "TICKER":
            continue
        order = _parse_row(fields)
        if order.ticker in orders:
            diagnostics.append(f"duplicate (2a) ticker row: {order.ticker}.")
        orders[order.ticker] = order
        diagnostics.extend(order.diagnostics)

    return ExistingBuyOpenOrdersParseResult(
        section_present=True,
        orders=orders,
        diagnostics=diagnostics,
    )


def _extract_section_2a(text: str) -> str | None:
    start = SECTION_2A_START_RE.search(text)
    if start is None:
        return None
    rest = text[start.end():]
    end = SECTION_2B_START_RE.search(rest)
    return rest[: end.start()] if end is not None else rest


def _parse_row(fields: list[str]) -> ExistingBuyOpenOrder:
    ticker = _normalize_ticker(fields[_COL_TICKER])
    diagnostics: list[str] = []

    if len(fields) != EXPECTED_COLUMN_COUNT:
        return ExistingBuyOpenOrder(
            ticker=ticker,
            budget=None,
            stated_compiled_notional=None,
            reconstructed_notional=None,
            data_gap=True,
            diagnostics=[
                f"{ticker}: expected {EXPECTED_COLUMN_COUNT} columns, got {len(fields)} "
                "(PARSE_BLOCKED)."
            ],
        )

    budget = _parse_decimal(fields[_COL_BUDGET])
    stated = _parse_decimal(fields[_COL_COMPILED_NOTIONAL])
    steps, step_diag = _parse_steps(fields[_COL_STEPS_SUMMARY])
    qtys, qty_diag = _parse_qtys(fields[_COL_QTYS_SUMMARY])
    recon_diag: list[str] = []
    reconstructed = _reconstruct_notional(ticker, steps, qtys, recon_diag)
    diagnostics.extend(f"{ticker}: {d}" for d in (*step_diag, *qty_diag, *recon_diag))

    # data_gap flags only genuine parse failures. A reconstructed=None caused
    # solely by omitted (optional) live-structure columns is NOT a parse failure.
    data_gap = bool(step_diag or qty_diag or recon_diag)

    return ExistingBuyOpenOrder(
        ticker=ticker,
        budget=budget,
        stated_compiled_notional=stated,
        reconstructed_notional=reconstructed,
        steps=steps,
        qtys=qtys,
        data_gap=data_gap,
        diagnostics=diagnostics,
    )


def _parse_steps(value: str) -> tuple[dict[str, Decimal], list[str]]:
    """Parse 'L2@658.95;L3@638.03' -> {'L2': 658.95, 'L3': 638.03}."""
    result: dict[str, Decimal] = {}
    diagnostics: list[str] = []
    if value == "":
        return result, diagnostics
    for token in value.split(";"):
        token = token.strip()
        if not token:
            continue
        name, sep, price_text = token.partition("@")
        price = _parse_decimal(price_text)
        if not sep or not name.strip() or price is None:
            diagnostics.append(f"unparseable live_order_steps token: {token!r}.")
            continue
        result[name.strip()] = price
    return result, diagnostics


def _parse_qtys(value: str) -> tuple[dict[str, Decimal], list[str]]:
    """Parse 'L2:5;L3:3' -> {'L2': 5, 'L3': 3}."""
    result: dict[str, Decimal] = {}
    diagnostics: list[str] = []
    if value == "":
        return result, diagnostics
    for token in value.split(";"):
        token = token.strip()
        if not token:
            continue
        name, sep, qty_text = token.partition(":")
        qty = _parse_decimal(qty_text)
        if not sep or not name.strip() or qty is None:
            diagnostics.append(f"unparseable live_order_qtys token: {token!r}.")
            continue
        result[name.strip()] = qty
    return result, diagnostics


def _reconstruct_notional(
    ticker: str,
    steps: Mapping[str, Decimal],
    qtys: Mapping[str, Decimal],
    diagnostics: list[str],
) -> Decimal | None:
    """Reconstruct Σ(step_qty × step_limit_price); None when not deterministically derivable."""
    if not steps or not qtys:
        return None
    if set(steps) != set(qtys):
        diagnostics.append(
            f"{ticker}: live_order_steps_summary and live_order_qtys_summary step names "
            f"do not match ({sorted(steps)} vs {sorted(qtys)})."
        )
        return None
    total = Decimal("0")
    for name, price in steps.items():
        total += price * qtys[name]
    return total


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _normalize_ticker(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()
