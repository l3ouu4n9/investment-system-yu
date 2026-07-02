from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from investment_orchestrator.research.actionable_handoff_candidate import (
    CANDIDATE_SCHEMA_VERSION,
)
from investment_orchestrator.research.actionable_promotion_pointer import (
    PERMISSION_EFFECT_PENDING_GATES,
    POINTER_SOURCE,
    PROMOTION_STATUS_PENDING_GATES,
    SCHEMA_VERSION as ACTIVE_POINTER_SCHEMA_VERSION,
)
from investment_orchestrator.research.promoted_handoff_verifier import (
    BLOCKER_EFFECTIVE_HANDOFF_ACTIONABLE_TICKER_MISMATCH,
    BLOCKER_EFFECTIVE_HANDOFF_EXTENDED_SLEEVE_ENABLED,
    BLOCKER_EFFECTIVE_HANDOFF_HASH_MISMATCH,
    BLOCKER_EFFECTIVE_HANDOFF_MISSING,
    BLOCKER_EFFECTIVE_HANDOFF_SCHEMA_INVALID,
    BLOCKER_EFFECTIVE_VALIDATION_FAILED,
    BLOCKER_EFFECTIVE_VALIDATION_MISSING,
    BLOCKER_NO_ACTIONABLE_ROWS,
    BLOCKER_POINTER_MALFORMED,
    BLOCKER_POINTER_MISSING,
    BLOCKER_POINTER_PERMISSION_MARKERS_INVALID,
    BLOCKER_POINTER_SCHEMA_INVALID,
    BLOCKER_PROMOTION_EXPIRED,
    FUTURE_PERMISSION_REQUIRED,
    SCHEMA_VERSION,
    VERIFICATION_BLOCKER_REASON_CODES,
    verify_promoted_handoff_for_step2_decision,
)


TODAY = date(2026, 6, 28)


def sha256_of(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def effective_handoff(*, ticker: str = "QQQ", schema_version: str = CANDIDATE_SCHEMA_VERSION) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "is_llm_generated": False,
        "report_only": True,
        "not_authorization": True,
        "trade_universe": {"allowed_buy_tickers": ["QQQ", "VOO", "SMH"]},
        "optional_extended_etf_sleeve": {"enabled": False},
        "buy_universe_scorecard": [
            {"ticker": ticker, "actionability_status": "actionable_this_run"},
            {"ticker": "VOO", "actionability_status": "ranking_hold_watch_only"},
        ],
        "strategy_a_research_handoff": {"positive_delta_research_supported": [ticker]},
    }


def pointer_for(
    effective: dict[str, Any],
    *,
    promotion_expires_at: str = "2026-07-31",
    tickers: list[str] | None = None,
    row_count: int = 1,
    consumed_by_step2: bool = False,
    consumed_by_gates: bool = False,
    permission_effect: str = PERMISSION_EFFECT_PENDING_GATES,
) -> dict[str, Any]:
    digest = sha256_of(effective)
    return {
        "schema_version": ACTIVE_POINTER_SCHEMA_VERSION,
        "source": POINTER_SOURCE,
        "promotion_status": PROMOTION_STATUS_PENDING_GATES,
        "permission_effect": permission_effect,
        "not_authorization": True,
        "future_pr_required": True,
        "consumed_by_availability": False,
        "consumed_by_step2": consumed_by_step2,
        "consumed_by_gates": consumed_by_gates,
        "candidate_actionable_row_count": row_count,
        "actionable_this_run_tickers": tickers if tickers is not None else ["QQQ"],
        "promotion_expires_at": promotion_expires_at,
        "effective_handoff_sha256": digest,
        "candidate_sha256": digest,
    }


def verify(
    *,
    pointer: Any = None,
    effective: Any = None,
    validation: Any = None,
) -> dict[str, Any]:
    effective = effective if effective is not None else effective_handoff()
    pointer = pointer if pointer is not None else pointer_for(effective)
    validation = validation if validation is not None else {"valid": True}
    return verify_promoted_handoff_for_step2_decision(
        active_pointer=pointer,
        effective_handoff=effective,
        effective_validation=validation,
        today=TODAY,
    )


def assert_blocked(result: dict[str, Any], reason: str) -> None:
    assert result["valid_for_step2_decision"] is False
    assert reason in result["verification_blockers"]
    assert any(
        check["reason_code"] == reason and check["passed"] is False for check in result["checks"]
    )


def test_valid_pointer_effective_validation_returns_valid() -> None:
    result = verify()

    assert result["schema_version"] == SCHEMA_VERSION
    assert result["is_llm_generated"] is False
    assert result["valid_for_step2_decision"] is True
    assert result["verification_blockers"] == []
    assert result["source"] == POINTER_SOURCE
    assert result["promotion_status"] == PROMOTION_STATUS_PENDING_GATES
    assert result["pointer_permission_effect"] == PERMISSION_EFFECT_PENDING_GATES
    assert result["permission_effect"] == "none"
    assert result["not_authorization"] is True
    assert result["candidate_actionable_row_count"] == 1
    assert result["actionable_this_run_tickers"] == ["QQQ"]
    assert result["promotion_expires_at"] == "2026-07-31"
    assert result["effective_handoff_sha256"] == result["pointer_effective_handoff_sha256"]
    assert result["effective_validation_valid"] is True
    assert result["consumed_by_step2"] is False
    assert result["future_permission_required"] == FUTURE_PERMISSION_REQUIRED
    assert result["checks"]


