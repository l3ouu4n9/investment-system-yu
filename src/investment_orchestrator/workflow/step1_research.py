"""Manual Step 1 workflow: render prompt and ingest RESEARCH_JSON."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import json
import re
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import (
    ensure_dir,
    file_exists,
    read_json,
    read_text,
    write_json,
    write_text,
)
from investment_orchestrator.common.paths import repo_root, require_prompt_path
from investment_orchestrator.llm.manual_output import (
    ensure_manual_output_metadata_template,
    render_prompt,
    write_rendered_prompt,
)
from investment_orchestrator.normalizers.research_handoff_candidate import (
    normalize_research_handoff_candidate,
    research_handoff_normalization_result_to_dict,
)
from investment_orchestrator.parsers.extract_research_json import extract_research_json
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
from investment_orchestrator.research.evidence_packet import write_evidence_packet
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
from investment_orchestrator.research.research_anchor_approval_manifest import (
    validate_research_anchor_approvals,
    write_research_anchor_approvals_validation,
)
from investment_orchestrator.research.research_anchor_revocation_manifest import (
    write_research_anchor_revocations_validation,
)
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    build_active_research_anchor_registry_with_approvals,
)
from investment_orchestrator.research.approval_registry_dual_read_diff import (
    build_approval_registry_dual_read_diff,
)
from investment_orchestrator.research.approval_registry_switch_readiness import (
    write_approval_registry_switch_readiness,
)
from investment_orchestrator.research.support_signals_dual_ground_diff import (
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
    evaluate_research_availability,
    research_availability_result_to_dict,
    research_degraded_mode_decision_to_dict,
    research_freshness_report_to_dict,
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
RESEARCH_ANCHORS_INPUT_FILENAME = "research_anchors.yaml"
RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME = "research_anchor_approvals.yaml"
CURRENT_RUN_INPUT_NOTES_RE = re.compile(
    r"(?:\r?\n)*────────────────────────────────────────\r?\n"
    r"【Current Run Inputs（injected by workflow; rendered prompt must contain actual values, not placeholder notes）】"
    r"[\s\S]*?(?=CURRENT_RUN_INPUTS_START)",
)


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


def load_strategy_settings_yaml_text() -> str:
    """Read the operator-maintained strategy settings YAML exactly as stored on disk."""
    return _require_non_empty_text(
        current_inputs_dir() / "strategy_settings.yaml",
        label="strategy settings YAML input",
    )


def load_strategy_settings() -> dict[str, Any]:
    """Parse the operator-maintained strategy settings YAML."""
    return parse_strategy_settings_text(load_strategy_settings_yaml_text())


def load_strategy_settings_for_handoff_validation() -> dict[str, Any] | None:
    """Load strategy settings for report-only handoff validation without blocking parse."""
    try:
        return load_strategy_settings()
    except Exception:
        return None


def load_portfolio_snapshot_text() -> str:
    """Read the operator-maintained portfolio snapshot exactly as stored on disk."""
    return _require_non_empty_text(
        current_inputs_dir() / "portfolio_snapshot.txt",
        label="portfolio snapshot input",
    )


def load_current_run_user_approved_extended_etf_static_list_json() -> str:
    """Load the current-run approved ETF static list and serialize it as a JSON array string."""
    strategy_settings = load_strategy_settings()
    approved_static_list = strategy_settings.get("user_approved_extended_etf_static_list")
    if approved_static_list is None:
        raise ValueError(
            "Missing required field 'user_approved_extended_etf_static_list' in "
            "inputs/current/strategy_settings.yaml"
        )
    if not isinstance(approved_static_list, list):
        raise ValueError(
            "inputs/current/strategy_settings.yaml field "
            "'user_approved_extended_etf_static_list' must be a list."
        )
    if not all(isinstance(item, str) for item in approved_static_list):
        raise ValueError(
            "inputs/current/strategy_settings.yaml field "
            "'user_approved_extended_etf_static_list' must contain only strings."
        )
    return json.dumps(approved_static_list, ensure_ascii=False, indent=2)


def sanitize_rendered_step1_prompt(text: str) -> str:
    """Remove workflow-only current-run explanatory notes from the rendered prompt."""
    return CURRENT_RUN_INPUT_NOTES_RE.sub("\n", text, count=1)


def build_step1_prompt_text() -> str:
    """Render the Step 1 prompt without mutating the source prompt file."""
    prompt_template = read_text(resolve_step1_prompt_template_path()).rstrip()
    strategy_settings_text = load_strategy_settings_yaml_text()
    portfolio_snapshot_text = load_portfolio_snapshot_text()
    approved_static_list_json = load_current_run_user_approved_extended_etf_static_list_json()

    rendered_prompt = render_prompt(
        prompt_template,
        {
            "current_run_user_approved_extended_etf_static_list_json": approved_static_list_json,
            "strategy_settings_yaml": strategy_settings_text,
            "portfolio_snapshot": portfolio_snapshot_text,
        },
    )
    return sanitize_rendered_step1_prompt(rendered_prompt).rstrip() + "\n"


def render_step1_prompt() -> dict[str, str]:
    """Write the rendered Step 1 prompt and prepare the manual output artifact."""
    artifact_dir = step1_artifact_dir()
    prompt_output_path = step1_prompt_path()
    raw_output_path = step1_raw_output_path()

    write_rendered_prompt(prompt_output_path, build_step1_prompt_text())
    if not file_exists(raw_output_path):
        write_text(raw_output_path, "")
    metadata_path = ensure_manual_output_metadata_template(
        raw_output_path,
        prompt_path=prompt_output_path,
    )

    return {
        "artifact_dir": str(artifact_dir),
        "prompt_path": str(prompt_output_path),
        "raw_output_path": str(raw_output_path),
        "raw_output_metadata_path": str(metadata_path),
        "prompt_template_path": str(resolve_step1_prompt_template_path()),
    }


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


def parse_step1_output(
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Parse and validate the manual Step 1 output into research_output.json."""
    handoff_strategy_settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )

    # Report-only layer 0 (R2B/R2G-5c-2): deterministic evidence packet. Built
    # from operator inputs + last-good metadata only (no LLM, no parsed payload).
    # Its embedded active_anchor_registry is now selected by a fresh, in-memory
    # approvals-readiness compile; this still changes no permission, gate, or
    # order path and remains independent of the degraded-mode decision below.
    _write_evidence_packet_report_only(strategy_settings=handoff_strategy_settings)

    # Report-only layer 0a2 (R2G-1): deterministic baseline active anchor registry
    # compiled from the operator research_anchors.yaml source only. The standalone
    # artifact remains an observer; support_signals consumes the embedded registry
    # that the evidence packet selected above.
    _write_active_research_anchor_registry_report_only(
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
        strategy_settings=handoff_strategy_settings
    )

    # Report-only layer 0c (R2D): deterministic strict-handoff compiler. Compiles
    # the evidence packet (+ optional valid analyst memo) into a structurally
    # complete candidate, validates it with the existing validator, and writes
    # compiled_* artifacts. It is NOT fed into research_degraded_mode_decision and
    # never changes allowed_actions; evidence-only / invalid-memo never support
    # NEW_BUY. The raw Deep Research candidate below remains the active source.
    compiled_handoff_summary = _compile_research_handoff_report_only(
        strategy_settings=handoff_strategy_settings
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
    _write_research_anchor_approvals_validation_report_only(
        strategy_settings=handoff_strategy_settings
    )

    # Report-only layer 0c3b (R2G-5d-0): operator-REVOCATION manifest validator.
    # Reads the optional revocations: section of research_anchor_approvals.yaml and
    # writes research_anchor_revocations_validation.json — a diagnostic that answers
    # "does this revocation deterministically bind to one operator-approved anchor?".
    # Strictly inert: it APPLIES nothing, does not change the approvals-inclusive
    # registry compiler / support_signals / evidence_packet registry selection /
    # readiness, is consumed by NOTHING, and cannot affect allowed_actions / add
    # NEW_BUY / ORDER_COMPILATION. Unknown target fails closed (mandatory amendment).
    _write_research_anchor_revocations_validation_report_only(
        strategy_settings=handoff_strategy_settings
    )

    # Report-only layer 0c4 (R2G-5b): approvals-inclusive active registry + dual-read
    # diff. Overlays validated operator-approved anchors (recomputed directly from
    # research_anchor_approvals.yaml; the R2G-5a artifact / would_activate are never
    # trusted as authority) onto the baseline registry, then diffs the two. The
    # on-disk artifact remains a SEPARATE observer; the evidence packet switch above
    # recomputes its own fresh in-memory approvals-inclusive registry.
    _write_approval_registry_dual_read_report_only(strategy_settings=handoff_strategy_settings)

    # Report-only layer 0c5 (R2G-5c-0): approval-registry switch-READINESS gate. The
    # on-disk artifact is still diagnostic/write-only; the actual 5c-2 switch above
    # recomputes readiness from fresh in-memory YAML-derived objects.
    _write_approval_registry_switch_readiness_report_only(strategy_settings=handoff_strategy_settings)

    # Report-only layer 0c6 (R2G-5c-1): support_signals dual-ground DRY-RUN diff.
    # Compares support_signals grounding under the embedded registry vs a freshly
    # compiled approvals-inclusive dry-run view. The artifact itself remains
    # write-only and cannot affect allowed_actions / add NEW_BUY / ORDER_COMPILATION.
    _write_support_signals_dual_ground_diff_report_only(strategy_settings=handoff_strategy_settings)

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


def _write_evidence_packet_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> None:
    """Build and write the deterministic evidence packet defensively (R2B).

    Report-only: never raises into the Step 1 parse flow, never gates the
    pipeline, and never feeds the degraded-mode decision. Uses only operator
    inputs + last-good metadata (no LLM, no parsed payload). A missing portfolio
    snapshot becomes an explicit DATA_GAP inside the packet rather than a crash.
    """
    try:
        snapshot_path = current_inputs_dir() / "portfolio_snapshot.txt"
        research_anchors_path = current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        try:
            snapshot_text: str | None = load_portfolio_snapshot_text()
        except Exception:  # noqa: BLE001 - missing snapshot -> DATA_GAP, not crash
            snapshot_text = None
        last_good = read_last_good_research_handoff(step1_state_dir())
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        write_evidence_packet(
            output_path=step1_evidence_packet_path(),
            strategy_settings=strategy_settings,
            portfolio_snapshot_text=snapshot_text,
            portfolio_snapshot_path=snapshot_path,
            last_good_available=last_good.available,
            last_good_metadata=last_good.metadata,
            now_date=settings_as_of,
            source_artifacts={
                "strategy_settings": str(current_inputs_dir() / "strategy_settings.yaml"),
                "portfolio_snapshot": str(snapshot_path),
                "last_good_metadata": str(last_good_research_handoff_metadata_path(step1_state_dir())),
                "research_anchors": str(research_anchors_path),
                "research_anchor_approvals": str(approvals_path),
            },
            research_anchors_path=research_anchors_path,
            research_anchor_approvals_path=approvals_path,
        )
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        # Best-effort only: do not mask or alter existing Step 1 behavior.
        pass


def _write_active_research_anchor_registry_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> None:
    """Compile + write the R2G-1 active anchor registry defensively (report-only).

    Wraps the existing research-anchors validator (``research_anchors.yaml``) into
    a deterministic ``active_research_anchor_registry.json``. Additive only:
    NOTHING consumes this artifact in R2G-1 (not support_signals, not the compiler,
    not the actionable preview/candidate/eligibility, not availability, not gates,
    not Step 2/3/4, not weekly). It never raises into the Step 1 parse flow,
    never gates the pipeline, never touches ``evidence_packet.research_anchors``,
    and adds no permission / state / action.
    """
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
) -> None:
    """Validate the operator-approval manifest defensively (R2G-5a, report-only).

    Reads ``inputs/current/research_anchor_approvals.yaml`` and writes
    ``research_anchor_approvals_validation.json``. Strictly inert: it activates NO
    anchor, is consumed by NOTHING (not support_signals, not the active registry,
    not the compiler, not the actionable preview/candidate/eligibility, not
    availability, not gates, not Step 2/3/4, not weekly, not broker/live), is never
    added to promoted_source_artifacts / allowed_actions / any gate, and adds no
    permission / state / action. A missing manifest yields a valid, empty report.
    Any error is swallowed so Step 1 parse is never affected.
    """
    try:
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        allowed_universe = _allowed_buy_universe_for_anchor_registry(strategy_settings)
        write_research_anchor_approvals_validation(
            output_path=step1_research_anchor_approvals_validation_path(),
            manifest_path=approvals_path,
            allowed_universe=allowed_universe,
            today=settings_as_of,
            as_of_date=settings_as_of if isinstance(settings_as_of, str) else None,
        )
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        pass


