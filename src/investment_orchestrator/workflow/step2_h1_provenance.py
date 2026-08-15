"""Provenance contracts and manual capture for Step 2 H1 qualitative responses.

This module owns the exact deterministic provenance byte identities and the
explicit manual response-capture operation. It grants NO parsing, investment,
state, freshness, or downstream authority.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

import re

from investment_orchestrator.common.io import file_exists, read_json, write_json
from investment_orchestrator.mmi.canonical import MAXIMUM_MMI_RAW_RESPONSE_BYTES

STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION: Final = "step2_h1_render_commitment_v1"
STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION: Final = "step2_h1_capture_receipt_v1"

H1_LH2_RENDER_ONLY_PROMPT_CONTRACT_VERSION: Final = "h1_lh2_render_only_v1"
H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION: Final = "h1_lh2_structured_report_v1"

_RECOGNIZED_H1_PROMPT_CONTRACT_VERSIONS: Final = frozenset({
    H1_LH2_RENDER_ONLY_PROMPT_CONTRACT_VERSION,
    H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION,
})

_SHA256_LOWERCASE_HEX_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


def require_sha256_representation(value: object, field_name: str) -> str:
    """Require exactly a 64-character lowercase hex string."""
    if not isinstance(value, str) or not _SHA256_LOWERCASE_HEX_PATTERN.match(value):
        raise ValueError(f"{field_name} must be a 64-character lowercase hex string.")
    return value


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_and_validate_h1_render_commitment(commitment_path: Path) -> dict[str, object]:
    """Read and structurally validate an H1 render commitment."""
    if not file_exists(commitment_path):
        raise FileNotFoundError(f"H1 render commitment is missing: {commitment_path}")
    
    commitment = read_json(commitment_path)
    if not isinstance(commitment, dict):
        raise ValueError("H1 render commitment must be a JSON object.")
        
    expected_keys = {
        "schema_version",
        "prompt_contract_version",
        "rendered_prompt_sha256",
        "evidence_entry_identities_sha256",
    }
    if set(commitment.keys()) != expected_keys:
        raise ValueError(f"H1 render commitment must contain exactly {expected_keys}")

    schema_version = commitment["schema_version"]
    if schema_version != STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION:
        raise ValueError(
            f"Invalid render commitment schema version. Expected "
            f"'{STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION}', got '{schema_version}'"
        )
        
    prompt_contract_version = commitment["prompt_contract_version"]
    if prompt_contract_version not in _RECOGNIZED_H1_PROMPT_CONTRACT_VERSIONS:
        raise ValueError(
            f"Unrecognized prompt contract version: '{prompt_contract_version}'"
        )
        
    require_sha256_representation(
        commitment["rendered_prompt_sha256"],
        "Render commitment 'rendered_prompt_sha256'"
    )

    evidence_ids = commitment["evidence_entry_identities_sha256"]
    if not isinstance(evidence_ids, list) or not evidence_ids:
        raise ValueError("Render commitment 'evidence_entry_identities_sha256' must be a non-empty list.")
    
    seen_ids = set()
    for eid in evidence_ids:
        require_sha256_representation(eid, "Evidence entry identity")
        if eid in seen_ids:
            raise ValueError(f"Duplicate evidence entry identity: {eid}")
        seen_ids.add(eid)

    return commitment


def read_and_validate_h1_capture_receipt(receipt_path: Path) -> dict[str, object]:
    """Read and structurally validate an H1 capture receipt."""
    if not file_exists(receipt_path):
        raise FileNotFoundError(f"H1 capture receipt is missing: {receipt_path}")

    receipt = read_json(receipt_path)
    if not isinstance(receipt, dict):
        raise ValueError("H1 capture receipt must be a JSON object.")

    expected_keys = {
        "schema_version",
        "rendered_prompt_sha256",
        "raw_response_sha256",
    }
    if set(receipt.keys()) != expected_keys:
        raise ValueError(f"H1 capture receipt must contain exactly {expected_keys}")

    schema_version = receipt["schema_version"]
    if schema_version != STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION:
        raise ValueError(
            f"Invalid capture receipt schema version. Expected "
            f"'{STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION}', got '{schema_version}'"
        )

    require_sha256_representation(receipt["rendered_prompt_sha256"], "Capture receipt 'rendered_prompt_sha256'")
    require_sha256_representation(receipt["raw_response_sha256"], "Capture receipt 'raw_response_sha256'")

    return receipt


def read_exact_raw_response_bytes(raw_output_path: Path) -> bytes:
    """Resource-safe exact-byte read for the manual LLM response boundary."""
    if not raw_output_path.is_file():
        raise FileNotFoundError(f"Raw output file is missing or not a file: {raw_output_path}")

    # Pre-read size guard to prevent DOS allocation
    st_size = raw_output_path.stat().st_size
    if not (1 <= st_size <= MAXIMUM_MMI_RAW_RESPONSE_BYTES):
        raise ValueError(
            f"Raw response byte size {st_size} is outside the allowed bounds "
            f"(1..{MAXIMUM_MMI_RAW_RESPONSE_BYTES})."
        )

    raw_bytes = raw_output_path.read_bytes()

    # Post-read size guard to close stat/read race
    actual_len = len(raw_bytes)
    if not (1 <= actual_len <= MAXIMUM_MMI_RAW_RESPONSE_BYTES):
        raise ValueError(
            f"Raw response actual byte size {actual_len} is outside the allowed bounds "
            f"(1..{MAXIMUM_MMI_RAW_RESPONSE_BYTES})."
        )

    return raw_bytes


def write_h1_render_commitment(
    commitment_path: Path,
    *,
    prompt_text: str,
    prompt_contract_version: str,
    evidence_identities_sha256: list[str],
) -> None:
    """Construct and atomically write the deterministic H1 render commitment."""
    if prompt_contract_version not in _RECOGNIZED_H1_PROMPT_CONTRACT_VERSIONS:
        raise ValueError(f"Cannot write unrecognized prompt contract version: '{prompt_contract_version}'")

    rendered_prompt_sha256 = _sha256_bytes(prompt_text.encode("utf-8"))

    payload = {
        "schema_version": STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION,
        "prompt_contract_version": prompt_contract_version,
        "rendered_prompt_sha256": rendered_prompt_sha256,
        "evidence_entry_identities_sha256": evidence_identities_sha256,
    }
    write_json(commitment_path, payload)


def capture_h1_response(
    *,
    commitment_path: Path,
    prompt_path: Path,
    raw_output_path: Path,
    receipt_path: Path,
) -> None:
    """Explicitly associate the manual raw response bytes with the render commitment.

    This is a strictly manual provenance boundary. It performs no semantic
    parsing, JSON validation, freshness checking, or state mutation.
    """
    # 1. Read and validate fixed render commitment
    commitment = read_and_validate_h1_render_commitment(commitment_path)
    expected_prompt_sha256 = commitment["rendered_prompt_sha256"]

    # 2. Read exact current prompt.txt bytes and verify hash
    if not file_exists(prompt_path):
        raise FileNotFoundError(f"Prompt file is missing: {prompt_path}")

    prompt_bytes = prompt_path.read_bytes()
    current_prompt_sha256 = _sha256_bytes(prompt_bytes)
    if current_prompt_sha256 != expected_prompt_sha256:
        raise ValueError(
            "Prompt hash mismatch. The current prompt.txt does not match the render "
            "commitment. A new render may have occurred or the file was modified."
        )

    # 3. Read exact raw_output.txt bytes with resource safety
    raw_response_bytes = read_exact_raw_response_bytes(raw_output_path)
    raw_response_sha256 = _sha256_bytes(raw_response_bytes)

    # 4. Write exact capture receipt
    receipt = {
        "schema_version": STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION,
        "rendered_prompt_sha256": expected_prompt_sha256,
        "raw_response_sha256": raw_response_sha256,
    }
    write_json(receipt_path, receipt)
