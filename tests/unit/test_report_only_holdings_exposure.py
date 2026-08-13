"""Focused contracts for the network-free holdings/capture observer."""

from __future__ import annotations

from datetime import datetime, timezone
import errno
import hashlib
import inspect
from pathlib import Path

import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.market import (
    us_equity_session_calendar as calendar,
)
from investment_orchestrator.market import (
    us_equity_yfinance_valuation_capture as capture,
)
from investment_orchestrator.market.us_equity_session_calendar import (
    CompletedUsEquitySession,
)
from investment_orchestrator.mmi.canonical import canonical_json_bytes
from investment_orchestrator.mmi.contracts import (
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
)
from investment_orchestrator.mmi.source_capture import (
    _capture_mmi_source_at_root,
)
from investment_orchestrator.mmi.stable_read import (
    MmiStableReadError,
    MmiStableReadErrorCode,
)
from investment_orchestrator.observability import (
    report_only_holdings_exposure as exposure,
)
from investment_orchestrator.observability.report_only_holdings_exposure import (
    StrictHoldingsDomain,
)


_SCHEDULE_SHA256 = (
    "a7142dcf13f52f30f07cc48942abe1e325ace21d644a2198c5e5667cf9d20007"
)


class _FixedClock:
    def now_utc(self) -> datetime:
        return datetime(2026, 8, 12, 20, tzinfo=timezone.utc)


def _strict_section(rows: tuple[str, ...]) -> bytes:
    return "\n".join(
        (
            "[STRICT_POSITIVE_ETF_POSITIONS_V1]",
            "schema_version = strict_positive_etf_positions_v1",
            "portfolio_scope_id = yu_etf_portfolio",
            "operator_scope_complete = true",
            "TICKER | shares",
            *rows,
            "[/STRICT_POSITIVE_ETF_POSITIONS_V1]",
        )
    ).encode("utf-8")


def _portfolio(rows: tuple[str, ...] = ("QQQ | 2.5", "SMH | 3")) -> bytes:
    current = (repo_root() / "inputs/current/portfolio_snapshot.txt").read_bytes()
    base, marker, _existing = current.partition(
        b"\n[STRICT_POSITIVE_ETF_POSITIONS_V1]\n"
    )
    assert marker
    return base + b"\n" + _strict_section(rows) + b"\n"


