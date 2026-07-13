"""Strict, pure R2F-1b-a memo-content validation.

R2F-1b-a accepts only content-only JSON from a verified v2 generation. It
validates structure, code-derived binding, deterministic identifier membership,
and active-anchor references. It writes no artifacts and has no runtime consumer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any
import unicodedata

from investment_orchestrator.research import replacement_generation_reader as generation_reader
from investment_orchestrator.research.replacement_generation_reader import (
    EligibleInstrument,
    MemoRawRead,
    VerifiedSourceBinding,
    _VerifiedMemoInput,
)

__all__ = (
    "ReplacementMemoContractError",
    "ValidatedMemoEnvelope",
    "validate_generation_memo",
)


RAW_MEMO_SCHEMA_VERSION = "r2f_analyst_memo_content_v2"
VALIDATED_MEMO_SCHEMA_VERSION = "r2f_validated_memo_envelope_v2"
MAXIMUM_MEMO_BYTES = 65_536
MAXIMUM_OBSERVATIONS = 32
MAXIMUM_REFERENCES_PER_OBSERVATION = 8
MAXIMUM_RATIONALE_CODEPOINTS = 280

MEMO_RESULT_NO_TRADE = "NO_TRADE"
MEMO_RESULT_OBSERVATION_ONLY = "OBSERVATION_ONLY"
MEMO_RESULTS = frozenset({MEMO_RESULT_NO_TRADE, MEMO_RESULT_OBSERVATION_ONLY})
CONFIDENCE_VALUES = frozenset({"LOW", "MEDIUM", "HIGH"})
RESEARCH_VIEWS = frozenset({"PREFER", "NEUTRAL", "DEPRIORITIZE"})
ACTIVE_ANCHOR_NAMESPACE = "ACTIVE_ANCHOR"

_RAW_MEMO_KEYS = frozenset(
    {"schema_version", "memo_result", "confidence", "instrument_observations"}
)
_OBSERVATION_KEYS = frozenset(
    {"instrument_id", "research_view", "rationale", "evidence_references"}
)
_REFERENCE_KEYS = frozenset({"namespace", "evidence_id"})
_IDENTIFIER_RE = re.compile(r"[A-Z][A-Z0-9.-]{0,14}\Z")

_AUTHORITY_MARKERS = {
    "artifact_role": "NON_AUTHORITATIVE_RESEARCH_OBSERVATION",
    "report_only": True,
    "runtime_consumed": False,
    "permission_effect": "NONE",
    "not_authorization": True,
    "order_authorization": False,
    "broker_authorization": False,
}


class ReplacementMemoContractError(RuntimeError):
    """Bounded contract failure that never exposes raw memo content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedMemoEnvelope:
    """In-memory, code-owned output for a valid strict memo envelope."""

    payload: Mapping[str, Any]
    canonical_bytes: bytes
    canonical_sha256: str


@dataclass(frozen=True, slots=True)
class _MemoValidationOutcome:
    result: ValidatedMemoEnvelope | None
    failure_code: str | None


def validate_generation_memo(generation_id: str) -> ValidatedMemoEnvelope:
    """Validate one exact R2F-1a generation and memo in a single operation."""
    return _validate_generation_memo_with_runner(
        generation_id,
        generation_reader._validate_generation_memo_operation,
    )


def _validate_generation_memo_at_root_for_tests(
    generation_id: str,
    repository_root: Path,
) -> ValidatedMemoEnvelope:
    """Private isolated-root seam; no descriptor or path enters the result."""
    return _validate_generation_memo_with_runner(
        generation_id,
        lambda value, validator: generation_reader._validate_generation_memo_operation_at_root_for_tests(
            value,
            repository_root,
            validator,
        ),
    )


def _validate_generation_memo_with_runner(
    generation_id: str,
    runner: Any,
) -> ValidatedMemoEnvelope:
    outcome = runner(generation_id, _validate_operation_input)
    if outcome.failure_code is not None:
        raise ReplacementMemoContractError(outcome.failure_code) from None
    if outcome.result is None:
        raise ReplacementMemoContractError("MEMO_JSON_INVALID")
    return outcome.result


def _validate_operation_input(value: _VerifiedMemoInput) -> _MemoValidationOutcome:
    failure_code: str | None = None
    result: ValidatedMemoEnvelope | None = None
    try:
        result = _validate_memo_raw(
            value.memo_raw,
            source_binding=value.source_binding,
            eligible_instruments=value.eligible_instruments,
            active_anchor_ids=value.active_anchor_ids,
        )
    except ReplacementMemoContractError as error:
        failure_code = error.code
    return _MemoValidationOutcome(result=result, failure_code=failure_code)


