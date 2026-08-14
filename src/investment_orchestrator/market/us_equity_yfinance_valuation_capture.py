"""Explicit, report-only yfinance capture for one trusted US-equity session.

This module acquires no holdings facts and resolves no sessions itself.  It
uses the existing strict-holdings and reviewed-calendar owners, then persists
one complete normalized close capture for later report-only consumption.
Nothing here invokes the exposure observer's valuation path, prompts, gates,
publication, orders, or broker code.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import errno
from enum import Enum
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import stat
from typing import Any, Final

from investment_orchestrator.common.io import atomic_write_text
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MmiCanonicalizationError,
    canonical_json_bytes,
    normalize_decimal_string,
)
from investment_orchestrator.mmi.contracts import (
    begin_mmi_projection_run,
)
from investment_orchestrator.common.stable_read import (
    MmiStableReadError,
    MmiStableReadErrorCode,
    stable_read_exact_bytes,
)
from investment_orchestrator.market.us_equity_session_calendar import (
    CompletedUsEquitySession,
    MarkFreshnessStatus,
    UsEquitySessionResolutionError,
    resolve_trusted_completed_us_equity_session,
)
from investment_orchestrator.observability.report_only_holdings_exposure import (
    StrictHoldingsDomain,
    StrictHoldingsDomainError,
    capture_current_validated_strict_holdings_domain,
)


__all__ = (
    "CurrentYfinanceValuationCaptureError",
    "ValidatedYfinanceValuationCapture",
    "YfinanceValuationCaptureResult",
    "YfinanceValuationCaptureStatus",
    "capture_current_us_equity_yfinance_valuation",
    "read_current_validated_us_equity_yfinance_valuation_capture",
)


_CAPTURE_RELATIVE_PATH: Final = (
    "artifacts",
    "current",
    "us_equity_yfinance_valuation",
    "capture.json",
)
_SCHEMA_VERSION: Final = "us_equity_yfinance_valuation_capture_v1"
_ACQUISITION_CONTRACT_VERSION: Final = "us_equity_yfinance_raw_close_v1"
_AUTHORITY_EFFECT_NONE: Final = "NONE"
_SOURCE_KIND: Final = "EXTERNAL_MARKET_DATA_CAPTURE"
_PROVIDER_ID: Final = "YAHOO_FINANCE"
_CLIENT_LIBRARY: Final = "yfinance"
_PROVIDER_FIELD: Final = "Close"
_REVIEWED_YFINANCE_VERSION: Final = "1.4.1"
_REVIEWED_PANDAS_VERSION: Final = "3.0.3"
_REQUEST_TIMEOUT_SECONDS: Final = 10
_CAPTURE_MAXIMUM_CANONICAL_BYTES: Final = 262_144
_EXPECTED_COLUMNS: Final = frozenset(
    {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
)


class YfinanceValuationCaptureStatus(str, Enum):
    """Closed, non-authorizing status for one capture attempt."""

    CAPTURED = "CAPTURED"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class YfinanceValuationCaptureResult:
    """One report-only capture result; no result grants market authority."""

    authority_effect: str
    status: YfinanceValuationCaptureStatus
    reason_codes: tuple[str, ...]
    artifact_repository_relative_path: str | None
    artifact_sha256: str | None


class CurrentYfinanceValuationCaptureError(ValueError):
    """Closed fixed-source failure for the normalized current capture."""

    def __init__(
        self,
        reason_codes: tuple[str, ...],
        *,
        unavailable: bool,
    ) -> None:
        super().__init__(
            reason_codes[0] if reason_codes else "YFINANCE_CAPTURE_INVALID"
        )
        self.reason_codes = reason_codes
        self.unavailable = unavailable


@dataclass(frozen=True, slots=True)
class ValidatedYfinanceValuationCapture:
    """One exact-byte, schema-validated, normalized current capture."""

    artifact_sha256: str
    portfolio_source_sha256: str
    portfolio_scope_id: str
    source_kind: str
    provider_id: str
    requested_ticker_domain: tuple[str, ...]
    marks: tuple[tuple[str, str], ...]
    calendar_id: str
    calendar_schedule_sha256: str
    capture_trusted_evaluation_timestamp_utc: str
    session_date: date
    official_close_timestamp_et: str


class _CaptureFailure(ValueError):
    def __init__(
        self,
        status: YfinanceValuationCaptureStatus,
        code: str,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _result(
    status: YfinanceValuationCaptureStatus,
    reason_codes: tuple[str, ...],
    *,
    artifact_sha256: str | None = None,
) -> YfinanceValuationCaptureResult:
    return YfinanceValuationCaptureResult(
        authority_effect=_AUTHORITY_EFFECT_NONE,
        status=status,
        reason_codes=reason_codes,
        artifact_repository_relative_path=(
            "/".join(_CAPTURE_RELATIVE_PATH)
            if artifact_sha256 is not None
            else None
        ),
        artifact_sha256=artifact_sha256,
    )


def _capture_path() -> Path:
    return repo_root().joinpath(*_CAPTURE_RELATIVE_PATH)


def _open_current_capture_directory(root: Path) -> tuple[int, int]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    root_fd: int | None = None
    artifacts_fd: int | None = None
    current_fd: int | None = None
    capture_fd: int | None = None
    try:
        root_fd = os.open(os.fspath(root), flags)
        artifacts_fd = os.open("artifacts", flags, dir_fd=root_fd)
        current_fd = os.open("current", flags, dir_fd=artifacts_fd)
        capture_fd = os.open(
            "us_equity_yfinance_valuation",
            flags,
            dir_fd=current_fd,
        )
        if not all(
            stat.S_ISDIR(os.fstat(fd).st_mode)
            for fd in (root_fd, artifacts_fd, current_fd, capture_fd)
        ):
            raise OSError("current capture hierarchy is not a directory")
        return root_fd, capture_fd
    except OSError:
        for fd in (capture_fd, current_fd, artifacts_fd, root_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        raise
    finally:
        for fd in (current_fd, artifacts_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _read_current_capture_bytes() -> bytes:
    try:
        root_fd, capture_fd = _open_current_capture_directory(repo_root())
    except OSError as exc:
        raise CurrentYfinanceValuationCaptureError(
            ("YFINANCE_CAPTURE_CURRENT_SOURCE_UNAVAILABLE",),
            unavailable=True,
        ) from exc
    try:
        try:
            return stable_read_exact_bytes(
                capture_fd,
                "capture.json",
                maximum_bytes=_CAPTURE_MAXIMUM_CANONICAL_BYTES,
            )
        except MmiStableReadError as exc:
            if exc.os_error_errno in {errno.EACCES, errno.EPERM}:
                raise CurrentYfinanceValuationCaptureError(
                    ("YFINANCE_CAPTURE_CURRENT_SOURCE_UNREADABLE",),
                    unavailable=True,
                ) from exc
            if exc.code is MmiStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE:
                raise CurrentYfinanceValuationCaptureError(
                    ("YFINANCE_CAPTURE_CURRENT_SOURCE_UNAVAILABLE",),
                    unavailable=True,
                ) from exc
            try:
                os.stat("capture.json", dir_fd=capture_fd, follow_symlinks=False)
            except FileNotFoundError:
                raise CurrentYfinanceValuationCaptureError(
                    ("YFINANCE_CAPTURE_CURRENT_SOURCE_ABSENT",),
                    unavailable=True,
                ) from exc
            except OSError:
                raise CurrentYfinanceValuationCaptureError(
                    ("YFINANCE_CAPTURE_CURRENT_SOURCE_UNREADABLE",),
                    unavailable=True,
                ) from exc
            raise CurrentYfinanceValuationCaptureError(
                ("YFINANCE_CAPTURE_CURRENT_SOURCE_INVALID",),
                unavailable=False,
            ) from exc
        except OSError as exc:
            raise CurrentYfinanceValuationCaptureError(
                ("YFINANCE_CAPTURE_CURRENT_SOURCE_UNREADABLE",),
                unavailable=True,
            ) from exc
    finally:
        os.close(capture_fd)
        os.close(root_fd)


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate JSON key")
        output[key] = value
    return output


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON constant")


def _validated_capture_from_payload(
    *,
    payload: object,
    artifact_sha256: str,
) -> ValidatedYfinanceValuationCapture:
    _validate_capture_payload(payload)
    if type(payload) is not dict:  # pragma: no cover - guarded above.
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
        )
    completed_session = payload["completed_session"]
    requested = payload["requested_ticker_domain"]
    marks = payload["marks"]
    if (
        type(completed_session) is not dict
        or type(requested) is not list
        or type(marks) is not list
    ):
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
        )
    try:
        session_date = date.fromisoformat(completed_session["session_date"])
        capture_evaluation = datetime.fromisoformat(
            completed_session["trusted_evaluation_timestamp_utc"].replace(
                "Z", "+00:00"
            )
        )
        official_close = datetime.fromisoformat(
            completed_session["official_close_timestamp_et"]
        )
    except (TypeError, ValueError, AttributeError):
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_COMPLETED_SESSION_INVALID",
        ) from None
    if (
        capture_evaluation.tzinfo is None
        or official_close.tzinfo is None
        or official_close.date() != session_date
        or capture_evaluation < official_close.astimezone(timezone.utc)
    ):
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_COMPLETED_SESSION_INVALID",
        )
    return ValidatedYfinanceValuationCapture(
        artifact_sha256=artifact_sha256,
        portfolio_source_sha256=payload["portfolio_source_sha256"],
        portfolio_scope_id=payload["portfolio_scope_id"],
        source_kind=payload["source_kind"],
        provider_id=payload["provider_id"],
        requested_ticker_domain=tuple(requested),
        marks=tuple((mark["ticker"], mark["mark"]) for mark in marks),
        calendar_id=completed_session["calendar_id"],
        calendar_schedule_sha256=completed_session["calendar_schedule_sha256"],
        capture_trusted_evaluation_timestamp_utc=(
            completed_session["trusted_evaluation_timestamp_utc"]
        ),
        session_date=session_date,
        official_close_timestamp_et=(
            completed_session["official_close_timestamp_et"]
        ),
    )


def read_current_validated_us_equity_yfinance_valuation_capture(
) -> ValidatedYfinanceValuationCapture:
    """Read only the fixed normalized current capture; never contact a provider."""
    raw_bytes = _read_current_capture_bytes()
    artifact_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
        if text.startswith("\ufeff") or "\x00" in text:
            raise ValueError("invalid capture text")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise CurrentYfinanceValuationCaptureError(
            ("YFINANCE_CAPTURE_CURRENT_SOURCE_JSON_INVALID",),
            unavailable=False,
        ) from exc
    try:
        capture = _validated_capture_from_payload(
            payload=payload,
            artifact_sha256=artifact_sha256,
        )
        canonical_bytes = canonical_json_bytes(
            payload,
            maximum_bytes=_CAPTURE_MAXIMUM_CANONICAL_BYTES,
        )
    except _CaptureFailure as exc:
        raise CurrentYfinanceValuationCaptureError(
            (exc.code,),
            unavailable=False,
        ) from exc
    except MmiCanonicalizationError as exc:
        raise CurrentYfinanceValuationCaptureError(
            ("YFINANCE_CAPTURE_CURRENT_SOURCE_INVALID",),
            unavailable=False,
        ) from exc
    if canonical_bytes != raw_bytes:
        raise CurrentYfinanceValuationCaptureError(
            ("YFINANCE_CAPTURE_CURRENT_SOURCE_CANONICAL_INVALID",),
            unavailable=False,
        )
    return capture


def _runtime_versions() -> tuple[str, str]:
    try:
        yfinance_version = version("yfinance")
        pandas_version = version("pandas")
    except PackageNotFoundError as exc:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_RUNTIME_DEPENDENCY_UNAVAILABLE",
        ) from exc
    if yfinance_version != _REVIEWED_YFINANCE_VERSION:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_YFINANCE_VERSION_UNSUPPORTED",
        )
    if pandas_version != _REVIEWED_PANDAS_VERSION:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_PANDAS_VERSION_UNSUPPORTED",
        )
    return yfinance_version, pandas_version


def _provider_modules() -> tuple[Any, Any]:
    """Load provider dependencies only on the explicit acquisition path."""
    import pandas as pandas_module
    import yfinance as yfinance_module

    return pandas_module, yfinance_module


def _download_history_for_session(
    *,
    ticker: str,
    session_date: date,
) -> object:
    """Request a one-civil-day window; calendar ownership remains upstream."""
    _pandas_module, yfinance_module = _provider_modules()
    return yfinance_module.download(
        ticker,
        start=session_date.isoformat(),
        end=(session_date + timedelta(days=1)).isoformat(),
        actions=False,
        threads=False,
        ignore_tz=True,
        group_by="column",
        auto_adjust=False,
        back_adjust=False,
        repair=False,
        keepna=False,
        progress=False,
        period=None,
        interval="1d",
        prepost=False,
        rounding=False,
        timeout=_REQUEST_TIMEOUT_SECONDS,
        session=None,
        multi_level_index=False,
    )


def _normalized_mark(value: object) -> str:
    if type(value) in {bool, str, bytes, bytearray, type(None)}:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_CLOSE_INVALID",
        )
    try:
        pandas_module, _yfinance_module = _provider_modules()
        if bool(pandas_module.isna(value)):
            raise _CaptureFailure(
                YfinanceValuationCaptureStatus.INVALID,
                "YFINANCE_CAPTURE_CLOSE_INVALID",
            )
        decimal_value = Decimal(str(value))
        normalized = normalize_decimal_string(decimal_value)
    except (InvalidOperation, MmiCanonicalizationError, TypeError, ValueError):
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_CLOSE_INVALID",
        ) from None
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_CLOSE_INVALID",
        )
    return normalized


def _history_mark_for_session(
    *,
    ticker: str,
    history: object,
    session_date: date,
) -> dict[str, str]:
    pandas_module, _yfinance_module = _provider_modules()
    if history is None:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.UNAVAILABLE,
            "YFINANCE_CAPTURE_HISTORY_UNAVAILABLE",
        )
    if type(history) is not pandas_module.DataFrame:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_RESPONSE_SHAPE_INVALID",
        )
    if history.empty:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.UNAVAILABLE,
            "YFINANCE_CAPTURE_HISTORY_UNAVAILABLE",
        )
    if isinstance(history.columns, pandas_module.MultiIndex):
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_RESPONSE_SHAPE_INVALID",
        )
    if not history.columns.is_unique or set(history.columns) != _EXPECTED_COLUMNS:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_RESPONSE_COLUMNS_INVALID",
        )
    if (
        type(history.index) is not pandas_module.DatetimeIndex
        or history.index.tz is not None
    ):
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_RESPONSE_INDEX_INVALID",
        )
    if not history.index.is_unique or not history.index.is_monotonic_increasing:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_RESPONSE_INDEX_INVALID",
        )
    session_rows = 0
    observed_session_date: date | None = None
    for timestamp in history.index:
        if pandas_module.isna(timestamp):
            raise _CaptureFailure(
                YfinanceValuationCaptureStatus.INVALID,
                "YFINANCE_CAPTURE_RESPONSE_INDEX_INVALID",
            )
        row_date = timestamp.date()
        if row_date > session_date:
            raise _CaptureFailure(
                YfinanceValuationCaptureStatus.INVALID,
                "YFINANCE_CAPTURE_RESPONSE_DATE_INVALID",
            )
        if row_date == session_date:
            session_rows += 1
            observed_session_date = row_date
    if session_rows == 0:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.UNAVAILABLE,
            "YFINANCE_CAPTURE_EXACT_SESSION_ROW_UNAVAILABLE",
        )
    if session_rows != 1 or len(history.index) != 1:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_RESPONSE_DATE_INVALID",
        )
    if observed_session_date is None:  # pragma: no cover - guarded above.
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_RESPONSE_DATE_INVALID",
        )
    return {
        "ticker": ticker,
        "actual_data_date": observed_session_date.isoformat(),
        "provider_field": _PROVIDER_FIELD,
        "mark": _normalized_mark(history["Close"].iloc[0]),
    }


def _captured_at_utc() -> str:
    """Retrieval metadata only; it never participates in session selection."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _capture_payload(
    *,
    holdings: StrictHoldingsDomain,
    completed_session: CompletedUsEquitySession,
    yfinance_version: str,
    pandas_version: str,
    marks: tuple[dict[str, str], ...],
) -> dict[str, object]:
    tickers = tuple(sorted(holdings.tickers))
    if not tickers:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_HOLDINGS_DOMAIN_EMPTY",
        )
    if len(tickers) != len(set(tickers)):
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_HOLDINGS_DOMAIN_DUPLICATE",
        )
    ordered_marks = tuple(sorted(marks, key=lambda mark: mark["ticker"]))
    if tuple(mark["ticker"] for mark in ordered_marks) != tickers:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_MARK_DOMAIN_MISMATCH",
        )
    return {
        "schema_version": _SCHEMA_VERSION,
        "authority_effect": _AUTHORITY_EFFECT_NONE,
        "source_kind": _SOURCE_KIND,
        "provider_id": _PROVIDER_ID,
        "client_library": _CLIENT_LIBRARY,
        "client_version": yfinance_version,
        "pandas_version": pandas_version,
        "acquisition_contract_version": _ACQUISITION_CONTRACT_VERSION,
        "captured_at_utc": _captured_at_utc(),
        "portfolio_source_sha256": holdings.portfolio_source_sha256,
        "portfolio_scope_id": holdings.portfolio_scope_id,
        "requested_ticker_domain": list(tickers),
        "completed_session": {
            "calendar_id": completed_session.calendar_id,
            "calendar_schedule_sha256": completed_session.calendar_schedule_sha256,
            "trusted_evaluation_timestamp_utc": (
                completed_session.trusted_evaluation_timestamp_utc
            ),
            "session_date": completed_session.session_date,
            "official_close_timestamp_et": (
                completed_session.official_close_timestamp_et
            ),
        },
        "marks": list(ordered_marks),
    }


