"""Focused contracts for fixed-X report-only BUY capacity facts."""

from __future__ import annotations

import hashlib
import inspect
import errno
from decimal import (
    Clamped,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    ROUND_DOWN,
    Rounded,
    Underflow,
    localcontext,
)
from pathlib import Path

import pytest

from investment_orchestrator.mmi.contracts import MmiSourceRole
from investment_orchestrator.mmi.source_capture import _capture_mmi_source_at_root
from investment_orchestrator.mmi.stable_read import (
    MmiStableReadError,
    MmiStableReadErrorCode,
)
from investment_orchestrator.observability import (
    report_only_budget_capacity as capacity,
)
from investment_orchestrator.parsers.portfolio_snapshot_existing_orders import (
    parse_existing_buy_open_orders_summary,
)


_OPEN_BUY_HEADER = (
    "TICKER | budget | compiled_open_order_notional(optional) | "
    "residual_cash_not_allocated(optional) | template_id | "
    "anchor_baseline_last_close | anchor_price_asof | "
    "last_refresh_date_et(optional) | highest_live_limit(optional) | "
    "lowest_live_limit(optional) | live_step_count(optional) | "
    "live_order_steps_summary(optional) | live_order_qtys_summary(optional)"
)
_OPEN_BUY_SECTION_START = (
    "(2a) existing_buy_open_orders_summary"
    "（optional, ticker-level summary; buy-side existing open orders SSOT）"
)
_OPEN_BUY_SECTION_END = (
    "(2b) sell_open_orders"
    "（optional, lot-aware open sell orders summary）"
)


def _portfolio(rows: tuple[str, ...] = ()) -> bytes:
    return "\n".join(
        (
            "# updated 2026-08-12",
            "",
            _OPEN_BUY_SECTION_START,
            _OPEN_BUY_HEADER,
            *rows,
            "",
            _OPEN_BUY_SECTION_END,
            "",
        )
    ).encode("utf-8")


def _write_ceiling(root: Path, amount: str) -> None:
    current = root / "inputs/current"
    current.mkdir(parents=True, exist_ok=True)
    (current / "budget_ceiling.txt").write_text(
        "\n".join(
            (
                "schema_version = report_only_budget_ceiling_v1",
                "currency = USD",
                f"maximum_total_unfilled_buy_commitment = {amount}",
                "",
            )
        ),
        encoding="utf-8",
    )


def _observe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ceiling: str | None,
    rows: tuple[str, ...] = (),
):
    current = tmp_path / "inputs/current"
    current.mkdir(parents=True, exist_ok=True)
    portfolio_bytes = _portfolio(rows)
    portfolio_path = current / "portfolio_snapshot.txt"
    portfolio_path.write_bytes(portfolio_bytes)
    if ceiling is not None:
        _write_ceiling(tmp_path, ceiling)
    expected_sha256 = hashlib.sha256(portfolio_bytes).hexdigest()

    def _capture(role: MmiSourceRole, *, expected_source_sha256: str):
        assert role is MmiSourceRole.PORTFOLIO_SNAPSHOT
        return _capture_mmi_source_at_root(
            tmp_path,
            role=role,
            expected_source_sha256=expected_source_sha256,
        )

    monkeypatch.setattr(capacity, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(capacity, "capture_current_mmi_source", _capture)
    return capacity.observe_current_report_only_budget_capacity(
        portfolio_snapshot_expected_sha256=expected_sha256,
    )


def test_valid_fixed_x_reader_binds_actual_bytes_and_exact_decimal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_ceiling(tmp_path, "100.25")
    monkeypatch.setattr(capacity, "repo_root", lambda: tmp_path)

    source, reasons, invalid = capacity._read_current_budget_ceiling()

    raw = (tmp_path / "inputs/current/budget_ceiling.txt").read_bytes()
    assert reasons == ()
    assert invalid is False
    assert source is not None
    assert source.repository_relative_locator == "inputs/current/budget_ceiling.txt"
    assert source.observed_sha256 == hashlib.sha256(raw).hexdigest()
    assert source.observed_size_bytes == len(raw)
    assert source.currency == "USD"
    assert source.maximum_total_unfilled_buy_commitment == "100.25"


def test_missing_or_malformed_x_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "inputs/current").mkdir(parents=True)
    monkeypatch.setattr(capacity, "repo_root", lambda: tmp_path)

    source, reasons, invalid = capacity._read_current_budget_ceiling()
    assert source is None
    assert reasons == ("BUDGET_CAPACITY_X_SOURCE_ABSENT",)
    assert invalid is False

    for amount in ("-1", "1.234", "1.0"):
        _write_ceiling(tmp_path, amount)
        source, reasons, invalid = capacity._read_current_budget_ceiling()
        assert source is None
        assert reasons == ("BUDGET_CAPACITY_X_SOURCE_INVALID",)
        assert invalid is True

    _write_ceiling(tmp_path, "1")
    ceiling_path = tmp_path / "inputs/current/budget_ceiling.txt"
    ceiling_path.write_bytes(
        ceiling_path.read_bytes() + b"unexpected_field = invalid\n"
    )
    source, reasons, invalid = capacity._read_current_budget_ceiling()
    assert source is None
    assert reasons == ("BUDGET_CAPACITY_X_SOURCE_INVALID",)
    assert invalid is True