def _validate_memo_raw(
    raw: MemoRawRead,
    *,
    source_binding: VerifiedSourceBinding,
    eligible_instruments: tuple[EligibleInstrument, ...],
    active_anchor_ids: tuple[str, ...],
) -> ValidatedMemoEnvelope:
    """Private deterministic normalizer over already verified reader context."""
    if raw.byte_size != len(raw.raw_bytes) or raw.file_sha256 != _sha256(raw.raw_bytes):
        raise ReplacementMemoContractError("MEMO_SOURCE_READ_FAILED")
    if raw.byte_size > MAXIMUM_MEMO_BYTES:
        raise ReplacementMemoContractError("MEMO_TOO_LARGE")
    text = _decode_memo_text(raw.raw_bytes)
    if text.strip() == "":
        raise ReplacementMemoContractError("MEMO_BLANK")
    normalized_text = _normalize_newlines(text)
    payload = _parse_json_object(normalized_text)
    normalized_memo = _validate_raw_memo(
        payload,
        eligible_instruments=eligible_instruments,
        active_anchor_ids=active_anchor_ids,
    )
    result: dict[str, Any] = {
        "schema_version": VALIDATED_MEMO_SCHEMA_VERSION,
        **_AUTHORITY_MARKERS,
        "source_binding": source_binding.to_dict(),
        "memo_input": {
            "byte_size": raw.byte_size,
            "file_sha256": raw.file_sha256,
            "normalized_text_sha256": _sha256(normalized_text.encode("utf-8")),
        },
        "normalized_memo": normalized_memo,
        "contract_validation": "VALID",
    }
    canonical = canonical_json_bytes(result)
    return ValidatedMemoEnvelope(
        payload=_deep_freeze(result),
        canonical_bytes=canonical,
        canonical_sha256=_sha256(canonical),
    )


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Return the deterministic compact JSON form used for R2F-1b identities."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_memo_text(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ReplacementMemoContractError("MEMO_UTF8_INVALID")
    failed = False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        failed = True
        text = ""
    if failed:
        raise ReplacementMemoContractError("MEMO_UTF8_INVALID") from None
    return text


def _normalize_newlines(value: str) -> str:
    """Use deterministic universal-newline semantics without changing raw identity."""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _parse_json_object(value: str) -> dict[str, Any]:
    failure_code: str | None = None
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except _DuplicateJsonKey:
        failure_code = "MEMO_DUPLICATE_KEY"
        parsed = None
    except (json.JSONDecodeError, ValueError):
        failure_code = "MEMO_JSON_INVALID"
        parsed = None
    if failure_code is not None:
        raise ReplacementMemoContractError(failure_code) from None
    if not isinstance(parsed, dict):
        raise ReplacementMemoContractError("MEMO_JSON_INVALID")
    return parsed


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError


def _validate_raw_memo(
    payload: Mapping[str, Any],
    *,
    eligible_instruments: tuple[EligibleInstrument, ...],
    active_anchor_ids: tuple[str, ...],
) -> dict[str, Any]:
    # Stable failure precedence: closure/schema, contradictions,
    # universe membership, evidence references, then rationale canonicality.
    if set(payload) != _RAW_MEMO_KEYS:
        raise ReplacementMemoContractError("MEMO_KEY_CLOSURE_INVALID")
    if payload.get("schema_version") != RAW_MEMO_SCHEMA_VERSION:
        raise ReplacementMemoContractError("MEMO_SCHEMA_UNSUPPORTED")
    _validate_nested_key_closure(payload)
    memo_result = payload.get("memo_result")
    confidence = payload.get("confidence")
    observations = payload.get("instrument_observations")
    if not isinstance(memo_result, str) or memo_result not in MEMO_RESULTS:
        raise ReplacementMemoContractError("MEMO_SCHEMA_UNSUPPORTED")
    if not isinstance(confidence, str) or confidence not in CONFIDENCE_VALUES:
        raise ReplacementMemoContractError("MEMO_SCHEMA_UNSUPPORTED")
    if not isinstance(observations, list):
        raise ReplacementMemoContractError("MEMO_SCHEMA_UNSUPPORTED")

    if memo_result == MEMO_RESULT_NO_TRADE:
        if observations:
            raise ReplacementMemoContractError("MEMO_RESULT_CONTRADICTORY")
        return {
            "memo_result": memo_result,
            "confidence": confidence,
            "instrument_observations": [],
        }

    if not 1 <= len(observations) <= MAXIMUM_OBSERVATIONS:
        raise ReplacementMemoContractError("MEMO_SCHEMA_UNSUPPORTED")

    structured = [_validate_observation_structure(row) for row in observations]
    categories = {item.instrument_id: item.universe_category for item in eligible_instruments}
    positions = {item.instrument_id: item.deterministic_position for item in eligible_instruments}
    allowed_anchor_ids = set(active_anchor_ids)

    # Universe membership and duplicate identifiers are validated as one stage
    # before any reference or prose validation.
    seen_instruments: set[str] = set()
    for row in structured:
        instrument_id = row["instrument_id"]
        if not _valid_instrument_id(instrument_id) or instrument_id not in categories:
            raise ReplacementMemoContractError("MEMO_IDENTIFIER_INVALID")
        if instrument_id in seen_instruments:
            raise ReplacementMemoContractError("MEMO_IDENTIFIER_INVALID")
        seen_instruments.add(instrument_id)

    normalized_references: dict[str, list[dict[str, str]]] = {}
    for row in structured:
        normalized_references[row["instrument_id"]] = _validate_references(
            row["evidence_references"],
            allowed_anchor_ids=allowed_anchor_ids,
        )

    normalized: list[dict[str, Any]] = []
    for row in structured:
        rationale = row["rationale"]
        _validate_rationale(rationale)
        assert isinstance(rationale, str)
        instrument_id = row["instrument_id"]
        normalized.append(
            {
                "instrument_id": instrument_id,
                "universe_category": categories[instrument_id],
                "research_view": row["research_view"],
                "rationale_utf8_sha256": _sha256(rationale.encode("utf-8")),
                "rationale_code_point_count": len(rationale),
                "evidence_references": normalized_references[instrument_id],
            }
        )
    normalized.sort(key=lambda row: positions[row["instrument_id"]])
    return {
        "memo_result": memo_result,
        "confidence": confidence,
        "instrument_observations": normalized,
    }


