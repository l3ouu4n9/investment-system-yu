"""Step 2 enforcement gate for Step 1 degraded-mode permissions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import read_json, write_json


ACTIONABLE_REQUIRED_STATE = "STRICT_FRESH"
REQUIRED_ACTIONS = ("NEW_BUY", "ORDER_COMPILATION")
HOLD_NO_TRADE_ACTIONS = ("HOLD", "NO_TRADE")
MISSING_RESEARCH_PERMISSION = "MISSING_RESEARCH_PERMISSION"
MALFORMED_RESEARCH_PERMISSION = "MALFORMED_RESEARCH_PERMISSION"


class ResearchDegradedModeGateError(RuntimeError):
    """Raised when Step 2 must not render an actionable prompt."""


@dataclass(frozen=True)
class ResearchDegradedModeGateResult:
    """Decision for whether Step 2 may enter the actionable render path."""

    allowed: bool
    state: str
    allowed_actions: list[str]
    blocked_actions: list[str]
    manual_review_required: bool
    blocker_reasons: list[str]
    malformed_reasons: list[str]


def enforce_step2_research_gate(
    *,
    source_artifact_path: Path,
    blocked_artifact_path: Path,
    repo_root_path: Path | None = None,
) -> ResearchDegradedModeGateResult:
    """Fail closed unless Step 1 explicitly permits actionable Step 2 work."""
    result = load_and_evaluate_step2_research_gate(source_artifact_path)
    if result.allowed:
        return result

    blocked_payload = step2_research_gate_blocked_artifact(
        result,
        source_artifact_path=source_artifact_path,
        repo_root_path=repo_root_path,
    )
    write_json(blocked_artifact_path, blocked_payload)
    raise ResearchDegradedModeGateError(
        "Step 2 blocked by research degraded-mode gate: "
        f"state={result.state}; allowed_actions={result.allowed_actions}; "
        f"manual_review_required={result.manual_review_required}; "
        f"blocked_artifact={_display_path(blocked_artifact_path, repo_root_path)}"
    )


def load_and_evaluate_step2_research_gate(
    source_artifact_path: Path,
) -> ResearchDegradedModeGateResult:
    """Load the Step 1 permission artifact and evaluate the Step 2 gate."""
    try:
        payload = read_json(source_artifact_path)
    except FileNotFoundError:
        return _blocked_result(
            state=MISSING_RESEARCH_PERMISSION,
            blocker_reasons=["missing research degraded-mode decision artifact."],
        )
    except json.JSONDecodeError as exc:
        return _blocked_result(
            state=MALFORMED_RESEARCH_PERMISSION,
            malformed_reasons=[f"malformed JSON: {exc}"],
        )

    return evaluate_step2_research_gate(payload)


def evaluate_step2_research_gate(payload: Any) -> ResearchDegradedModeGateResult:
    """Evaluate a decoded degraded-mode decision payload."""
    if not isinstance(payload, Mapping):
        return _blocked_result(
            state=MALFORMED_RESEARCH_PERMISSION,
            malformed_reasons=["permission artifact must be a JSON object."],
        )

    state = payload.get("state")
    allowed_actions_value = payload.get("allowed_actions")
    blocked_actions_value = payload.get("blocked_actions")
    manual_review_required = payload.get("manual_review_required")
    blocker_reasons_value = payload.get("blocker_reasons")

    malformed_reasons: list[str] = []
    if not isinstance(state, str) or not state:
        malformed_reasons.append("state must be a non-empty string.")
        state = MALFORMED_RESEARCH_PERMISSION

    allowed_actions = _string_list(allowed_actions_value)
    if allowed_actions is None:
        malformed_reasons.append("allowed_actions must be a string array.")
        allowed_actions = list(HOLD_NO_TRADE_ACTIONS)

    blocked_actions = _string_list(blocked_actions_value)
    if blocked_actions is None:
        blocked_actions = []
    blocked_actions = _ensure_required_actions_blocked(allowed_actions, blocked_actions)

    if not isinstance(manual_review_required, bool):
        malformed_reasons.append("manual_review_required must be a boolean.")
        manual_review_required = False

    blocker_reasons = _string_list(blocker_reasons_value) or []
    blocker_reasons.extend(malformed_reasons)

    allowed = (
        not malformed_reasons
        and state == ACTIONABLE_REQUIRED_STATE
        and all(action in allowed_actions for action in REQUIRED_ACTIONS)
        and manual_review_required is False
    )

    if not allowed and not malformed_reasons:
        missing_actions = [action for action in REQUIRED_ACTIONS if action not in allowed_actions]
        if state != ACTIONABLE_REQUIRED_STATE:
            blocker_reasons.append(
                f"research state {state} is not {ACTIONABLE_REQUIRED_STATE}."
            )
        if missing_actions:
            blocker_reasons.append(
                "research permission does not allow required actions: "
                + ", ".join(missing_actions)
            )
        if manual_review_required is True:
            blocker_reasons.append("research permission requires manual review.")

    return ResearchDegradedModeGateResult(
        allowed=allowed,
        state=state,
        allowed_actions=allowed_actions,
        blocked_actions=blocked_actions,
        manual_review_required=manual_review_required,
        blocker_reasons=blocker_reasons,
        malformed_reasons=malformed_reasons,
    )


def step2_research_gate_blocked_artifact(
    result: ResearchDegradedModeGateResult,
    *,
    source_artifact_path: Path,
    repo_root_path: Path | None = None,
) -> dict[str, Any]:
    """Build the deterministic blocked/no-trade artifact for Step 2."""
    return {
        "blocked": True,
        "reason": "research_degraded_mode_gate",
        "state": result.state,
        "allowed_actions": result.allowed_actions,
        "blocked_actions": result.blocked_actions,
        "manual_review_required": result.manual_review_required,
        "blocker_reasons": result.blocker_reasons,
        "source_artifact": _display_path(source_artifact_path, repo_root_path),
        "recommended_result": "NO_TRADE",
        "report_only": False,
    }


def _blocked_result(
    *,
    state: str,
    blocker_reasons: list[str] | None = None,
    malformed_reasons: list[str] | None = None,
) -> ResearchDegradedModeGateResult:
    return ResearchDegradedModeGateResult(
        allowed=False,
        state=state,
        allowed_actions=list(HOLD_NO_TRADE_ACTIONS),
        blocked_actions=list(REQUIRED_ACTIONS),
        manual_review_required=False,
        blocker_reasons=list(blocker_reasons or malformed_reasons or []),
        malformed_reasons=list(malformed_reasons or []),
    )


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return list(value)


def _ensure_required_actions_blocked(
    allowed_actions: list[str],
    blocked_actions: list[str],
) -> list[str]:
    merged = list(blocked_actions)
    for action in REQUIRED_ACTIONS:
        if action not in allowed_actions and action not in merged:
            merged.append(action)
    return merged


def _display_path(path: Path, repo_root_path: Path | None) -> str:
    if repo_root_path is not None:
        try:
            return str(path.relative_to(repo_root_path))
        except ValueError:
            pass
    return str(path)
