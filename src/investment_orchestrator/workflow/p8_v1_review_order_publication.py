"""V1-P8A IMMUTABLE REVIEW-ONLY BUY ARTIFACT.

This module provides one narrow, durable, write-once artifact publication
boundary for deterministic POSTCOMPILE_CANDIDATE_VALID outcomes evaluated by P6.

It explicitly:
* Rejects HOLD and NO_TRADE.
* Encapsulates a fresh P6 invocation to guarantee non-transferability.
* Creates an immutable content-addressed durable record.
* Retains exact deterministic upstream sizing without recalculation.
* Grants ZERO execution authority (purely for human review).
* Does not modify any state, permission, or current/latest pointer.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Final

from investment_orchestrator.common.io import atomic_write_text, ensure_dir, file_exists
from investment_orchestrator.common.paths import artifacts_dir
from investment_orchestrator.mmi.canonical import canonical_json_bytes
from investment_orchestrator.mmi.contracts import AUTHORITY_EFFECT_NONE
from investment_orchestrator.workflow.h1_v1_postcompile_final_safety import (
    POSTCOMPILE_CANDIDATE_VALID,
    evaluate_h1_v1_postcompile_final_safety,
)

SCHEMA_NAME: Final = "v1_review_order_publication"
SCHEMA_VERSION: Final = "1.0"
PUBLICATION_KIND: Final = "V1_REVIEW_ORDER"
ACTION_BUY: Final = "BUY"

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


class V1P8APublicationError(RuntimeError):
    """P8A publication failed (e.g. subject mismatch, validator failure, existing mismatch)."""
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class V1ReviewOrderPublicationResult:
    """Operational facts returned by the immutable publication."""
    terminal_outcome: str
    artifact_identity_sha256: str
    immutable_path: Path
    existed_idempotently: bool
    selected_ticker: str
    total_candidate_notional: str


def _artifact_dir() -> Path:
    """Return the isolated P8A namespace."""
    return ensure_dir(artifacts_dir() / "v1_review_orders")


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Require exact closed schema, no order abstractions."""
    expected_keys = {
        "schema_name",
        "schema_version",
        "publication_kind",
        "ticker",
        "action",
        "target_notional",
        "total_candidate_notional",
        "legs",
        "source_bindings",
        "authority_effect",
        "not_authorization",
    }
    if set(payload.keys()) != expected_keys:
        raise V1P8APublicationError("V1_P8A_VALIDATION_SCHEMA_KEYS_INVALID")

    if payload["schema_name"] != SCHEMA_NAME:
        raise V1P8APublicationError("V1_P8A_VALIDATION_SCHEMA_NAME_INVALID")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise V1P8APublicationError("V1_P8A_VALIDATION_SCHEMA_VERSION_INVALID")
    if payload["publication_kind"] != PUBLICATION_KIND:
        raise V1P8APublicationError("V1_P8A_VALIDATION_PUBLICATION_KIND_INVALID")
    if payload["action"] != ACTION_BUY:
        raise V1P8APublicationError("V1_P8A_VALIDATION_ACTION_INVALID")

    if type(payload["ticker"]) is not str:
        raise V1P8APublicationError("V1_P8A_VALIDATION_TICKER_INVALID")
    if type(payload["target_notional"]) is not str:
        raise V1P8APublicationError("V1_P8A_VALIDATION_NOTIONAL_INVALID")
    if type(payload["total_candidate_notional"]) is not str:
        raise V1P8APublicationError("V1_P8A_VALIDATION_NOTIONAL_INVALID")

    if payload["authority_effect"] != AUTHORITY_EFFECT_NONE:
        raise V1P8APublicationError("V1_P8A_VALIDATION_AUTHORITY_POSTURE_INVALID")
    if payload["not_authorization"] is not True:
        raise V1P8APublicationError("V1_P8A_VALIDATION_AUTHORITY_POSTURE_INVALID")

    # Validate legs
    legs = payload["legs"]
    if type(legs) not in (list, tuple):
        raise V1P8APublicationError("V1_P8A_VALIDATION_LEGS_INVALID")

    leg_expected_keys = {
        "step_name",
        "whole_share_quantity",
        "rounded_limit_price",
        "candidate_notional",
    }
    for leg in legs:
        if type(leg) is not dict:
            raise V1P8APublicationError("V1_P8A_VALIDATION_LEGS_INVALID")
        if set(leg.keys()) != leg_expected_keys:
            raise V1P8APublicationError("V1_P8A_VALIDATION_LEGS_INVALID")
        if type(leg["step_name"]) is not str:
            raise V1P8APublicationError("V1_P8A_VALIDATION_LEGS_INVALID")
        if type(leg["whole_share_quantity"]) is not int or leg["whole_share_quantity"] <= 0:
            raise V1P8APublicationError("V1_P8A_VALIDATION_LEGS_INVALID")
        if type(leg["rounded_limit_price"]) is not str:
            raise V1P8APublicationError("V1_P8A_VALIDATION_LEGS_INVALID")
        if type(leg["candidate_notional"]) is not str:
            raise V1P8APublicationError("V1_P8A_VALIDATION_LEGS_INVALID")

    # Validate bindings
    bindings = payload["source_bindings"]
    if type(bindings) is not dict:
        raise V1P8APublicationError("V1_P8A_VALIDATION_PROVENANCE_INVALID")
    if set(bindings.keys()) != (_EXPECTED_STRING_BINDINGS | _EXPECTED_LIST_BINDINGS):
        raise V1P8APublicationError("V1_P8A_VALIDATION_PROVENANCE_INVALID")

    for k in _EXPECTED_STRING_BINDINGS:
        v = bindings[k]
        if v is not None and type(v) is not str:
            raise V1P8APublicationError("V1_P8A_VALIDATION_PROVENANCE_INVALID")

    for k in _EXPECTED_LIST_BINDINGS:
        v = bindings[k]
        if v is not None:
            if type(v) not in (list, tuple):
                raise V1P8APublicationError("V1_P8A_VALIDATION_PROVENANCE_INVALID")
            if any(type(item) is not str for item in v):
                raise V1P8APublicationError("V1_P8A_VALIDATION_PROVENANCE_INVALID")

    return payload


