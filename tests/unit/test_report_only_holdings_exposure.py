"""Focused contracts for the isolated holdings/valuation observer."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi.contracts import (
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
)
from investment_orchestrator.mmi.source_capture import (
    _capture_mmi_source_at_root,
)
from investment_orchestrator.observability import (
    report_only_holdings_exposure as exposure,
)
from investment_orchestrator.market import us_equity_session_calendar as calendar


class _FixedClock:
    def now_utc(self) -> datetime:
        return datetime(2026, 8, 12, 20, tzinfo=timezone.utc)


def _strict_section(rows: tuple[str, ...] = ("QQQ | 2.5", "SMH | 3")) -> bytes:
    return "\n".join(
        (
            "[STRICT_POSITIVE_ETF_POSITIONS_V1]",
            "schema_version = strict_positive_etf_positions_v1",
            "portfolio_scope_id = etf_strategy_portfolio_v1",
            "operator_scope_complete = true",
            "TICKER | shares",
            *rows,
            "[/STRICT_POSITIVE_ETF_POSITIONS_V1]",
        )
    ).encode("utf-8")


def _portfolio(rows: tuple[str, ...] = ("QQQ | 2.5", "SMH | 3")) -> bytes:
    raw = (repo_root() / "inputs/current/portfolio_snapshot.txt").read_bytes()
    return raw + b"\n" + _strict_section(rows) + b"\n"


def _valuation(
    marks: list[dict[str, str]] | None = None,
    *,
    as_of: str = "2026-08-12",
    currency: str = "USD",
    extra: dict[str, object] | None = None,
) -> bytes:
    payload: dict[str, object] = {
        "schema_version": "manual_valuation_marks_v1",
        "currency": currency,
        "mark_source_id": "operator_reconciled_close",
        "mark_as_of_date": as_of,
        "marks": marks
        if marks is not None
        else [
            {"ticker": "QQQ", "mark": "400.125"},
            {"ticker": "SMH", "mark": "200.5"},
        ],
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


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


def _prepared_current_root(tmp_path: Path, *, portfolio_bytes: bytes, valuation_bytes: bytes | None = None) -> tuple[object, object]:
    current = tmp_path / "inputs/current"
    current.mkdir(parents=True)
    (current / "strategy_settings.yaml").write_bytes(
        (repo_root() / "inputs/current/strategy_settings.yaml").read_bytes()
    )
    (current / "portfolio_snapshot.txt").write_bytes(portfolio_bytes)
    if valuation_bytes is not None:
        (current / "manual_valuation_marks.json").write_bytes(valuation_bytes)
    return (
        _source(tmp_path, MmiSourceRole.STRATEGY_SETTINGS),
        _source(tmp_path, MmiSourceRole.PORTFOLIO_SNAPSHOT),
    )


def _observe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    portfolio_bytes: bytes | None = None,
    valuation_bytes: bytes | None = None,
):
    strategy_source, portfolio_source = _prepared_current_root(
        tmp_path,
        portfolio_bytes=portfolio_bytes if portfolio_bytes is not None else _portfolio(),
        valuation_bytes=valuation_bytes if valuation_bytes is not None else _valuation(),
    )

    def _capture(role: MmiSourceRole, *, expected_source_sha256: str):
        assert expected_source_sha256 == ("a" * 64 if role is MmiSourceRole.STRATEGY_SETTINGS else "b" * 64)
        return type("Result", (), {
            "valid": True,
            "authority_effect": "NONE",
            "source": strategy_source if role is MmiSourceRole.STRATEGY_SETTINGS else portfolio_source,
            "reason_codes": (),
        })()

    monkeypatch.setattr(exposure, "capture_current_mmi_source", _capture)
    monkeypatch.setattr(exposure, "repo_root", lambda: tmp_path)
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


def test_existing_canonical_portfolio_date_is_the_only_projection_date(
    tmp_path: Path,
) -> None:
    strategy_source, portfolio_source = _prepared_current_root(
        tmp_path,
        portfolio_bytes=_portfolio(),
        valuation_bytes=_valuation(),
    )
    run_context = exposure.begin_mmi_projection_run()
    policy = exposure.build_mmi_policy_projection(
        strategy_source,
        run_context=run_context,
    ).projection
    assert isinstance(policy, dict)
    projection_result = exposure.build_mmi_portfolio_snapshot_projection(
        portfolio_source,
        policy_projection=policy,
        policy_source=strategy_source,
        run_context=run_context,
    )
    assert projection_result.valid
    assert isinstance(projection_result.projection, dict)
    assert projection_result.projection["portfolio_source_date"] == "2026-08-12"
    strict_holdings = exposure._parse_strict_holdings(portfolio_source.raw_bytes)
    assert not hasattr(strict_holdings, "holdings_as_of_date")


def test_public_observer_has_no_bare_date_or_session_bypass() -> None:
    assert not hasattr(exposure, "build_report_only_holdings_exposure")
    public_parameters = inspect.signature(
        exposure.observe_current_report_only_holdings_exposure
    ).parameters
    assert set(public_parameters) == {
        "strategy_settings_expected_sha256",
        "portfolio_snapshot_expected_sha256",
    }
    assert "evaluation_date" not in public_parameters
    assert "evaluation_timestamp" not in public_parameters
    assert "completed_trading_session_date" not in public_parameters
    assert tuple(exposure.__all__) == (
        "ExposureObservationResult",
        "ExposureObservationStatus",
        "ExposurePosition",
        "ExposureProjection",
        "observe_current_report_only_holdings_exposure",
    )


def test_complete_fresh_sources_return_valid_report_only_with_bound_calendar_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freshness_results: list[calendar.MarkFreshnessResult] = []
    freshness_owner = exposure.assess_manual_mark_freshness

    def _capture_freshness(**kwargs: object) -> calendar.MarkFreshnessResult:
        result = freshness_owner(**kwargs)
        freshness_results.append(result)
        return result

    monkeypatch.setattr(exposure, "assess_manual_mark_freshness", _capture_freshness)
    result = _observe(
        tmp_path,
        monkeypatch,
        portfolio_bytes=_portfolio(("QQQ | 2.5", "SMH | 3", "VOO | 1")),
        valuation_bytes=_valuation(
            [
                {"ticker": "QQQ", "mark": "400.125"},
                {"ticker": "SMH", "mark": "200.5"},
                {"ticker": "VOO", "mark": "500"},
            ]
        ),
    )
    assert result.status is exposure.ExposureObservationStatus.VALID_REPORT_ONLY
    assert result.reason_codes == ("US_EQUITY_SESSION_MARK_DATE_FRESH",)
    assert result.authority_effect == "NONE"
    assert result.projection is not None
    assert result.projection.authority_effect == "NONE"
    assert result.projection.calendar_id == "US_EQUITY_REGULAR"
    assert result.projection.calendar_schedule_sha256 == (
        "a7142dcf13f52f30f07cc48942abe1e325ace21d644a2198c5e5667cf9d20007"
    )
    assert result.projection.latest_completed_session_date == "2026-08-12"
    assert result.projection.latest_completed_session_close_timestamp_et == (
        "2026-08-12T16:00:00-04:00"
    )
    assert result.projection.freshness_status == "FRESH"
    assert freshness_results[0].mark_as_of_date == date(2026, 8, 12)
    assert result.projection.mark_as_of_date == (
        freshness_results[0].mark_as_of_date.isoformat()
    )
    projection_fields = set(result.projection.__dataclass_fields__)
    assert not {
        "target_commitment",
        "desired_increment",
        "final_increment",
        "priority",
        "order",
        "quantity",
    } & projection_fields


def test_observer_rejects_a_freshness_mark_date_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freshness_owner = exposure.assess_manual_mark_freshness

    def _mismatched_freshness(**kwargs: object) -> calendar.MarkFreshnessResult:
        result = freshness_owner(**kwargs)
        assert result.status is calendar.MarkFreshnessStatus.FRESH
        return replace(result, mark_as_of_date=date(2026, 8, 11))

    monkeypatch.setattr(
        exposure,
        "assess_manual_mark_freshness",
        _mismatched_freshness,
    )
    result = _observe(tmp_path, monkeypatch)
    assert result.status is exposure.ExposureObservationStatus.INVALID
    assert result.reason_codes == ("US_EQUITY_SESSION_MARK_DATE_PROVENANCE_MISMATCH",)
    assert result.projection is None


@pytest.mark.parametrize(
    ("mark_as_of", "status", "reason"),
    (
        (
            "2026-08-11",
            exposure.ExposureObservationStatus.UNAVAILABLE,
            "US_EQUITY_SESSION_MARK_DATE_STALE",
        ),
        (
            "2026-08-13",
            exposure.ExposureObservationStatus.INVALID,
            "US_EQUITY_SESSION_MARK_DATE_AFTER_EVALUATION",
        ),
        (
            "2026-08-08",
            exposure.ExposureObservationStatus.INVALID,
            "US_EQUITY_SESSION_MARK_DATE_NON_SESSION",
        ),
    ),
)
def test_observer_consumes_one_calendar_owned_mark_freshness_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mark_as_of: str,
    status: exposure.ExposureObservationStatus,
    reason: str,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        valuation_bytes=_valuation(as_of=mark_as_of),
    )
    assert result.status is status
    assert result.reason_codes == (reason,)
    assert result.projection is None


def test_calendar_unavailability_remains_observer_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calendar, "repo_root", lambda: tmp_path)
    result = _observe(tmp_path, monkeypatch)
    assert result.status is exposure.ExposureObservationStatus.UNAVAILABLE
    assert result.reason_codes == ("US_EQUITY_SESSION_CALENDAR_SOURCE_UNAVAILABLE",)
    assert result.projection is None


def test_malformed_present_holdings_beat_freshness_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        portfolio_bytes=_portfolio(("QQQ | 0",)),
        valuation_bytes=_valuation([{"ticker": "QQQ", "mark": "400"}]),
    )
    assert result.status is exposure.ExposureObservationStatus.INVALID
    assert result.reason_codes == (
        "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SHARES_INVALID",
    )


def test_malformed_present_valuation_beats_freshness_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        valuation_bytes=_valuation(
            [
                {"ticker": "QQQ", "mark": "400.125"},
                {"ticker": "SMH", "mark": "0"},
            ]
        ),
    )
    assert result.status is exposure.ExposureObservationStatus.INVALID
    assert result.reason_codes == ("REPORT_ONLY_EXPOSURE_VALUATION_MARK_INVALID",)


def test_missing_required_section_and_valuation_source_are_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy_source, portfolio_source = _prepared_current_root(
        tmp_path,
        portfolio_bytes=(repo_root() / "inputs/current/portfolio_snapshot.txt").read_bytes(),
        valuation_bytes=None,
    )

    def _capture(role: MmiSourceRole, *, expected_source_sha256: str):
        return type("Result", (), {
            "valid": True,
            "authority_effect": "NONE",
            "source": strategy_source if role is MmiSourceRole.STRATEGY_SETTINGS else portfolio_source,
            "reason_codes": (),
        })()

    monkeypatch.setattr(exposure, "capture_current_mmi_source", _capture)
    monkeypatch.setattr(exposure, "repo_root", lambda: tmp_path)
    result = exposure.observe_current_report_only_holdings_exposure(
        strategy_settings_expected_sha256="a" * 64,
        portfolio_snapshot_expected_sha256="b" * 64,
    )
    assert result.status is exposure.ExposureObservationStatus.UNAVAILABLE
    assert set(result.reason_codes) == {
        "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SECTION_ABSENT",
        "REPORT_ONLY_EXPOSURE_VALUATION_SOURCE_ABSENT",
    }


def test_present_nonregular_valuation_source_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy_source, portfolio_source = _prepared_current_root(
        tmp_path,
        portfolio_bytes=_portfolio(),
        valuation_bytes=None,
    )
    valuation_path = tmp_path / "inputs/current/manual_valuation_marks.json"
    target = tmp_path / "operator_marks.json"
    target.write_bytes(_valuation())
    valuation_path.symlink_to(target)

    def _capture(role: MmiSourceRole, *, expected_source_sha256: str):
        return type("Result", (), {
            "valid": True,
            "authority_effect": "NONE",
            "source": strategy_source if role is MmiSourceRole.STRATEGY_SETTINGS else portfolio_source,
            "reason_codes": (),
        })()

    monkeypatch.setattr(exposure, "capture_current_mmi_source", _capture)
    monkeypatch.setattr(exposure, "repo_root", lambda: tmp_path)
    result = exposure.observe_current_report_only_holdings_exposure(
        strategy_settings_expected_sha256="a" * 64,
        portfolio_snapshot_expected_sha256="b" * 64,
    )
    assert result.status is exposure.ExposureObservationStatus.INVALID
    assert result.reason_codes == ("REPORT_ONLY_EXPOSURE_VALUATION_SOURCE_INVALID",)


def test_domain_equality_and_no_partial_total_are_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        valuation_bytes=_valuation([{"ticker": "QQQ", "mark": "400"}]),
    )
    assert result.status is exposure.ExposureObservationStatus.INVALID
    assert result.reason_codes == (
        "REPORT_ONLY_EXPOSURE_VALUATION_TICKER_DOMAIN_MISMATCH",
    )
    assert result.projection is None


def test_policy_roles_are_source_bound_and_unknown_holding_is_manual_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _observe(
        tmp_path,
        monkeypatch,
        portfolio_bytes=_portfolio(("QQQ | 1", "ZZZ | 2")),
        valuation_bytes=_valuation(
            [
                {"ticker": "QQQ", "mark": "400"},
                {"ticker": "ZZZ", "mark": "100"},
            ]
        ),
    )
    assert result.status is exposure.ExposureObservationStatus.MANUAL_REVIEW
    assert result.reason_codes == (
        "REPORT_ONLY_EXPOSURE_TICKER_OUTSIDE_DETERMINISTIC_POLICY",
    )
    assert result.projection is not None
    assert result.projection.policy_projection_identity_sha256
    assert result.projection.positions[1].classification == "UNCLASSIFIED"
    assert "policy_role_by_ticker" not in Path(exposure.__file__).read_text(encoding="utf-8")


def test_private_decimal_arithmetic_has_one_market_value_owner(tmp_path: Path) -> None:
    strategy_source, portfolio_source = _prepared_current_root(
        tmp_path,
        portfolio_bytes=_portfolio(),
        valuation_bytes=_valuation(),
    )
    run_context = exposure.begin_mmi_projection_run()
    policy = exposure.build_mmi_policy_projection(
        strategy_source,
        run_context=run_context,
    ).projection
    assert isinstance(policy, dict)
    roles, identity = exposure._policy_roles_and_identity(policy)
    holdings = exposure._parse_strict_holdings(portfolio_source.raw_bytes)
    valuation_source = exposure._CapturedManualValuationSource(
        raw_bytes=_valuation(),
        observed_sha256=hashlib.sha256(_valuation()).hexdigest(),
        observed_size_bytes=len(_valuation()),
        repository_relative_locator="inputs/current/manual_valuation_marks.json",
    )
    valuation = exposure._parse_manual_valuation(
        valuation_source.raw_bytes,
    )
    projection, unknown = exposure._derive_projection(
        holdings=holdings,
        valuation=valuation,
        portfolio_source=portfolio_source,
        valuation_source=valuation_source,
        holdings_observation_date="2026-08-12",
        policy_roles=roles,
        policy_projection_identity_sha256=identity,
    )
    assert unknown == ()
    assert [row.market_value for row in projection.positions] == ["1000.3125", "601.5"]
    assert projection.total_market_value == "1601.8125"
    assert projection.portfolio_source_sha256 == hashlib.sha256(
        portfolio_source.raw_bytes
    ).hexdigest()
    assert projection.valuation_source_sha256 == hashlib.sha256(
        valuation_source.raw_bytes
    ).hexdigest()
    assert "market_value" not in _valuation().decode("utf-8")


def test_observer_is_not_imported_by_existing_authority_bearing_flows() -> None:
    root = Path(__file__).resolve().parents[2] / "src/investment_orchestrator"
    module_file = root / "observability/report_only_holdings_exposure.py"
    calendar_file = root / "market/us_equity_session_calendar.py"
    prohibited = (
        "investment_orchestrator.workflow",
        "investment_orchestrator.state",
        "investment_orchestrator.permissions",
        "investment_orchestrator.orders",
        "investment_orchestrator.broker",
        "investment_orchestrator.llm",
    )
    module_text = module_file.read_text(encoding="utf-8")
    assert all(value not in module_text for value in prohibited)
    calendar_text = calendar_file.read_text(encoding="utf-8")
    assert all(value not in calendar_text for value in prohibited)
    assert all(
        "report_only_holdings_exposure" not in candidate.read_text(encoding="utf-8")
        and "us_equity_session_calendar" not in candidate.read_text(encoding="utf-8")
        for candidate in root.rglob("*.py")
        if candidate not in {module_file, calendar_file}
    )
