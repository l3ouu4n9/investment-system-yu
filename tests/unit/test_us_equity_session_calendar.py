"""Focused contracts for the one offline US-equity session freshness owner."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi.contracts import (
    MmiProjectionRunContext,
    _begin_mmi_projection_run_with_clock,
)
from investment_orchestrator.market import us_equity_session_calendar as calendar


_APPROVED_SHA256 = (
    "a7142dcf13f52f30f07cc48942abe1e325ace21d644a2198c5e5667cf9d20007"
)


class _FixedClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now_utc(self) -> datetime:
        return self._value


def _context(value: datetime) -> MmiProjectionRunContext:
    return _begin_mmi_projection_run_with_clock(_FixedClock(value))


def _assess(value: datetime, mark_as_of_date: date):
    return calendar.assess_manual_mark_freshness(
        mark_as_of_date=mark_as_of_date,
        run_context=_context(value),
    )


def _write_schedule_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_bytes: bytes,
    *,
    accept_exact_bytes: bool,
) -> None:
    directory = tmp_path / "reference_data"
    directory.mkdir()
    (directory / "us_equity_regular_sessions_v1.json").write_bytes(raw_bytes)
    monkeypatch.setattr(calendar, "repo_root", lambda: tmp_path)
    if accept_exact_bytes:
        monkeypatch.setattr(
            calendar,
            "_APPROVED_SCHEDULE_SHA256",
            hashlib.sha256(raw_bytes).hexdigest(),
        )


def _schedule_payload() -> dict[str, object]:
    return json.loads(
        (repo_root() / "reference_data/us_equity_regular_sessions_v1.json").read_text(
            encoding="utf-8"
        )
    )


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_public_freshness_entry_has_no_bare_evaluation_or_session_bypass() -> None:
    assert tuple(calendar.__all__) == (
        "CompletedUsEquitySession",
        "MarkFreshnessResult",
        "MarkFreshnessStatus",
        "UsEquitySessionResolutionError",
        "assess_manual_mark_freshness",
        "resolve_trusted_completed_us_equity_session",
    )
    parameters = inspect.signature(calendar.assess_manual_mark_freshness).parameters
    assert set(parameters) == {"mark_as_of_date", "run_context"}
    assert not {"evaluation_date", "evaluation_timestamp", "latest_completed_session_date", "calendar_path"} & set(parameters)
    forged = object.__new__(MmiProjectionRunContext)
    result = calendar.assess_manual_mark_freshness(
        mark_as_of_date=date(2026, 8, 12),
        run_context=forged,
    )
    assert result.status is calendar.MarkFreshnessStatus.INVALID
    assert result.reason_codes == ("US_EQUITY_SESSION_RUN_CONTEXT_OR_MARK_DATE_INVALID",)
    assert result.mark_as_of_date == date(2026, 8, 12)
    assert "mark_as_of_date" in calendar.MarkFreshnessResult.__dataclass_fields__
    assert "mark_as_of_date" not in calendar.CompletedUsEquitySession.__dataclass_fields__


def test_completed_session_factory_has_no_caller_date_or_calendar_bypass() -> None:
    parameters = inspect.signature(
        calendar.resolve_trusted_completed_us_equity_session
    ).parameters
    assert set(parameters) == {"run_context"}
    assert not {
        "evaluation_date",
        "evaluation_timestamp",
        "session_date",
        "calendar_path",
        "calendar_id",
        "calendar_schedule_sha256",
    } & set(parameters)
    completed = calendar.resolve_trusted_completed_us_equity_session(
        run_context=_context(datetime(2026, 8, 12, 20, tzinfo=timezone.utc)),
    )
    assert completed.session_date == "2026-08-12"
    forged = object.__new__(MmiProjectionRunContext)
    with pytest.raises(calendar.UsEquitySessionResolutionError) as exc_info:
        calendar.resolve_trusted_completed_us_equity_session(run_context=forged)
    assert exc_info.value.status is calendar.MarkFreshnessStatus.INVALID
    assert exc_info.value.reason_codes == (
        "US_EQUITY_SESSION_RUN_CONTEXT_INVALID",
    )


def test_exact_approved_schedule_loads_without_a_production_session_count_oracle() -> None:
    module_text = Path(calendar.__file__).read_text(encoding="utf-8")
    assert "_REQUIRED_SESSION_COUNT" not in module_text
    assert "251" not in module_text

    raw_bytes = (
        repo_root() / "reference_data/us_equity_regular_sessions_v1.json"
    ).read_bytes()
    assert hashlib.sha256(raw_bytes).hexdigest() == _APPROVED_SHA256
    payload = json.loads(raw_bytes)
    assert isinstance(payload["sessions"], list)
    assert len(payload["sessions"]) == 251

    result = _assess(
        datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
        date(2026, 8, 12),
    )
    assert result.status is calendar.MarkFreshnessStatus.FRESH
    assert result.mark_as_of_date == date(2026, 8, 12)


@pytest.mark.parametrize(
    ("evaluation", "expected_session"),
    (
        (datetime(2026, 8, 12, 19, 59, tzinfo=timezone.utc), "2026-08-11"),
        (datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc), "2026-08-12"),
        (datetime(2026, 8, 12, 20, 1, tzinfo=timezone.utc), "2026-08-12"),
    ),
)
def test_regular_close_boundary_uses_trusted_session_close(
    evaluation: datetime,
    expected_session: str,
) -> None:
    result = _assess(evaluation, date.fromisoformat(expected_session))
    assert result.status is calendar.MarkFreshnessStatus.FRESH
    assert result.completed_session is not None
    assert result.completed_session.session_date == expected_session
    assert result.completed_session.official_close_timestamp_et.endswith(
        "16:00:00-04:00"
    )
    assert result.completed_session.calendar_schedule_sha256 == _APPROVED_SHA256


@pytest.mark.parametrize(
    ("evaluation", "expected_session"),
    (
        (datetime(2026, 11, 27, 17, 59, tzinfo=timezone.utc), "2026-11-25"),
        (datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc), "2026-11-27"),
        (datetime(2026, 11, 27, 18, 1, tzinfo=timezone.utc), "2026-11-27"),
        (datetime(2026, 12, 24, 17, 59, tzinfo=timezone.utc), "2026-12-23"),
        (datetime(2026, 12, 24, 18, 0, tzinfo=timezone.utc), "2026-12-24"),
    ),
)
def test_explicit_early_close_boundary_uses_schedule_not_weekday_rules(
    evaluation: datetime,
    expected_session: str,
) -> None:
    result = _assess(evaluation, date.fromisoformat(expected_session))
    assert result.status is calendar.MarkFreshnessStatus.FRESH
    assert result.completed_session is not None
    assert result.completed_session.session_date == expected_session
    if expected_session in {"2026-11-27", "2026-12-24"}:
        assert result.completed_session.official_close_timestamp_et.endswith(
            "13:00:00-05:00"
        )


@pytest.mark.parametrize(
    ("evaluation", "expected_session"),
    (
        (datetime(2026, 8, 15, 16, tzinfo=timezone.utc), "2026-08-14"),
        (datetime(2026, 11, 26, 18, tzinfo=timezone.utc), "2026-11-25"),
    ),
)
def test_weekend_and_scheduled_holiday_use_prior_actual_session(
    evaluation: datetime,
    expected_session: str,
) -> None:
    result = _assess(evaluation, date.fromisoformat(expected_session))
    assert result.status is calendar.MarkFreshnessStatus.FRESH
    assert result.completed_session is not None
    assert result.completed_session.session_date == expected_session


def test_mark_freshness_classification_never_clamps_dates() -> None:
    fresh = _assess(datetime(2026, 8, 12, 20, tzinfo=timezone.utc), date(2026, 8, 12))
    assert fresh.status is calendar.MarkFreshnessStatus.FRESH
    assert fresh.mark_as_of_date == date(2026, 8, 12)
    stale = _assess(datetime(2026, 8, 12, 20, tzinfo=timezone.utc), date(2026, 8, 11))
    assert stale.status is calendar.MarkFreshnessStatus.UNAVAILABLE
    assert stale.reason_codes == ("US_EQUITY_SESSION_MARK_DATE_STALE",)
    assert stale.mark_as_of_date == date(2026, 8, 11)
    future = _assess(datetime(2026, 8, 12, 20, tzinfo=timezone.utc), date(2026, 8, 13))
    assert future.status is calendar.MarkFreshnessStatus.INVALID
    assert future.reason_codes == ("US_EQUITY_SESSION_MARK_DATE_AFTER_EVALUATION",)
    assert future.mark_as_of_date == date(2026, 8, 13)
    uncompleted = _assess(datetime(2026, 8, 12, 19, 59, tzinfo=timezone.utc), date(2026, 8, 12))
    assert uncompleted.status is calendar.MarkFreshnessStatus.INVALID
    assert uncompleted.reason_codes == ("US_EQUITY_SESSION_MARK_DATE_UNCOMPLETED",)
    assert uncompleted.mark_as_of_date == date(2026, 8, 12)
    weekend = _assess(datetime(2026, 8, 10, 20, tzinfo=timezone.utc), date(2026, 8, 8))
    assert weekend.status is calendar.MarkFreshnessStatus.INVALID
    assert weekend.reason_codes == ("US_EQUITY_SESSION_MARK_DATE_NON_SESSION",)
    assert weekend.mark_as_of_date == date(2026, 8, 8)
    holiday = _assess(datetime(2026, 7, 6, 20, tzinfo=timezone.utc), date(2026, 7, 3))
    assert holiday.status is calendar.MarkFreshnessStatus.INVALID
    assert holiday.reason_codes == ("US_EQUITY_SESSION_MARK_DATE_NON_SESSION",)
    assert holiday.mark_as_of_date == date(2026, 7, 3)


def test_calendar_coverage_is_bounded_and_never_extrapolates() -> None:
    future = _assess(datetime(2027, 1, 4, 20, tzinfo=timezone.utc), date(2027, 1, 4))
    assert future.status is calendar.MarkFreshnessStatus.UNAVAILABLE
    assert future.reason_codes == ("US_EQUITY_SESSION_CALENDAR_COVERAGE_INSUFFICIENT",)
    before_first = _assess(datetime(2026, 1, 1, 20, tzinfo=timezone.utc), date(2026, 1, 1))
    assert before_first.status is calendar.MarkFreshnessStatus.UNAVAILABLE
    assert before_first.reason_codes == ("US_EQUITY_SESSION_CALENDAR_COVERAGE_INSUFFICIENT",)


def test_missing_calendar_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calendar, "repo_root", lambda: tmp_path)
    result = _assess(datetime(2026, 8, 12, 20, tzinfo=timezone.utc), date(2026, 8, 12))
    assert result.status is calendar.MarkFreshnessStatus.UNAVAILABLE
    assert result.reason_codes == ("US_EQUITY_SESSION_CALENDAR_SOURCE_UNAVAILABLE",)


def test_schedule_sha_mismatch_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_schedule_root(tmp_path, monkeypatch, b"{}", accept_exact_bytes=False)
    result = _assess(datetime(2026, 8, 12, 20, tzinfo=timezone.utc), date(2026, 8, 12))
    assert result.status is calendar.MarkFreshnessStatus.INVALID
    assert result.reason_codes == ("US_EQUITY_SESSION_CALENDAR_SHA256_MISMATCH",)


@pytest.mark.parametrize(
    "kind",
    (
        "malformed",
        "schema",
        "calendar",
        "timezone",
        "duplicate",
        "unordered",
        "outside_coverage",
        "weekend",
        "close_time",
    ),
)
def test_malformed_or_semantically_invalid_schedules_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    if kind == "malformed":
        raw_bytes = b"{"
    else:
        payload = _schedule_payload()
        if kind == "schema":
            payload["schema_version"] = "unexpected"
        elif kind == "calendar":
            payload["calendar_id"] = "OTHER"
        elif kind == "timezone":
            payload["timezone"] = "UTC"
        elif kind == "duplicate":
            sessions = payload["sessions"]
            assert isinstance(sessions, list)
            sessions[1] = dict(sessions[0])
        elif kind == "unordered":
            sessions = payload["sessions"]
            assert isinstance(sessions, list)
            sessions[0], sessions[1] = sessions[1], sessions[0]
        elif kind == "weekend":
            sessions = payload["sessions"]
            assert isinstance(sessions, list)
            sessions[0]["session_date"] = "2026-01-03"
        elif kind == "close_time":
            sessions = payload["sessions"]
            assert isinstance(sessions, list)
            sessions[0]["official_close_time_et"] = "14:00"
        else:
            sessions = payload["sessions"]
            assert isinstance(sessions, list)
            sessions[0]["session_date"] = "2025-12-31"
        raw_bytes = _canonical_json(payload)
    _write_schedule_root(tmp_path, monkeypatch, raw_bytes, accept_exact_bytes=True)
    result = _assess(datetime(2026, 8, 12, 20, tzinfo=timezone.utc), date(2026, 8, 12))
    assert result.status is calendar.MarkFreshnessStatus.INVALID
    assert result.completed_session is None
