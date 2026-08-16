"""V1-P7A IMMUTABLE HOLD/NO_TRADE PUBLICATION ONLY.

This module provides one narrow, durable, write-once artifact publication
boundary for deterministic terminal HOLD / NO_TRADE outcomes evaluated by P6.

It explicitly:
* Rejects any positive order-compiler candidate.
* Encapsulates a fresh P6 invocation to guarantee non-transferability.
* Creates an immutable content-addressed durable record.
* Does not modify any state, permission, or current/latest pointer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Final

from investment_orchestrator.common.io import atomic_write_text, ensure_dir, file_exists, read_text
from investment_orchestrator.common.paths import artifacts_dir
from investment_orchestrator.mmi.canonical import canonical_json_bytes
from investment_orchestrator.mmi.contracts import AUTHORITY_EFFECT_NONE
from investment_orchestrator.workflow.h1_v1_postcompile_final_safety import (
    POSTCOMPILE_HOLD,
    POSTCOMPILE_NO_TRADE,
    evaluate_h1_v1_postcompile_final_safety,
)

SCHEMA_NAME: Final = "v1_hold_no_trade_publication"
SCHEMA_VERSION: Final = "1.0"
PUBLICATION_KIND: Final = "V1_HOLD_NO_TRADE_PUBLICATION"
_ALLOWED_TERMINAL: Final = (POSTCOMPILE_HOLD, POSTCOMPILE_NO_TRADE)

_ALLOWED_HOLD_REASONS: Final = frozenset({
    "NO_INCREMENT_ELIGIBLE_TICKER",
    "NO_SHARED_CAPACITY",
    "SELECTED_TARGET_NOT_POSITIVE",
    "NO_WHOLE_SHARE_FEASIBILITY",
})

_ALLOWED_NO_TRADE_REASONS: Final = frozenset({
    "EXISTING_COMMITMENT_EXCEEDS_R",
    "EXISTING_COMMITMENT_EXCEEDS_X",
    "H1_CONTEXT_NOT_CURRENT",
    "INITIAL_ALPHA_EXCEEDS_CORE",
    "INPUT_GENERATION_MISMATCH",
    "INPUT_OWNER_NOT_VALID",
    "INPUT_SOURCE_CONTRACT_NOT_VALID",
    "REQUIRED_EXPOSURE_ROLE_UNRESOLVED",
    "US_EQUITY_SESSION_CALENDAR_COVERAGE_INSUFFICIENT",
    "US_EQUITY_SESSION_EVALUATION_TIMESTAMP_INVALID",
    "US_EQUITY_SESSION_MARK_DATE_AFTER_EVALUATION",
    "US_EQUITY_SESSION_MARK_DATE_NON_SESSION",
    "US_EQUITY_SESSION_MARK_DATE_OUTSIDE_CALENDAR_COVERAGE",
    "US_EQUITY_SESSION_MARK_DATE_STALE",
    "US_EQUITY_SESSION_MARK_DATE_UNCOMPLETED",
    "US_EQUITY_SESSION_RUN_CONTEXT_INVALID",
    "US_EQUITY_SESSION_RUN_CONTEXT_OR_MARK_DATE_INVALID",
    "V1_BUY_DRY_RUN_PORTFOLIO_GENERATION_MISMATCH",
    "V1_BUY_DRY_RUN_PROPOSAL_GENERATION_INCOMPLETE",
    "V1_BUY_DRY_RUN_STRATEGY_GENERATION_MISMATCH",
    "V1_BUY_DRY_RUN_VALUATION_GENERATION_MISMATCH",
    "V1_BUY_DRY_RUN_VALUATION_NOT_CURRENT",
    "V1_POSTCOMPILE_INPUT_GENERATION_MISMATCH",
    "V1_POSTCOMPILE_VALUATION_GENERATION_MISMATCH",
    "V1_POSTCOMPILE_VALUATION_NOT_CURRENT",
})

_EXPECTED_STRING_BINDINGS: Final = frozenset({
    "calendar_id",
    "calendar_schedule_sha256",
    "h1_raw_response_sha256",
    "h1_rendered_prompt_sha256",
    "holdings_observation_date",
    "holdings_policy_projection_identity_sha256",
    "latest_completed_session_date",
    "portfolio_scope_id",
    "portfolio_source_record_identity_sha256",
    "portfolio_source_sha256",
    "r_source_sha256",
    "role_universe_projection_identity_sha256",
    "strategy_source_record_identity_sha256",
    "strategy_source_sha256",
    "valuation_capture_sha256",
    "valuation_freshness_status",
    "valuation_provider_id",
    "valuation_session_date",
    "valuation_source_kind",
    "valuation_trusted_evaluation_timestamp_utc",
    "x_source_sha256",
})

_EXPECTED_LIST_BINDINGS: Final = frozenset({
    "h1_evidence_entry_identities_sha256",
    "h1_report_evidence_references",
})


class V1P7APublicationError(RuntimeError):
    """P7A publication failed (e.g. subject mismatch, validator failure, existing mismatch)."""
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class V1HoldNoTradePublicationResult:
    """Operational facts returned by the immutable publication."""
    terminal_outcome: str
    artifact_identity_sha256: str
    immutable_path: Path
    existed_idempotently: bool


def _artifact_dir() -> Path:
    """Return the isolated P7A namespace."""
    return ensure_dir(artifacts_dir() / "v1_hold_no_trade_publication")


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Require exact closed schema, no order abstractions."""
    expected_keys = {
        "schema_name",
        "schema_version",
        "publication_kind",
        "terminal_outcome",
        "reason_code",
        "source_bindings",
        "authority_effect",
        "not_authorization",
    }
    if set(payload.keys()) != expected_keys:
        raise V1P7APublicationError("V1_P7A_VALIDATION_SCHEMA_KEYS_INVALID")

    if payload["schema_name"] != SCHEMA_NAME:
        raise V1P7APublicationError("V1_P7A_VALIDATION_SCHEMA_NAME_INVALID")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise V1P7APublicationError("V1_P7A_VALIDATION_SCHEMA_VERSION_INVALID")
    if payload["publication_kind"] != PUBLICATION_KIND:
        raise V1P7APublicationError("V1_P7A_VALIDATION_PUBLICATION_KIND_INVALID")
    if payload["terminal_outcome"] not in _ALLOWED_TERMINAL:
        raise V1P7APublicationError("V1_P7A_VALIDATION_TERMINAL_INVALID")
        
    reason_code = payload["reason_code"]
    if payload["terminal_outcome"] == POSTCOMPILE_HOLD:
        if reason_code not in _ALLOWED_HOLD_REASONS:
            raise V1P7APublicationError("V1_P7A_VALIDATION_REASON_CODE_INVALID")
    else:
        if reason_code not in _ALLOWED_NO_TRADE_REASONS:
            raise V1P7APublicationError("V1_P7A_VALIDATION_REASON_CODE_INVALID")
            
    if payload["authority_effect"] != AUTHORITY_EFFECT_NONE:
        raise V1P7APublicationError("V1_P7A_VALIDATION_AUTHORITY_POSTURE_INVALID")
    if payload["not_authorization"] is not True:
        raise V1P7APublicationError("V1_P7A_VALIDATION_AUTHORITY_POSTURE_INVALID")

    bindings = payload["source_bindings"]
    if type(bindings) is not dict:
        raise V1P7APublicationError("V1_P7A_VALIDATION_PROVENANCE_INVALID")
    if set(bindings.keys()) != (_EXPECTED_STRING_BINDINGS | _EXPECTED_LIST_BINDINGS):
        raise V1P7APublicationError("V1_P7A_VALIDATION_PROVENANCE_INVALID")

    for k in _EXPECTED_STRING_BINDINGS:
        v = bindings[k]
        if v is not None and type(v) is not str:
            raise V1P7APublicationError("V1_P7A_VALIDATION_PROVENANCE_INVALID")
            
    for k in _EXPECTED_LIST_BINDINGS:
        v = bindings[k]
        if v is not None:
            if type(v) not in (list, tuple):
                raise V1P7APublicationError("V1_P7A_VALIDATION_PROVENANCE_INVALID")
            if any(type(item) is not str for item in v):
                raise V1P7APublicationError("V1_P7A_VALIDATION_PROVENANCE_INVALID")

    return payload