def _validate_capture_payload(payload: object) -> None:
    try:
        validate_artifact_schema(
            payload,
            schema_name="us_equity_yfinance_valuation_capture_v1.schema.json",
        )
    except ArtifactSchemaError as exc:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
        ) from exc
    if type(payload) is not dict:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
        )
    completed_session = payload["completed_session"]
    requested = payload["requested_ticker_domain"]
    marks = payload["marks"]
    if type(completed_session) is not dict or type(requested) is not list or type(marks) is not list:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
        )
    if requested != sorted(requested):
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
        )
    session_date = completed_session["session_date"]
    if type(session_date) is not str:
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
        )
    mark_tickers: list[str] = []
    for mark in marks:
        if type(mark) is not dict:
            raise _CaptureFailure(
                YfinanceValuationCaptureStatus.INVALID,
                "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
            )
        ticker = mark.get("ticker")
        actual_data_date = mark.get("actual_data_date")
        mark_text = mark.get("mark")
        if (
            type(ticker) is not str
            or actual_data_date != session_date
            or type(mark_text) is not str
        ):
            raise _CaptureFailure(
                YfinanceValuationCaptureStatus.INVALID,
                "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
            )
        try:
            decimal_value = Decimal(mark_text)
            if normalize_decimal_string(decimal_value) != mark_text:
                raise ValueError
        except (InvalidOperation, MmiCanonicalizationError, ValueError):
            raise _CaptureFailure(
                YfinanceValuationCaptureStatus.INVALID,
                "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
            ) from None
        if not decimal_value.is_finite() or decimal_value <= 0:
            raise _CaptureFailure(
                YfinanceValuationCaptureStatus.INVALID,
                "YFINANCE_CAPTURE_NORMALIZED_SCHEMA_INVALID",
            )
        mark_tickers.append(ticker)
    if mark_tickers != requested or len(mark_tickers) != len(set(mark_tickers)):
        raise _CaptureFailure(
            YfinanceValuationCaptureStatus.INVALID,
            "YFINANCE_CAPTURE_MARK_DOMAIN_MISMATCH",
        )