def _source(root: Path, role: MmiSourceRole):
    path = root / (
        "inputs/current/strategy_settings.yaml"
        if role is MmiSourceRole.STRATEGY_SETTINGS
        else "inputs/current/portfolio_snapshot.txt"
    )
    result = _capture_mmi_source_at_root(
        root,
        role=role,
        expected_source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    assert result.valid and result.source is not None
    return result.source


def _prepared_current_root(
    tmp_path: Path,
    *,
    portfolio_bytes: bytes,
    manual_valuation_bytes: bytes | None = None,
) -> tuple[object, object]:
    current = tmp_path / "inputs/current"
    current.mkdir(parents=True, exist_ok=True)
    (current / "strategy_settings.yaml").write_bytes(
        (repo_root() / "inputs/current/strategy_settings.yaml").read_bytes()
    )
    (current / "portfolio_snapshot.txt").write_bytes(portfolio_bytes)
    if manual_valuation_bytes is not None:
        (current / "manual_valuation_marks.json").write_bytes(
            manual_valuation_bytes
        )
    return (
        _source(tmp_path, MmiSourceRole.STRATEGY_SETTINGS),
        _source(tmp_path, MmiSourceRole.PORTFOLIO_SNAPSHOT),
    )


def _session(
    *,
    session_date: str = "2026-08-12",
    official_close_timestamp_et: str = "2026-08-12T16:00:00-04:00",
    trusted_evaluation_timestamp_utc: str = "2026-08-12T20:00:00Z",
    calendar_schedule_sha256: str = _SCHEDULE_SHA256,
) -> CompletedUsEquitySession:
    return CompletedUsEquitySession(
        authority_effect="NONE",
        calendar_id="US_EQUITY_REGULAR",
        calendar_schedule_sha256=calendar_schedule_sha256,
        coverage_start_date="2026-01-01",
        coverage_end_date="2026-12-31",
        trusted_evaluation_timestamp_utc=trusted_evaluation_timestamp_utc,
        session_date=session_date,
        official_close_timestamp_et=official_close_timestamp_et,
    )


def _write_capture(
    tmp_path: Path,
    *,
    portfolio_source_sha256: str,
    tickers: tuple[str, ...],
    marks: dict[str, str] | None = None,
    completed_session: CompletedUsEquitySession | None = None,
    mutate: callable | None = None,
) -> bytes:
    session = completed_session if completed_session is not None else _session()
    payload = capture._capture_payload(
        holdings=StrictHoldingsDomain(
            portfolio_source_sha256=portfolio_source_sha256,
            portfolio_scope_id="yu_etf_portfolio",
            tickers=tickers,
        ),
        completed_session=session,
        yfinance_version="1.4.1",
        pandas_version="3.0.3",
        marks=tuple(
            {
                "ticker": ticker,
                "actual_data_date": session.session_date,
                "provider_field": "Close",
                "mark": (
                    marks[ticker]
                    if marks is not None
                    else str(index + 100)
                ),
            }
            for index, ticker in enumerate(sorted(tickers))
        ),
    )
    if mutate is not None:
        mutate(payload)
    raw = canonical_json_bytes(payload, maximum_bytes=262_144)
    path = tmp_path / "artifacts/current/us_equity_yfinance_valuation/capture.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _observe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    portfolio_bytes: bytes | None = None,
    capture_source_sha256: str | None = None,
    capture_tickers: tuple[str, ...] | None = None,
    capture_marks: dict[str, str] | None = None,
    completed_session: CompletedUsEquitySession | None = None,
    capture_raw: bytes | None = None,
    manual_valuation_bytes: bytes | None = None,
):
    selected_portfolio = portfolio_bytes if portfolio_bytes is not None else _portfolio()
    strategy_source, portfolio_source = _prepared_current_root(
        tmp_path,
        portfolio_bytes=selected_portfolio,
        manual_valuation_bytes=manual_valuation_bytes,
    )
    try:
        strict_holdings = exposure._parse_strict_holdings(selected_portfolio)
    except exposure._ExposureInputError:
        strict_holdings = None
    if capture_raw is not None:
        path = tmp_path / "artifacts/current/us_equity_yfinance_valuation/capture.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(capture_raw)
    elif capture_source_sha256 is not False:
        _write_capture(
            tmp_path,
            portfolio_source_sha256=(
                capture_source_sha256
                if capture_source_sha256 is not None
                else portfolio_source.source_record["observed_sha256"]
            ),
            tickers=(
                capture_tickers
                if capture_tickers is not None
                else (
                    tuple(position.ticker for position in strict_holdings.positions)
                    if strict_holdings is not None
                    else ("QQQ",)
                )
            ),
            marks=capture_marks,
            completed_session=completed_session,
        )

    def _capture(role: MmiSourceRole, *, expected_source_sha256: str):
        assert expected_source_sha256 == (
            "a" * 64
            if role is MmiSourceRole.STRATEGY_SETTINGS
            else "b" * 64
        )
        return type("Result", (), {
            "valid": True,
            "authority_effect": "NONE",
            "source": (
                strategy_source
                if role is MmiSourceRole.STRATEGY_SETTINGS
                else portfolio_source
            ),
            "reason_codes": (),
        })()

    monkeypatch.setattr(exposure, "capture_current_mmi_source", _capture)
    monkeypatch.setattr(capture, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        exposure,
        "begin_mmi_projection_run",
        lambda: _begin_mmi_projection_run_with_clock(_FixedClock()),
    )
    return exposure.observe_current_report_only_holdings_exposure(
        strategy_settings_expected_sha256="a" * 64,
        portfolio_snapshot_expected_sha256="b" * 64,
    )