def _write_research_anchor_revocations_validation_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> None:
    """Validate the operator-revocation manifest defensively (R2G-5d-0, report-only).

    Reads the optional ``revocations:`` section of
    ``inputs/current/research_anchor_approvals.yaml`` and writes
    ``research_anchor_revocations_validation.json``. Strictly inert: it APPLIES no
    revocation, does not change the approvals-inclusive registry compiler,
    ``support_signals``, the embedded ``evidence_packet`` registry selection, or
    readiness; is consumed by NOTHING (not support_signals, not any registry, not
    availability, not gates, not Step 2/3/4, not weekly, not broker/live), is never
    added to promoted_source_artifacts / allowed_actions / any gate, and adds no
    permission / state / action. Unknown target fails closed (mandatory R2G-5d-0
    amendment). A missing manifest yields a valid, empty report. Any error is
    swallowed so Step 1 parse is never affected.
    """
    try:
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        allowed_universe = _allowed_buy_universe_for_anchor_registry(strategy_settings)
        write_research_anchor_revocations_validation(
            output_path=step1_research_anchor_revocations_validation_path(),
            manifest_path=approvals_path,
            allowed_universe=allowed_universe,
            today=settings_as_of,
            as_of_date=settings_as_of if isinstance(settings_as_of, str) else None,
        )
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        pass


