"""Pure Step 2 publication-identity receipt construction and verification.

R2F-2b-c1 defines identity evidence for a future gate-before-publication
integration.  This module is deliberately non-authorizing and has no
filesystem, clock, workflow, pointer, or publication side effects.

A valid receipt proves identity consistency only.  It does not grant Step 2
publication permission and does not grant HOLD, NO_TRADE, SELL, NEW_BUY,
ORDER_COMPILATION, Step 3, Step 4, final-safety, order, broker, or execution
authority.  No production workflow consumes this module in R2F-2b-c1.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum
import hashlib
import json
import re
from typing import Any, TypeAlias

from jsonschema import Draft202012Validator, FormatChecker

from investment_orchestrator.research.promoted_handoff_verifier import (
    verify_promoted_handoff_for_step2_decision,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    ACTIONABLE_REQUIRED_STATE,
    MODE_PROMOTED_STEP2_DECISION_ONLY,
    MODE_STRICT_FRESH_ACTIONABLE,
    PROMOTED_STEP2_DECISION_ONLY_STATE,
    ResearchDegradedModeGateResult,
    evaluate_step2_research_gate,
)


RECEIPT_SCHEMA_VERSION = "step2_publication_receipt_v1"
VERIFICATION_SCHEMA_VERSION = "step2_publication_identity_verification_v1"
RECEIPT_SCHEMA_FILENAME = "step2_publication_receipt.schema.json"

IDENTITY_ONLY = True
NOT_AUTHORIZATION = True
PERMISSION_EFFECT_NONE = "none"

# Contract bounds apply to every caller-controlled decoded/snapshotted JSON
# value.  Depth is the number of child edges from the root (root depth is 0),
# and node count includes every container and scalar value, but not object keys.
MAX_JSON_NESTING_DEPTH = 32
MAX_JSON_NODE_COUNT = 4096

STRICT_CALENDAR_DATE_FORMAT = "date"
VERIFICATION_BOOLEAN_COERCION_ERROR = (
    "inspect identity_consistent explicitly; verification results have no truth value"
)

GATE_POLICY = "research_degraded_mode_gate"
GATE_OUTCOME_ALLOWED = "allowed"
GATE_ALLOWED_REASON_CODE = "step2_gate_allowed"

SUPPORTED_PUBLICATION_MODES = frozenset(
    {MODE_STRICT_FRESH_ACTIONABLE, MODE_PROMOTED_STEP2_DECISION_ONLY}
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_RECEIPT_FORMAT_CHECKER = FormatChecker()

BytesLike: TypeAlias = bytes | bytearray | memoryview

_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "generation_id",
        "publication_mode",
        "evaluated_date",
        "candidate_identities",
        "input_identities",
        "gate_result",
        "promoted_source_identities",
        "identity_only",
        "not_authorization",
        "permission_effect",
    }
)
_CANDIDATE_IDENTITY_KEYS = frozenset(
    {"template2_output_sha256", "decision_packet_sha256"}
)
_INPUT_IDENTITY_KEYS = frozenset(
    {
        "prompt_sha256",
        "raw_output_sha256",
        "normalization_settings_sha256",
        "permission_artifact_sha256",
    }
)
_GATE_RESULT_KEYS = frozenset(
    {
        "policy",
        "outcome",
        "reason_code",
        "allowed",
        "mode",
        "state",
        "manual_review_required",
        "gate_result_sha256",
    }
)
_PROMOTED_IDENTITY_KEYS = frozenset(
    {
        "active_research_handoff_source_sha256",
        "research_handoff_candidate_effective_sha256",
        "research_handoff_candidate_effective_validation_sha256",
        "verified_effective_handoff_sha256",
        "pointer_effective_handoff_sha256",
        "promoted_verification_sha256",
        "promotion_expires_at",
    }
)


class Step2PublicationIdentityDiagnostic(str, Enum):
    """Closed, code-owned diagnostics for receipt construction and verification."""

    IDENTITY_CONSISTENT = "receipt_identity_consistent"
    RECEIPT_MISSING = "receipt_missing"
    RECEIPT_SCHEMA_INVALID = "receipt_schema_invalid"
    RECEIPT_MODE_INVALID = "receipt_mode_invalid"
    RECEIPT_IDENTITY_MISSING = "receipt_identity_missing"
    RECEIPT_IDENTITY_MALFORMED = "receipt_identity_malformed"
    RECEIPT_TEMPLATE_MISMATCH = "receipt_template_mismatch"
    RECEIPT_PACKET_MISMATCH = "receipt_packet_mismatch"
    RECEIPT_PROMPT_MISMATCH = "receipt_prompt_mismatch"
    RECEIPT_RAW_OUTPUT_MISMATCH = "receipt_raw_output_mismatch"
    RECEIPT_SETTINGS_MISMATCH = "receipt_settings_mismatch"
    RECEIPT_PERMISSION_MISMATCH = "receipt_permission_mismatch"
    RECEIPT_GATE_MISMATCH = "receipt_gate_mismatch"
    RECEIPT_PROMOTED_SOURCE_MISMATCH = "receipt_promoted_source_mismatch"
    RECEIPT_GENERATION_MISMATCH = "receipt_generation_mismatch"
    RECEIPT_EVALUATED_DATE_MISMATCH = "receipt_evaluated_date_mismatch"


class Step2PublicationIdentityError(ValueError):
    """Bounded construction error containing only a code-owned diagnostic."""

    def __init__(self, diagnostic_code: Step2PublicationIdentityDiagnostic) -> None:
        self.diagnostic_code = diagnostic_code
        super().__init__(diagnostic_code.value)


@dataclass(frozen=True, slots=True)
class Step2PublicationIdentityVerification:
    """Immutable, closed result of pure receipt verification."""

    identity_consistent: bool
    diagnostic_code: Step2PublicationIdentityDiagnostic
    generation_id: str | None
    schema_version: str = VERIFICATION_SCHEMA_VERSION
    identity_only: bool = IDENTITY_ONLY
    not_authorization: bool = NOT_AUTHORIZATION
    permission_effect: str = PERMISSION_EFFECT_NONE

    def __bool__(self) -> bool:
        """Reject ambiguous success/failure coercion by every caller."""
        raise TypeError(VERIFICATION_BOOLEAN_COERCION_ERROR)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible copy of this non-authorizing result."""
        return {
            "schema_version": self.schema_version,
            "identity_consistent": self.identity_consistent,
            "diagnostic_code": self.diagnostic_code.value,
            "generation_id": self.generation_id,
            "identity_only": self.identity_only,
            "not_authorization": self.not_authorization,
            "permission_effect": self.permission_effect,
        }