def test_strict_section_has_no_independent_holdings_date() -> None:
    assert "holdings_as_of_date" not in exposure._POSITIONS_PREFIX
    invalid_section = _strict_section(("QQQ | 1",)).replace(
        b"QQQ | 1",
        b"holdings_as_of_date = 2026-08-12\nQQQ | 1",
    )
    with pytest.raises(exposure._ExposureInputError) as exc_info:
        exposure._parse_strict_holdings(invalid_section)
    assert exc_info.value.code == "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_ROW_INVALID"


def test_public_observer_has_no_caller_valuation_or_session_bypass() -> None:
    parameters = inspect.signature(
        exposure.observe_current_report_only_holdings_exposure
    ).parameters
    assert set(parameters) == {
        "strategy_settings_expected_sha256",
        "portfolio_snapshot_expected_sha256",
    }
    assert not {
        "evaluation_date",
        "evaluation_timestamp",
        "completed_trading_session_date",
        "capture_path",
        "capture_sha256",
        "valuation",
    } & set(parameters)


def test_strict_holdings_domain_accessor_binds_existing_observed_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portfolio_bytes = _portfolio(("SMH | 3", "QQQ | 2.5"))
    _prepared_current_root(tmp_path, portfolio_bytes=portfolio_bytes)
    expected_sha256 = hashlib.sha256(portfolio_bytes).hexdigest()

    def _capture(role: MmiSourceRole, *, expected_source_sha256: str):
        return _capture_mmi_source_at_root(
            tmp_path,
            role=role,
            expected_source_sha256=expected_source_sha256,
        )

    monkeypatch.setattr(exposure, "capture_current_mmi_source", _capture)
    domain = exposure.capture_current_validated_strict_holdings_domain(
        portfolio_snapshot_expected_sha256=expected_sha256,
    )
    assert domain.portfolio_source_sha256 == expected_sha256
    assert domain.tickers == ("SMH", "QQQ")


def test_valid_current_capture_returns_complete_decimal_valid_report_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marks = {"QQQ": "400.125", "SMH": "200.5"}
    monkeypatch.setattr(
        capture,
        "_provider_modules",
        lambda: (_ for _ in ()).throw(AssertionError("provider forbidden")),
    )
    result = _observe(
        tmp_path,
        monkeypatch,
        capture_marks=marks,
        manual_valuation_bytes=b'{"malformed":"manual data is not a fallback"}',
    )

    assert result.status is exposure.ExposureObservationStatus.VALID_REPORT_ONLY
    assert result.reason_codes == ("US_EQUITY_SESSION_MARK_DATE_FRESH",)
    assert result.authority_effect == "NONE"
    assert result.projection is not None
    assert result.projection.authority_effect == "NONE"
    assert result.projection.capture_source_kind == "EXTERNAL_MARKET_DATA_CAPTURE"
    assert result.projection.capture_provider_id == "YAHOO_FINANCE"
    assert result.projection.capture_session_date == "2026-08-12"
    assert result.projection.mark_ticker_domain == ("QQQ", "SMH")
    assert [row.market_value for row in result.projection.positions] == [
        "1000.3125",
        "601.5",
    ]
    assert result.projection.total_market_value == "1601.8125"
    assert result.projection.calendar_id == "US_EQUITY_REGULAR"
    assert result.projection.calendar_schedule_sha256 == _SCHEDULE_SHA256
    assert result.projection.latest_completed_session_date == "2026-08-12"
    assert result.projection.latest_completed_session_close_timestamp_et == (
        "2026-08-12T16:00:00-04:00"
    )
    assert result.projection.freshness_status == "FRESH"


