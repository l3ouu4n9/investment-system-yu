"""Fixed-source, report-only increment-capacity basis (r x H).

This module owns no allocation, target, order, eligibility, or permission
behavior.  It reads one fixed operator-owned fraction ``r`` from its fixed
path and multiplies it, exactly, by the existing trusted total holdings
exposure ``H`` already validated by the existing report-only holdings
exposure observer.  The result is in-memory only: a single report-only
sizing basis scalar, never a per-ticker increment, never combined with any
Budget ceiling fact, and never an actionable disposition.
"""

from __future__ import annotations

from dataclasses import dataclass
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
from investment_orchestrator.common.stable_read import (
    MmiStableReadError,
    MmiStableReadErrorCode,
    stable_read_exact_bytes,
)
from investment_orchestrator.observability import (
    report_only_holdings_exposure as _holdings_exposure,
)


__all__ = (
    "IncrementCapacityObservationResult",
    "IncrementCapacityObservationStatus",
    "IncrementCapacityProjection",
    "IncrementFractionSource",
    "observe_current_report_only_increment_capacity",
    "observe_report_only_increment_capacity_from_exposure",
)


_AUTHORITY_EFFECT_NONE: Final = "NONE"
_INCREMENT_FRACTION_FILENAME: Final = "increment_fraction.txt"
_INCREMENT_FRACTION_REPOSITORY_RELATIVE_LOCATOR: Final = (
    "inputs/current/increment_fraction.txt"
)
_INCREMENT_FRACTION_MAXIMUM_BYTES: Final = 4_096
_INCREMENT_FRACTION_SCHEMA_LINE: Final = (
    "schema_version = report_only_increment_fraction_v1"
)
_INCREMENT_FRACTION_BASIS_LINE: Final = "basis = total_holdings_exposure"
_INCREMENT_FRACTION_AMOUNT_PREFIX: Final = "increment_fraction = "
_R_DECIMAL_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


class IncrementCapacityObservationStatus(str, Enum):
    """Closed, non-authoritative status vocabulary.

    Mirrors the existing holdings-exposure owner's four-state vocabulary so
    that owner's ``MANUAL_REVIEW`` classification is preserved rather than
    collapsed into ``INVALID`` or ``UNAVAILABLE``.
    """

    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    VALID_REPORT_ONLY = "VALID_REPORT_ONLY"


@dataclass(frozen=True, slots=True)
class IncrementFractionSource:
    """One actual stable-read of the fixed operator-owned r source."""

    repository_relative_locator: str
    observed_sha256: str
    observed_size_bytes: int
    basis: str
    increment_fraction: str


@dataclass(frozen=True, slots=True)
class IncrementCapacityProjection:
    """In-memory r x H basis facts with no authority beyond observation."""

    schema_version: str
    authority_effect: str
    increment_fraction_source: IncrementFractionSource
    currency: str
    portfolio_source_sha256: str
    portfolio_source_record_identity_sha256: str
    portfolio_scope_id: str
    holdings_observation_date: str
    capture_artifact_sha256: str
    capture_session_date: str
    calendar_id: str | None
    calendar_schedule_sha256: str | None
    latest_completed_session_date: str | None
    freshness_status: str | None
    policy_projection_identity_sha256: str
    total_holdings_exposure: str
    increment_fraction: str
    increment_cap_basis: str


@dataclass(frozen=True, slots=True)
class IncrementCapacityObservationResult:
    authority_effect: str
    status: IncrementCapacityObservationStatus
    reason_codes: tuple[str, ...]
    projection: IncrementCapacityProjection | None


class _IncrementFractionParseError(ValueError):
    pass


def _canonical_decimal_units(value: str) -> tuple[int, int]:
    """Exact (units, scale) decomposition of a canonical nonnegative decimal."""
    if "." in value:
        integral, fractional = value.split(".", 1)
    else:
        integral, fractional = value, ""
    return int(integral + fractional), len(fractional)


def _exact_nonnegative_product(multiplier: str, multiplicand: str) -> str:
    """Compute ``multiplier * multiplicand`` exactly, independent of ambient
    ``decimal.getcontext().prec``.

    Both operands are already-canonical nonnegative decimal strings.  The
    product is taken on integer coefficients, so no Decimal arithmetic
    operation (and therefore no ambient-context rounding) is ever performed
    on the operands themselves.
    """
    multiplier_units, multiplier_scale = _canonical_decimal_units(multiplier)
    multiplicand_units, multiplicand_scale = _canonical_decimal_units(
        multiplicand
    )
    product_units = multiplier_units * multiplicand_units
    product_scale = multiplier_scale + multiplicand_scale
    digits = str(product_units)
    if product_scale:
        digits = digits.rjust(product_scale + 1, "0")
        rendered = f"{digits[:-product_scale]}.{digits[-product_scale:]}"
    else:
        rendered = digits
    return normalize_decimal_string(rendered)