def _validate_nested_key_closure(payload: Mapping[str, Any]) -> None:
    observations = payload.get("instrument_observations")
    if not isinstance(observations, list):
        raise ReplacementMemoContractError("MEMO_SCHEMA_UNSUPPORTED")
    for row in observations:
        if not isinstance(row, Mapping) or set(row) != _OBSERVATION_KEYS:
            raise ReplacementMemoContractError("MEMO_KEY_CLOSURE_INVALID")
        references = row.get("evidence_references")
        if not isinstance(references, list):
            raise ReplacementMemoContractError("MEMO_EVIDENCE_REFERENCE_INVALID")
        for reference in references:
            if not isinstance(reference, Mapping) or set(reference) != _REFERENCE_KEYS:
                raise ReplacementMemoContractError("MEMO_KEY_CLOSURE_INVALID")


def _validate_observation_structure(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _OBSERVATION_KEYS:
        raise ReplacementMemoContractError("MEMO_KEY_CLOSURE_INVALID")
    instrument_id = value.get("instrument_id")
    research_view = value.get("research_view")
    rationale = value.get("rationale")
    references = value.get("evidence_references")
    if not isinstance(instrument_id, str):
        raise ReplacementMemoContractError("MEMO_IDENTIFIER_INVALID")
    if not isinstance(research_view, str) or research_view not in RESEARCH_VIEWS:
        raise ReplacementMemoContractError("MEMO_SCHEMA_UNSUPPORTED")
    return {
        "instrument_id": instrument_id,
        "research_view": research_view,
        "rationale": rationale,
        "evidence_references": references,
    }


def _validate_references(value: Any, *, allowed_anchor_ids: set[str]) -> list[dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAXIMUM_REFERENCES_PER_OBSERVATION:
        raise ReplacementMemoContractError("MEMO_EVIDENCE_REFERENCE_INVALID")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in value:
        if not isinstance(row, Mapping) or set(row) != _REFERENCE_KEYS:
            raise ReplacementMemoContractError("MEMO_KEY_CLOSURE_INVALID")
        namespace = row.get("namespace")
        evidence_id = row.get("evidence_id")
        if namespace != ACTIVE_ANCHOR_NAMESPACE or not _canonical_anchor_id(evidence_id):
            raise ReplacementMemoContractError("MEMO_EVIDENCE_REFERENCE_INVALID")
        assert isinstance(evidence_id, str)
        if evidence_id not in allowed_anchor_ids or evidence_id in seen:
            raise ReplacementMemoContractError("MEMO_EVIDENCE_REFERENCE_INVALID")
        seen.add(evidence_id)
        normalized.append({"namespace": ACTIVE_ANCHOR_NAMESPACE, "evidence_id": evidence_id})
    return sorted(normalized, key=lambda row: row["evidence_id"])


def _validate_rationale(value: Any) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= MAXIMUM_RATIONALE_CODEPOINTS:
        raise ReplacementMemoContractError("MEMO_SCHEMA_UNSUPPORTED")
    if value.strip() == "":
        raise ReplacementMemoContractError("MEMO_SCHEMA_UNSUPPORTED")
    if value != unicodedata.normalize("NFC", value):
        raise ReplacementMemoContractError("MEMO_SCHEMA_UNSUPPORTED")
    if "\r" in value or "\n" in value or any(
        unicodedata.category(char).startswith("C") or unicodedata.category(char) in {"Zl", "Zp"}
        for char in value
    ):
        raise ReplacementMemoContractError("MEMO_SCHEMA_UNSUPPORTED")


def _valid_instrument_id(value: Any) -> bool:
    return isinstance(value, str) and _IDENTIFIER_RE.fullmatch(value) is not None


def _canonical_anchor_id(value: Any) -> bool:
    return isinstance(value, str) and value != "" and value == value.strip()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
