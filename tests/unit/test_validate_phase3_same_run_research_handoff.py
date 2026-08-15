"""Tests for Phase-3 same-run cross-object cohesion validator."""

from investment_orchestrator.validators.validate_phase3_same_run_research_handoff import (
    PHASE3_RESEARCH_ADMISSION_MAPPING_REPORT_IDENTITY_MISMATCH,
    PHASE3_RESEARCH_ADMISSION_SELECTION_STRUCTURALLY_INCONSISTENT,
    PHASE3_RESEARCH_ADMISSION_VALIDATED_RESPONSE_IDENTITY_MISMATCH,
    Phase3SameRunHandoffIdentityClaims,
    Phase3SameRunHandoffValidationStatus,
    validate_phase3_same_run_research_handoff_cohesion,
)


def test_validator_valid_cohesion() -> None:
    claims = Phase3SameRunHandoffIdentityClaims(
        qualitative_response_identity_sha256="a" * 64,
        recognition_response_identity_sha256="a" * 64,
        recognition_mapping_report_identity_sha256="b" * 64,
        selection_mapping_report_identity_sha256="b" * 64,
    )
    result = validate_phase3_same_run_research_handoff_cohesion(claims)
    assert result.status == Phase3SameRunHandoffValidationStatus.VALID
    assert not result.reason_codes


def test_validator_invalid_response_identity_mismatch() -> None:
    claims = Phase3SameRunHandoffIdentityClaims(
        qualitative_response_identity_sha256="a" * 64,
        recognition_response_identity_sha256="c" * 64,
        recognition_mapping_report_identity_sha256="b" * 64,
        selection_mapping_report_identity_sha256="b" * 64,
    )
    result = validate_phase3_same_run_research_handoff_cohesion(claims)
    assert result.status == Phase3SameRunHandoffValidationStatus.INVALID
    assert result.reason_codes == (PHASE3_RESEARCH_ADMISSION_VALIDATED_RESPONSE_IDENTITY_MISMATCH,)


def test_validator_invalid_mapping_identity_mismatch() -> None:
    claims = Phase3SameRunHandoffIdentityClaims(
        qualitative_response_identity_sha256="a" * 64,
        recognition_response_identity_sha256="a" * 64,
        recognition_mapping_report_identity_sha256="b" * 64,
        selection_mapping_report_identity_sha256="c" * 64,
    )
    result = validate_phase3_same_run_research_handoff_cohesion(claims)
    assert result.status == Phase3SameRunHandoffValidationStatus.INVALID
    assert result.reason_codes == (PHASE3_RESEARCH_ADMISSION_MAPPING_REPORT_IDENTITY_MISMATCH,)


def test_validator_invalid_malformed_mapping_identity() -> None:
    claims = Phase3SameRunHandoffIdentityClaims(
        qualitative_response_identity_sha256="a" * 64,
        recognition_response_identity_sha256="a" * 64,
        recognition_mapping_report_identity_sha256="invalid",
        selection_mapping_report_identity_sha256="invalid",
    )
    result = validate_phase3_same_run_research_handoff_cohesion(claims)
    assert result.status == Phase3SameRunHandoffValidationStatus.INVALID
    assert result.reason_codes == (PHASE3_RESEARCH_ADMISSION_SELECTION_STRUCTURALLY_INCONSISTENT,)