@pytest.mark.parametrize("error_number", (errno.EACCES, errno.EPERM))
def test_permission_denied_x_read_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
) -> None:
    _write_ceiling(tmp_path, "100")
    monkeypatch.setattr(capacity, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        capacity,
        "stable_read_exact_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            MmiStableReadError(
                MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID,
                os_error_errno=error_number,
            )
        ),
    )

    source, reasons, invalid = capacity._read_current_budget_ceiling()

    assert source is None
    assert reasons == ("BUDGET_CAPACITY_X_SOURCE_UNREADABLE",)
    assert invalid is False


def test_present_empty_2a_is_complete_zero_commitment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(tmp_path, monkeypatch, ceiling="100")

    assert result.status is capacity.BudgetCapacityObservationStatus.VALID_REPORT_ONLY
    assert result.authority_effect == "NONE"
    assert result.projection is not None
    assert result.projection.current_open_buy_commitments == ()
    assert result.projection.portfolio_source_sha256 == hashlib.sha256(
        _portfolio()
    ).hexdigest()
    assert len(result.projection.portfolio_source_record_identity_sha256) == 64
    assert result.projection.portfolio_source_date == "2026-08-12"
    assert result.projection.total_current_unfilled_buy_commitment == "0"
    assert result.projection.remaining_ceiling == "100"
    assert result.projection.over_ceiling_amount == "0"


def test_strict_budget_path_rejects_malformed_data_like_row_legacy_ignores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed = (
        "qqq | 100 | 50 | 50 | T4-E | 744 | 2026-06-15 |  |  |  |  |  | ",
    )
    assert parse_existing_buy_open_orders_summary(
        _portfolio(malformed).decode("utf-8")
    ).orders == {}

    result = _observe(tmp_path, monkeypatch, ceiling="100", rows=malformed)

    assert result.status is capacity.BudgetCapacityObservationStatus.INVALID
    assert result.reason_codes == (
        "MMI_PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED",
    )
    assert result.projection is None


def test_strict_budget_path_rejects_duplicate_ladder_name_before_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate_ladder = (
        "QQQ | 100 |  |  | T4-E | 744 | 2026-06-15 |  | 20 | 10 | 1 | L1@10;L1@20 | L1:1",
    )
    legacy = parse_existing_buy_open_orders_summary(
        _portfolio(duplicate_ladder).decode("utf-8")
    )
    assert legacy.orders["QQQ"].reconstructed_notional == 20

    result = _observe(
        tmp_path,
        monkeypatch,
        ceiling="100",
        rows=duplicate_ladder,
    )

    assert result.status is capacity.BudgetCapacityObservationStatus.INVALID
    assert result.reason_codes == (
        "MMI_PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED",
    )
    assert result.projection is None


def test_exact_stated_and_reconstructed_commitments_are_reported_without_budget_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        ceiling="100",
        rows=(
            "QQQ | 90 | 30.5 | 59.5 | T4-E | 744 | 2026-06-15 |  | 30.5 | 30.5 | 1 | L1@30.5 | L1:1",
            "VOO | 100 |  |  | T4-B | 500 | 2026-06-15 |  | 20 | 20 | 1 | L1@20 | L1:2",
        ),
    )

    assert result.status is capacity.BudgetCapacityObservationStatus.VALID_REPORT_ONLY
    assert result.projection is not None
    assert result.projection.current_open_buy_commitments == (
        capacity.CurrentOpenBuyCommitment(
            ticker="QQQ",
            commitment="30.5",
            commitment_source="STATED_AND_RECONSTRUCTED",
        ),
        capacity.CurrentOpenBuyCommitment(
            ticker="VOO",
            commitment="40",
            commitment_source="RECONSTRUCTED_REMAINING_LIMIT_NOTIONAL",
        ),
    )
    assert result.projection.total_current_unfilled_buy_commitment == "70.5"
    assert result.projection.remaining_ceiling == "29.5"


def test_exact_stated_commitment_is_sufficient_without_ladder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        ceiling="100",
        rows=(
            "QQQ | 100 | 30.5 | 69.5 | T4-E | 744 | 2026-06-15 |  |  |  |  |  | ",
        ),
    )

    assert result.status is capacity.BudgetCapacityObservationStatus.VALID_REPORT_ONLY
    assert result.projection is not None
    assert result.projection.current_open_buy_commitments == (
        capacity.CurrentOpenBuyCommitment(
            ticker="QQQ",
            commitment="30.5",
            commitment_source="STATED_COMPILED_NOTIONAL",
        ),
    )


