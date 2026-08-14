"""Strict positive ETF holdings-domain types and fixed current-source accessor.

This module owns the parser-defined strict-holdings domain types
(``StrictHoldingsDomain``, ``StrictHoldingsDomainError``) and the
narrow ``capture_current_validated_strict_holdings_domain`` accessor
that reads the fixed current portfolio source through its existing
MMI capture owner.

Extracted verbatim from ``observability.report_only_holdings_exposure``
to eliminate the false ``market → observability`` dependency while
preserving byte-identical parser and provenance behaviour.  The module
has no provider client, no valuation logic, no session resolver, no
publication, no prompt, no gate, and no order authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Final

from investment_orchestrator.mmi.canonical import (
    MmiCanonicalizationError,
    normalize_decimal_string,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MmiCapturedSource,
    MmiSourceRole,
)
from investment_orchestrator.mmi.source_capture import (
    capture_current_mmi_source,
)


__all__ = (
    "StrictHoldingsDomain",
    "StrictHoldingsDomainError",
    "capture_current_validated_strict_holdings_domain",
)


_POSITIONS_START: Final = "[STRICT_POSITIVE_ETF_POSITIONS_V1]"
_POSITIONS_END: Final = "[/STRICT_POSITIVE_ETF_POSITIONS_V1]"
_POSITIONS_PREFIX: Final = (
    "schema_version = strict_positive_etf_positions_v1",
    "portfolio_scope_id = ",
    "operator_scope_complete = true",
    "TICKER | shares",
)
_TICKER_RE: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_SCOPE_ID_RE: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DECIMAL_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_CAPTURE_ABSENT_CODES: Final = frozenset({"MMI_SOURCE_MISSING"})


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