def test_capture_artifact_identity_and_source_provenance_bind_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(tmp_path, monkeypatch)
    assert result.projection is not None
    raw = (
        tmp_path / "artifacts/current/us_equity_yfinance_valuation/capture.json"
    ).read_bytes()
    assert result.projection.capture_artifact_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.projection.capture_trusted_evaluation_timestamp_utc == (
        "2026-08-12T20:00:00Z"
    )
    assert result.projection.portfolio_source_sha256 == hashlib.sha256(
        _portfolio()
    ).hexdigest()


def test_absent_capture_is_unavailable_and_never_reads_manual_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        exposure,
        "_capture_manual_valuation_source",
        lambda: (_ for _ in ()).throw(AssertionError("manual fallback forbidden")),
    )
    result = _observe(
        tmp_path,
        monkeypatch,
        capture_source_sha256=False,
        manual_valuation_bytes=b'{"not":"a valuation fallback"}',
    )
    assert result.status is exposure.ExposureObservationStatus.UNAVAILABLE
    assert result.projection is None
    assert result.reason_codes == ("YFINANCE_CAPTURE_CURRENT_SOURCE_UNAVAILABLE",)


def test_permission_denied_capture_is_unavailable_without_manual_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        exposure,
        "_capture_manual_valuation_source",
        lambda: (_ for _ in ()).throw(AssertionError("manual fallback forbidden")),
    )

    def _permission_denied(*_args: object, **_kwargs: object) -> bytes:
        raise MmiStableReadError(
            MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID,
            os_error_errno=errno.EACCES,
        )

    monkeypatch.setattr(capture, "stable_read_exact_bytes", _permission_denied)
    result = _observe(tmp_path, monkeypatch)

    assert result.status is exposure.ExposureObservationStatus.UNAVAILABLE
    assert result.projection is None
    assert result.reason_codes == (
        "YFINANCE_CAPTURE_CURRENT_SOURCE_UNREADABLE",
    )


@pytest.mark.parametrize(
    "raw",
    (b"{", b'{"authority_effect":"NONE"}'),
)
def test_present_malformed_capture_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
) -> None:
    result = _observe(tmp_path, monkeypatch, capture_raw=raw)
    assert result.status is exposure.ExposureObservationStatus.INVALID
    assert result.projection is None


def test_canonical_byte_mutation_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(tmp_path, monkeypatch, capture_raw=b"{}\n")
    assert result.status is exposure.ExposureObservationStatus.INVALID
    assert result.projection is None


def test_current_portfolio_source_mismatch_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        capture_source_sha256="0" * 64,
    )
    assert result.status is exposure.ExposureObservationStatus.UNAVAILABLE
    assert result.reason_codes == (
        "REPORT_ONLY_EXPOSURE_CAPTURE_PORTFOLIO_SOURCE_MISMATCH",
    )


def test_same_source_capture_domain_contradiction_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        capture_tickers=("QQQ",),
    )
    assert result.status is exposure.ExposureObservationStatus.INVALID
    assert result.reason_codes == (
        "REPORT_ONLY_EXPOSURE_CAPTURE_TICKER_DOMAIN_MISMATCH",
    )


@pytest.mark.parametrize(
    ("completed_session", "status", "reason"),
    (
        (
            _session(
                session_date="2026-08-11",
                official_close_timestamp_et="2026-08-11T16:00:00-04:00",
                trusted_evaluation_timestamp_utc="2026-08-11T20:00:00Z",
            ),
            exposure.ExposureObservationStatus.UNAVAILABLE,
            "US_EQUITY_SESSION_MARK_DATE_STALE",
        ),
        (
            _session(
                session_date="2026-08-13",
                official_close_timestamp_et="2026-08-13T16:00:00-04:00",
                trusted_evaluation_timestamp_utc="2026-08-13T20:00:00Z",
            ),
            exposure.ExposureObservationStatus.INVALID,
            "US_EQUITY_SESSION_MARK_DATE_AFTER_EVALUATION",
        ),
    ),
)
def test_capture_session_freshness_rejects_stale_and_future(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed_session: CompletedUsEquitySession,
    status: exposure.ExposureObservationStatus,
    reason: str,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        completed_session=completed_session,
    )
    assert result.status is status
    assert result.reason_codes == (reason,)
    assert result.projection is None


