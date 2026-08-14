"""Offline freshness owner for the reviewed US-equity regular-session schedule.

This module has one intentionally narrow job: using a validated UTC
evaluation instant and the committed schedule, determine whether one manually
supplied closing-mark date is the latest completed regular US-equity session.
It owns no prices, permissions, portfolio decisions, or publication.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.stable_read import (
    MmiStableReadError,
    MmiStableReadErrorCode,
    stable_read_exact_bytes,
)


_AUTHORITY_EFFECT_NONE: Final = "NONE"
_CANONICAL_UTC_TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"


__all__ = (
    "CompletedUsEquitySession",
    "MarkFreshnessResult",
    "MarkFreshnessStatus",
    "UsEquitySessionResolutionError",
    "assess_manual_mark_freshness",
    "resolve_trusted_completed_us_equity_session",
)


_CALENDAR_DIRECTORY_COMPONENT: Final = "reference_data"
_CALENDAR_FILENAME: Final = "us_equity_regular_sessions_v1.json"
_CALENDAR_MAXIMUM_BYTES: Final = 262_144
_APPROVED_SCHEDULE_SHA256: Final = (
    "a7142dcf13f52f30f07cc48942abe1e325ace21d644a2198c5e5667cf9d20007"
)
_SCHEMA_VERSION: Final = "US_EQUITY_REGULAR_SESSIONS_V1"
_CALENDAR_ID: Final = "US_EQUITY_REGULAR"
_TIMEZONE_NAME: Final = "America/New_York"
_COVERAGE_START_DATE: Final = date(2026, 1, 1)
_COVERAGE_END_DATE: Final = date(2026, 12, 31)
_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE: Final = re.compile(r"^(?:13:00|16:00)$")


class MarkFreshnessStatus(str, Enum):
    """Closed status vocabulary for the one calendar-owned freshness check."""

    FRESH = "FRESH"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


class UsEquitySessionResolutionError(ValueError):
    """Closed failure result for the narrow completed-session fact factory."""

    def __init__(
        self,
        status: MarkFreshnessStatus,
        reason_codes: tuple[str, ...],
    ) -> None:
        super().__init__(reason_codes[0] if reason_codes else status.value)
        self.status = status
        self.reason_codes = reason_codes


@dataclass(frozen=True, slots=True)
class CompletedUsEquitySession:
    """The one schedule-bound session fact required by mark freshness."""

    authority_effect: str
    calendar_id: str
    calendar_schedule_sha256: str
    coverage_start_date: str
    coverage_end_date: str
    trusted_evaluation_timestamp_utc: str
    session_date: str
    official_close_timestamp_et: str


@dataclass(frozen=True, slots=True)
class MarkFreshnessResult:
    """Non-authorizing result of comparing a mark date to one session fact."""

    authority_effect: str
    status: MarkFreshnessStatus
    reason_codes: tuple[str, ...]
    mark_as_of_date: date | None
    completed_session: CompletedUsEquitySession | None


@dataclass(frozen=True, slots=True)
class _Session:
    session_date: date
    close_time_et: time


@dataclass(frozen=True, slots=True)
class _ValidatedSchedule:
    observed_sha256: str
    sessions: tuple[_Session, ...]


class _CalendarInputError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _result(
    status: MarkFreshnessStatus,
    reason_codes: tuple[str, ...],
    *,
    mark_as_of_date: date | None,
    completed_session: CompletedUsEquitySession | None = None,
) -> MarkFreshnessResult:
    return MarkFreshnessResult(
        authority_effect=_AUTHORITY_EFFECT_NONE,
        status=status,
        reason_codes=reason_codes,
        mark_as_of_date=mark_as_of_date,
        completed_session=completed_session,
    )


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_JSON_DUPLICATE_KEY")
        output[key] = value
    return output


def _parse_date(value: object, *, code: str) -> date:
    if type(value) is not str or not _DATE_RE.fullmatch(value):
        raise _CalendarInputError(code)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _CalendarInputError(code) from None


def _parse_close_time(value: object) -> time:
    if type(value) is not str or not _TIME_RE.fullmatch(value):
        raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_CLOSE_TIME_INVALID")
    return time.fromisoformat(value)


def _decode_schedule_utf8(raw_bytes: bytes) -> str:
    if type(raw_bytes) is not bytes or raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_SOURCE_INVALID")
    try:
        value = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_SOURCE_INVALID") from None
    if "\x00" in value:
        raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_SOURCE_INVALID")
    return value


def _parse_and_validate_schedule(raw_bytes: bytes) -> _ValidatedSchedule:
    observed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if observed_sha256 != _APPROVED_SCHEDULE_SHA256:
        raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_SHA256_MISMATCH")
    try:
        payload = json.loads(
            _decode_schedule_utf8(raw_bytes),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (_CalendarInputError, ValueError):
        raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_JSON_INVALID") from None
    if type(payload) is not dict or set(payload) != {
        "schema_version",
        "calendar_id",
        "timezone",
        "coverage_start_date",
        "coverage_end_date",
        "official_source_references",
        "sessions",
    }:
        raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_SCHEMA_INVALID")
    if (
        payload.get("schema_version") != _SCHEMA_VERSION
        or payload.get("calendar_id") != _CALENDAR_ID
        or payload.get("timezone") != _TIMEZONE_NAME
    ):
        raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_SCHEMA_INVALID")
    coverage_start = _parse_date(
        payload.get("coverage_start_date"),
        code="US_EQUITY_SESSION_CALENDAR_COVERAGE_INVALID",
    )
    coverage_end = _parse_date(
        payload.get("coverage_end_date"),
        code="US_EQUITY_SESSION_CALENDAR_COVERAGE_INVALID",
    )
    if (
        coverage_start != _COVERAGE_START_DATE
        or coverage_end != _COVERAGE_END_DATE
        or coverage_start > coverage_end
    ):
        raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_COVERAGE_INVALID")
    references = payload.get("official_source_references")
    if (
        type(references) is not list
        or not references
        or any(type(reference) is not str or not reference for reference in references)
    ):
        raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_SCHEMA_INVALID")
    raw_sessions = payload.get("sessions")
    if type(raw_sessions) is not list or not raw_sessions:
        raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_SESSIONS_INVALID")
    sessions: list[_Session] = []
    prior_date: date | None = None
    for raw_session in raw_sessions:
        if type(raw_session) is not dict or set(raw_session) != {
            "session_date",
            "official_close_time_et",
        }:
            raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_SESSION_ROW_INVALID")
        session_date = _parse_date(
            raw_session.get("session_date"),
            code="US_EQUITY_SESSION_CALENDAR_SESSION_DATE_INVALID",
        )
        close_time_et = _parse_close_time(
            raw_session.get("official_close_time_et")
        )
        if (
            session_date < coverage_start
            or session_date > coverage_end
            or session_date.weekday() > 4
        ):
            raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_SESSION_INVALID")
        if prior_date is not None and session_date <= prior_date:
            raise _CalendarInputError("US_EQUITY_SESSION_CALENDAR_SESSION_ORDER_INVALID")
        sessions.append(_Session(session_date=session_date, close_time_et=close_time_et))
        prior_date = session_date
    return _ValidatedSchedule(
        observed_sha256=observed_sha256,
        sessions=tuple(sessions),
    )


def _open_reference_data_directory(root: Path) -> tuple[int, int]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    root_fd: int | None = None
    reference_data_fd: int | None = None
    try:
        root_fd = os.open(os.fspath(root), flags)
        reference_data_fd = os.open(
            _CALENDAR_DIRECTORY_COMPONENT,
            flags,
            dir_fd=root_fd,
        )
        if not all(
            stat.S_ISDIR(os.fstat(fd).st_mode)
            for fd in (root_fd, reference_data_fd)
        ):
            raise OSError("reference data hierarchy is not a directory")
        return root_fd, reference_data_fd
    except OSError:
        for fd in (reference_data_fd, root_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        raise


def _load_approved_schedule() -> tuple[_ValidatedSchedule | None, tuple[str, ...], bool]:
    try:
        root_fd, reference_data_fd = _open_reference_data_directory(repo_root())
    except OSError:
        return None, ("US_EQUITY_SESSION_CALENDAR_SOURCE_UNAVAILABLE",), False
    try:
        try:
            raw_bytes = stable_read_exact_bytes(
                reference_data_fd,
                _CALENDAR_FILENAME,
                maximum_bytes=_CALENDAR_MAXIMUM_BYTES,
            )
        except MmiStableReadError as exc:
            if exc.code is MmiStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE:
                return None, ("US_EQUITY_SESSION_CALENDAR_SOURCE_UNAVAILABLE",), False
            try:
                os.stat(
                    _CALENDAR_FILENAME,
                    dir_fd=reference_data_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return None, ("US_EQUITY_SESSION_CALENDAR_SOURCE_ABSENT",), False
            except OSError:
                return None, ("US_EQUITY_SESSION_CALENDAR_SOURCE_UNAVAILABLE",), False
            return None, ("US_EQUITY_SESSION_CALENDAR_SOURCE_INVALID",), True
        except OSError:
            return None, ("US_EQUITY_SESSION_CALENDAR_SOURCE_UNAVAILABLE",), False
    finally:
        os.close(reference_data_fd)
        os.close(root_fd)
    try:
        return _parse_and_validate_schedule(raw_bytes), (), False
    except _CalendarInputError as exc:
        return None, (exc.code,), True


def _resolve_latest_completed_session(
    *,
    schedule: _ValidatedSchedule,
    evaluation_time_utc: datetime,
) -> CompletedUsEquitySession | None:
    try:
        evaluation_et = evaluation_time_utc.astimezone(
            ZoneInfo(_TIMEZONE_NAME)
        )
    except (ValueError, ZoneInfoNotFoundError):
        return None
    if (
        evaluation_et.date() < _COVERAGE_START_DATE
        or evaluation_et.date() > _COVERAGE_END_DATE
    ):
        return None
    latest: _Session | None = None
    latest_close: datetime | None = None
    for session in schedule.sessions:
        close_timestamp = datetime.combine(
            session.session_date,
            session.close_time_et,
            tzinfo=evaluation_et.tzinfo,
        )
        if close_timestamp <= evaluation_et:
            latest = session
            latest_close = close_timestamp
        else:
            break
    if latest is None or latest_close is None:
        return None
    return CompletedUsEquitySession(
        authority_effect=_AUTHORITY_EFFECT_NONE,
        calendar_id=_CALENDAR_ID,
        calendar_schedule_sha256=schedule.observed_sha256,
        coverage_start_date=_COVERAGE_START_DATE.isoformat(),
        coverage_end_date=_COVERAGE_END_DATE.isoformat(),
        trusted_evaluation_timestamp_utc=evaluation_time_utc.strftime(
            _CANONICAL_UTC_TIMESTAMP_FORMAT
        ),
        session_date=latest.session_date.isoformat(),
        official_close_timestamp_et=latest_close.isoformat(timespec="seconds"),
    )


def _evaluation_time_utc_is_valid(value: object) -> bool:
    """Fail-closed acceptance test for a caller-supplied UTC evaluation instant.

    Must run before any ``astimezone``/``strftime`` use: a naive datetime's
    ``astimezone`` silently assumes the system-local timezone rather than
    raising, so naivety has to be rejected here first.
    """
    if type(value) is not datetime:
        return False
    try:
        offset = value.utcoffset()
    except Exception:
        return False
    return offset == timedelta(0)


def _resolve_trusted_completed_us_equity_session(
    *,
    evaluation_time_utc: datetime,
) -> tuple[CompletedUsEquitySession, _ValidatedSchedule]:
    if not _evaluation_time_utc_is_valid(evaluation_time_utc):
        raise UsEquitySessionResolutionError(
            MarkFreshnessStatus.INVALID,
            ("US_EQUITY_SESSION_RUN_CONTEXT_INVALID",),
        )
    schedule, schedule_reasons, schedule_invalid = _load_approved_schedule()
    if schedule_invalid:
        raise UsEquitySessionResolutionError(
            MarkFreshnessStatus.INVALID,
            schedule_reasons,
        )
    if schedule is None:
        raise UsEquitySessionResolutionError(
            MarkFreshnessStatus.UNAVAILABLE,
            schedule_reasons,
        )
    completed_session = _resolve_latest_completed_session(
        schedule=schedule,
        evaluation_time_utc=evaluation_time_utc,
    )
    if completed_session is None:
        raise UsEquitySessionResolutionError(
            MarkFreshnessStatus.UNAVAILABLE,
            ("US_EQUITY_SESSION_CALENDAR_COVERAGE_INSUFFICIENT",),
        )
    return completed_session, schedule


def resolve_trusted_completed_us_equity_session(
    *,
    evaluation_time_utc: datetime,
) -> CompletedUsEquitySession:
    """Return the one reviewed-calendar session fact for a trusted UTC instant.

    This intentionally accepts no caller date, session, calendar path, or
    calendar identity.  It is the reusable acquisition seam for code that
    needs the completed session before it requests external data; the calendar
    owner remains the only owner of session resolution and coverage semantics.
    """
    completed_session, _schedule = _resolve_trusted_completed_us_equity_session(
        evaluation_time_utc=evaluation_time_utc,
    )
    return completed_session


def assess_manual_mark_freshness(
    *,
    mark_as_of_date: date,
    evaluation_time_utc: datetime,
) -> MarkFreshnessResult:
    """Compare one parsed mark date to the trusted latest completed session.

    ``mark_as_of_date`` is the factual date parsed from the manual valuation
    source.  Callers supply the trusted evaluation instant directly; they
    cannot inject a completed-session date, calendar path, or calendar
    identity.
    """
    assessed_mark_as_of_date = (
        mark_as_of_date if type(mark_as_of_date) is date else None
    )
    if assessed_mark_as_of_date is None:
        return _result(
            MarkFreshnessStatus.INVALID,
            ("US_EQUITY_SESSION_RUN_CONTEXT_OR_MARK_DATE_INVALID",),
            mark_as_of_date=assessed_mark_as_of_date,
        )
    try:
        completed_session, schedule = _resolve_trusted_completed_us_equity_session(
            evaluation_time_utc=evaluation_time_utc,
        )
    except UsEquitySessionResolutionError as exc:
        reason_codes = exc.reason_codes
        if reason_codes == ("US_EQUITY_SESSION_RUN_CONTEXT_INVALID",):
            reason_codes = ("US_EQUITY_SESSION_RUN_CONTEXT_OR_MARK_DATE_INVALID",)
        return _result(
            exc.status,
            reason_codes,
            mark_as_of_date=assessed_mark_as_of_date,
        )
    if assessed_mark_as_of_date < _COVERAGE_START_DATE:
        return _result(
            MarkFreshnessStatus.UNAVAILABLE,
            ("US_EQUITY_SESSION_MARK_DATE_OUTSIDE_CALENDAR_COVERAGE",),
            mark_as_of_date=assessed_mark_as_of_date,
            completed_session=completed_session,
        )
    try:
        evaluation_date_et = evaluation_time_utc.astimezone(
            ZoneInfo(_TIMEZONE_NAME)
        ).date()
    except (ValueError, ZoneInfoNotFoundError):
        return _result(
            MarkFreshnessStatus.INVALID,
            ("US_EQUITY_SESSION_EVALUATION_TIMESTAMP_INVALID",),
            mark_as_of_date=assessed_mark_as_of_date,
        )
    if assessed_mark_as_of_date > evaluation_date_et:
        return _result(
            MarkFreshnessStatus.INVALID,
            ("US_EQUITY_SESSION_MARK_DATE_AFTER_EVALUATION",),
            mark_as_of_date=assessed_mark_as_of_date,
            completed_session=completed_session,
        )
    session_dates = {session.session_date for session in schedule.sessions}
    if assessed_mark_as_of_date not in session_dates:
        return _result(
            MarkFreshnessStatus.INVALID,
            ("US_EQUITY_SESSION_MARK_DATE_NON_SESSION",),
            mark_as_of_date=assessed_mark_as_of_date,
            completed_session=completed_session,
        )
    latest_session_date = date.fromisoformat(completed_session.session_date)
    if assessed_mark_as_of_date < latest_session_date:
        return _result(
            MarkFreshnessStatus.UNAVAILABLE,
            ("US_EQUITY_SESSION_MARK_DATE_STALE",),
            mark_as_of_date=assessed_mark_as_of_date,
            completed_session=completed_session,
        )
    if assessed_mark_as_of_date > latest_session_date:
        return _result(
            MarkFreshnessStatus.INVALID,
            ("US_EQUITY_SESSION_MARK_DATE_UNCOMPLETED",),
            mark_as_of_date=assessed_mark_as_of_date,
            completed_session=completed_session,
        )
    return _result(
        MarkFreshnessStatus.FRESH,
        ("US_EQUITY_SESSION_MARK_DATE_FRESH",),
        mark_as_of_date=assessed_mark_as_of_date,
        completed_session=completed_session,
    )
