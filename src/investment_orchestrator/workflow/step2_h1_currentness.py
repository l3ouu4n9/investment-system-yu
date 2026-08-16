"""H1 Qualitative Report Currentness Observer.

This module owns the deterministic reporting of H1 qualitative report currentness.
It enforces the closed historical/current binding chain, re-parses raw evidence
to guarantee derivative equality, and reports currentness against the system date.
It explicitly grants ZERO actionable investment permission, Step 3 authority, or
final safety.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final, Literal

from investment_orchestrator.common.io import atomic_write_text
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi.long_horizon_research_payload_v2 import (
    MmiLongHorizonResearchPayloadV2,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    load_and_evaluate_step2_research_gate,
)
from investment_orchestrator.workflow.step2_decision_builder import (
    H1Lh2TemporalFailureKind,
    H1Lh2TemporalPolicyError,
    is_exact_h1_render_prerequisite,
    load_current_h1_lh2_payload,
    step2_artifact_dir,
    step2_h1_capture_receipt_path,
    step2_prompt_path,
    step2_raw_output_path,
    step2_render_commitment_path,
    system_now_date,
    validate_h1_lh2_temporal_policy,
)
from investment_orchestrator.workflow.step1_research import (
    step1_research_degraded_mode_decision_path,
)
from investment_orchestrator.workflow.step2_h1_provenance import (
    H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION,
    read_and_validate_h1_capture_receipt,
    read_and_validate_h1_render_commitment,
    read_exact_raw_response_bytes,
)
from investment_orchestrator.workflow.step2_h1_report import (
    parse_h1_qualitative_response,
    read_and_validate_h1_report,
)

STEP2_H1_CURRENTNESS_OBSERVATION_SCHEMA_VERSION: Final = "step2_h1_qualitative_currentness_observation_v1"

_ReasonCode = Literal[
    "REPORT_BINDING_MISMATCH",
    "CURRENT_LH2_STALE",
    "CURRENT_LH2_FUTURE",
    "CURRENT_H1_PREREQUISITE_NOT_MET",
    "CURRENT_EVIDENCE_UNIVERSE_CHANGED",
]


@dataclass(frozen=True, slots=True)
class ValidatedCurrentH1Context:
    """One independently validated CURRENT H1 context with no authority."""

    observed_on: str
    rendered_prompt_sha256: str
    raw_response_sha256: str
    evidence_entry_identities_sha256: tuple[str, ...]
    evidence_references: tuple[str, ...]
    long_horizon_opportunity: str
    valuation_context: str
    portfolio_contribution: str
    evidence_integrity: str
    prior_thesis_change: str
    current_lh2_payload: MmiLongHorizonResearchPayloadV2


@dataclass(frozen=True, slots=True)
class H1CurrentContextEvaluation:
    """Pure current-context evaluation used by observation and future readers."""

    observed_on: str
    is_current: bool
    rendered_prompt_sha256: str
    raw_response_sha256: str
    reason_code: _ReasonCode | None
    context: ValidatedCurrentH1Context | None


def _not_current(
    *,
    observed_on: str,
    rendered_prompt_sha256: str,
    raw_response_sha256: str,
    reason_code: _ReasonCode,
) -> H1CurrentContextEvaluation:
    return H1CurrentContextEvaluation(
        observed_on=observed_on,
        is_current=False,
        rendered_prompt_sha256=rendered_prompt_sha256,
        raw_response_sha256=raw_response_sha256,
        reason_code=reason_code,
        context=None,
    )


def _write_observation(
    *,
    observed_on: str,
    is_current: bool,
    rendered_prompt_sha256: str,
    raw_response_sha256: str,
    reason_code: _ReasonCode | None,
) -> None:
    observation = {
        "schema_version": STEP2_H1_CURRENTNESS_OBSERVATION_SCHEMA_VERSION,
        "observed_on": observed_on,
        "is_current": is_current,
        "rendered_prompt_sha256": rendered_prompt_sha256,
        "raw_response_sha256": raw_response_sha256,
        "reason_code": reason_code,
    }
    observation_path = step2_artifact_dir() / "h1_qualitative_currentness_observation.json"
    atomic_write_text(
        observation_path,
        json.dumps(observation, ensure_ascii=False, indent=2) + "\n",
    )


def evaluate_current_h1_context() -> H1CurrentContextEvaluation:
    """Independently evaluate H1 currentness without writing an artifact."""
    evaluation_date = system_now_date()
    observed_on = evaluation_date.isoformat()
    report_path = step2_artifact_dir() / "h1_qualitative_report.json"
    commitment_path = step2_render_commitment_path()
    receipt_path = step2_h1_capture_receipt_path()
    prompt_path = step2_prompt_path()
    raw_output_path = step2_raw_output_path()

    report = read_and_validate_h1_report(report_path)
    commitment = read_and_validate_h1_render_commitment(commitment_path)

    if commitment["prompt_contract_version"] != H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION:
        raise ValueError("Unsupported prompt contract version in commitment.")

    if report["prompt_contract_version"] != commitment["prompt_contract_version"]:
        return _not_current(
            observed_on=observed_on,
            rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
            raw_response_sha256=report["raw_response_sha256"],
            reason_code="REPORT_BINDING_MISMATCH",
        )

    if report["rendered_prompt_sha256"] != commitment["rendered_prompt_sha256"]:
        return _not_current(
            observed_on=observed_on,
            rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
            raw_response_sha256=report["raw_response_sha256"],
            reason_code="REPORT_BINDING_MISMATCH",
        )

    try:
        current_prompt_bytes = prompt_path.read_bytes()
    except OSError:
        raise FileNotFoundError(f"Missing required prompt at {prompt_path}") from None

    if hashlib.sha256(current_prompt_bytes).hexdigest() != commitment["rendered_prompt_sha256"]:
        return _not_current(
            observed_on=observed_on,
            rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
            raw_response_sha256=report["raw_response_sha256"],
            reason_code="REPORT_BINDING_MISMATCH",
        )

    receipt = read_and_validate_h1_capture_receipt(receipt_path)

    if receipt["rendered_prompt_sha256"] != commitment["rendered_prompt_sha256"]:
        return _not_current(
            observed_on=observed_on,
            rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
            raw_response_sha256=report["raw_response_sha256"],
            reason_code="REPORT_BINDING_MISMATCH",
        )

    if report["raw_response_sha256"] != receipt["raw_response_sha256"]:
        return _not_current(
            observed_on=observed_on,
            rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
            raw_response_sha256=receipt["raw_response_sha256"],
            reason_code="REPORT_BINDING_MISMATCH",
        )

    raw_bytes = read_exact_raw_response_bytes(raw_output_path)

    if hashlib.sha256(raw_bytes).hexdigest() != receipt["raw_response_sha256"]:
        return _not_current(
            observed_on=observed_on,
            rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
            raw_response_sha256=receipt["raw_response_sha256"],
            reason_code="REPORT_BINDING_MISMATCH",
        )

    try:
        parsed = parse_h1_qualitative_response(
            raw_bytes,
            allowed_evidence_shas=frozenset(commitment["evidence_entry_identities_sha256"]),
        )
    except ValueError:
        raise ValueError("Bound raw bytes failed deterministic re-parsing.") from None

    binding_mismatch = (
        report["long_horizon_opportunity"] != parsed.long_horizon_opportunity or
        report["valuation_context"] != parsed.valuation_context or
        report["portfolio_contribution"] != parsed.portfolio_contribution or
        report["evidence_integrity"] != parsed.evidence_integrity or
        report["prior_thesis_change"] != parsed.prior_thesis_change or
        tuple(report["evidence_references"]) != parsed.evidence_references
    )

    if binding_mismatch:
        return _not_current(
            observed_on=observed_on,
            rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
            raw_response_sha256=receipt["raw_response_sha256"],
            reason_code="REPORT_BINDING_MISMATCH",
        )

    p_current = load_current_h1_lh2_payload(repo_root_path=repo_root())

    try:
        validate_h1_lh2_temporal_policy(p_current, now_date=evaluation_date)
    except H1Lh2TemporalPolicyError as exc:
        if H1Lh2TemporalFailureKind.STALE in exc.failure_kinds:
            return _not_current(
                observed_on=observed_on,
                rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
                raw_response_sha256=receipt["raw_response_sha256"],
                reason_code="CURRENT_LH2_STALE",
            )
        if H1Lh2TemporalFailureKind.FUTURE in exc.failure_kinds:
            return _not_current(
                observed_on=observed_on,
                rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
                raw_response_sha256=receipt["raw_response_sha256"],
                reason_code="CURRENT_LH2_FUTURE",
            )
        raise

    gate_result = load_and_evaluate_step2_research_gate(
        step1_research_degraded_mode_decision_path()
    )
    if not is_exact_h1_render_prerequisite(gate_result):
        return _not_current(
            observed_on=observed_on,
            rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
            raw_response_sha256=receipt["raw_response_sha256"],
            reason_code="CURRENT_H1_PREREQUISITE_NOT_MET",
        )

    current_evidence_shas = set(
        entry.source_entry_identity_sha256 for entry in p_current.sources
    )
    if current_evidence_shas != set(commitment["evidence_entry_identities_sha256"]):
        return _not_current(
            observed_on=observed_on,
            rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
            raw_response_sha256=receipt["raw_response_sha256"],
            reason_code="CURRENT_EVIDENCE_UNIVERSE_CHANGED",
        )

    context = ValidatedCurrentH1Context(
        observed_on=observed_on,
        rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
        raw_response_sha256=receipt["raw_response_sha256"],
        evidence_entry_identities_sha256=tuple(
            commitment["evidence_entry_identities_sha256"]
        ),
        evidence_references=parsed.evidence_references,
        long_horizon_opportunity=parsed.long_horizon_opportunity,
        valuation_context=parsed.valuation_context,
        portfolio_contribution=parsed.portfolio_contribution,
        evidence_integrity=parsed.evidence_integrity,
        prior_thesis_change=parsed.prior_thesis_change,
        current_lh2_payload=p_current,
    )
    return H1CurrentContextEvaluation(
        observed_on=observed_on,
        is_current=True,
        rendered_prompt_sha256=commitment["rendered_prompt_sha256"],
        raw_response_sha256=receipt["raw_response_sha256"],
        reason_code=None,
        context=context,
    )


def evaluate_h1_currentness_workflow() -> None:
    """Evaluate and atomically persist an H1 currentness observation."""
    evaluation = evaluate_current_h1_context()
    _write_observation(
        observed_on=evaluation.observed_on,
        is_current=evaluation.is_current,
        rendered_prompt_sha256=evaluation.rendered_prompt_sha256,
        raw_response_sha256=evaluation.raw_response_sha256,
        reason_code=evaluation.reason_code,
    )