def _write_approval_registry_dual_read_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> None:
    """Compile + write the R2G-5b approvals-inclusive registry + dual-read diff (report-only).

    Recomputes the baseline registry (identical to what support_signals' embedded
    registry uses) and re-validates approvals directly from
    ``research_anchor_approvals.yaml`` (never reading the R2G-5a artifact or its
    would_activate flag as authority), overlays approved anchors onto a SEPARATE
    approvals-inclusive registry, and writes the two report-only artifacts.
    Strictly inert: neither artifact is embedded in the evidence packet, added to
    support_signals input / promoted_source_artifacts / active handoff /
    allowed_actions / any gate / Step 2/3/4 input; both are consumed by NOTHING and
    add no permission / state / action. Any error is swallowed so Step 1 parse is
    never affected.
    """
    try:
        anchors_path = current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        allowed_universe = _allowed_buy_universe_for_anchor_registry(strategy_settings)

        # Baseline: the exact same compile support_signals' embedded registry uses.
        baseline = compile_active_research_anchor_registry(
            anchors_path=anchors_path,
            allowed_universe=allowed_universe,
            today=settings_as_of,
        )
        # Approvals: recomputed directly from YAML (not read from the R2G-5a artifact).
        approvals_validation = validate_research_anchor_approvals(
            manifest_path=approvals_path,
            allowed_universe=allowed_universe,
            today=settings_as_of,
        )
        with_approvals = build_active_research_anchor_registry_with_approvals(
            baseline=baseline, approvals_validation=approvals_validation
        )
        with_approvals_path = step1_active_research_anchor_registry_with_approvals_path()
        write_json(with_approvals_path, with_approvals)

        diff = build_approval_registry_dual_read_diff(
            baseline_registry=baseline,
            approvals_registry=with_approvals,
            baseline_registry_path=str(step1_active_research_anchor_registry_path()),
            approvals_registry_path=str(with_approvals_path),
        )
        write_json(step1_approval_registry_dual_read_diff_path(), diff)
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        pass


