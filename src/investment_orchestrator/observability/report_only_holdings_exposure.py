"""Strict, report-only ETF holdings and manually supplied mark observation.

This observer is intentionally disconnected from every current workflow.  Its
public observer captures the existing source-bound MMI strategy and portfolio
sources, reads an optional manual valuation source, and returns an in-memory
diagnostic.  A second narrow accessor exposes only the existing parser-owned
strict holdings domain for a separate report-only acquisition boundary.  This
module never publishes, prompts, grants permission, opens a gate, compiles an
order, or contacts a provider.

The strict positions section is optional and is the only holdings syntax this
module reads.  It has no date: the existing ``# updated YYYY-MM-DD`` portfolio
header remains the sole holdings-observation-date owner, as parsed by the
existing MMI portfolio projection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, InvalidOperation, localcontext
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Final

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi.canonical import (
    MmiCanonicalizationError,
    normalize_decimal_string,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MmiCapturedSource,
    MmiSourceRole,
    begin_mmi_projection_run,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.mmi.source_capture import (
    capture_current_mmi_source,
)
from investment_orchestrator.mmi.stable_read import (
    MmiStableReadError,
    MmiStableReadErrorCode,
    stable_read_exact_bytes,
)
from investment_orchestrator.market.us_equity_session_calendar import (
    MarkFreshnessStatus,
    assess_manual_mark_freshness,
)


__all__ = (
    "ExposureObservationResult",
    "ExposureObservationStatus",
    "ExposurePosition",
    "ExposureProjection",
    "StrictHoldingsDomain",
    "StrictHoldingsDomainError",
    "capture_current_validated_strict_holdings_domain",
    "observe_current_report_only_holdings_exposure",
)


MANUAL_VALUATION_MARKS_RELATIVE_PATH: Final = (
    "inputs",
    "current",
    "manual_valuation_marks.json",
)
_VALUATION_SOURCE_MAXIMUM_BYTES: Final = 262_144
_POSITIONS_START: Final = "[STRICT_POSITIVE_ETF_POSITIONS_V1]"
_POSITIONS_END: Final = "[/STRICT_POSITIVE_ETF_POSITIONS_V1]"
_VALUATION_SCHEMA_VERSION: Final = "manual_valuation_marks_v1"
_POSITIONS_PREFIX: Final = (
    "schema_version = strict_positive_etf_positions_v1",
    "portfolio_scope_id = ",
    "operator_scope_complete = true",
    "TICKER | shares",
)
_TICKER_RE: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_SCOPE_ID_RE: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_MARK_SOURCE_ID_RE: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DECIMAL_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_POLICY_ROLES: Final = frozenset(
    {"CORE", "SATELLITE", "APPROVED_EXTENDED"}
)
_ARITHMETIC_CONTEXT: Final = Context(prec=112)
_CAPTURE_ABSENT_CODES: Final = frozenset({"MMI_SOURCE_MISSING"})


class ExposureObservationStatus(str, Enum):
    """Closed diagnostic-only status vocabulary."""

    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    VALID_REPORT_ONLY = "VALID_REPORT_ONLY"


class _ExposureInputError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StrictHoldingsDomainError(ValueError):
    """Closed current-source failure from the existing strict-holdings owner."""

    def __init__(
        self,
        reason_codes: tuple[str, ...],
        *,
        unavailable: bool,
    ) -> None:
        super().__init__(reason_codes[0] if reason_codes else "STRICT_HOLDINGS_INVALID")
        self.reason_codes = reason_codes
        self.unavailable = unavailable


@dataclass(frozen=True, slots=True)
class _CapturedManualValuationSource:
    raw_bytes: bytes
    observed_sha256: str
    observed_size_bytes: int
    repository_relative_locator: str
    stable_read_status: str = "STABLE_BEFORE_AND_AFTER"
    regular_file_status: str = "REGULAR_FILE"


@dataclass(frozen=True, slots=True)
class _StrictPosition:
    ticker: str
    shares: Decimal
    source_shares: str


@dataclass(frozen=True, slots=True)
class _StrictHoldings:
    portfolio_scope_id: str
    positions: tuple[_StrictPosition, ...]


@dataclass(frozen=True, slots=True)
class StrictHoldingsDomain:
    """One source-bound, parser-owned strict holdings ticker domain."""

    portfolio_source_sha256: str
    portfolio_scope_id: str
    tickers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ManualMark:
    ticker: str
    mark: Decimal
    source_mark: str


@dataclass(frozen=True, slots=True)
class _ManualValuation:
    mark_source_id: str
    mark_as_of_date: date
    marks: tuple[_ManualMark, ...]


@dataclass(frozen=True, slots=True)
class ExposurePosition:
    ticker: str
    shares: str
    mark: str
    market_value: str
    classification: str


@dataclass(frozen=True, slots=True)
class ExposureProjection:
    """Derived diagnostic H_i/H facts, never an actionable portfolio state."""

    schema_version: str
    authority_effect: str
    portfolio_source_sha256: str
    portfolio_source_record_identity_sha256: str
    valuation_source_sha256: str
    portfolio_scope_id: str
    holdings_observation_date: str
    valuation_mark_source_id: str
    mark_as_of_date: str
    calendar_id: str | None
    calendar_schedule_sha256: str | None
    calendar_coverage_start_date: str | None
    calendar_coverage_end_date: str | None
    trusted_evaluation_timestamp_utc: str | None
    latest_completed_session_date: str | None
    latest_completed_session_close_timestamp_et: str | None
    freshness_status: str | None
    policy_projection_identity_sha256: str
    currency: str
    positions: tuple[ExposurePosition, ...]
    total_market_value: str


@dataclass(frozen=True, slots=True)
class ExposureObservationResult:
    authority_effect: str
    status: ExposureObservationStatus
    reason_codes: tuple[str, ...]
    projection: ExposureProjection | None


def _decode_utf8(raw_bytes: bytes, *, code: str) -> str:
    if type(raw_bytes) is not bytes or raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise _ExposureInputError(code)
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _ExposureInputError(code) from None
    if "\x00" in text or "\r" in text:
        raise _ExposureInputError(code)
    return text


def _parse_date(value: object, *, code: str) -> date:
    if type(value) is not str or not _DATE_RE.fullmatch(value):
        raise _ExposureInputError(code)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _ExposureInputError(code) from None


def _parse_positive_decimal(value: object, *, code: str) -> Decimal:
    if type(value) is not str or not _DECIMAL_RE.fullmatch(value):
        raise _ExposureInputError(code)
    try:
        decimal_value = Decimal(value)
        normalize_decimal_string(decimal_value)
    except (InvalidOperation, MmiCanonicalizationError):
        raise _ExposureInputError(code) from None
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise _ExposureInputError(code)
    return decimal_value


def _parse_strict_holdings(raw_bytes: bytes) -> _StrictHoldings:
    text = _decode_utf8(
        raw_bytes,
        code="REPORT_ONLY_EXPOSURE_PORTFOLIO_SOURCE_INVALID",
    )
    lines = text.split("\n")
    starts = [index for index, line in enumerate(lines) if line == _POSITIONS_START]
    ends = [index for index, line in enumerate(lines) if line == _POSITIONS_END]
    if not starts and not ends:
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SECTION_ABSENT"
        )
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SECTION_INVALID"
        )
    section = lines[starts[0] + 1 : ends[0]]
    if len(section) < len(_POSITIONS_PREFIX):
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SECTION_INVALID"
        )
    if (
        section[0] != _POSITIONS_PREFIX[0]
        or not section[1].startswith(_POSITIONS_PREFIX[1])
        or section[2] != _POSITIONS_PREFIX[2]
        or section[3] != _POSITIONS_PREFIX[3]
    ):
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SECTION_INVALID"
        )
    scope_id = section[1][len("portfolio_scope_id = ") :]
    if not _SCOPE_ID_RE.fullmatch(scope_id):
        raise _ExposureInputError("REPORT_ONLY_EXPOSURE_PORTFOLIO_SCOPE_INVALID")
    positions: list[_StrictPosition] = []
    seen_tickers: set[str] = set()
    for row in section[len(_POSITIONS_PREFIX) :]:
        cells = row.split(" | ")
        if len(cells) != 2:
            raise _ExposureInputError(
                "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_ROW_INVALID"
            )
        ticker, shares_text = cells
        if not _TICKER_RE.fullmatch(ticker):
            raise _ExposureInputError(
                "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_TICKER_INVALID"
            )
        if ticker in seen_tickers:
            raise _ExposureInputError(
                "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_TICKER_DUPLICATE"
            )
        positions.append(
            _StrictPosition(
                ticker=ticker,
                shares=_parse_positive_decimal(
                    shares_text,
                    code="REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SHARES_INVALID",
                ),
                source_shares=shares_text,
            )
        )
        seen_tickers.add(ticker)
    return _StrictHoldings(
        portfolio_scope_id=scope_id,
        positions=tuple(positions),
    )


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise _ExposureInputError(
                "REPORT_ONLY_EXPOSURE_VALUATION_JSON_DUPLICATE_KEY"
            )
        output[key] = value
    return output


def _parse_manual_valuation(
    raw_bytes: bytes,
) -> _ManualValuation:
    text = _decode_utf8(
        raw_bytes,
        code="REPORT_ONLY_EXPOSURE_VALUATION_SOURCE_INVALID",
    )
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except (_ExposureInputError, ValueError):
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_VALUATION_SOURCE_INVALID"
        ) from None
    if type(payload) is not dict or set(payload) != {
        "schema_version",
        "currency",
        "mark_source_id",
        "mark_as_of_date",
        "marks",
    }:
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_VALUATION_SCHEMA_INVALID"
        )
    if payload.get("schema_version") != _VALUATION_SCHEMA_VERSION:
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_VALUATION_SCHEMA_INVALID"
        )
    if payload.get("currency") != "USD":
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_VALUATION_CURRENCY_INVALID"
        )
    mark_source_id = payload.get("mark_source_id")
    if (
        type(mark_source_id) is not str
        or not _MARK_SOURCE_ID_RE.fullmatch(mark_source_id)
    ):
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_VALUATION_MARK_SOURCE_INVALID"
        )
    mark_as_of_date = _parse_date(
        payload.get("mark_as_of_date"),
        code="REPORT_ONLY_EXPOSURE_VALUATION_DATE_INVALID",
    )
    raw_marks = payload.get("marks")
    if type(raw_marks) is not list:
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_VALUATION_MARKS_INVALID"
        )
    marks: list[_ManualMark] = []
    seen_tickers: set[str] = set()
    for row in raw_marks:
        if type(row) is not dict or set(row) != {"ticker", "mark"}:
            raise _ExposureInputError(
                "REPORT_ONLY_EXPOSURE_VALUATION_MARK_ROW_INVALID"
            )
        ticker = row.get("ticker")
        if type(ticker) is not str or not _TICKER_RE.fullmatch(ticker):
            raise _ExposureInputError(
                "REPORT_ONLY_EXPOSURE_VALUATION_TICKER_INVALID"
            )
        if ticker in seen_tickers:
            raise _ExposureInputError(
                "REPORT_ONLY_EXPOSURE_VALUATION_TICKER_DUPLICATE"
            )
        mark_text = row.get("mark")
        marks.append(
            _ManualMark(
                ticker=ticker,
                mark=_parse_positive_decimal(
                    mark_text,
                    code="REPORT_ONLY_EXPOSURE_VALUATION_MARK_INVALID",
                ),
                source_mark=mark_text,
            )
        )
        seen_tickers.add(ticker)
    return _ManualValuation(
        mark_source_id=mark_source_id,
        mark_as_of_date=mark_as_of_date,
        marks=tuple(marks),
    )


def _open_current_inputs_directory(root: Path) -> tuple[int, int]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    root_fd: int | None = None
    inputs_fd: int | None = None
    current_fd: int | None = None
    try:
        root_fd = os.open(os.fspath(root), flags)
        inputs_fd = os.open("inputs", flags, dir_fd=root_fd)
        current_fd = os.open("current", flags, dir_fd=inputs_fd)
        if not all(
            stat.S_ISDIR(os.fstat(fd).st_mode)
            for fd in (root_fd, inputs_fd, current_fd)
        ):
            raise OSError("current input hierarchy is not a directory")
        return root_fd, current_fd
    except OSError:
        for fd in (current_fd, inputs_fd, root_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        raise
    finally:
        if inputs_fd is not None:
            try:
                os.close(inputs_fd)
            except OSError:
                pass


def _capture_manual_valuation_source() -> tuple[
    _CapturedManualValuationSource | None,
    tuple[str, ...],
    bool,
]:
    try:
        root_fd, current_fd = _open_current_inputs_directory(repo_root())
    except OSError:
        return (
            None,
            ("REPORT_ONLY_EXPOSURE_INPUT_DIRECTORY_UNAVAILABLE",),
            False,
        )
    try:
        try:
            raw_bytes = stable_read_exact_bytes(
                current_fd,
                MANUAL_VALUATION_MARKS_RELATIVE_PATH[-1],
                maximum_bytes=_VALUATION_SOURCE_MAXIMUM_BYTES,
            )
        except MmiStableReadError as exc:
            if exc.code is MmiStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE:
                return (
                    None,
                    ("REPORT_ONLY_EXPOSURE_SOURCE_CAPTURE_CAPABILITY_UNAVAILABLE",),
                    False,
                )
            try:
                os.stat(
                    MANUAL_VALUATION_MARKS_RELATIVE_PATH[-1],
                    dir_fd=current_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return (
                    None,
                    ("REPORT_ONLY_EXPOSURE_VALUATION_SOURCE_ABSENT",),
                    False,
                )
            except OSError:
                return (
                    None,
                    ("REPORT_ONLY_EXPOSURE_VALUATION_SOURCE_UNREADABLE",),
                    False,
                )
            return (
                None,
                ("REPORT_ONLY_EXPOSURE_VALUATION_SOURCE_INVALID",),
                True,
            )
        except OSError:
            return (
                None,
                ("REPORT_ONLY_EXPOSURE_VALUATION_SOURCE_UNREADABLE",),
                False,
            )
    finally:
        os.close(current_fd)
        os.close(root_fd)
    return (
        _CapturedManualValuationSource(
            raw_bytes=raw_bytes,
            observed_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            observed_size_bytes=len(raw_bytes),
            repository_relative_locator="/".join(
                MANUAL_VALUATION_MARKS_RELATIVE_PATH
            ),
        ),
        (),
        False,
    )


def _capture_current_mmi_source(
    role: MmiSourceRole,
    *,
    expected_sha256: str,
) -> tuple[MmiCapturedSource | None, tuple[str, ...], bool]:
    result = capture_current_mmi_source(
        role,
        expected_source_sha256=expected_sha256,
    )
    reasons = tuple(result.reason_codes)
    if (
        result.valid
        and result.authority_effect == AUTHORITY_EFFECT_NONE
        and type(result.source) is MmiCapturedSource
        and result.source.role is role
    ):
        return result.source, (), False
    return None, reasons or ("MMI_SOURCE_CAPTURE_INVALID",), bool(
        set(reasons) - _CAPTURE_ABSENT_CODES
    )


def capture_current_validated_strict_holdings_domain(
    *,
    portfolio_snapshot_expected_sha256: str,
) -> StrictHoldingsDomain:
    """Read the fixed current portfolio source through its existing owner.

    This exposes only the parser-owned scope and ticker domain for a separate
    report-only acquisition boundary.  It accepts no source path, ticker list,
    valuation date, or holdings facts supplied outside the fixed MMI capture.
    """
    source, reasons, source_invalid = _capture_current_mmi_source(
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
        expected_sha256=portfolio_snapshot_expected_sha256,
    )
    if source is None:
        raise StrictHoldingsDomainError(
            reasons,
            unavailable=not source_invalid,
        )
    portfolio_source_sha256 = source.source_record.get("observed_sha256")
    if type(portfolio_source_sha256) is not str:
        raise StrictHoldingsDomainError(
            ("REPORT_ONLY_EXPOSURE_PORTFOLIO_SOURCE_IDENTITY_INVALID",),
            unavailable=False,
        )
    try:
        holdings = _parse_strict_holdings(source.raw_bytes)
    except _ExposureInputError as exc:
        raise StrictHoldingsDomainError(
            (exc.code,),
            unavailable=(
                exc.code == "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SECTION_ABSENT"
            ),
        ) from None
    return StrictHoldingsDomain(
        portfolio_source_sha256=portfolio_source_sha256,
        portfolio_scope_id=holdings.portfolio_scope_id,
        tickers=tuple(position.ticker for position in holdings.positions),
    )


def _source_sha256(source: MmiCapturedSource) -> str:
    return hashlib.sha256(source.raw_bytes).hexdigest()


def _source_record_identity(source: MmiCapturedSource) -> str:
    value = source.source_record.get("source_record_identity_sha256")
    if type(value) is not str:
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_PORTFOLIO_SOURCE_IDENTITY_INVALID"
        )
    return value


def _policy_roles_and_identity(
    policy_projection: Mapping[str, object],
) -> tuple[Mapping[str, str], str]:
    identity = policy_projection.get("policy_projection_identity_sha256")
    universe = policy_projection.get("universe_projection")
    if type(identity) is not str or type(universe) is not dict:
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_POLICY_PROJECTION_INVALID"
        )
    raw_roles = universe.get("role_by_ticker")
    if type(raw_roles) is not dict:
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_POLICY_PROJECTION_INVALID"
        )
    roles: dict[str, str] = {}
    for ticker, role in raw_roles.items():
        if (
            type(ticker) is not str
            or not _TICKER_RE.fullmatch(ticker)
            or role not in _POLICY_ROLES
        ):
            raise _ExposureInputError(
                "REPORT_ONLY_EXPOSURE_POLICY_PROJECTION_INVALID"
            )
        roles[ticker] = role
    return roles, identity


def _derive_projection(
    *,
    holdings: _StrictHoldings,
    valuation: _ManualValuation,
    portfolio_source: MmiCapturedSource,
    valuation_source: _CapturedManualValuationSource,
    holdings_observation_date: str,
    policy_roles: Mapping[str, str],
    policy_projection_identity_sha256: str,
    calendar_id: str | None = None,
    calendar_schedule_sha256: str | None = None,
    calendar_coverage_start_date: str | None = None,
    calendar_coverage_end_date: str | None = None,
    trusted_evaluation_timestamp_utc: str | None = None,
    latest_completed_session_date: str | None = None,
    latest_completed_session_close_timestamp_et: str | None = None,
    verified_mark_as_of_date: date | None = None,
) -> tuple[ExposureProjection, tuple[str, ...]]:
    holding_tickers = {position.ticker for position in holdings.positions}
    valuation_tickers = {mark.ticker for mark in valuation.marks}
    if holding_tickers != valuation_tickers:
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_VALUATION_TICKER_DOMAIN_MISMATCH"
        )
    marks_by_ticker = {mark.ticker: mark for mark in valuation.marks}
    rows: list[ExposurePosition] = []
    unclassified: list[str] = []
    total = Decimal(0)
    try:
        with localcontext(_ARITHMETIC_CONTEXT):
            for position in holdings.positions:
                mark = marks_by_ticker[position.ticker]
                market_value = position.shares * mark.mark
                total += market_value
                classification = policy_roles.get(
                    position.ticker,
                    "UNCLASSIFIED",
                )
                if classification == "UNCLASSIFIED":
                    unclassified.append(position.ticker)
                rows.append(
                    ExposurePosition(
                        ticker=position.ticker,
                        shares=position.source_shares,
                        mark=mark.source_mark,
                        market_value=normalize_decimal_string(market_value),
                        classification=classification,
                    )
                )
            normalized_total = normalize_decimal_string(total)
    except (InvalidOperation, MmiCanonicalizationError):
        raise _ExposureInputError(
            "REPORT_ONLY_EXPOSURE_ARITHMETIC_INVALID"
        ) from None
    return (
        ExposureProjection(
            schema_version="report_only_holdings_exposure_projection_v1",
            authority_effect=AUTHORITY_EFFECT_NONE,
            portfolio_source_sha256=_source_sha256(portfolio_source),
            portfolio_source_record_identity_sha256=_source_record_identity(
                portfolio_source
            ),
            valuation_source_sha256=valuation_source.observed_sha256,
            portfolio_scope_id=holdings.portfolio_scope_id,
            holdings_observation_date=holdings_observation_date,
            valuation_mark_source_id=valuation.mark_source_id,
            mark_as_of_date=(
                verified_mark_as_of_date
                if verified_mark_as_of_date is not None
                else valuation.mark_as_of_date
            ).isoformat(),
            calendar_id=calendar_id,
            calendar_schedule_sha256=calendar_schedule_sha256,
            calendar_coverage_start_date=calendar_coverage_start_date,
            calendar_coverage_end_date=calendar_coverage_end_date,
            trusted_evaluation_timestamp_utc=trusted_evaluation_timestamp_utc,
            latest_completed_session_date=latest_completed_session_date,
            latest_completed_session_close_timestamp_et=(
                latest_completed_session_close_timestamp_et
            ),
            freshness_status=(
                MarkFreshnessStatus.FRESH.value
                if calendar_id is not None
                else None
            ),
            policy_projection_identity_sha256=policy_projection_identity_sha256,
            currency="USD",
            positions=tuple(rows),
            total_market_value=normalized_total,
        ),
        tuple(sorted(unclassified)),
    )


def _result(
    status: ExposureObservationStatus,
    reason_codes: tuple[str, ...],
    projection: ExposureProjection | None = None,
) -> ExposureObservationResult:
    return ExposureObservationResult(
        authority_effect=AUTHORITY_EFFECT_NONE,
        status=status,
        reason_codes=reason_codes,
        projection=projection,
    )


def observe_current_report_only_holdings_exposure(
    *,
    strategy_settings_expected_sha256: str,
    portfolio_snapshot_expected_sha256: str,
) -> ExposureObservationResult:
    """Observe current strict holdings and marks without authority effects.

    The existing MMI source capture and projection owners supply source-bound
    universe roles and the sole canonical portfolio date.  The session owner
    alone determines whether a parsed manual mark date is fresh.  This entry
    point deliberately has no caller-supplied evaluation or session date.
    """
    run_context = begin_mmi_projection_run()
    strategy_source, strategy_reasons, strategy_invalid = (
        _capture_current_mmi_source(
            MmiSourceRole.STRATEGY_SETTINGS,
            expected_sha256=strategy_settings_expected_sha256,
        )
    )
    portfolio_source, portfolio_reasons, portfolio_invalid = (
        _capture_current_mmi_source(
            MmiSourceRole.PORTFOLIO_SNAPSHOT,
            expected_sha256=portfolio_snapshot_expected_sha256,
        )
    )
    valuation_source, valuation_capture_reasons, valuation_capture_invalid = (
        _capture_manual_valuation_source()
    )

    invalid_reasons: list[str] = []
    unavailable_reasons: list[str] = []
    if strategy_invalid:
        invalid_reasons.extend(strategy_reasons)
    elif strategy_source is None:
        unavailable_reasons.extend(strategy_reasons)
    if portfolio_invalid:
        invalid_reasons.extend(portfolio_reasons)
    elif portfolio_source is None:
        unavailable_reasons.extend(portfolio_reasons)
    if valuation_source is None:
        if valuation_capture_invalid:
            invalid_reasons.extend(valuation_capture_reasons)
        else:
            unavailable_reasons.extend(valuation_capture_reasons)

    holdings: _StrictHoldings | None = None
    valuation: _ManualValuation | None = None
    if portfolio_source is not None:
        try:
            holdings = _parse_strict_holdings(portfolio_source.raw_bytes)
        except _ExposureInputError as exc:
            if exc.code == "REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SECTION_ABSENT":
                unavailable_reasons.append(exc.code)
            else:
                invalid_reasons.append(exc.code)
    if valuation_source is not None:
        try:
            valuation = _parse_manual_valuation(valuation_source.raw_bytes)
        except _ExposureInputError as exc:
            invalid_reasons.append(exc.code)

    policy_projection: Mapping[str, object] | None = None
    portfolio_projection: Mapping[str, object] | None = None
    if strategy_source is not None:
        policy_result = build_mmi_policy_projection(
            strategy_source,
            run_context=run_context,
        )
        if policy_result.valid and type(policy_result.projection) is dict:
            policy_projection = policy_result.projection
        else:
            invalid_reasons.extend(policy_result.reason_codes)
    if policy_projection is not None and portfolio_source is not None and strategy_source is not None:
        portfolio_result = build_mmi_portfolio_snapshot_projection(
            portfolio_source,
            policy_projection=policy_projection,
            policy_source=strategy_source,
            run_context=run_context,
        )
        if portfolio_result.valid and type(portfolio_result.projection) is dict:
            portfolio_projection = portfolio_result.projection
        else:
            invalid_reasons.extend(portfolio_result.reason_codes)

    if invalid_reasons:
        return _result(
            ExposureObservationStatus.INVALID,
            tuple(sorted(set(invalid_reasons))),
        )
    if unavailable_reasons:
        return _result(
            ExposureObservationStatus.UNAVAILABLE,
            tuple(sorted(set(unavailable_reasons))),
        )
    if (
        holdings is None
        or valuation is None
        or policy_projection is None
        or portfolio_projection is None
        or portfolio_source is None
        or valuation_source is None
    ):
        return _result(
            ExposureObservationStatus.UNAVAILABLE,
            ("REPORT_ONLY_EXPOSURE_INTERNAL_INPUT_UNAVAILABLE",),
        )

    try:
        policy_roles, policy_identity = _policy_roles_and_identity(
            policy_projection
        )
        holdings_observation_date = portfolio_projection.get(
            "portfolio_source_date"
        )
        if type(holdings_observation_date) is not str:
            return _result(
                ExposureObservationStatus.UNAVAILABLE,
                ("PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE",),
            )
        projection, unclassified = _derive_projection(
            holdings=holdings,
            valuation=valuation,
            portfolio_source=portfolio_source,
            valuation_source=valuation_source,
            holdings_observation_date=holdings_observation_date,
            policy_roles=policy_roles,
            policy_projection_identity_sha256=policy_identity,
        )
    except _ExposureInputError as exc:
        return _result(ExposureObservationStatus.INVALID, (exc.code,))
    if unclassified:
        return _result(
            ExposureObservationStatus.MANUAL_REVIEW,
            ("REPORT_ONLY_EXPOSURE_TICKER_OUTSIDE_DETERMINISTIC_POLICY",),
            projection,
        )
    freshness = assess_manual_mark_freshness(
        mark_as_of_date=valuation.mark_as_of_date,
        run_context=run_context,
    )
    if freshness.status is MarkFreshnessStatus.INVALID:
        return _result(ExposureObservationStatus.INVALID, freshness.reason_codes)
    if freshness.status is MarkFreshnessStatus.UNAVAILABLE:
        return _result(ExposureObservationStatus.UNAVAILABLE, freshness.reason_codes)
    if freshness.status is not MarkFreshnessStatus.FRESH:
        return _result(
            ExposureObservationStatus.INVALID,
            ("US_EQUITY_SESSION_MARK_DATE_STATUS_INVALID",),
        )
    if freshness.mark_as_of_date != valuation.mark_as_of_date:
        return _result(
            ExposureObservationStatus.INVALID,
            ("US_EQUITY_SESSION_MARK_DATE_PROVENANCE_MISMATCH",),
        )
    completed_session = freshness.completed_session
    if completed_session is None:
        return _result(
            ExposureObservationStatus.INVALID,
            ("US_EQUITY_SESSION_FRESHNESS_FACT_UNAVAILABLE",),
        )
    try:
        projection, unclassified = _derive_projection(
            holdings=holdings,
            valuation=valuation,
            portfolio_source=portfolio_source,
            valuation_source=valuation_source,
            holdings_observation_date=holdings_observation_date,
            policy_roles=policy_roles,
            policy_projection_identity_sha256=policy_identity,
            calendar_id=completed_session.calendar_id,
            calendar_schedule_sha256=completed_session.calendar_schedule_sha256,
            calendar_coverage_start_date=completed_session.coverage_start_date,
            calendar_coverage_end_date=completed_session.coverage_end_date,
            trusted_evaluation_timestamp_utc=(
                completed_session.trusted_evaluation_timestamp_utc
            ),
            latest_completed_session_date=completed_session.session_date,
            latest_completed_session_close_timestamp_et=(
                completed_session.official_close_timestamp_et
            ),
            verified_mark_as_of_date=freshness.mark_as_of_date,
        )
    except _ExposureInputError as exc:
        return _result(ExposureObservationStatus.INVALID, (exc.code,))
    if unclassified:
        return _result(
            ExposureObservationStatus.MANUAL_REVIEW,
            ("REPORT_ONLY_EXPOSURE_TICKER_OUTSIDE_DETERMINISTIC_POLICY",),
            projection,
        )
    return _result(
        ExposureObservationStatus.VALID_REPORT_ONLY,
        freshness.reason_codes,
        projection,
    )
