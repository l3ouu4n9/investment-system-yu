"""Structured qualitative report generation and validation for H1 Step 2.

This module owns the exact strict parser for the H1 qualitative JSON schema
and the deterministic workflow to bind it to its provenance constraints and
produce a report-only artifact. It grants NO actionable investment authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Final, Any

from investment_orchestrator.common.io import read_text, write_json
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi.canonical import MAXIMUM_MMI_RAW_RESPONSE_BYTES
from investment_orchestrator.workflow.step2_decision_builder import (
    step2_artifact_dir,
    step2_render_commitment_path,
    step2_h1_capture_receipt_path,
    step2_prompt_path,
    step2_raw_output_path,
)
from investment_orchestrator.workflow.step2_h1_provenance import (
    H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION,
    require_sha256_representation,
    read_and_validate_h1_render_commitment,
    read_and_validate_h1_capture_receipt,
    read_exact_raw_response_bytes,
)

STEP2_H1_QUALITATIVE_RESPONSE_SCHEMA_VERSION: Final = "step2_h1_qualitative_response_v1"
STEP2_H1_QUALITATIVE_REPORT_SCHEMA_VERSION: Final = "step2_h1_qualitative_report_v1"

_QUALITATIVE_FIELDS: Final = frozenset({
    "long_horizon_opportunity",
    "valuation_context",
    "portfolio_contribution",
    "evidence_integrity",
    "prior_thesis_change",
})


@dataclass(frozen=True, slots=True)
class H1QualitativeResponse:
    """Immutable parsed result of the H1 qualitative response."""
    schema_version: str
    long_horizon_opportunity: str
    valuation_context: str
    portfolio_contribution: str
    evidence_integrity: str
    prior_thesis_change: str
    evidence_references: tuple[str, ...]


def _reject_duplicates(ordered_pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Strict JSON object pairs hook to reject duplicate keys."""
    out = {}
    for key, value in ordered_pairs:
        if key in out:
            raise ValueError(f"Duplicate JSON key rejected: {key!r}")
        out[key] = value
    return out


def _reject_invalid_floats(value: str) -> float:
    """Strict JSON float hook to reject NaN, Infinity, -Infinity."""
    fval = float(value)
    if math.isnan(fval) or math.isinf(fval):
        raise ValueError(f"NaN/Infinity rejected: {value}")
    return fval


def parse_h1_qualitative_response(
    raw_bytes: bytes,
    allowed_evidence_shas: frozenset[str],
) -> H1QualitativeResponse:
    """Parse exact raw LLM bytes into an immutable structured qualitative response."""
    # 1. Independent parser byte limit
    raw_len = len(raw_bytes)
    if not (1 <= raw_len <= MAXIMUM_MMI_RAW_RESPONSE_BYTES):
        raise ValueError(
            f"Parser rejected raw response of size {raw_len} outside bounds "
            f"(1..{MAXIMUM_MMI_RAW_RESPONSE_BYTES})"
        )

    # 2. UTF-8 decode (strict, no replacement)
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise ValueError("Parser rejected raw response: starts with UTF-8 BOM")
    text = raw_bytes.decode("utf-8", errors="strict")

    # 3. Strict JSON parse
    # Stdlib `json.loads` natively ignores leading/trailing JSON whitespace but rejects trailing text
    try:
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_float=_reject_invalid_floats,
            parse_constant=lambda _: _reject_invalid_floats("NaN"), # Catch raw NaN/Infinity tokens
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid strict JSON grammar: {exc}") from None

    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object.")

    # 4. Schema validation
    expected_keys = {"schema_version", "evidence_references"} | _QUALITATIVE_FIELDS
    if set(data.keys()) != expected_keys:
        raise ValueError(f"Exact schema keys required: {expected_keys}")

    if data["schema_version"] != STEP2_H1_QUALITATIVE_RESPONSE_SCHEMA_VERSION:
        raise ValueError(
            f"Invalid schema_version. Expected '{STEP2_H1_QUALITATIVE_RESPONSE_SCHEMA_VERSION}', "
            f"got '{data['schema_version']}'"
        )

    # 5. Qualitative strings validation
    for field in _QUALITATIVE_FIELDS:
        val = data[field]
        if type(val) is not str:
            raise ValueError(f"Field {field} must be a string.")
        if not val.strip():
            raise ValueError(f"Field {field} must contain non-whitespace text.")

    # 6. Evidence references validation
    raw_refs = data["evidence_references"]
    if not isinstance(raw_refs, list) or not raw_refs:
        raise ValueError("Field evidence_references must be a non-empty list.")

    seen_refs = set()
    for ref in raw_refs:
        require_sha256_representation(ref, "Evidence reference")
        if ref in seen_refs:
            raise ValueError(f"Duplicate evidence reference: {ref}")
        if ref not in allowed_evidence_shas:
            raise ValueError(f"Unknown or unauthorized evidence reference: {ref}")
        seen_refs.add(ref)

    return H1QualitativeResponse(
        schema_version=data["schema_version"],
        long_horizon_opportunity=data["long_horizon_opportunity"],
        valuation_context=data["valuation_context"],
        portfolio_contribution=data["portfolio_contribution"],
        evidence_integrity=data["evidence_integrity"],
        prior_thesis_change=data["prior_thesis_change"],
        evidence_references=tuple(raw_refs),
    )


