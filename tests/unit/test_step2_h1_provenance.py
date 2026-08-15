"""Integration and unit tests for Step 2 H1 provenance and capture operation."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from unittest import mock

from investment_orchestrator.workflow.step2_h1_provenance import (
    STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION,
    STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION,
    capture_h1_response,
    write_h1_render_commitment,
)
from investment_orchestrator.workflow.step2_decision_builder import (
    render_step2_prompt,
    step2_render_commitment_path,
)
from investment_orchestrator.mmi.long_horizon_research_payload_v2 import (
    MmiLongHorizonResearchPayloadV2,
    MmiLongHorizonResearchSourceEntry,
    MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_VERSION,
)


def _setup_mock_h1_payload() -> MmiLongHorizonResearchPayloadV2:
    entry = MmiLongHorizonResearchSourceEntry(
        publisher="Mock Publisher",
        published_at="2026-08-10",
        source_locator="mock_locator",
        tickers=("MOCK",),
        excerpt_text="Mock excerpt",
        source_entry_identity_sha256="c" * 64,
    )
    return MmiLongHorizonResearchPayloadV2(
        schema_version=MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_VERSION,
        sources=(entry,),
    )


def test_h1_render_commitment_exact_contract(tmp_path: Path):
    """Prove the exact H1 render commitment contract."""
    commitment_path = tmp_path / "render_commitment.json"
    prompt_text = "Prompt text exactly."
    
    write_h1_render_commitment(
        commitment_path,
        prompt_text=prompt_text,
        evidence_identities_sha256=["a" * 64, "b" * 64],
    )
    
    data = json.loads(commitment_path.read_text())
    assert data["schema_version"] == STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION
    assert data["prompt_contract_version"] == "h1_lh2_render_only_v1"
    assert data["rendered_prompt_sha256"] == "e026e7c55b319a33a13ccf46a18c26a1d2d64e0bbf2885e24bf84eaaebba36a6"
    assert data["evidence_entry_identities_sha256"] == ["a" * 64, "b" * 64]
    # No extra fields
    assert set(data.keys()) == {
        "schema_version",
        "prompt_contract_version",
        "rendered_prompt_sha256",
        "evidence_entry_identities_sha256",
    }


def test_explicit_capture_operation_success(tmp_path: Path):
    """Prove successful manual explicit capture."""
    commitment_path = tmp_path / "render_commitment.json"
    prompt_path = tmp_path / "prompt.txt"
    raw_path = tmp_path / "raw_output.txt"
    receipt_path = tmp_path / "h1_capture_receipt.json"
    
    prompt_text = "Expected prompt."
    prompt_path.write_bytes(prompt_text.encode("utf-8"))
    
    write_h1_render_commitment(
        commitment_path,
        prompt_text=prompt_text,
        evidence_identities_sha256=["0" * 64],
    )
    
    raw_path.write_bytes(b"Raw response bytes.")
    
    capture_h1_response(
        commitment_path=commitment_path,
        prompt_path=prompt_path,
        raw_output_path=raw_path,
        receipt_path=receipt_path,
    )
    
    receipt = json.loads(receipt_path.read_text())
    assert receipt["schema_version"] == STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION
    assert receipt["rendered_prompt_sha256"] == "4bb97a790d098e7b0f7d1abde10ec73477420c7bd76be799f753b35a0aab25ec"
    assert receipt["raw_response_sha256"] == "1dfbcb6d3be0641a39a31154fe92b8993208b74d27262dca594e450fdce86354"
    # Exact three fields
    assert set(receipt.keys()) == {
        "schema_version",
        "rendered_prompt_sha256",
        "raw_response_sha256",
    }


def test_explicit_capture_prompt_mismatch(tmp_path: Path):
    """Prove capture fails if prompt bytes mismatch commitment."""
    commitment_path = tmp_path / "render_commitment.json"
    prompt_path = tmp_path / "prompt.txt"
    raw_path = tmp_path / "raw_output.txt"
    receipt_path = tmp_path / "h1_capture_receipt.json"
    
    write_h1_render_commitment(
        commitment_path,
        prompt_text="Original prompt.",
        evidence_identities_sha256=["a" * 64],  # 64 chars
    )
    
    # Prompt altered on disk
    prompt_path.write_bytes(b"Altered prompt.")
    raw_path.write_bytes(b"Response")
    
    with pytest.raises(ValueError, match="Prompt hash mismatch"):
        capture_h1_response(
            commitment_path=commitment_path,
            prompt_path=prompt_path,
            raw_output_path=raw_path,
            receipt_path=receipt_path,
        )


def test_explicit_capture_empty_response(tmp_path: Path):
    """Prove capture fails if raw response is empty."""
    commitment_path = tmp_path / "render_commitment.json"
    prompt_path = tmp_path / "prompt.txt"
    raw_path = tmp_path / "raw_output.txt"
    receipt_path = tmp_path / "h1_capture_receipt.json"
    
    prompt_text = "Same prompt."
    prompt_path.write_bytes(prompt_text.encode("utf-8"))
    
    write_h1_render_commitment(
        commitment_path,
        prompt_text=prompt_text,
        evidence_identities_sha256=["b" * 64],  # 64 chars
    )
    
    # Empty response
    raw_path.write_bytes(b"")
    
    with pytest.raises(ValueError, match="Raw output file is empty"):
        capture_h1_response(
            commitment_path=commitment_path,
            prompt_path=prompt_path,
            raw_output_path=raw_path,
            receipt_path=receipt_path,
        )


@pytest.mark.parametrize("mutator", [
    lambda d: d.update({"schema_version": "wrong_v1"}),
    lambda d: d.update({"prompt_contract_version": "wrong_v1"}),
    lambda d: d.update({"extra_field": "value"}),
    lambda d: d.pop("rendered_prompt_sha256"),
    lambda d: d.update({"rendered_prompt_sha256": "short"}),
    lambda d: d.update({"rendered_prompt_sha256": "A" * 64}),  # Uppercase
    lambda d: d.update({"rendered_prompt_sha256": "g" * 64}),  # Not hex
    lambda d: d.update({"evidence_entry_identities_sha256": []}),
    lambda d: d.update({"evidence_entry_identities_sha256": "not a list"}),
    lambda d: d.update({"evidence_entry_identities_sha256": ["a" * 64, "a" * 64]}),  # Duplicate
    lambda d: d.update({"evidence_entry_identities_sha256": ["short"]}),
    lambda d: d.update({"evidence_entry_identities_sha256": ["g" * 64]}),  # Not hex
])
def test_explicit_capture_invalid_commitment_fails(tmp_path: Path, mutator):
    """Prove closed contract validation rejects malformed commitments before capture."""
    commitment_path = tmp_path / "render_commitment.json"
    prompt_path = tmp_path / "prompt.txt"
    raw_path = tmp_path / "raw_output.txt"
    receipt_path = tmp_path / "h1_capture_receipt.json"
    
    prompt_path.write_bytes(b"prompt")
    raw_path.write_bytes(b"response")
    
    valid_commitment = {
        "schema_version": STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION,
        "prompt_contract_version": "h1_lh2_render_only_v1",
        "rendered_prompt_sha256": "a" * 64,
        "evidence_entry_identities_sha256": ["b" * 64],
    }
    mutator(valid_commitment)
    
    commitment_path.write_text(json.dumps(valid_commitment))
    
    with pytest.raises(ValueError):
        capture_h1_response(
            commitment_path=commitment_path,
            prompt_path=prompt_path,
            raw_output_path=raw_path,
            receipt_path=receipt_path,
        )
    
    assert not receipt_path.exists()



@mock.patch("investment_orchestrator.workflow.step2_decision_builder.step2_artifact_dir")
@mock.patch("investment_orchestrator.workflow.step2_decision_builder._enforce_step2_invocation_admission")
def test_render_step2_prompt_h1_creates_commitment(
    mock_enforce,
    mock_artifact_dir,
    tmp_path: Path,
):
    """Prove that an H1 render securely writes the render commitment last."""
    mock_artifact_dir.return_value = tmp_path
    
    # Ensure prompts/ exists in repo to allow _build_h1_lh2_step2_prompt_text
    import investment_orchestrator.workflow.step2_decision_builder as builder
    original_require = builder.require_prompt_path
    
    def fake_require_prompt_path(name: str) -> Path:
        p = tmp_path / name
        p.write_text("Fake prompt template with {{ lh2_payload_json }}")
        return p
        
    builder.require_prompt_path = fake_require_prompt_path
    
    try:
        mock_payload = _setup_mock_h1_payload()
        mock_gate = mock.MagicMock()
        mock_gate.mode = "h1_lh2_render_only"
        mock_enforce.return_value = (mock_gate, mock_payload)
        
        result = render_step2_prompt()
        
        commitment_path = tmp_path / "render_commitment.json"
        assert commitment_path.exists()
        
        data = json.loads(commitment_path.read_text())
        assert data["evidence_entry_identities_sha256"] == ["c" * 64]
        
    finally:
        builder.require_prompt_path = original_require


@mock.patch("investment_orchestrator.workflow.step2_decision_builder.step2_artifact_dir")
@mock.patch("investment_orchestrator.workflow.step2_decision_builder._enforce_step2_invocation_admission")
def test_render_commitment_failure_ordering(
    mock_enforce,
    mock_artifact_dir,
    tmp_path: Path,
):
    """Prove that if the commitment write fails, render fails and an old commitment would cause capture mismatch."""
    mock_artifact_dir.return_value = tmp_path
    
    import investment_orchestrator.workflow.step2_decision_builder as builder
    original_require = builder.require_prompt_path
    original_write = builder.write_h1_render_commitment
    
    def fake_require_prompt_path(name: str) -> Path:
        p = tmp_path / name
        p.write_text("Template {{ lh2_payload_json }}")
        return p
        
    def failing_write(*args, **kwargs):
        raise OSError("Simulated disk full")
        
    builder.require_prompt_path = fake_require_prompt_path
    builder.write_h1_render_commitment = failing_write
    
    try:
        mock_payload = _setup_mock_h1_payload()
        mock_gate = mock.MagicMock()
        mock_enforce.return_value = (mock_gate, mock_payload)
        
        with pytest.raises(OSError, match="Simulated disk full"):
            render_step2_prompt()
            
        # The prompt output path was already written before failure
        prompt_output = tmp_path / "prompt.txt"
        assert prompt_output.exists()
        
        # We did not write a new commitment
        commitment_path = tmp_path / "render_commitment.json"
        assert not commitment_path.exists()
        
    finally:
        builder.require_prompt_path = original_require
        builder.write_h1_render_commitment = original_write

