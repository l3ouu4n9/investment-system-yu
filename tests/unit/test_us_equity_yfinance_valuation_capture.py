"""Focused contracts for the isolated yfinance valuation-capture foundation."""

from __future__ import annotations

from datetime import datetime, timezone
import errno
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from investment_orchestrator.market import us_equity_yfinance_valuation_capture as capture
from investment_orchestrator.market.us_equity_session_calendar import (
    CompletedUsEquitySession,
    MarkFreshnessStatus,
    UsEquitySessionResolutionError,
)
from investment_orchestrator.common.stable_read import (
    MmiStableReadError,
    MmiStableReadErrorCode,
)
from investment_orchestrator.observability.report_only_holdings_exposure import (
    StrictHoldingsDomain,
    StrictHoldingsDomainError,
)


_SESSION_DATE = "2026-08-12"
_SCHEDULE_SHA256 = (
    "a7142dcf13f52f30f07cc48942abe1e325ace21d644a2198c5e5667cf9d20007"
)
_PORTFOLIO_SOURCE_SHA256 = "b" * 64


def _session() -> CompletedUsEquitySession:
    return CompletedUsEquitySession(
        authority_effect="NONE",
        calendar_id="US_EQUITY_REGULAR",
        calendar_schedule_sha256=_SCHEDULE_SHA256,
        coverage_start_date="2026-01-01",
        coverage_end_date="2026-12-31",
        trusted_evaluation_timestamp_utc="2026-08-12T20:00:00Z",
        session_date=_SESSION_DATE,
        official_close_timestamp_et="2026-08-12T16:00:00-04:00",
    )


def _history(
    *,
    close: object = 100.25,
    adj_close: object = 99.75,
    dates: tuple[str, ...] = (_SESSION_DATE,),
) -> pd.DataFrame:
    count = len(dates)
    return pd.DataFrame(
        {
            "Open": [99.0] * count,
            "High": [101.0] * count,
            "Low": [98.0] * count,
            "Close": [close] * count,
            "Adj Close": [adj_close] * count,
            "Volume": [1000] * count,
        },
        index=pd.DatetimeIndex(dates),
    )


def _configure_prerequisites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tickers: tuple[str, ...] = ("QQQ", "SMH"),
) -> None:
    monkeypatch.setattr(capture, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        capture,
        "capture_current_validated_strict_holdings_domain",
        lambda **_kwargs: StrictHoldingsDomain(
            portfolio_source_sha256=_PORTFOLIO_SOURCE_SHA256,
            portfolio_scope_id="yu_etf_portfolio",
            tickers=tickers,
        ),
    )
    monkeypatch.setattr(
        capture,
        "begin_mmi_projection_run",
        lambda: object(),
    )
    monkeypatch.setattr(
        capture,
        "resolve_trusted_completed_us_equity_session",
        lambda **_kwargs: _session(),
    )
    monkeypatch.setattr(
        capture,
        "_captured_at_utc",
        lambda: "2026-08-12T20:01:02Z",
    )


def _artifact_path(tmp_path: Path) -> Path:
    return tmp_path / "artifacts/current/us_equity_yfinance_valuation/capture.json"


def _configure_provider(
    monkeypatch: pytest.MonkeyPatch,
    download: object,
) -> None:
    provider = SimpleNamespace(download=download)
    monkeypatch.setattr(
        capture,
        "_provider_modules",
        lambda: (pd, provider),
    )


def _normalized_capture_payload(
    *,
    portfolio_source_sha256: str = _PORTFOLIO_SOURCE_SHA256,
    tickers: tuple[str, ...] = ("QQQ", "SMH"),
    session: CompletedUsEquitySession | None = None,
) -> dict[str, object]:
    completed_session = session if session is not None else _session()
    return capture._capture_payload(
        holdings=StrictHoldingsDomain(
            portfolio_source_sha256=portfolio_source_sha256,
            portfolio_scope_id="yu_etf_portfolio",
            tickers=tickers,
        ),
        completed_session=completed_session,
        yfinance_version="1.4.1",
        pandas_version="3.0.3",
        marks=tuple(
            {
                "ticker": ticker,
                "actual_data_date": completed_session.session_date,
                "provider_field": "Close",
                "mark": str(index + 100),
            }
            for index, ticker in enumerate(sorted(tickers))
        ),
    )