def publish_h1_v1_review_order() -> V1ReviewOrderPublicationResult:
    """Freshly evaluate P6 and Durably Publish a review-only BUY artifact.

    Rejects HOLD and NO_TRADE. Creates an immutable write-once
    artifact matching the canonical identity of the bound generation.
    Does not update any current or latest pointers.
    """
    # 1. Evaluate fresh P6 invocation purely in-memory
    p6_result = evaluate_h1_v1_postcompile_final_safety()

    # 2. Reject HOLD/NO_TRADE (publication subject mismatch)
    if p6_result.terminal_outcome != POSTCOMPILE_CANDIDATE_VALID:
        raise V1P8APublicationError("V1_P8A_PUBLICATION_SUBJECT_MISMATCH")

    # 3. Construct minimal complete exact package
    source_bindings_dict = {
        k: list(v) if type(v) is tuple else v
        for k, v in p6_result.source_bindings
    }

    # Require values for a valid buy candidate
    if p6_result.selected_ticker is None:
        raise V1P8APublicationError("V1_P8A_VALIDATION_TICKER_INVALID")
    if p6_result.target_increment is None:
        raise V1P8APublicationError("V1_P8A_VALIDATION_NOTIONAL_INVALID")
    if p6_result.total_new_candidate_notional is None:
        raise V1P8APublicationError("V1_P8A_VALIDATION_NOTIONAL_INVALID")

    legs = []
    for leg in p6_result.candidate_legs:
        legs.append({
            "step_name": leg.step_name,
            "whole_share_quantity": leg.whole_share_quantity,
            "rounded_limit_price": leg.rounded_limit_price,
            "candidate_notional": leg.candidate_notional,
        })

    payload = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "publication_kind": PUBLICATION_KIND,
        "ticker": p6_result.selected_ticker,
        "action": ACTION_BUY,
        "target_notional": p6_result.target_increment,
        "total_candidate_notional": p6_result.total_new_candidate_notional,
        "legs": legs,
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
            raise V1P8APublicationError("V1_P8A_EXISTING_READ_FAILURE")

        if actual_bytes == canonical_bytes:
            existed = True
        else:
            raise V1P8APublicationError("V1_P8A_EXISTING_IDENTITY_MISMATCH")
    else:
        # 8. Atomic Write
        # atomic_write_text relies on os.replace, ensuring atomic durability on disk
        # without exposing partial writes.
        try:
            atomic_write_text(target_path, canonical_bytes.decode("utf-8"))
        except OSError:
            raise V1P8APublicationError("V1_P8A_ATOMIC_WRITE_FAILED")

        # 9. Verify on disk
        try:
            actual_bytes = target_path.read_bytes()
        except OSError:
            raise V1P8APublicationError("V1_P8A_EXISTING_READ_FAILURE")

        if actual_bytes != canonical_bytes:
            raise V1P8APublicationError("V1_P8A_EXISTING_IDENTITY_MISMATCH")

    # 10. Operational Return
    return V1ReviewOrderPublicationResult(
        terminal_outcome=p6_result.terminal_outcome,
        artifact_identity_sha256=artifact_identity,
        immutable_path=target_path,
        existed_idempotently=existed,
        selected_ticker=p6_result.selected_ticker,
        total_candidate_notional=p6_result.total_new_candidate_notional,
    )