def _write_approval_registry_switch_readiness_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> None:
    """Compile + write the R2G-5c-0 switch-readiness gate (report-only).

    Recomputes the baseline registry, approvals-inclusive registry, and dual-read
    diff directly from the current ``research_anchors.yaml`` /
    ``research_anchor_approvals.yaml`` bytes (never reading the R2G-5a validation
    artifact or trusting its would_activate flag), then evaluates whether a FUTURE
    switch would be safe. Strictly inert: it switches NO consumer, does not change
    ``evidence_packet.active_anchor_registry`` or the baseline registry
    support_signals consumes, is consumed by NOTHING, is never added to
    promoted_source_artifacts / allowed_actions / any gate / Step 2/3/4 input, and
    adds no permission / state / action. Any error is swallowed so Step 1 parse is
    never affected.
    """
    try:
        anchors_path = current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME
        approvals_path = current_inputs_dir() / RESEARCH_ANCHOR_APPROVALS_INPUT_FILENAME
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        allowed_universe = _allowed_buy_universe_for_anchor_registry(strategy_settings)
        write_approval_registry_switch_readiness(
            output_path=step1_approval_registry_switch_readiness_path(),
            anchors_path=anchors_path,
            approvals_path=approvals_path,
            allowed_universe=allowed_universe,
            today=settings_as_of,
        )
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        pass


