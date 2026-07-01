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


RUN_SUMMARY_FILENAME = "run_summary.json"
STRICT_FRESH = "STRICT_FRESH"

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
    source_artifacts: dict[str, str] = field(default_factory=dict)
    read_errors: list[str] = field(default_factory=list)


def build_blocked_run_summary(
    *,
    step1_decision: Mapping[str, Any] | None,
    step2_block: Mapping[str, Any] | None,
    step3_block: Mapping[str, Any] | None,
    step4_block: Mapping[str, Any] | None,
    step4_final_safety_block: Mapping[str, Any] | None = None,
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

    # Both the Step 4 upstream guard and the final execution safety gate block
    # Step 4; "step4" appears at most once in blocked_stages.
    step4_blocked = step4_block is not None or step4_final_safety_block is not None
    blocked_stages = [
        stage
        for stage, blocked in (
            ("step2", step2_block is not None),
            ("step3", step3_block is not None),
            ("step4", step4_blocked),
        )
        if blocked
    ]

    step1_state = _str_or_none(step1_decision.get("state")) if step1_decision else None
    step1_degraded = step1_state is not None and step1_state != STRICT_FRESH
    run_blocked = bool(blocked_stages) or step1_degraded

    permission = _primary_permission(step1_decision, step2_block, step3_block, step4_block)
    research_state = step1_state or (_str_or_none(permission.get("state")) if permission else None)
    research_availability = (
        _str_or_none(permission.get("research_availability")) if permission else None
    )

    allowed_actions = _string_list(permission.get("allowed_actions")) if permission else []
    blocked_actions = _string_list(permission.get("blocked_actions")) if permission else []
    if run_blocked:
        blocked_actions = _with_required_blocked_actions(allowed_actions, blocked_actions)

    manual_review_required = _any_manual_review(
        step1_decision, step2_block, step3_block, step4_block, step4_final_safety_block
    )

    recommended_result = "NO_TRADE" if run_blocked else None

    return BlockedRunSummaryResult(
        run_blocked=run_blocked,
        recommended_result=recommended_result,
        manual_review_required=manual_review_required,
        highest_severity_state=_highest_severity_state(
            step1_decision, step2_block, step3_block, step4_block
        ),
        research_state=research_state,
        research_availability=research_availability,
        allowed_actions=allowed_actions,
        blocked_actions=blocked_actions,
        blocked_stages=blocked_stages,
        primary_blocker_reasons=_primary_blocker_reasons(
            step1_decision, step2_block, step3_block, step4_block, step4_final_safety_block
        ),
        source_artifacts=dict(source_artifacts or {}),
        read_errors=list(read_errors or []),
    )


def blocked_run_summary_result_to_dict(result: BlockedRunSummaryResult) -> dict[str, Any]:
    """Serialize the run summary as a stable, deterministic operational artifact."""
    return {
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
        source_artifacts=source_artifacts,
        read_errors=read_errors,
    )

    from investment_orchestrator.common.io import write_json

    write_json(output_path, blocked_run_summary_result_to_dict(result))
    return result


# --- helpers -----------------------------------------------------------------


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