def sha256_exact_bytes(value: BytesLike) -> str:
    """Return SHA-256 over the exact supplied bytes, without normalization."""
    try:
        captured = _capture_bytes(value)
    except (TypeError, ValueError):
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MALFORMED
        ) from None
    return hashlib.sha256(captured).hexdigest()


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Snapshot a code-owned JSON mapping into deterministic UTF-8 bytes.

    Mapping order is irrelevant.  Non-string keys, floats, non-JSON values,
    and non-finite numbers are rejected rather than coerced.
    """
    snapshot = _snapshot_json_value(value)
    if type(snapshot) is not dict:
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
        )
    try:
        return _iterative_canonical_json_bytes(snapshot)
    except (TypeError, ValueError, RecursionError):
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
        ) from None


def _iterative_canonical_json_bytes(value: dict[str, Any]) -> bytes:
    """Serialize a validated strict JSON tree without recursive traversal."""
    fragments: list[bytes] = []
    # Frames are either a validated JSON value or a raw JSON punctuation token.
    stack: list[tuple[str, Any]] = [("value", value)]

    while stack:
        operation, current = stack.pop()
        if operation == "raw":
            fragments.append(current)
            continue

        if current is None:
            fragments.append(b"null")
            continue
        if type(current) is bool:
            fragments.append(b"true" if current else b"false")
            continue
        if type(current) is int:
            fragments.append(str(current).encode("ascii"))
            continue
        if type(current) is str:
            fragments.append(
                json.dumps(current, ensure_ascii=False, allow_nan=False).encode(
                    "utf-8"
                )
            )
            continue

        if type(current) is list:
            fragments.append(b"[")
            stack.append(("raw", b"]"))
            for index in range(len(current) - 1, -1, -1):
                stack.append(("value", current[index]))
                if index != 0:
                    stack.append(("raw", b","))
            continue

        if type(current) is dict:
            fragments.append(b"{")
            stack.append(("raw", b"}"))
            keys = sorted(current)
            for index in range(len(keys) - 1, -1, -1):
                key = keys[index]
                if type(key) is not str:
                    raise TypeError("canonical JSON object key is not a string")
                stack.append(("value", current[key]))
                stack.append(("raw", b":"))
                stack.append(("value", key))
                if index != 0:
                    stack.append(("raw", b","))
            continue

        raise TypeError("canonical JSON value has an unsupported type")

    return b"".join(fragments)


def is_step2_publication_receipt_schema_valid(
    receipt: Any,
    *,
    schema: Mapping[str, Any],
) -> bool:
    """Validate a receipt with the required Draft 2020-12 format assertion.

    The schema is code-owned and therefore checked rather than converted.  The
    caller-controlled receipt is first snapshotted under this module's strict
    JSON-native structural contract so cycles, excessive structures, and
    unsupported values cannot reach the recursive schema validator.
    """
    if type(schema) is not dict:
        raise TypeError("step2 publication receipt schema must be a JSON object")
    Draft202012Validator.check_schema(schema)
    try:
        snapshot = _snapshot_json_value(receipt)
    except Step2PublicationIdentityError:
        return False
    if type(snapshot) is not dict:
        return False
    validator = Draft202012Validator(
        schema,
        format_checker=_RECEIPT_FORMAT_CHECKER,
    )
    try:
        return validator.is_valid(snapshot)
    except (TypeError, ValueError, RecursionError):
        return False


def derive_generation_id(receipt_without_generation_id: Mapping[str, Any]) -> str:
    """Derive the generation ID from the complete closed identity material."""
    material = canonical_json_bytes(receipt_without_generation_id)
    domain = (RECEIPT_SCHEMA_VERSION + "\0").encode("ascii")
    return hashlib.sha256(domain + material).hexdigest()


def build_step2_publication_receipt(
    *,
    publication_mode: str,
    evaluated_date: str,
    template2_output_bytes: BytesLike,
    decision_packet_bytes: BytesLike,
    prompt_bytes: BytesLike,
    raw_output_bytes: BytesLike,
    normalization_settings_bytes: BytesLike,
    permission_artifact_bytes: BytesLike,
    promoted_active_pointer_bytes: BytesLike | None = None,
    promoted_effective_handoff_bytes: BytesLike | None = None,
    promoted_effective_validation_bytes: BytesLike | None = None,
) -> dict[str, Any]:
    """Build a pure, non-authorizing receipt from caller-captured bytes.

    The existing deterministic Step 2 gate is evaluated directly from the
    captured permission bytes.  Promoted mode additionally invokes the existing
    promoted-handoff verifier from the three captured source artifacts.  No
    caller-supplied success mapping or precomputed identity is accepted.
    """
    mode = _require_supported_mode(publication_mode)
    evaluated = _parse_strict_date(evaluated_date)
    captured = _capture_required_inputs(
        template2_output_bytes=template2_output_bytes,
        decision_packet_bytes=decision_packet_bytes,
        prompt_bytes=prompt_bytes,
        raw_output_bytes=raw_output_bytes,
        normalization_settings_bytes=normalization_settings_bytes,
        permission_artifact_bytes=permission_artifact_bytes,
    )

    gate = _evaluate_captured_gate(captured["permission_artifact_bytes"], mode)
    gate_identity = _gate_identity(gate)
    promoted_identity = _build_promoted_identity(
        mode=mode,
        evaluated_date=evaluated,
        active_pointer_bytes=promoted_active_pointer_bytes,
        effective_handoff_bytes=promoted_effective_handoff_bytes,
        effective_validation_bytes=promoted_effective_validation_bytes,
    )

    receipt_without_generation_id: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "publication_mode": mode,
        "evaluated_date": evaluated.isoformat(),
        "candidate_identities": {
            "template2_output_sha256": sha256_exact_bytes(
                captured["template2_output_bytes"]
            ),
            "decision_packet_sha256": sha256_exact_bytes(
                captured["decision_packet_bytes"]
            ),
        },
        "input_identities": {
            "prompt_sha256": sha256_exact_bytes(captured["prompt_bytes"]),
            "raw_output_sha256": sha256_exact_bytes(captured["raw_output_bytes"]),
            "normalization_settings_sha256": sha256_exact_bytes(
                captured["normalization_settings_bytes"]
            ),
            "permission_artifact_sha256": sha256_exact_bytes(
                captured["permission_artifact_bytes"]
            ),
        },
        "gate_result": gate_identity,
        "promoted_source_identities": promoted_identity,
        "identity_only": IDENTITY_ONLY,
        "not_authorization": NOT_AUTHORIZATION,
        "permission_effect": PERMISSION_EFFECT_NONE,
    }
    generation_id = derive_generation_id(receipt_without_generation_id)
    receipt = {
        "schema_version": receipt_without_generation_id["schema_version"],
        "generation_id": generation_id,
        **{
            key: value
            for key, value in receipt_without_generation_id.items()
            if key != "schema_version"
        },
    }
    diagnostic = _receipt_shape_diagnostic(receipt)
    if diagnostic is not None:
        raise Step2PublicationIdentityError(diagnostic)
    return receipt


def verify_step2_publication_receipt(
    *,
    receipt: Mapping[str, Any] | None,
    expected_publication_mode: str,
    expected_evaluated_date: str,
    template2_output_bytes: BytesLike,
    decision_packet_bytes: BytesLike,
    prompt_bytes: BytesLike,
    raw_output_bytes: BytesLike,
    normalization_settings_bytes: BytesLike,
    permission_artifact_bytes: BytesLike,
    promoted_active_pointer_bytes: BytesLike | None = None,
    promoted_effective_handoff_bytes: BytesLike | None = None,
    promoted_effective_validation_bytes: BytesLike | None = None,
) -> Step2PublicationIdentityVerification:
    """Purely verify a receipt against exact captured bytes and code-owned gates."""
    if receipt is None:
        return _verification_failure(Step2PublicationIdentityDiagnostic.RECEIPT_MISSING)

    try:
        receipt_snapshot = _snapshot_json_value(receipt)
    except Step2PublicationIdentityError:
        return _verification_failure(
            Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
        )
    if type(receipt_snapshot) is not dict:
        return _verification_failure(
            Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
        )

    shape_diagnostic = _receipt_shape_diagnostic(receipt_snapshot)
    if shape_diagnostic is not None:
        return _verification_failure(shape_diagnostic)

    try:
        expected = build_step2_publication_receipt(
            publication_mode=expected_publication_mode,
            evaluated_date=expected_evaluated_date,
            template2_output_bytes=template2_output_bytes,
            decision_packet_bytes=decision_packet_bytes,
            prompt_bytes=prompt_bytes,
            raw_output_bytes=raw_output_bytes,
            normalization_settings_bytes=normalization_settings_bytes,
            permission_artifact_bytes=permission_artifact_bytes,
            promoted_active_pointer_bytes=promoted_active_pointer_bytes,
            promoted_effective_handoff_bytes=promoted_effective_handoff_bytes,
            promoted_effective_validation_bytes=promoted_effective_validation_bytes,
        )
    except Step2PublicationIdentityError as exc:
        return _verification_failure(exc.diagnostic_code)

    if receipt_snapshot["publication_mode"] != expected["publication_mode"]:
        return _verification_failure(
            Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH
        )
    if receipt_snapshot["evaluated_date"] != expected["evaluated_date"]:
        return _verification_failure(
            Step2PublicationIdentityDiagnostic.RECEIPT_EVALUATED_DATE_MISMATCH
        )

    actual_candidates = receipt_snapshot["candidate_identities"]
    expected_candidates = expected["candidate_identities"]
    if (
        actual_candidates["template2_output_sha256"]
        != expected_candidates["template2_output_sha256"]
    ):
        return _verification_failure(
            Step2PublicationIdentityDiagnostic.RECEIPT_TEMPLATE_MISMATCH
        )
    if (
        actual_candidates["decision_packet_sha256"]
        != expected_candidates["decision_packet_sha256"]
    ):
        return _verification_failure(
            Step2PublicationIdentityDiagnostic.RECEIPT_PACKET_MISMATCH
        )

    actual_inputs = receipt_snapshot["input_identities"]
    expected_inputs = expected["input_identities"]
    input_checks = (
        (
            "prompt_sha256",
            Step2PublicationIdentityDiagnostic.RECEIPT_PROMPT_MISMATCH,
        ),
        (
            "raw_output_sha256",
            Step2PublicationIdentityDiagnostic.RECEIPT_RAW_OUTPUT_MISMATCH,
        ),
        (
            "normalization_settings_sha256",
            Step2PublicationIdentityDiagnostic.RECEIPT_SETTINGS_MISMATCH,
        ),
        (
            "permission_artifact_sha256",
            Step2PublicationIdentityDiagnostic.RECEIPT_PERMISSION_MISMATCH,
        ),
    )
    for field, diagnostic in input_checks:
        if actual_inputs[field] != expected_inputs[field]:
            return _verification_failure(diagnostic)

    if receipt_snapshot["gate_result"] != expected["gate_result"]:
        return _verification_failure(
            Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH
        )
    if (
        receipt_snapshot["promoted_source_identities"]
        != expected["promoted_source_identities"]
    ):
        return _verification_failure(
            Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH
        )
    if receipt_snapshot["generation_id"] != expected["generation_id"]:
        return _verification_failure(
            Step2PublicationIdentityDiagnostic.RECEIPT_GENERATION_MISMATCH
        )

    return Step2PublicationIdentityVerification(
        identity_consistent=True,
        diagnostic_code=Step2PublicationIdentityDiagnostic.IDENTITY_CONSISTENT,
        generation_id=expected["generation_id"],
    )


def _capture_required_inputs(**values: BytesLike) -> dict[str, bytes]:
    captured: dict[str, bytes] = {}
    for key, value in values.items():
        try:
            captured[key] = _capture_bytes(value)
        except (TypeError, ValueError):
            diagnostic = (
                Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING
                if value is None
                else Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MALFORMED
            )
            raise Step2PublicationIdentityError(diagnostic) from None
    return captured


def _capture_bytes(value: BytesLike) -> bytes:
    if isinstance(value, bytes):
        return bytes(value)
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    raise TypeError("identity inputs must be bytes-like")


def _require_supported_mode(value: Any) -> str:
    if type(value) is not str or value not in SUPPORTED_PUBLICATION_MODES:
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_MODE_INVALID
        )
    return value


def _parse_strict_date(value: Any) -> date:
    if type(value) is not str or _DATE_RE.fullmatch(value) is None:
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
        ) from None
    if parsed.isoformat() != value:
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
        )
    return parsed


@_RECEIPT_FORMAT_CHECKER.checks(STRICT_CALENDAR_DATE_FORMAT)
def _is_strict_calendar_date_format(value: Any) -> bool:
    """Return whether *value* is the contract's exact calendar-date string."""
    try:
        _parse_strict_date(value)
    except Step2PublicationIdentityError:
        return False
    return True