def read_and_validate_h1_report(report_path: Path) -> dict[str, Any]:
    """Read and strict-validate the persisted H1 qualitative report."""
    try:
        data = json.loads(
            read_text(report_path),
            object_pairs_hook=_reject_duplicates,
        )
    except FileNotFoundError:
        raise FileNotFoundError(f"Missing H1 qualitative report at {report_path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed H1 qualitative report JSON: {exc}") from None

    if not isinstance(data, dict):
        raise ValueError("Report JSON root must be an object.")

    expected_keys = {
        "schema_version",
        "prompt_contract_version",
        "rendered_prompt_sha256",
        "raw_response_sha256",
        "evidence_references",
    } | _QUALITATIVE_FIELDS

    if set(data.keys()) != expected_keys:
        raise ValueError(f"Exact report schema keys required: {expected_keys}")

    if data["schema_version"] != STEP2_H1_QUALITATIVE_REPORT_SCHEMA_VERSION:
        raise ValueError(
            f"Invalid schema_version. Expected '{STEP2_H1_QUALITATIVE_REPORT_SCHEMA_VERSION}', "
            f"got '{data['schema_version']}'"
        )

    if data["prompt_contract_version"] != H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION:
        raise ValueError(
            f"Unsupported prompt contract version '{data['prompt_contract_version']}' "
            "for structured report validation."
        )

    require_sha256_representation(data["rendered_prompt_sha256"], "rendered_prompt_sha256")
    require_sha256_representation(data["raw_response_sha256"], "raw_response_sha256")

    for field in _QUALITATIVE_FIELDS:
        val = data[field]
        if type(val) is not str:
            raise ValueError(f"Field {field} must be a string.")
        if not val.strip():
            raise ValueError(f"Field {field} must contain non-whitespace text.")

    raw_refs = data["evidence_references"]
    if not isinstance(raw_refs, list) or not raw_refs:
        raise ValueError("Field evidence_references must be a non-empty list.")

    seen_refs = set()
    for ref in raw_refs:
        if type(ref) is not str:
            raise ValueError("evidence_references entries must be strings.")
        require_sha256_representation(ref, "Evidence reference")
        if ref in seen_refs:
            raise ValueError(f"Duplicate evidence reference: {ref}")
        seen_refs.add(ref)

    return data


def validate_h1_report_workflow() -> None:
    """Explicitly validate an H1 response and persist a non-actionable report."""
    commitment_path = step2_render_commitment_path()
    receipt_path = step2_h1_capture_receipt_path()
    prompt_path = step2_prompt_path()
    raw_output_path = step2_raw_output_path()
    report_path = step2_artifact_dir() / "h1_qualitative_report.json"

    # 1. Read and validate render commitment
    commitment = read_and_validate_h1_render_commitment(commitment_path)
    prompt_contract_version = commitment["prompt_contract_version"]
    if prompt_contract_version != H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION:
        raise ValueError(
            f"Unsupported prompt contract version '{prompt_contract_version}' "
            "for structured report validation."
        )

    expected_prompt_sha256 = commitment["rendered_prompt_sha256"]

    # 2. Read exact prompt and check SHA
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Prompt file missing: {prompt_path}")
    current_prompt_sha256 = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    if current_prompt_sha256 != expected_prompt_sha256:
        raise ValueError("Prompt SHA mismatch against render commitment.")

    # 3. Read and validate capture receipt
    receipt = read_and_validate_h1_capture_receipt(receipt_path)
    if receipt["rendered_prompt_sha256"] != expected_prompt_sha256:
        raise ValueError("Receipt prompt SHA mismatch against render commitment.")

    # 4. Read bounded exact raw response
    raw_bytes = read_exact_raw_response_bytes(raw_output_path)

    # 5. Check raw response SHA
    raw_response_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if raw_response_sha256 != receipt["raw_response_sha256"]:
        raise ValueError("Raw response SHA mismatch against capture receipt.")

    # 6. Parse structured response
    allowed_shas = frozenset(commitment["evidence_entry_identities_sha256"])
    parsed = parse_h1_qualitative_response(raw_bytes, allowed_shas)

    # 7. Construct report
    report = {
        "schema_version": STEP2_H1_QUALITATIVE_REPORT_SCHEMA_VERSION,
        "prompt_contract_version": H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION,
        "rendered_prompt_sha256": expected_prompt_sha256,
        "raw_response_sha256": raw_response_sha256,
        "long_horizon_opportunity": parsed.long_horizon_opportunity,
        "valuation_context": parsed.valuation_context,
        "portfolio_contribution": parsed.portfolio_contribution,
        "evidence_integrity": parsed.evidence_integrity,
        "prior_thesis_change": parsed.prior_thesis_change,
        "evidence_references": list(parsed.evidence_references),
    }

    # 8. Atomically persist report
    write_json(report_path, report)
