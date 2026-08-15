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

STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION: Final = "step2_h1_render_commitment_v1"
STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION: Final = "step2_h1_capture_receipt_v1"
H1_LH2_PROMPT_CONTRACT_VERSION: Final = "h1_lh2_render_only_v1"

_SHA256_LOWERCASE_HEX_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


def _require_sha256_representation(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_LOWERCASE_HEX_PATTERN.match(value):
        raise ValueError(f"{field_name} must be a 64-character lowercase hex string.")
    return value


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_h1_render_commitment(
    commitment_path: Path,
    *,
    prompt_text: str,
    evidence_identities_sha256: list[str],
) -> None:
    """Construct and atomically write the deterministic H1 render commitment."""
    rendered_prompt_sha256 = _sha256_bytes(prompt_text.encode("utf-8"))

    payload = {
        "schema_version": STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION,
        "prompt_contract_version": H1_LH2_PROMPT_CONTRACT_VERSION,
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
    if prompt_contract_version != H1_LH2_PROMPT_CONTRACT_VERSION:
        raise ValueError(
            f"Invalid prompt contract version. Expected "
            f"'{H1_LH2_PROMPT_CONTRACT_VERSION}', got '{prompt_contract_version}'"
        )
        
    expected_prompt_sha256 = _require_sha256_representation(
        commitment["rendered_prompt_sha256"],
        "Render commitment 'rendered_prompt_sha256'"
    )

    evidence_ids = commitment["evidence_entry_identities_sha256"]
    if not isinstance(evidence_ids, list) or not evidence_ids:
        raise ValueError("Render commitment 'evidence_entry_identities_sha256' must be a non-empty list.")
    
    seen_ids = set()
    for eid in evidence_ids:
        _require_sha256_representation(eid, "Evidence entry identity")
        if eid in seen_ids:
            raise ValueError(f"Duplicate evidence entry identity: {eid}")
        seen_ids.add(eid)

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

    # 3. Read exact raw_output.txt bytes
    if not file_exists(raw_output_path):
        raise FileNotFoundError(f"Raw output file is missing: {raw_output_path}")
        
    raw_response_bytes = raw_output_path.read_bytes()
    if not raw_response_bytes:
        raise ValueError("Raw output file is empty. Please provide the exact raw response bytes.")
        
    raw_response_sha256 = _sha256_bytes(raw_response_bytes)

    # 4. Write exact capture receipt
    receipt = {
        "schema_version": STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION,
        "rendered_prompt_sha256": expected_prompt_sha256,
        "raw_response_sha256": raw_response_sha256,
    }
    write_json(receipt_path, receipt)
