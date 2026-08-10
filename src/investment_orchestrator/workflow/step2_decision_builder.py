"""Manual Step 2 workflow: render prompt and ingest Template 2 + DECISION_PACKET.

R2E.5b-6c: when the research gate allows the promoted Step 2 decision-only mode
(``STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY`` +
``PROMOTED_RESEARCH_DECISION``), the prompt is rendered from the deterministic
promoted effective handoff (``research_handoff_candidate_effective.json``) —
never from the raw Deep Research output — after a fail-closed live re-run of the
R2E.5b-6a verifier over the active pointer / effective handoff / validation. A
deterministic ``step2_promoted_decision_only.json`` marker records that the run
is decision-only: ``order_compilation_allowed: false``, ``new_buy_permission:
false``, not execution authorization. The legacy STRICT_FRESH render path is
byte-for-byte unchanged.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
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
from investment_orchestrator.parsers.extract_template2_and_decision_packet import (
    extract_template2_and_decision_packet,
)
from investment_orchestrator.research.promoted_handoff_verifier import (
    verify_promoted_handoff_for_step2_decision,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    MODE_PROMOTED_STEP2_DECISION_ONLY,
    NO_TRADE_PENDING_FINAL_GATES,
    PROMOTED_SOURCE,
    ResearchDegradedModeGateError,
    ResearchDegradedModeGateResult,
    enforce_step2_research_gate,
)
from investment_orchestrator.validators.strategy_settings import parse_strategy_settings_text
from investment_orchestrator.workflow.step1_research import (
    refresh_promoted_step3_audit_only_permission_after_step2,
    step1_active_research_handoff_source_path,
    step1_effective_research_handoff_path,
    step1_effective_research_handoff_validation_path,
    step1_research_degraded_mode_decision_path,
    step1_research_output_path,
)


STEP2_DIRNAME = "step2_decision_builder"
PROMPT_FILENAME = "prompt.txt"
RAW_OUTPUT_FILENAME = "raw_output.txt"
TEMPLATE2_OUTPUT_FILENAME = "template2_output.txt"
DECISION_PACKET_FILENAME = "decision_packet.json"
STEP2_BLOCKED_BY_RESEARCH_GATE_FILENAME = "step2_blocked_by_research_gate.json"
STEP2_PROMOTED_DECISION_ONLY_FILENAME = "step2_promoted_decision_only.json"
STEP2_PROMOTED_DECISION_ONLY_SCHEMA_VERSION = "step2_promoted_decision_only_v1"
PROMOTED_VERIFICATION_FAILED_REASON = "promoted_step2_verification_failed"


def current_inputs_dir() -> Path:
    """Return the operator-maintained current input directory."""
    return repo_root() / "inputs" / "current"


def step2_artifact_dir() -> Path:
    """Return the Step 2 artifact directory."""
    return ensure_dir(repo_root() / "artifacts" / "current" / STEP2_DIRNAME)


def step2_prompt_path() -> Path:
    """Return the rendered Step 2 prompt path."""
    return step2_artifact_dir() / PROMPT_FILENAME


def step2_raw_output_path() -> Path:
    """Return the manual Step 2 raw output path."""
    return step2_artifact_dir() / RAW_OUTPUT_FILENAME


def step2_template2_output_path() -> Path:
    """Return the parsed Template 2 text path."""
    return step2_artifact_dir() / TEMPLATE2_OUTPUT_FILENAME


def step2_decision_packet_path() -> Path:
    """Return the parsed decision packet path."""
    return step2_artifact_dir() / DECISION_PACKET_FILENAME


def step2_blocked_by_research_gate_path() -> Path:
    """Return the deterministic Step 2 research-gate block artifact path."""
    return step2_artifact_dir() / STEP2_BLOCKED_BY_RESEARCH_GATE_FILENAME


def step2_promoted_decision_only_path() -> Path:
    """Return the deterministic promoted decision-only marker artifact path (R2E.5b-6c)."""
    return step2_artifact_dir() / STEP2_PROMOTED_DECISION_ONLY_FILENAME


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


def load_strategy_settings() -> dict[str, Any]:
    """Parse the operator-maintained strategy settings YAML."""
    return parse_strategy_settings_text(load_strategy_settings_text())


def load_portfolio_snapshot_text() -> str:
    """Read the operator-maintained portfolio snapshot exactly as stored on disk."""
    return _require_non_empty_text(
        current_inputs_dir() / "portfolio_snapshot.txt",
        label="portfolio snapshot input",
    )


def load_research_output() -> dict[str, Any]:
    """Read the parsed Step 1 artifact required by Step 2."""
    research_output_path = step1_research_output_path()
    if not research_output_path.exists():
        raise FileNotFoundError(
            f"Missing Step 1 artifact {research_output_path}. Run Step 1 parse before rendering Step 2."
        )
    payload = read_json(research_output_path)
    if not isinstance(payload, dict):
        raise ValueError("step1 research_output.json must be a JSON object.")
    return payload


def build_step2_prompt_text() -> str:
    """Render the Step 2 prompt from prompt template plus current artifacts."""
    strategy_settings_text = load_strategy_settings_text()
    portfolio_snapshot_text = load_portfolio_snapshot_text()
    research_output = load_research_output()

    prompt_template = read_text(require_prompt_path("strategy_a_decision_builder.txt"))

    rendered = render_prompt(
        prompt_template,
        {
            "research_json": json.dumps(research_output, ensure_ascii=False, indent=2),
            "portfolio_snapshot": portfolio_snapshot_text,
            "strategy_settings": strategy_settings_text,
        },
    )
    return rendered.rstrip() + "\n"


def render_step2_prompt() -> dict[str, str]:
    """Write the rendered Step 2 prompt and prepare the manual output artifact.

    Legacy STRICT_FRESH runs render exactly as before (raw research_output.json).
    Promoted decision-only runs (R2E.5b-6c) render from the promoted effective
    handoff after a fail-closed live verification, and additionally write the
    ``step2_promoted_decision_only.json`` marker.
    """
    gate = enforce_step2_research_gate(
        source_artifact_path=step1_research_degraded_mode_decision_path(),
        blocked_artifact_path=step2_blocked_by_research_gate_path(),
        repo_root_path=repo_root(),
    )

    if gate.mode == MODE_PROMOTED_STEP2_DECISION_ONLY:
        prompt_text, promoted_context = _build_promoted_step2_prompt_or_block(gate)
    else:
        prompt_text, promoted_context = build_step2_prompt_text(), None

    artifact_dir = step2_artifact_dir()
    prompt_output_path = step2_prompt_path()
    raw_output_path = step2_raw_output_path()

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
        "mode": gate.mode,
    }
    if promoted_context is not None:
        marker_path = _write_promoted_decision_only_marker(gate, promoted_context)
        result["step2_promoted_decision_only_path"] = str(marker_path)
        result["order_compilation_allowed"] = "False"
        result["new_buy_permission"] = "False"
        result["recommended_terminal_result_after_step2"] = NO_TRADE_PENDING_FINAL_GATES
    return result


def parse_step2_output() -> dict[str, str]:
    """Parse and validate the manual Step 2 output artifacts.

    Admission is enforced before the raw response is read or any Step 2 output
    is extracted or persisted. A denied state therefore writes only the existing
    deterministic research-gate block artifact through the gate owner.

    On a promoted decision-only run (R2E.5b-6c) the parse additionally refreshes
    the deterministic decision-only marker and reports it: the parsed decision
    packet is a research decision only — order_compilation_allowed stays false
    and Step 3/4 remain blocked. The LLM decision packet itself is written
    verbatim (never mutated by deterministic code).
    """
    gate = enforce_step2_research_gate(
        source_artifact_path=step1_research_degraded_mode_decision_path(),
        blocked_artifact_path=step2_blocked_by_research_gate_path(),
        repo_root_path=repo_root(),
    )
    promoted_context = (
        _load_promoted_step2_context_or_block(gate)
        if gate.mode == MODE_PROMOTED_STEP2_DECISION_ONLY
        else None
    )

    template2_text, decision_packet = extract_template2_and_decision_packet(
        raw_output_path=step2_raw_output_path(),
        template2_output_path=step2_template2_output_path(),
        decision_packet_path=step2_decision_packet_path(),
    )
    result = {
        "template2_output_path": str(step2_template2_output_path()),
        "decision_packet_path": str(step2_decision_packet_path()),
        "template2_output_chars": str(len(template2_text)),
        "market_snapshot_type": str(
            decision_packet.get("MARKET_DATA_SNAPSHOT", {}).get("snapshot_type", "")
        ),
    }
    if promoted_context is not None:
        marker_path = _write_promoted_decision_only_marker(gate, promoted_context)
        result["mode"] = gate.mode
        result["step2_promoted_decision_only_path"] = str(marker_path)
        result["order_compilation_allowed"] = "False"
        result["new_buy_permission"] = "False"
        result["recommended_terminal_result_after_step2"] = NO_TRADE_PENDING_FINAL_GATES
        step3_refresh = refresh_promoted_step3_audit_only_permission_after_step2()
        result["promoted_handoff_step3_audit_verification_path"] = step3_refresh.get(
            "promoted_handoff_step3_audit_verification_path", ""
        )
        result["promoted_step3_audit_gate_dry_run_path"] = step3_refresh.get(
            "promoted_step3_audit_gate_dry_run_path", ""
        )
        result["promoted_step3_audit_gate_dry_run_would_allow"] = step3_refresh.get(
            "promoted_step3_audit_gate_dry_run_would_allow", ""
        )
        result["promoted_step3_audit_only"] = step3_refresh.get(
            "promoted_step3_audit_only", "False"
        )
    return result


# --- R2E.5b-6c promoted Step 2 decision-only render ----------------------------


def _build_promoted_step2_prompt_or_block(
    gate: ResearchDegradedModeGateResult,
) -> tuple[str, dict[str, Any]]:
    """Build the promoted decision-only prompt, failing closed on verification."""
    promoted_context = _load_promoted_step2_context_or_block(gate)
    return _build_promoted_step2_prompt_text(promoted_context), promoted_context


def _load_promoted_step2_context_or_block(
    gate: ResearchDegradedModeGateResult,
) -> dict[str, Any]:
    """Load + live-re-verify the promoted source artifacts. Fail closed.

    Rendering the promoted path requires ``active_research_handoff_source.json``,
    ``research_handoff_candidate_effective.json``, and
    ``research_handoff_candidate_effective_validation.json``, and the R2E.5b-6a
    verifier must pass at render time (this re-checks pointer markers, hashes,
    actionable-ticker consistency, and ``promotion_expires_at`` *now*, not at
    Step 1 time). Any failure writes the deterministic Step 2 blocked artifact
    and raises — no prompt is rendered.
    """
    pointer = _read_json_object_or_none(step1_active_research_handoff_source_path())
    effective = _read_json_object_or_none(step1_effective_research_handoff_path())
    validation = _read_json_object_or_none(step1_effective_research_handoff_validation_path())

    verification = verify_promoted_handoff_for_step2_decision(
        active_pointer=pointer,
        effective_handoff=effective,
        effective_validation=validation,
        today=_settings_as_of_date_or_none(),
    )
    if verification.get("valid_for_step2_decision") is not True:
        blocked_payload = {
            "blocked": True,
            "reason": PROMOTED_VERIFICATION_FAILED_REASON,
            "state": gate.state,
            "mode": gate.mode,
            "allowed_actions": list(gate.allowed_actions),
            "blocked_actions": list(gate.blocked_actions),
            "order_compilation_allowed": False,
            "new_buy_permission": False,
            "manual_review_required": gate.manual_review_required,
            "blocker_reasons": list(verification.get("verification_blockers") or []),
            "source_artifact": _display_path(step1_research_degraded_mode_decision_path()),
            "recommended_result": "NO_TRADE",
            "report_only": False,
        }
        write_json(step2_blocked_by_research_gate_path(), blocked_payload)
        raise ResearchDegradedModeGateError(
            "Step 2 blocked by promoted decision-only verification: "
            f"blockers={blocked_payload['blocker_reasons']}; "
            f"blocked_artifact={_display_path(step2_blocked_by_research_gate_path())}"
        )

    return {
        "pointer": pointer,
        "effective_handoff": effective,
        "verification": verification,
        "active_pointer_sha256": _sha256_of(pointer),
        "effective_handoff_sha256": verification.get("effective_handoff_sha256"),
        "promotion_status": verification.get("promotion_status"),
        "promotion_expires_at": verification.get("promotion_expires_at"),
        "actionable_this_run_tickers": list(verification.get("actionable_this_run_tickers") or []),
    }


def _build_promoted_step2_prompt_text(promoted_context: dict[str, Any]) -> str:
    """Render Step 2 from the promoted effective handoff + source metadata block.

    The research body is ``research_handoff_candidate_effective.json`` — the raw
    Deep Research ``research_output.json`` and the active non-actionable compiled
    candidate are deliberately NOT used on this path.
    """
    strategy_settings_text = load_strategy_settings_text()
    portfolio_snapshot_text = load_portfolio_snapshot_text()

    prompt_template = read_text(require_prompt_path("strategy_a_decision_builder.txt"))
    rendered = render_prompt(
        prompt_template,
        {
            "research_json": json.dumps(
                promoted_context["effective_handoff"], ensure_ascii=False, indent=2
            ),
            "portfolio_snapshot": portfolio_snapshot_text,
            "strategy_settings": strategy_settings_text,
        },
    )
    return rendered.rstrip() + "\n" + _promoted_source_metadata_block(promoted_context)


def _promoted_source_metadata_block(promoted_context: dict[str, Any]) -> str:
    """Deterministic source-metadata block appended to the promoted prompt."""
    tickers = json.dumps(promoted_context["actionable_this_run_tickers"], ensure_ascii=False)
    return (
        "\n────────────────────────────────────────\n"
        "【PROMOTED RESEARCH SOURCE — Step 2 decision-only (R2E.5b-6c)】\n"
        f"source: {PROMOTED_SOURCE}\n"
        f"promotion_status: {promoted_context['promotion_status']}\n"
        f"active_pointer_sha256: {promoted_context['active_pointer_sha256']}\n"
        f"effective_handoff_sha256: {promoted_context['effective_handoff_sha256']}\n"
        f"promotion_expires_at: {promoted_context['promotion_expires_at']}\n"
        f"actionable_this_run_tickers: {tickers}\n"
        "NOTE: the research input above is the deterministic promoted compiled handoff "
        "(research_handoff_candidate_effective.json), NOT raw Deep Research output.\n"
        "NOTE: this run is Step 2 decision-only under PROMOTED_RESEARCH_DECISION. "
        "It is NOT order authorization and NOT execution authorization.\n"
        "NOTE: ORDER_COMPILATION and NEW_BUY are NOT allowed in this state; Step 3 audit, "
        "Step 4 order compilation, and the final execution safety gate remain blocked "
        "pending future gate PRs. The expected terminal result after Step 2 is "
        f"{NO_TRADE_PENDING_FINAL_GATES}.\n"
    )


def _write_promoted_decision_only_marker(
    gate: ResearchDegradedModeGateResult,
    promoted_context: dict[str, Any],
) -> Path:
    """Write the deterministic decision-only marker artifact (never LLM output)."""
    marker_path = step2_promoted_decision_only_path()
    write_json(
        marker_path,
        {
            "schema_version": STEP2_PROMOTED_DECISION_ONLY_SCHEMA_VERSION,
            "is_llm_generated": False,
            "mode": MODE_PROMOTED_STEP2_DECISION_ONLY,
            "promoted_step2_decision_only": True,
            "decision_only": True,
            "order_compilation_allowed": False,
            "new_buy_permission": False,
            "step3_allowed": False,
            "step4_allowed": False,
            "not_execution_authorization": True,
            "recommended_terminal_result_after_step2": NO_TRADE_PENDING_FINAL_GATES,
            "research_state": gate.state,
            "allowed_actions": list(gate.allowed_actions),
            "blocked_actions": list(gate.blocked_actions),
            "source": PROMOTED_SOURCE,
            "promotion_status": promoted_context["promotion_status"],
            "active_pointer_sha256": promoted_context["active_pointer_sha256"],
            "effective_handoff_sha256": promoted_context["effective_handoff_sha256"],
            "promotion_expires_at": promoted_context["promotion_expires_at"],
            "actionable_this_run_tickers": promoted_context["actionable_this_run_tickers"],
            "source_artifacts": {
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
            },
            "report_only": False,
        },
    )
    return marker_path


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
        as_of = load_strategy_settings().get("as_of")
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


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(repo_root()))
    except ValueError:
        return str(path)
