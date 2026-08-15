"""Unit tests for the Step 2 H1 qualitative report parser and workflow."""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest import mock
import pytest

from investment_orchestrator.workflow.step2_h1_report import (
    parse_h1_qualitative_response,
    validate_h1_report_workflow,
    STEP2_H1_QUALITATIVE_RESPONSE_SCHEMA_VERSION,
    STEP2_H1_QUALITATIVE_REPORT_SCHEMA_VERSION,
)
from investment_orchestrator.workflow.step2_h1_provenance import (
    STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION,
    STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION,
    H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION,
    _sha256_bytes,
)
from investment_orchestrator.mmi.canonical import MAXIMUM_MMI_RAW_RESPONSE_BYTES


def _valid_response_dict() -> dict:
    return {
        "schema_version": STEP2_H1_QUALITATIVE_RESPONSE_SCHEMA_VERSION,
        "long_horizon_opportunity": "Good opportunity.",
        "valuation_context": "Fairly valued.",
        "portfolio_contribution": "Diversification.",
        "evidence_integrity": "Evidence is solid.",
        "prior_thesis_change": "No change.",
        "evidence_references": ["a" * 64],
    }


def _valid_response_bytes() -> bytes:
    return json.dumps(_valid_response_dict()).encode("utf-8")


# --- Parser Tests ---

def test_valid_structured_response_accepted():
    """Test I: Valid structured response accepted."""
    parsed = parse_h1_qualitative_response(
        _valid_response_bytes(),
        frozenset({"a" * 64, "b" * 64})
    )
    assert parsed.long_horizon_opportunity == "Good opportunity."
    assert parsed.evidence_references == ("a" * 64,)


def test_duplicate_decoded_key_rejected():
    """Test J: duplicate decoded key rejected."""
    raw = b'{"schema_version": "v1", "schema_version": "v2"}'
    with pytest.raises(ValueError, match="Duplicate JSON key rejected: 'schema_version'"):
        parse_h1_qualitative_response(raw, frozenset())


def test_escaped_equivalent_duplicate_key_rejected():
    """Test K: escaped-equivalent duplicate key rejected."""
    # \\u0073 is 's'
    raw = b'{"schema_version": "v1", "\\u0073chema_version": "v2"}'
    with pytest.raises(ValueError, match="Duplicate JSON key rejected: 'schema_version'"):
        parse_h1_qualitative_response(raw, frozenset())


@pytest.mark.parametrize("bad_val", ["NaN", "Infinity", "-Infinity"])
def test_nan_infinity_representative_rejection(bad_val):
    """Test L: NaN/Infinity representative rejection."""
    raw = f'{{"schema_version": {bad_val}}}'.encode("utf-8")
    with pytest.raises(ValueError, match="NaN/Infinity rejected"):
        parse_h1_qualitative_response(raw, frozenset())


def test_missing_extra_key_representative_rejection():
    """Test M: missing/extra key representative rejection."""
    d = _valid_response_dict()
    del d["long_horizon_opportunity"]
    with pytest.raises(ValueError, match="Exact schema keys required"):
        parse_h1_qualitative_response(json.dumps(d).encode("utf-8"), frozenset({"a" * 64}))

    d2 = _valid_response_dict()
    d2["extra"] = "value"
    with pytest.raises(ValueError, match="Exact schema keys required"):
        parse_h1_qualitative_response(json.dumps(d2).encode("utf-8"), frozenset({"a" * 64}))


@pytest.mark.parametrize("bad_str", ["", "   ", "\n\t"])
def test_whitespace_only_qualitative_field_rejected(bad_str):
    """Test N: whitespace-only qualitative field rejected."""
    d = _valid_response_dict()
    d["long_horizon_opportunity"] = bad_str
    with pytest.raises(ValueError, match="must contain non-whitespace text"):
        parse_h1_qualitative_response(json.dumps(d).encode("utf-8"), frozenset({"a" * 64}))


