"""Manual Step 1 workflow: render prompt and ingest RESEARCH_JSON."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import (
    atomic_write_text,
    ensure_dir,
    file_exists,
    read_json,
    read_text,
    write_json,
    write_text,
)
from investment_orchestrator.common.paths import repo_root, require_prompt_path
from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    load_artifact_schema,
)
from investment_orchestrator.llm.legacy_step1_prompt_compiler import (
    compile_legacy_step1_prompt_text,
    derive_legacy_approved_extended_etf_json,
)
from investment_orchestrator.llm.manual_output import (
    ensure_manual_output_metadata_template,
    write_rendered_prompt,
)
from investment_orchestrator.normalizers.research_handoff_candidate import (
    normalize_research_handoff_candidate,
    research_handoff_normalization_result_to_dict,
)
from investment_orchestrator.parsers.extract_research_json import (
    ResearchExtractionError,
    extract_research_json,
    parse_research_output_text,
)
from investment_orchestrator.research.analyst_memo import (
    analyst_memo_parse_result_to_dict,
    evidence_universe_from_packet,
    parse_analyst_memo_text,
    render_analyst_memo_prompt,
)
from investment_orchestrator.research.actionable_handoff_candidate import (
    write_actionable_handoff_candidate,
)
from investment_orchestrator.research.actionable_handoff_preview import (
    write_actionable_handoff_preview,
)
from investment_orchestrator.research.actionable_promotion_eligibility import (
    write_actionable_promotion_eligibility,
)
from investment_orchestrator.research.actionable_promotion_pointer import (
    write_actionable_promotion_pointer_if_eligible,
)
from investment_orchestrator.research.actionable_promotion_pointer_preview import (
    write_actionable_promotion_pointer_preview,
)
from investment_orchestrator.research.evidence_packet import (
    _build_evidence_packet_and_selection_from_sanitized_source,
    build_evidence_packet_and_selection,
    compare_embedded_selection_parity,
    compare_evidence_packet_runtime_parity,
)
from investment_orchestrator.research.grounding_status_observatory import (
    build_grounding_status_observatory,
)
from investment_orchestrator.research.step1a_retirement_observation import (
    build_step1a_retirement_observation,
)
from investment_orchestrator.research.handoff_compiler import write_compiled_research_handoff
from investment_orchestrator.research.promoted_handoff_verifier import (
    verify_promoted_handoff_for_step2_decision,
)
from investment_orchestrator.research.promoted_step2_gate_dry_run import (
    evaluate_promoted_step2_gate_dry_run,
)
from investment_orchestrator.research.promoted_step3_audit_dry_run import (
    evaluate_promoted_step3_audit_gate_dry_run,
    verify_promoted_handoff_for_step3_audit,
)
from investment_orchestrator.research.promoted_step4_readiness_dry_run import (
    evaluate_promoted_step4_preview_gate_dry_run,
    verify_promoted_step3_for_step4_readiness,
)
from investment_orchestrator.research.active_research_anchor_registry import (
    compile_active_research_anchor_registry,
    write_active_research_anchor_registry,
)
from investment_orchestrator.research.anchor_source_equivalence import (
    write_anchor_source_equivalence,
)
from investment_orchestrator.research.research_anchor_candidates import (
    write_research_anchor_candidates,
)
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    CapturedResearchAnchorApprovalSource,
    _ValidatedCapturedResearchAnchorApprovalSource,
    _build_from_sanitized_source,
    _build_research_anchor_approval_source_validations_from_sanitized,
    _sanitize_captured_source,
    _verified_approval_source_summary,
    build_active_research_anchor_registry_with_approvals,
    build_research_anchor_approval_source_validations,
    capture_research_anchor_approval_source,
)
from investment_orchestrator.research.approval_registry_dual_read_diff import (
    _build_approval_registry_dual_read_diff_from_sanitized_source,
    build_approval_registry_dual_read_diff,
)
from investment_orchestrator.research.approval_registry_switch_readiness import (
    _build_approval_registry_switch_readiness_from_sanitized_source,
    build_approval_registry_switch_readiness_from_captured_source,
)
from investment_orchestrator.research.support_signals_dual_ground_diff import (
    _write_support_signals_dual_ground_diff_from_sanitized_source,
    write_support_signals_dual_ground_diff,
)
from investment_orchestrator.state.final_execution_safety_preflight import (
    evaluate_promoted_final_safety_preflight,
)
from investment_orchestrator.state.last_good_research_handoff import (
    LastGoodResearchHandoffWriteResult,
    last_good_research_handoff_metadata_path,
    last_good_research_handoff_write_result_to_dict,
    read_last_good_research_handoff,
    write_last_good_research_handoff_if_valid,
)
from investment_orchestrator.state.research_availability import (
    H1MappedResearchSelectionProjection,
    build_h1_mapped_research_selection_projection,
    evaluate_research_availability,
    research_availability_result_to_dict,
    research_degraded_mode_decision_to_dict,
    research_freshness_report_to_dict,
)
from investment_orchestrator.workflow.step1a_grounding_compile import (
    _build_step1a_active_research_anchor_registry_with_approvals_from_sanitized_source,
    _build_step1a_approval_registry_dual_read_diff_from_sanitized_source,
    _build_step1a_approval_registry_switch_readiness_from_sanitized_source,
    _build_step1a_evidence_packet_from_sanitized_inputs,
    _build_step1a_grounding_compile_bundle_from_sanitized_source,
    _build_step1a_research_anchor_approvals_validation_from_sanitized_source,
    _build_step1a_research_anchor_revocations_validation_from_sanitized_source,
    _enforce_workflow_approval_source_identity,
    build_step1a_active_research_anchor_registry,
    build_step1a_active_research_anchor_registry_with_approvals,
    build_step1a_approval_registry_dual_read_diff,
    build_step1a_approval_registry_switch_readiness,
    build_step1a_evidence_packet,
    build_step1a_grounding_compile_bundle,
    build_step1a_grounding_compile_shadow_diff,
    build_step1a_research_anchor_approvals_validation,
    build_step1a_research_anchor_revocations_validation,
)
from investment_orchestrator.validators.strategy_settings import parse_strategy_settings_text
from investment_orchestrator.validators.validate_research_handoff import (
    research_handoff_validation_result_to_dict,
    validate_research_handoff,
)


STEP1_DIRNAME = "step1_research"
PROMPT_FILENAME = "prompt.txt"
RAW_OUTPUT_FILENAME = "raw_output.txt"
RESEARCH_OUTPUT_FILENAME = "research_output.json"
RESEARCH_HANDOFF_VALIDATION_FILENAME = "research_handoff_validation.json"
RESEARCH_HANDOFF_CANDIDATE_FILENAME = "research_handoff_candidate.json"
RESEARCH_HANDOFF_CANDIDATE_NORMALIZATION_FILENAME = "research_handoff_candidate_normalization.json"
RESEARCH_HANDOFF_CANDIDATE_VALIDATION_FILENAME = "research_handoff_candidate_validation.json"
LAST_GOOD_WRITE_RESULT_FILENAME = "last_good_research_handoff_write_result.json"
RESEARCH_AVAILABILITY_FILENAME = "research_availability.json"
RESEARCH_FRESHNESS_REPORT_FILENAME = "research_freshness_report.json"
RESEARCH_DEGRADED_MODE_DECISION_FILENAME = "research_degraded_mode_decision.json"
EVIDENCE_PACKET_FILENAME = "evidence_packet.json"
ANALYST_MEMO_PROMPT_FILENAME = "analyst_memo_prompt.txt"
ANALYST_MEMO_RAW_OUTPUT_FILENAME = "analyst_memo_raw_output.txt"
ANALYST_MEMO_FILENAME = "analyst_memo.json"
ANALYST_MEMO_VALIDATION_FILENAME = "analyst_memo_validation.json"
COMPILED_HANDOFF_CANDIDATE_FILENAME = "compiled_research_handoff_candidate.json"
COMPILED_HANDOFF_VALIDATION_FILENAME = "compiled_research_handoff_validation.json"
COMPILED_HANDOFF_METADATA_FILENAME = "compiled_research_handoff_metadata.json"
COMPILED_SUPPORT_SIGNALS_FILENAME = "compiled_support_signals.json"
ACTIONABLE_HANDOFF_PREVIEW_FILENAME = "compiled_actionable_handoff_preview.json"
ACTIONABLE_HANDOFF_CANDIDATE_FILENAME = "compiled_actionable_research_handoff_candidate.json"
ACTIONABLE_HANDOFF_VALIDATION_FILENAME = "compiled_actionable_research_handoff_validation.json"
ACTIONABLE_HANDOFF_METADATA_FILENAME = "compiled_actionable_research_handoff_metadata.json"
ACTIONABLE_PROMOTION_ELIGIBILITY_FILENAME = "compiled_actionable_handoff_promotion_eligibility.json"
ACTIONABLE_PROMOTION_POINTER_PREVIEW_FILENAME = "compiled_actionable_handoff_promotion_pointer_preview.json"
ACTIONABLE_EFFECTIVE_HANDOFF_PREVIEW_FILENAME = "compiled_actionable_research_handoff_effective_preview.json"
ACTIONABLE_EFFECTIVE_HANDOFF_PREVIEW_VALIDATION_FILENAME = (
    "compiled_actionable_research_handoff_effective_preview_validation.json"
)
ACTIVE_RESEARCH_HANDOFF_SOURCE_FILENAME = "active_research_handoff_source.json"
EFFECTIVE_RESEARCH_HANDOFF_FILENAME = "research_handoff_candidate_effective.json"
EFFECTIVE_RESEARCH_HANDOFF_VALIDATION_FILENAME = "research_handoff_candidate_effective_validation.json"
ACTIVE_POINTER_WRITE_STATUS_FILENAME = "active_research_handoff_source_write_status.json"
PROMOTED_HANDOFF_STEP2_VERIFICATION_FILENAME = "promoted_handoff_step2_verification.json"
PROMOTED_STEP2_GATE_DRY_RUN_FILENAME = "promoted_step2_gate_dry_run.json"
PROMOTED_HANDOFF_STEP3_AUDIT_VERIFICATION_FILENAME = (
    "promoted_handoff_step3_audit_verification.json"
)
PROMOTED_STEP3_AUDIT_GATE_DRY_RUN_FILENAME = "promoted_step3_audit_gate_dry_run.json"
PROMOTED_STEP4_READINESS_VERIFICATION_FILENAME = "promoted_step4_readiness_verification.json"
PROMOTED_STEP4_PREVIEW_GATE_DRY_RUN_FILENAME = "promoted_step4_preview_gate_dry_run.json"
PROMOTED_FINAL_SAFETY_PREFLIGHT_FILENAME = "promoted_final_safety_preflight.json"
ACTIVE_RESEARCH_ANCHOR_REGISTRY_FILENAME = "active_research_anchor_registry.json"
ANCHOR_SOURCE_EQUIVALENCE_FILENAME = "anchor_source_equivalence.json"
RESEARCH_ANCHOR_CANDIDATES_FILENAME = "research_anchor_candidates.json"
RESEARCH_ANCHOR_APPROVALS_VALIDATION_FILENAME = "research_anchor_approvals_validation.json"
RESEARCH_ANCHOR_REVOCATIONS_VALIDATION_FILENAME = "research_anchor_revocations_validation.json"
ACTIVE_RESEARCH_ANCHOR_REGISTRY_WITH_APPROVALS_FILENAME = (
    "active_research_anchor_registry_with_approvals.json"
)
APPROVAL_REGISTRY_DUAL_READ_DIFF_FILENAME = "approval_registry_dual_read_diff.json"
APPROVAL_REGISTRY_SWITCH_READINESS_FILENAME = "approval_registry_switch_readiness.json"
SUPPORT_SIGNALS_DUAL_GROUND_DIFF_FILENAME = "support_signals_dual_ground_diff.json"
GROUNDING_STATUS_OBSERVATORY_FILENAME = "grounding_status_observatory.json"
EMBEDDED_ACTIVE_REGISTRY_SELECTION_FILENAME = "embedded_active_registry_selection.json"
STEP1A_GROUNDING_COMPILE_SHADOW_DIFF_FILENAME = "step1a_grounding_compile_shadow_diff.json"
STEP1A_ARTIFACT_SWITCH_STATUS_FILENAME = "step1a_artifact_switch_status.json"
STEP1A_RETIREMENT_OBSERVATION_FILENAME = "step1a_retirement_observation.json"
RESEARCH_ANCHORS_INPUT_FILENAME = "research_anchors.yaml"
RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME = "research_anchor_approvals.yaml"

# Report-only render continuity leaves (S1P-2). Private: no external consumer
# exists, and nothing may read either file as machine authority.
_RENDER_COMMITMENT_FILENAME = "render_commitment.json"
_RENDER_CONTINUITY_REPORT_FILENAME = "render_continuity_report.json"
_RENDER_COMMITMENT_SCHEMA_VERSION = "step1_render_commitment_v2"

# Retired S1P-1 source-only leaves. Their v1 schemas keep their original
# meaning forever (strategy + portfolio identity only); v2 retires them rather
# than redefining them, so archived v1 trees stay readable as v1. These names
# survive here solely so a current tree written before the upgrade cannot leave
# a stale success artifact beside fresh v2 evidence.
_RETIRED_RENDER_SOURCE_COMMITMENT_FILENAME = "render_source_commitment.json"
_RETIRED_RENDER_SOURCE_CONTINUITY_REPORT_FILENAME = "render_source_continuity_report.json"

_LOWERCASE_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class _NoOutputWarrantResult(Enum):
    OUTPUT_UNAVAILABLE = "OUTPUT_UNAVAILABLE"
    NO_BASE_CONTEXT = "NO_BASE_CONTEXT"


@dataclass(frozen=True)
class _NoOutputWarrant:
    result: _NoOutputWarrantResult
    detail: str


def current_inputs_dir() -> Path:
    """Return the operator-maintained current input directory."""
    return repo_root() / "inputs" / "current"


def step1_artifact_dir() -> Path:
    """Return the Step 1 artifact directory."""
    return ensure_dir(repo_root() / "artifacts" / "current" / STEP1_DIRNAME)


def step1_prompt_path() -> Path:
    """Return the rendered Step 1 prompt path."""
    return step1_artifact_dir() / PROMPT_FILENAME


def step1_raw_output_path() -> Path:
    """Return the manual Step 1 raw output path."""
    return step1_artifact_dir() / RAW_OUTPUT_FILENAME


def step1_research_output_path() -> Path:
    """Return the parsed research output path."""
    return step1_artifact_dir() / RESEARCH_OUTPUT_FILENAME


def step1_research_handoff_validation_path() -> Path:
    """Return the report-only raw research handoff validation artifact path."""
    return step1_artifact_dir() / RESEARCH_HANDOFF_VALIDATION_FILENAME


def step1_research_handoff_candidate_path() -> Path:
    """Return the normalized research handoff candidate artifact path."""
    return step1_artifact_dir() / RESEARCH_HANDOFF_CANDIDATE_FILENAME


def step1_research_handoff_candidate_normalization_path() -> Path:
    """Return the normalization-diagnostics artifact path for the candidate."""
    return step1_artifact_dir() / RESEARCH_HANDOFF_CANDIDATE_NORMALIZATION_FILENAME


def step1_research_handoff_candidate_validation_path() -> Path:
    """Return the report-only candidate handoff validation artifact path."""
    return step1_artifact_dir() / RESEARCH_HANDOFF_CANDIDATE_VALIDATION_FILENAME


def step1_state_dir() -> Path:
    """Return the persistent state directory (outside current/; survives prepare_next_run)."""
    return ensure_dir(repo_root() / "artifacts" / "state")


def step1_last_good_write_result_path() -> Path:
    """Return the report-only per-run last-good write-result artifact path."""
    return step1_artifact_dir() / LAST_GOOD_WRITE_RESULT_FILENAME


def step1_research_availability_path() -> Path:
    """Return the report-only research availability artifact path."""
    return step1_artifact_dir() / RESEARCH_AVAILABILITY_FILENAME


def step1_research_freshness_report_path() -> Path:
    """Return the report-only research freshness report artifact path."""
    return step1_artifact_dir() / RESEARCH_FRESHNESS_REPORT_FILENAME


def step1_research_degraded_mode_decision_path() -> Path:
    """Return the report-only degraded-mode decision artifact path."""
    return step1_artifact_dir() / RESEARCH_DEGRADED_MODE_DECISION_FILENAME


def step1_evidence_packet_path() -> Path:
    """Return the report-only deterministic evidence packet artifact path (R2B)."""
    return step1_artifact_dir() / EVIDENCE_PACKET_FILENAME


def step1_analyst_memo_prompt_path() -> Path:
    """Return the rendered Step 1B analyst-memo prompt path (R2C, report-only)."""
    return step1_artifact_dir() / ANALYST_MEMO_PROMPT_FILENAME


def step1_analyst_memo_raw_output_path() -> Path:
    """Return the manual Step 1B analyst-memo raw output path (R2C, report-only)."""
    return step1_artifact_dir() / ANALYST_MEMO_RAW_OUTPUT_FILENAME


def step1_analyst_memo_path() -> Path:
    """Return the parsed Step 1B analyst-memo artifact path (R2C, report-only)."""
    return step1_artifact_dir() / ANALYST_MEMO_FILENAME


def step1_analyst_memo_validation_path() -> Path:
    """Return the report-only Step 1B analyst-memo validation artifact path (R2C)."""
    return step1_artifact_dir() / ANALYST_MEMO_VALIDATION_FILENAME


def step1_compiled_handoff_candidate_path() -> Path:
    """Return the report-only Step 1C compiled handoff candidate artifact path (R2D)."""
    return step1_artifact_dir() / COMPILED_HANDOFF_CANDIDATE_FILENAME


def step1_compiled_handoff_validation_path() -> Path:
    """Return the report-only Step 1C compiled handoff validation artifact path (R2D)."""
    return step1_artifact_dir() / COMPILED_HANDOFF_VALIDATION_FILENAME


def step1_compiled_handoff_metadata_path() -> Path:
    """Return the report-only Step 1C compiled handoff metadata artifact path (R2D)."""
    return step1_artifact_dir() / COMPILED_HANDOFF_METADATA_FILENAME


def step1_compiled_support_signals_path() -> Path:
    """Return the report-only Step 1C support-signals artifact path (R2E.3)."""
    return step1_artifact_dir() / COMPILED_SUPPORT_SIGNALS_FILENAME


def step1_actionable_handoff_preview_path() -> Path:
    """Return the report-only Step 1C actionable-handoff preview artifact path (R2E.5b-0)."""
    return step1_artifact_dir() / ACTIONABLE_HANDOFF_PREVIEW_FILENAME


def step1_actionable_handoff_candidate_path() -> Path:
    """Return the report-only Step 1C actionable handoff candidate artifact path (R2E.5b-1)."""
    return step1_artifact_dir() / ACTIONABLE_HANDOFF_CANDIDATE_FILENAME


def step1_actionable_handoff_validation_path() -> Path:
    """Return the report-only Step 1C actionable handoff validation artifact path (R2E.5b-1)."""
    return step1_artifact_dir() / ACTIONABLE_HANDOFF_VALIDATION_FILENAME


def step1_actionable_handoff_metadata_path() -> Path:
    """Return the report-only Step 1C actionable handoff metadata artifact path (R2E.5b-1)."""
    return step1_artifact_dir() / ACTIONABLE_HANDOFF_METADATA_FILENAME


def step1_actionable_promotion_eligibility_path() -> Path:
    """Return the report-only promotion-eligibility artifact path (R2E.5b-3)."""
    return step1_artifact_dir() / ACTIONABLE_PROMOTION_ELIGIBILITY_FILENAME


def step1_actionable_promotion_pointer_preview_path() -> Path:
    """Return the report-only promotion pointer-preview artifact path (R2E.5b-4)."""
    return step1_artifact_dir() / ACTIONABLE_PROMOTION_POINTER_PREVIEW_FILENAME


def step1_actionable_effective_handoff_preview_path() -> Path:
    """Return the report-only effective-handoff preview artifact path (R2E.5b-4)."""
    return step1_artifact_dir() / ACTIONABLE_EFFECTIVE_HANDOFF_PREVIEW_FILENAME


def step1_actionable_effective_handoff_preview_validation_path() -> Path:
    """Return the report-only effective-handoff preview validation path (R2E.5b-4)."""
    return step1_artifact_dir() / ACTIONABLE_EFFECTIVE_HANDOFF_PREVIEW_VALIDATION_FILENAME


def step1_active_research_handoff_source_path() -> Path:
    """Return the REAL active-pointer artifact path (R2E.5b-5a; pending gates, no consumers)."""
    return step1_artifact_dir() / ACTIVE_RESEARCH_HANDOFF_SOURCE_FILENAME


def step1_effective_research_handoff_path() -> Path:
    """Return the REAL effective-handoff artifact path (R2E.5b-5a; pending gates, no consumers)."""
    return step1_artifact_dir() / EFFECTIVE_RESEARCH_HANDOFF_FILENAME


def step1_effective_research_handoff_validation_path() -> Path:
    """Return the REAL effective-handoff validation artifact path (R2E.5b-5a)."""
    return step1_artifact_dir() / EFFECTIVE_RESEARCH_HANDOFF_VALIDATION_FILENAME


def step1_active_pointer_write_status_path() -> Path:
    """Return the active-pointer write-status artifact path (R2E.5b-5a)."""
    return step1_artifact_dir() / ACTIVE_POINTER_WRITE_STATUS_FILENAME


def step1_promoted_handoff_step2_verification_path() -> Path:
    """Return the report-only promoted-handoff Step 2 verification path (R2E.5b-6b)."""
    return step1_artifact_dir() / PROMOTED_HANDOFF_STEP2_VERIFICATION_FILENAME


def step1_promoted_step2_gate_dry_run_path() -> Path:
    """Return the report-only promoted Step 2 gate dry-run artifact path (R2E.5b-6b)."""
    return step1_artifact_dir() / PROMOTED_STEP2_GATE_DRY_RUN_FILENAME


def step1_promoted_handoff_step3_audit_verification_path() -> Path:
    """Return the report-only promoted-handoff Step 3 audit verification path (R2E.5b-6e)."""
    return step1_artifact_dir() / PROMOTED_HANDOFF_STEP3_AUDIT_VERIFICATION_FILENAME


def step1_promoted_step3_audit_gate_dry_run_path() -> Path:
    """Return the report-only promoted Step 3 audit gate dry-run artifact path (R2E.5b-6e)."""
    return step1_artifact_dir() / PROMOTED_STEP3_AUDIT_GATE_DRY_RUN_FILENAME


def step1_promoted_step4_readiness_verification_path() -> Path:
    """Return the report-only promoted Step 4 readiness verification path (R2E.5b-7b)."""
    return step1_artifact_dir() / PROMOTED_STEP4_READINESS_VERIFICATION_FILENAME


def step1_promoted_step4_preview_gate_dry_run_path() -> Path:
    """Return the report-only promoted Step 4 preview gate dry-run path (R2E.5b-7b)."""
    return step1_artifact_dir() / PROMOTED_STEP4_PREVIEW_GATE_DRY_RUN_FILENAME


def step1_promoted_final_safety_preflight_path() -> Path:
    """Return the report-only, rowless final-safety preflight path (R2E.5b-7c)."""
    return step1_artifact_dir() / PROMOTED_FINAL_SAFETY_PREFLIGHT_FILENAME


def step1_active_research_anchor_registry_path() -> Path:
    """Return the report-only active research-anchor registry path (R2G-1)."""
    return step1_artifact_dir() / ACTIVE_RESEARCH_ANCHOR_REGISTRY_FILENAME


def step1_anchor_source_equivalence_path() -> Path:
    """Return the report-only anchor-source equivalence oracle path (R2G-2)."""
    return step1_artifact_dir() / ANCHOR_SOURCE_EQUIVALENCE_FILENAME


def step1_research_anchor_candidates_path() -> Path:
    """Return the report-only research-anchor candidates path (R2G-4)."""
    return step1_artifact_dir() / RESEARCH_ANCHOR_CANDIDATES_FILENAME


def step1_research_anchor_approvals_validation_path() -> Path:
    """Return the report-only operator-approval manifest validation path (R2G-5a)."""
    return step1_artifact_dir() / RESEARCH_ANCHOR_APPROVALS_VALIDATION_FILENAME


def step1_research_anchor_revocations_validation_path() -> Path:
    """Return the report-only operator-revocation manifest validation path (R2G-5d-0)."""
    return step1_artifact_dir() / RESEARCH_ANCHOR_REVOCATIONS_VALIDATION_FILENAME


def step1_active_research_anchor_registry_with_approvals_path() -> Path:
    """Return the report-only approvals-inclusive active registry path (R2G-5b)."""
    return step1_artifact_dir() / ACTIVE_RESEARCH_ANCHOR_REGISTRY_WITH_APPROVALS_FILENAME


def step1_approval_registry_dual_read_diff_path() -> Path:
    """Return the report-only baseline-vs-approvals registry dual-read diff path (R2G-5b)."""
    return step1_artifact_dir() / APPROVAL_REGISTRY_DUAL_READ_DIFF_FILENAME


def step1_approval_registry_switch_readiness_path() -> Path:
    """Return the report-only approval-registry switch-readiness gate path (R2G-5c-0)."""
    return step1_artifact_dir() / APPROVAL_REGISTRY_SWITCH_READINESS_FILENAME


def step1_support_signals_dual_ground_diff_path() -> Path:
    """Return the report-only support_signals dual-ground dry-run diff path (R2G-5c-1)."""
    return step1_artifact_dir() / SUPPORT_SIGNALS_DUAL_GROUND_DIFF_FILENAME


def step1_grounding_status_observatory_path() -> Path:
    """Return the report-only grounding status observatory path (R2G-6b)."""
    return step1_artifact_dir() / GROUNDING_STATUS_OBSERVATORY_FILENAME


def step1_embedded_active_registry_selection_path() -> Path:
    """Return the report-only persisted embedded registry selection path (S1A-2).

    This records the CURRENT production evidence-packet selection for shadow
    parity diagnostics only; it is not an authority or selection input.
    """
    return step1_artifact_dir() / EMBEDDED_ACTIVE_REGISTRY_SELECTION_FILENAME


def step1a_grounding_compile_shadow_diff_path() -> Path:
    """Return the report-only Step 1A grounding compile shadow diff path (S1A-1)."""
    return step1_artifact_dir() / STEP1A_GROUNDING_COMPILE_SHADOW_DIFF_FILENAME


def step1a_artifact_switch_status_path() -> Path:
    """Return the report-only Step 1A artifact switch status path (S1A-3).

    Provenance diagnostics only: records which writer source produced each
    switched artifact this run. Not an authority and consumed by nothing.
    """
    return step1_artifact_dir() / STEP1A_ARTIFACT_SWITCH_STATUS_FILENAME


def step1a_retirement_observation_path() -> Path:
    """Return the single-run, report-only Step 1A retirement observation path.

    This file describes only the current parse's final observed state.  It is
    overwritten for the next current run and is not a history, archive, or
    readiness accumulator.
    """
    return step1_artifact_dir() / STEP1A_RETIREMENT_OBSERVATION_FILENAME


def _step1_render_commitment_path() -> Path:
    """Return the report-only render commitment path (S1P-2)."""
    return step1_artifact_dir() / _RENDER_COMMITMENT_FILENAME


def _step1_render_continuity_report_path() -> Path:
    """Return the report-only render continuity report path (S1P-2)."""
    return step1_artifact_dir() / _RENDER_CONTINUITY_REPORT_FILENAME


def _step1_retired_render_source_commitment_path() -> Path:
    """Return the retired S1P-1 source-only commitment path.

    Only ever deleted, never read: a v1 artifact records source identity alone
    and can never supply the prompt identity the v2 contract requires.
    """
    return step1_artifact_dir() / _RETIRED_RENDER_SOURCE_COMMITMENT_FILENAME


def _step1_retired_render_source_continuity_report_path() -> Path:
    """Return the retired S1P-1 source-only continuity report path."""
    return step1_artifact_dir() / _RETIRED_RENDER_SOURCE_CONTINUITY_REPORT_FILENAME


def resolve_step1_prompt_template_path() -> Path:
    """Resolve the formal Step 1 prompt template from prompts/."""
    return require_prompt_path("research_dual_lane.txt")


def resolve_analyst_memo_prompt_template_path() -> Path:
    """Resolve the small Step 1B analyst-memo prompt template from prompts/."""
    return require_prompt_path("analyst_memo.txt")


def _require_non_empty_text(path: Path, *, label: str) -> str:
    """Read a required text input and fail clearly when it is missing or empty."""
    try:
        text = read_text(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Missing required {label}: {path}") from exc

    if not text.strip():
        raise ValueError(f"Required {label} is empty: {path}")
    return text


def _decode_legacy_text_from_exact_bytes(raw_bytes: bytes) -> str:
    """Decode exact source bytes with Legacy ``Path.read_text`` semantics."""
    return io.TextIOWrapper(
        io.BytesIO(raw_bytes),
        encoding="utf-8",
        errors="strict",
        newline=None,
    ).read()


def _strategy_settings_input_path() -> Path:
    """Return the fixed CURRENT strategy-settings source path."""
    return current_inputs_dir() / "strategy_settings.yaml"


def _portfolio_snapshot_input_path() -> Path:
    """Return the fixed CURRENT portfolio-snapshot source path."""
    return current_inputs_dir() / "portfolio_snapshot.txt"


def _load_strategy_settings_exact_bytes_and_text() -> tuple[bytes, str]:
    """Acquire the strategy settings source once and retain its exact bytes.

    This owns the whole strategy source contract: one ``Path.read_bytes`` of the
    fixed CURRENT path, the missing-file translation, the shared Legacy decoder,
    and the decoded-text nonempty validation. Retaining the buffer is what lets
    a render describe the source it actually consumed without reopening it.
    """
    path = _strategy_settings_input_path()
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Missing required strategy settings YAML input: {path}"
        ) from exc

    text = _decode_legacy_text_from_exact_bytes(raw_bytes)
    if not text.strip():
        raise ValueError(f"Required strategy settings YAML input is empty: {path}")
    return raw_bytes, text


def load_strategy_settings_yaml_text() -> str:
    """Read the operator-maintained strategy settings YAML exactly as stored on disk."""
    return _load_strategy_settings_exact_bytes_and_text()[1]


def load_strategy_settings() -> dict[str, Any]:
    """Parse the operator-maintained strategy settings YAML."""
    return parse_strategy_settings_text(load_strategy_settings_yaml_text())


def load_strategy_settings_for_handoff_validation() -> dict[str, Any] | None:
    """Load strategy settings for report-only handoff validation without blocking parse."""
    try:
        return load_strategy_settings()
    except Exception:
        return None


def _load_portfolio_snapshot_exact_bytes_and_text() -> tuple[bytes, str]:
    """Acquire the portfolio snapshot source once and retain its exact bytes.

    Mirrors :func:`_load_strategy_settings_exact_bytes_and_text` for the second
    fixed CURRENT source; see there for why the buffer is retained.
    """
    path = _portfolio_snapshot_input_path()
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Missing required portfolio snapshot input: {path}"
        ) from exc

    text = _decode_legacy_text_from_exact_bytes(raw_bytes)
    if not text.strip():
        raise ValueError(f"Required portfolio snapshot input is empty: {path}")
    return raw_bytes, text


def load_portfolio_snapshot_text() -> str:
    """Read the operator-maintained portfolio snapshot exactly as stored on disk."""
    return _load_portfolio_snapshot_exact_bytes_and_text()[1]


def load_current_run_user_approved_extended_etf_static_list_json() -> str:
    """Load the current-run approved ETF static list and serialize it as a JSON array string."""
    return derive_legacy_approved_extended_etf_json(
        strategy_settings_text=load_strategy_settings_yaml_text(),
    )


def _build_step1_prompt_text_with_render_sources(
    template_path: Path,
) -> tuple[str, bytes, bytes]:
    """Run one Step 1 render acquisition and return the prompt with its exact sources.

    The acquisition order stays template -> strategy -> portfolio, each source
    opened exactly once. The two returned buffers are the ONLY authorized digest
    inputs: a render describes the bytes it actually compiled, never a reread.
    """
    template_text = read_text(template_path)
    strategy_settings_bytes, strategy_settings_text = (
        _load_strategy_settings_exact_bytes_and_text()
    )
    portfolio_snapshot_bytes, portfolio_snapshot_text = (
        _load_portfolio_snapshot_exact_bytes_and_text()
    )
    approved_extended_etf_json = derive_legacy_approved_extended_etf_json(
        strategy_settings_text=strategy_settings_text,
    )
    prompt_text = compile_legacy_step1_prompt_text(
        template_text=template_text,
        strategy_settings_text=strategy_settings_text,
        portfolio_snapshot_text=portfolio_snapshot_text,
        approved_extended_etf_json=approved_extended_etf_json,
    )
    return prompt_text, strategy_settings_bytes, portfolio_snapshot_bytes


def build_step1_prompt_text() -> str:
    """Render the Step 1 prompt without mutating the source prompt file."""
    prompt_text, _strategy_settings_bytes, _portfolio_snapshot_bytes = (
        _build_step1_prompt_text_with_render_sources(
            resolve_step1_prompt_template_path()
        )
    )
    return prompt_text


def _serialize_step1_render_commitment(
    *,
    strategy_settings_sha256: str,
    portfolio_snapshot_sha256: str,
    prompt_sha256: str,
) -> str:
    """Serialize the closed v2 render commitment payload deterministically."""
    payload = {
        "schema_version": _RENDER_COMMITMENT_SCHEMA_VERSION,
        "strategy_settings_sha256": strategy_settings_sha256,
        "portfolio_snapshot_sha256": portfolio_snapshot_sha256,
        "prompt_sha256": prompt_sha256,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_step1_prompt() -> dict[str, str]:
    """Write the rendered Step 1 prompt and prepare the manual output artifact.

    This also publishes the report-only ``render_commitment.json`` (S1P-2): the
    SHA-256 of the exact strategy and portfolio buffers THIS render compiled and
    of the exact prompt text it handed to the writer. The commitment is written
    last and atomically, which makes it the single durable completion point —
    every earlier failure below leaves no commitment at all, so no surviving
    commitment can ever describe a prompt that was not fully published. It
    records nothing about raw_output.txt, which may legally outlive a rerender,
    and no gate, permission, state, pointer, publication, or order path reads it.
    """
    artifact_dir = step1_artifact_dir()
    prompt_output_path = step1_prompt_path()
    raw_output_path = step1_raw_output_path()
    template_path = resolve_step1_prompt_template_path()
    commitment_path = _step1_render_commitment_path()
    continuity_report_path = _step1_render_continuity_report_path()
    retired_commitment_path = _step1_retired_render_source_commitment_path()
    retired_report_path = _step1_retired_render_source_continuity_report_path()

    (
        prompt_text,
        strategy_settings_bytes,
        portfolio_snapshot_bytes,
    ) = _build_step1_prompt_text_with_render_sources(template_path)
    # The prompt digest originates from the exact text handed to the writer,
    # never from a reread. Hashing what the render INTENDED keeps a write-stage
    # defect visible: the parse-time comparison against the persisted bytes is
    # what would surface it, whereas hashing the file back would launder it into
    # the commitment. The committed value therefore identifies the UTF-8
    # encoding of that text alone, and claims nothing about the persisted bytes
    # on a platform whose text-mode writer translates newlines.
    commitment_text = _serialize_step1_render_commitment(
        strategy_settings_sha256=hashlib.sha256(strategy_settings_bytes).hexdigest(),
        portfolio_snapshot_sha256=hashlib.sha256(portfolio_snapshot_bytes).hexdigest(),
        prompt_sha256=hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
    )

    # Everything above succeeded in memory, so retire every previous run's
    # evidence before any new bytes land: reports before commitments (evidence
    # before claims) and retired v1 leaves before current v2 leaves. Because all
    # four deletions precede prompt mutation, a commitment surviving a failed
    # render necessarily still describes an unmutated prompt.
    retired_report_path.unlink(missing_ok=True)
    continuity_report_path.unlink(missing_ok=True)
    retired_commitment_path.unlink(missing_ok=True)
    commitment_path.unlink(missing_ok=True)

    write_rendered_prompt(prompt_output_path, prompt_text)
    if not file_exists(raw_output_path):
        write_text(raw_output_path, "")
    metadata_path = ensure_manual_output_metadata_template(
        raw_output_path,
        prompt_path=prompt_output_path,
    )

    result = {
        "artifact_dir": str(artifact_dir),
        "prompt_path": str(prompt_output_path),
        "raw_output_path": str(raw_output_path),
        "raw_output_metadata_path": str(metadata_path),
        "prompt_template_path": str(template_path),
    }
    atomic_write_text(commitment_path, commitment_text)
    return result


def render_step1_analyst_memo_prompt(
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Render the small Step 1B analyst-memo prompt from the evidence packet (R2C).

    Report-only: builds (or reuses) the deterministic ``evidence_packet.json``,
    injects it into the small memo prompt template, and writes
    ``analyst_memo_prompt.txt`` plus a blank ``analyst_memo_raw_output.txt`` for
    the operator to paste the LLM memo into. This neither runs the model nor
    changes any gate, permission, or degraded-mode decision.
    """
    settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )

    packet = _load_or_build_evidence_packet(strategy_settings=settings)
    template = read_text(resolve_analyst_memo_prompt_template_path())
    rendered = render_analyst_memo_prompt(prompt_template=template, evidence_packet=packet)

    prompt_output_path = step1_analyst_memo_prompt_path()
    write_text(prompt_output_path, rendered.rstrip() + "\n")
    raw_output_path = step1_analyst_memo_raw_output_path()
    if not file_exists(raw_output_path):
        write_text(raw_output_path, "")

    return {
        "analyst_memo_prompt_path": str(prompt_output_path),
        "analyst_memo_raw_output_path": str(raw_output_path),
        "evidence_packet_path": str(step1_evidence_packet_path()),
        "analyst_memo_prompt_template_path": str(resolve_analyst_memo_prompt_template_path()),
    }


