"""Fixed-source, report-only current open-BUY capacity facts.

This module owns no allocation, target, order, availability, or permission
behavior.  It reads one optional human-owned monetary ceiling from its fixed
path and combines it with exact current BUY commitments resolved by the
existing portfolio section-(2a) parser.  The result is in-memory only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
import errno
import hashlib
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
)
from investment_orchestrator.mmi.source_capture import (
    capture_current_mmi_source,
)
from investment_orchestrator.mmi.portfolio_projection import (
    StrictCurrentOpenBuyCommitmentError,
    resolve_strict_current_open_buy_commitments,
)
from investment_orchestrator.common.stable_read import (
    MmiStableReadError,
    MmiStableReadErrorCode,
    stable_read_exact_bytes,
)


__all__ = (
    "BudgetCapacityObservationResult",
    "BudgetCapacityObservationStatus",
    "BudgetCapacityProjection",
    "BudgetCeilingSource",
    "CurrentOpenBuyCommitment",
    "observe_current_report_only_budget_capacity",
)


_BUDGET_CEILING_FILENAME: Final = "budget_ceiling.txt"
_BUDGET_CEILING_REPOSITORY_RELATIVE_LOCATOR: Final = (
    "inputs/current/budget_ceiling.txt"
)
_BUDGET_CEILING_MAXIMUM_BYTES: Final = 4_096
_BUDGET_CEILING_SCHEMA_LINE: Final = (
    "schema_version = report_only_budget_ceiling_v1"
)
_BUDGET_CEILING_CURRENCY_LINE: Final = "currency = USD"
_BUDGET_CEILING_AMOUNT_PREFIX: Final = (
    "maximum_total_unfilled_buy_commitment = "
)
_X_DECIMAL_RE: Final = re.compile(
    r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$"
)
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_PORTFOLIO_UPDATED_HEADER_RE: Final = re.compile(
    r"^# updated (?P<date>\d{4}-\d{2}-\d{2})$"
)
_PORTFOLIO_UPDATED_HEADER_CANDIDATE_RE: Final = re.compile(
    r"^\s*#\s*updated\b",
    re.IGNORECASE,
)
_PORTFOLIO_SECTION_START_RE: Final = re.compile(r"^\([0-9]+[a-z_]*\)")
_PORTFOLIO_SOURCE_UNAVAILABLE_CODES: Final = frozenset(
    {
        "MMI_SOURCE_MISSING",
        "MMI_SOURCE_UNREADABLE",
        "MMI_SOURCE_REPOSITORY_ROOT_UNAVAILABLE",
        "MMI_SOURCE_FILESYSTEM_PRIMITIVES_UNAVAILABLE",
    }
)


class BudgetCapacityObservationStatus(str, Enum):
    """Closed, non-authoritative status vocabulary."""

    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    VALID_REPORT_ONLY = "VALID_REPORT_ONLY"


@dataclass(frozen=True, slots=True)
class BudgetCeilingSource:
    """One actual stable-read of the fixed operator-owned X source."""

    repository_relative_locator: str
    observed_sha256: str
    observed_size_bytes: int
    currency: str
    maximum_total_unfilled_buy_commitment: str


@dataclass(frozen=True, slots=True)
class CurrentOpenBuyCommitment:
    """One exact current unfilled BUY commitment, never a target."""

    ticker: str
    commitment: str
    commitment_source: str


@dataclass(frozen=True, slots=True)
class BudgetCapacityProjection:
    """In-memory capacity facts with no authority beyond observation."""

    schema_version: str
    authority_effect: str
    budget_ceiling_source: BudgetCeilingSource
    portfolio_source_sha256: str
    portfolio_source_record_identity_sha256: str
    portfolio_source_date: str
    currency: str
    current_open_buy_commitments: tuple[CurrentOpenBuyCommitment, ...]
    total_current_unfilled_buy_commitment: str
    remaining_ceiling: str
    over_ceiling_amount: str


@dataclass(frozen=True, slots=True)
class BudgetCapacityObservationResult:
    authority_effect: str
    status: BudgetCapacityObservationStatus
    reason_codes: tuple[str, ...]
    projection: BudgetCapacityProjection | None


@dataclass(frozen=True, slots=True)
class _CapturedPortfolioSource:
    raw_bytes: bytes
    observed_sha256: str
    source_record_identity_sha256: str
    source_date: str


class _BudgetCeilingParseError(ValueError):
    pass


class _PortfolioSourceDateError(ValueError):
    pass


def _canonical_decimal_units(value: str) -> tuple[int, int]:
    """Exact (units, scale) decomposition of a canonical nonnegative decimal."""
    if "." in value:
        integral, fractional = value.split(".", 1)
    else:
        integral, fractional = value, ""
    return int(integral + fractional), len(fractional)


def _exact_nonnegative_difference(minuend: str, subtrahend: str) -> str:
    """Compute max(minuend - subtrahend, 0) exactly, independent of ambient
    ``decimal.getcontext().prec``.

    Both operands are already-canonical nonnegative decimal strings.  The
    difference is taken on integer coefficients at a shared scale, so no
    Decimal arithmetic operation (and therefore no ambient-context rounding)
    is ever performed on the operands themselves.
    """
    minuend_units, minuend_scale = _canonical_decimal_units(minuend)
    subtrahend_units, subtrahend_scale = _canonical_decimal_units(subtrahend)
    scale = max(minuend_scale, subtrahend_scale)
    minuend_units *= 10 ** (scale - minuend_scale)
    subtrahend_units *= 10 ** (scale - subtrahend_scale)
    difference_units = max(minuend_units - subtrahend_units, 0)
    digits = str(difference_units)
    if scale:
        digits = digits.rjust(scale + 1, "0")
        rendered = f"{digits[:-scale]}.{digits[-scale:]}"
    else:
        rendered = digits
    return normalize_decimal_string(rendered)


def _result(
    status: BudgetCapacityObservationStatus,
    reason_codes: tuple[str, ...],
    projection: BudgetCapacityProjection | None = None,
) -> BudgetCapacityObservationResult:
    return BudgetCapacityObservationResult(
        authority_effect=AUTHORITY_EFFECT_NONE,
        status=status,
        reason_codes=reason_codes,
        projection=projection,
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
        for descriptor in (current_fd, inputs_fd, root_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        raise
    finally:
        if inputs_fd is not None:
            try:
                os.close(inputs_fd)
            except OSError:
                pass


def _parse_budget_ceiling_source(raw_bytes: bytes) -> BudgetCeilingSource:
    if type(raw_bytes) is not bytes or raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise _BudgetCeilingParseError
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _BudgetCeilingParseError from None
    if "\x00" in text or "\r" in text or not text.endswith("\n"):
        raise _BudgetCeilingParseError
    lines = text.splitlines()
    if (
        len(lines) != 3
        or lines[0] != _BUDGET_CEILING_SCHEMA_LINE
        or lines[1] != _BUDGET_CEILING_CURRENCY_LINE
        or not lines[2].startswith(_BUDGET_CEILING_AMOUNT_PREFIX)
    ):
        raise _BudgetCeilingParseError
    amount_text = lines[2][len(_BUDGET_CEILING_AMOUNT_PREFIX) :]
    if len(amount_text) > 64 or _X_DECIMAL_RE.fullmatch(amount_text) is None:
        raise _BudgetCeilingParseError
    try:
        amount = Decimal(amount_text)
        normalized = normalize_decimal_string(amount)
    except (InvalidOperation, MmiCanonicalizationError):
        raise _BudgetCeilingParseError from None
    if not amount.is_finite() or amount < 0 or normalized != amount_text:
        raise _BudgetCeilingParseError
    return BudgetCeilingSource(
        repository_relative_locator=_BUDGET_CEILING_REPOSITORY_RELATIVE_LOCATOR,
        observed_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        observed_size_bytes=len(raw_bytes),
        currency="USD",
        maximum_total_unfilled_buy_commitment=amount_text,
    )


def _parse_portfolio_source_date(portfolio_text: str) -> str:
    """Bind the existing ``# updated`` source date as provenance only."""
    lines = portfolio_text.splitlines()
    first_section = next(
        (
            index
            for index, line in enumerate(lines)
            if _PORTFOLIO_SECTION_START_RE.match(line)
        ),
        len(lines),
    )
    headers = [
        line
        for line in lines[:first_section]
        if _PORTFOLIO_UPDATED_HEADER_CANDIDATE_RE.match(line)
    ]
    if len(headers) != 1:
        raise _PortfolioSourceDateError
    match = _PORTFOLIO_UPDATED_HEADER_RE.fullmatch(headers[0])
    if match is None:
        raise _PortfolioSourceDateError
    value = match.group("date")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise _PortfolioSourceDateError
    except ValueError:
        raise _PortfolioSourceDateError from None
    return value