def _write_normalized_capture(
    tmp_path: Path,
    payload: dict[str, object],
) -> bytes:
    path = _artifact_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = capture.canonical_json_bytes(payload, maximum_bytes=262_144)
    path.write_bytes(raw)
    return raw


def _capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return capture.capture_current_us_equity_yfinance_valuation(
        portfolio_snapshot_expected_sha256="a" * 64,
    )


def test_public_capture_has_only_the_fixed_current_source_parameter() -> None:
    parameters = inspect.signature(
        capture.capture_current_us_equity_yfinance_valuation
    ).parameters
    assert set(parameters) == {"portfolio_snapshot_expected_sha256"}
    source = Path(capture.__file__).read_text(encoding="utf-8")
    for prohibited in (
        "infer_target_close_date",
        "previous_weekday",
        "run_timestamp_et",
        "target_close_date_et",
        "period=\"1y\"",
    ):
        assert prohibited not in source


def test_prerequisite_failure_never_reaches_yfinance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _unexpected_download(*_args: object, **_kwargs: object):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    _configure_provider(monkeypatch, _unexpected_download)
    monkeypatch.setattr(
        capture,
        "capture_current_validated_strict_holdings_domain",
        lambda **_kwargs: (_ for _ in ()).throw(
            StrictHoldingsDomainError(
                ("REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SECTION_ABSENT",),
                unavailable=True,
            )
        ),
    )
    result = _capture(tmp_path, monkeypatch)
    assert result.status is capture.YfinanceValuationCaptureStatus.UNAVAILABLE
    assert called is False

    _configure_prerequisites(tmp_path, monkeypatch)
    monkeypatch.setattr(
        capture,
        "resolve_trusted_completed_us_equity_session",
        lambda **_kwargs: (_ for _ in ()).throw(
            UsEquitySessionResolutionError(
                MarkFreshnessStatus.UNAVAILABLE,
                ("US_EQUITY_SESSION_CALENDAR_COVERAGE_INSUFFICIENT",),
            )
        ),
    )
    result = _capture(tmp_path, monkeypatch)
    assert result.status is capture.YfinanceValuationCaptureStatus.UNAVAILABLE
    assert called is False


def test_complete_capture_uses_trusted_session_raw_close_and_pinned_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_prerequisites(tmp_path, monkeypatch, tickers=("SMH", "QQQ"))
    calls: list[tuple[str, dict[str, object]]] = []

    def _download(ticker: str, **kwargs: object) -> pd.DataFrame:
        calls.append((ticker, kwargs))
        return _history(close=100.25 if ticker == "QQQ" else 200.5, adj_close=1)

    _configure_provider(monkeypatch, _download)
    atomic_calls: list[tuple[Path, str]] = []
    real_atomic_write = capture.atomic_write_text

    def _atomic_write(path: Path, text: str) -> Path:
        atomic_calls.append((path, text))
        return real_atomic_write(path, text)

    monkeypatch.setattr(capture, "atomic_write_text", _atomic_write)
    result = _capture(tmp_path, monkeypatch)

    assert result.status is capture.YfinanceValuationCaptureStatus.CAPTURED
    assert result.authority_effect == "NONE"
    assert result.reason_codes == ("YFINANCE_CAPTURE_COMPLETE",)
    assert result.artifact_repository_relative_path == (
        "artifacts/current/us_equity_yfinance_valuation/capture.json"
    )
    assert result.artifact_sha256 is not None
    expected_kwargs = {
        "start": "2026-08-12",
        "end": "2026-08-13",
        "actions": False,
        "threads": False,
        "ignore_tz": True,
        "group_by": "column",
        "auto_adjust": False,
        "back_adjust": False,
        "repair": False,
        "keepna": False,
        "progress": False,
        "period": None,
        "interval": "1d",
        "prepost": False,
        "rounding": False,
        "timeout": 10,
        "session": None,
        "multi_level_index": False,
    }
    assert calls == [("QQQ", expected_kwargs), ("SMH", expected_kwargs)]
    assert len(atomic_calls) == 1
    assert atomic_calls[0][0] == _artifact_path(tmp_path)
    artifact_bytes = _artifact_path(tmp_path).read_bytes()
    assert artifact_bytes == atomic_calls[0][1].encode("utf-8")
    assert hashlib.sha256(artifact_bytes).hexdigest() == result.artifact_sha256
    payload = json.loads(artifact_bytes)
    assert "capture_identity_sha256" not in payload
    assert "expected_portfolio_sha256" not in payload
    assert payload["authority_effect"] == "NONE"
    assert payload["portfolio_source_sha256"] == _PORTFOLIO_SOURCE_SHA256
    assert payload["requested_ticker_domain"] == ["QQQ", "SMH"]
    assert payload["completed_session"]["session_date"] == _SESSION_DATE
    assert payload["marks"] == [
        {
            "actual_data_date": _SESSION_DATE,
            "mark": "100.25",
            "provider_field": "Close",
            "ticker": "QQQ",
        },
        {
            "actual_data_date": _SESSION_DATE,
            "mark": "200.5",
            "provider_field": "Close",
            "ticker": "SMH",
        },
    ]
    assert all(type(mark["mark"]) is str for mark in payload["marks"])
    assert payload["client_version"] == "1.4.1"
    assert payload["pandas_version"] == "3.0.3"


