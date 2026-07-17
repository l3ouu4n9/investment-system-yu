"""Deterministic run-level blocked / no-trade summary (PR UX1).

This aggregates the existing per-step degraded-mode / gate / guard artifacts
into a single operational summary so an operator can see at a glance that a
Deep Research no-output / invalid-research run resolved to NO_TRADE / blocked /
manual-review — rather than mistaking the chain of `exit 1`s for a broken
system.

It is a **deterministic operational artifact**, not an LLM output: it never
fabricates a decision packet, audited packet, or orders, and it never permits
any action. It only reads and echoes what the upstream deterministic artifacts
already decided. `is_llm_generated` is always ``False``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from investment_orchestrator.common.schema_validation import write_validated_json
from investment_orchestrator.state.research_degraded_mode_gate import (
    MODE_PROMOTED_STEP2_DECISION_ONLY,
    ResearchDegradedModeGateResult,
)


RUN_SUMMARY_FILENAME = "run_summary.json"
RUN_SUMMARY_SCHEMA_NAME = "blocked_run_summary.schema.json"
RUN_SUMMARY_CONTRACT_VERSION = "blocked_run_summary_observability_v1"
STRICT_FRESH = "STRICT_FRESH"

TERMINAL_REASON_RESEARCH_DEGRADED_MODE = "research_degraded_mode"
TERMINAL_REASON_PROMOTED_STEP2_DECISION_ONLY_PENDING_FINAL_GATES = (
    "promoted_step2_decision_only_pending_final_gates"
)
TERMINAL_REASON_STEP2_RESEARCH_GATE_BLOCKED = "step2_research_gate_blocked"
TERMINAL_REASON_STEP3_UPSTREAM_GATE_BLOCKED = "step3_upstream_gate_blocked"
TERMINAL_REASON_STEP4_UPSTREAM_GATE_BLOCKED = "step4_upstream_gate_blocked"
TERMINAL_REASON_FINAL_EXECUTION_SAFETY_GATE_BLOCKED = (
    "final_execution_safety_gate_blocked"
)
TERMINAL_REASON_DIAGNOSTIC_UNAVAILABLE = "terminal_diagnostic_unavailable"
TERMINAL_REASON_DIAGNOSTIC_INVALID = "terminal_diagnostic_invalid"
TERMINAL_REASON_SOURCE_CONFLICT = "terminal_source_conflict"
TERMINAL_REASON_SUMMARY_SOURCE_INVALID = "summary_source_invalid"

_TERMINAL_STAGES = frozenset(
    {
        "weekly_orchestrator",
        "step2_research_gate",
        "step3_upstream_gate",
        "step4_upstream_gate",
        "step4_final_execution_safety_gate",
    }
)
_STOPPED_BEFORE_STAGES = frozenset(
    {
        "step2_decision_builder",
        "step3_audit_engine",
        "step4_order_compiler",
        "order_compilation",
    }
)

# Order-generating actions that must be explicitly listed as blocked whenever
# they are not allowed on a blocked run.
_ORDER_GENERATING_ACTIONS = ("NEW_BUY", "ORDER_COMPILATION")

# Severity ordering for highest_severity_state (higher = more severe).
# STRICT_FRESH_EVIDENCE_ONLY (R2E.1) is a benign, non-actionable HOLD/NO_TRADE
# state — a deterministic compiled handoff exists — so it ranks low (just above
# STRICT_FRESH), well below the degraded/invalid states.
_STATE_SEVERITY = {
    "STRICT_FRESH": 0,
    "STRICT_FRESH_EVIDENCE_ONLY": 1,
    # R2E.4: grounded memo support, still benign / non-actionable (HOLD/NO_TRADE).
    "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE": 1,
    # R2E.5b-5b: promoted handoff recognized, still pending gates / non-actionable.
    "STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES": 1,
    # R2E.5b-6c: Step 2 decision-only permitted; order path still blocked, so the
    # run still summarizes as blocked / NO_TRADE (benign severity).
    "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY": 1,
    # R2E.5b-6f: Step 3 audit-only permitted; order path still blocked (no
    # NEW_BUY / ORDER_COMPILATION), same benign severity tier as the other
    # promoted non-order / pending-final-gates states.
    "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY": 1,
    "STRICT_STALE": 2,
    "DEGRADED_WITH_LAST_GOOD": 3,
    "DEGRADED_NO_RESEARCH": 4,
    "INVALID_CONTRACT": 5,
    "NO_OUTPUT": 6,
    "MANUAL_REVIEW_REQUIRED": 7,
}


@dataclass(frozen=True)
class BlockedRunSummaryResult:
    """Deterministic run-level summary of the current run's gate/guard state."""

    run_blocked: bool
    recommended_result: str | None
    manual_review_required: bool
    highest_severity_state: str | None
    research_state: str | None
    research_availability: str | None
    allowed_actions: list[str]
    blocked_actions: list[str]
    blocked_stages: list[str]
    primary_blocker_reasons: list[str]
    terminal_stage: str | None
    stopped_before_stage: str | None
    terminal_reason_codes: list[str]
    terminal_diagnostics: list[str]
    source_artifacts: dict[str, str] = field(default_factory=dict)
    read_errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunSummaryTerminalObservation:
    """Closed, in-memory routing facts supplied by the weekly entrypoint.

    This is deliberately not a persisted permission artifact.  It carries the
    exact result already used by the caller to choose a controlled weekly
    terminal, so the summary does not re-evaluate a gate or infer a terminal
    from absent downstream files.
    """

    terminal_stage: str
    stopped_before_stage: str | None
    terminal_reason_codes: tuple[str, ...]
    primary_blocker_reasons: tuple[str, ...]
    research_state: str
    allowed_actions: tuple[str, ...]
    blocked_actions: tuple[str, ...]
    manual_review_required: bool