def _read_current_budget_ceiling() -> tuple[
    BudgetCeilingSource | None,
    tuple[str, ...],
    bool,
]:
    """Stable-read the fixed optional X source without a caller path seam."""
    try:
        root_fd, current_fd = _open_current_inputs_directory(repo_root())
    except OSError:
        return None, ("BUDGET_CAPACITY_X_SOURCE_UNREADABLE",), False
    try:
        try:
            raw_bytes = stable_read_exact_bytes(
                current_fd,
                _BUDGET_CEILING_FILENAME,
                maximum_bytes=_BUDGET_CEILING_MAXIMUM_BYTES,
            )
        except MmiStableReadError as exc:
            if exc.os_error_errno in {errno.EACCES, errno.EPERM}:
                return None, ("BUDGET_CAPACITY_X_SOURCE_UNREADABLE",), False
            if exc.code is MmiStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE:
                return None, ("BUDGET_CAPACITY_X_SOURCE_UNAVAILABLE",), False
            try:
                os.stat(
                    _BUDGET_CEILING_FILENAME,
                    dir_fd=current_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return None, ("BUDGET_CAPACITY_X_SOURCE_ABSENT",), False
            except OSError:
                return None, ("BUDGET_CAPACITY_X_SOURCE_UNREADABLE",), False
            return None, ("BUDGET_CAPACITY_X_SOURCE_INVALID",), True
        except OSError:
            return None, ("BUDGET_CAPACITY_X_SOURCE_UNREADABLE",), False
    finally:
        os.close(current_fd)
        os.close(root_fd)
    try:
        return _parse_budget_ceiling_source(raw_bytes), (), False
    except _BudgetCeilingParseError:
        return None, ("BUDGET_CAPACITY_X_SOURCE_INVALID",), True


def _capture_current_portfolio_source(
    *,
    expected_sha256: str,
) -> tuple[_CapturedPortfolioSource | None, tuple[str, ...], bool]:
    result = capture_current_mmi_source(
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
        expected_source_sha256=expected_sha256,
    )
    reasons = tuple(result.reason_codes) or ("BUDGET_CAPACITY_PORTFOLIO_SOURCE_INVALID",)
    source = result.source
    if (
        result.valid
        and result.authority_effect == AUTHORITY_EFFECT_NONE
        and type(source) is MmiCapturedSource
        and source.role is MmiSourceRole.PORTFOLIO_SNAPSHOT
    ):
        record: Mapping[str, object] = source.source_record
        observed_sha256 = record.get("observed_sha256")
        source_record_identity = record.get("source_record_identity_sha256")
        try:
            source_date = _parse_portfolio_source_date(
                source.raw_bytes.decode("utf-8", errors="strict")
            )
        except (UnicodeDecodeError, _PortfolioSourceDateError):
            return None, ("BUDGET_CAPACITY_PORTFOLIO_SOURCE_INVALID",), True
        if (
            type(observed_sha256) is str
            and _SHA256_RE.fullmatch(observed_sha256)
            and type(source_record_identity) is str
            and _SHA256_RE.fullmatch(source_record_identity)
        ):
            return (
                _CapturedPortfolioSource(
                    raw_bytes=source.raw_bytes,
                    observed_sha256=observed_sha256,
                    source_record_identity_sha256=source_record_identity,
                    source_date=source_date,
                ),
                (),
                False,
            )
        return None, ("BUDGET_CAPACITY_PORTFOLIO_SOURCE_INVALID",), True
    return (
        None,
        reasons,
        not set(reasons).issubset(_PORTFOLIO_SOURCE_UNAVAILABLE_CODES),
    )


def observe_current_report_only_budget_capacity(
    *,
    portfolio_snapshot_expected_sha256: str,
) -> BudgetCapacityObservationResult:
    """Observe fixed X and complete current BUY commitments without authority.

    The expected portfolio digest is only an invocation-time exact-state pin.
    The returned portfolio provenance is the MMI source owner's actual observed
    digest and source-record identity.  This entry point accepts no X, path,
    cash, buying-power, order, or valuation input.
    """
    portfolio_source, portfolio_reasons, portfolio_invalid = (
        _capture_current_portfolio_source(
            expected_sha256=portfolio_snapshot_expected_sha256,
        )
    )
    if portfolio_source is None:
        return _result(
            (
                BudgetCapacityObservationStatus.INVALID
                if portfolio_invalid
                else BudgetCapacityObservationStatus.UNAVAILABLE
            ),
            portfolio_reasons,
        )

    try:
        commitments = resolve_strict_current_open_buy_commitments(
            portfolio_source.raw_bytes.decode("utf-8", errors="strict")
        )
    except UnicodeDecodeError:
        return _result(
            BudgetCapacityObservationStatus.INVALID,
            ("BUDGET_CAPACITY_PORTFOLIO_SOURCE_INVALID",),
        )
    except StrictCurrentOpenBuyCommitmentError as exc:
        return _result(
            (
                BudgetCapacityObservationStatus.UNAVAILABLE
                if exc.unavailable
                else BudgetCapacityObservationStatus.INVALID
            ),
            (exc.code,),
        )

    ceiling_source, ceiling_reasons, ceiling_invalid = _read_current_budget_ceiling()
    if ceiling_source is None:
        return _result(
            (
                BudgetCapacityObservationStatus.INVALID
                if ceiling_invalid
                else BudgetCapacityObservationStatus.UNAVAILABLE
            ),
            ceiling_reasons,
        )
    try:
        maximum = ceiling_source.maximum_total_unfilled_buy_commitment
        total = commitments.total_commitment
        remaining = _exact_nonnegative_difference(maximum, total)
        over_ceiling = _exact_nonnegative_difference(total, maximum)
        projection = BudgetCapacityProjection(
            schema_version="report_only_budget_capacity_projection_v1",
            authority_effect=AUTHORITY_EFFECT_NONE,
            budget_ceiling_source=ceiling_source,
            portfolio_source_sha256=portfolio_source.observed_sha256,
            portfolio_source_record_identity_sha256=(
                portfolio_source.source_record_identity_sha256
            ),
            portfolio_source_date=portfolio_source.source_date,
            currency="USD",
            current_open_buy_commitments=tuple(
                CurrentOpenBuyCommitment(
                    ticker=commitment.ticker,
                    commitment=commitment.commitment,
                    commitment_source=commitment.commitment_source,
                )
                for commitment in commitments.commitments
            ),
            total_current_unfilled_buy_commitment=(
                commitments.total_commitment
            ),
            remaining_ceiling=remaining,
            over_ceiling_amount=over_ceiling,
        )
    except (InvalidOperation, MmiCanonicalizationError):
        return _result(
            BudgetCapacityObservationStatus.INVALID,
            ("BUDGET_CAPACITY_ARITHMETIC_INVALID",),
        )
    return _result(
        BudgetCapacityObservationStatus.VALID_REPORT_ONLY,
        (),
        projection,
    )