def _write_support_signals_dual_ground_diff_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
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
        write_support_signals_dual_ground_diff(
            output_path=step1_support_signals_dual_ground_diff_path(),
            evidence_packet=evidence_packet if isinstance(evidence_packet, Mapping) else None,
            analyst_memo=analyst_memo if isinstance(analyst_memo, Mapping) else None,
            compilation_mode=compilation_mode,
            anchors_path=anchors_path,
            approvals_path=approvals_path,
            allowed_universe=allowed_universe,
            today=settings_as_of,
        )
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        pass


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
) -> dict[str, Any]:
    """Ensure the deterministic evidence packet is fresh on disk, then return it.

    Used by the analyst-memo render/parse (R2C). Falls back to an in-memory build
    so rendering/parsing still works even if the disk write/read fails.
    """
    _write_evidence_packet_report_only(strategy_settings=strategy_settings)
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
        packet = _load_or_build_evidence_packet(strategy_settings=strategy_settings)
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
) -> dict[str, Any]:
    """Run the analyst-memo parse defensively as a report-only layer (R2C).

    Step 1 parse must never fail because of this observer, and the memo must
    never change the degraded-mode decision or any allowed action. A missing raw
    memo is simply skipped; any error is swallowed.
    """
    absent = {"present": False, "valid": False, "validation_path": "", "memo_path": ""}
    try:
        result = _run_analyst_memo_parse(strategy_settings=strategy_settings)
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
) -> dict[str, str]:
    """Compile + validate + write the three report-only R2D artifacts."""
    packet = _load_or_build_evidence_packet(strategy_settings=strategy_settings)
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
) -> dict[str, Any]:
    """Run the deterministic handoff compiler defensively as a report-only layer (R2D).

    Step 1 parse must never fail because of this observer, and the compiled
    candidate must never change the degraded-mode decision or any allowed action
    (it is not fed into the availability evaluator). Any error is swallowed.
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
        return _run_compile_research_handoff(strategy_settings=strategy_settings)
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
):
    """Best-effort PR C artifacts for no-output / parse-failure Step 1 runs.

    This preserves the original parser error behavior: callers still raise
    after this report-only observer writes conservative degraded-mode artifacts.
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