def terminal_observation_from_research_gate(
    gate: ResearchDegradedModeGateResult,
) -> RunSummaryTerminalObservation | None:
    """Return the terminal observation implied by an already-evaluated gate.

    ``None`` means the legacy strict-fresh actionable route proceeds.  This
    function only projects the supplied result; it does not evaluate a gate.
    """
    if gate.allowed and gate.mode != MODE_PROMOTED_STEP2_DECISION_ONLY:
        return None

    if gate.allowed:
        terminal_stage = "weekly_orchestrator"
        stopped_before_stage = None
        reason_code = TERMINAL_REASON_PROMOTED_STEP2_DECISION_ONLY_PENDING_FINAL_GATES
    else:
        terminal_stage = "step2_research_gate"
        stopped_before_stage = "step2_decision_builder"
        reason_code = TERMINAL_REASON_RESEARCH_DEGRADED_MODE

    return RunSummaryTerminalObservation(
        terminal_stage=terminal_stage,
        stopped_before_stage=stopped_before_stage,
        terminal_reason_codes=(reason_code,),
        primary_blocker_reasons=tuple(_exact_string_list(gate.blocker_reasons)),
        research_state=gate.state if type(gate.state) is str else "",
        allowed_actions=tuple(_exact_string_list(gate.allowed_actions)),
        blocked_actions=tuple(_exact_string_list(gate.blocked_actions)),
        manual_review_required=gate.manual_review_required is True,
    )