def parse_step1_analyst_memo_output(
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Standalone parse of a pasted analyst-memo output (R2C, report-only).

    Requires ``analyst_memo_raw_output.txt`` to be present; writes
    ``analyst_memo.json`` + ``analyst_memo_validation.json``. Unlike the layer
    embedded in ``parse_step1_output`` (which silently skips when no raw memo
    exists), this CLI-facing entrypoint raises if the raw memo is absent.
    """
    settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )
    raw_path = step1_analyst_memo_raw_output_path()
    if not file_exists(raw_path) or not read_text(raw_path).strip():
        raise FileNotFoundError(
            f"Missing analyst memo raw output: {raw_path}. "
            "Run `run_step1 analyst-memo-render` and paste the memo first."
        )
    result = _run_analyst_memo_parse(strategy_settings=settings)
    assert result is not None  # raw is present per the guard above
    return result


class _Step1RenderCommitmentError(Exception):
    """Private carrier for exactly one closed render-commitment reason code."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _reject_duplicate_commitment_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    """Fail closed on duplicate JSON members instead of letting the last one win."""
    keys = [key for key, _value in pairs]
    if len(set(keys)) != len(keys):
        raise _Step1RenderCommitmentError("RENDER_COMMITMENT_INVALID_CONTRACT")
    return dict(pairs)


def _load_step1_render_commitment_digests() -> tuple[str, str, str]:
    """Read and validate ``render_commitment.json``; return its three digests.

    Single owning parser/validator for that artifact. Any problem raises
    :class:`_Step1RenderCommitmentError` carrying exactly one closed reason
    code, in this precedence: absent source, unreadable source, JSON syntax,
    the structure required to read a version, the version itself, then the v2
    field contract. Applying v2 field rules only AFTER the version is confirmed
    is what stops an unsupported version from being misreported as a generic
    contract violation.

    The retired ``render_source_commitment.json`` is never opened here: a v1
    artifact carries source identity only, so reading it would have to invent
    the prompt identity this contract requires. Its absence at the v2 path is
    simply ``RENDER_COMMITMENT_MISSING``.
    """
    path = _step1_render_commitment_path()
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise _Step1RenderCommitmentError("RENDER_COMMITMENT_MISSING") from exc
    except OSError as exc:
        raise _Step1RenderCommitmentError("RENDER_COMMITMENT_UNREADABLE") from exc

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Step1RenderCommitmentError("RENDER_COMMITMENT_UNREADABLE") from exc

    try:
        payload = json.loads(
            text, object_pairs_hook=_reject_duplicate_commitment_members
        )
    except json.JSONDecodeError as exc:
        raise _Step1RenderCommitmentError("RENDER_COMMITMENT_INVALID_JSON") from exc

    if not isinstance(payload, dict):
        raise _Step1RenderCommitmentError("RENDER_COMMITMENT_INVALID_CONTRACT")
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str):
        raise _Step1RenderCommitmentError("RENDER_COMMITMENT_INVALID_CONTRACT")
    if schema_version != _RENDER_COMMITMENT_SCHEMA_VERSION:
        raise _Step1RenderCommitmentError(
            "RENDER_COMMITMENT_UNSUPPORTED_SCHEMA_VERSION"
        )

    if set(payload) != {
        "schema_version",
        "strategy_settings_sha256",
        "portfolio_snapshot_sha256",
        "prompt_sha256",
    }:
        raise _Step1RenderCommitmentError("RENDER_COMMITMENT_INVALID_CONTRACT")

    digests: list[str] = []
    for key in (
        "strategy_settings_sha256",
        "portfolio_snapshot_sha256",
        "prompt_sha256",
    ):
        value = payload[key]
        if not isinstance(value, str) or _LOWERCASE_SHA256_RE.fullmatch(value) is None:
            raise _Step1RenderCommitmentError("RENDER_COMMITMENT_INVALID_CONTRACT")
        digests.append(value)
    return digests[0], digests[1], digests[2]


def _read_current_endpoint_bytes_for_continuity(path: Path) -> bytes | None:
    """Read one fixed CURRENT endpoint's exact bytes; ``None`` on a role-local failure.

    Only ``OSError`` is expected here. The bytes are never decoded, so an
    endpoint holding invalid UTF-8 is still perfectly hashable and is NOT a read
    failure. This is used for both operator sources and the rendered prompt.
    """
    try:
        return path.read_bytes()
    except OSError:
        return None


def _evaluate_step1_render_continuity() -> list[str]:
    """Return this parse's closed continuity reason codes in canonical order."""
    try:
        (
            strategy_expected_sha256,
            portfolio_expected_sha256,
            prompt_expected_sha256,
        ) = _load_step1_render_commitment_digests()
    except _Step1RenderCommitmentError as exc:
        # A commitment problem is a singleton outcome: with nothing valid to
        # compare against, the current endpoints are not read at all.
        return [exc.reason_code]

    strategy_bytes = _read_current_endpoint_bytes_for_continuity(
        _strategy_settings_input_path()
    )
    portfolio_bytes = _read_current_endpoint_bytes_for_continuity(
        _portfolio_snapshot_input_path()
    )
    prompt_bytes = _read_current_endpoint_bytes_for_continuity(step1_prompt_path())

    reason_codes: list[str] = []
    if strategy_bytes is None:
        reason_codes.append("STRATEGY_CURRENT_SOURCE_READ_FAILED")
    if portfolio_bytes is None:
        reason_codes.append("PORTFOLIO_CURRENT_SOURCE_READ_FAILED")
    if prompt_bytes is None:
        # The prompt is a rendered artifact rather than an operator source.
        reason_codes.append("PROMPT_CURRENT_ARTIFACT_READ_FAILED")
    if (
        strategy_bytes is not None
        and hashlib.sha256(strategy_bytes).hexdigest() != strategy_expected_sha256
    ):
        reason_codes.append("STRATEGY_SHA256_MISMATCH")
    if (
        portfolio_bytes is not None
        and hashlib.sha256(portfolio_bytes).hexdigest() != portfolio_expected_sha256
    ):
        reason_codes.append("PORTFOLIO_SHA256_MISMATCH")
    if (
        prompt_bytes is not None
        and hashlib.sha256(prompt_bytes).hexdigest() != prompt_expected_sha256
    ):
        reason_codes.append("PROMPT_SHA256_MISMATCH")
    return reason_codes


def _write_step1_render_continuity_report() -> None:
    """Refresh the report-only render continuity evidence (S1P-2).

    A ``RENDER_ENDPOINT_COMPLETE_MATCH`` claims exactly one thing: during THIS
    parse invocation's sequential endpoint check, the current strategy raw
    bytes, the current portfolio raw bytes, and the current persisted prompt
    bytes SHA-256-equal the values atomically recorded by the most recently
    successfully completed standard Step 1 render — where ``prompt_sha256``
    identifies the UTF-8 encoding of the exact prompt text that render supplied
    to the writer. It does not claim the three endpoints were read
    simultaneously, nor that any of them held still between them.

    It proves nothing about the prompt having been submitted, a response being
    bound to that prompt, operator identity, model or provider identity,
    response authenticity, freshness, T1, H1 availability, permissions, gates,
    final safety, continuous stability, tamper resistance, or cross-platform
    persisted prompt-byte identity beyond this parse-time comparison.

    Report-only: every outcome below leaves Legacy parse, availability,
    permissions, gates, Step 2/3, final safety, publication, pointers, and
    order paths exactly as they were.
    """
    report_path = _step1_render_continuity_report_path()
    retired_report_path = _step1_retired_render_source_continuity_report_path()

    # Load-bearing: a parse must never proceed while ANY earlier parse's report
    # can survive, so both the retired v1 report and the current v2 report are
    # removed first and a failed deletion propagates rather than degrading. A
    # surviving v1 COMPLETE_MATCH beside a fresh v2 UNVERIFIED would be exactly
    # the stale-success evidence this policy exists to prevent.
    retired_report_path.unlink(missing_ok=True)
    report_path.unlink(missing_ok=True)

    reason_codes = _evaluate_step1_render_continuity()
    payload = {
        "schema_version": "step1_render_continuity_report_v2",
        "status": (
            "RENDER_ENDPOINT_COMPLETE_MATCH"
            if not reason_codes
            else "RENDER_ENDPOINT_UNVERIFIED"
        ),
        "reason_codes": reason_codes,
        "authority_effect": "NONE",
    }
    try:
        atomic_write_text(
            report_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
    except OSError:
        # Both stale reports were already removed, so a failed write leaves no
        # stale evidence behind. Legacy parse continues; nothing else changes.
        return


def parse_step1_output(
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Parse and validate the manual Step 1 output into research_output.json."""
    # Report-only layer -1 (S1P-2): deliberately first, so every supported parse
    # attempt either refreshes continuity evidence or leaves none at all. Its
    # outcome never reaches the Legacy parse below.
    _write_step1_render_continuity_report()

    handoff_strategy_settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )
    workflow_approval_source = _sanitize_captured_source(
        capture_research_anchor_approval_source(
            current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        )
    )
    (
        workflow_approval_source_state,
        workflow_approval_source_sha256,
        workflow_approval_source_diagnostics_incomplete,
    ) = _verified_approval_source_summary(workflow_approval_source)

    # Report-only layer 0 (R2B/R2G-5c-2, writer switched by S1A-11; embedded
    # selection switched by S1A-12): deterministic evidence packet + guarded
    # embedded-selection artifact. Built from operator inputs + last-good
    # metadata only (no LLM, no parsed payload). Its embedded
    # active_anchor_registry is selected by a fresh, in-memory
    # approvals-readiness compile. Both disk payloads now come from the Step 1A
    # source behind strict run-time parity guards (legacy bytes on any
    # divergence); support_signals, which grounds off the disk read-back,
    # therefore consumes an identical registry. This still changes no
    # permission, gate, or order path and remains independent of the
    # degraded-mode decision below. The writer runs AGAIN later in this parse
    # (handoff/compiler read-back preparation, rarely the memo layer), so every
    # invocation's statuses are collected here and the switch status reports the
    # invocation that produced the FINAL disk bytes.
    evidence_packet_write_log: list[dict[str, Any]] = []
    evidence_packet_switch_status, embedded_selection_switch_status = (
        _write_evidence_packet_report_only(
            strategy_settings=handoff_strategy_settings,
            evidence_packet_write_log=evidence_packet_write_log,
            captured_approval_source=workflow_approval_source,
        )
    )

    # Report-only layer 0a2 (R2G-1, switched by S1A-3): deterministic baseline
    # active anchor registry, now sourced from the Step 1A accessor with the
    # legacy writer retained as fallback (payload byte-identical either way). The
    # standalone artifact remains an observer; support_signals consumes the
    # embedded registry that the evidence packet selected above.
    active_registry_switch_status = _write_active_research_anchor_registry_report_only(
        strategy_settings=handoff_strategy_settings
    )

    # Report-only layer 0a3 (R2G-2): anchor-source equivalence oracle. Compares the
    # usable-anchor grounding view of the authoritative evidence_packet.research_anchors
    # (what support_signals reads today) against the R2G-1 active registry. Diagnostic
    # only: switches no consumer, changes no behavior, consumed by nothing.
    _write_anchor_source_equivalence_report_only(strategy_settings=handoff_strategy_settings)

    # Report-only layer 0b (R2C): small analyst-memo parse/validation. Only runs
    # when a raw memo output exists; it writes its own two artifacts and never
    # gates the pipeline, never feeds the degraded-mode decision, and can never
    # permit NEW_BUY.
    analyst_memo_summary = _parse_analyst_memo_report_only(
        strategy_settings=handoff_strategy_settings,
        evidence_packet_write_log=evidence_packet_write_log,
        captured_approval_source=workflow_approval_source,
    )

    # Report-only layer 0c (R2D): deterministic strict-handoff compiler. Compiles
    # the evidence packet (+ optional valid analyst memo) into a structurally
    # complete candidate, validates it with the existing validator, and writes
    # compiled_* artifacts. It is NOT fed into research_degraded_mode_decision and
    # never changes allowed_actions; evidence-only / invalid-memo never support
    # NEW_BUY. The raw Deep Research candidate below remains the active source.
    compiled_handoff_summary = _compile_research_handoff_report_only(
        strategy_settings=handoff_strategy_settings,
        evidence_packet_write_log=evidence_packet_write_log,
        captured_approval_source=workflow_approval_source,
    )

    # Report-only layer 0c2 (R2G-4): advisory research-anchor CANDIDATES. Suggests
    # anchors an operator might author, derived from the analyst memo + the (just
    # written) support-signal gaps + active-registry coverage. Strictly inert:
    # consumed by NOTHING (support_signals, active registry, preview, candidate,
    # eligibility, availability, gates, Step 2/3/4, weekly all ignore it), never
    # active, and it cannot affect allowed_actions / add NEW_BUY / ORDER_COMPILATION.
    _write_research_anchor_candidates_report_only(strategy_settings=handoff_strategy_settings)

    # Report-only layer 0c3 (R2G-5a): operator-approval MANIFEST validator. Reads
    # inputs/current/research_anchor_approvals.yaml and writes
    # research_anchor_approvals_validation.json — a diagnostic that answers "would
    # this operator-completed anchor be eligible for a FUTURE R2G-5b compiler?".
    # Strictly inert: it activates NOTHING, consumed by NOTHING (support_signals,
    # active registry, preview, candidate, eligibility, availability, gates,
    # Step 2/3/4, weekly all ignore it), and cannot affect allowed_actions / add
    # NEW_BUY / ORDER_COMPILATION. operator_completed_anchor_sha256 is the
    # activation-binding hash; candidate_sha256 is audit-only.
    approvals_validation_switch_status = _write_research_anchor_approvals_validation_report_only(
        strategy_settings=handoff_strategy_settings,
        captured_approval_source=workflow_approval_source,
    )

    # Report-only layer 0c3b (R2G-5d-0, switched by S1A-5): operator-REVOCATION
    # manifest validator. Reads the optional revocations: section of
    # research_anchor_approvals.yaml and writes
    # research_anchor_revocations_validation.json — a diagnostic that answers
    # "does this revocation deterministically bind to one operator-approved anchor?".
    # Strictly inert: it APPLIES nothing, does not change the approvals-inclusive
    # registry compiler / support_signals / evidence_packet registry selection /
    # readiness, is consumed by NOTHING, and cannot affect allowed_actions / add
    # NEW_BUY / ORDER_COMPILATION. Unknown target fails closed (mandatory amendment).
    revocations_validation_switch_status = _write_research_anchor_revocations_validation_report_only(
        strategy_settings=handoff_strategy_settings,
        captured_approval_source=workflow_approval_source,
    )

    # Report-only layer 0c4 (R2G-5b, switched by S1A-6): approvals-inclusive active
    # registry + dual-read diff. Overlays validated operator-approved anchors
    # (recomputed directly from research_anchor_approvals.yaml; the R2G-5a artifact /
    # would_activate are never trusted as authority) onto the baseline registry, then
    # diffs the two. The with-approvals payload now comes from the Step 1A accessor
    # behind a run-time parity guard (legacy bytes on any divergence); the dual-read
    # diff keeps its legacy in-memory lineage and is NOT switched. The on-disk
    # artifact remains a SEPARATE observer; the evidence packet switch above
    # recomputes its own fresh in-memory approvals-inclusive registry.
    with_approvals_switch_status, dual_read_diff_switch_status = (
        _write_approval_registry_dual_read_report_only(
            strategy_settings=handoff_strategy_settings,
            captured_approval_source=workflow_approval_source,
        )
    )

    # Report-only layer 0c5 (R2G-5c-0, switched by S1A-7): approval-registry
    # switch-READINESS disk observer. The on-disk artifact is still diagnostic/
    # write-only — its payload now comes from the Step 1A accessor (legacy write
    # wrapper retained as fallback; both share one deterministic builder) — while
    # the actual 5c-2 switch above recomputes readiness from fresh in-memory
    # YAML-derived objects and never reads this JSON.
    readiness_switch_status = _write_approval_registry_switch_readiness_report_only(
        strategy_settings=handoff_strategy_settings,
        captured_approval_source=workflow_approval_source,
    )

    # Report-only layer 0c5b (S1A-3/4/5/6/7/8/11/12): per-artifact switch
    # provenance for the eight switched writers (the six above plus the S1A-11
    # evidence_packet writer and the S1A-12 embedded-selection writer from
    # layer 0). Written once, AFTER the last possible evidence-packet/selection
    # write invocation (the layer-0c compiler read-back preparation above), so
    # the packet/selection entries describe the invocation that produced the
    # FINAL on-disk bytes — never stale first-write provenance. Diagnostic only,
    # consumed by nothing; never gates, never grants actions.
    (
        evidence_packet_switch_status,
        embedded_selection_switch_status,
        evidence_packet_write_invocations,
    ) = _resolve_final_evidence_packet_write_statuses(evidence_packet_write_log)
    _write_step1a_artifact_switch_status_report_only(
        [
            active_registry_switch_status,
            approvals_validation_switch_status,
            revocations_validation_switch_status,
            with_approvals_switch_status,
            dual_read_diff_switch_status,
            readiness_switch_status,
            evidence_packet_switch_status,
            embedded_selection_switch_status,
        ],
        evidence_packet_write_invocations=evidence_packet_write_invocations,
    )

    # Report-only layer 0c6 (R2G-5c-1): support_signals dual-ground DRY-RUN diff.
    # Compares support_signals grounding under the embedded registry vs a freshly
    # compiled approvals-inclusive dry-run view. The artifact itself remains
    # write-only and cannot affect allowed_actions / add NEW_BUY / ORDER_COMPILATION.
    _write_support_signals_dual_ground_diff_report_only(
        strategy_settings=handoff_strategy_settings,
        captured_approval_source=workflow_approval_source,
    )

    # Authoritative bundle-consistency boundary.  The artifacts above are
    # diagnostic candidates until this deterministic join succeeds.  On a
    # mismatch, replace every activation-bearing view with the existing safe
    # baseline/fail-closed selection and rebuild compiled support from that safe
    # evidence before actionable preview or promotion can consume it.
    (
        workflow_approval_source_identity_mismatch,
        fail_closed_compiled_handoff_summary,
    ) = _enforce_complete_step1_workflow_approval_source_identity(
        approval_source=workflow_approval_source,
        strategy_settings=handoff_strategy_settings,
        source_summary_sha256=workflow_approval_source_sha256,
    )
    if fail_closed_compiled_handoff_summary is not None:
        compiled_handoff_summary = fail_closed_compiled_handoff_summary

    # Report-only layer 0c7 (R2G-6b): grounding-status observatory. Reads only the
    # already-written Step 1 diagnostic artifacts and writes a single inert summary.
    # Consumed by NOTHING: not readiness, not evidence_packet, not support_signals,
    # not availability, not Step 2/3/4, not gates, not weekly, not broker/live, and
    # never allowed_actions / order readiness / order path.
    _write_grounding_status_observatory_report_only(
        workflow_identity_mismatch=workflow_approval_source_identity_mismatch
    )

    # Report-only layer 0c8 (S1A-1): Step 1A shadow-run diff. Calls the pure
    # extraction bundle and compares it against the already-written Step 1
    # deterministic/R2G artifacts. It writes only a diagnostic diff and switches
    # no production artifact path; downstream, gates, permissions, and order paths
    # consume NOTHING from this layer. Any failure is recorded best-effort and
    # swallowed.
    step1a_shadow_summary = _write_step1a_grounding_compile_shadow_diff_report_only(
        strategy_settings=handoff_strategy_settings,
        captured_approval_source=workflow_approval_source,
    )

    # Report-only layer 0d (R2E.5b-0): a SEPARATE actionable-handoff preview built
    # from the just-written compiled_support_signals + evidence packet + memo. It
    # previews which tickers WOULD become actionable rows IF a future PR opened an
    # actionable path; it never mutates the active compiled handoff (which stays
    # non-actionable), is NOT fed into the availability evaluator or Step 2, and
    # never changes allowed_actions / adds NEW_BUY / ORDER_COMPILATION.
    actionable_handoff_preview_summary = _build_actionable_handoff_preview_report_only()

    # Report-only layer 0e (R2E.5b-1): a SEPARATE actionable compiled-handoff
    # candidate that overlays the preview's actionable rows onto a full strict
    # handoff and validates it with the existing validator. It answers only "does
    # the future actionable handoff shape validate?" — it never mutates the active
    # compiled handoff (which stays non-actionable), is NOT fed into the availability
    # evaluator / Step 2 / weekly path / final safety gate, and never adds NEW_BUY /
    # ORDER_COMPILATION.
    actionable_handoff_candidate_summary = _build_actionable_handoff_candidate_report_only(
        strategy_settings=handoff_strategy_settings
    )

    # Report-only layer 0f (R2E.5b-3): deterministic promotion-ELIGIBILITY check
    # over the just-written actionable candidate chain. It answers only "WOULD the
    # separate actionable candidate be eligible for a future promotion?" — it never
    # promotes (no active pointer / effective handoff is created), never mutates
    # the active compiled handoff, is NOT fed into the availability evaluator /
    # Step 2 / weekly / gates, and never adds NEW_BUY / ORDER_COMPILATION.
    promotion_eligibility_summary = _build_actionable_promotion_eligibility_report_only(
        strategy_settings=handoff_strategy_settings
    )

    # Report-only layer 0g (R2E.5b-4): pointer PREVIEW + effective-handoff PREVIEW.
    # Previews what the future active-pointer promotion WOULD look like from the
    # just-written eligibility verdict. Nothing is promoted: the reserved
    # active_research_handoff_source.json / research_handoff_candidate_effective.json
    # are NOT created, no consumer reads the previews (not fed into availability /
    # Step 2 / weekly / gates), and no NEW_BUY / ORDER_COMPILATION is added.
    pointer_preview_summary = _build_actionable_promotion_pointer_preview_report_only(
        strategy_settings=handoff_strategy_settings
    )

    # Layer 0h (R2E.5b-5a): REAL active-pointer writer. When the pointer preview
    # says would_promote, the real
    # active_research_handoff_source.json + research_handoff_candidate_effective.json
    # (+ validation) are written with promotion_status=pending_gates. Availability
    # may recognize them only as a non-actionable pending-gates diagnostic; Step 2,
    # weekly actionable flow, order compiler, and gates remain closed, and no
    # NEW_BUY / ORDER_COMPILATION is added. Fail-closed: a non-promotable run
    # writes only the status artifact and removes any stale pointer files.
    active_pointer_summary = _write_actionable_promotion_pointer_report_only(
        strategy_settings=handoff_strategy_settings
    )

    try:
        payload = extract_research_json(
            raw_output_path=step1_raw_output_path(),
            output_path=step1_research_output_path(),
            pretty=True,
        )
    except Exception as exc:
        _write_no_output_research_availability_artifacts_report_only(
            strategy_settings=handoff_strategy_settings,
            diagnostic_reason="step1 parse failed before research_output.json was produced.",
            parse_error=str(exc),
        )
        # The parse error remains authoritative.  This best-effort observer runs
        # only after the no-output availability mapping has reached final disk
        # state and can never mask or change the original failure.
        _write_step1a_retirement_observation_report_only()
        raise

    # Report-only layer 1: validate the raw parsed output as-is.
    handoff_validation = validate_research_handoff(
        payload,
        strategy_settings=handoff_strategy_settings,
    )
    write_json(
        step1_research_handoff_validation_path(),
        research_handoff_validation_result_to_dict(handoff_validation),
    )

    # Report-only layer 2: deterministically normalize a strict-handoff
    # candidate, then validate the candidate. This never mutates
    # research_output.json or the raw handoff validation artifact, and never
    # blocks the pipeline regardless of candidate validity.
    normalization = normalize_research_handoff_candidate(
        payload,
        strategy_settings=handoff_strategy_settings,
    )
    write_json(step1_research_handoff_candidate_path(), normalization.candidate)
    write_json(
        step1_research_handoff_candidate_normalization_path(),
        research_handoff_normalization_result_to_dict(normalization),
    )
    candidate_validation = validate_research_handoff(
        normalization.candidate,
        strategy_settings=handoff_strategy_settings,
    )
    write_json(
        step1_research_handoff_candidate_validation_path(),
        research_handoff_validation_result_to_dict(candidate_validation),
    )

    # Report-only layer 3 (PR B): persist the last-known-good strict handoff to
    # artifacts/state/ only when the candidate is strict-valid. This is a writer
    # only — no downstream step reads it, it never blocks the pipeline, and a
    # writer failure is recorded rather than raised.
    last_good_result = _write_last_good_research_handoff_report_only(
        candidate=normalization.candidate,
        candidate_validation=candidate_validation,
        strategy_settings=handoff_strategy_settings,
        source_as_of_date=payload.get("as_of") if isinstance(payload, Mapping) else None,
    )
    write_json(
        step1_last_good_write_result_path(),
        last_good_research_handoff_write_result_to_dict(last_good_result),
    )

    # Layers 4-6: deterministic research availability / freshness / degraded-mode
    # decision artifacts (PR C), the R2E.5b-6a/6b promoted verification + gate
    # dry-run artifacts, and — R2E.5b-6c — the Step 2 decision-only permission
    # upgrade. Two-pass: a preliminary (pending-gates-posture) evaluation feeds
    # the verification/dry-run layer, then the final evaluation consumes those
    # artifacts and may upgrade an eligible pending-gates run to
    # STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY (HOLD / NO_TRADE /
    # PROMOTED_RESEARCH_DECISION only — never NEW_BUY / ORDER_COMPILATION).
    # Defensive: parse never fails because of this layer.
    availability, promoted_dry_run_summary = _evaluate_research_availability_report_only(
        candidate=normalization.candidate,
        candidate_validation=candidate_validation,
        strategy_settings=handoff_strategy_settings,
        payload=payload,
    )
    # Phase 1A retirement instrumentation.  This is deliberately last: every
    # observed source below (including final packet/selection provenance,
    # support signals, production-sourced observatory, shadow, and final
    # availability/allowed-actions mapping) has reached its final current-run
    # write state.  It reads those current mappings once and is consumed by
    # nothing; it does not recompute availability or permission outcomes.
    retirement_observation_summary = _write_step1a_retirement_observation_report_only()

    return {
        "research_output_path": str(step1_research_output_path()),
        "research_handoff_validation_path": str(step1_research_handoff_validation_path()),
        "research_handoff_valid": str(handoff_validation.valid),
        "research_handoff_candidate_path": str(step1_research_handoff_candidate_path()),
        "research_handoff_candidate_normalization_path": str(
            step1_research_handoff_candidate_normalization_path()
        ),
        "research_handoff_candidate_validation_path": str(
            step1_research_handoff_candidate_validation_path()
        ),
        "research_handoff_candidate_valid": str(candidate_validation.valid),
        "research_handoff_candidate_source_shape": normalization.source_shape,
        "research_handoff_candidate_normalization_mode": normalization.normalization_mode,
        "last_good_research_handoff_write_result_path": str(step1_last_good_write_result_path()),
        "last_good_research_handoff_written": str(last_good_result.wrote),
        "last_good_research_handoff_path": (
            str(last_good_result.handoff_path) if last_good_result.handoff_path is not None else ""
        ),
        "research_availability_path": str(step1_research_availability_path()),
        "research_freshness_report_path": str(step1_research_freshness_report_path()),
        "research_degraded_mode_decision_path": str(step1_research_degraded_mode_decision_path()),
        "research_availability_state": availability.state,
        "research_availability_fresh": str(availability.fresh_research_available),
        "evidence_packet_path": str(step1_evidence_packet_path()),
        "analyst_memo_present": str(analyst_memo_summary.get("present", False)),
        "analyst_memo_valid": str(analyst_memo_summary.get("valid", False)),
        "analyst_memo_validation_path": analyst_memo_summary.get("validation_path", ""),
        "analyst_memo_path": analyst_memo_summary.get("memo_path", ""),
        "compiled_research_handoff_candidate_path": compiled_handoff_summary.get("candidate_path", ""),
        "compiled_research_handoff_validation_path": compiled_handoff_summary.get("validation_path", ""),
        "compiled_research_handoff_metadata_path": compiled_handoff_summary.get("metadata_path", ""),
        "compiled_support_signals_path": compiled_handoff_summary.get("support_signals_path", ""),
        "actionable_handoff_preview_path": actionable_handoff_preview_summary.get(
            "actionable_handoff_preview_path", ""
        ),
        "actionable_handoff_candidate_path": actionable_handoff_candidate_summary.get(
            "actionable_candidate_path", ""
        ),
        "actionable_handoff_validation_path": actionable_handoff_candidate_summary.get(
            "actionable_validation_path", ""
        ),
        "actionable_handoff_metadata_path": actionable_handoff_candidate_summary.get(
            "actionable_metadata_path", ""
        ),
        "actionable_handoff_validation_passed": actionable_handoff_candidate_summary.get(
            "validation_passed", ""
        ),
        "actionable_promotion_eligibility_path": promotion_eligibility_summary.get(
            "actionable_promotion_eligibility_path", ""
        ),
        "actionable_promotion_eligible": promotion_eligibility_summary.get(
            "eligible_for_promotion", ""
        ),
        "actionable_promotion_pointer_preview_path": pointer_preview_summary.get(
            "actionable_promotion_pointer_preview_path", ""
        ),
        "actionable_promotion_would_promote": pointer_preview_summary.get("would_promote", ""),
        "actionable_effective_handoff_preview_path": pointer_preview_summary.get(
            "actionable_effective_handoff_preview_path", ""
        ),
        "actionable_effective_handoff_preview_validation_path": pointer_preview_summary.get(
            "actionable_effective_handoff_preview_validation_path", ""
        ),
        "active_pointer_created": active_pointer_summary.get("active_pointer_created", ""),
        "active_research_handoff_source_path": active_pointer_summary.get(
            "active_research_handoff_source_path", ""
        ),
        "effective_research_handoff_path": active_pointer_summary.get(
            "effective_research_handoff_path", ""
        ),
        "active_pointer_write_status_path": active_pointer_summary.get(
            "active_pointer_write_status_path", ""
        ),
        "promoted_handoff_step2_verification_path": promoted_dry_run_summary.get(
            "promoted_handoff_step2_verification_path", ""
        ),
        "promoted_step2_gate_dry_run_path": promoted_dry_run_summary.get(
            "promoted_step2_gate_dry_run_path", ""
        ),
        "promoted_step2_gate_dry_run_would_allow": promoted_dry_run_summary.get(
            "promoted_step2_gate_dry_run_would_allow", ""
        ),
        "promoted_handoff_step3_audit_verification_path": promoted_dry_run_summary.get(
            "promoted_handoff_step3_audit_verification_path", ""
        ),
        "promoted_step3_audit_gate_dry_run_path": promoted_dry_run_summary.get(
            "promoted_step3_audit_gate_dry_run_path", ""
        ),
        "promoted_step3_audit_gate_dry_run_would_allow": promoted_dry_run_summary.get(
            "promoted_step3_audit_gate_dry_run_would_allow", ""
        ),
        "promoted_step4_readiness_verification_path": promoted_dry_run_summary.get(
            "promoted_step4_readiness_verification_path", ""
        ),
        "promoted_step4_preview_gate_dry_run_path": promoted_dry_run_summary.get(
            "promoted_step4_preview_gate_dry_run_path", ""
        ),
        "promoted_step4_preview_gate_dry_run_would_allow": promoted_dry_run_summary.get(
            "promoted_step4_preview_gate_dry_run_would_allow", ""
        ),
        "promoted_final_safety_preflight_path": promoted_dry_run_summary.get(
            "promoted_final_safety_preflight_path", ""
        ),
        "promoted_final_safety_preflight_passed": promoted_dry_run_summary.get(
            "promoted_final_safety_preflight_passed", ""
        ),
        "compiled_research_handoff_mode": compiled_handoff_summary.get("compilation_mode", ""),
        "compiled_research_handoff_valid": compiled_handoff_summary.get("compiled_candidate_valid", ""),
        "research_anchor_approvals_source_state": workflow_approval_source_state,
        "research_anchor_approvals_source_sha256": (
            ""
            if workflow_approval_source_identity_mismatch
            else (workflow_approval_source_sha256 or "")
        ),
        "workflow_approval_source_identity_mismatch": str(
            workflow_approval_source_identity_mismatch
        ),
        "grounding_diagnostics_incomplete": str(
            workflow_approval_source_diagnostics_incomplete
            or workflow_approval_source_identity_mismatch
        ),
        "grounding_status_observatory_path": str(step1_grounding_status_observatory_path()),
        "step1a_grounding_compile_shadow_diff_path": step1a_shadow_summary.get("path", ""),
        "step1a_grounding_compile_shadow_diff_status": step1a_shadow_summary.get("comparison_status", ""),
        "active_research_anchor_registry_writer_source": str(
            active_registry_switch_status.get("writer_source", "")
        ),
        "research_anchor_approvals_validation_writer_source": str(
            approvals_validation_switch_status.get("writer_source", "")
        ),
        "research_anchor_revocations_validation_writer_source": str(
            revocations_validation_switch_status.get("writer_source", "")
        ),
        "active_research_anchor_registry_with_approvals_writer_source": str(
            with_approvals_switch_status.get("writer_source", "")
        ),
        "approval_registry_dual_read_diff_writer_source": str(
            dual_read_diff_switch_status.get("writer_source", "")
        ),
        "approval_registry_switch_readiness_writer_source": str(
            readiness_switch_status.get("writer_source", "")
        ),
        "evidence_packet_writer_source": str(
            evidence_packet_switch_status.get("writer_source", "")
        ),
        "embedded_active_anchor_registry_selection_writer_source": str(
            embedded_selection_switch_status.get("writer_source", "")
        ),
        "step1a_artifact_switch_status_path": str(step1a_artifact_switch_status_path()),
        "step1a_retirement_observation_path": retirement_observation_summary.get("path", ""),
        "step1a_retirement_observation_completeness": retirement_observation_summary.get(
            "observation_completeness", ""
        ),
        "schema_version": str(payload.get("schema_version", "")),
    }


_EMPTY_PROMOTED_STEP2_SUMMARY: dict[str, Any] = {
    "promoted_handoff_step2_verification_path": "",
    "promoted_step2_gate_dry_run_path": "",
    "promoted_step2_gate_dry_run_would_allow": "",
    "promoted_handoff_step3_audit_verification_path": "",
    "promoted_step3_audit_gate_dry_run_path": "",
    "promoted_step3_audit_gate_dry_run_would_allow": "",
    "promoted_step4_readiness_verification_path": "",
    "promoted_step4_preview_gate_dry_run_path": "",
    "promoted_step4_preview_gate_dry_run_would_allow": "",
    "promoted_final_safety_preflight_path": "",
    "promoted_final_safety_preflight_passed": "",
    "verification": None,
    "dry_run": None,
    "step3_audit_verification": None,
    "step3_audit_dry_run": None,
    "step4_readiness_verification": None,
    "step4_preview_dry_run": None,
    "final_safety_preflight": None,
}


def _evaluate_research_availability_report_only(
    *,
    candidate: Mapping[str, Any],
    candidate_validation: Any,
    strategy_settings: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
    h1_mapped_facts: Any | None = None,
):
    """Evaluate availability, write the promoted Step 2 artifacts, and persist.

    Step 1 parse must never fail because of this layer, so any error is
    swallowed and recorded into the artifacts as a conservative NO_OUTPUT-style
    decision rather than raised.

    R2E.5b-6c two-pass flow: pass 1 evaluates the availability WITHOUT the
    promoted-Step-2 inputs (the pre-upgrade / pending-gates posture); the
    R2E.5b-6a verification and R2E.5b-6b dry-run are computed against that
    preliminary decision and written; pass 2 re-evaluates WITH this run's
    verification + dry-run, which may upgrade an eligible pending-gates run to
    the Step 2 decision-only state. Fail closed: if the promoted layer errors,
    the preliminary (pending-gates) result is written unchanged.

    ``h1_mapped_facts`` is an optional, already-validated
    ``H1MappedRecognitionFacts`` passed straight through to the availability
    owner in-process. It is never persisted and never reconstructed from a
    stored bridge object: on restart the upstream evidence is re-read and the
    bridge rebuilds the facts under its own contract. ``None`` (the default)
    leaves every Legacy outcome and artifact byte-for-byte unchanged.
    """
    try:
        last_good = read_last_good_research_handoff(step1_state_dir())
        # now_date is the current run's SSOT date (strategy settings as_of),
        # falling back to the parsed research as_of. source_as_of_date is the
        # research as_of for the current handoff candidate.
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        payload_as_of = payload.get("as_of") if isinstance(payload, Mapping) else None
        # R2E.1 (report-only recognition): feed the deterministic compiled
        # evidence-first handoff (Step 1C) so a valid+fresh compiled candidate is
        # recognized as STRICT_FRESH_EVIDENCE_ONLY (HOLD / NO_TRADE only) instead
        # of a misleading INVALID_CONTRACT / DEGRADED_*. This never adds NEW_BUY /
        # ORDER_COMPILATION. Only the normal parse path (parsed output present) is
        # fed compiled inputs; a hard parse failure stays NO_OUTPUT (see the
        # no-output writer, which is intentionally left unchanged).
        compiled_inputs = _compiled_handoff_availability_inputs()
        base_kwargs: dict[str, Any] = {
            "candidate_validation": candidate_validation,
            "candidate": candidate,
            "strategy_settings": strategy_settings,
            "source_as_of_date": payload_as_of,
            "now_date": settings_as_of or payload_as_of,
            "last_good_handoff": last_good.handoff,
            "last_good_metadata": last_good.metadata,
            "compiled_candidate_validation": compiled_inputs["compiled_candidate_validation"],
            "compiled_metadata": compiled_inputs["compiled_metadata"],
            "compiled_source_as_of_date": settings_as_of,
            "compiled_source_artifacts": compiled_inputs["compiled_source_artifacts"],
            "compiled_support_signals": compiled_inputs["compiled_support_signals"],
            "promoted_pointer": compiled_inputs["promoted_pointer"],
            "promoted_effective_handoff": compiled_inputs["promoted_effective_handoff"],
            "promoted_effective_validation": compiled_inputs["promoted_effective_validation"],
            "promoted_source_artifacts": compiled_inputs["promoted_source_artifacts"],
            "h1_mapped_facts": h1_mapped_facts,
        }
        # Pass 1: pre-upgrade posture; this is what the verification / dry-run
        # layer diagnoses (the dry-run's pending-gates criteria stay meaningful).
        preliminary = evaluate_research_availability(**base_kwargs)
        promoted_step2 = _write_promoted_step2_gate_dry_run_report_only(
            strategy_settings=strategy_settings,
            research_decision=research_degraded_mode_decision_to_dict(preliminary),
        )
        # Pass 2 (R2E.5b-6c): consume this run's verification + dry-run. Only an
        # eligible pending-gates run upgrades; every other outcome is identical
        # to the preliminary evaluation.
        promoted_source_artifacts = dict(compiled_inputs["promoted_source_artifacts"])
        if promoted_step2.get("promoted_handoff_step2_verification_path"):
            promoted_source_artifacts["promoted_handoff_step2_verification"] = promoted_step2[
                "promoted_handoff_step2_verification_path"
            ]
        if promoted_step2.get("promoted_step2_gate_dry_run_path"):
            promoted_source_artifacts["promoted_step2_gate_dry_run"] = promoted_step2[
                "promoted_step2_gate_dry_run_path"
            ]
        availability = evaluate_research_availability(
            **{**base_kwargs, "promoted_source_artifacts": promoted_source_artifacts},
            promoted_step2_verification=promoted_step2.get("verification"),
            promoted_step2_gate_dry_run=promoted_step2.get("dry_run"),
        )
        promoted_step3_audit = _write_promoted_step3_audit_dry_run_report_only(
            strategy_settings=strategy_settings,
            research_decision=research_degraded_mode_decision_to_dict(availability),
        )
        if promoted_step3_audit.get("promoted_handoff_step3_audit_verification_path"):
            promoted_source_artifacts["promoted_handoff_step3_audit_verification"] = (
                promoted_step3_audit["promoted_handoff_step3_audit_verification_path"]
            )
        if promoted_step3_audit.get("promoted_step3_audit_gate_dry_run_path"):
            promoted_source_artifacts["promoted_step3_audit_gate_dry_run"] = (
                promoted_step3_audit["promoted_step3_audit_gate_dry_run_path"]
            )
        availability = evaluate_research_availability(
            **{**base_kwargs, "promoted_source_artifacts": promoted_source_artifacts},
            promoted_step2_verification=promoted_step2.get("verification"),
            promoted_step2_gate_dry_run=promoted_step2.get("dry_run"),
            promoted_step3_audit_verification=promoted_step3_audit.get(
                "step3_audit_verification"
            ),
            promoted_step3_audit_gate_dry_run=promoted_step3_audit.get(
                "step3_audit_dry_run"
            ),
        )
        # R2E.5b-7b: report-only Step 4 readiness diagnostics, computed from the
        # FINAL decision. Deliberately NOT fed back into any availability
        # evaluation and NOT added to promoted_source_artifacts: nothing
        # consumes these artifacts in 7b (consumed_by_availability=false).
        promoted_step4 = _write_promoted_step4_readiness_dry_run_report_only(
            strategy_settings=strategy_settings,
            research_decision=research_degraded_mode_decision_to_dict(availability),
        )
        # R2E.5b-7c: rowless final-safety preflight, also computed from the FINAL
        # decision and reading the just-written 7b artifacts. Same posture: NOT
        # fed back into availability, NOT added to promoted_source_artifacts,
        # consumed by nothing (consumed_by_availability/step4/gates=false).
        promoted_final_safety = _write_promoted_final_safety_preflight_report_only(
            strategy_settings=strategy_settings,
            research_decision=research_degraded_mode_decision_to_dict(availability),
        )
        promoted_summary = {
            **promoted_step2,
            **promoted_step3_audit,
            **promoted_step4,
            **promoted_final_safety,
        }
        write_json(
            step1_research_availability_path(),
            research_availability_result_to_dict(availability),
        )
        write_json(
            step1_research_freshness_report_path(),
            research_freshness_report_to_dict(availability),
        )
        write_json(
            step1_research_degraded_mode_decision_path(),
            research_degraded_mode_decision_to_dict(availability),
        )
        return availability, promoted_summary
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        fallback = evaluate_research_availability(
            candidate_validation=None,
            candidate=None,
            strategy_settings=None,
            source_as_of_date=None,
            now_date=None,
        )
        try:
            error_payload = {
                **research_availability_result_to_dict(fallback),
                "evaluator_error": f"availability evaluation failed (report-only, not raised): {exc}",
            }
            write_json(step1_research_availability_path(), error_payload)
            write_json(
                step1_research_freshness_report_path(),
                research_freshness_report_to_dict(fallback),
            )
            write_json(
                step1_research_degraded_mode_decision_path(),
                research_degraded_mode_decision_to_dict(fallback),
            )
        except Exception:  # noqa: BLE001 - best-effort artifact emission
            pass
        return fallback, dict(_EMPTY_PROMOTED_STEP2_SUMMARY)


def refresh_promoted_step3_audit_only_permission_after_step2(
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Refresh Step 3 audit-only eligibility after Step 2 parse writes its artifacts.

    Step 1 parse runs before the promoted Step 2 marker and decision packet
    exist, so the 6e Step 3 verifier/dry-run fail closed at that point. The
    promoted Step 2 parse calls this deterministic hook after it writes those
    artifacts. This rewrites only the availability/permission diagnostics and
    the promoted Step 3 verification/dry-run files; it does not run Step 3,
    Step 4, an order compiler, broker automation, or any live execution path.
    """
    settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )
    candidate = _read_json_if_exists(step1_research_handoff_candidate_path())
    candidate_validation = _read_json_if_exists(step1_research_handoff_candidate_validation_path())
    payload = _read_json_if_exists(step1_research_output_path())
    if not isinstance(candidate, Mapping) or not isinstance(payload, Mapping):
        return {
            "promoted_handoff_step3_audit_verification_path": "",
            "promoted_step3_audit_gate_dry_run_path": "",
            "promoted_step3_audit_gate_dry_run_would_allow": "",
            "research_availability_state": "",
            "promoted_step3_audit_only": "False",
        }

    availability, promoted_summary = _evaluate_research_availability_report_only(
        candidate=candidate,
        candidate_validation=candidate_validation,
        strategy_settings=settings,
        payload=payload,
    )
    return {
        "promoted_handoff_step3_audit_verification_path": str(
            promoted_summary.get("promoted_handoff_step3_audit_verification_path", "")
        ),
        "promoted_step3_audit_gate_dry_run_path": str(
            promoted_summary.get("promoted_step3_audit_gate_dry_run_path", "")
        ),
        "promoted_step3_audit_gate_dry_run_would_allow": str(
            promoted_summary.get("promoted_step3_audit_gate_dry_run_would_allow", "")
        ),
        "research_availability_state": availability.state,
        "promoted_step3_audit_only": str(availability.promoted_step3_audit_only),
    }


def _invalidate_current_research_availability_artifacts() -> None:
    """Remove every current Step 1 artifact that can carry an availability state claim.

    Exactly three leaves qualify: the authority-bearing degraded-mode decision
    that Step 2/3/4, the final safety gate, and the weekly router consume, plus
    its two report-only diagnostics. The promoted dry-run artifacts are derived
    from a decision rather than being one, are recomputed from the fresh decision
    on every evaluation, and no consumer reads them as the current Step 1
    permission claim, so they are deliberately out of scope.

    The authority-bearing decision is removed FIRST. If a later diagnostic
    deletion then fails, the consumed permission claim is already gone and
    downstream authority fails closed on a missing artifact, so no rollback is
    needed and none is provided. A failed deletion propagates rather than
    degrading — this deletion, not the rebuild in the caller, is what guarantees
    an H1 recognition claim cannot outlive the mapping completion that justified
    it.
    """
    step1_research_degraded_mode_decision_path().unlink(missing_ok=True)
    step1_research_availability_path().unlink(missing_ok=True)
    step1_research_freshness_report_path().unlink(missing_ok=True)


def _resolve_no_output_warrant_for_h1_refresh() -> _NoOutputWarrant:
    """Distinguish genuine Legacy output unavailability from missing context."""
    try:
        raw_text = read_text(step1_raw_output_path())
    except FileNotFoundError:
        return _NoOutputWarrant(
            _NoOutputWarrantResult.OUTPUT_UNAVAILABLE,
            "Step 1 raw output is absent.",
        )
    except PermissionError:
        return _NoOutputWarrant(
            _NoOutputWarrantResult.NO_BASE_CONTEXT,
            "Step 1 raw output is not readable: PermissionError.",
        )
    except IsADirectoryError:
        return _NoOutputWarrant(
            _NoOutputWarrantResult.NO_BASE_CONTEXT,
            "Step 1 raw output path is a directory.",
        )
    except UnicodeDecodeError:
        return _NoOutputWarrant(
            _NoOutputWarrantResult.NO_BASE_CONTEXT,
            "Step 1 raw output is not valid UTF-8.",
        )

    # Successful preload proves that a later ArtifactSchemaError originates
    # from validating the raw output instance, not from schema infrastructure.
    load_artifact_schema("research_output.schema.json")

    try:
        parse_research_output_text(raw_text)
    except ResearchExtractionError as exc:
        return _NoOutputWarrant(
            _NoOutputWarrantResult.OUTPUT_UNAVAILABLE,
            f"Step 1 raw output extraction failed: {exc}",
        )
    except ArtifactSchemaError as exc:
        return _NoOutputWarrant(
            _NoOutputWarrantResult.OUTPUT_UNAVAILABLE,
            f"Step 1 raw output failed research-output schema validation: {exc}",
        )

    return _NoOutputWarrant(
        _NoOutputWarrantResult.NO_BASE_CONTEXT,
        "Step 1 raw output validates, but derived base context is absent.",
    )


@dataclass(frozen=True, slots=True)
class H1ResearchAvailabilityRefreshResult:
    """SAME-RUN composite over exactly one availability evaluation/write.

    ``public_projection`` is the existing, unchanged legacy
    ``refresh_research_availability_for_h1_replacement`` return dict —
    ``dict[str, str]`` — kept here only so a future caller that needs both
    views does not force a second evaluation. ``h1_selection`` is the narrow,
    immutable :class:`H1MappedResearchSelectionProjection` this SAME
    evaluation already computed, or ``None`` when no availability evaluation
    ran at all this call (the "no base context" outcome, where
    ``public_projection["research_availability_state"] == ""``).

    Neither field is, or holds a reference into, the raw
    ``ResearchAvailabilityResult``: ``public_projection`` is a plain string
    dict and ``h1_selection`` copies only immutable scalars. A future Phase-3
    admission factory may consume ``h1_selection`` for SAME-RUN selection
    authority without ever receiving the owner's mutable ``allowed_actions`` /
    ``h1_mapped_recognition`` containers or any permission vector.
    """

    public_projection: dict[str, str]
    h1_selection: H1MappedResearchSelectionProjection | None


def refresh_research_availability_for_h1_replacement_with_selection(
    *,
    h1_mapped_facts: Any | None = None,
    strategy_settings: Mapping[str, Any] | None = None,
) -> H1ResearchAvailabilityRefreshResult:
    """Clear, then optionally re-derive, the current Step 1 availability claim.

    The manual H1 replacement prepare / consume CLIs own the calls. With
    ``h1_mapped_facts`` omitted this is the pre-attempt CLEAR: both P2b engines
    destroy the ``mmi_h1_legacy_step1_mapping_report_v1`` completion within a few
    statements of entry, so the availability claim that completion justified is
    removed BEFORE either engine runs. A failed clear therefore aborts the
    operator command with nothing invalidated, and a successful clear leaves no
    H1 claim regardless of how the engine then fails. With a validated
    ``H1MappedRecognitionFacts`` this is the post-consume SUCCESS REFRESH.

    Deletion happens first and unconditionally; the Legacy rebuild below is NOT
    load-bearing. When derived context is absent, only a narrow raw-output
    warrant may invoke the existing NO_OUTPUT owner. An unreadable or valid raw
    output leaves no decision, and existing missing-permission handling
    downstream owns that fail-closed outcome. No candidate or payload is
    fabricated to fill the gap, and there is no retry, backup, or rollback.

    The facts object is threaded through unchanged: it is never serialized,
    copied into a dict, persisted, reconstructed, or rebuilt from the mapping
    report, and nothing here discovers artifacts, writes a pointer or last-good
    handoff, or changes any state, freshness, or permission policy.

    This is the SOLE owner of the clear + evaluate + write sequence: exactly
    one availability evaluation and one artifact-write set happens per call,
    regardless of which of ``public_projection`` / ``h1_selection`` a caller
    actually uses. ``refresh_research_availability_for_h1_replacement`` is a
    thin wrapper over this function that returns only ``public_projection``,
    so existing callers observe no change at all.
    """
    _invalidate_current_research_availability_artifacts()

    settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )
    candidate = _read_json_if_exists(step1_research_handoff_candidate_path())
    candidate_validation = _read_json_if_exists(
        step1_research_handoff_candidate_validation_path()
    )
    payload = _read_json_if_exists(step1_research_output_path())
    if not isinstance(candidate, Mapping) or not isinstance(payload, Mapping):
        warrant = _resolve_no_output_warrant_for_h1_refresh()
        if warrant.result is _NoOutputWarrantResult.OUTPUT_UNAVAILABLE:
            availability = _write_no_output_research_availability_artifacts_report_only(
                strategy_settings=settings,
                diagnostic_reason=(
                    "H1 availability refresh confirmed Legacy output unavailability."
                ),
                parse_error=warrant.detail,
                h1_mapped_facts=h1_mapped_facts,
                raise_on_failure=True,
            )
            if availability is not None:
                return H1ResearchAvailabilityRefreshResult(
                    public_projection={
                        "research_availability_state": availability.state,
                        "research_availability_decision_present": str(
                            file_exists(step1_research_degraded_mode_decision_path())
                        ),
                        "h1_mapped_selected": str(availability.h1_mapped_selected),
                    },
                    h1_selection=build_h1_mapped_research_selection_projection(availability),
                )

        # The raw output refuted NO_OUTPUT, or the best-effort NO_OUTPUT writer
        # did not return an authoritative result. No H1 state is inferred here.
        return H1ResearchAvailabilityRefreshResult(
            public_projection={
                "research_availability_state": "",
                "research_availability_decision_present": "False",
                "h1_mapped_selected": "False",
            },
            h1_selection=None,
        )

    availability, _ = _evaluate_research_availability_report_only(
        candidate=candidate,
        candidate_validation=candidate_validation,
        strategy_settings=settings,
        payload=payload,
        h1_mapped_facts=h1_mapped_facts,
    )
    return H1ResearchAvailabilityRefreshResult(
        public_projection={
            "research_availability_state": availability.state,
            # Reported from disk, not from the returned result: the report-only
            # evaluator swallows write failures, so only presence proves publication.
            "research_availability_decision_present": str(
                file_exists(step1_research_degraded_mode_decision_path())
            ),
            "h1_mapped_selected": str(availability.h1_mapped_selected),
        },
        h1_selection=build_h1_mapped_research_selection_projection(availability),
    )


def refresh_research_availability_for_h1_replacement(
    *,
    h1_mapped_facts: Any | None = None,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Existing public refresh contract: unchanged legacy ``dict[str, str]``.

    Thin wrapper over :func:`refresh_research_availability_for_h1_replacement_with_selection`
    that discards ``h1_selection`` and returns only ``public_projection`` — see
    that function's docstring for the full behavior contract. Existing callers
    (the H1 replacement prepare / consume CLIs) need no changes.
    """
    return refresh_research_availability_for_h1_replacement_with_selection(
        h1_mapped_facts=h1_mapped_facts,
        strategy_settings=strategy_settings,
    ).public_projection


# S1A-11: the ONLY normalized paths the runtime-parity comparator may report for
# the evidence_packet disk-writer switch to use the Step 1A source. The normalizer
# only ever normalizes generated_at keys, so these are the sole expected entries;
# anything else means it touched a field it should not have -> fail closed to the
# legacy payload.
_APPROVED_EVIDENCE_PACKET_NORMALIZED_PATHS = frozenset(
    {"generated_at", "active_anchor_registry.generated_at"}
)

# S1A-12: the ONLY generated_at paths the embedded-selection parity guard may see
# normalized. Both lineages receive one shared wall-clock stamp, threaded by the
# canonical selection builder into exactly these six structural sites; a
# normalized path outside this set means the payload shape changed -> fail closed.
_APPROVED_EMBEDDED_SELECTION_NORMALIZED_PATHS = frozenset(
    {
        "generated_at",
        "selected_registry.generated_at",
        "baseline_registry.generated_at",
        "approvals_registry.generated_at",
        "dual_read_diff.generated_at",
        "readiness.generated_at",
    }
)


def _compact_str_list(values: Any, limit: int = 5) -> str:
    items = [str(v) for v in values if isinstance(v, str)] if isinstance(values, list) else []
    shown = items[:limit]
    suffix = f",(+{len(items) - limit})" if len(items) > limit else ""
    return ",".join(shown) + suffix


def _compact_diff_paths(differences: Any, limit: int = 5) -> str:
    paths = (
        [
            str(d.get("path"))
            for d in differences
            if isinstance(d, Mapping) and d.get("path") is not None
        ]
        if isinstance(differences, list)
        else []
    )
    shown = paths[:limit]
    suffix = f",(+{len(paths) - limit})" if len(paths) > limit else ""
    return ",".join(shown) + suffix


def _evaluate_step1a_evidence_packet_guard(parity: Mapping[str, Any]) -> dict[str, Any]:
    """Conservative S1A-11 guard over ``compare_evidence_packet_runtime_parity``.

    Returns ``{"ok": bool, "error_summary": str}``. ``ok`` is True only when the
    runtime-relevant subtree is byte-identical (``subtree_match``), no unknown
    runtime timestamp leaked, ONLY the approved generated_at paths were
    normalized, AND there are zero report-only differences — i.e. the Step 1A
    disk payload is byte-stable except for approved generated_at normalization.
    Any failure returns a compact, content-free diagnostic token (paths / field
    names only, never raw anchor content).
    """
    if not isinstance(parity, Mapping):
        return {"ok": False, "error_summary": "step1a_evidence_packet_parity_result_unavailable"}
    unknown_ts = parity.get("unknown_runtime_timestamp_fields") or []
    normalized_paths = parity.get("normalized_paths") or []
    report_only = parity.get("report_only_differences") or []
    differences = parity.get("differences") or []
    unexpected = [
        p
        for p in normalized_paths
        if isinstance(p, str) and p not in _APPROVED_EVIDENCE_PACKET_NORMALIZED_PATHS
    ]

    # Unknown runtime timestamp is checked first: it also forces subtree_match
    # False, but deserves its own specific fail-closed token.
    if unknown_ts:
        return {
            "ok": False,
            "error_summary": "step1a_evidence_packet_unknown_runtime_timestamp: "
            + _compact_str_list(unknown_ts),
        }
    if parity.get("subtree_match") is not True:
        return {
            "ok": False,
            "error_summary": "step1a_evidence_packet_parity_mismatch: diff_paths="
            + _compact_diff_paths(differences)
            + "; normalized_paths="
            + _compact_str_list(normalized_paths),
        }
    if unexpected:
        return {
            "ok": False,
            "error_summary": "step1a_evidence_packet_unexpected_normalized_path: "
            + _compact_str_list(unexpected),
        }
    if report_only:
        return {
            "ok": False,
            "error_summary": "step1a_evidence_packet_report_only_difference: "
            + _compact_diff_paths(report_only),
        }
    return {"ok": True, "error_summary": ""}


def _evaluate_step1a_embedded_selection_guard(parity: Mapping[str, Any]) -> dict[str, Any]:
    """Conservative S1A-12 guard over ``compare_embedded_selection_parity``.

    Returns ``{"ok": bool, "error_summary": str}``. ``ok`` is True only when the
    FULL canonical selection payload is byte-identical (``payload_match``), no
    unknown ISO-datetime leaked anywhere in either payload, and ONLY the six
    approved generated_at paths were normalized. One tier: every difference
    blocks — the selection artifact has no report-only/non-blocking category.
    Any failure returns a compact, content-free diagnostic token (paths / field
    names only, never raw anchor content). Lineage coupling to the S1A-11
    evidence-packet guard is enforced by the caller, not here.
    """
    if not isinstance(parity, Mapping):
        return {"ok": False, "error_summary": "step1a_embedded_selection_parity_result_unavailable"}
    unknown_ts = parity.get("unknown_runtime_timestamp_fields") or []
    normalized_paths = parity.get("normalized_paths") or []
    differences = parity.get("differences") or []
    unexpected = [
        p
        for p in normalized_paths
        if isinstance(p, str) and p not in _APPROVED_EMBEDDED_SELECTION_NORMALIZED_PATHS
    ]

    # Unknown runtime timestamp is checked first: it also forces payload_match
    # False, but deserves its own specific fail-closed token.
    if unknown_ts:
        return {
            "ok": False,
            "error_summary": "step1a_embedded_selection_unknown_runtime_timestamp: "
            + _compact_str_list(unknown_ts),
        }
    if parity.get("payload_match") is not True:
        return {
            "ok": False,
            "error_summary": "step1a_embedded_selection_parity_mismatch: diff_paths="
            + _compact_diff_paths(differences)
            + "; normalized_paths="
            + _compact_str_list(normalized_paths),
        }
    if unexpected:
        return {
            "ok": False,
            "error_summary": "step1a_embedded_selection_unexpected_normalized_path: "
            + _compact_str_list(unexpected),
        }
    return {"ok": True, "error_summary": ""}


def _unwritten_selection_status(error_summary: str) -> dict[str, Any]:
    """Report-only ``unwritten`` embedded-selection status with an upstream token."""
    status: dict[str, Any] = {
        "artifact": "embedded_active_anchor_registry_selection",
        "output_path": "",
        "writer_source": "unwritten",
        "fallback_used": False,
        "error_summary": error_summary,
    }
    try:
        status["output_path"] = str(step1_embedded_active_registry_selection_path())
    except Exception:  # noqa: BLE001 - report-only: best-effort path resolution
        pass
    return status


def _write_evidence_packet_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    evidence_packet_write_log: list[dict[str, Any]] | None = None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one guarded evidence-packet + embedded-selection write pass; log statuses.

    Thin wrapper around a single write pass (S1A-11 packet + S1A-12 embedded
    selection). The evidence-packet writer is invoked MORE THAN ONCE during one
    Step 1 parse: the initial layer-0 write, then the handoff/compiler read-back
    preparation write (via ``_load_or_build_evidence_packet``), and — rarely —
    a memo-layer rebuild when the disk packet is unreadable. Each invocation
    re-runs both guards independently and can overwrite both artifacts, so the
    per-invocation statuses are appended to ``evidence_packet_write_log`` (when
    provided) and the switch-status writer later reports the statuses of the
    LAST invocation that determined the final disk contents (see
    ``_resolve_final_evidence_packet_write_statuses``) — never first-write
    provenance for subsequently overwritten artifacts. Returns
    ``(evidence_packet_status, embedded_selection_status)`` for THIS invocation.
    """
    packet_status, selection_status = _write_evidence_packet_and_selection_once(
        strategy_settings=strategy_settings,
        captured_approval_source=captured_approval_source,
    )
    if evidence_packet_write_log is not None:
        evidence_packet_write_log.append(
            {
                "evidence_packet": packet_status,
                "embedded_active_anchor_registry_selection": selection_status,
            }
        )
    return packet_status, selection_status


def _write_evidence_packet_and_selection_once(
    *,
    strategy_settings: Mapping[str, Any] | None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write the deterministic evidence packet from the Step 1A source (S1A-11).

    Seventh artifact switch, and the FIRST grounding-input switch: support_signals
    grounds off a fresh read-back of this on-disk packet (through the handoff
    compiler), so the writer routes the disk payload to the Step 1A accessor only
    behind a strict run-time parity guard. The legacy/current packet is built in
    memory every run and is both the comparison reference AND the fallback
    payload; the Step 1A candidate reaches disk only when
    ``compare_evidence_packet_runtime_parity`` confirms the runtime-relevant
    subtree is byte-identical (only approved generated_at paths normalized) with
    NO report-only differences either (conservative first-switch policy). On any
    accessor failure, subtree mismatch, unknown runtime timestamp, unexpected
    normalized path, or report-only difference, the legacy/current payload is
    written and the fallback is recorded.

    Report-only: never raises into the Step 1 parse flow, never gates the
    pipeline, never feeds the degraded-mode decision, and never changes any
    permission / gate / order path. Output path, schema, and markers are
    unchanged; a missing snapshot still becomes an explicit DATA_GAP.

    S1A-12 (eighth switch): after a successful packet write, the embedded
    selection artifact is written by the guarded S1A-12 selection writer from
    the two captures this pass ALREADY produced — the legacy capture from the
    legacy packet build and the Step 1A capture from the same accessor call
    that built the Step 1A candidate (no new independent selection compile).
    The Step 1A selection reaches disk only when this pass's packet guard chose
    the Step 1A packet AND ``compare_embedded_selection_parity`` confirms the
    full canonical payloads match (lineage coupling: a packet fallback forces a
    selection fallback so the artifact always describes the selection lineage
    behind the packet written by this pass). Returns
    ``(evidence_packet_status, embedded_selection_status)`` report-only
    provenance for ``step1a_artifact_switch_status.json``.
    """
    status: dict[str, Any] = {
        "artifact": "evidence_packet",
        "output_path": "",
        "writer_source": "unwritten",
        "fallback_used": False,
        "error_summary": "",
    }
    legacy_selection_capture: dict[str, Any] = {}
    step1a_selection_capture: dict[str, Any] = {}
    try:
        output_path = step1_evidence_packet_path()
        status["output_path"] = str(output_path)
        snapshot_path = current_inputs_dir() / "portfolio_snapshot.txt"
        research_anchors_path = current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        approval_source = (
            captured_approval_source
            if type(captured_approval_source)
            is _ValidatedCapturedResearchAnchorApprovalSource
            else _sanitize_captured_source(
                capture_research_anchor_approval_source(approvals_path)
            )
        )
        try:
            snapshot_text: str | None = load_portfolio_snapshot_text()
        except Exception:  # noqa: BLE001 - missing snapshot -> DATA_GAP, not crash
            snapshot_text = None
        last_good = read_last_good_research_handoff(step1_state_dir())
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        source_artifacts = {
            "strategy_settings": str(current_inputs_dir() / "strategy_settings.yaml"),
            "portfolio_snapshot": str(snapshot_path),
            "last_good_metadata": str(last_good_research_handoff_metadata_path(step1_state_dir())),
            "research_anchors": str(research_anchors_path),
            "research_anchor_approvals": str(approvals_path),
        }
        # One wall-clock stamp shared by both lineages so generated_at can never be
        # a spurious source of divergence (the comparator normalizes it anyway).
        generated_at = datetime.now(timezone.utc).isoformat()

        # Legacy/current packet + embedded selection, built in memory (no write
        # yet): the exact bytes today's production writer would produce.
        legacy_packet = _build_evidence_packet_and_selection_from_sanitized_source(
            strategy_settings=strategy_settings,
            portfolio_snapshot_text=snapshot_text,
            portfolio_snapshot_path=snapshot_path,
            last_good_available=last_good.available,
            last_good_metadata=last_good.metadata,
            now_date=settings_as_of,
            generated_at=generated_at,
            source_artifacts=source_artifacts,
            research_anchors_path=research_anchors_path,
            research_anchor_approvals_path=approvals_path,
            embedded_selection_out=legacy_selection_capture,
            approval_source=approval_source,
        )
    except Exception as exc:  # noqa: BLE001 - legacy build failed -> preserve swallowed behavior
        # Lower-bound of "both fail": nothing written, nothing on disk changed.
        # The selection write depends on the legacy capture AND a successful
        # packet write, so it is also skipped (pre-switch swallowed behavior).
        status["writer_source"] = "unwritten"
        status["error_summary"] = f"legacy_evidence_packet_build_failed: {exc}"
        return status, _unwritten_selection_status("legacy_evidence_packet_build_failed_upstream")

    # Decide the disk payload: Step 1A only behind the strict guard, else legacy.
    payload = legacy_packet
    try:
        try:
            research_anchors_text = read_text(research_anchors_path)
        except Exception:  # noqa: BLE001 - match Step 1A optional-source behavior
            research_anchors_text = None
        step1a_candidate = _build_step1a_evidence_packet_from_sanitized_inputs(
            strategy_settings=strategy_settings,
            portfolio_snapshot_text=snapshot_text,
            portfolio_snapshot_path=snapshot_path,
            last_good_available=last_good.available,
            last_good_metadata=last_good.metadata,
            research_anchors_text=research_anchors_text,
            research_anchors_path=str(research_anchors_path),
            research_anchor_approvals_path=str(approvals_path),
            source_artifacts=source_artifacts,
            generated_at=generated_at,
            now_date=settings_as_of,
            # S1A-12: capture the EXACT selection this candidate embedded (the
            # accessor copies it out before assembling the packet) so the guarded
            # selection writer below reuses it — no new independent selection
            # compile, no mid-run input drift, and the out-param provably does
            # not change the candidate bytes or this guard's decision.
            embedded_selection_out=step1a_selection_capture,
            approval_source=approval_source,
        )
        parity = compare_evidence_packet_runtime_parity(legacy_packet, step1a_candidate)
        guard = _evaluate_step1a_evidence_packet_guard(parity)
        if guard["ok"]:
            payload = step1a_candidate
            status["writer_source"] = "step1a"
        else:
            status["writer_source"] = "legacy_fallback"
            status["fallback_used"] = True
            status["error_summary"] = guard["error_summary"]
    except Exception as exc:  # noqa: BLE001 - accessor failed -> legacy fallback
        status["writer_source"] = "legacy_fallback"
        status["fallback_used"] = True
        status["error_summary"] = f"step1a_accessor_failed: {exc}"

    # Single write of the chosen payload; preserve the pre-switch swallowed
    # behavior (artifact absent -> tolerant readers degrade) on a write failure.
    try:
        write_json(output_path, payload)
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        # Ordering preserved from the pre-switch flow: the selection is written
        # only after a successful packet write, so a failed packet write leaves
        # the selection unwritten by THIS pass (a prior pass's artifact, if any,
        # remains on disk untouched — write_json never removes the target).
        status["writer_source"] = "unwritten"
        status["error_summary"] = (
            f"{status['error_summary']}; evidence_packet_write_failed: {exc}"
            if status["error_summary"]
            else f"evidence_packet_write_failed: {exc}"
        )
        return status, _unwritten_selection_status("evidence_packet_write_failed_upstream")

    # Report-only S1A-12 (was S1A-2): the embedded-selection artifact is now
    # written by the guarded selection writer from the two captures produced
    # above — Step 1A capture behind the full-payload parity guard when THIS
    # pass's packet guard chose Step 1A, else the legacy capture (today's exact
    # bytes). Consumed by nothing but the report-only shadow diff; any failure
    # is swallowed and only makes the shadow report that input unavailable.
    try:
        selection_status = _write_embedded_active_registry_selection_report_only(
            legacy_selection=legacy_selection_capture,
            step1a_selection=step1a_selection_capture,
            evidence_packet_status=status,
        )
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        selection_status = _unwritten_selection_status(f"embedded_selection_write_failed: {exc}")
    return status, selection_status


def _write_embedded_active_registry_selection_report_only(
    *,
    legacy_selection: Mapping[str, Any],
    step1a_selection: Mapping[str, Any],
    evidence_packet_status: Mapping[str, Any],
) -> dict[str, Any]:
    """Write the guarded embedded-selection artifact from the Step 1A source (S1A-12).

    Eighth artifact switch. Writes ``embedded_active_registry_selection.json``
    (existing filename/path unchanged; canonical artifact key
    ``embedded_active_anchor_registry_selection``) from the two selection
    captures the SAME write pass already produced — never a new selection
    compile. The Step 1A capture reaches disk only when ALL hold: this pass's
    S1A-11 packet guard chose the Step 1A packet (lineage coupling — the
    artifact must describe the selection lineage behind the packet just
    written, so a packet fallback forces a selection fallback even if the
    selection comparator itself would pass), both captures exist, and
    ``compare_embedded_selection_parity`` reports a full-payload match with no
    unknown timestamp and only approved generated_at paths normalized. On any
    other outcome the legacy capture — today's exact bytes — is written; an
    empty legacy capture writes nothing (no unverified Step 1A payload may
    reach disk). Wrapper provenance is stamped truthfully per branch:
    ``production_source:false / step1a_output:true`` only for a guard-passed
    Step 1A write, else ``production_source:true / step1a_output:false``.

    The artifact stays read ONLY by the report-only Step 1A shadow comparison —
    never by support_signals, readiness, gates, Step 2/3/4, final gate, weekly,
    broker/live, or any order path. Never raises into the Step 1 parse flow;
    returns report-only provenance for ``step1a_artifact_switch_status.json``.
    """
    status: dict[str, Any] = {
        "artifact": "embedded_active_anchor_registry_selection",
        "output_path": "",
        "writer_source": "unwritten",
        "fallback_used": False,
        "error_summary": "",
    }
    try:
        output_path = step1_embedded_active_registry_selection_path()
        status["output_path"] = str(output_path)

        if not legacy_selection:
            # Pre-switch behavior preserved: no legacy capture -> nothing written
            # (and the Step 1A capture cannot be verified without it).
            status["error_summary"] = "legacy_selection_capture_empty"
            return status

        payload: Mapping[str, Any] = legacy_selection
        production_source = True
        packet_writer_source = str(evidence_packet_status.get("writer_source", ""))
        packet_error_summary = str(evidence_packet_status.get("error_summary", ""))
        if packet_writer_source != "step1a":
            # Lineage coupling: the packet on disk came from the legacy build,
            # so the selection artifact must describe the legacy lineage too.
            status["writer_source"] = "legacy_fallback"
            status["fallback_used"] = True
            if "step1a_accessor_failed" in packet_error_summary or not step1a_selection:
                status["error_summary"] = "step1a_accessor_failed: no_step1a_selection_capture"
            else:
                status["error_summary"] = (
                    "step1a_embedded_selection_skipped_evidence_packet_fallback"
                )
        elif not step1a_selection:
            # Defensive: the accessor succeeded but populated no capture.
            status["writer_source"] = "legacy_fallback"
            status["fallback_used"] = True
            status["error_summary"] = "step1a_accessor_failed: step1a_selection_capture_empty"
        else:
            try:
                parity = compare_embedded_selection_parity(legacy_selection, step1a_selection)
                guard = _evaluate_step1a_embedded_selection_guard(parity)
            except Exception as exc:  # noqa: BLE001 - comparator failure -> legacy fallback
                guard = {
                    "ok": False,
                    "error_summary": f"step1a_embedded_selection_guard_failed: {exc}",
                }
            if guard["ok"]:
                payload = step1a_selection
                production_source = False
                status["writer_source"] = "step1a"
            else:
                status["writer_source"] = "legacy_fallback"
                status["fallback_used"] = True
                status["error_summary"] = str(guard["error_summary"])

        artifact = dict(payload)
        artifact.update(
            {
                "consumed_by_gates": False,
                "consumed_by_order_path": False,
                "consumed_by_downstream": False,
                "cannot_affect_allowed_actions": True,
                "cannot_affect_registry_selection": True,
                "not_registry_selection_input": True,
                "not_order_input": True,
                "production_source": production_source,
                "step1a_output": not production_source,
                "safe_to_ignore": True,
            }
        )
        write_json(output_path, artifact)
        return status
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        status["writer_source"] = "unwritten"
        token = f"embedded_selection_write_failed: {exc}"
        status["error_summary"] = (
            f"{status['error_summary']}; {token}" if status["error_summary"] else token
        )
        return status


def _resolve_final_evidence_packet_write_statuses(
    evidence_packet_write_log: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Resolve final-disk-truth statuses across the parse's write invocations.

    The evidence-packet writer runs more than once per parse and each pass can
    overwrite both artifacts, so the switch status must describe the FINAL
    on-disk write, not the first invocation. Per artifact, independently: the
    final status is the status of the LAST invocation that actually wrote the
    file (``writer_source`` in ``step1a`` / ``legacy_fallback``); an
    ``unwritten`` pass wrote nothing, so a preceding successful write's bytes
    remain on disk and its status stays the disk truth (``write_json`` never
    removes the target; a mid-write I/O failure could leave partial content —
    pre-existing writer behavior, and the shadow integrity comparison reports an
    unreadable artifact as an explicit skip, never a false pass). Only when NO
    invocation wrote is the final status ``unwritten``. Also returns per-artifact
    report-only multi-write diagnostics (invocation counts, writer-source
    tokens, and whether first and final statuses differ) — status/provenance/
    error-token information only, never anchor content, consumed by nothing.
    """

    def _entries(artifact: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for invocation in evidence_packet_write_log:
            entry = invocation.get(artifact) if isinstance(invocation, Mapping) else None
            if isinstance(entry, Mapping):
                out.append(dict(entry))
        return out

    def _final(entries: list[dict[str, Any]], artifact: str) -> dict[str, Any]:
        written = [e for e in entries if e.get("writer_source") in ("step1a", "legacy_fallback")]
        if written:
            return written[-1]
        if entries:
            return entries[-1]
        return {
            "artifact": artifact,
            "output_path": "",
            "writer_source": "unwritten",
            "fallback_used": False,
            "error_summary": "no_write_invocation_recorded",
        }

    def _status_key(entry: Mapping[str, Any]) -> tuple[str, bool, str]:
        return (
            str(entry.get("writer_source", "")),
            entry.get("fallback_used") is True,
            str(entry.get("error_summary", "")),
        )

    def _diagnostics(entries: list[dict[str, Any]], artifact: str) -> dict[str, Any]:
        final = _final(entries, artifact)
        first = entries[0] if entries else final
        written_indexes = [
            index
            for index, entry in enumerate(entries)
            if entry.get("writer_source") in ("step1a", "legacy_fallback")
        ]
        return {
            "invocation_count": len(entries),
            "writer_sources": [str(e.get("writer_source", "")) for e in entries],
            "first_writer_source": str(first.get("writer_source", "")),
            "first_fallback_used": first.get("fallback_used") is True,
            "first_error_summary": str(first.get("error_summary", "")),
            "final_writer_source": str(final.get("writer_source", "")),
            # 1-based invocation whose bytes are on disk; 0 = no pass wrote.
            "final_disk_write_invocation": (written_indexes[-1] + 1) if written_indexes else 0,
            "first_and_final_statuses_differ": bool(entries)
            and _status_key(first) != _status_key(final),
        }

    packet_entries = _entries("evidence_packet")
    selection_entries = _entries("embedded_active_anchor_registry_selection")
    diagnostics = {
        "evidence_packet": _diagnostics(packet_entries, "evidence_packet"),
        "embedded_active_anchor_registry_selection": _diagnostics(
            selection_entries, "embedded_active_anchor_registry_selection"
        ),
    }
    return (
        _final(packet_entries, "evidence_packet"),
        _final(selection_entries, "embedded_active_anchor_registry_selection"),
        diagnostics,
    )


def _write_active_research_anchor_registry_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Write the R2G-1 active anchor registry from the Step 1A source (S1A-3).

    First artifact switch of the Step 1 split: the payload now comes from the
    narrow Step 1A accessor, which is byte-identical to the legacy compile by
    construction (same deterministic compiler, same universe/as-of derivation,
    ``generated_at=None``). The legacy writer is retained as the runtime
    fallback; a double failure preserves the pre-switch swallowed behavior
    (absent artifact, tolerant readers degrade). Output path, layer position,
    schema, markers, and payload bytes are unchanged. The artifact stays an
    observer: NOTHING authoritative consumes it (not support_signals, not the
    compiler, not availability, not gates, not Step 2/3/4, not weekly), and it
    never raises into the Step 1 parse flow. Returns report-only provenance for
    ``step1a_artifact_switch_status.json``.
    """
    status: dict[str, Any] = {
        "artifact": "active_research_anchor_registry",
        "output_path": "",
        "writer_source": "unwritten",
        "fallback_used": False,
        "error_summary": "",
    }
    try:
        output_path = step1_active_research_anchor_registry_path()
        status["output_path"] = str(output_path)
        registry = build_step1a_active_research_anchor_registry(
            strategy_settings=strategy_settings,
            research_anchors_path=current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME,
        )
        write_json(output_path, registry)
        status["writer_source"] = "step1a"
        return status
    except Exception as exc:  # noqa: BLE001 - fall back to the retained legacy writer
        status["fallback_used"] = True
        status["error_summary"] = f"step1a_accessor_failed: {exc}"
    try:
        research_anchors_path = current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        allowed_universe = _allowed_buy_universe_for_anchor_registry(strategy_settings)
        write_active_research_anchor_registry(
            output_path=step1_active_research_anchor_registry_path(),
            anchors_path=research_anchors_path,
            allowed_universe=allowed_universe,
            today=settings_as_of,
        )
        status["writer_source"] = "legacy_fallback"
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        status["writer_source"] = "unwritten"
        status["error_summary"] = f"{status['error_summary']}; legacy_writer_failed: {exc}"
    return status


def _write_step1a_artifact_switch_status_report_only(
    switched: list[Mapping[str, Any]],
    *,
    evidence_packet_write_invocations: Mapping[str, Any] | None = None,
) -> None:
    """Write ``step1a_artifact_switch_status.json`` (S1A-3, report-only).

    Per-artifact provenance for the Step 1A writer switches: which source wrote
    each switched artifact this run (``step1a`` | ``legacy_fallback`` |
    ``unwritten``). The evidence_packet / embedded-selection entries are the
    FINAL-write statuses (their writer runs more than once per parse);
    ``evidence_packet_write_invocations`` carries the per-invocation report-only
    diagnostics (status/provenance/error tokens only, never anchor content).
    Consumed by NOTHING: not readiness, not evidence_packet, not
    support_signals, not gates, not Step 2/3/4, not final gate, not weekly, not
    broker/live, not any order path. Any failure is swallowed.
    """
    try:
        artifact = {
            "schema_version": "step1a_artifact_switch_status_v1",
            "is_llm_generated": False,
            "generated_at": None,
            "report_only": True,
            "permission_effect": "none",
            "not_authorization": True,
            "not_execution_authorization": True,
            "consumed_by_gates": False,
            "consumed_by_order_path": False,
            "consumed_by_downstream": False,
            "cannot_affect_allowed_actions": True,
            "cannot_affect_registry_selection": True,
            "not_registry_selection_input": True,
            "not_order_input": True,
            "not_permission_input": True,
            "not_budget_input": True,
            "not_allocation_input": True,
            "no_execution_authority": True,
            "safe_to_ignore": True,
            # S1A-5.1/S1A-11/S1A-12: boundary-scope markers. The writer-source
            # switches change WHO compiles the payloads, not artifact paths.
            # S1A-11 flipped evidence_packet_uses_step1a_output to True; S1A-12
            # flips embedded_selection_uses_step1a_output to True (the selection
            # DISK artifact is now Step 1A-sourced behind a full-payload parity
            # guard lineage-coupled to the packet guard). The runtime-authority
            # markers stay False because the guards prove byte-identical
            # payloads (legacy bytes on any divergence), the selection artifact
            # is consumed by nothing at runtime, and no gate/order/readiness
            # path consumes Step 1A output.
            "production_artifact_paths_switched": False,
            "evidence_packet_uses_step1a_output": True,
            "embedded_selection_uses_step1a_output": True,
            "support_signals_uses_step1a_output": False,
            "readiness_uses_step1a_output": False,
            "order_path_uses_step1a_output": False,
            "runtime_authority_uses_step1a_output": False,
            "shadow_comparison_note": (
                "For a switched artifact the Step 1A shadow diff compares the "
                "Step 1A bundle against the on-disk Step 1A write (an integrity/"
                "staleness check), no longer legacy-vs-Step1A parity."
            ),
            "switched_artifacts": {
                str(entry.get("artifact", "")): {
                    "writer_source": str(entry.get("writer_source", "unwritten")),
                    "output_path": str(entry.get("output_path", "")),
                    "fallback_used": entry.get("fallback_used") is True,
                    "error_summary": str(entry.get("error_summary", "")),
                }
                for entry in switched
                if isinstance(entry, Mapping)
            },
            # S1A-12 final-write truthfulness: the packet/selection writer runs
            # more than once per parse; these report-only diagnostics record
            # every invocation's provenance and flag when first and final
            # statuses differ. Authority-free and consumed by nothing.
            "evidence_packet_write_invocations": dict(evidence_packet_write_invocations or {}),
        }
        write_json(step1a_artifact_switch_status_path(), artifact)
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        pass


def _write_anchor_source_equivalence_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> None:
    """Compile + write the R2G-2 anchor-source equivalence oracle (report-only).

    Reads the just-written evidence packet (authoritative
    ``research_anchors`` view) and the R2G-1 active registry, then writes a
    diagnostic diff. Additive only: it switches no consumer, changes no behavior,
    never touches ``evidence_packet.research_anchors`` or ``support_signals``,
    never gates the pipeline, and adds no permission / state / action. Any error
    is swallowed so Step 1 parse is never affected.
    """
    try:
        evidence_packet = _read_json_if_exists(step1_evidence_packet_path())
        active_registry = _read_json_if_exists(step1_active_research_anchor_registry_path())
        write_anchor_source_equivalence(
            output_path=step1_anchor_source_equivalence_path(),
            evidence_packet=evidence_packet if isinstance(evidence_packet, Mapping) else None,
            active_registry=active_registry if isinstance(active_registry, Mapping) else None,
        )
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        pass


def _write_research_anchor_candidates_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> None:
    """Compile + write the R2G-4 advisory research-anchor candidates (report-only).

    Reads the just-written evidence packet, analyst memo, support-signal gaps, and
    active registry, then writes ``research_anchor_candidates.json`` — advisory
    suggestions for human review. Strictly inert: consumed by NOTHING (not
    support_signals, the active registry, the actionable preview/candidate/
    eligibility, availability, gates, Step 2/3/4, weekly, broker/live), never made
    active, never added to promoted_source_artifacts / allowed_actions / any gate,
    and it adds no permission / state / action. Any error is swallowed so Step 1
    parse is never affected.
    """
    try:
        evidence_packet = _read_json_if_exists(step1_evidence_packet_path())
        analyst_memo = _read_json_if_exists(step1_analyst_memo_path())
        support_signals = _read_json_if_exists(step1_compiled_support_signals_path())
        active_registry = _read_json_if_exists(step1_active_research_anchor_registry_path())
        memo_valid = (
            isinstance(support_signals, Mapping)
            and support_signals.get("analyst_memo_valid") is True
        )
        as_of_date = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        write_research_anchor_candidates(
            output_path=step1_research_anchor_candidates_path(),
            evidence_packet=evidence_packet if isinstance(evidence_packet, Mapping) else None,
            analyst_memo=analyst_memo if isinstance(analyst_memo, Mapping) else None,
            analyst_memo_valid=memo_valid,
            compiled_support_signals=support_signals if isinstance(support_signals, Mapping) else None,
            active_registry=active_registry if isinstance(active_registry, Mapping) else None,
            as_of_date=as_of_date if isinstance(as_of_date, str) else None,
        )
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        pass


def _write_research_anchor_approvals_validation_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> dict[str, Any]:
    """Write the R2G-5a approvals-validation report from the Step 1A source (S1A-4).

    Second artifact switch of the Step 1 split: the payload now comes from the
    narrow Step 1A accessor for the standalone REPORT variant (byte-identical to
    the legacy compile for string-or-absent ``as_of``; the overlay variant that
    feeds the with-approvals registry is separate and unchanged). The legacy
    writer is retained as the runtime fallback; a double failure preserves the
    pre-switch swallowed behavior. Output path, layer position, schema, markers,
    and payload bytes are unchanged. The artifact stays strictly inert: it
    activates NO anchor, is consumed by NOTHING authoritative (not
    support_signals, not the active registry, not the compiler, not availability,
    not gates, not Step 2/3/4, not weekly, not broker/live), ``would_activate``
    is never trusted, and it never raises into the Step 1 parse flow. A missing
    manifest yields a valid, empty report. Returns report-only provenance for
    ``step1a_artifact_switch_status.json``.
    """
    status: dict[str, Any] = {
        "artifact": "research_anchor_approvals_validation",
        "output_path": "",
        "writer_source": "unwritten",
        "fallback_used": False,
        "error_summary": "",
    }
    try:
        output_path = step1_research_anchor_approvals_validation_path()
        status["output_path"] = str(output_path)
        payload = _build_step1a_research_anchor_approvals_validation_from_sanitized_source(
            strategy_settings=strategy_settings,
            generated_at=None,
            now_date=None,
            approval_source=captured_approval_source,
        )
        write_json(output_path, payload)
        status["writer_source"] = "step1a"
        return status
    except Exception as exc:  # noqa: BLE001 - fall back to the retained legacy writer
        status["fallback_used"] = True
        status["error_summary"] = f"step1a_accessor_failed: {exc}"
    try:
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        allowed_universe = _allowed_buy_universe_for_anchor_registry(strategy_settings)
        source = (
            captured_approval_source
            if type(captured_approval_source)
            is _ValidatedCapturedResearchAnchorApprovalSource
            else _sanitize_captured_source(
                capture_research_anchor_approval_source(approvals_path)
            )
        )
        approvals_validation, _ = _build_research_anchor_approval_source_validations_from_sanitized(
            approval_source=source,
            allowed_universe=allowed_universe,
            today=settings_as_of,
            as_of_date=settings_as_of if isinstance(settings_as_of, str) else None,
            generated_at=None,
            candidate_index=None,
        )
        write_json(step1_research_anchor_approvals_validation_path(), approvals_validation)
        status["writer_source"] = "legacy_fallback"
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        status["writer_source"] = "unwritten"
        status["error_summary"] = f"{status['error_summary']}; legacy_writer_failed: {exc}"
    return status


def _write_research_anchor_revocations_validation_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> dict[str, Any]:
    """Write the R2G-5d-0 revocations-validation report from the Step 1A source (S1A-5).

    Third artifact switch of the Step 1 split: the payload now comes from the
    narrow Step 1A accessor for the settings-anchored standalone REPORT variant
    (byte-identical to the legacy compile for string-or-absent ``as_of``; the
    baseline-coupled overlay variant that feeds the with-approvals registry is
    separate and unchanged). The legacy writer is retained as the runtime
    fallback; a double failure preserves the pre-switch swallowed behavior.
    Output path, layer position, schema, markers, and payload bytes are
    unchanged. The artifact stays strictly inert: it APPLIES no revocation, does
    not change ``support_signals``, the embedded ``evidence_packet`` registry
    selection, or readiness; is consumed by NOTHING as an artifact; unknown
    target still fails closed and ``reason`` stays non-authoritative. A missing
    manifest yields a valid, empty report; it never raises into the Step 1 parse
    flow. Returns report-only provenance for
    ``step1a_artifact_switch_status.json``.
    """
    status: dict[str, Any] = {
        "artifact": "research_anchor_revocations_validation",
        "output_path": "",
        "writer_source": "unwritten",
        "fallback_used": False,
        "error_summary": "",
    }
    try:
        output_path = step1_research_anchor_revocations_validation_path()
        status["output_path"] = str(output_path)
        payload = _build_step1a_research_anchor_revocations_validation_from_sanitized_source(
            strategy_settings=strategy_settings,
            generated_at=None,
            now_date=None,
            approval_source=captured_approval_source,
        )
        write_json(output_path, payload)
        status["writer_source"] = "step1a"
        return status
    except Exception as exc:  # noqa: BLE001 - fall back to the retained legacy writer
        status["fallback_used"] = True
        status["error_summary"] = f"step1a_accessor_failed: {exc}"
    try:
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        allowed_universe = _allowed_buy_universe_for_anchor_registry(strategy_settings)
        source = (
            captured_approval_source
            if type(captured_approval_source)
            is _ValidatedCapturedResearchAnchorApprovalSource
            else _sanitize_captured_source(
                capture_research_anchor_approval_source(approvals_path)
            )
        )
        _, revocations_validation = _build_research_anchor_approval_source_validations_from_sanitized(
            approval_source=source,
            allowed_universe=allowed_universe,
            today=settings_as_of,
            as_of_date=settings_as_of if isinstance(settings_as_of, str) else None,
            generated_at=None,
            candidate_index=None,
        )
        write_json(step1_research_anchor_revocations_validation_path(), revocations_validation)
        status["writer_source"] = "legacy_fallback"
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        status["writer_source"] = "unwritten"
        status["error_summary"] = f"{status['error_summary']}; legacy_writer_failed: {exc}"
    return status


def _write_approval_registry_dual_read_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write the R2G-5b approvals-inclusive registry (S1A-6) + dual-read diff (S1A-8, report-only).

    Two OVERLAY-lineage artifact switches live in this paired writer, each
    behind its own run-time parity guard against the legacy overlay derivation,
    which is retained VERBATIM below and computed every run:

    * ``active_research_anchor_registry_with_approvals.json`` (S1A-6): the
      Step 1A with-approvals candidate is written only when it equals the legacy
      overlay compile; otherwise the legacy bytes are written and
      ``legacy_fallback`` recorded.
    * ``approval_registry_dual_read_diff.json`` (S1A-8): the Step 1A diff
      (composed from the S1A-3 baseline + S1A-6 with-approvals accessors) is
      written only when it equals the legacy diff built from the legacy
      in-memory baseline/with_approvals objects; otherwise the legacy diff is
      written and ``legacy_fallback`` recorded. This upgrades the overlay
      cross-lineage parity signal from a shadow-only diagnostic to an always-on
      guard surfaced in switch status.

    The two guards are independent: a dual-read-diff fallback or failure never
    changes the with-approvals status, and vice versa. Neither Step 1A payload
    reaches disk unless it byte-matches its legacy counterpart, including the
    non-string ``as_of`` normalization edge. Output paths, layer position,
    schema, markers, and payload bytes are unchanged. Semantics of the
    derivation are unchanged: approvals recomputed directly from
    ``research_anchor_approvals.yaml`` (never reading the R2G-5a artifact or its
    would_activate flag as authority), revocations re-validated from the same
    YAML bytes, valid active revocations applied only to this SEPARATE standalone
    registry. Strictly inert: neither artifact is embedded in the evidence
    packet, added to support_signals input / promoted_source_artifacts / active
    handoff / allowed_actions / any gate / Step 2/3/4 input; both are consumed by
    NOTHING directly and add no permission / state / action. A legacy-derivation
    or write failure preserves the pre-switch swallowed behavior (artifacts
    absent, ``"unwritten"``); Step 1 parse is never affected. Returns report-only
    provenance for both artifacts for ``step1a_artifact_switch_status.json``.
    """
    with_approvals_status: dict[str, Any] = {
        "artifact": "active_research_anchor_registry_with_approvals",
        "output_path": "",
        "writer_source": "unwritten",
        "fallback_used": False,
        "error_summary": "",
    }
    dual_read_diff_status: dict[str, Any] = {
        "artifact": "approval_registry_dual_read_diff",
        "output_path": "",
        "writer_source": "unwritten",
        "fallback_used": False,
        "error_summary": "",
    }
    with_approvals_written = False
    diff_written = False
    try:
        anchors_path = current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        approval_source = (
            captured_approval_source
            if type(captured_approval_source)
            is _ValidatedCapturedResearchAnchorApprovalSource
            else _sanitize_captured_source(
                capture_research_anchor_approval_source(approvals_path)
            )
        )
        with_approvals_path = step1_active_research_anchor_registry_with_approvals_path()
        diff_path = step1_approval_registry_dual_read_diff_path()
        with_approvals_status["output_path"] = str(with_approvals_path)
        dual_read_diff_status["output_path"] = str(diff_path)
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        allowed_universe = _allowed_buy_universe_for_anchor_registry(strategy_settings)

        # Legacy overlay derivation, retained verbatim: it feeds both parity
        # guards below every run and doubles as the switched writes' fallback.
        # Baseline: the exact same compile support_signals' embedded registry uses.
        baseline = compile_active_research_anchor_registry(
            anchors_path=anchors_path,
            allowed_universe=allowed_universe,
            today=settings_as_of,
        )
        # Approvals: exact source bytes and the independent settings boundary are
        # revalidated inside the activation call. The persisted R2G-5a mapping is
        # never an activation input.
        with_approvals = _build_from_sanitized_source(
            baseline=baseline,
            approval_source=approval_source,
            allowed_universe=allowed_universe,
            today=settings_as_of,
            generated_at=None,
            candidate_index=None,
        )
        # Legacy dual-read diff: always built from the legacy in-memory objects,
        # never from a Step 1A candidate. It is the S1A-8 parity-guard reference
        # and the switched write's fallback payload.
        legacy_diff = _build_approval_registry_dual_read_diff_from_sanitized_source(
            baseline_registry=baseline,
            approvals_registry=with_approvals,
            approval_source=approval_source,
            baseline_registry_path=str(step1_active_research_anchor_registry_path()),
            approvals_registry_path=str(with_approvals_path),
        )

        # --- S1A-6 with-approvals switched payload selection (parity guard) -----
        # The legacy object is computed every run anyway, so an unverified Step 1A
        # byte can never reach disk (a divergence would otherwise be invisible —
        # the shadow comparison for a switched artifact is step1a-vs-step1a).
        payload = with_approvals
        try:
            candidate = _build_step1a_active_research_anchor_registry_with_approvals_from_sanitized_source(
                strategy_settings=strategy_settings,
                research_anchors_path=anchors_path,
                generated_at=None,
                now_date=None,
                approval_source=approval_source,
            )
            if candidate == with_approvals:
                payload = candidate
                with_approvals_status["writer_source"] = "step1a"
            else:
                with_approvals_status["writer_source"] = "legacy_fallback"
                with_approvals_status["fallback_used"] = True
                with_approvals_status["error_summary"] = (
                    "step1a_overlay_parity_mismatch: Step 1A candidate differs from "
                    "the legacy overlay compile; legacy payload written to keep the "
                    "artifact consistent with the dual-read diff lineage"
                )
        except Exception as exc:  # noqa: BLE001 - fall back to the retained legacy payload
            with_approvals_status["writer_source"] = "legacy_fallback"
            with_approvals_status["fallback_used"] = True
            with_approvals_status["error_summary"] = f"step1a_accessor_failed: {exc}"
        write_json(with_approvals_path, payload)
        with_approvals_written = True

        # --- S1A-8 dual-read diff switched payload selection (parity guard) -----
        # Independent of the with-approvals guard above. The legacy diff is
        # computed every run, so an unverified Step 1A diff byte can never reach
        # disk. By construction the Step 1A diff equals the legacy diff whenever
        # the S1A-3 baseline and S1A-6 with-approvals accessors match legacy
        # (they are byte-proven), so this guard essentially always passes.
        diff_payload = legacy_diff
        try:
            diff_candidate = _build_step1a_approval_registry_dual_read_diff_from_sanitized_source(
                strategy_settings=strategy_settings,
                research_anchors_path=anchors_path,
                baseline_registry_artifact_path=step1_active_research_anchor_registry_path(),
                approvals_registry_artifact_path=with_approvals_path,
                generated_at=None,
                now_date=None,
                approval_source=approval_source,
            )
            if diff_candidate == legacy_diff:
                diff_payload = diff_candidate
                dual_read_diff_status["writer_source"] = "step1a"
            else:
                dual_read_diff_status["writer_source"] = "legacy_fallback"
                dual_read_diff_status["fallback_used"] = True
                dual_read_diff_status["error_summary"] = (
                    "step1a_dual_read_diff_parity_mismatch: Step 1A dual-read diff "
                    "differs from the legacy-lineage diff; legacy diff written"
                )
        except Exception as exc:  # noqa: BLE001 - fall back to the retained legacy diff
            dual_read_diff_status["writer_source"] = "legacy_fallback"
            dual_read_diff_status["fallback_used"] = True
            dual_read_diff_status["error_summary"] = f"step1a_accessor_failed: {exc}"
        write_json(diff_path, diff_payload)
        diff_written = True
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        failure = f"legacy_derivation_or_write_failed: {exc}"
        if not with_approvals_written:
            with_approvals_status["writer_source"] = "unwritten"
            with_approvals_status["error_summary"] = (
                f"{with_approvals_status['error_summary']}; {failure}"
                if with_approvals_status["error_summary"]
                else failure
            )
        if not diff_written:
            dual_read_diff_status["writer_source"] = "unwritten"
            dual_read_diff_status["error_summary"] = (
                f"{dual_read_diff_status['error_summary']}; {failure}"
                if dual_read_diff_status["error_summary"]
                else failure
            )
    return with_approvals_status, dual_read_diff_status


def _write_approval_registry_switch_readiness_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> dict[str, Any]:
    """Write the R2G-5c-0 switch-readiness DISK OBSERVER from the Step 1A source (S1A-7).

    Fifth artifact switch of the Step 1 split. The payload now comes from the
    narrow Step 1A accessor, which wraps the SAME shared deterministic
    ``build_approval_registry_switch_readiness`` the retained legacy write
    wrapper delegates to — byte-identical by construction for string-or-absent
    ``as_of``. No run-time parity guard is needed here (unlike S1A-6): both
    sides share one builder, there is no paired sibling artifact whose
    consistency a divergence could break, and legacy is not otherwise computed
    each run. The legacy writer is retained as the runtime fallback; a double
    failure preserves the pre-switch swallowed behavior (absent artifact,
    tolerant readers degrade). Output path, layer position, schema, markers,
    and payload bytes are unchanged. The artifact stays a DISK OBSERVER only:
    runtime readiness is recomputed in memory by the evidence packet's embedded
    selector (and the support-signals dual-ground dry run) and never reads this
    JSON as authority — ``ready``/``switch_target`` are report fields, not
    permissions, so the runtime-scoped ``readiness_uses_step1a_output:false``
    diagnostics marker remains accurate after this switch. The readiness
    evaluation itself recomputes everything from the current YAML bytes (never
    reading the R2G-5a validation artifact or trusting its would_activate
    flag). Strictly inert: it switches NO consumer, does not change
    ``evidence_packet.active_anchor_registry`` or the baseline registry
    support_signals consumes, is consumed by NOTHING authoritative, is never
    added to promoted_source_artifacts / allowed_actions / any gate /
    Step 2/3/4 input, adds no permission / state / action, and never raises
    into the Step 1 parse flow. Returns report-only provenance for
    ``step1a_artifact_switch_status.json``.
    """
    status: dict[str, Any] = {
        "artifact": "approval_registry_switch_readiness",
        "output_path": "",
        "writer_source": "unwritten",
        "fallback_used": False,
        "error_summary": "",
    }
    try:
        anchors_path = current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        output_path = step1_approval_registry_switch_readiness_path()
        status["output_path"] = str(output_path)
        payload = _build_step1a_approval_registry_switch_readiness_from_sanitized_source(
            strategy_settings=strategy_settings,
            research_anchors_path=anchors_path,
            generated_at=None,
            now_date=None,
            approval_source=captured_approval_source,
        )
        write_json(output_path, payload)
        status["writer_source"] = "step1a"
        return status
    except Exception as exc:  # noqa: BLE001 - fall back to the retained legacy writer
        status["fallback_used"] = True
        status["error_summary"] = f"step1a_accessor_failed: {exc}"
    try:
        anchors_path = current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        allowed_universe = _allowed_buy_universe_for_anchor_registry(strategy_settings)
        source = (
            captured_approval_source
            if type(captured_approval_source)
            is _ValidatedCapturedResearchAnchorApprovalSource
            else _sanitize_captured_source(
                capture_research_anchor_approval_source(approvals_path)
            )
        )
        payload = _build_approval_registry_switch_readiness_from_sanitized_source(
            anchors_path=anchors_path,
            approval_source=source,
            allowed_universe=allowed_universe,
            today=settings_as_of,
        )
        write_json(step1_approval_registry_switch_readiness_path(), payload)
        status["writer_source"] = "legacy_fallback"
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        status["writer_source"] = "unwritten"
        status["error_summary"] = f"{status['error_summary']}; legacy_writer_failed: {exc}"
    return status


def _write_support_signals_dual_ground_diff_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> None:
    """Compile + write the R2G-5c-1 support_signals dual-ground DRY-RUN diff (report-only).

    Reads the already-written evidence packet, analyst memo, and compiled support
    signals (for ``compilation_mode``), recompiles the approvals-inclusive registry
    + dual-read diff fresh from YAML, and compares support_signals grounding under
    the baseline embedded registry vs the approvals-inclusive registry (subject to
    readiness). Strictly a DRY-RUN: it never changes support_signals runtime output,
    never mutates ``evidence_packet.active_anchor_registry``, never switches the
    embedded registry, is consumed by NOTHING, and adds no permission / state /
    action. Only runs when compiled_support_signals.json exists. Any error is
    swallowed so Step 1 parse is never affected.
    """
    try:
        support_signals = _read_json_if_exists(step1_compiled_support_signals_path())
        if not isinstance(support_signals, Mapping):
            return
        evidence_packet = _read_json_if_exists(step1_evidence_packet_path())
        analyst_memo = _read_json_if_exists(step1_analyst_memo_path())
        compilation_mode = support_signals.get("compilation_mode")
        if not isinstance(compilation_mode, str):
            return
        anchors_path = current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        allowed_universe = _allowed_buy_universe_for_anchor_registry(strategy_settings)
        _write_support_signals_dual_ground_diff_from_sanitized_source(
            output_path=step1_support_signals_dual_ground_diff_path(),
            evidence_packet=evidence_packet if isinstance(evidence_packet, Mapping) else None,
            analyst_memo=analyst_memo if isinstance(analyst_memo, Mapping) else None,
            compilation_mode=compilation_mode,
            anchors_path=anchors_path,
            approvals_path=approvals_path,
            allowed_universe=allowed_universe,
            today=settings_as_of,
            approval_source=captured_approval_source,
        )
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        pass


def _enforce_complete_step1_workflow_approval_source_identity(
    *,
    approval_source: _ValidatedCapturedResearchAnchorApprovalSource,
    strategy_settings: Mapping[str, Any] | None,
    source_summary_sha256: str | None,
) -> tuple[bool, dict[str, Any] | None]:
    """Fail closed before Step 1 adopts approval-derived grounding.

    Complete Step 1 derives several report artifacts before all identity inputs
    exist.  This boundary joins those candidates to the one workflow-owned
    source snapshot.  A mismatch preserves the disagreeing reports as evidence,
    but replaces every activation-bearing view and recompiles support from the
    safe evidence packet before any actionable or promotion preview runs.
    """

    def mapping_at(path: Path) -> dict[str, Any]:
        value = _read_json_if_exists(path)
        return dict(value) if isinstance(value, Mapping) else {}

    active_registry = mapping_at(step1_active_research_anchor_registry_path())
    approvals_validation = mapping_at(
        step1_research_anchor_approvals_validation_path()
    )
    revocations_validation = mapping_at(
        step1_research_anchor_revocations_validation_path()
    )
    approvals_registry_path = (
        step1_active_research_anchor_registry_with_approvals_path()
    )
    approvals_registry_value = _read_json_if_exists(approvals_registry_path)
    approvals_registry_present = isinstance(approvals_registry_value, Mapping)
    approvals_registry = (
        dict(approvals_registry_value) if approvals_registry_present else {}
    )
    dual_read_diff_path = step1_approval_registry_dual_read_diff_path()
    dual_read_diff_value = _read_json_if_exists(dual_read_diff_path)
    dual_read_diff_present = isinstance(dual_read_diff_value, Mapping)
    dual_read_diff = (
        dict(dual_read_diff_value) if dual_read_diff_present else {}
    )
    readiness_path = step1_approval_registry_switch_readiness_path()
    readiness_value = _read_json_if_exists(readiness_path)
    readiness_present = isinstance(readiness_value, Mapping)
    readiness = dict(readiness_value) if readiness_present else {}
    embedded_selection_path = step1_embedded_active_registry_selection_path()
    embedded_selection_value = _read_json_if_exists(embedded_selection_path)
    embedded_selection_present = isinstance(embedded_selection_value, Mapping)
    embedded_selection = (
        dict(embedded_selection_value) if embedded_selection_present else {}
    )
    evidence_packet_path = step1_evidence_packet_path()
    evidence_packet_value = _read_json_if_exists(evidence_packet_path)
    evidence_packet_present = isinstance(evidence_packet_value, Mapping)
    evidence_packet = (
        dict(evidence_packet_value) if evidence_packet_present else {}
    )
    dual_ground_value = _read_json_if_exists(
        step1_support_signals_dual_ground_diff_path()
    )
    dual_ground_diff = (
        dict(dual_ground_value)
        if isinstance(dual_ground_value, Mapping)
        else None
    )
    compiled_support_signals_value = _read_json_if_exists(
        step1_compiled_support_signals_path()
    )
    compiled_support_signals = (
        dict(compiled_support_signals_value)
        if isinstance(compiled_support_signals_value, Mapping)
        else None
    )

    (
        safe_overlay,
        safe_diff,
        safe_readiness,
        safe_selection,
        safe_packet,
        mismatch,
    ) = _enforce_workflow_approval_source_identity(
        approval_source=approval_source,
        active_registry=active_registry,
        approvals_validation=approvals_validation,
        revocations_validation=revocations_validation,
        approvals_registry=approvals_registry,
        dual_read_diff=dual_read_diff,
        readiness=readiness,
        embedded_selection=(
            embedded_selection if embedded_selection_present else None
        ),
        evidence_packet=evidence_packet,
        dual_ground_diff=dual_ground_diff,
        compiled_support_signals=compiled_support_signals,
        source_summary_sha256=source_summary_sha256,
        generated_at=None,
    )
    if not mismatch:
        return False, None

    # These writes are authoritative for the remainder of this synchronous Step
    # 1 evaluation.  Failure is not swallowed: continuing with an earlier unsafe
    # candidate would violate the fail-closed adoption boundary.
    # Preserve the committed observer-artifact absence contract when an earlier
    # best-effort writer produced no artifact at all.  Absence still participates
    # in the join and forces the authoritative evidence/readiness path closed.
    if approvals_registry_present:
        write_json(approvals_registry_path, safe_overlay)
    if dual_read_diff_present:
        write_json(dual_read_diff_path, safe_diff)
    if readiness_present:
        write_json(readiness_path, safe_readiness)
    if embedded_selection_present:
        write_json(embedded_selection_path, safe_selection)
    if evidence_packet_present:
        write_json(evidence_packet_path, safe_packet)

    analyst_memo = _load_analyst_memo_for_compiler()
    compiled = write_compiled_research_handoff(
        candidate_path=step1_compiled_handoff_candidate_path(),
        validation_path=step1_compiled_handoff_validation_path(),
        metadata_path=step1_compiled_handoff_metadata_path(),
        evidence_packet=safe_packet,
        analyst_memo=analyst_memo,
        strategy_settings=strategy_settings,
        evidence_packet_path=str(step1_evidence_packet_path()),
        analyst_memo_path=(
            str(step1_analyst_memo_path()) if analyst_memo is not None else None
        ),
        support_signals_path=step1_compiled_support_signals_path(),
    )
    return True, {
        "candidate_path": compiled["compiled_research_handoff_candidate_path"],
        "validation_path": compiled["compiled_research_handoff_validation_path"],
        "metadata_path": compiled["compiled_research_handoff_metadata_path"],
        "support_signals_path": compiled.get("compiled_support_signals_path", ""),
        "compilation_mode": compiled["compilation_mode"],
        "compiled_candidate_valid": compiled["compiled_candidate_valid"],
    }


def _write_grounding_status_observatory_report_only(
    *, workflow_identity_mismatch: bool = False
) -> None:
    """Build + write the R2G-6b grounding-status observatory (report-only).

    Uses only already-written Step 1 mappings as diagnostics inputs. It does not
    recompute readiness or registry selection, and the written artifact is
    consumed by NOTHING (not evidence_packet, readiness, support_signals,
    availability, gates, Step 2/3/4, weekly, broker/live, allowed_actions, or any
    order path). Any error is swallowed so Step 1 parse is never affected.
    """
    try:
        evidence_packet = _read_json_if_exists(step1_evidence_packet_path())
        readiness = _read_json_if_exists(step1_approval_registry_switch_readiness_path())
        baseline_registry = _read_json_if_exists(step1_active_research_anchor_registry_path())
        approvals_registry = _read_json_if_exists(step1_active_research_anchor_registry_with_approvals_path())
        approvals_validation = _read_json_if_exists(step1_research_anchor_approvals_validation_path())
        revocations_validation = _read_json_if_exists(step1_research_anchor_revocations_validation_path())
        dual_read_diff = _read_json_if_exists(step1_approval_registry_dual_read_diff_path())
        dual_ground_diff = _read_json_if_exists(step1_support_signals_dual_ground_diff_path())
        candidates = _read_json_if_exists(step1_research_anchor_candidates_path())
        support_signals = _read_json_if_exists(step1_compiled_support_signals_path())

        result = build_grounding_status_observatory(
            evidence_packet=evidence_packet if isinstance(evidence_packet, Mapping) else None,
            embedded_registry_selection=None,
            readiness=readiness if isinstance(readiness, Mapping) else None,
            baseline_registry=baseline_registry if isinstance(baseline_registry, Mapping) else None,
            approvals_registry=approvals_registry if isinstance(approvals_registry, Mapping) else None,
            approvals_validation=approvals_validation if isinstance(approvals_validation, Mapping) else None,
            revocations_validation=revocations_validation if isinstance(revocations_validation, Mapping) else None,
            dual_read_diff=dual_read_diff if isinstance(dual_read_diff, Mapping) else None,
            support_signals_dual_ground_diff=dual_ground_diff
            if isinstance(dual_ground_diff, Mapping)
            else None,
            candidates=candidates if isinstance(candidates, Mapping) else None,
            support_signals=support_signals if isinstance(support_signals, Mapping) else None,
        )
        if workflow_identity_mismatch:
            blockers = [
                value
                for value in result.get("blockers", [])
                if isinstance(value, str)
            ]
            if "workflow_approval_source_identity_mismatch" not in blockers:
                blockers.append("workflow_approval_source_identity_mismatch")
            diagnostics = (
                dict(result.get("diagnostics"))
                if isinstance(result.get("diagnostics"), Mapping)
                else {}
            )
            diagnostics["workflow_approval_source_identity_mismatch"] = True
            diagnostics["diagnostics_incomplete"] = True
            result = {
                **dict(result),
                "blockers": blockers,
                "diagnostics": diagnostics,
            }
        write_json(step1_grounding_status_observatory_path(), result)
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        pass


def _write_step1a_grounding_compile_shadow_diff_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> dict[str, Any]:
    """Build + write the S1A-1 Step 1A shadow diff (report-only).

    Calls the pure Step 1A extraction bundle after existing deterministic/R2G
    Step 1 artifacts are already present, compares semantic summaries, and writes
    ``step1a_grounding_compile_shadow_diff.json``. The diff is consumed by
    NOTHING: not readiness, not evidence_packet, not support_signals, not
    availability, not gates, not Step 2/3/4, not weekly, not broker/live, and not
    any allowed_actions / order path. Any failure is swallowed.
    """
    output_path = step1a_grounding_compile_shadow_diff_path()
    current_paths = _step1a_shadow_current_artifact_paths()
    try:
        snapshot_path = current_inputs_dir() / "portfolio_snapshot.txt"
        try:
            snapshot_text: str | None = load_portfolio_snapshot_text()
        except Exception:  # noqa: BLE001 - mirror evidence-packet DATA_GAP behavior
            snapshot_text = None

        last_good = read_last_good_research_handoff(step1_state_dir())
        current_artifacts = _read_step1a_shadow_current_artifacts(current_paths)
        bundle = _build_step1a_grounding_compile_bundle_from_sanitized_source(
            strategy_settings=strategy_settings,
            research_anchors_path=current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME,
            research_anchor_approvals_path=current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME,
            portfolio_snapshot_text=snapshot_text,
            portfolio_snapshot_path=snapshot_path,
            last_good_available=last_good.available,
            last_good_metadata=last_good.metadata,
            strategy_settings_path=current_inputs_dir() / "strategy_settings.yaml",
            last_good_metadata_path=last_good_research_handoff_metadata_path(step1_state_dir()),
            active_registry_artifact_path=step1_active_research_anchor_registry_path(),
            approvals_registry_artifact_path=step1_active_research_anchor_registry_with_approvals_path(),
            optional_research_anchor_candidates=current_artifacts.get("research_anchor_candidates")
            if isinstance(current_artifacts.get("research_anchor_candidates"), Mapping)
            else None,
            optional_compiled_support_signals=current_artifacts.get("compiled_support_signals")
            if isinstance(current_artifacts.get("compiled_support_signals"), Mapping)
            else None,
            approval_source=captured_approval_source,
        )
        diff = build_step1a_grounding_compile_shadow_diff(
            step1a_bundle=bundle,
            current_artifacts=current_artifacts,
            current_artifact_paths=current_paths,
        )
        _annotate_step1a_shadow_diff_io(
            diff,
            current_paths=current_paths,
            current_artifacts=current_artifacts,
            output_path=output_path,
        )
        write_json(output_path, diff)
        return {"path": str(output_path), "comparison_status": str(diff.get("comparison_status", ""))}
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        failure = build_step1a_grounding_compile_shadow_diff(
            step1a_bundle=None,
            current_artifacts={},
            current_artifact_paths=current_paths,
            shadow_run_error=str(exc),
        )
        # The exception may have interrupted artifact reads, so no read is claimed.
        _annotate_step1a_shadow_diff_io(
            failure,
            current_paths=current_paths,
            current_artifacts={},
            output_path=output_path,
        )
        try:
            write_json(output_path, failure)
        except Exception:  # noqa: BLE001 - even failure reporting must be best-effort
            pass
        return {"path": str(output_path), "comparison_status": "failed"}


def _step1a_shadow_current_artifact_paths() -> dict[str, Path | None]:
    return {
        "active_research_anchor_registry": step1_active_research_anchor_registry_path(),
        "research_anchor_approvals_validation": step1_research_anchor_approvals_validation_path(),
        "research_anchor_revocations_validation": step1_research_anchor_revocations_validation_path(),
        "active_research_anchor_registry_with_approvals": step1_active_research_anchor_registry_with_approvals_path(),
        "approval_registry_dual_read_diff": step1_approval_registry_dual_read_diff_path(),
        "approval_registry_switch_readiness": step1_approval_registry_switch_readiness_path(),
        # S1A-2: the in-memory selection is now also persisted (report-only) by the
        # evidence-packet layer, so the shadow diff can compare it directly.
        "embedded_active_anchor_registry_selection": step1_embedded_active_registry_selection_path(),
        "evidence_packet": step1_evidence_packet_path(),
        "grounding_status_observatory": step1_grounding_status_observatory_path(),
        # Optional diagnostic inputs for the Step 1A bundle's observatory summary.
        "research_anchor_candidates": step1_research_anchor_candidates_path(),
        "compiled_support_signals": step1_compiled_support_signals_path(),
    }


def _read_step1a_shadow_current_artifacts(
    paths: Mapping[str, Path | None],
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for key, path in paths.items():
        artifacts[key] = _read_json_if_exists(path) if isinstance(path, Path) else None
    return artifacts


def _annotate_step1a_shadow_diff_io(
    diff: dict[str, Any],
    *,
    current_paths: Mapping[str, Path | None],
    current_artifacts: Mapping[str, Any],
    output_path: Path,
) -> None:
    """Record diagnostic-only path info on the shadow diff.

    ``comparison_input_paths`` lists the intended comparison inputs;
    ``files_read`` lists only artifacts actually loaded this run and stays empty
    on a failed shadow run rather than guessing. Nothing consumes these fields.
    """
    diagnostics = diff.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return
    comparisons = diff.get("comparisons")
    comparison_keys = set(comparisons) if isinstance(comparisons, Mapping) else set()
    diagnostics["comparison_input_paths"] = sorted(
        str(path)
        for key, path in current_paths.items()
        if key in comparison_keys and isinstance(path, Path)
    )
    optional_keys = [key for key in current_paths if key not in comparison_keys]
    if diagnostics.get("shadow_run_failed") is True:
        diagnostics["files_read"] = []
        diagnostics["optional_inputs_read"] = []
        diagnostics["optional_inputs_missing"] = []
    else:
        diagnostics["files_read"] = sorted(
            str(path)
            for key, path in current_paths.items()
            if isinstance(path, Path) and current_artifacts.get(key) is not None
        )
        diagnostics["optional_inputs_read"] = sorted(
            str(current_paths[key])
            for key in optional_keys
            if isinstance(current_paths.get(key), Path) and current_artifacts.get(key) is not None
        )
        diagnostics["optional_inputs_missing"] = sorted(
            str(current_paths[key])
            for key in optional_keys
            if isinstance(current_paths.get(key), Path) and current_artifacts.get(key) is None
        )
    diagnostics["files_written"] = [str(output_path)]


def _allowed_buy_universe_for_anchor_registry(
    strategy_settings: Mapping[str, Any] | None,
) -> list[str]:
    """Deterministic base buy universe (core ∪ satellite) for anchor validation.

    Mirrors the evidence packet's ``allowed_buy_tickers`` derivation so the R2G-1
    registry validates anchors against exactly the same universe the existing
    ``research_anchors`` summary already uses (no divergent validation).
    """
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else {}
    out: list[str] = []
    seen: set[str] = set()
    for key in ("core_universe", "satellite_universe"):
        value = settings.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str) and item.strip():
                ticker = item.strip().upper()
                if ticker not in seen:
                    seen.add(ticker)
                    out.append(ticker)
    return out


def _load_or_build_evidence_packet(
    *,
    strategy_settings: Mapping[str, Any] | None,
    evidence_packet_write_log: list[dict[str, Any]] | None = None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> dict[str, Any]:
    """Ensure the deterministic evidence packet is fresh on disk, then return it.

    Used by the analyst-memo render/parse (R2C) and the handoff compiler (R2D)
    read-back preparation. Falls back to an in-memory build so rendering/parsing
    still works even if the disk write/read fails. This is the parse flow's
    SECOND (or later) guarded write invocation: it overwrites both the evidence
    packet and the embedded-selection artifact, so its statuses are appended to
    ``evidence_packet_write_log`` (when provided) for truthful final-write
    switch-status reporting.
    """
    _write_evidence_packet_report_only(
        strategy_settings=strategy_settings,
        evidence_packet_write_log=evidence_packet_write_log,
        captured_approval_source=captured_approval_source,
    )
    try:
        return read_json(step1_evidence_packet_path())
    except Exception:  # noqa: BLE001 - report-only fallback to in-memory build
        from investment_orchestrator.research.evidence_packet import build_evidence_packet

        try:
            snapshot_text: str | None = load_portfolio_snapshot_text()
        except Exception:  # noqa: BLE001 - missing snapshot -> DATA_GAP, not crash
            snapshot_text = None
        return build_evidence_packet(
            strategy_settings=strategy_settings,
            portfolio_snapshot_text=snapshot_text,
            generated_at=None,
        )


def _run_analyst_memo_parse(
    *,
    strategy_settings: Mapping[str, Any] | None,
    evidence_packet_write_log: list[dict[str, Any]] | None = None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> dict[str, str] | None:
    """Parse + validate a pasted analyst memo and write its two report-only artifacts.

    Returns ``None`` when no raw memo output exists (treated as absent, not an
    error). The evidence universe comes from the deterministic evidence packet;
    the memo can only express a relative view inside that universe.
    """
    raw_path = step1_analyst_memo_raw_output_path()
    if not file_exists(raw_path):
        return None
    raw_text = read_text(raw_path)
    if not raw_text.strip():
        return None

    try:
        packet = read_json(step1_evidence_packet_path())
    except Exception:  # noqa: BLE001 - build the packet if it is not on disk yet
        packet = _load_or_build_evidence_packet(
            strategy_settings=strategy_settings,
            evidence_packet_write_log=evidence_packet_write_log,
            captured_approval_source=captured_approval_source,
        )
    universe = evidence_universe_from_packet(packet)

    result = parse_analyst_memo_text(raw_text, evidence_universe=universe)
    if isinstance(result.memo, Mapping):
        write_json(step1_analyst_memo_path(), dict(result.memo))
    else:
        write_json(
            step1_analyst_memo_path(),
            {
                "schema_version": "analyst_memo_v1",
                "present": result.present,
                "valid": result.valid,
                "note": "no parseable analyst_memo object (see analyst_memo_validation.json).",
                "parse_error": result.parse_error,
            },
        )
    write_json(
        step1_analyst_memo_validation_path(),
        analyst_memo_parse_result_to_dict(result),
    )
    return {
        "present": str(result.present),
        "valid": str(result.valid),
        "memo_path": str(step1_analyst_memo_path()),
        "validation_path": str(step1_analyst_memo_validation_path()),
    }


def _parse_analyst_memo_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    evidence_packet_write_log: list[dict[str, Any]] | None = None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> dict[str, Any]:
    """Run the analyst-memo parse defensively as a report-only layer (R2C).

    Step 1 parse must never fail because of this observer, and the memo must
    never change the degraded-mode decision or any allowed action. A missing raw
    memo is simply skipped; any error is swallowed. When the disk packet is
    unreadable this layer rebuilds it — a guarded write invocation whose
    statuses flow into ``evidence_packet_write_log``.
    """
    absent = {"present": False, "valid": False, "validation_path": "", "memo_path": ""}
    try:
        result = _run_analyst_memo_parse(
            strategy_settings=strategy_settings,
            evidence_packet_write_log=evidence_packet_write_log,
            captured_approval_source=captured_approval_source,
        )
        return result if result is not None else absent
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return absent


def _read_json_if_exists(path: Path) -> Any | None:
    """Read a JSON artifact if present; return None when absent or unreadable."""
    if not file_exists(path):
        return None
    try:
        return read_json(path)
    except Exception:  # noqa: BLE001 - report-only: a malformed artifact is treated as absent
        return None


def _read_current_artifact_observation(
    path: Path,
) -> tuple[bool, bool, Mapping[str, Any] | None]:
    """Read one final current-run artifact for an inert observation.

    ``present`` and ``parseable`` remain distinct so a retained or malformed
    disk artifact cannot be misreported as a successful current final write.
    This helper is intentionally outside the pure retirement-observation
    builder; the builder receives only its already-resolved values.
    """
    present = file_exists(path)
    if not present:
        return False, False, None
    try:
        value = read_json(path)
    except Exception:  # noqa: BLE001 - report-only observation fails closed
        return True, False, None
    return True, True, value if isinstance(value, Mapping) else None


def _resolve_step1a_retirement_observation_code_identity(
    *,
    repo_root_path: Path | None = None,
) -> dict[str, Any]:
    """Best-effort Git identity with no authority effect.

    This optional diagnostic never blocks parsing. A missing Git executable,
    non-repository directory, timeout, or command failure is explicitly
    ``unavailable`` (never ``dirty``). Only a known clean commit is usable as
    retirement evidence; the builder itself receives this result as an injected
    mapping and stays pure.
    """
    root = repo_root_path if repo_root_path is not None else repo_root()
    unavailable = {
        "git_commit": None,
        "git_state": "unavailable",
        "code_version_usable_for_evidence": False,
    }
    try:
        commit_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if commit_result.returncode != 0:
            return unavailable
        commit = commit_result.stdout.strip()
        if not commit:
            return unavailable
        status_result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if status_result.returncode != 0:
            return unavailable
        dirty = bool(status_result.stdout.strip())
        return {
            "git_commit": commit,
            "git_state": "dirty" if dirty else "clean",
            "code_version_usable_for_evidence": not dirty,
        }
    except Exception:  # noqa: BLE001 - best-effort identity must never block parse
        return unavailable


def _write_step1a_retirement_observation_report_only() -> dict[str, str]:
    """Write the one-run Step 1A retirement observation after final sources.

    The grounding observatory remains production-sourced: this writer only
    reads its final disk artifact and records that integration fact. It does not
    replay an observatory, recompute support signals, availability, allowed
    actions, or any guard. No production code reads the resulting file.
    """
    output_path = step1a_retirement_observation_path()
    try:
        artifact_paths = {
            "switch_status": step1a_artifact_switch_status_path(),
            "shadow_diff": step1a_grounding_compile_shadow_diff_path(),
            "evidence_packet": step1_evidence_packet_path(),
            "embedded_selection": step1_embedded_active_registry_selection_path(),
            "compiled_support_signals": step1_compiled_support_signals_path(),
            "grounding_status_observatory": step1_grounding_status_observatory_path(),
            "research_availability": step1_research_availability_path(),
        }
        snapshots = {
            name: _read_current_artifact_observation(path)
            for name, path in artifact_paths.items()
        }
        evidence_present, evidence_parseable, evidence_packet = snapshots["evidence_packet"]
        support_present, support_parseable, compiled_support_signals = snapshots[
            "compiled_support_signals"
        ]
        observatory_mapping = snapshots["grounding_status_observatory"][2]
        observation = build_step1a_retirement_observation(
            generated_at=datetime.now(timezone.utc).isoformat(),
            code_identity=_resolve_step1a_retirement_observation_code_identity(),
            switch_status=snapshots["switch_status"][2],
            shadow_diff=snapshots["shadow_diff"][2],
            evidence_packet=evidence_packet,
            embedded_selection=snapshots["embedded_selection"][2],
            compiled_support_signals=compiled_support_signals,
            grounding_status_observatory=observatory_mapping,
            research_availability=snapshots["research_availability"][2],
            evidence_packet_artifact_present=evidence_present,
            evidence_packet_artifact_parseable=evidence_parseable,
            compiled_support_signals_artifact_present=support_present,
            compiled_support_signals_artifact_parseable=support_parseable,
            observatory_integration_result=(
                "production_sourced" if observatory_mapping is not None else None
            ),
        )
        write_json(output_path, observation)
        return {
            "path": str(output_path),
            "observation_completeness": str(observation.get("observation_completeness", "")),
        }
    except Exception:  # noqa: BLE001 - report-only writer must never affect Step 1
        return {"path": "", "observation_completeness": ""}


def _compiled_handoff_availability_inputs() -> dict[str, Any]:
    """Load the R2D compiled-handoff validation + metadata for the availability evaluator.

    Report-only: a missing / malformed compiled artifact is treated as absent, so
    the evaluator falls back to its pre-R2E.1 behavior (no relabel).
    """
    return {
        "compiled_candidate_validation": _read_json_if_exists(step1_compiled_handoff_validation_path()),
        "compiled_metadata": _read_json_if_exists(step1_compiled_handoff_metadata_path()),
        "compiled_support_signals": _read_json_if_exists(step1_compiled_support_signals_path()),
        "promoted_pointer": _read_json_if_exists(step1_active_research_handoff_source_path()),
        "promoted_effective_handoff": _read_json_if_exists(step1_effective_research_handoff_path()),
        "promoted_effective_validation": _read_json_if_exists(
            step1_effective_research_handoff_validation_path()
        ),
        "compiled_source_artifacts": {
            "compiled_research_handoff_candidate": str(step1_compiled_handoff_candidate_path()),
            "compiled_research_handoff_validation": str(step1_compiled_handoff_validation_path()),
            "compiled_research_handoff_metadata": str(step1_compiled_handoff_metadata_path()),
            "compiled_support_signals": str(step1_compiled_support_signals_path()),
        },
        "promoted_source_artifacts": {
            "active_research_handoff_source": str(step1_active_research_handoff_source_path()),
            "research_handoff_candidate_effective": str(step1_effective_research_handoff_path()),
            "research_handoff_candidate_effective_validation": str(
                step1_effective_research_handoff_validation_path()
            ),
        },
    }


def _load_analyst_memo_for_compiler() -> Mapping[str, Any] | None:
    """Read the parsed analyst memo artifact for the compiler, if present.

    The compiler re-validates whatever it is given, so a stub / invalid memo is
    safely classified as ``invalid_memo_ignored``. A missing memo file means the
    compiler runs in ``evidence_only`` mode.
    """
    memo_path = step1_analyst_memo_path()
    if not file_exists(memo_path):
        return None
    try:
        memo = read_json(memo_path)
    except Exception:  # noqa: BLE001 - unreadable memo -> treated as absent
        return None
    return memo if isinstance(memo, Mapping) else None


def _run_compile_research_handoff(
    *,
    strategy_settings: Mapping[str, Any] | None,
    evidence_packet_write_log: list[dict[str, Any]] | None = None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> dict[str, str]:
    """Compile + validate + write the three report-only R2D artifacts.

    The read-back preparation below is the parse flow's second guarded
    evidence-packet/selection write invocation; its statuses flow into
    ``evidence_packet_write_log`` so switch status reports final disk truth.
    """
    packet = _load_or_build_evidence_packet(
        strategy_settings=strategy_settings,
        evidence_packet_write_log=evidence_packet_write_log,
        captured_approval_source=captured_approval_source,
    )
    analyst_memo = _load_analyst_memo_for_compiler()
    result = write_compiled_research_handoff(
        candidate_path=step1_compiled_handoff_candidate_path(),
        validation_path=step1_compiled_handoff_validation_path(),
        metadata_path=step1_compiled_handoff_metadata_path(),
        evidence_packet=packet,
        analyst_memo=analyst_memo,
        strategy_settings=strategy_settings,
        evidence_packet_path=str(step1_evidence_packet_path()),
        analyst_memo_path=str(step1_analyst_memo_path()) if analyst_memo is not None else None,
        support_signals_path=step1_compiled_support_signals_path(),
    )
    return {
        "candidate_path": result["compiled_research_handoff_candidate_path"],
        "validation_path": result["compiled_research_handoff_validation_path"],
        "metadata_path": result["compiled_research_handoff_metadata_path"],
        "support_signals_path": result.get("compiled_support_signals_path", ""),
        "compilation_mode": result["compilation_mode"],
        "compiled_candidate_valid": result["compiled_candidate_valid"],
    }


def _compile_research_handoff_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    evidence_packet_write_log: list[dict[str, Any]] | None = None,
    captured_approval_source: _ValidatedCapturedResearchAnchorApprovalSource | None = None,
) -> dict[str, Any]:
    """Run the deterministic handoff compiler defensively as a report-only layer (R2D).

    Step 1 parse must never fail because of this observer, and the compiled
    candidate must never change the degraded-mode decision or any allowed action
    (it is not fed into the availability evaluator). Any error is swallowed.
    Its evidence-packet read-back preparation re-runs the guarded packet +
    selection writers; those statuses flow into ``evidence_packet_write_log``.
    """
    empty = {
        "candidate_path": "",
        "validation_path": "",
        "metadata_path": "",
        "support_signals_path": "",
        "compilation_mode": "",
        "compiled_candidate_valid": "",
    }
    try:
        return _run_compile_research_handoff(
            strategy_settings=strategy_settings,
            evidence_packet_write_log=evidence_packet_write_log,
            captured_approval_source=captured_approval_source,
        )
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return empty


def _run_actionable_handoff_preview() -> dict[str, Any]:
    """Build + write the R2E.5b-0 preview from the just-written report-only artifacts.

    Only runs when ``compiled_support_signals.json`` exists (per R2E.5b-0 scope). It
    reads the compiled support signals + evidence packet + parsed memo + compiled
    handoff candidate that the R2D/R2E.3 flow already wrote, and derives a separate
    preview artifact. It never mutates any of those inputs and is never fed into the
    availability evaluator or Step 2.
    """
    signals_path = step1_compiled_support_signals_path()
    compiled_support_signals = _read_json_if_exists(signals_path)
    if not isinstance(compiled_support_signals, Mapping):
        return {"actionable_handoff_preview_path": ""}

    evidence_packet_path = step1_evidence_packet_path()
    candidate_path = step1_compiled_handoff_candidate_path()
    result = write_actionable_handoff_preview(
        output_path=step1_actionable_handoff_preview_path(),
        evidence_packet=_read_json_if_exists(evidence_packet_path),
        analyst_memo=_load_analyst_memo_for_compiler(),
        compiled_support_signals=compiled_support_signals,
        compiled_handoff_candidate=_read_json_if_exists(candidate_path),
        evidence_packet_path=str(evidence_packet_path),
        compiled_support_signals_path=str(signals_path),
        compiled_handoff_candidate_path=str(candidate_path),
    )
    return result


def _build_actionable_handoff_preview_report_only() -> dict[str, Any]:
    """Run the actionable-handoff preview defensively as a report-only layer (R2E.5b-0).

    Step 1 parse must never fail because of this observer. The preview is a SEPARATE
    artifact: it does not change the active compiled handoff (still non-actionable),
    the availability state, ``allowed_actions``, the Step 2/3/4 workflow, the order
    compiler, prompts, or any gate, and it never adds ``NEW_BUY`` /
    ``ORDER_COMPILATION``. Any error is swallowed.
    """
    empty = {"actionable_handoff_preview_path": ""}
    try:
        return _run_actionable_handoff_preview()
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return empty


def _run_actionable_handoff_candidate(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Build + validate + write the R2E.5b-1 actionable candidate from the preview.

    Only runs when ``compiled_actionable_handoff_preview.json`` exists. It reads the
    just-written report-only artifacts (preview + support signals + evidence packet +
    the active compiled handoff as base) and writes a SEPARATE candidate + validation
    + metadata. It never mutates the active compiled handoff and is never fed into the
    availability evaluator, Step 2, the weekly path, or the final safety gate.
    """
    preview_path = step1_actionable_handoff_preview_path()
    preview = _read_json_if_exists(preview_path)
    if not isinstance(preview, Mapping):
        return {"actionable_candidate_path": ""}

    evidence_packet_path = step1_evidence_packet_path()
    support_signals_path = step1_compiled_support_signals_path()
    base_path = step1_compiled_handoff_candidate_path()
    result = write_actionable_handoff_candidate(
        candidate_path=step1_actionable_handoff_candidate_path(),
        validation_path=step1_actionable_handoff_validation_path(),
        metadata_path=step1_actionable_handoff_metadata_path(),
        evidence_packet=_read_json_if_exists(evidence_packet_path),
        analyst_memo=_load_analyst_memo_for_compiler(),
        actionable_handoff_preview=preview,
        compiled_support_signals=_read_json_if_exists(support_signals_path),
        base_candidate=_read_json_if_exists(base_path),
        strategy_settings=strategy_settings,
        actionable_handoff_preview_path=str(preview_path),
        compiled_support_signals_path=str(support_signals_path),
        evidence_packet_path=str(evidence_packet_path),
        base_candidate_path=str(base_path),
    )
    return result


def _build_actionable_handoff_candidate_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Run the actionable-candidate builder defensively as a report-only layer (R2E.5b-1).

    Step 1 parse must never fail because of this observer. The candidate is a SEPARATE
    artifact: it never mutates the active compiled handoff (still non-actionable), the
    availability state, ``allowed_actions``, the Step 2/3/4 workflow, the order compiler,
    prompts, or any gate, and adds no ``NEW_BUY`` / ``ORDER_COMPILATION``. Any error is
    swallowed.
    """
    empty = {"actionable_candidate_path": ""}
    try:
        return _run_actionable_handoff_candidate(strategy_settings=strategy_settings)
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return empty


def _run_actionable_promotion_eligibility(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Evaluate + write the R2E.5b-3 promotion-eligibility artifact.

    Only runs when the actionable candidate + metadata exist. It re-reads the
    just-written report-only artifacts and writes a SEPARATE eligibility artifact;
    it never creates the future active pointer / effective handoff, never mutates
    the active compiled handoff, and is never fed into the availability evaluator,
    Step 2, the weekly path, or any gate.
    """
    candidate = _read_json_if_exists(step1_actionable_handoff_candidate_path())
    metadata = _read_json_if_exists(step1_actionable_handoff_metadata_path())
    if not isinstance(candidate, Mapping) or not isinstance(metadata, Mapping):
        return {"actionable_promotion_eligibility_path": ""}

    settings_as_of = (
        strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
    )
    return write_actionable_promotion_eligibility(
        output_path=step1_actionable_promotion_eligibility_path(),
        evidence_packet=_read_json_if_exists(step1_evidence_packet_path()),
        compiled_support_signals=_read_json_if_exists(step1_compiled_support_signals_path()),
        actionable_preview=_read_json_if_exists(step1_actionable_handoff_preview_path()),
        actionable_candidate=candidate,
        actionable_candidate_validation=_read_json_if_exists(step1_actionable_handoff_validation_path()),
        actionable_candidate_metadata=metadata,
        active_compiled_handoff=_read_json_if_exists(step1_compiled_handoff_candidate_path()),
        strategy_settings=strategy_settings,
        today=settings_as_of,
        evidence_packet_path=str(step1_evidence_packet_path()),
        compiled_support_signals_path=str(step1_compiled_support_signals_path()),
        actionable_preview_path=str(step1_actionable_handoff_preview_path()),
        actionable_candidate_path=str(step1_actionable_handoff_candidate_path()),
        actionable_candidate_validation_path=str(step1_actionable_handoff_validation_path()),
        actionable_candidate_metadata_path=str(step1_actionable_handoff_metadata_path()),
        active_compiled_handoff_path=str(step1_compiled_handoff_candidate_path()),
    )


def _build_actionable_promotion_eligibility_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Run the promotion-eligibility checker defensively as a report-only layer (R2E.5b-3).

    Step 1 parse must never fail because of this observer. The eligibility artifact
    is a SEPARATE report-only file: it never promotes, never creates the future
    active pointer, never mutates the active compiled handoff (still non-actionable),
    the availability state, ``allowed_actions``, the Step 2/3/4 workflow, the order
    compiler, prompts, or any gate, and adds no ``NEW_BUY`` / ``ORDER_COMPILATION``.
    Any error is swallowed.
    """
    empty = {"actionable_promotion_eligibility_path": ""}
    try:
        return _run_actionable_promotion_eligibility(strategy_settings=strategy_settings)
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return empty


def _run_actionable_promotion_pointer_preview(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build + write the R2E.5b-4 pointer preview (+ effective preview when promotable).

    Only runs when the eligibility artifact exists. Nothing is promoted: the
    reserved active pointer / effective handoff names are never written, and no
    consumer reads the previews.
    """
    eligibility = _read_json_if_exists(step1_actionable_promotion_eligibility_path())
    if not isinstance(eligibility, Mapping):
        return {"actionable_promotion_pointer_preview_path": ""}

    settings_as_of = (
        strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
    )
    return write_actionable_promotion_pointer_preview(
        pointer_preview_path=step1_actionable_promotion_pointer_preview_path(),
        effective_preview_path=step1_actionable_effective_handoff_preview_path(),
        effective_preview_validation_path=step1_actionable_effective_handoff_preview_validation_path(),
        eligibility=eligibility,
        actionable_candidate=_read_json_if_exists(step1_actionable_handoff_candidate_path()),
        actionable_candidate_validation=_read_json_if_exists(step1_actionable_handoff_validation_path()),
        actionable_candidate_metadata=_read_json_if_exists(step1_actionable_handoff_metadata_path()),
        strategy_settings=strategy_settings,
        today=settings_as_of,
        candidate_path=str(step1_actionable_handoff_candidate_path()),
        eligibility_path=str(step1_actionable_promotion_eligibility_path()),
    )


def _build_actionable_promotion_pointer_preview_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Run the pointer-preview builder defensively as a report-only layer (R2E.5b-4).

    Step 1 parse must never fail because of this observer. The pointer preview and
    effective-handoff preview are SEPARATE report-only files: nothing is promoted,
    the reserved active pointer / effective handoff names are never created, the
    active compiled handoff (still non-actionable), availability state,
    ``allowed_actions``, the Step 2/3/4 workflow, the order compiler, prompts, and
    every gate are unchanged, and no ``NEW_BUY`` / ``ORDER_COMPILATION`` is added.
    Any error is swallowed.
    """
    empty = {"actionable_promotion_pointer_preview_path": ""}
    try:
        return _run_actionable_promotion_pointer_preview(strategy_settings=strategy_settings)
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return empty


def _run_actionable_promotion_pointer(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Attempt the R2E.5b-5a real pointer write from the just-written previews.

    Only runs when the pointer-preview artifact exists. Availability may observe
    the pointer as pending-gates only; Step 2, the weekly actionable path, and
    every gate remain unchanged. The writer itself is fail-closed and never raises.
    """
    preview = _read_json_if_exists(step1_actionable_promotion_pointer_preview_path())
    if not isinstance(preview, Mapping):
        return {"active_pointer_created": "", "active_research_handoff_source_path": ""}

    settings_as_of = (
        strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
    )
    return write_actionable_promotion_pointer_if_eligible(
        pointer_preview=preview,
        effective_preview=_read_json_if_exists(step1_actionable_effective_handoff_preview_path()),
        effective_preview_validation=_read_json_if_exists(
            step1_actionable_effective_handoff_preview_validation_path()
        ),
        output_pointer_path=step1_active_research_handoff_source_path(),
        output_effective_path=step1_effective_research_handoff_path(),
        output_effective_validation_path=step1_effective_research_handoff_validation_path(),
        output_status_path=step1_active_pointer_write_status_path(),
        strategy_settings=strategy_settings,
        today=settings_as_of,
        pointer_preview_path=str(step1_actionable_promotion_pointer_preview_path()),
    )


def _write_actionable_promotion_pointer_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Run the real pointer writer defensively as layer 0h (R2E.5b-5a).

    Step 1 parse must never fail because of this layer. The pointer + effective
    handoff remain non-authorizing: availability may recognize them as
    pending-gates only, the active compiled handoff stays the non-actionable
    source of record for existing compiled behavior, the Step 2/3/4 workflow,
    order compiler, prompts, and every gate are unchanged, and no ``NEW_BUY`` /
    ``ORDER_COMPILATION`` is added. Any error is swallowed.
    """
    empty = {"active_pointer_created": "", "active_research_handoff_source_path": ""}
    try:
        return _run_actionable_promotion_pointer(strategy_settings=strategy_settings)
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return empty


def _write_promoted_step2_gate_dry_run_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_decision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Run the R2E.5b-6a verification + R2E.5b-6b dry-run writers defensively.

    Step 1 parse must never fail because of this layer. It re-verifies the real
    pointer / effective handoff / validation with the R2E.5b-6a helper, writes
    ``promoted_handoff_step2_verification.json``, then simulates the promoted
    decision-only gate against the supplied PRELIMINARY (pre-upgrade) decision
    and writes ``promoted_step2_gate_dry_run.json``.

    R2E.5b-6c: the availability evaluator now consumes the returned
    ``verification`` / ``dry_run`` objects — a fully passing pair upgrades an
    eligible pending-gates run to the Step 2 decision-only state. The dry-run's
    recorded ``would_allow`` / ``current_real_gate_allows`` remain the
    pre-upgrade diagnostic; the artifacts still never feed Step 3/4, the order
    compiler, or the final execution safety gate, and no ``NEW_BUY`` /
    ``ORDER_COMPILATION`` is added. Any error is swallowed (no upgrade).
    """
    try:
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        verification = verify_promoted_handoff_for_step2_decision(
            active_pointer=_read_json_if_exists(step1_active_research_handoff_source_path()),
            effective_handoff=_read_json_if_exists(step1_effective_research_handoff_path()),
            effective_validation=_read_json_if_exists(
                step1_effective_research_handoff_validation_path()
            ),
            today=_parse_iso_date_or_none(settings_as_of),
        )
        write_json(step1_promoted_handoff_step2_verification_path(), verification)

        dry_run = evaluate_promoted_step2_gate_dry_run(
            research_decision=research_decision,
            promoted_verification=verification,
        )
        write_json(step1_promoted_step2_gate_dry_run_path(), dry_run)
        return {
            "promoted_handoff_step2_verification_path": str(
                step1_promoted_handoff_step2_verification_path()
            ),
            "promoted_step2_gate_dry_run_path": str(step1_promoted_step2_gate_dry_run_path()),
            "promoted_step2_gate_dry_run_would_allow": str(
                dry_run.get("would_allow_step2_promoted_decision")
            ),
            "verification": verification,
            "dry_run": dry_run,
        }
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return dict(_EMPTY_PROMOTED_STEP2_SUMMARY)


def _write_promoted_step3_audit_dry_run_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_decision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Write R2E.5b-6e Step 3 audit-only prerequisite diagnostics.

    The artifacts remain deterministic and non-authorizing. R2E.5b-6f may
    consume a passing pair as a prerequisite for the audit-only state, but the
    artifacts still never open Step 4, ORDER_COMPILATION, final execution, or
    live/broker paths. Missing Step 2 marker or decision-packet artifacts simply
    produce a fail-closed dry-run verdict.
    """
    try:
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        step2_marker_path = _step2_promoted_decision_only_report_path()
        step2_packet_path = _step2_decision_packet_report_path()
        source_artifacts = {
            "active_research_handoff_source": str(step1_active_research_handoff_source_path()),
            "research_handoff_candidate_effective": str(step1_effective_research_handoff_path()),
            "research_handoff_candidate_effective_validation": str(
                step1_effective_research_handoff_validation_path()
            ),
            "step2_promoted_decision_only": str(step2_marker_path),
            "step2_decision_packet": str(step2_packet_path),
        }
        verification = verify_promoted_handoff_for_step3_audit(
            active_pointer=_read_json_if_exists(step1_active_research_handoff_source_path()),
            effective_handoff=_read_json_if_exists(step1_effective_research_handoff_path()),
            effective_validation=_read_json_if_exists(
                step1_effective_research_handoff_validation_path()
            ),
            step2_promoted_marker=_read_json_if_exists(step2_marker_path),
            step2_decision_packet=_read_json_if_exists(step2_packet_path),
            today=_parse_iso_date_or_none(settings_as_of),
            source_artifacts=source_artifacts,
        )
        write_json(step1_promoted_handoff_step3_audit_verification_path(), verification)

        dry_run = evaluate_promoted_step3_audit_gate_dry_run(
            research_decision=research_decision,
            promoted_step3_verification=verification,
        )
        write_json(step1_promoted_step3_audit_gate_dry_run_path(), dry_run)
        return {
            "promoted_handoff_step3_audit_verification_path": str(
                step1_promoted_handoff_step3_audit_verification_path()
            ),
            "promoted_step3_audit_gate_dry_run_path": str(
                step1_promoted_step3_audit_gate_dry_run_path()
            ),
            "promoted_step3_audit_gate_dry_run_would_allow": str(
                dry_run.get("would_allow_promoted_step3_audit")
            ),
            "step3_audit_verification": verification,
            "step3_audit_dry_run": dry_run,
        }
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return {
            "promoted_handoff_step3_audit_verification_path": "",
            "promoted_step3_audit_gate_dry_run_path": "",
            "promoted_step3_audit_gate_dry_run_would_allow": "",
            "step3_audit_verification": None,
            "step3_audit_dry_run": None,
        }


def _write_promoted_step4_readiness_dry_run_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_decision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Write R2E.5b-7b Step 4 preview-only prerequisite diagnostics.

    Report-only and non-authorizing: the artifacts never open Step 4,
    ORDER_COMPILATION, final execution, or live/broker paths, and NOTHING
    consumes them in 7b (not availability, not Step 4, not any gate). Missing
    Step 3 marker / downstream-block artifacts simply produce a fail-closed
    verdict. Any internal error yields an empty summary, never an exception.
    """
    try:
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        source_artifacts = {
            "active_research_handoff_source": str(step1_active_research_handoff_source_path()),
            "research_handoff_candidate_effective": str(step1_effective_research_handoff_path()),
            "research_handoff_candidate_effective_validation": str(
                step1_effective_research_handoff_validation_path()
            ),
            "step2_promoted_decision_only": str(_step2_promoted_decision_only_report_path()),
            "step2_decision_packet": str(_step2_decision_packet_report_path()),
            "step3_promoted_audit_only": str(_step3_promoted_audit_only_report_path()),
            "step3_promoted_audit_only_downstream_block": str(
                _step3_promoted_audit_only_downstream_block_report_path()
            ),
            "step3_template3_audit": str(_step3_template3_audit_report_path()),
            "strategy_settings": str(current_inputs_dir() / "strategy_settings.yaml"),
            "portfolio_snapshot": str(current_inputs_dir() / "portfolio_snapshot.txt"),
        }
        verification = verify_promoted_step3_for_step4_readiness(
            active_pointer=_read_json_if_exists(step1_active_research_handoff_source_path()),
            effective_handoff=_read_json_if_exists(step1_effective_research_handoff_path()),
            effective_validation=_read_json_if_exists(
                step1_effective_research_handoff_validation_path()
            ),
            step2_promoted_marker=_read_json_if_exists(_step2_promoted_decision_only_report_path()),
            step2_decision_packet=_read_json_if_exists(_step2_decision_packet_report_path()),
            step3_promoted_marker=_read_json_if_exists(_step3_promoted_audit_only_report_path()),
            step3_downstream_block=_read_json_if_exists(
                _step3_promoted_audit_only_downstream_block_report_path()
            ),
            step3_audit_output_text=_read_text_if_exists(_step3_template3_audit_report_path()),
            legacy_audited_packet_present=file_exists(
                _step3_audited_decision_packet_report_path()
            ),
            strategy_settings=strategy_settings,
            portfolio_snapshot_text=_read_text_if_exists(
                current_inputs_dir() / "portfolio_snapshot.txt"
            ),
            today=_parse_iso_date_or_none(settings_as_of),
            source_artifacts=source_artifacts,
        )
        write_json(step1_promoted_step4_readiness_verification_path(), verification)

        dry_run = evaluate_promoted_step4_preview_gate_dry_run(
            research_decision=research_decision,
            promoted_step4_verification=verification,
        )
        write_json(step1_promoted_step4_preview_gate_dry_run_path(), dry_run)
        return {
            "promoted_step4_readiness_verification_path": str(
                step1_promoted_step4_readiness_verification_path()
            ),
            "promoted_step4_preview_gate_dry_run_path": str(
                step1_promoted_step4_preview_gate_dry_run_path()
            ),
            "promoted_step4_preview_gate_dry_run_would_allow": str(
                dry_run.get("would_allow_promoted_step4_preview")
            ),
            "step4_readiness_verification": verification,
            "step4_preview_dry_run": dry_run,
        }
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return {
            "promoted_step4_readiness_verification_path": "",
            "promoted_step4_preview_gate_dry_run_path": "",
            "promoted_step4_preview_gate_dry_run_would_allow": "",
            "step4_readiness_verification": None,
            "step4_preview_dry_run": None,
        }


def _write_promoted_final_safety_preflight_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_decision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Write the R2E.5b-7c rowless final-safety preflight diagnostic.

    Report-only and non-authorizing. It reads (never consumes as authority) the
    just-written 7b readiness artifacts plus the promoted chain artifacts and
    the deterministic budget/cap inputs, then writes a single rowless report. It
    grants nothing: no state change, no Step 4, no ORDER_COMPILATION, no order /
    preview rows, no broker/live path, and NOTHING consumes the output. The
    final execution safety gate is reused only as a pure function and its
    behavior is unchanged. Any internal error yields an empty summary.
    """
    try:
        source_artifacts = {
            "research_degraded_mode_decision": str(
                step1_research_degraded_mode_decision_path()
            ),
            "promoted_step4_readiness_verification": str(
                step1_promoted_step4_readiness_verification_path()
            ),
            "promoted_step4_preview_gate_dry_run": str(
                step1_promoted_step4_preview_gate_dry_run_path()
            ),
            "step2_promoted_decision_only": str(_step2_promoted_decision_only_report_path()),
            "step2_decision_packet": str(_step2_decision_packet_report_path()),
            "step3_promoted_audit_only": str(_step3_promoted_audit_only_report_path()),
            "step3_promoted_audit_only_downstream_block": str(
                _step3_promoted_audit_only_downstream_block_report_path()
            ),
            "strategy_settings": str(current_inputs_dir() / "strategy_settings.yaml"),
            "portfolio_snapshot": str(current_inputs_dir() / "portfolio_snapshot.txt"),
            "promoted_final_safety_preflight": str(
                step1_promoted_final_safety_preflight_path()
            ),
        }
        preflight = evaluate_promoted_final_safety_preflight(
            research_decision=research_decision,
            step4_readiness_verification=_read_json_if_exists(
                step1_promoted_step4_readiness_verification_path()
            ),
            step4_preview_gate_dry_run=_read_json_if_exists(
                step1_promoted_step4_preview_gate_dry_run_path()
            ),
            strategy_settings=strategy_settings,
            portfolio_snapshot_text=_read_text_if_exists(
                current_inputs_dir() / "portfolio_snapshot.txt"
            ),
            step2_decision_packet=_read_json_if_exists(_step2_decision_packet_report_path()),
            step2_promoted_marker=_read_json_if_exists(
                _step2_promoted_decision_only_report_path()
            ),
            step3_promoted_marker=_read_json_if_exists(_step3_promoted_audit_only_report_path()),
            step3_downstream_block=_read_json_if_exists(
                _step3_promoted_audit_only_downstream_block_report_path()
            ),
            source_artifacts=source_artifacts,
        )
        write_json(step1_promoted_final_safety_preflight_path(), preflight)
        return {
            "promoted_final_safety_preflight_path": str(
                step1_promoted_final_safety_preflight_path()
            ),
            "promoted_final_safety_preflight_passed": str(preflight.get("preflight_passed")),
            "final_safety_preflight": preflight,
        }
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return {
            "promoted_final_safety_preflight_path": "",
            "promoted_final_safety_preflight_passed": "",
            "final_safety_preflight": None,
        }


def refresh_promoted_step4_readiness_after_step3(
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Refresh the R2E.5b-7b report-only Step 4 readiness diagnostics.

    Step 1 parse runs before the promoted Step 3 marker / downstream block
    exist, so the 7b verifier fails closed at that point. The promoted Step 3
    parse calls this deterministic hook after it writes those artifacts.

    Unlike ``refresh_promoted_step3_audit_only_permission_after_step2``, this
    hook rewrites ONLY the report-only Step 4 readiness files plus the R2E.5b-7c
    rowless final-safety preflight. It does not re-run availability, does not
    rewrite any permission/decision artifact, does not change any state, and
    does not run Step 4, an order compiler, broker automation, or any live
    execution path.
    """
    settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )
    decision = _read_json_if_exists(step1_research_degraded_mode_decision_path())
    decision_mapping = decision if isinstance(decision, Mapping) else None
    summary = _write_promoted_step4_readiness_dry_run_report_only(
        strategy_settings=settings,
        research_decision=decision_mapping,
    )
    # R2E.5b-7c: regenerate the rowless final-safety preflight now that the
    # promoted Step 3 marker / downstream block and fresh 7b artifacts exist.
    preflight_summary = _write_promoted_final_safety_preflight_report_only(
        strategy_settings=settings,
        research_decision=decision_mapping,
    )
    return {
        "promoted_step4_readiness_verification_path": str(
            summary.get("promoted_step4_readiness_verification_path", "")
        ),
        "promoted_step4_preview_gate_dry_run_path": str(
            summary.get("promoted_step4_preview_gate_dry_run_path", "")
        ),
        "promoted_step4_preview_gate_dry_run_would_allow": str(
            summary.get("promoted_step4_preview_gate_dry_run_would_allow", "")
        ),
        "promoted_final_safety_preflight_path": str(
            preflight_summary.get("promoted_final_safety_preflight_path", "")
        ),
        "promoted_final_safety_preflight_passed": str(
            preflight_summary.get("promoted_final_safety_preflight_passed", "")
        ),
    }


def _read_text_if_exists(path: Path) -> str | None:
    """Read a text artifact if present; return None when absent or unreadable."""
    if not file_exists(path):
        return None
    try:
        return read_text(path)
    except Exception:  # noqa: BLE001 - report-only: unreadable text treated as absent
        return None


def _step2_report_artifact_dir() -> Path:
    return repo_root() / "artifacts" / "current" / "step2_decision_builder"


def _step2_promoted_decision_only_report_path() -> Path:
    return _step2_report_artifact_dir() / "step2_promoted_decision_only.json"


def _step2_decision_packet_report_path() -> Path:
    return _step2_report_artifact_dir() / "decision_packet.json"


# R2E.5b-7b: mirrored Step 3 artifact paths. ``step3_audit_engine`` imports from
# this module, so importing its path helpers here would be circular; a
# drift-guard unit test asserts these equal the Step 3 engine's real paths.
def _step3_report_artifact_dir() -> Path:
    return repo_root() / "artifacts" / "current" / "step3_audit_engine"


def _step3_promoted_audit_only_report_path() -> Path:
    return _step3_report_artifact_dir() / "step3_promoted_audit_only.json"


def _step3_promoted_audit_only_downstream_block_report_path() -> Path:
    return _step3_report_artifact_dir() / "step3_promoted_audit_only_downstream_block.json"


def _step3_template3_audit_report_path() -> Path:
    return _step3_report_artifact_dir() / "template3_audit.txt"


def _step3_audited_decision_packet_report_path() -> Path:
    return _step3_report_artifact_dir() / "audited_decision_packet.json"


def _parse_iso_date_or_none(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def compile_step1_research_handoff(
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Standalone deterministic handoff compile (R2D, report-only) for the CLI.

    Builds/reuses the evidence packet, reads the parsed analyst memo if present,
    compiles the strict candidate, validates it, and writes the compiled_*
    artifacts. Report-only: not fed into the degraded-mode decision.
    """
    settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )
    return _run_compile_research_handoff(strategy_settings=settings)


def _write_no_output_research_availability_artifacts_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    diagnostic_reason: str,
    parse_error: str | None = None,
    h1_mapped_facts: Any | None = None,
    raise_on_failure: bool = False,
):
    """Best-effort PR C artifacts for no-output / parse-failure Step 1 runs.

    The original parser caller still raises after this observer writes its
    conservative artifacts; H1 refresh may instead consume the returned result.
    """
    try:
        last_good = read_last_good_research_handoff(step1_state_dir())
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        availability = evaluate_research_availability(
            candidate_validation=None,
            candidate=None,
            strategy_settings=strategy_settings,
            source_as_of_date=None,
            now_date=settings_as_of,
            last_good_handoff=last_good.handoff,
            last_good_metadata=last_good.metadata,
            parsed_output_available=False,
            h1_mapped_facts=h1_mapped_facts,
        )
        diagnostic = {
            "diagnostic_reason": diagnostic_reason,
            "parse_error": parse_error,
        }
        write_json(
            step1_research_availability_path(),
            {**research_availability_result_to_dict(availability), **diagnostic},
        )
        write_json(
            step1_research_freshness_report_path(),
            {**research_freshness_report_to_dict(availability), **diagnostic},
        )
        write_json(
            step1_research_degraded_mode_decision_path(),
            {**research_degraded_mode_decision_to_dict(availability), **diagnostic},
        )
        return availability
    except Exception:
        if raise_on_failure:
            raise
        # Best-effort only: never mask the original parse failure.
        return None


def _write_last_good_research_handoff_report_only(
    *,
    candidate: Mapping[str, Any],
    candidate_validation: Any,
    strategy_settings: Mapping[str, Any] | None,
    source_as_of_date: str | None,
) -> LastGoodResearchHandoffWriteResult:
    """Call the last-good writer defensively so Step 1 parse stays report-only.

    source_run_id is genuinely unknown at parse time (the archive label is only
    assigned later by prepare_next_run), so it is passed as None and recorded as
    "unknown" rather than fabricated.
    """
    try:
        return write_last_good_research_handoff_if_valid(
            candidate=candidate,
            candidate_validation=candidate_validation,
            strategy_settings=strategy_settings,
            source_run_id=None,
            source_as_of_date=source_as_of_date,
            output_dir=step1_state_dir(),
        )
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        return LastGoodResearchHandoffWriteResult(
            wrote=False,
            handoff_path=None,
            metadata_path=None,
            skip_reasons=[f"last-good writer error (report-only, not raised): {exc}"],
            metadata={},
        )