def _evaluate_captured_gate(
    permission_artifact_bytes: bytes,
    expected_mode: str,
) -> ResearchDegradedModeGateResult:
    # JSONDecodeError and UnicodeDecodeError are bounded by ValueError here.
    try:
        permission = _load_strict_json_object(permission_artifact_bytes)
    except (TypeError, ValueError, RecursionError):
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH
        ) from None

    # The payload is fully decoded, structurally bounded, and snapshotted before
    # the dependency call.  Unexpected dependency/programming failures must
    # propagate rather than masquerade as malformed caller input.
    gate = evaluate_step2_research_gate(permission)
    if gate.allowed is not True or gate.mode != expected_mode:
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH
        )
    return gate


def _gate_identity(gate: ResearchDegradedModeGateResult) -> dict[str, Any]:
    gate_projection = {
        "allowed": gate.allowed,
        "state": gate.state,
        "allowed_actions": gate.allowed_actions,
        "blocked_actions": gate.blocked_actions,
        "manual_review_required": gate.manual_review_required,
        "blocker_reasons": gate.blocker_reasons,
        "malformed_reasons": gate.malformed_reasons,
        "mode": gate.mode,
        "order_compilation_allowed": gate.order_compilation_allowed,
        "new_buy_permission": gate.new_buy_permission,
        "step3_allowed": gate.step3_allowed,
        "step4_allowed": gate.step4_allowed,
        "recommended_terminal_result_after_step2": (
            gate.recommended_terminal_result_after_step2
        ),
    }
    return {
        "policy": GATE_POLICY,
        "outcome": GATE_OUTCOME_ALLOWED,
        "reason_code": GATE_ALLOWED_REASON_CODE,
        "allowed": True,
        "mode": gate.mode,
        "state": gate.state,
        "manual_review_required": False,
        "gate_result_sha256": sha256_exact_bytes(canonical_json_bytes(gate_projection)),
    }