@pytest.mark.parametrize(
    "history",
    (
        _history(dates=("2026-08-11",)),
        _history(dates=("2026-08-13",)),
        pd.DataFrame(
            {
                "Open": [99.0, 99.0],
                "High": [101.0, 101.0],
                "Low": [98.0, 98.0],
                "Close": [100.0, 100.0],
                "Adj Close": [99.0, 99.0],
                "Volume": [1000, 1000],
            },
            index=pd.DatetimeIndex([_SESSION_DATE, _SESSION_DATE]),
        ),
        _history(close=float("nan")),
        _history(close=float("inf")),
        _history(close=0.0),
        _history(close=-1.0),
        _history().drop(columns=["Adj Close"]),
        pd.DataFrame(
            {
                "Open": [99.0],
                "High": [101.0],
                "Low": [98.0],
                "Close": [100.0],
                "Adj Close": [99.0],
                "Volume": [1000],
            },
            index=[_SESSION_DATE],
        ),
    ),
)
def test_invalid_or_nonexact_provider_content_never_writes_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    history: pd.DataFrame,
) -> None:
    _configure_prerequisites(tmp_path, monkeypatch, tickers=("QQQ",))
    _configure_provider(monkeypatch, lambda *_args, **_kwargs: history)
    result = _capture(tmp_path, monkeypatch)
    assert result.status in {
        capture.YfinanceValuationCaptureStatus.UNAVAILABLE,
        capture.YfinanceValuationCaptureStatus.INVALID,
    }
    assert result.status is not capture.YfinanceValuationCaptureStatus.CAPTURED
    assert not _artifact_path(tmp_path).exists()


def test_network_or_empty_single_ticker_failure_preserves_prior_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_prerequisites(tmp_path, monkeypatch, tickers=("QQQ",))
    _configure_provider(monkeypatch, lambda *_args, **_kwargs: _history())
    success = _capture(tmp_path, monkeypatch)
    assert success.status is capture.YfinanceValuationCaptureStatus.CAPTURED
    prior = _artifact_path(tmp_path).read_bytes()

    _configure_provider(
        monkeypatch,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("offline")),
    )
    unavailable = _capture(tmp_path, monkeypatch)
    assert unavailable.status is capture.YfinanceValuationCaptureStatus.UNAVAILABLE
    assert unavailable.reason_codes == ("YFINANCE_CAPTURE_PROVIDER_REQUEST_UNAVAILABLE",)
    assert _artifact_path(tmp_path).read_bytes() == prior

    _configure_provider(
        monkeypatch,
        lambda *_args, **_kwargs: pd.DataFrame(
            columns=list(capture._EXPECTED_COLUMNS)
        ),
    )
    unavailable = _capture(tmp_path, monkeypatch)
    assert unavailable.status is capture.YfinanceValuationCaptureStatus.UNAVAILABLE
    assert unavailable.reason_codes == ("YFINANCE_CAPTURE_HISTORY_UNAVAILABLE",)
    assert _artifact_path(tmp_path).read_bytes() == prior