def publish_h1_v1_hold_no_trade() -> V1HoldNoTradePublicationResult:
    """Freshly evaluate P6 and Durably Publish HOLD or NO_TRADE.

    Rejects POSTCOMPILE_CANDIDATE_VALID. Creates an immutable write-once
    artifact matching the canonical identity of the bound generation.
    Does not update any current or latest pointers.
    """
    # 1. Evaluate fresh P6 invocation purely in-memory
    p6_result = evaluate_h1_v1_postcompile_final_safety()

    # 2. Reject positive candidate (publication subject mismatch)
    if p6_result.terminal_outcome not in _ALLOWED_TERMINAL:
        raise V1P7APublicationError("V1_P7A_PUBLICATION_SUBJECT_MISMATCH")

    # 3. Construct minimal complete exact package
    source_bindings_dict = {k: v for k, v in p6_result.source_bindings}
    payload = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "publication_kind": PUBLICATION_KIND,
        "terminal_outcome": p6_result.terminal_outcome,
        "reason_code": p6_result.reason_code,
        "source_bindings": source_bindings_dict,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "not_authorization": True,
    }

    # 4. Strict round-trip validation in memory
    _validate_payload(payload)

    # 5. Canonicalize and Hash
    canonical_bytes = canonical_json_bytes(payload)
    artifact_identity = hashlib.sha256(canonical_bytes).hexdigest()

    # 6. Content-addressed Path
    target_path = _artifact_dir() / f"{artifact_identity}.json"

    # 7. Write-once idempotency logic
    existed = False
    if file_exists(target_path):
        try:
            actual_bytes = target_path.read_bytes()
        except OSError:
            raise V1P7APublicationError("V1_P7A_EXISTING_READ_FAILURE")
            
        if actual_bytes == canonical_bytes:
            existed = True
        else:
            raise V1P7APublicationError("V1_P7A_EXISTING_IDENTITY_MISMATCH")
    else:
        # 8. Atomic Write
        # atomic_write_text relies on os.replace, ensuring atomic durability on disk
        # without exposing partial writes.
        try:
            atomic_write_text(target_path, canonical_bytes.decode("utf-8"))
        except OSError:
            raise V1P7APublicationError("V1_P7A_ATOMIC_WRITE_FAILED")

        # 9. Verify on disk
        try:
            actual_bytes = target_path.read_bytes()
        except OSError:
            raise V1P7APublicationError("V1_P7A_EXISTING_READ_FAILURE")
            
        if actual_bytes != canonical_bytes:
            raise V1P7APublicationError("V1_P7A_EXISTING_IDENTITY_MISMATCH")

    # 10. Operational Return
    return V1HoldNoTradePublicationResult(
        terminal_outcome=p6_result.terminal_outcome,
        artifact_identity_sha256=artifact_identity,
        immutable_path=target_path,
        existed_idempotently=existed,
    )