def _build_promoted_identity(
    *,
    mode: str,
    evaluated_date: date,
    active_pointer_bytes: BytesLike | None,
    effective_handoff_bytes: BytesLike | None,
    effective_validation_bytes: BytesLike | None,
) -> dict[str, Any] | None:
    values = (active_pointer_bytes, effective_handoff_bytes, effective_validation_bytes)
    if mode == MODE_STRICT_FRESH_ACTIONABLE:
        if any(value is not None for value in values):
            raise Step2PublicationIdentityError(
                Step2PublicationIdentityDiagnostic.RECEIPT_MODE_INVALID
            )
        return None

    if mode != MODE_PROMOTED_STEP2_DECISION_ONLY:
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_MODE_INVALID
        )
    if any(value is None for value in values):
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING
        )

    try:
        pointer_bytes = _capture_bytes(active_pointer_bytes)  # type: ignore[arg-type]
        effective_bytes = _capture_bytes(effective_handoff_bytes)  # type: ignore[arg-type]
        validation_bytes = _capture_bytes(effective_validation_bytes)  # type: ignore[arg-type]
        pointer = _load_strict_json_object(pointer_bytes)
        effective = _load_strict_json_object(effective_bytes)
        validation = _load_strict_json_object(validation_bytes)
    # JSONDecodeError and UnicodeDecodeError are bounded by ValueError here.
    except (TypeError, ValueError, RecursionError):
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH
        ) from None

    verification = verify_promoted_handoff_for_step2_decision(
        active_pointer=pointer,
        effective_handoff=effective,
        effective_validation=validation,
        today=evaluated_date,
    )
    if (
        verification.get("valid_for_step2_decision") is not True
        or verification.get("verification_blockers") != []
    ):
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH
        )

    verified_effective_hash = verification.get("effective_handoff_sha256")
    pointer_effective_hash = verification.get("pointer_effective_handoff_sha256")
    promotion_expires_at = verification.get("promotion_expires_at")
    if (
        not _is_sha256(verified_effective_hash)
        or not _is_sha256(pointer_effective_hash)
        or type(promotion_expires_at) is not str
    ):
        raise Step2PublicationIdentityError(
            Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH
        )
    _parse_strict_date(promotion_expires_at)

    return {
        "active_research_handoff_source_sha256": sha256_exact_bytes(pointer_bytes),
        "research_handoff_candidate_effective_sha256": sha256_exact_bytes(
            effective_bytes
        ),
        "research_handoff_candidate_effective_validation_sha256": sha256_exact_bytes(
            validation_bytes
        ),
        "verified_effective_handoff_sha256": verified_effective_hash,
        "pointer_effective_handoff_sha256": pointer_effective_hash,
        "promoted_verification_sha256": sha256_exact_bytes(
            canonical_json_bytes(verification)
        ),
        "promotion_expires_at": promotion_expires_at,
    }


