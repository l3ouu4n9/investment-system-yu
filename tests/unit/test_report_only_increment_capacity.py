"""Focused contracts for fixed-r report-only r x H increment-capacity basis."""

from __future__ import annotations

import inspect
from decimal import (
    Clamped,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    ROUND_DOWN,
    Rounded,
    Underflow,
    localcontext,
)
from fractions import Fraction
from pathlib import Path

import pytest

from investment_orchestrator.observability import (
    report_only_increment_capacity as capacity,
)
from investment_orchestrator.observability.report_only_holdings_exposure import (
    ExposureObservationResult,
    ExposureObservationStatus,
    ExposureProjection,
)


_STRATEGY_SHA = "0" * 64
_PORTFOLIO_SHA = "1" * 64


def _write_r(tmp_path: Path, amount: str) -> None:
    current = tmp_path / "inputs/current"
    current.mkdir(parents=True, exist_ok=True)
    (current / "increment_fraction.txt").write_text(
        "\n".join(
            (
                "schema_version = report_only_increment_fraction_v1",
                "basis = total_holdings_exposure",
                f"increment_fraction = {amount}",
                "",
            )
        )
    )


def _fake_exposure_projection(
    *,
    total_market_value: str = "131485.13987731932915",
) -> ExposureProjection:
    return ExposureProjection(
        schema_version="report_only_holdings_exposure_projection_v1",
        authority_effect="NONE",
        portfolio_source_sha256="a" * 64,
        portfolio_source_record_identity_sha256="b" * 64,
        portfolio_scope_id="primary",
        holdings_observation_date="2026-08-12",
        capture_artifact_sha256="c" * 64,
        capture_source_kind="NORMALIZED_YFINANCE",
        capture_provider_id="yfinance",
        capture_session_date="2026-08-12",
        capture_trusted_evaluation_timestamp_utc="2026-08-12T21:00:00.000000Z",
        mark_ticker_domain=("QQQ",),
        mark_as_of_date="2026-08-12",
        calendar_id="XNYS",
        calendar_schedule_sha256="d" * 64,
        calendar_coverage_start_date="2026-01-01",
        calendar_coverage_end_date="2026-12-31",
        trusted_evaluation_timestamp_utc="2026-08-12T21:00:00.000000Z",
        latest_completed_session_date="2026-08-12",
        latest_completed_session_close_timestamp_et="2026-08-12T16:00:00-04:00",
        freshness_status="FRESH",
        policy_projection_identity_sha256="e" * 64,
        currency="USD",
        positions=(),
        total_market_value=total_market_value,
    )


def _observe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    r_amount: str | None,
    exposure_result: ExposureObservationResult | None = None,
):
    monkeypatch.setattr(capacity, "repo_root", lambda: tmp_path)
    if r_amount is not None:
        _write_r(tmp_path, r_amount)

    def _fake_observer(**_kwargs: object) -> ExposureObservationResult:
        if exposure_result is None:
            raise AssertionError(
                "H observer must not be consumed for this case"
            )
        return exposure_result

    monkeypatch.setattr(
        capacity._holdings_exposure,
        "observe_current_report_only_holdings_exposure",
        _fake_observer,
    )
    return capacity.observe_current_report_only_increment_capacity(
        strategy_settings_expected_sha256=_STRATEGY_SHA,
        portfolio_snapshot_expected_sha256=_PORTFOLIO_SHA,
    )


def test_valid_canonical_r_source_binds_fixed_basis_and_exact_provenance(
    tmp_path: Path,
) -> None:
    _write_r(tmp_path, "0.1")
    original_repo_root = capacity.repo_root
    capacity.repo_root = lambda: tmp_path
    try:
        source, reasons, invalid = capacity._read_current_increment_fraction()
    finally:
        capacity.repo_root = original_repo_root

    raw = (tmp_path / "inputs/current/increment_fraction.txt").read_bytes()
    import hashlib

    assert reasons == ()
    assert invalid is False
    assert source is not None
    assert source.repository_relative_locator == (
        "inputs/current/increment_fraction.txt"
    )
    assert source.observed_sha256 == hashlib.sha256(raw).hexdigest()
    assert source.observed_size_bytes == len(raw)
    assert source.basis == "total_holdings_exposure"
    assert source.increment_fraction == "0.1"