def test_evidence_refs_validation():
    """Test O: evidence refs valid subset accepted, various malformed rejected."""
    # Empty
    d = _valid_response_dict()
    d["evidence_references"] = []
    with pytest.raises(ValueError, match="must be a non-empty list"):
        parse_h1_qualitative_response(json.dumps(d).encode("utf-8"), frozenset({"a" * 64}))

    # Malformed (short)
    d["evidence_references"] = ["abc"]
    with pytest.raises(ValueError, match="Evidence reference must be a 64-character lowercase"):
        parse_h1_qualitative_response(json.dumps(d).encode("utf-8"), frozenset({"a" * 64}))

    # Duplicate
    d["evidence_references"] = ["a" * 64, "a" * 64]
    with pytest.raises(ValueError, match="Duplicate evidence reference"):
        parse_h1_qualitative_response(json.dumps(d).encode("utf-8"), frozenset({"a" * 64}))

    # Unknown
    d["evidence_references"] = ["b" * 64]
    with pytest.raises(ValueError, match="Unknown or unauthorized evidence reference"):
        parse_h1_qualitative_response(json.dumps(d).encode("utf-8"), frozenset({"a" * 64}))


# --- Workflow Tests ---

@pytest.fixture
def workflow_paths(tmp_path):
    commitment_path = tmp_path / "render_commitment.json"
    receipt_path = tmp_path / "h1_capture_receipt.json"
    prompt_path = tmp_path / "prompt.txt"
    raw_path = tmp_path / "raw_output.txt"
    report_path = tmp_path / "h1_qualitative_report.json"

    prompt_text = "Structured prompt."
    prompt_bytes = prompt_text.encode("utf-8")
    prompt_path.write_bytes(prompt_bytes)
    prompt_sha256 = _sha256_bytes(prompt_bytes)

    raw_bytes = _valid_response_bytes()
    raw_path.write_bytes(raw_bytes)
    raw_sha256 = _sha256_bytes(raw_bytes)

    commitment_path.write_text(json.dumps({
        "schema_version": STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION,
        "prompt_contract_version": H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION,
        "rendered_prompt_sha256": prompt_sha256,
        "evidence_entry_identities_sha256": ["a" * 64],
    }))

    receipt_path.write_text(json.dumps({
        "schema_version": STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION,
        "rendered_prompt_sha256": prompt_sha256,
        "raw_response_sha256": raw_sha256,
    }))

    return {
        "tmp_path": tmp_path,
        "commitment_path": commitment_path,
        "receipt_path": receipt_path,
        "prompt_path": prompt_path,
        "raw_path": raw_path,
        "report_path": report_path,
        "prompt_sha256": prompt_sha256,
        "raw_sha256": raw_sha256,
    }


@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_artifact_dir")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_render_commitment_path")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_h1_capture_receipt_path")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_prompt_path")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_raw_output_path")
def test_valid_exact_binding_writes_report(
    mock_raw, mock_prompt, mock_receipt, mock_commitment, mock_artifact, workflow_paths
):
    """Test S: valid exact binding atomically writes exact report-only artifact."""
    mock_artifact.return_value = workflow_paths["tmp_path"]
    mock_commitment.return_value = workflow_paths["commitment_path"]
    mock_receipt.return_value = workflow_paths["receipt_path"]
    mock_prompt.return_value = workflow_paths["prompt_path"]
    mock_raw.return_value = workflow_paths["raw_path"]

    validate_h1_report_workflow()

    report_path = workflow_paths["report_path"]
    assert report_path.exists()

    report = json.loads(report_path.read_text())
    assert report["schema_version"] == STEP2_H1_QUALITATIVE_REPORT_SCHEMA_VERSION
    assert report["prompt_contract_version"] == H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION
    assert report["rendered_prompt_sha256"] == workflow_paths["prompt_sha256"]
    assert report["raw_response_sha256"] == workflow_paths["raw_sha256"]
    assert report["long_horizon_opportunity"] == "Good opportunity."
    assert report["evidence_references"] == ["a" * 64]

    # Test T: ensure NO downstream artifacts are generated by this workflow
    assert not (workflow_paths["tmp_path"] / "template2_output.txt").exists()
    assert not (workflow_paths["tmp_path"] / "decision_packet.json").exists()