def _load_strict_json_object(payload_bytes: bytes) -> dict[str, Any]:
    def reject_constant(_: str) -> Any:
        raise ValueError("non-finite JSON number")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    text = payload_bytes.decode("utf-8")
    value = json.loads(
        text,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )
    snapshot = _snapshot_json_value(value)
    if type(snapshot) is not dict:
        raise ValueError("JSON root must be an object")
    return snapshot


def _snapshot_json_value(value: Any) -> Any:
    """Iteratively snapshot one strict JSON-native value or fail closed.

    Only exact built-in ``dict``, ``list``, ``str``, ``bool``, ``int``, and
    ``None`` values are accepted.  The explicit stack avoids dependence on the
    interpreter recursion limit; the active-container set rejects cycles while
    still allowing acyclic shared references to be serialized by value.
    """
    root: list[Any] = [None]
    active_container_ids: set[int] = set()
    node_count = 0
    # Frames are (operation, source value, destination container, slot, depth).
    stack: list[tuple[str, Any, Any, Any, int]] = [
        ("visit", value, root, 0, 0)
    ]

    while stack:
        operation, source, destination, slot, depth = stack.pop()
        if operation == "leave":
            active_container_ids.remove(id(source))
            continue

        node_count += 1
        if node_count > MAX_JSON_NODE_COUNT or depth > MAX_JSON_NESTING_DEPTH:
            raise Step2PublicationIdentityError(
                Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
            )

        if source is None or type(source) in {bool, str, int}:
            destination[slot] = source
            continue

        if type(source) not in {dict, list}:
            raise Step2PublicationIdentityError(
                Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
            )

        source_id = id(source)
        if source_id in active_container_ids:
            raise Step2PublicationIdentityError(
                Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
            )
        active_container_ids.add(source_id)
        stack.append(("leave", source, None, None, depth))

        if node_count + len(source) > MAX_JSON_NODE_COUNT:
            raise Step2PublicationIdentityError(
                Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
            )

        if type(source) is dict:
            snapshot_object: dict[str, Any] = {}
            destination[slot] = snapshot_object
            if any(type(key) is not str for key in source):
                raise Step2PublicationIdentityError(
                    Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
                )
            for key in reversed(source):
                stack.append(
                    ("visit", source[key], snapshot_object, key, depth + 1)
                )
            continue

        snapshot_array: list[Any] = [None] * len(source)
        destination[slot] = snapshot_array
        for index in range(len(source) - 1, -1, -1):
            stack.append(
                ("visit", source[index], snapshot_array, index, depth + 1)
            )

    return root[0]


