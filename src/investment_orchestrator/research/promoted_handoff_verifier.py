"""Fail-closed verifier for promoted handoff pointer + effective handoff.

R2E.5b-6a: this module is a pure, deterministic helper for future gates. It
does not change any live permission, gate, workflow, prompt, or order path.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from typing import Any

from investment_orchestrator.research.actionable_handoff_candidate import (
    CANDIDATE_SCHEMA_VERSION as ACTIONABLE_CANDIDATE_SCHEMA_VERSION,
)
from investment_orchestrator.research.actionable_promotion_pointer import (
    PERMISSION_EFFECT_PENDING_GATES,
    POINTER_SOURCE,
    PROMOTION_STATUS_PENDING_GATES,
    SCHEMA_VERSION as ACTIVE_POINTER_SCHEMA_VERSION,
)


SCHEMA_VERSION = "promoted_handoff_step2_verification_v1"
FUTURE_PERMISSION_REQUIRED = "PROMOTED_RESEARCH_DECISION"
CURRENT_PERMISSION_EFFECT = "none"

SEVERITY_BLOCKER = "blocker"
SEVERITY_WARNING = "warning"

BLOCKER_POINTER_MISSING = "pointer_missing"
BLOCKER_POINTER_MALFORMED = "pointer_malformed"
BLOCKER_POINTER_SCHEMA_INVALID = "pointer_schema_invalid"
BLOCKER_POINTER_SOURCE_INVALID = "pointer_source_invalid"
BLOCKER_POINTER_STATUS_INVALID = "pointer_status_invalid"
BLOCKER_POINTER_PERMISSION_MARKERS_INVALID = "pointer_permission_markers_invalid"
BLOCKER_PROMOTION_EXPIRED = "promotion_expired"
BLOCKER_NO_ACTIONABLE_ROWS = "no_actionable_rows"
BLOCKER_EFFECTIVE_HANDOFF_MISSING = "effective_handoff_missing"
BLOCKER_EFFECTIVE_HANDOFF_HASH_MISMATCH = "effective_handoff_hash_mismatch"
BLOCKER_EFFECTIVE_HANDOFF_SCHEMA_INVALID = "effective_handoff_schema_invalid"
BLOCKER_EFFECTIVE_HANDOFF_ACTIONABLE_TICKER_MISMATCH = (
    "effective_handoff_actionable_ticker_mismatch"
)
BLOCKER_EFFECTIVE_HANDOFF_EXTENDED_SLEEVE_ENABLED = "effective_handoff_extended_sleeve_enabled"
BLOCKER_EFFECTIVE_VALIDATION_MISSING = "effective_validation_missing"
BLOCKER_EFFECTIVE_VALIDATION_FAILED = "effective_validation_failed"

VERIFICATION_BLOCKER_REASON_CODES = (
    BLOCKER_POINTER_MISSING,
    BLOCKER_POINTER_MALFORMED,
    BLOCKER_POINTER_SCHEMA_INVALID,
    BLOCKER_POINTER_SOURCE_INVALID,
    BLOCKER_POINTER_STATUS_INVALID,
    BLOCKER_POINTER_PERMISSION_MARKERS_INVALID,
    BLOCKER_PROMOTION_EXPIRED,
    BLOCKER_NO_ACTIONABLE_ROWS,
    BLOCKER_EFFECTIVE_HANDOFF_MISSING,
    BLOCKER_EFFECTIVE_HANDOFF_HASH_MISMATCH,
    BLOCKER_EFFECTIVE_HANDOFF_SCHEMA_INVALID,
    BLOCKER_EFFECTIVE_HANDOFF_ACTIONABLE_TICKER_MISMATCH,
    BLOCKER_EFFECTIVE_HANDOFF_EXTENDED_SLEEVE_ENABLED,
    BLOCKER_EFFECTIVE_VALIDATION_MISSING,
    BLOCKER_EFFECTIVE_VALIDATION_FAILED,
)


def verify_promoted_handoff_for_step2_decision(
    *,
    active_pointer: Mapping[str, Any] | None,
    effective_handoff: Mapping[str, Any] | None,
    effective_validation: Mapping[str, Any] | None,
    today: date | None = None,
) -> dict[str, Any]:
    """Verify promoted handoff artifacts for future Step 2 decision-only use.

    Pure and fail-closed: returns a deterministic result and never raises.
    """
    try:
        return _verify(
            active_pointer=active_pointer,
            effective_handoff=effective_handoff,
            effective_validation=effective_validation,
            today=today,
        )
    except Exception as exc:  # noqa: BLE001 - verifier must never raise
        return _result(
            valid=False,
            blockers=[BLOCKER_POINTER_MALFORMED],
            warnings=[],
            checks=[
                _check(
                    "verifier_never_raise_fallback",
                    False,
                    BLOCKER_POINTER_MALFORMED,
                    details={"error": str(exc)},
                )
            ],
            pointer=None,
            effective_handoff=None,
            effective_validation=None,
            effective_hash=None,
        )


def _verify(
    *,
    active_pointer: Mapping[str, Any] | None,
    effective_handoff: Mapping[str, Any] | None,
    effective_validation: Mapping[str, Any] | None,
    today: date | None,
) -> dict[str, Any]:
    pointer = active_pointer if isinstance(active_pointer, Mapping) else None
    effective = effective_handoff if isinstance(effective_handoff, Mapping) else None
    validation = effective_validation if isinstance(effective_validation, Mapping) else None
    today_value = today if isinstance(today, date) else date.today()

    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []

    def add_check(
        check_id: str,
        passed: bool,
        reason_code: str | None = None,
        *,
        severity: str = SEVERITY_BLOCKER,
        **details: Any,
    ) -> None:
        checks.append(_check(check_id, passed, reason_code, severity=severity, details=details))
        if not passed and reason_code is not None:
            target = blockers if severity == SEVERITY_BLOCKER else warnings
            if reason_code not in target:
                target.append(reason_code)

    add_check("pointer_present", active_pointer is not None, BLOCKER_POINTER_MISSING)
    add_check("pointer_is_mapping", pointer is not None, BLOCKER_POINTER_MALFORMED)

    promotion_status = _str_or_none(pointer.get("promotion_status")) if pointer else None
    pointer_permission_effect = _str_or_none(pointer.get("permission_effect")) if pointer else None
    expires_at = _str_or_none(pointer.get("promotion_expires_at")) if pointer else None
    row_count = _positive_int_or_none(pointer.get("candidate_actionable_row_count")) if pointer else None
    pointer_tickers = _string_items(pointer.get("actionable_this_run_tickers")) if pointer else []
    pointer_effective_hash = _str_or_none(pointer.get("effective_handoff_sha256")) if pointer else None

    if pointer is not None:
        add_check(
            "pointer_schema_expected",
            pointer.get("schema_version") == ACTIVE_POINTER_SCHEMA_VERSION,
            BLOCKER_POINTER_SCHEMA_INVALID,
            expected_schema=ACTIVE_POINTER_SCHEMA_VERSION,
            actual_schema=pointer.get("schema_version"),
        )
        add_check(
            "pointer_source_promoted",
            pointer.get("source") == POINTER_SOURCE,
            BLOCKER_POINTER_SOURCE_INVALID,
            expected_source=POINTER_SOURCE,
            actual_source=pointer.get("source"),
        )
        add_check(
            "pointer_status_pending_gates",
            promotion_status == PROMOTION_STATUS_PENDING_GATES,
            BLOCKER_POINTER_STATUS_INVALID,
            expected_status=PROMOTION_STATUS_PENDING_GATES,
            actual_status=promotion_status,
        )
        markers_ok = (
            pointer.get("not_authorization") is True
            and pointer.get("future_pr_required") is True
            and pointer.get("consumed_by_availability") is False
            and pointer.get("consumed_by_step2") is False
            and pointer.get("consumed_by_gates") is False
            and pointer_permission_effect == PERMISSION_EFFECT_PENDING_GATES
        )
        add_check(
            "pointer_permission_markers_safe",
            markers_ok,
            BLOCKER_POINTER_PERMISSION_MARKERS_INVALID,
            not_authorization=pointer.get("not_authorization"),
            future_pr_required=pointer.get("future_pr_required"),
            consumed_by_availability=pointer.get("consumed_by_availability"),
            consumed_by_step2=pointer.get("consumed_by_step2"),
            consumed_by_gates=pointer.get("consumed_by_gates"),
            permission_effect=pointer_permission_effect,
            expected_permission_effect=PERMISSION_EFFECT_PENDING_GATES,
        )
        add_check(
            "promotion_not_expired",
            _promotion_not_expired(expires_at, today_value),
            BLOCKER_PROMOTION_EXPIRED,
            promotion_expires_at=expires_at,
            today=today_value.isoformat(),
        )
        add_check(
            "pointer_actionable_rows_present",
            row_count is not None and row_count > 0 and bool(pointer_tickers),
            BLOCKER_NO_ACTIONABLE_ROWS,
            candidate_actionable_row_count=row_count,
            actionable_this_run_tickers=pointer_tickers,
        )

    effective_hash = _sha256_of(effective) if effective is not None else None
    add_check(
        "effective_handoff_present",
        effective_handoff is not None and effective is not None,
        BLOCKER_EFFECTIVE_HANDOFF_MISSING,
    )
    if effective is not None:
        add_check(
            "effective_handoff_hash_matches_pointer",
            effective_hash is not None
            and pointer_effective_hash is not None
            and effective_hash == pointer_effective_hash,
            BLOCKER_EFFECTIVE_HANDOFF_HASH_MISMATCH,
            effective_handoff_sha256=effective_hash,
            pointer_effective_handoff_sha256=pointer_effective_hash,
        )
        add_check(
            "effective_handoff_schema_expected",
            effective.get("schema_version") == ACTIONABLE_CANDIDATE_SCHEMA_VERSION,
            BLOCKER_EFFECTIVE_HANDOFF_SCHEMA_INVALID,
            expected_schema=ACTIONABLE_CANDIDATE_SCHEMA_VERSION,
            actual_schema=effective.get("schema_version"),
        )
        effective_tickers = _effective_actionable_tickers(effective)
        positive_delta = _positive_delta_tickers(effective)
        allowed_buy = _allowed_buy_tickers(effective)
        ticker_match = (
            bool(pointer_tickers)
            and effective_tickers == pointer_tickers
            and positive_delta == pointer_tickers
            and (not allowed_buy or set(pointer_tickers).issubset(set(allowed_buy)))
        )
        add_check(
            "effective_handoff_actionable_tickers_match_pointer",
            ticker_match,
            BLOCKER_EFFECTIVE_HANDOFF_ACTIONABLE_TICKER_MISMATCH,
            pointer_actionable_this_run_tickers=pointer_tickers,
            effective_actionable_tickers=effective_tickers,
            positive_delta_research_supported=positive_delta,
            allowed_buy_tickers=allowed_buy,
        )
        add_check(
            "effective_handoff_extended_sleeve_disabled",
            not _extended_sleeve_enabled(effective),
            BLOCKER_EFFECTIVE_HANDOFF_EXTENDED_SLEEVE_ENABLED,
            optional_extended_etf_sleeve=effective.get("optional_extended_etf_sleeve"),
        )

    add_check(
        "effective_validation_present",
        effective_validation is not None and validation is not None,
        BLOCKER_EFFECTIVE_VALIDATION_MISSING,
    )
    if validation is not None:
        validation_valid = validation.get("valid") is True or validation.get("validation_passed") is True
        validation_hash_ok = _validation_hash_matches(validation, effective_hash)
        add_check(
            "effective_validation_passed",
            validation_valid and validation_hash_ok,
            BLOCKER_EFFECTIVE_VALIDATION_FAILED,
            valid=validation.get("valid"),
            validation_passed=validation.get("validation_passed"),
            effective_handoff_sha256=effective_hash,
        )

    return _result(
        valid=not blockers,
        blockers=blockers,
        warnings=warnings,
        checks=checks,
        pointer=pointer,
        effective_handoff=effective,
        effective_validation=validation,
        effective_hash=effective_hash,
    )


def _result(
    *,
    valid: bool,
    blockers: list[str],
    warnings: list[str],
    checks: list[dict[str, Any]],
    pointer: Mapping[str, Any] | None,
    effective_handoff: Mapping[str, Any] | None,
    effective_validation: Mapping[str, Any] | None,
    effective_hash: str | None,
) -> dict[str, Any]:
    pointer_effective_hash = (
        _str_or_none(pointer.get("effective_handoff_sha256")) if pointer is not None else None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "valid_for_step2_decision": bool(valid),
        "verification_blockers": list(blockers),
        "verification_warnings": list(warnings),
        "checks": list(checks),
        "source": POINTER_SOURCE,
        "promotion_status": _str_or_none(pointer.get("promotion_status")) if pointer else None,
        "pointer_permission_effect": (
            _str_or_none(pointer.get("permission_effect")) if pointer else None
        ),
        "permission_effect": CURRENT_PERMISSION_EFFECT,
        "not_authorization": pointer.get("not_authorization") if pointer else None,
        "candidate_actionable_row_count": (
            _positive_int_or_none(pointer.get("candidate_actionable_row_count")) if pointer else None
        ),
        "actionable_this_run_tickers": (
            _string_items(pointer.get("actionable_this_run_tickers")) if pointer else []
        ),
        "promotion_expires_at": _str_or_none(pointer.get("promotion_expires_at")) if pointer else None,
        "effective_handoff_sha256": effective_hash,
        "pointer_effective_handoff_sha256": pointer_effective_hash,
        "effective_validation_valid": (
            (effective_validation.get("valid") is True or effective_validation.get("validation_passed") is True)
            if isinstance(effective_validation, Mapping)
            else False
        ),
        "consumed_by_step2": pointer.get("consumed_by_step2") if pointer else None,
        "future_permission_required": FUTURE_PERMISSION_REQUIRED,
        "report_only": True,
    }


def _check(
    check_id: str,
    passed: bool,
    reason_code: str | None,
    *,
    severity: str = SEVERITY_BLOCKER,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "passed": bool(passed),
        "severity": severity,
        "reason_code": reason_code,
        "details": dict(details or {}),
    }


def _promotion_not_expired(expires_at: Any, today_value: date) -> bool:
    expires = _parse_date(expires_at)
    return expires is not None and expires >= today_value


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validation_hash_matches(validation: Mapping[str, Any], effective_hash: str | None) -> bool:
    if effective_hash is None:
        return False
    hash_fields = (
        "candidate_sha256",
        "effective_handoff_sha256",
        "handoff_sha256",
        "source_sha256",
    )
    present = [
        validation.get(field)
        for field in hash_fields
        if isinstance(validation.get(field), str) and validation.get(field)
    ]
    return all(value == effective_hash for value in present)


def _effective_actionable_tickers(handoff: Mapping[str, Any]) -> list[str]:
    scorecard = handoff.get("buy_universe_scorecard")
    if not isinstance(scorecard, list):
        return []
    tickers: list[str] = []
    for row in scorecard:
        if not isinstance(row, Mapping):
            continue
        if row.get("actionability_status") != "actionable_this_run":
            continue
        ticker = _str_or_none(row.get("ticker"))
        if ticker and ticker.strip():
            tickers.append(ticker.strip())
    return tickers


def _positive_delta_tickers(handoff: Mapping[str, Any]) -> list[str]:
    handoff_body = handoff.get("strategy_a_research_handoff")
    if not isinstance(handoff_body, Mapping):
        return []
    return _string_items(handoff_body.get("positive_delta_research_supported"))


def _allowed_buy_tickers(handoff: Mapping[str, Any]) -> list[str]:
    trade_universe = handoff.get("trade_universe")
    if not isinstance(trade_universe, Mapping):
        return []
    return _string_items(trade_universe.get("allowed_buy_tickers"))


def _extended_sleeve_enabled(handoff: Mapping[str, Any]) -> bool:
    sleeve = handoff.get("optional_extended_etf_sleeve")
    if not isinstance(sleeve, Mapping):
        return False
    return sleeve.get("enabled") is True


def _positive_int_or_none(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
