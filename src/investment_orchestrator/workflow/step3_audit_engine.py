"""Manual Step 3 workflow: render prompt and ingest audit artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import hashlib
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
from investment_orchestrator.parsers.extract_audit_and_audited_packet import (
    extract_audit_and_audited_packet,
)
from investment_orchestrator.research.promoted_step3_audit_dry_run import (
    FUTURE_STEP3_SOURCE_ARTIFACT,
    verify_promoted_handoff_for_step3_audit,
)
from investment_orchestrator.state.research_availability import (
    PROMOTED_RESEARCH_AUDIT_ACTION,
    PROMOTED_RESEARCH_DECISION_ACTION,
    STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    MODE_PROMOTED_STEP2_DECISION_ONLY,
    MODE_STRICT_FRESH_ACTIONABLE,
    NO_TRADE_PENDING_FINAL_GATES,
    PROMOTED_SOURCE,
    ResearchDegradedModeGateResult,
    load_and_evaluate_step2_research_gate,
)
from investment_orchestrator.state.upstream_artifact_guard import (
    UpstreamArtifactGuardError,
    enforce_upstream_artifact_guard,
    resolve_upstream_permission_metadata,
)
from investment_orchestrator.validators.strategy_settings import parse_strategy_settings_text
from investment_orchestrator.workflow.step1_research import (
    refresh_promoted_step4_readiness_after_step3,
    step1_active_research_handoff_source_path,
    step1_effective_research_handoff_path,
    step1_effective_research_handoff_validation_path,
    step1_research_degraded_mode_decision_path,
    step1_research_output_path,
)
from investment_orchestrator.workflow.step2_decision_builder import (
    step2_blocked_by_research_gate_path,
    step2_decision_packet_path,
    step2_prompt_path,
    step2_promoted_decision_only_path,
    step2_raw_output_path,
    step2_template2_output_path,
)


STEP3_DIRNAME = "step3_audit_engine"
PROMPT_FILENAME = "prompt.txt"
RAW_OUTPUT_FILENAME = "raw_output.txt"
TEMPLATE3_AUDIT_FILENAME = "template3_audit.txt"
TEMPLATE2_PATCH_FILENAME = "template2_patch.txt"
AUDITED_DECISION_PACKET_FILENAME = "audited_decision_packet.json"
STEP3_BLOCKED_BY_UPSTREAM_GATE_FILENAME = "step3_blocked_by_upstream_gate.json"
STEP3_BLOCKED_BY_PROMOTED_DECISION_ONLY_GATE_FILENAME = (
    "step3_blocked_by_promoted_decision_only_gate.json"
)
STEP3_PROMOTED_AUDIT_ONLY_FILENAME = "step3_promoted_audit_only.json"
STEP3_PROMOTED_AUDIT_ONLY_DOWNSTREAM_BLOCK_FILENAME = (
    "step3_promoted_audit_only_downstream_block.json"
)
STEP3_PROMOTED_AUDIT_ONLY_SCHEMA_VERSION = "step3_promoted_audit_only_v1"
STEP3_PROMOTED_AUDIT_ONLY_DOWNSTREAM_BLOCK_SCHEMA_VERSION = (
    "step3_promoted_audit_only_downstream_block_v1"
)
MODE_PROMOTED_STEP3_AUDIT_ONLY = "promoted_step3_audit_only"
STEP3_RESEARCH_ADMISSION_DENIED_REASON = "step3_research_admission_denied"
PROMOTED_DECISION_ONLY_NO_AUDIT_REASON = "promoted_step2_decision_only_no_audit_permission"
PROMOTED_STEP3_AUDIT_ONLY_VERIFICATION_FAILED_REASON = (
    "promoted_step3_audit_only_verification_failed"
)
PROMOTED_STEP3_AUDIT_ONLY_NO_ORDER_COMPILATION_REASON = (
    "promoted_step3_audit_only_no_order_compilation_permission"
)
PROMOTED_STEP3_AUDIT_ONLY_ALLOWED_ACTIONS = (
    "HOLD",
    "NO_TRADE",
    PROMOTED_RESEARCH_DECISION_ACTION,
    PROMOTED_RESEARCH_AUDIT_ACTION,
)

STRATEGY_SETTINGS_BLOCK_RE = re.compile(
    r"STRATEGY_SETTINGS_START\s*\n.*?\nSTRATEGY_SETTINGS_END",
    re.DOTALL,
)


def current_inputs_dir() -> Path:
    """Return the operator-maintained current input directory."""
    return repo_root() / "inputs" / "current"


def step3_artifact_dir() -> Path:
    """Return the Step 3 artifact directory."""
    return ensure_dir(repo_root() / "artifacts" / "current" / STEP3_DIRNAME)


def step3_prompt_path() -> Path:
    """Return the rendered Step 3 prompt path."""
    return step3_artifact_dir() / PROMPT_FILENAME


def step3_raw_output_path() -> Path:
    """Return the manual Step 3 raw output path."""
    return step3_artifact_dir() / RAW_OUTPUT_FILENAME


def step3_template3_audit_path() -> Path:
    """Return the extracted Template 3 audit text path."""
    return step3_artifact_dir() / TEMPLATE3_AUDIT_FILENAME


def step3_template2_patch_path() -> Path:
    """Return the extracted optional Template 2 patch text path."""
    return step3_artifact_dir() / TEMPLATE2_PATCH_FILENAME


def step3_audited_decision_packet_path() -> Path:
    """Return the extracted audited decision packet path."""
    return step3_artifact_dir() / AUDITED_DECISION_PACKET_FILENAME


def step3_blocked_by_upstream_gate_path() -> Path:
    """Return the deterministic Step 3 upstream-gate block artifact path."""
    return step3_artifact_dir() / STEP3_BLOCKED_BY_UPSTREAM_GATE_FILENAME


def step3_blocked_by_promoted_decision_only_gate_path() -> Path:
    """Return the deterministic promoted decision-only Step 3 block artifact path (R2E.5b-6c)."""
    return step3_artifact_dir() / STEP3_BLOCKED_BY_PROMOTED_DECISION_ONLY_GATE_FILENAME


def step3_promoted_audit_only_path() -> Path:
    """Return the deterministic promoted Step 3 audit-only marker path (R2E.5b-6f)."""
    return step3_artifact_dir() / STEP3_PROMOTED_AUDIT_ONLY_FILENAME


def step3_promoted_audit_only_downstream_block_path() -> Path:
    """Return the deterministic block that prevents Step 4 from consuming audit-only output."""
    return step3_artifact_dir() / STEP3_PROMOTED_AUDIT_ONLY_DOWNSTREAM_BLOCK_FILENAME


def enforce_step3_upstream_guard() -> dict[str, Any] | None:
    """Resolve and enforce current Step 3 admission before any Step 3 work.

    This is the single shared admission boundary for both ``run_step3 render``
    and ``run_step3 parse``; neither duplicates normal state policy. Ordering is
    authoritative, and the current permission decision is read and interpreted
    exactly once per invocation:

    1. exact promoted Step 3 audit-only recognition + provenance verification
       (R2E.5b-6f). On success the resolved promoted context is returned and the
       normal policy below is deliberately not consulted.
    2. R2E.5b-6c: the promoted Step 2 decision-only mode permits Step 2 ONLY.
       When the research gate resolves to that mode, Step 3 deterministically
       blocks (``promoted_step2_decision_only_no_audit_permission``) regardless
       of which Step 2 artifacts exist — a decision-only packet must never be
       audited into order readiness without a future explicit audit-permission
       PR.
    3. current normal research admission: the authoritative Step 2 research gate
       must *currently* resolve to the STRICT_FRESH actionable mode. Every other
       current state — including a missing, malformed, or otherwise gate-denied
       permission decision — fails closed here, before any residual Step 2
       content is read and before any Step 3 artifact is produced.
    4. the existing generic upstream artifact guard, which keeps its own
       ownership: upstream block presence and required artifact presence.

    Residual Step 2 artifacts never confer Step 3 authority by presence alone.
    Admission is a property of the *current* permission decision, not of files
    left on disk by an earlier admitted run.
    """
    promoted_context = _load_promoted_step3_context_if_state_or_block()
    if promoted_context is not None:
        return promoted_context

    gate = load_and_evaluate_step2_research_gate(step1_research_degraded_mode_decision_path())
    _enforce_step3_promoted_decision_only_block(gate)
    _enforce_step3_normal_research_admission(gate)
    enforce_upstream_artifact_guard(
        blocked_artifact_path=step3_blocked_by_upstream_gate_path(),
        upstream_blocked_artifacts=[step2_blocked_by_research_gate_path()],
        required_artifacts=[
            step2_prompt_path(),
            step2_raw_output_path(),
            step2_template2_output_path(),
            step2_decision_packet_path(),
        ],
        repo_root_path=repo_root(),
        permission_fallback_artifacts=[step1_research_degraded_mode_decision_path()],
    )
    return None


def _enforce_step3_normal_research_admission(gate: ResearchDegradedModeGateResult) -> None:
    """Require the current gate result that authorizes normal Step 3.

    Normal Step 3 is admitted only by the existing authoritative research-gate
    result for normal actionable work: literal ``STRICT_FRESH`` resolved to
    ``MODE_STRICT_FRESH_ACTIONABLE`` with ``step3_allowed``. The gate already
    owns the exact state, the permission semantics, manual-review validity, the
    mode, and the NEW_BUY / ORDER_COMPILATION policy, so nothing is
    reinterpreted here: there is no local state whitelist and no inference from
    artifact presence.

    Denial writes the existing normal Step 3 block artifact and raises. No
    preexisting Step 3 prompt/raw/audit/packet file is written, truncated, or
    removed — the fresh block artifact is what makes those stale bytes unusable
    downstream.
    """
    if gate.allowed and gate.mode == MODE_STRICT_FRESH_ACTIONABLE and gate.step3_allowed:
        return

    permission, read_errors = resolve_upstream_permission_metadata(
        blocked_by_artifact=None,
        fallback_artifacts=[
            step2_blocked_by_research_gate_path(),
            step1_research_degraded_mode_decision_path(),
        ],
        repo_root_path=repo_root(),
    )
    blocked_artifact_path = step3_blocked_by_upstream_gate_path()
    write_json(
        blocked_artifact_path,
        {
            "blocked": True,
            "reason": STEP3_RESEARCH_ADMISSION_DENIED_REASON,
            "state": gate.state,
            "mode": gate.mode,
            "allowed_actions": list(gate.allowed_actions),
            "blocked_actions": list(gate.blocked_actions),
            "order_compilation_allowed": gate.order_compilation_allowed,
            "new_buy_permission": gate.new_buy_permission,
            "step3_allowed": gate.step3_allowed,
            "step4_allowed": gate.step4_allowed,
            "manual_review_required": gate.manual_review_required,
            "blocker_reasons": list(gate.blocker_reasons),
            "malformed_reasons": list(gate.malformed_reasons),
            "source_artifact": _display_path(step1_research_degraded_mode_decision_path()),
            "recommended_result": "NO_TRADE",
            "report_only": False,
            "upstream_permission": permission,
            "upstream_permission_read_errors": read_errors,
        },
    )
    raise UpstreamArtifactGuardError(
        "Step 3 blocked by current research admission: "
        f"reason={STEP3_RESEARCH_ADMISSION_DENIED_REASON}; state={gate.state}; "
        f"mode={gate.mode}; manual_review_required={gate.manual_review_required}; "
        f"blocked_artifact={_display_path(blocked_artifact_path)}"
    )


def _enforce_step3_promoted_decision_only_block(gate: ResearchDegradedModeGateResult) -> None:
    """Block Step 3 when Step 2 ran (or would run) in promoted decision-only mode."""
    if not (gate.allowed and gate.mode == MODE_PROMOTED_STEP2_DECISION_ONLY):
        return
    blocked_artifact_path = step3_blocked_by_promoted_decision_only_gate_path()
    write_json(
        blocked_artifact_path,
        {
            "blocked": True,
            "reason": PROMOTED_DECISION_ONLY_NO_AUDIT_REASON,
            "state": gate.state,
            "mode": gate.mode,
            "allowed_actions": list(gate.allowed_actions),
            "blocked_actions": list(gate.blocked_actions),
            "order_compilation_allowed": False,
            "new_buy_permission": False,
            "step3_allowed": False,
            "step4_allowed": False,
            "manual_review_required": gate.manual_review_required,
            "blocker_reasons": [
                "promoted Step 2 decision-only permits Step 2 render/parse only; "
                "Step 3 audit requires a future explicit audit-permission PR."
            ],
            "recommended_result": NO_TRADE_PENDING_FINAL_GATES,
            "source_artifact": _display_path(step1_research_degraded_mode_decision_path()),
            "report_only": False,
        },
    )
    raise UpstreamArtifactGuardError(
        "Step 3 blocked by promoted decision-only gate: "
        f"reason={PROMOTED_DECISION_ONLY_NO_AUDIT_REASON}; "
        f"blocked_artifact={_display_path(blocked_artifact_path)}"
    )


def _load_promoted_step3_context_if_state_or_block() -> dict[str, Any] | None:
    """Return promoted Step 3 context when the Step 1 permission is audit-only.

    Any malformed or stale audit-only posture fails closed with the regular Step
    3 upstream block artifact. Non-promoted states return ``None`` so the legacy
    Step 3 guard/path remains unchanged.
    """
    decision = _read_json_object_or_none(step1_research_degraded_mode_decision_path())
    if not isinstance(decision, dict):
        return None
    if decision.get("state") != STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY:
        return None

    pointer = _read_json_object_or_none(step1_active_research_handoff_source_path())
    effective = _read_json_object_or_none(step1_effective_research_handoff_path())
    validation = _read_json_object_or_none(step1_effective_research_handoff_validation_path())
    marker = _read_json_object_or_none(step2_promoted_decision_only_path())
    packet = _read_json_object_or_none(step2_decision_packet_path())
    source_artifacts = {
        "research_degraded_mode_decision": _display_path(
            step1_research_degraded_mode_decision_path()
        ),
        "active_research_handoff_source": _display_path(
            step1_active_research_handoff_source_path()
        ),
        "research_handoff_candidate_effective": _display_path(
            step1_effective_research_handoff_path()
        ),
        "research_handoff_candidate_effective_validation": _display_path(
            step1_effective_research_handoff_validation_path()
        ),
        "step2_promoted_decision_only": _display_path(step2_promoted_decision_only_path()),
        "step2_decision_packet": _display_path(step2_decision_packet_path()),
    }
    verification = verify_promoted_handoff_for_step3_audit(
        active_pointer=pointer,
        effective_handoff=effective,
        effective_validation=validation,
        step2_promoted_marker=marker,
        step2_decision_packet=packet,
        today=_settings_as_of_date_or_none(),
        source_artifacts=source_artifacts,
    )

    blockers = _promoted_step3_permission_blockers(decision)
    blockers.extend(list(verification.get("verification_blockers") or []))
    if verification.get("valid_for_promoted_step3_audit") is not True and not blockers:
        blockers.append("promoted_step3_audit_verification_invalid")

    if blockers:
        _write_promoted_step3_blocked_artifact(
            decision=decision,
            verification=verification,
            blockers=blockers,
            source_artifacts=source_artifacts,
        )
        raise UpstreamArtifactGuardError(
            "Step 3 blocked by promoted audit-only verification: "
            f"blockers={blockers}; "
            f"blocked_artifact={_display_path(step3_blocked_by_upstream_gate_path())}"
        )

    return {
        "decision": decision,
        "pointer": pointer,
        "effective_handoff": effective,
        "effective_validation": validation,
        "step2_marker": marker,
        "step2_decision_packet": packet,
        "verification": verification,
        "source_artifacts": source_artifacts,
        "active_pointer_sha256": _sha256_of(pointer),
        "effective_handoff_sha256": verification.get("effective_handoff_sha256"),
        "promotion_status": verification.get("promotion_status"),
        "promotion_expires_at": verification.get("promotion_expires_at"),
    }


def _promoted_step3_permission_blockers(decision: Mapping[str, Any]) -> list[str]:
    allowed_actions = _string_items(decision.get("allowed_actions"))
    blockers: list[str] = []
    if allowed_actions != list(PROMOTED_STEP3_AUDIT_ONLY_ALLOWED_ACTIONS):
        blockers.append("promoted_step3_audit_only_allowed_actions_invalid")
    if "NEW_BUY" in allowed_actions:
        blockers.append("promoted_step3_audit_only_widened_new_buy")
    if "ORDER_COMPILATION" in allowed_actions:
        blockers.append("promoted_step3_audit_only_widened_order_compilation")
    if decision.get("source") != PROMOTED_SOURCE:
        blockers.append("promoted_step3_audit_only_source_invalid")
    if decision.get("promoted_step2_decision_only") is not True:
        blockers.append("promoted_step3_audit_only_missing_step2_decision_permission")
    if decision.get("promoted_step3_audit_only") is not True:
        blockers.append("promoted_step3_audit_only_marker_missing")
    if decision.get("manual_review_required") is True:
        blockers.append("promoted_step3_audit_only_manual_review_required")
    if decision.get("order_compilation_allowed") is not False:
        blockers.append("promoted_step3_audit_only_order_compilation_flag_invalid")
    if decision.get("new_buy_permission") is not False:
        blockers.append("promoted_step3_audit_only_new_buy_flag_invalid")
    return blockers


def _write_promoted_step3_blocked_artifact(
    *,
    decision: Mapping[str, Any],
    verification: Mapping[str, Any],
    blockers: list[str],
    source_artifacts: Mapping[str, str],
) -> None:
    write_json(
        step3_blocked_by_upstream_gate_path(),
        {
            "blocked": True,
            "reason": PROMOTED_STEP3_AUDIT_ONLY_VERIFICATION_FAILED_REASON,
            "state": decision.get("state"),
            "mode": MODE_PROMOTED_STEP3_AUDIT_ONLY,
            "allowed_actions": _string_items(decision.get("allowed_actions")),
            "blocked_actions": _string_items(decision.get("blocked_actions")),
            "audit_only": True,
            "permission_effect": "step3_audit_only",
            "not_authorization": True,
            "not_execution_authorization": True,
            "order_compilation_allowed": False,
            "new_buy_permission": False,
            "step4_allowed": False,
            "final_execution_allowed": False,
            "broker_automation_allowed": False,
            "manual_review_required": decision.get("manual_review_required") is True,
            "blocker_reasons": list(dict.fromkeys(blockers)),
            "verification_blockers": list(verification.get("verification_blockers") or []),
            "source_artifact": _display_path(step1_research_degraded_mode_decision_path()),
            "source_artifacts": dict(source_artifacts),
            "recommended_result": NO_TRADE_PENDING_FINAL_GATES,
            "report_only": False,
        },
    )


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(repo_root()))
    except ValueError:
        return str(path)


def _require_non_empty_text(path: Path, *, label: str) -> str:
    """Read a required text input and fail clearly when it is missing or empty."""
    try:
        text = read_text(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Missing required {label}: {path}") from exc

    if not text.strip():
        raise ValueError(f"Required {label} is empty: {path}")
    return text


def load_strategy_settings_text() -> str:
    """Read the operator-maintained strategy settings YAML exactly as stored on disk."""
    return _require_non_empty_text(
        current_inputs_dir() / "strategy_settings.yaml",
        label="strategy settings YAML input",
    )


def load_portfolio_snapshot_text() -> str:
    """Read the operator-maintained portfolio snapshot exactly as stored on disk."""
    return _require_non_empty_text(
        current_inputs_dir() / "portfolio_snapshot.txt",
        label="portfolio snapshot input",
    )


def load_research_output_text() -> str:
    """Read the parsed Step 1 research artifact exactly as stored on disk."""
    return _require_non_empty_text(
        step1_research_output_path(),
        label="Step 1 research_output.json artifact",
    )


def load_template2_output_text() -> str:
    """Read the parsed Step 2 template2 output artifact."""
    return _require_non_empty_text(
        step2_template2_output_path(),
        label="Step 2 template2_output.txt artifact",
    )


def load_decision_packet_text() -> str:
    """Read the parsed Step 2 decision packet artifact."""
    return _require_non_empty_text(
        step2_decision_packet_path(),
        label="Step 2 decision_packet.json artifact",
    )


def inject_strategy_settings_block(prompt_text: str, strategy_settings_text: str) -> str:
    """Replace the in-template Strategy Settings block with the operator-maintained YAML."""
    replacement = f"STRATEGY_SETTINGS_START\n{strategy_settings_text}\nSTRATEGY_SETTINGS_END"
    if STRATEGY_SETTINGS_BLOCK_RE.search(prompt_text):
        return STRATEGY_SETTINGS_BLOCK_RE.sub(replacement, prompt_text, count=1)
    return f"{prompt_text.rstrip()}\n\n{replacement}\n"


def build_step3_prompt_text() -> str:
    """Render the Step 3 prompt from formal artifacts plus current operator inputs."""
    prompt_template = read_text(require_prompt_path("strategy_b_audit_engine.txt"))
    prompt_template = inject_strategy_settings_block(prompt_template, load_strategy_settings_text())

    rendered = render_prompt(
        prompt_template,
        {
            "research_json": load_research_output_text(),
            "portfolio_snapshot": load_portfolio_snapshot_text(),
            "template2_output": load_template2_output_text(),
            "decision_packet": load_decision_packet_text(),
        },
    )
    return rendered.rstrip() + "\n"


def _build_promoted_step3_prompt_text(promoted_context: dict[str, Any]) -> str:
    """Render promoted Step 3 audit-only from the effective handoff, never raw Deep Research."""
    prompt_template = read_text(require_prompt_path("strategy_b_audit_engine.txt"))
    prompt_template = inject_strategy_settings_block(prompt_template, load_strategy_settings_text())

    rendered = render_prompt(
        prompt_template,
        {
            "research_json": json.dumps(
                promoted_context["effective_handoff"], ensure_ascii=False, indent=2
            ),
            "portfolio_snapshot": load_portfolio_snapshot_text(),
            "template2_output": load_template2_output_text(),
            "decision_packet": json.dumps(
                promoted_context["step2_decision_packet"], ensure_ascii=False, indent=2
            ),
        },
    )
    return rendered.rstrip() + "\n" + _promoted_step3_source_metadata_block(promoted_context)


def _promoted_step3_source_metadata_block(promoted_context: dict[str, Any]) -> str:
    return (
        "\n────────────────────────────────────────\n"
        "【PROMOTED RESEARCH SOURCE — Step 3 audit-only (R2E.5b-6f)】\n"
        f"mode: {MODE_PROMOTED_STEP3_AUDIT_ONLY}\n"
        f"state: {STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY}\n"
        f"action: {PROMOTED_RESEARCH_AUDIT_ACTION}\n"
        f"source: {PROMOTED_SOURCE}\n"
        f"future_step3_source_artifact: {FUTURE_STEP3_SOURCE_ARTIFACT}\n"
        f"promotion_status: {promoted_context['promotion_status']}\n"
        f"active_pointer_sha256: {promoted_context['active_pointer_sha256']}\n"
        f"effective_handoff_sha256: {promoted_context['effective_handoff_sha256']}\n"
        f"promotion_expires_at: {promoted_context['promotion_expires_at']}\n"
        "NOTE: the research input above is research_handoff_candidate_effective.json, "
        "NOT raw Deep Research output and NOT research_output.json.\n"
        "NOTE: this is a manual Step 3 audit-only prompt. It is NOT order "
        "authorization and NOT execution authorization.\n"
        "NOTE: NEW_BUY and ORDER_COMPILATION are NOT allowed. Step 4 order "
        "compilation, final execution, broker automation, and live order "
        "submission remain blocked.\n"
    )


def render_step3_prompt() -> dict[str, str]:
    """Write the rendered Step 3 prompt and prepare the manual output artifact.

    Admission is resolved once by the shared Step 3 guard before any Step 2
    residual content is read and before any Step 3 artifact is written.
    """
    promoted_context = enforce_step3_upstream_guard()

    artifact_dir = step3_artifact_dir()
    prompt_output_path = step3_prompt_path()
    raw_output_path = step3_raw_output_path()

    prompt_text = (
        _build_promoted_step3_prompt_text(promoted_context)
        if promoted_context is not None
        else build_step3_prompt_text()
    )
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
    }
    if promoted_context is not None:
        marker_path, block_path = _write_promoted_step3_audit_only_artifacts(promoted_context)
        result.update(
            {
                "mode": MODE_PROMOTED_STEP3_AUDIT_ONLY,
                "step3_promoted_audit_only_path": str(marker_path),
                "step3_promoted_audit_only_downstream_block_path": str(block_path),
                "order_compilation_allowed": "False",
                "new_buy_permission": "False",
                "step4_allowed": "False",
            }
        )
    return result


def parse_step3_output() -> dict[str, str]:
    """Parse and validate the manual Step 3 output artifacts.

    Admission is resolved once by the shared Step 3 guard — the same boundary
    ``render`` passes — before the Step 3 raw output is read, before any audit
    output is constructed, and before any Step 3 artifact is persisted.
    """
    promoted_context = enforce_step3_upstream_guard()
    if promoted_context is not None:
        audit_text = _require_non_empty_text(
            step3_raw_output_path(),
            label="Step 3 promoted audit-only raw output",
        )
        write_text(step3_template3_audit_path(), audit_text.rstrip() + "\n")
        write_text(step3_template2_patch_path(), "")
        marker_path, block_path = _write_promoted_step3_audit_only_artifacts(promoted_context)
        # R2E.5b-7b/7c: regenerate the report-only Step 4 readiness diagnostics
        # and the rowless final-safety preflight now that the promoted marker /
        # downstream block / audit text exist. This grants nothing: no state
        # change, no Step 4 permission, no orders, no gate change.
        step4_readiness = refresh_promoted_step4_readiness_after_step3()
        return {
            "mode": MODE_PROMOTED_STEP3_AUDIT_ONLY,
            "template3_audit_path": str(step3_template3_audit_path()),
            "template2_patch_path": str(step3_template2_patch_path()),
            "step3_promoted_audit_only_path": str(marker_path),
            "step3_promoted_audit_only_downstream_block_path": str(block_path),
            "template3_audit_chars": str(len(audit_text)),
            "template2_patch_chars": "0",
            "order_compilation_allowed": "False",
            "new_buy_permission": "False",
            "step4_allowed": "False",
            "promoted_step4_readiness_verification_path": step4_readiness.get(
                "promoted_step4_readiness_verification_path", ""
            ),
            "promoted_step4_preview_gate_dry_run_path": step4_readiness.get(
                "promoted_step4_preview_gate_dry_run_path", ""
            ),
            "promoted_step4_preview_gate_dry_run_would_allow": step4_readiness.get(
                "promoted_step4_preview_gate_dry_run_would_allow", ""
            ),
            "promoted_final_safety_preflight_path": step4_readiness.get(
                "promoted_final_safety_preflight_path", ""
            ),
            "promoted_final_safety_preflight_passed": step4_readiness.get(
                "promoted_final_safety_preflight_passed", ""
            ),
        }

    template3_audit_text, template2_patch_text, audited_packet = extract_audit_and_audited_packet(
        raw_output_path=step3_raw_output_path(),
        template3_audit_path=step3_template3_audit_path(),
        template2_patch_path=step3_template2_patch_path(),
        audited_decision_packet_path=step3_audited_decision_packet_path(),
    )
    return {
        "template3_audit_path": str(step3_template3_audit_path()),
        "template2_patch_path": str(step3_template2_patch_path()),
        "audited_decision_packet_path": str(step3_audited_decision_packet_path()),
        "template3_audit_chars": str(len(template3_audit_text)),
        "template2_patch_chars": str(len(template2_patch_text)),
        "audit_passed": str(audited_packet.get("audit_passed", "")),
    }


def _write_promoted_step3_audit_only_artifacts(
    promoted_context: dict[str, Any],
) -> tuple[Path, Path]:
    """Write deterministic audit-only marker and Step 4 downstream block artifacts."""
    decision = promoted_context["decision"]
    verification = promoted_context["verification"]
    allowed_actions = list(PROMOTED_STEP3_AUDIT_ONLY_ALLOWED_ACTIONS)
    blocked_actions = _string_items(decision.get("blocked_actions"))
    source_artifacts = dict(promoted_context["source_artifacts"])
    source_artifacts["step3_promoted_audit_only"] = _display_path(step3_promoted_audit_only_path())
    common = {
        "is_llm_generated": False,
        "mode": MODE_PROMOTED_STEP3_AUDIT_ONLY,
        "state": STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY,
        "research_state": STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY,
        "allowed_actions": allowed_actions,
        "blocked_actions": blocked_actions,
        "audit_only": True,
        "permission_effect": "step3_audit_only",
        "not_authorization": True,
        "not_execution_authorization": True,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "step4_allowed": False,
        "final_execution_allowed": False,
        "broker_automation_allowed": False,
        "source": PROMOTED_SOURCE,
        "future_step3_source_artifact": FUTURE_STEP3_SOURCE_ARTIFACT,
        "raw_deep_research_source_used": False,
        "promotion_status": promoted_context["promotion_status"],
        "promotion_expires_at": promoted_context["promotion_expires_at"],
        "active_pointer_sha256": promoted_context["active_pointer_sha256"],
        "effective_handoff_sha256": promoted_context["effective_handoff_sha256"],
        "pointer_effective_handoff_sha256": verification.get("pointer_effective_handoff_sha256"),
        "step2_promoted_marker_sha256": verification.get("step2_promoted_marker_sha256"),
        "step2_decision_packet_sha256": verification.get("step2_decision_packet_sha256"),
        "source_artifacts": source_artifacts,
        "source_artifact_hashes": dict(verification.get("source_artifact_hashes") or {}),
        "reason": PROMOTED_STEP3_AUDIT_ONLY_NO_ORDER_COMPILATION_REASON,
        "recommended_result": NO_TRADE_PENDING_FINAL_GATES,
        "report_only": False,
    }
    marker_path = step3_promoted_audit_only_path()
    block_path = step3_promoted_audit_only_downstream_block_path()
    write_json(
        marker_path,
        {
            "schema_version": STEP3_PROMOTED_AUDIT_ONLY_SCHEMA_VERSION,
            **common,
        },
    )
    write_json(
        block_path,
        {
            "schema_version": STEP3_PROMOTED_AUDIT_ONLY_DOWNSTREAM_BLOCK_SCHEMA_VERSION,
            "blocked": True,
            **common,
        },
    )
    return marker_path, block_path


def _read_json_object_or_none(path: Path) -> dict[str, Any] | None:
    if not file_exists(path):
        return None
    try:
        payload = read_json(path)
    except Exception:  # noqa: BLE001 - fail closed: unreadable -> treated as absent
        return None
    return payload if isinstance(payload, dict) else None


def _settings_as_of_date_or_none() -> date | None:
    try:
        as_of = parse_strategy_settings_text(load_strategy_settings_text()).get("as_of")
    except Exception:  # noqa: BLE001 - verifier falls back to today() on None
        return None
    if isinstance(as_of, date):
        return as_of
    if not isinstance(as_of, str):
        return None
    try:
        return date.fromisoformat(as_of.strip())
    except ValueError:
        return None


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