def _receipt_shape_diagnostic(
    receipt: Mapping[str, Any],
) -> Step2PublicationIdentityDiagnostic | None:
    if set(receipt) != _ROOT_KEYS:
        missing = _ROOT_KEYS - set(receipt)
        identity_roots = {
            "generation_id",
            "candidate_identities",
            "input_identities",
            "gate_result",
            "promoted_source_identities",
        }
        if missing & identity_roots:
            return Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID

    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    mode = receipt.get("publication_mode")
    if type(mode) is not str or mode not in SUPPORTED_PUBLICATION_MODES:
        return Step2PublicationIdentityDiagnostic.RECEIPT_MODE_INVALID
    try:
        _parse_strict_date(receipt.get("evaluated_date"))
    except Step2PublicationIdentityError:
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID

    if receipt.get("identity_only") is not True:
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    if receipt.get("not_authorization") is not True:
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    if receipt.get("permission_effect") != PERMISSION_EFFECT_NONE:
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    if not _is_sha256(receipt.get("generation_id")):
        return Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MALFORMED

    candidate_diagnostic = _identity_object_diagnostic(
        receipt.get("candidate_identities"), _CANDIDATE_IDENTITY_KEYS
    )
    if candidate_diagnostic is not None:
        return candidate_diagnostic
    input_diagnostic = _identity_object_diagnostic(
        receipt.get("input_identities"), _INPUT_IDENTITY_KEYS
    )
    if input_diagnostic is not None:
        return input_diagnostic

    gate = receipt.get("gate_result")
    if not isinstance(gate, Mapping):
        return Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING
    if set(gate) != _GATE_RESULT_KEYS:
        if _GATE_RESULT_KEYS - set(gate):
            return Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    if any(
        type(gate.get(field)) is not str
        for field in ("policy", "outcome", "reason_code", "mode", "state")
    ):
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    if type(gate.get("allowed")) is not bool or type(
        gate.get("manual_review_required")
    ) is not bool:
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    if not _is_sha256(gate.get("gate_result_sha256")):
        return Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MALFORMED
    if (
        gate.get("policy") != GATE_POLICY
        or gate.get("outcome") != GATE_OUTCOME_ALLOWED
        or gate.get("reason_code") != GATE_ALLOWED_REASON_CODE
        or gate.get("allowed") is not True
        or gate.get("manual_review_required") is not False
    ):
        return Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH
    expected_state = (
        ACTIONABLE_REQUIRED_STATE
        if mode == MODE_STRICT_FRESH_ACTIONABLE
        else PROMOTED_STEP2_DECISION_ONLY_STATE
    )
    if gate.get("mode") != mode or gate.get("state") != expected_state:
        return Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH

    promoted = receipt.get("promoted_source_identities")
    if mode == MODE_STRICT_FRESH_ACTIONABLE:
        if promoted is not None:
            return Step2PublicationIdentityDiagnostic.RECEIPT_MODE_INVALID
    else:
        promoted_diagnostic = _promoted_identity_diagnostic(promoted)
        if promoted_diagnostic is not None:
            return promoted_diagnostic
    return None