def build_blocked_run_summary(
    *,
    step1_decision: Mapping[str, Any] | None,
    step2_block: Mapping[str, Any] | None,
    step3_block: Mapping[str, Any] | None,
    step4_block: Mapping[str, Any] | None,
    step4_final_safety_block: Mapping[str, Any] | None = None,
    terminal_observation: RunSummaryTerminalObservation | None = None,
    source_artifacts: Mapping[str, str] | None = None,
    read_errors: list[str] | None = None,
) -> BlockedRunSummaryResult:
    """Deterministically summarize the per-step degraded/gate/guard artifacts."""
    step1_decision = step1_decision if isinstance(step1_decision, Mapping) else None
    step2_block = step2_block if isinstance(step2_block, Mapping) else None
    step3_block = step3_block if isinstance(step3_block, Mapping) else None
    step4_block = step4_block if isinstance(step4_block, Mapping) else None
    step4_final_safety_block = (
        step4_final_safety_block if isinstance(step4_final_safety_block, Mapping) else None
    )

    explicit_step2_block = _is_valid_explicit_block_artifact(step2_block)
    explicit_step3_block = _is_valid_explicit_block_artifact(step3_block)
    explicit_step4_block = _is_valid_explicit_block_artifact(step4_block)
    explicit_final_safety_block = _is_valid_explicit_block_artifact(step4_final_safety_block)
    invalid_explicit_block_sources = [
        label
        for label, source, valid in (
            ("step2", step2_block, explicit_step2_block),
            ("step3", step3_block, explicit_step3_block),
            ("step4", step4_block, explicit_step4_block),
            ("step4 final execution safety", step4_final_safety_block, explicit_final_safety_block),
        )
        if source is not None and not valid
    ]

    # Both the Step 4 upstream guard and the final execution safety gate block
    # Step 4; "step4" appears at most once in blocked_stages.  A malformed JSON
    # object is not a valid explicit block artifact and must not be promoted to
    # a stage classification merely because its path exists.
    step4_blocked = explicit_step4_block or explicit_final_safety_block
    blocked_stages = [
        stage
        for stage, blocked in (
            ("step2", explicit_step2_block),
            ("step3", explicit_step3_block),
            ("step4", step4_blocked),
        )
        if blocked
    ]

    step1_state = _str_or_none(step1_decision.get("state")) if step1_decision else None
    step1_degraded = step1_state is not None and step1_state != STRICT_FRESH
    terminal_observation = _validated_terminal_observation(terminal_observation)
    explicit_observation = _terminal_observation_from_explicit_block(
        step2_block=step2_block if explicit_step2_block else None,
        step3_block=step3_block if explicit_step3_block else None,
        step4_block=step4_block if explicit_step4_block else None,
        step4_final_safety_block=(
            step4_final_safety_block if explicit_final_safety_block else None
        ),
    )

    terminal_reason_codes: list[str] = []
    terminal_diagnostics: list[str] = []
    terminal_stage: str | None = None
    stopped_before_stage: str | None = None
    primary_blocker_reasons: list[str] = []

    if terminal_observation is not None:
        terminal_stage = terminal_observation.terminal_stage
        stopped_before_stage = terminal_observation.stopped_before_stage
        terminal_reason_codes.extend(terminal_observation.terminal_reason_codes)
        primary_blocker_reasons.extend(terminal_observation.primary_blocker_reasons)
        if explicit_observation is not None:
            terminal_reason_codes.append(TERMINAL_REASON_SOURCE_CONFLICT)
            terminal_diagnostics.append(
                "weekly terminal observation conflicts with explicit downstream block artifacts."
            )
        if invalid_explicit_block_sources:
            terminal_reason_codes.append(TERMINAL_REASON_SUMMARY_SOURCE_INVALID)
            terminal_diagnostics.extend(
                f"{label} explicit block artifact is invalid."
                for label in invalid_explicit_block_sources
            )
    elif explicit_observation is not None:
        terminal_stage = explicit_observation.terminal_stage
        stopped_before_stage = explicit_observation.stopped_before_stage
        terminal_reason_codes.extend(explicit_observation.terminal_reason_codes)
        primary_blocker_reasons.extend(explicit_observation.primary_blocker_reasons)
    elif invalid_explicit_block_sources:
        terminal_reason_codes.append(TERMINAL_REASON_SUMMARY_SOURCE_INVALID)
        terminal_diagnostics.extend(
            f"{label} explicit block artifact is invalid."
            for label in invalid_explicit_block_sources
        )
    elif step1_degraded:
        # Direct builder callers without the weekly gate retain the established
        # NO_TRADE classification.  Production weekly callers supply the exact
        # gate observation above, so this fallback is never a second gate.
        terminal_stage = "step2_research_gate"
        stopped_before_stage = "step2_decision_builder"
        terminal_reason_codes.append(TERMINAL_REASON_RESEARCH_DEGRADED_MODE)
        primary_blocker_reasons.extend(_primary_blocker_reasons(
            step1_decision, None, None, None, None
        ))
    elif read_errors:
        terminal_reason_codes.append(TERMINAL_REASON_SUMMARY_SOURCE_INVALID)
        terminal_diagnostics.extend(_exact_string_list(read_errors))

    terminal_diagnostics.extend(_step1_terminal_diagnostics(step1_decision, terminal_reason_codes))

    if terminal_reason_codes and not primary_blocker_reasons and not terminal_diagnostics:
        terminal_reason_codes.append(TERMINAL_REASON_DIAGNOSTIC_UNAVAILABLE)
        terminal_diagnostics.append("deterministic terminal diagnostic detail is unavailable.")

    terminal_reason_codes = _dedupe_exact_strings(terminal_reason_codes)
    terminal_diagnostics = _dedupe_exact_strings(terminal_diagnostics)
    primary_blocker_reasons = _dedupe_exact_strings(primary_blocker_reasons)

    run_blocked = bool(blocked_stages) or step1_degraded or bool(terminal_reason_codes)

    permission = _primary_permission(
        step1_decision,
        step2_block if explicit_step2_block else None,
        step3_block if explicit_step3_block else None,
        step4_block if explicit_step4_block else None,
    )
    research_state = step1_state or (_str_or_none(permission.get("state")) if permission else None)
    research_availability = (
        _str_or_none(permission.get("research_availability")) if permission else None
    )

    allowed_actions = _string_list(permission.get("allowed_actions")) if permission else []
    blocked_actions = _string_list(permission.get("blocked_actions")) if permission else []
    if terminal_observation is not None:
        if research_state is None:
            research_state = terminal_observation.research_state or None
        if not allowed_actions:
            allowed_actions = list(terminal_observation.allowed_actions)
        if not blocked_actions:
            blocked_actions = list(terminal_observation.blocked_actions)
    if run_blocked:
        blocked_actions = _with_required_blocked_actions(allowed_actions, blocked_actions)

    manual_review_required = _any_manual_review(
        step1_decision,
        step2_block if explicit_step2_block else None,
        step3_block if explicit_step3_block else None,
        step4_block if explicit_step4_block else None,
        step4_final_safety_block if explicit_final_safety_block else None,
    )
    if terminal_observation is not None and not manual_review_required:
        manual_review_required = terminal_observation.manual_review_required

    recommended_result = "NO_TRADE" if run_blocked else None

    return BlockedRunSummaryResult(
        run_blocked=run_blocked,
        recommended_result=recommended_result,
        manual_review_required=manual_review_required,
        highest_severity_state=_highest_severity_state(
            step1_decision,
            step2_block if explicit_step2_block else None,
            step3_block if explicit_step3_block else None,
            step4_block if explicit_step4_block else None,
        ),
        research_state=research_state,
        research_availability=research_availability,
        allowed_actions=allowed_actions,
        blocked_actions=blocked_actions,
        blocked_stages=blocked_stages,
        primary_blocker_reasons=primary_blocker_reasons,
        terminal_stage=terminal_stage,
        stopped_before_stage=stopped_before_stage,
        terminal_reason_codes=terminal_reason_codes,
        terminal_diagnostics=terminal_diagnostics,
        source_artifacts=dict(source_artifacts or {}),
        read_errors=list(read_errors or []),
    )