def test_missing_r_is_unavailable_and_h_observer_not_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "inputs/current").mkdir(parents=True)
    result = _observe(tmp_path, monkeypatch, r_amount=None)

    assert result.status is capacity.IncrementCapacityObservationStatus.UNAVAILABLE
    assert result.reason_codes == ("BUDGET_INCREMENT_R_SOURCE_ABSENT",)
    assert result.projection is None
    assert result.authority_effect == "NONE"


@pytest.mark.parametrize(
    "amount",
    ("not-a-number", "0.10", "1.0", "-0", "01", "1e-1"),
)
def test_malformed_or_noncanonical_r_is_invalid_and_h_observer_not_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amount: str,
) -> None:
    result = _observe(tmp_path, monkeypatch, r_amount=amount)

    assert result.status is capacity.IncrementCapacityObservationStatus.INVALID
    assert result.reason_codes == ("BUDGET_INCREMENT_R_SOURCE_INVALID",)
    assert result.projection is None


@pytest.mark.parametrize(
    ("amount", "expected_status"),
    (
        ("0", capacity.IncrementCapacityObservationStatus.VALID_REPORT_ONLY),
        ("1", capacity.IncrementCapacityObservationStatus.VALID_REPORT_ONLY),
        ("1.01", capacity.IncrementCapacityObservationStatus.INVALID),
        ("-0.1", capacity.IncrementCapacityObservationStatus.INVALID),
    ),
)
def test_r_semantic_domain_is_closed_zero_one_inclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amount: str,
    expected_status: capacity.IncrementCapacityObservationStatus,
) -> None:
    exposure_result = None
    if expected_status is capacity.IncrementCapacityObservationStatus.VALID_REPORT_ONLY:
        exposure_result = ExposureObservationResult(
            authority_effect="NONE",
            status=ExposureObservationStatus.VALID_REPORT_ONLY,
            reason_codes=(),
            projection=_fake_exposure_projection(total_market_value="100"),
        )
    result = _observe(
        tmp_path, monkeypatch, r_amount=amount, exposure_result=exposure_result
    )
    assert result.status is expected_status