def _identity_object_diagnostic(
    value: Any,
    expected_keys: frozenset[str],
) -> Step2PublicationIdentityDiagnostic | None:
    if not isinstance(value, Mapping):
        return Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING
    if set(value) != expected_keys:
        if expected_keys - set(value):
            return Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    if any(not _is_sha256(value.get(key)) for key in expected_keys):
        return Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MALFORMED
    return None


def _promoted_identity_diagnostic(
    value: Any,
) -> Step2PublicationIdentityDiagnostic | None:
    if not isinstance(value, Mapping):
        return Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING
    if set(value) != _PROMOTED_IDENTITY_KEYS:
        if _PROMOTED_IDENTITY_KEYS - set(value):
            return Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    hash_keys = _PROMOTED_IDENTITY_KEYS - {"promotion_expires_at"}
    if any(not _is_sha256(value.get(key)) for key in hash_keys):
        return Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MALFORMED
    try:
        _parse_strict_date(value.get("promotion_expires_at"))
    except Step2PublicationIdentityError:
        return Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    return None


def _is_sha256(value: Any) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _verification_failure(
    diagnostic: Step2PublicationIdentityDiagnostic,
) -> Step2PublicationIdentityVerification:
    return Step2PublicationIdentityVerification(
        identity_consistent=False,
        diagnostic_code=diagnostic,
        generation_id=None,
    )


__all__ = [
    "GATE_ALLOWED_REASON_CODE",
    "GATE_OUTCOME_ALLOWED",
    "GATE_POLICY",
    "IDENTITY_ONLY",
    "MAX_JSON_NESTING_DEPTH",
    "MAX_JSON_NODE_COUNT",
    "NOT_AUTHORIZATION",
    "PERMISSION_EFFECT_NONE",
    "RECEIPT_SCHEMA_FILENAME",
    "RECEIPT_SCHEMA_VERSION",
    "SUPPORTED_PUBLICATION_MODES",
    "STRICT_CALENDAR_DATE_FORMAT",
    "VERIFICATION_SCHEMA_VERSION",
    "VERIFICATION_BOOLEAN_COERCION_ERROR",
    "Step2PublicationIdentityDiagnostic",
    "Step2PublicationIdentityError",
    "Step2PublicationIdentityVerification",
    "build_step2_publication_receipt",
    "canonical_json_bytes",
    "derive_generation_id",
    "is_step2_publication_receipt_schema_valid",
    "sha256_exact_bytes",
    "verify_step2_publication_receipt",
]
