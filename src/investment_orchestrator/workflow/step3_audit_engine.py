"""Manual Step 3 workflow: render prompt and ingest audit artifacts."""

from __future__ import annotations

import re
from pathlib import Path

from investment_orchestrator.common.io import ensure_dir, file_exists, read_text, write_text
from investment_orchestrator.common.paths import repo_root, require_prompt_path
from investment_orchestrator.llm.manual_output import (
    ensure_manual_output_metadata_template,
    render_prompt,
    write_rendered_prompt,
)
from investment_orchestrator.common.io import write_json
from investment_orchestrator.parsers.extract_audit_and_audited_packet import (
    extract_audit_and_audited_packet,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    MODE_PROMOTED_STEP2_DECISION_ONLY,
    NO_TRADE_PENDING_FINAL_GATES,
    load_and_evaluate_step2_research_gate,
)
from investment_orchestrator.state.upstream_artifact_guard import (
    UpstreamArtifactGuardError,
    enforce_upstream_artifact_guard,
)
from investment_orchestrator.workflow.step1_research import (
    step1_research_degraded_mode_decision_path,
    step1_research_output_path,
)
from investment_orchestrator.workflow.step2_decision_builder import (
    step2_blocked_by_research_gate_path,
    step2_decision_packet_path,
    step2_prompt_path,
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
PROMOTED_DECISION_ONLY_NO_AUDIT_REASON = "promoted_step2_decision_only_no_audit_permission"

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


def enforce_step3_upstream_guard() -> None:
    """Fail closed before Step 3 consumes blocked or missing Step 2 artifacts.

    R2E.5b-6c: the promoted Step 2 decision-only mode permits Step 2 ONLY. When
    the research gate resolves to that mode, Step 3 deterministically blocks
    (``promoted_step2_decision_only_no_audit_permission``) regardless of which
    Step 2 artifacts exist — a decision-only packet must never be audited into
    order readiness without a future explicit audit-permission PR.
    """
    _enforce_step3_promoted_decision_only_block()
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


def _enforce_step3_promoted_decision_only_block() -> None:
    """Block Step 3 when Step 2 ran (or would run) in promoted decision-only mode."""
    gate = load_and_evaluate_step2_research_gate(step1_research_degraded_mode_decision_path())
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


def render_step3_prompt() -> dict[str, str]:
    """Write the rendered Step 3 prompt and prepare the manual output artifact."""
    enforce_step3_upstream_guard()

    artifact_dir = step3_artifact_dir()
    prompt_output_path = step3_prompt_path()
    raw_output_path = step3_raw_output_path()

    write_rendered_prompt(prompt_output_path, build_step3_prompt_text())
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
    }


def parse_step3_output() -> dict[str, str]:
    """Parse and validate the manual Step 3 output artifacts."""
    enforce_step3_upstream_guard()

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