def blocked_run_summary_result_to_dict(result: BlockedRunSummaryResult) -> dict[str, Any]:
    """Serialize the run summary as a stable, deterministic operational artifact."""
    return {
        "summary_contract_version": RUN_SUMMARY_CONTRACT_VERSION,
        "run_blocked": result.run_blocked,
        "recommended_result": result.recommended_result,
        "manual_review_required": result.manual_review_required,
        "highest_severity_state": result.highest_severity_state,
        "research_state": result.research_state,
        "research_availability": result.research_availability,
        "allowed_actions": list(result.allowed_actions),
        "blocked_actions": list(result.blocked_actions),
        "blocked_stages": list(result.blocked_stages),
        "primary_blocker_reasons": list(result.primary_blocker_reasons),
        "terminal_stage": result.terminal_stage,
        "stopped_before_stage": result.stopped_before_stage,
        "terminal_reason_codes": list(result.terminal_reason_codes),
        "terminal_diagnostics": list(result.terminal_diagnostics),
        "source_artifacts": dict(result.source_artifacts),
        "read_errors": list(result.read_errors),
        "is_llm_generated": False,
        "report_only": False,
    }


def safe_load_json_object(
    path: Path,
    *,
    repo_root_path: Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Read a JSON object, never raising.

    Returns ``(obj, None)`` on success, ``(None, None)`` when the file is simply
    absent (not an error for an optional artifact), and ``(None, error)`` when
    the file exists but is malformed / not an object.
    """
    if not path.is_file():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"could not read {_display_path(path, repo_root_path)}: {exc}"
    if not isinstance(payload, dict):
        return None, f"{_display_path(path, repo_root_path)} is not a JSON object."
    return payload, None


def summarize_current_run(
    *,
    step1_decision_path: Path,
    step2_block_path: Path,
    step3_block_path: Path,
    step4_block_path: Path,
    output_path: Path,
    step4_final_safety_block_path: Path | None = None,
    repo_root_path: Path | None = None,
    terminal_observation: RunSummaryTerminalObservation | None = None,
) -> BlockedRunSummaryResult:
    """Read the current-run artifacts, build the summary, and write it.

    Best-effort and deterministic: missing artifacts are skipped, malformed
    artifacts are recorded in ``read_errors`` (never fabricated), and nothing
    here changes any gate decision.
    """
    read_errors: list[str] = []
    source_artifacts: dict[str, str] = {}

    sources: list[tuple[str, Path]] = [
        ("step1_degraded_decision", step1_decision_path),
        ("step2_blocked_by_research_gate", step2_block_path),
        ("step3_blocked_by_upstream_gate", step3_block_path),
        ("step4_blocked_by_upstream_gate", step4_block_path),
    ]
    if step4_final_safety_block_path is not None:
        sources.append(("step4_final_execution_safety_gate", step4_final_safety_block_path))

    loaded: dict[str, Mapping[str, Any] | None] = {}
    for key, path in sources:
        obj, error = safe_load_json_object(path, repo_root_path=repo_root_path)
        loaded[key] = obj
        if error is not None:
            read_errors.append(error)
        if obj is not None or error is not None:
            source_artifacts[key] = _display_path(path, repo_root_path)

    result = build_blocked_run_summary(
        step1_decision=loaded["step1_degraded_decision"],
        step2_block=loaded["step2_blocked_by_research_gate"],
        step3_block=loaded["step3_blocked_by_upstream_gate"],
        step4_block=loaded["step4_blocked_by_upstream_gate"],
        step4_final_safety_block=loaded.get("step4_final_execution_safety_gate"),
        terminal_observation=terminal_observation,
        source_artifacts=source_artifacts,
        read_errors=read_errors,
    )

    write_validated_json(
        output_path,
        blocked_run_summary_result_to_dict(result),
        schema_name=RUN_SUMMARY_SCHEMA_NAME,
    )
    return result


# --- helpers -----------------------------------------------------------------


def _validated_terminal_observation(
    observation: RunSummaryTerminalObservation | None,
) -> RunSummaryTerminalObservation | None:
    if type(observation) is not RunSummaryTerminalObservation:
        return None
    if type(observation.terminal_stage) is not str:
        return None
    if observation.terminal_stage not in _TERMINAL_STAGES:
        return None
    if observation.stopped_before_stage is not None and type(observation.stopped_before_stage) is not str:
        return None
    if observation.stopped_before_stage not in _STOPPED_BEFORE_STAGES | {None}:
        return None
    if type(observation.terminal_reason_codes) is not tuple:
        return None
    if not _exact_string_list(observation.terminal_reason_codes):
        return None
    if type(observation.primary_blocker_reasons) is not tuple:
        return None
    if type(observation.research_state) is not str:
        return None
    if type(observation.allowed_actions) is not tuple:
        return None
    if type(observation.blocked_actions) is not tuple:
        return None
    if type(observation.manual_review_required) is not bool:
        return None
    if len(_exact_string_list(observation.terminal_reason_codes)) != len(
        observation.terminal_reason_codes
    ):
        return None
    if len(_exact_string_list(observation.primary_blocker_reasons)) != len(
        observation.primary_blocker_reasons
    ):
        return None
    if len(_exact_string_list(observation.allowed_actions)) != len(observation.allowed_actions):
        return None
    if len(_exact_string_list(observation.blocked_actions)) != len(observation.blocked_actions):
        return None
    return observation


def _is_valid_explicit_block_artifact(source: Mapping[str, Any] | None) -> bool:
    """Return whether a decoded artifact is a minimum valid explicit block.

    The summary is not a substitute validator for Step 2--4 artifacts.  It
    nevertheless must not report an arbitrary malformed mapping as a persisted
    block merely because a file happened to exist.
    """
    if type(source) is not dict:
        return False
    return (
        source.get("blocked") is True
        and type(source.get("reason")) is str
        and bool(source["reason"])
        and type(source.get("recommended_result")) is str
        and source["recommended_result"] == "NO_TRADE"
    )


def _terminal_observation_from_explicit_block(
    *,
    step2_block: Mapping[str, Any] | None,
    step3_block: Mapping[str, Any] | None,
    step4_block: Mapping[str, Any] | None,
    step4_final_safety_block: Mapping[str, Any] | None,
) -> RunSummaryTerminalObservation | None:
    """Return the first valid persisted block in execution order."""
    sources: tuple[tuple[Mapping[str, Any] | None, str, str, str], ...] = (
        (
            step2_block,
            "step2_research_gate",
            "step2_decision_builder",
            TERMINAL_REASON_STEP2_RESEARCH_GATE_BLOCKED,
        ),
        (
            step3_block,
            "step3_upstream_gate",
            "step3_audit_engine",
            TERMINAL_REASON_STEP3_UPSTREAM_GATE_BLOCKED,
        ),
        (
            step4_block,
            "step4_upstream_gate",
            "step4_order_compiler",
            TERMINAL_REASON_STEP4_UPSTREAM_GATE_BLOCKED,
        ),
        (
            step4_final_safety_block,
            "step4_final_execution_safety_gate",
            "order_compilation",
            TERMINAL_REASON_FINAL_EXECUTION_SAFETY_GATE_BLOCKED,
        ),
    )
    for source, terminal_stage, stopped_before_stage, reason_code in sources:
        if not _is_valid_explicit_block_artifact(source):
            continue
        reasons = _blocker_reasons_for_explicit_block(source)
        return RunSummaryTerminalObservation(
            terminal_stage=terminal_stage,
            stopped_before_stage=stopped_before_stage,
            terminal_reason_codes=(reason_code,),
            primary_blocker_reasons=tuple(reasons),
            research_state="",
            allowed_actions=(),
            blocked_actions=(),
            manual_review_required=False,
        )
    return None


def _blocker_reasons_for_explicit_block(source: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    reasons.extend(_exact_string_list(source.get("blocker_reasons")))
    reasons.extend(_exact_string_list(source.get("fail_reasons")))
    permission = source.get("upstream_permission")
    if type(permission) is dict:
        reasons.extend(_exact_string_list(permission.get("blocker_reasons")))
    if not reasons:
        reason = source.get("reason")
        if type(reason) is str and reason:
            reasons.append(reason)
    return _dedupe_exact_strings(reasons)


def _step1_terminal_diagnostics(
    step1_decision: Mapping[str, Any] | None,
    terminal_reason_codes: list[str],
) -> list[str]:
    if type(step1_decision) is not dict:
        return []

    diagnostics: list[str] = []
    for field_name in ("diagnostic_reason", "parse_error"):
        if field_name not in step1_decision:
            continue
        value = step1_decision[field_name]
        if value is None:
            continue
        if type(value) is str and value:
            diagnostics.append(value)
            continue
        terminal_reason_codes.append(TERMINAL_REASON_DIAGNOSTIC_INVALID)
        diagnostics.append(f"step1 {field_name} is not a non-empty string.")
    return diagnostics


def _exact_string_list(value: Any) -> list[str]:
    if type(value) not in (list, tuple):
        return []
    return [item for item in value if type(item) is str and item]


def _dedupe_exact_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if type(value) is str and value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _primary_permission(
    step1_decision: Mapping[str, Any] | None,
    step2_block: Mapping[str, Any] | None,
    step3_block: Mapping[str, Any] | None,
    step4_block: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Pick the authoritative permission source: Step 1 decision, then Step 2,
    then the upstream_permission embedded in the Step 3/4 blocked artifacts."""
    for source in (step1_decision, step2_block):
        if isinstance(source, Mapping) and ("allowed_actions" in source or "state" in source):
            return source
    for block in (step3_block, step4_block):
        if isinstance(block, Mapping):
            permission = block.get("upstream_permission")
            if isinstance(permission, Mapping):
                return permission
    return None


def _with_required_blocked_actions(
    allowed_actions: list[str],
    blocked_actions: list[str],
) -> list[str]:
    merged = list(blocked_actions)
    for action in _ORDER_GENERATING_ACTIONS:
        if action not in allowed_actions and action not in merged:
            merged.append(action)
    return merged


def _any_manual_review(
    step1_decision: Mapping[str, Any] | None,
    step2_block: Mapping[str, Any] | None,
    step3_block: Mapping[str, Any] | None,
    step4_block: Mapping[str, Any] | None,
    step4_final_safety_block: Mapping[str, Any] | None = None,
) -> bool:
    for source in (step1_decision, step2_block, step3_block, step4_block, step4_final_safety_block):
        if _manual_review_true(source):
            return True
    for block in (step3_block, step4_block):
        if isinstance(block, Mapping) and _manual_review_true(block.get("upstream_permission")):
            return True
    return False


def _manual_review_true(source: Any) -> bool:
    return isinstance(source, Mapping) and source.get("manual_review_required") is True


def _highest_severity_state(*sources: Mapping[str, Any] | None) -> str | None:
    observed: list[str] = []
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        state = _str_or_none(source.get("state"))
        if state:
            observed.append(state)
        permission = source.get("upstream_permission")
        if isinstance(permission, Mapping):
            nested_state = _str_or_none(permission.get("state"))
            if nested_state:
                observed.append(nested_state)
    if not observed:
        return None
    return max(observed, key=lambda state: _STATE_SEVERITY.get(state, -1))


def _primary_blocker_reasons(
    step1_decision: Mapping[str, Any] | None,
    step2_block: Mapping[str, Any] | None,
    step3_block: Mapping[str, Any] | None,
    step4_block: Mapping[str, Any] | None,
    step4_final_safety_block: Mapping[str, Any] | None = None,
) -> list[str]:
    reasons: list[str] = []
    for source in (step1_decision, step2_block):
        if isinstance(source, Mapping):
            reasons.extend(_string_list(source.get("blocker_reasons")))
    for block in (step3_block, step4_block):
        if isinstance(block, Mapping):
            permission = block.get("upstream_permission")
            if isinstance(permission, Mapping):
                reasons.extend(_string_list(permission.get("blocker_reasons")))
    # Final execution safety gate diagnostics (fail_reasons == blocker_reasons there).
    if isinstance(step4_final_safety_block, Mapping):
        reasons.extend(_string_list(step4_final_safety_block.get("fail_reasons")))
        reasons.extend(_string_list(step4_final_safety_block.get("blocker_reasons")))
    # Deterministic de-duplication, preserving first-seen order.
    seen: set[str] = set()
    deduped: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            deduped.append(reason)
    return deduped


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _display_path(path: Path, repo_root_path: Path | None) -> str:
    if repo_root_path is not None:
        try:
            return str(path.relative_to(repo_root_path))
        except ValueError:
            pass
    return str(path)