def test_complete_domain_is_all_or_nothing_and_duplicate_holdings_fail_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_prerequisites(tmp_path, monkeypatch, tickers=("QQQ", "SMH"))
    calls: list[str] = []

    def _download(ticker: str, **_kwargs: object) -> pd.DataFrame:
        calls.append(ticker)
        if ticker == "SMH":
            return pd.DataFrame(columns=list(capture._EXPECTED_COLUMNS))
        return _history()

    _configure_provider(monkeypatch, _download)
    result = _capture(tmp_path, monkeypatch)
    assert result.status is capture.YfinanceValuationCaptureStatus.UNAVAILABLE
    assert calls == ["QQQ", "SMH"]
    assert not _artifact_path(tmp_path).exists()

    _configure_prerequisites(tmp_path, monkeypatch, tickers=("QQQ", "QQQ"))
    calls.clear()
    result = _capture(tmp_path, monkeypatch)
    assert result.status is capture.YfinanceValuationCaptureStatus.INVALID
    assert result.reason_codes == ("YFINANCE_CAPTURE_HOLDINGS_DOMAIN_DUPLICATE",)
    assert calls == []


def test_normalized_capture_rejects_any_mark_domain_mismatch() -> None:
    with pytest.raises(capture._CaptureFailure) as exc_info:
        capture._capture_payload(
            holdings=StrictHoldingsDomain(
                portfolio_source_sha256=_PORTFOLIO_SOURCE_SHA256,
                portfolio_scope_id="yu_etf_portfolio",
                tickers=("QQQ", "SMH"),
            ),
            completed_session=_session(),
            yfinance_version="1.4.1",
            pandas_version="3.0.3",
            marks=(
                {
                    "ticker": "QQQ",
                    "actual_data_date": _SESSION_DATE,
                    "provider_field": "Close",
                    "mark": "100",
                },
            ),
        )
    assert exc_info.value.status is capture.YfinanceValuationCaptureStatus.INVALID
    assert exc_info.value.code == "YFINANCE_CAPTURE_MARK_DOMAIN_MISMATCH"


def test_normalized_capture_rejects_noncanonical_ticker_order() -> None:
    payload = capture._capture_payload(
        holdings=StrictHoldingsDomain(
            portfolio_source_sha256=_PORTFOLIO_SOURCE_SHA256,
            portfolio_scope_id="yu_etf_portfolio",
            tickers=("QQQ", "SMH"),
        ),
        completed_session=_session(),
        yfinance_version="1.4.1",
        pandas_version="3.0.3",
        marks=(
            {
                "ticker": "QQQ",
                "actual_data_date": _SESSION_DATE,
                "provider_field": "Close",
                "mark": "100",
            },
            {
                "ticker": "SMH",
                "actual_data_date": _SESSION_DATE,
                "provider_field": "Close",
                "mark": "200",
            },
        ),
    )
    payload["requested_ticker_domain"] = ["SMH", "QQQ"]
    payload["marks"] = list(reversed(payload["marks"]))
    with pytest.raises(capture._CaptureFailure) as exc_info:
        capture._validate_capture_payload(payload)
    assert exc_info.value.status is capture.YfinanceValuationCaptureStatus.INVALID
    assert exc_info.value.code == "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID"


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload.pop("portfolio_source_sha256"),
        lambda payload: payload.__setitem__("portfolio_source_sha256", "not-a-sha"),
    ),
)
def test_normalized_capture_requires_the_bound_portfolio_source_sha(
    mutation,
) -> None:
    payload = capture._capture_payload(
        holdings=StrictHoldingsDomain(
            portfolio_source_sha256=_PORTFOLIO_SOURCE_SHA256,
            portfolio_scope_id="yu_etf_portfolio",
            tickers=("QQQ",),
        ),
        completed_session=_session(),
        yfinance_version="1.4.1",
        pandas_version="3.0.3",
        marks=(
            {
                "ticker": "QQQ",
                "actual_data_date": _SESSION_DATE,
                "provider_field": "Close",
                "mark": "100",
            },
        ),
    )
    mutation(payload)
    with pytest.raises(capture._CaptureFailure) as exc_info:
        capture._validate_capture_payload(payload)
    assert exc_info.value.status is capture.YfinanceValuationCaptureStatus.INVALID
    assert exc_info.value.code == "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID"


def test_runtime_version_drift_fails_closed_before_provider_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_prerequisites(tmp_path, monkeypatch, tickers=("QQQ",))
    called = False

    def _unexpected_download(*_args: object, **_kwargs: object):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    _configure_provider(monkeypatch, _unexpected_download)
    monkeypatch.setattr(capture, "version", lambda _name: "0.0.0")
    result = _capture(tmp_path, monkeypatch)
    assert result.status is capture.YfinanceValuationCaptureStatus.INVALID
    assert result.reason_codes == ("YFINANCE_CAPTURE_YFINANCE_VERSION_UNSUPPORTED",)
    assert called is False