def test_unprovable_commitment_is_unavailable_and_contradiction_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable = _observe(
        tmp_path,
        monkeypatch,
        ceiling="100",
        rows=("QQQ | 100 |  |  | T4-E | 744 | 2026-06-15 |  |  |  |  |  | ",),
    )
    assert unavailable.status is capacity.BudgetCapacityObservationStatus.UNAVAILABLE
    assert unavailable.reason_codes == ("OPEN_BUY_COMMITMENT_NOT_PROVABLE",)

    invalid = _observe(
        tmp_path,
        monkeypatch,
        ceiling="100",
        rows=("QQQ | 100 | 51 | 49 | T4-E | 744 | 2026-06-15 |  | 50 | 50 | 1 | L1@50 | L1:1",),
    )
    assert invalid.status is capacity.BudgetCapacityObservationStatus.INVALID
    assert invalid.reason_codes == (
        "MMI_PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED",
    )


def test_over_ceiling_is_report_only_and_never_an_action_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        ceiling="20",
        rows=("QQQ | 100 | 30.5 | 69.5 | T4-E | 744 | 2026-06-15 |  |  |  |  |  | ",),
    )

    assert result.status is capacity.BudgetCapacityObservationStatus.VALID_REPORT_ONLY
    assert result.authority_effect == "NONE"
    assert result.projection is not None
    assert result.projection.remaining_ceiling == "0"
    assert result.projection.over_ceiling_amount == "10.5"
    assert "action" not in result.projection.__dataclass_fields__


def test_ceiling_arithmetic_is_exact_under_low_ambient_decimal_precision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remaining_ceiling and over_ceiling_amount must be exact and fully
    independent of ``decimal.getcontext().prec``.

    Under ordinary ambient precision, subtracting a 2-fractional-digit
    commitment from a 48-digit ceiling (or vice versa) forces Decimal
    subtraction to round the result to the context's significant-digit
    count.  A rounding carry can even push the rendered digit count past the
    canonical 48-digit bound, so the observer would previously return
    BUDGET_CAPACITY_ARITHMETIC_INVALID for an otherwise perfectly valid
    ceiling/commitment pair.  Deliberately lowering the ambient precision
    (with every rounding-related trap enabled, so any context-affected
    arithmetic would raise instead of silently rounding) proves the exact
    result does not depend on it.  ``localcontext`` restores the ambient
    context automatically on exit.
    """
    large = "9" * 48
    small = "0.01"
    small_commitment_row = (
        "QQQ | 0.01 | 0.01 |  | T4-E | 744 | 2026-06-15 |  |  |  |  |  | "
    )
    large_commitment_row = (
        f"QQQ | {large} | {large} |  | T4-E | 744 | 2026-06-15 |  |  |  |  |  | "
    )
    expected_units = int(large) * 100 - 1
    expected_difference = (
        f"{str(expected_units)[:-2]}.{str(expected_units)[-2:]}"
    )

    with localcontext() as context:
        context.prec = 1
        context.rounding = ROUND_DOWN
        for signal in (
            Clamped,
            DivisionByZero,
            Inexact,
            InvalidOperation,
            Overflow,
            Rounded,
            Underflow,
        ):
            context.traps[signal] = True

        remaining_result = _observe(
            tmp_path / "remaining",
            monkeypatch,
            ceiling=large,
            rows=(small_commitment_row,),
        )
        over_ceiling_result = _observe(
            tmp_path / "over-ceiling",
            monkeypatch,
            ceiling=small,
            rows=(large_commitment_row,),
        )

    assert remaining_result.status is (
        capacity.BudgetCapacityObservationStatus.VALID_REPORT_ONLY
    )
    assert remaining_result.projection is not None
    assert remaining_result.projection.remaining_ceiling == expected_difference
    assert remaining_result.projection.over_ceiling_amount == "0"

    assert over_ceiling_result.status is (
        capacity.BudgetCapacityObservationStatus.VALID_REPORT_ONLY
    )
    assert over_ceiling_result.projection is not None
    assert (
        over_ceiling_result.projection.over_ceiling_amount
        == expected_difference
    )
    assert over_ceiling_result.projection.remaining_ceiling == "0"


def test_public_observer_has_no_x_path_or_market_data_bypass() -> None:
    parameters = inspect.signature(
        capacity.observe_current_report_only_budget_capacity
    ).parameters
    assert set(parameters) == {"portfolio_snapshot_expected_sha256"}
    assert not {
        "x",
        "budget_ceiling",
        "budget_ceiling_path",
        "cash",
        "buying_power",
        "capture_path",
        "valuation",
        "holdings",
    } & set(parameters)


def test_capacity_observer_is_h_free_and_disconnected_from_authority_flows() -> None:
    root = Path(__file__).resolve().parents[2] / "src/investment_orchestrator"
    module_file = root / "observability/report_only_budget_capacity.py"
    module_text = module_file.read_text(encoding="utf-8")
    assert all(
        value not in module_text
        for value in (
            "report_only_holdings_exposure",
            "us_equity_yfinance_valuation_capture",
            "import yfinance",
            "yf.download",
            "investment_orchestrator.workflow",
            "investment_orchestrator.state",
            "investment_orchestrator.permissions",
            "investment_orchestrator.orders",
            "investment_orchestrator.broker",
            "investment_orchestrator.llm",
        )
    )
    assert all(
        "report_only_budget_capacity" not in candidate.read_text(encoding="utf-8")
        for candidate in root.rglob("*.py")
        if candidate != module_file
    )