def test_missing_pointer_fails() -> None:
    result = verify_promoted_handoff_for_step2_decision(
        active_pointer=None,
        effective_handoff=effective_handoff(),
        effective_validation={"valid": True},
        today=TODAY,
    )
    assert_blocked(result, BLOCKER_POINTER_MISSING)


def test_malformed_pointer_fails() -> None:
    result = verify_promoted_handoff_for_step2_decision(
        active_pointer=["not", "mapping"],  # type: ignore[arg-type]
        effective_handoff=effective_handoff(),
        effective_validation={"valid": True},
        today=TODAY,
    )
    assert_blocked(result, BLOCKER_POINTER_MALFORMED)


def test_pointer_schema_invalid_fails() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective)
    pointer["schema_version"] = "unexpected"

    assert_blocked(verify(pointer=pointer, effective=effective), BLOCKER_POINTER_SCHEMA_INVALID)


def test_stale_promotion_fails() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective, promotion_expires_at="2026-06-27")

    assert_blocked(verify(pointer=pointer, effective=effective), BLOCKER_PROMOTION_EXPIRED)


def test_hash_mismatch_fails() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective)
    mutated = {**effective, "mutated": True}

    assert_blocked(verify(pointer=pointer, effective=mutated), BLOCKER_EFFECTIVE_HANDOFF_HASH_MISMATCH)


def test_validation_failed_fails() -> None:
    assert_blocked(verify(validation={"valid": False}), BLOCKER_EFFECTIVE_VALIDATION_FAILED)


def test_no_actionable_rows_fails() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective, tickers=[], row_count=0)

    assert_blocked(verify(pointer=pointer, effective=effective), BLOCKER_NO_ACTIONABLE_ROWS)


def test_consumed_by_step2_true_fails() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective, consumed_by_step2=True)

    assert_blocked(verify(pointer=pointer, effective=effective), BLOCKER_POINTER_PERMISSION_MARKERS_INVALID)


def test_consumed_by_gates_true_fails() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective, consumed_by_gates=True)

    assert_blocked(verify(pointer=pointer, effective=effective), BLOCKER_POINTER_PERMISSION_MARKERS_INVALID)


def test_permission_marker_mismatch_fails() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective, permission_effect="none")

    assert_blocked(verify(pointer=pointer, effective=effective), BLOCKER_POINTER_PERMISSION_MARKERS_INVALID)


def test_effective_handoff_schema_invalid_fails() -> None:
    effective = effective_handoff(schema_version="unexpected")
    pointer = pointer_for(effective)

    assert_blocked(verify(pointer=pointer, effective=effective), BLOCKER_EFFECTIVE_HANDOFF_SCHEMA_INVALID)


def test_extended_etf_sleeve_enabled_fails() -> None:
    effective = effective_handoff()
    effective["optional_extended_etf_sleeve"] = {"enabled": True}
    pointer = pointer_for(effective)

    assert_blocked(verify(pointer=pointer, effective=effective), BLOCKER_EFFECTIVE_HANDOFF_EXTENDED_SLEEVE_ENABLED)


def test_positive_delta_actionable_ticker_mismatch_fails() -> None:
    effective = effective_handoff()
    effective["strategy_a_research_handoff"]["positive_delta_research_supported"] = ["VOO"]
    pointer = pointer_for(effective)

    assert_blocked(verify(pointer=pointer, effective=effective), BLOCKER_EFFECTIVE_HANDOFF_ACTIONABLE_TICKER_MISMATCH)


def test_out_of_universe_actionable_ticker_fails_when_universe_available() -> None:
    effective = effective_handoff(ticker="TSLA")
    pointer = pointer_for(effective, tickers=["TSLA"])

    assert_blocked(verify(pointer=pointer, effective=effective), BLOCKER_EFFECTIVE_HANDOFF_ACTIONABLE_TICKER_MISMATCH)


def test_missing_effective_handoff_fails() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective)
    result = verify_promoted_handoff_for_step2_decision(
        active_pointer=pointer,
        effective_handoff=None,
        effective_validation={"valid": True},
        today=TODAY,
    )
    assert_blocked(result, BLOCKER_EFFECTIVE_HANDOFF_MISSING)


def test_missing_validation_fails() -> None:
    effective = effective_handoff()
    pointer = pointer_for(effective)
    result = verify_promoted_handoff_for_step2_decision(
        active_pointer=pointer,
        effective_handoff=effective,
        effective_validation=None,
        today=TODAY,
    )
    assert_blocked(result, BLOCKER_EFFECTIVE_VALIDATION_MISSING)


def test_function_never_raises_on_all_none_and_garbage_inputs() -> None:
    for pointer, effective, validation in (
        (None, None, None),
        ("bad", "bad", "bad"),
        ({"unjsonable": object()}, {"also": object()}, {"valid": object()}),
    ):
        result = verify_promoted_handoff_for_step2_decision(
            active_pointer=pointer,  # type: ignore[arg-type]
            effective_handoff=effective,  # type: ignore[arg-type]
            effective_validation=validation,  # type: ignore[arg-type]
            today=TODAY,
        )
        assert result["valid_for_step2_decision"] is False
        assert result["verification_blockers"]


def test_blocker_reason_codes_cover_expected_contract() -> None:
    expected = {
        BLOCKER_POINTER_MISSING,
        BLOCKER_POINTER_MALFORMED,
        BLOCKER_POINTER_SCHEMA_INVALID,
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
    }
    assert expected.issubset(set(VERIFICATION_BLOCKER_REASON_CODES))