def test_capture_calendar_or_close_provenance_mismatch_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        completed_session=_session(calendar_schedule_sha256="f" * 64),
    )
    assert result.status is exposure.ExposureObservationStatus.INVALID
    assert result.reason_codes == (
        "REPORT_ONLY_EXPOSURE_CAPTURE_CALENDAR_PROVENANCE_MISMATCH",
    )

    result = _observe(
        tmp_path,
        monkeypatch,
        completed_session=_session(
            official_close_timestamp_et="2026-08-12T15:00:00-04:00",
        ),
    )
    assert result.status is exposure.ExposureObservationStatus.INVALID
    assert result.reason_codes == (
        "REPORT_ONLY_EXPOSURE_CAPTURE_SESSION_PROVENANCE_MISMATCH",
    )


def test_malformed_holdings_precede_a_valid_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        portfolio_bytes=_portfolio(("QQQ | 0",)),
    )
    assert result.status is exposure.ExposureObservationStatus.INVALID
    assert result.reason_codes == (
        "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SHARES_INVALID",
    )


def test_missing_strict_holdings_precede_capture_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = (repo_root() / "inputs/current/portfolio_snapshot.txt").read_bytes()
    without_strict, marker, _existing = current.partition(
        b"\n[STRICT_POSITIVE_ETF_POSITIONS_V1]\n"
    )
    assert marker
    result = _observe(
        tmp_path,
        monkeypatch,
        portfolio_bytes=without_strict,
        capture_source_sha256=False,
    )
    assert result.status is exposure.ExposureObservationStatus.UNAVAILABLE
    assert result.reason_codes == (
        "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SECTION_ABSENT",
    )


def test_unknown_policy_ticker_remains_manual_review_after_capture_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        portfolio_bytes=_portfolio(("QQQ | 1", "ZZZ | 2")),
        capture_marks={"QQQ": "400", "ZZZ": "100"},
    )
    assert result.status is exposure.ExposureObservationStatus.MANUAL_REVIEW
    assert result.reason_codes == (
        "REPORT_ONLY_EXPOSURE_TICKER_OUTSIDE_DETERMINISTIC_POLICY",
    )
    assert result.projection is not None
    assert result.projection.positions[1].classification == "UNCLASSIFIED"


def test_observer_stays_provider_free_and_disconnected_from_authority_flows() -> None:
    root = Path(__file__).resolve().parents[2] / "src/investment_orchestrator"
    module_file = root / "observability/report_only_holdings_exposure.py"
    calendar_file = root / "market/us_equity_session_calendar.py"
    capture_file = root / "market/us_equity_yfinance_valuation_capture.py"
    module_text = module_file.read_text(encoding="utf-8")
    assert "import yfinance" not in module_text
    assert "yf.download" not in module_text
    assert "subprocess" not in module_text
    assert all(
        value not in module_text
        for value in (
            "investment_orchestrator.workflow",
            "investment_orchestrator.state",
            "investment_orchestrator.permissions",
            "investment_orchestrator.orders",
            "investment_orchestrator.broker",
            "investment_orchestrator.llm",
        )
    )
    assert all(
        "report_only_holdings_exposure" not in candidate.read_text(encoding="utf-8")
        and "us_equity_session_calendar" not in candidate.read_text(encoding="utf-8")
        and "us_equity_yfinance_valuation_capture" not in candidate.read_text(encoding="utf-8")
        for candidate in root.rglob("*.py")
        if candidate not in {module_file, calendar_file, capture_file}
    )