def capture_current_us_equity_yfinance_valuation(
    *,
    portfolio_snapshot_expected_sha256: str,
) -> YfinanceValuationCaptureResult:
    """Capture exactly one complete raw-close dataset for the trusted session.

    The fixed current artifact is replaced only after every holdings ticker,
    every normalized mark, and the closed artifact contract have passed.
    """
    try:
        holdings = capture_current_validated_strict_holdings_domain(
            portfolio_snapshot_expected_sha256=(
                portfolio_snapshot_expected_sha256
            ),
        )
    except StrictHoldingsDomainError as exc:
        return _result(
            (
                YfinanceValuationCaptureStatus.UNAVAILABLE
                if exc.unavailable
                else YfinanceValuationCaptureStatus.INVALID
            ),
            exc.reason_codes,
        )
    try:
        completed_session = resolve_trusted_completed_us_equity_session(
            run_context=begin_mmi_projection_run(),
        )
    except UsEquitySessionResolutionError as exc:
        return _result(
            (
                YfinanceValuationCaptureStatus.UNAVAILABLE
                if exc.status is MarkFreshnessStatus.UNAVAILABLE
                else YfinanceValuationCaptureStatus.INVALID
            ),
            exc.reason_codes,
        )
    try:
        yfinance_version, pandas_version = _runtime_versions()
        session_date = date.fromisoformat(completed_session.session_date)
        tickers = tuple(sorted(holdings.tickers))
        if not tickers:
            raise _CaptureFailure(
                YfinanceValuationCaptureStatus.INVALID,
                "YFINANCE_CAPTURE_HOLDINGS_DOMAIN_EMPTY",
            )
        if len(tickers) != len(set(tickers)):
            raise _CaptureFailure(
                YfinanceValuationCaptureStatus.INVALID,
                "YFINANCE_CAPTURE_HOLDINGS_DOMAIN_DUPLICATE",
            )
        marks: list[dict[str, str]] = []
        for ticker in tickers:
            try:
                history = _download_history_for_session(
                    ticker=ticker,
                    session_date=session_date,
                )
            except Exception:
                return _result(
                    YfinanceValuationCaptureStatus.UNAVAILABLE,
                    ("YFINANCE_CAPTURE_PROVIDER_REQUEST_UNAVAILABLE",),
                )
            marks.append(
                _history_mark_for_session(
                    ticker=ticker,
                    history=history,
                    session_date=session_date,
                )
            )
        payload = _capture_payload(
            holdings=holdings,
            completed_session=completed_session,
            yfinance_version=yfinance_version,
            pandas_version=pandas_version,
            marks=tuple(marks),
        )
        _validate_capture_payload(payload)
        artifact_bytes = canonical_json_bytes(
            payload,
            maximum_bytes=_CAPTURE_MAXIMUM_CANONICAL_BYTES,
        )
    except _CaptureFailure as exc:
        return _result(exc.status, (exc.code,))
    except (MmiCanonicalizationError, ValueError):
        return _result(
            YfinanceValuationCaptureStatus.INVALID,
            ("YFINANCE_CAPTURE_INTERNAL_NORMALIZATION_INVALID",),
        )

    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    atomic_write_text(_capture_path(), artifact_bytes.decode("utf-8"))
    return _result(
        YfinanceValuationCaptureStatus.CAPTURED,
        ("YFINANCE_CAPTURE_COMPLETE",),
        artifact_sha256=artifact_sha256,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture complete report-only US-equity raw Close marks from yfinance."
        )
    )
    parser.add_argument(
        "--portfolio-snapshot-expected-sha256",
        required=True,
        help=(
            "Exact SHA-256 of the fixed current portfolio snapshot to capture."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = capture_current_us_equity_yfinance_valuation(
        portfolio_snapshot_expected_sha256=args.portfolio_snapshot_expected_sha256,
    )
    print(
        json.dumps(
            {
                "status": result.status.value,
                "reason_codes": list(result.reason_codes),
                "artifact_repository_relative_path": (
                    result.artifact_repository_relative_path
                ),
                "artifact_sha256": result.artifact_sha256,
                "authority_effect": result.authority_effect,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if result.status is YfinanceValuationCaptureStatus.CAPTURED else 1


if __name__ == "__main__":
    raise SystemExit(main())