@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_artifact_dir")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_render_commitment_path")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_h1_capture_receipt_path")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_prompt_path")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_raw_output_path")
def test_prompt_sha_mismatch_fails(
    mock_raw, mock_prompt, mock_receipt, mock_commitment, mock_artifact, workflow_paths
):
    """Test P: prompt SHA mismatch fails before semantic parse."""
    mock_artifact.return_value = workflow_paths["tmp_path"]
    mock_commitment.return_value = workflow_paths["commitment_path"]
    mock_receipt.return_value = workflow_paths["receipt_path"]
    mock_prompt.return_value = workflow_paths["prompt_path"]
    mock_raw.return_value = workflow_paths["raw_path"]

    workflow_paths["prompt_path"].write_bytes(b"Altered prompt.")

    with pytest.raises(ValueError, match="Prompt SHA mismatch"):
        validate_h1_report_workflow()

    assert not workflow_paths["report_path"].exists()


@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_artifact_dir")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_render_commitment_path")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_h1_capture_receipt_path")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_prompt_path")
@mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_raw_output_path")
def test_raw_sha_mismatch_fails(
    mock_raw, mock_prompt, mock_receipt, mock_commitment, mock_artifact, workflow_paths
):
    """Test R: raw SHA mismatch fails before semantic parse."""
    mock_artifact.return_value = workflow_paths["tmp_path"]
    mock_commitment.return_value = workflow_paths["commitment_path"]
    mock_receipt.return_value = workflow_paths["receipt_path"]
    mock_prompt.return_value = workflow_paths["prompt_path"]
    mock_raw.return_value = workflow_paths["raw_path"]

    workflow_paths["raw_path"].write_bytes(b"Altered response.")

    with pytest.raises(ValueError, match="Raw response SHA mismatch"):
        validate_h1_report_workflow()

    assert not workflow_paths["report_path"].exists()


def test_rejects_old_prompt_version(workflow_paths):
    """Test D: validate-h1 rejects old prompt version before semantic response parsing."""
    commitment_path = workflow_paths["commitment_path"]
    commitment = json.loads(commitment_path.read_text())
    commitment["prompt_contract_version"] = "h1_lh2_render_only_v1"
    commitment_path.write_text(json.dumps(commitment))

    with mock.patch("investment_orchestrator.workflow.step2_h1_report.step2_render_commitment_path", return_value=commitment_path):
        with pytest.raises(ValueError, match="Unsupported prompt contract version 'h1_lh2_render_only_v1'"):
            validate_h1_report_workflow()


@mock.patch("investment_orchestrator.workflow.step2_h1_provenance.Path.stat")
def test_pre_read_resource_guard(mock_stat, workflow_paths):
    """Test G: validate-h1 rejects stat size >MAX before full read/parser."""
    from investment_orchestrator.workflow.step2_h1_provenance import read_exact_raw_response_bytes
    import os

    stat_result = os.stat_result((33188, 0, 0, 0, 0, 0, MAXIMUM_MMI_RAW_RESPONSE_BYTES + 1, 0, 0, 0))
    mock_stat.return_value = stat_result

    with pytest.raises(ValueError, match=f"outside the allowed bounds"):
        read_exact_raw_response_bytes(workflow_paths["raw_path"])


def test_post_read_resource_guard(workflow_paths, monkeypatch):
    """Test H: post-read >MAX fails if file changes between stat/read."""
    from investment_orchestrator.workflow.step2_h1_provenance import read_exact_raw_response_bytes

    original_stat = Path.stat

    def fake_stat(self):
        if self == workflow_paths["raw_path"]:
            import os
            # Stat returns valid small size
            return os.stat_result((33188, 0, 0, 0, 0, 0, 100, 0, 0, 0))
        return original_stat(self)

    monkeypatch.setattr(Path, "stat", fake_stat)

    # Create large file
    workflow_paths["raw_path"].write_bytes(b"A" * (MAXIMUM_MMI_RAW_RESPONSE_BYTES + 1))

    with pytest.raises(ValueError, match="actual byte size .* outside the allowed bounds"):
        read_exact_raw_response_bytes(workflow_paths["raw_path"])