def test_valid_trusted_h_produces_exact_r_times_h(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exposure_result = ExposureObservationResult(
        authority_effect="NONE",
        status=ExposureObservationStatus.VALID_REPORT_ONLY,
        reason_codes=(),
        projection=_fake_exposure_projection(
            total_market_value="131485.13987731932915"
        ),
    )
    result = _observe(
        tmp_path, monkeypatch, r_amount="0.1", exposure_result=exposure_result
    )

    assert result.status is capacity.IncrementCapacityObservationStatus.VALID_REPORT_ONLY
    assert result.authority_effect == "NONE"
    p = result.projection
    assert p is not None
    assert p.total_holdings_exposure == "131485.13987731932915"
    assert p.increment_fraction == "0.1"
    assert p.increment_cap_basis == "13148.513987731932915"
    assert Decimal(p.increment_cap_basis) == Decimal("0.1") * Decimal(
        "131485.13987731932915"
    )
    assert p.currency == "USD"
    assert p.portfolio_source_sha256 == "a" * 64
    assert p.portfolio_source_record_identity_sha256 == "b" * 64
    assert p.policy_projection_identity_sha256 == "e" * 64
    assert p.freshness_status == "FRESH"
    assert p.increment_fraction_source.increment_fraction == "0.1"


def test_h_unavailable_propagates_unavailable_with_owning_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exposure_result = ExposureObservationResult(
        authority_effect="NONE",
        status=ExposureObservationStatus.UNAVAILABLE,
        reason_codes=("MMI_SOURCE_MISSING",),
        projection=None,
    )
    result = _observe(
        tmp_path, monkeypatch, r_amount="0.1", exposure_result=exposure_result
    )

    assert result.status is capacity.IncrementCapacityObservationStatus.UNAVAILABLE
    assert result.reason_codes == ("MMI_SOURCE_MISSING",)
    assert result.projection is None


def test_h_invalid_propagates_invalid_with_owning_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exposure_result = ExposureObservationResult(
        authority_effect="NONE",
        status=ExposureObservationStatus.INVALID,
        reason_codes=("US_EQUITY_SESSION_MARK_DATE_STATUS_INVALID",),
        projection=None,
    )
    result = _observe(
        tmp_path, monkeypatch, r_amount="0.1", exposure_result=exposure_result
    )

    assert result.status is capacity.IncrementCapacityObservationStatus.INVALID
    assert result.reason_codes == ("US_EQUITY_SESSION_MARK_DATE_STATUS_INVALID",)
    assert result.projection is None


def test_h_manual_review_with_populated_projection_is_not_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOAD-BEARING: a populated MANUAL_REVIEW exposure projection must never
    be treated as a usable H.  ``projection is not None`` is not the gate;
    ``status is VALID_REPORT_ONLY`` is the only gate.
    """
    populated_projection = _fake_exposure_projection(total_market_value="999")
    exposure_result = ExposureObservationResult(
        authority_effect="NONE",
        status=ExposureObservationStatus.MANUAL_REVIEW,
        reason_codes=("REPORT_ONLY_EXPOSURE_TICKER_OUTSIDE_DETERMINISTIC_POLICY",),
        projection=populated_projection,
    )
    assert exposure_result.projection is not None

    result = _observe(
        tmp_path, monkeypatch, r_amount="0.1", exposure_result=exposure_result
    )

    assert result.status is capacity.IncrementCapacityObservationStatus.MANUAL_REVIEW
    assert result.reason_codes == (
        "REPORT_ONLY_EXPOSURE_TICKER_OUTSIDE_DETERMINISTIC_POLICY",
    )
    assert result.projection is None
    assert result.authority_effect == "NONE"


def test_exact_arithmetic_is_independent_of_hostile_ambient_decimal_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exposure_result = ExposureObservationResult(
        authority_effect="NONE",
        status=ExposureObservationStatus.VALID_REPORT_ONLY,
        reason_codes=(),
        projection=_fake_exposure_projection(
            total_market_value="999999999999999999999999.123456789"
        ),
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
        result = _observe(
            tmp_path,
            monkeypatch,
            r_amount="0.123456789012345",
            exposure_result=exposure_result,
        )

    assert result.status is capacity.IncrementCapacityObservationStatus.VALID_REPORT_ONLY
    assert result.projection is not None
    # Fraction gives an ambient-context-free exact oracle; a naive Decimal
    # multiplication here would itself round under the default prec=28
    # context, since the true product has more than 28 significant digits.
    expected = Fraction("0.123456789012345") * Fraction(
        "999999999999999999999999.123456789"
    )
    assert Fraction(result.projection.increment_cap_basis) == expected


def test_unrepresentable_product_fails_closed_without_enlarging_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exposure_result = ExposureObservationResult(
        authority_effect="NONE",
        status=ExposureObservationStatus.VALID_REPORT_ONLY,
        reason_codes=(),
        projection=_fake_exposure_projection(total_market_value="1.1234567890"),
    )
    result = _observe(
        tmp_path,
        monkeypatch,
        r_amount="0." + "9" * 20,
        exposure_result=exposure_result,
    )

    assert result.status is capacity.IncrementCapacityObservationStatus.INVALID
    assert result.reason_codes == ("BUDGET_INCREMENT_ARITHMETIC_INVALID",)
    assert result.projection is None
    from investment_orchestrator.mmi.canonical import (
        MAXIMUM_DECIMAL_FRACTIONAL_DIGITS,
        MAXIMUM_DECIMAL_INTEGRAL_DIGITS,
        MAXIMUM_DECIMAL_TOTAL_DIGITS,
    )

    assert MAXIMUM_DECIMAL_INTEGRAL_DIGITS == 48
    assert MAXIMUM_DECIMAL_FRACTIONAL_DIGITS == 24
    assert MAXIMUM_DECIMAL_TOTAL_DIGITS == 56


def test_valid_result_surface_is_scalar_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exposure_result = ExposureObservationResult(
        authority_effect="NONE",
        status=ExposureObservationStatus.VALID_REPORT_ONLY,
        reason_codes=(),
        projection=_fake_exposure_projection(),
    )
    result = _observe(
        tmp_path, monkeypatch, r_amount="0.1", exposure_result=exposure_result
    )
    assert result.projection is not None
    fields = set(result.projection.__dataclass_fields__)
    forbidden = {
        "ticker",
        "tickers",
        "desired_increment_i",
        "desired_increment",
        "eligibility",
        "eligible",
        "increment_eligible",
        "maintain_only",
        "exclude",
        "unresolved",
        "preferred",
        "standard",
        "rank",
        "score",
        "target",
        "target_commitment",
        "t_i",
        "final_increment",
        "quantity",
        "keep",
        "cancel",
        "replace",
        "new",
        "permission",
        "order_readiness",
        "x",
        "remaining_x",
        "y",
    }
    assert not (fields & forbidden)
    assert fields == {
        "schema_version",
        "authority_effect",
        "increment_fraction_source",
        "currency",
        "portfolio_source_sha256",
        "portfolio_source_record_identity_sha256",
        "portfolio_scope_id",
        "holdings_observation_date",
        "capture_artifact_sha256",
        "capture_session_date",
        "calendar_id",
        "calendar_schedule_sha256",
        "latest_completed_session_date",
        "freshness_status",
        "policy_projection_identity_sha256",
        "total_holdings_exposure",
        "increment_fraction",
        "increment_cap_basis",
    }


def test_authority_effect_is_none_across_every_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for status, exposure_result in (
        (
            ExposureObservationStatus.UNAVAILABLE,
            ExposureObservationResult(
                authority_effect="NONE",
                status=ExposureObservationStatus.UNAVAILABLE,
                reason_codes=("MMI_SOURCE_MISSING",),
                projection=None,
            ),
        ),
        (
            ExposureObservationStatus.INVALID,
            ExposureObservationResult(
                authority_effect="NONE",
                status=ExposureObservationStatus.INVALID,
                reason_codes=("SOME_CODE",),
                projection=None,
            ),
        ),
        (
            ExposureObservationStatus.MANUAL_REVIEW,
            ExposureObservationResult(
                authority_effect="NONE",
                status=ExposureObservationStatus.MANUAL_REVIEW,
                reason_codes=("SOME_CODE",),
                projection=_fake_exposure_projection(),
            ),
        ),
        (
            ExposureObservationStatus.VALID_REPORT_ONLY,
            ExposureObservationResult(
                authority_effect="NONE",
                status=ExposureObservationStatus.VALID_REPORT_ONLY,
                reason_codes=(),
                projection=_fake_exposure_projection(),
            ),
        ),
    ):
        result = _observe(
            tmp_path, monkeypatch, r_amount="0.1", exposure_result=exposure_result
        )
        assert result.authority_effect == "NONE", status


def test_public_observer_has_no_r_h_x_or_market_data_bypass() -> None:
    parameters = inspect.signature(
        capacity.observe_current_report_only_increment_capacity
    ).parameters
    assert set(parameters) == {
        "strategy_settings_expected_sha256",
        "portfolio_snapshot_expected_sha256",
    }
    assert not {
        "r",
        "increment_fraction",
        "h",
        "total_holdings_exposure",
        "x",
        "budget_ceiling",
        "cash",
        "buying_power",
        "capture_path",
        "valuation",
        "holdings",
    } & set(parameters)


def test_increment_capacity_module_is_x_free_with_one_report_only_consumer() -> None:
    root = Path(__file__).resolve().parents[2] / "src/investment_orchestrator"
    module_file = root / "observability/report_only_increment_capacity.py"
    module_text = module_file.read_text(encoding="utf-8")
    assert all(
        value not in module_text
        for value in (
            "report_only_budget_capacity",
            "budget_ceiling",
            "hard_cap_open_orders_budget",
            "target_new_buy_budget_this_run",
            "target_open_order_budget",
            "existing_open_order_budget",
            "remaining_ceiling",
            "over_ceiling",
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
    consumers = {
        candidate.relative_to(root).as_posix()
        for candidate in root.rglob("*.py")
        if candidate != module_file
        and "report_only_increment_capacity"
        in candidate.read_text(encoding="utf-8")
    }
    assert consumers == {"workflow/step3_h1_v1_proposal.py"}