def _result(
    status: IncrementCapacityObservationStatus,
    reason_codes: tuple[str, ...],
    projection: IncrementCapacityProjection | None = None,
) -> IncrementCapacityObservationResult:
    return IncrementCapacityObservationResult(
        authority_effect=_AUTHORITY_EFFECT_NONE,
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


def _parse_increment_fraction_source(
    raw_bytes: bytes,
) -> IncrementFractionSource:
    if type(raw_bytes) is not bytes or raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise _IncrementFractionParseError
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _IncrementFractionParseError from None
    if "\x00" in text or "\r" in text or not text.endswith("\n"):
        raise _IncrementFractionParseError
    lines = text.splitlines()
    if (
        len(lines) != 3
        or lines[0] != _INCREMENT_FRACTION_SCHEMA_LINE
        or lines[1] != _INCREMENT_FRACTION_BASIS_LINE
        or not lines[2].startswith(_INCREMENT_FRACTION_AMOUNT_PREFIX)
    ):
        raise _IncrementFractionParseError
    amount_text = lines[2][len(_INCREMENT_FRACTION_AMOUNT_PREFIX) :]
    if len(amount_text) > 64 or _R_DECIMAL_RE.fullmatch(amount_text) is None:
        raise _IncrementFractionParseError
    try:
        amount = Decimal(amount_text)
        normalized = normalize_decimal_string(amount)
    except (InvalidOperation, MmiCanonicalizationError):
        raise _IncrementFractionParseError from None
    if (
        not amount.is_finite()
        or normalized != amount_text
        or amount < 0
        or amount > 1
    ):
        raise _IncrementFractionParseError
    return IncrementFractionSource(
        repository_relative_locator=(
            _INCREMENT_FRACTION_REPOSITORY_RELATIVE_LOCATOR
        ),
        observed_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        observed_size_bytes=len(raw_bytes),
        basis="total_holdings_exposure",
        increment_fraction=amount_text,
    )


def _read_current_increment_fraction() -> tuple[
    IncrementFractionSource | None,
    tuple[str, ...],
    bool,
]:
    """Stable-read the fixed optional r source without a caller path seam."""
    try:
        root_fd, current_fd = _open_current_inputs_directory(repo_root())
    except OSError:
        return None, ("BUDGET_INCREMENT_R_SOURCE_UNREADABLE",), False
    try:
        try:
            raw_bytes = stable_read_exact_bytes(
                current_fd,
                _INCREMENT_FRACTION_FILENAME,
                maximum_bytes=_INCREMENT_FRACTION_MAXIMUM_BYTES,
            )
        except MmiStableReadError as exc:
            if exc.os_error_errno in {errno.EACCES, errno.EPERM}:
                return None, ("BUDGET_INCREMENT_R_SOURCE_UNREADABLE",), False
            if (
                exc.code
                is MmiStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE
            ):
                return None, ("BUDGET_INCREMENT_R_SOURCE_UNAVAILABLE",), False
            try:
                os.stat(
                    _INCREMENT_FRACTION_FILENAME,
                    dir_fd=current_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return None, ("BUDGET_INCREMENT_R_SOURCE_ABSENT",), False
            except OSError:
                return None, ("BUDGET_INCREMENT_R_SOURCE_UNREADABLE",), False
            return None, ("BUDGET_INCREMENT_R_SOURCE_INVALID",), True
        except OSError:
            return None, ("BUDGET_INCREMENT_R_SOURCE_UNREADABLE",), False
    finally:
        os.close(current_fd)
        os.close(root_fd)
    try:
        return _parse_increment_fraction_source(raw_bytes), (), False
    except _IncrementFractionParseError:
        return None, ("BUDGET_INCREMENT_R_SOURCE_INVALID",), True


_EXPOSURE_STATUS_MAP: Final = {
    _holdings_exposure.ExposureObservationStatus.UNAVAILABLE: (
        IncrementCapacityObservationStatus.UNAVAILABLE
    ),
    _holdings_exposure.ExposureObservationStatus.INVALID: (
        IncrementCapacityObservationStatus.INVALID
    ),
    _holdings_exposure.ExposureObservationStatus.MANUAL_REVIEW: (
        IncrementCapacityObservationStatus.MANUAL_REVIEW
    ),
}


def observe_current_report_only_increment_capacity(
    *,
    strategy_settings_expected_sha256: str,
    portfolio_snapshot_expected_sha256: str,
) -> IncrementCapacityObservationResult:
    """Observe fixed r and one exact r x H basis without authority.

    The expected strategy/portfolio digests are only invocation-time
    exact-state pins forwarded to the existing holdings-exposure observer.
    This entry point accepts no r, path, H, H_i, X, cash, buying-power, or
    market-data input.  It consumes the existing holdings-exposure owner's
    typed result only when that owner reports ``VALID_REPORT_ONLY``; a
    populated ``MANUAL_REVIEW`` projection from that owner is propagated as
    ``MANUAL_REVIEW`` and is never treated as a usable H.
    """
    r_source, r_reasons, r_invalid = _read_current_increment_fraction()
    if r_source is None:
        return _result(
            (
                IncrementCapacityObservationStatus.INVALID
                if r_invalid
                else IncrementCapacityObservationStatus.UNAVAILABLE
            ),
            r_reasons,
        )

    exposure_result = (
        _holdings_exposure.observe_current_report_only_holdings_exposure(
            strategy_settings_expected_sha256=strategy_settings_expected_sha256,
            portfolio_snapshot_expected_sha256=portfolio_snapshot_expected_sha256,
        )
    )
    return _project_increment_capacity_from_exposure(
        r_source=r_source,
        exposure_result=exposure_result,
    )


def observe_report_only_increment_capacity_from_exposure(
    *,
    exposure_result: _holdings_exposure.ExposureObservationResult,
) -> IncrementCapacityObservationResult:
    """Observe fixed r against one already-observed exact H generation."""
    r_source, r_reasons, r_invalid = _read_current_increment_fraction()
    if r_source is None:
        return _result(
            (
                IncrementCapacityObservationStatus.INVALID
                if r_invalid
                else IncrementCapacityObservationStatus.UNAVAILABLE
            ),
            r_reasons,
        )
    return _project_increment_capacity_from_exposure(
        r_source=r_source,
        exposure_result=exposure_result,
    )


def _project_increment_capacity_from_exposure(
    *,
    r_source: IncrementFractionSource,
    exposure_result: _holdings_exposure.ExposureObservationResult,
) -> IncrementCapacityObservationResult:
    if (
        exposure_result.status
        is not _holdings_exposure.ExposureObservationStatus.VALID_REPORT_ONLY
    ):
        return _result(
            _EXPOSURE_STATUS_MAP[exposure_result.status],
            tuple(exposure_result.reason_codes),
        )
    exposure = exposure_result.projection
    if exposure is None:
        return _result(
            IncrementCapacityObservationStatus.INVALID,
            ("BUDGET_INCREMENT_H_CONTRACT_INVALID",),
        )

    try:
        increment_cap_basis = _exact_nonnegative_product(
            r_source.increment_fraction,
            exposure.total_market_value,
        )
    except MmiCanonicalizationError:
        return _result(
            IncrementCapacityObservationStatus.INVALID,
            ("BUDGET_INCREMENT_ARITHMETIC_INVALID",),
        )

    projection = IncrementCapacityProjection(
        schema_version="report_only_increment_capacity_projection_v1",
        authority_effect=_AUTHORITY_EFFECT_NONE,
        increment_fraction_source=r_source,
        currency=exposure.currency,
        portfolio_source_sha256=exposure.portfolio_source_sha256,
        portfolio_source_record_identity_sha256=(
            exposure.portfolio_source_record_identity_sha256
        ),
        portfolio_scope_id=exposure.portfolio_scope_id,
        holdings_observation_date=exposure.holdings_observation_date,
        capture_artifact_sha256=exposure.capture_artifact_sha256,
        capture_session_date=exposure.capture_session_date,
        calendar_id=exposure.calendar_id,
        calendar_schedule_sha256=exposure.calendar_schedule_sha256,
        latest_completed_session_date=exposure.latest_completed_session_date,
        freshness_status=exposure.freshness_status,
        policy_projection_identity_sha256=(
            exposure.policy_projection_identity_sha256
        ),
        total_holdings_exposure=exposure.total_market_value,
        increment_fraction=r_source.increment_fraction,
        increment_cap_basis=increment_cap_basis,
    )
    return _result(
        IncrementCapacityObservationStatus.VALID_REPORT_ONLY,
        (),
        projection,
    )