def test_fixed_capture_reader_is_parameterless_canonical_and_provider_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture, "repo_root", lambda: tmp_path)
    raw = _write_normalized_capture(tmp_path, _normalized_capture_payload())
    monkeypatch.setattr(
        capture,
        "_provider_modules",
        lambda: (_ for _ in ()).throw(AssertionError("provider import forbidden")),
    )

    validated = (
        capture.read_current_validated_us_equity_yfinance_valuation_capture()
    )

    assert not inspect.signature(
        capture.read_current_validated_us_equity_yfinance_valuation_capture
    ).parameters
    assert validated.artifact_sha256 == hashlib.sha256(raw).hexdigest()
    assert validated.portfolio_source_sha256 == _PORTFOLIO_SOURCE_SHA256
    assert validated.requested_ticker_domain == ("QQQ", "SMH")
    assert validated.marks == (("QQQ", "100"), ("SMH", "101"))
    assert validated.session_date.isoformat() == _SESSION_DATE


@pytest.mark.parametrize(
    ("raw", "reason", "unavailable"),
    (
        (b"{", "YFINANCE_CAPTURE_CURRENT_SOURCE_JSON_INVALID", False),
        (
            b'{"authority_effect":"NONE"}',
            "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
            False,
        ),
    ),
)
def test_fixed_capture_reader_rejects_present_malformed_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    reason: str,
    unavailable: bool,
) -> None:
    monkeypatch.setattr(capture, "repo_root", lambda: tmp_path)
    path = _artifact_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)

    with pytest.raises(capture.CurrentYfinanceValuationCaptureError) as exc_info:
        capture.read_current_validated_us_equity_yfinance_valuation_capture()

    assert exc_info.value.reason_codes == (reason,)
    assert exc_info.value.unavailable is unavailable


def test_fixed_capture_reader_requires_exact_canonical_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture, "repo_root", lambda: tmp_path)
    raw = _write_normalized_capture(tmp_path, _normalized_capture_payload())
    _artifact_path(tmp_path).write_bytes(raw + b"\n")

    with pytest.raises(capture.CurrentYfinanceValuationCaptureError) as exc_info:
        capture.read_current_validated_us_equity_yfinance_valuation_capture()

    assert exc_info.value.reason_codes == (
        "YFINANCE_CAPTURE_CURRENT_SOURCE_CANONICAL_INVALID",
    )
    assert exc_info.value.unavailable is False


def test_fixed_capture_reader_maps_absence_to_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture, "repo_root", lambda: tmp_path)

    with pytest.raises(capture.CurrentYfinanceValuationCaptureError) as exc_info:
        capture.read_current_validated_us_equity_yfinance_valuation_capture()

    assert exc_info.value.unavailable is True
    assert exc_info.value.reason_codes == (
        "YFINANCE_CAPTURE_CURRENT_SOURCE_UNAVAILABLE",
    )


@pytest.mark.parametrize("error_number", (errno.EACCES, errno.EPERM))
def test_fixed_capture_reader_maps_permission_denied_to_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
) -> None:
    monkeypatch.setattr(capture, "repo_root", lambda: tmp_path)
    _write_normalized_capture(tmp_path, _normalized_capture_payload())

    def _permission_denied(*_args: object, **_kwargs: object) -> bytes:
        raise MmiStableReadError(
            MmiStableReadErrorCode.STABLE_READ_INPUT_INVALID,
            os_error_errno=error_number,
        )

    monkeypatch.setattr(capture, "stable_read_exact_bytes", _permission_denied)

    with pytest.raises(capture.CurrentYfinanceValuationCaptureError) as exc_info:
        capture.read_current_validated_us_equity_yfinance_valuation_capture()

    assert exc_info.value.reason_codes == (
        "YFINANCE_CAPTURE_CURRENT_SOURCE_UNREADABLE",
    )
    assert exc_info.value.unavailable is True


def test_capture_never_changes_observer_or_prompt_network_boundary() -> None:
    observer_path = (
        Path(__file__).parents[2]
        / "src/investment_orchestrator/observability/report_only_holdings_exposure.py"
    )
    observer_source = observer_path.read_text(encoding="utf-8")
    assert "import yfinance" not in observer_source
    assert "yf.download" not in observer_source
    assert "subprocess" not in observer_source
