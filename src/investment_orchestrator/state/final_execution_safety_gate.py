"""Deterministic final execution safety gate (PR F / P1).

Step 4 historically released order compilation on Step 3's **LLM self-reported**
``audit_passed`` / ``order_compiler_ready`` booleans. This gate makes those
booleans *necessary but not sufficient*: before any order compilation, a
deterministic, code-based check must independently confirm the run is genuinely
in an order-eligible state.

It fails closed: anything missing / malformed / blocked / not-STRICT_FRESH /
explicitly-blockered yields ``ready_for_order_compilation=False`` and a NO_TRADE
recommendation. It never fabricates a decision packet, audit packet, or order
output, and it does not change order-compiler / broker / execution logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import write_json


GATE_REASON = "final_execution_safety_gate"
ACTIONABLE_REQUIRED_STATE = "STRICT_FRESH"
REQUIRED_ALLOWED_ACTION = "ORDER_COMPILATION"
NEW_BUY_ACTION = "NEW_BUY"

# Step 2 decision-packet structural fields that must be present (and lists) for
# the downstream order compiler to have intended-action input to work from.
_STEP2_REQUIRED_LIST_FIELDS = (
    "active_shortlist",
    "buy_side_delta_table",
    "sell_side_delta_table_8_2",
    "execution_plan_drafts_8_5",
    "sell_execution_plan_drafts_8_6",
    "assumptions_and_data_gaps",
)
# Step 3 audited-packet structural fields that must be present (and lists).
_STEP3_REQUIRED_LIST_FIELDS = (
    "final_buy_side_delta_table",
    "final_sell_side_delta_table",
    "final_execution_plans",
    "final_sell_execution_plans",
)
# Fields scanned for explicit blockers in either packet.
_STEP2_BUY_INTENT_FIELDS = ("buy_side_delta_table", "execution_plan_drafts_8_5")
_STEP3_BUY_INTENT_FIELDS = ("final_buy_side_delta_table", "final_execution_plans")


class FinalExecutionSafetyGateError(RuntimeError):
    """Raised when Step 4 must not proceed to order compilation."""


@dataclass(frozen=True)
class FinalExecutionSafetyResult:
    """Deterministic decision on whether order compilation may proceed."""

    ready_for_order_compilation: bool
    blocked: bool
    reason: str | None
    fail_reasons: list[str] = field(default_factory=list)
    blocker_reasons: list[str] = field(default_factory=list)
    non_blocker_reasons: list[str] = field(default_factory=list)
    checked_conditions: dict[str, bool] = field(default_factory=dict)
    recommended_result: str | None = None
    manual_review_required: bool = False
    is_deterministic: bool = True


def evaluate_final_execution_safety(
    *,
    step2_decision_packet: Mapping[str, Any] | None,
    step3_audited_packet: Mapping[str, Any] | None,
    step1_permission: Mapping[str, Any] | None = None,
    step2_block: Mapping[str, Any] | None = None,
    step3_block: Mapping[str, Any] | None = None,
    step4_block: Mapping[str, Any] | None = None,
) -> FinalExecutionSafetyResult:
    """Deterministically decide whether Step 4 may compile orders. Fails closed."""
    fail_reasons: list[str] = []
    checked: dict[str, bool] = {}

    # A. No upstream block artifact present.
    upstream_blocks = {
        "step2_block": step2_block,
        "step3_block": step3_block,
        "step4_block": step4_block,
    }
    present_blocks = [name for name, block in upstream_blocks.items() if isinstance(block, Mapping)]
    no_upstream_block = not present_blocks
    checked["no_upstream_block"] = no_upstream_block
    if not no_upstream_block:
        fail_reasons.append(f"upstream block artifact(s) present: {', '.join(present_blocks)}.")

    # B. Step 1 permission allows the order path.
    permission_present = isinstance(step1_permission, Mapping)
    checked["step1_permission_present"] = permission_present
    if not permission_present:
        fail_reasons.append("missing or malformed Step 1 research degraded-mode decision artifact.")

    permission = step1_permission if permission_present else {}
    state = permission.get("state")
    allowed_actions = _string_list(permission.get("allowed_actions"))

    state_ok = permission_present and state == ACTIONABLE_REQUIRED_STATE
    checked["step1_state_strict_fresh"] = state_ok
    if permission_present and not state_ok:
        fail_reasons.append(f"Step 1 research state {state} is not {ACTIONABLE_REQUIRED_STATE}.")

    order_compilation_allowed = permission_present and REQUIRED_ALLOWED_ACTION in allowed_actions
    checked["order_compilation_allowed"] = order_compilation_allowed
    if permission_present and not order_compilation_allowed:
        fail_reasons.append(
            f"Step 1 permission does not allow {REQUIRED_ALLOWED_ACTION}."
        )

    # C / D structure: needed before the new-buy check can read intents.
    step2_structured, step2_struct_reasons = _evaluate_step2_structure(step2_decision_packet)
    checked["step2_decision_packet_structured"] = step2_structured
    fail_reasons.extend(step2_struct_reasons)

    step3_structured, step3_struct_reasons = _evaluate_step3_structure(step3_audited_packet)
    checked["step3_audited_packet_structured"] = step3_structured
    fail_reasons.extend(step3_struct_reasons)

    # B (cont). If the run carries buy intent, NEW_BUY must be permitted.
    has_buy_intent = _has_buy_intent(step2_decision_packet, step3_audited_packet)
    new_buy_ok = (not has_buy_intent) or (permission_present and NEW_BUY_ACTION in allowed_actions)
    checked["new_buy_allowed_if_needed"] = new_buy_ok
    if has_buy_intent and not new_buy_ok:
        fail_reasons.append(
            f"run carries buy intent but Step 1 permission does not allow {NEW_BUY_ACTION}."
        )

    # E. LLM self-report is necessary (when present in workflow) but never sufficient.
    audit_passed_ok = _truthy(step3_audited_packet, "audit_passed")
    checked["step3_audit_passed"] = audit_passed_ok
    if not audit_passed_ok:
        fail_reasons.append("Step 3 audit_passed is not true.")

    order_compiler_ready_ok = _truthy(step3_audited_packet, "order_compiler_ready")
    checked["step3_order_compiler_ready"] = order_compiler_ready_ok
    if not order_compiler_ready_ok:
        fail_reasons.append("Step 3 order_compiler_ready is not true.")

    # F. No explicit blockers in Step 2 / Step 3 packets.
    explicit_blockers = _explicit_blockers("step2_decision_packet", step2_decision_packet)
    explicit_blockers += _explicit_blockers("step3_audited_packet", step3_audited_packet)
    no_explicit_blockers = not explicit_blockers
    checked["no_explicit_blockers"] = no_explicit_blockers
    fail_reasons.extend(explicit_blockers)

    # B (cont). manual_review must be false across the permission and packets.
    manual_review_required = _any_manual_review(
        step1_permission, step2_decision_packet, step3_audited_packet, step2_block, step3_block, step4_block
    )
    no_manual_review = not manual_review_required
    checked["step1_no_manual_review"] = no_manual_review
    if manual_review_required:
        fail_reasons.append("manual_review_required is true upstream.")

    ready = all(checked.values())
    if not has_buy_intent:
        non_blocker_reasons = [
            "no buy-side intent detected; order compilation would proceed on a no-new-order path."
        ]
    else:
        non_blocker_reasons = []

    return FinalExecutionSafetyResult(
        ready_for_order_compilation=ready,
        blocked=not ready,
        reason=None if ready else GATE_REASON,
        fail_reasons=fail_reasons,
        blocker_reasons=list(fail_reasons),
        non_blocker_reasons=non_blocker_reasons,
        checked_conditions=checked,
        recommended_result=None if ready else "NO_TRADE",
        manual_review_required=manual_review_required,
        is_deterministic=True,
    )


def enforce_final_execution_safety_gate(
    *,
    blocked_artifact_path: Path,
    step1_permission_path: Path,
    step2_decision_packet_path: Path,
    step3_audited_packet_path: Path,
    step2_block_path: Path,
    step3_block_path: Path,
    step4_block_path: Path,
    repo_root_path: Path | None = None,
) -> FinalExecutionSafetyResult:
    """Fail closed before order compilation when the deterministic gate blocks."""
    step1_permission, _ = _read_json_object(step1_permission_path)
    step2_decision_packet, _ = _read_json_object(step2_decision_packet_path)
    step3_audited_packet, _ = _read_json_object(step3_audited_packet_path)
    step2_block, _ = _read_json_object(step2_block_path)
    step3_block, _ = _read_json_object(step3_block_path)
    step4_block, _ = _read_json_object(step4_block_path)

    result = evaluate_final_execution_safety(
        step2_decision_packet=step2_decision_packet,
        step3_audited_packet=step3_audited_packet,
        step1_permission=step1_permission,
        step2_block=step2_block,
        step3_block=step3_block,
        step4_block=step4_block,
    )
    if result.ready_for_order_compilation:
        return result

    source_artifacts = {
        "step1_permission": _display_path(step1_permission_path, repo_root_path),
        "step2_decision_packet": _display_path(step2_decision_packet_path, repo_root_path),
        "step3_audited_packet": _display_path(step3_audited_packet_path, repo_root_path),
        "step2_block": _display_path(step2_block_path, repo_root_path),
        "step3_block": _display_path(step3_block_path, repo_root_path),
        "step4_block": _display_path(step4_block_path, repo_root_path),
    }
    write_json(
        blocked_artifact_path,
        final_execution_safety_blocked_artifact_payload(result, source_artifacts=source_artifacts),
    )
    raise FinalExecutionSafetyGateError(
        "Step 4 blocked by final execution safety gate: "
        f"fail_reasons={result.fail_reasons}; "
        f"blocked_artifact={_display_path(blocked_artifact_path, repo_root_path)}"
    )


def final_execution_safety_blocked_artifact_payload(
    result: FinalExecutionSafetyResult,
    *,
    source_artifacts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the deterministic blocked / no-trade artifact for the final gate."""
    return {
        "blocked": True,
        "reason": result.reason or GATE_REASON,
        "ready_for_order_compilation": result.ready_for_order_compilation,
        "recommended_result": result.recommended_result or "NO_TRADE",
        "manual_review_required": result.manual_review_required,
        "fail_reasons": list(result.fail_reasons),
        "blocker_reasons": list(result.blocker_reasons),
        "non_blocker_reasons": list(result.non_blocker_reasons),
        "checked_conditions": dict(result.checked_conditions),
        "source_artifacts": dict(source_artifacts or {}),
        "is_deterministic": True,
        "report_only": False,
    }


# --- structural checks -------------------------------------------------------


def _evaluate_step2_structure(packet: Mapping[str, Any] | None) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not isinstance(packet, Mapping):
        return False, ["missing or malformed Step 2 decision packet."]
    if packet.get("decision_builder_ready_for_audit") is not True:
        reasons.append("Step 2 decision_builder_ready_for_audit is not true.")
    universe = packet.get("effective_allowed_buy_universe")
    if not (isinstance(universe, list) and universe and all(isinstance(x, str) and x.strip() for x in universe)):
        reasons.append("Step 2 effective_allowed_buy_universe must be a non-empty string list.")
    if not isinstance(packet.get("MARKET_DATA_SNAPSHOT"), Mapping):
        reasons.append("Step 2 MARKET_DATA_SNAPSHOT must be a JSON object.")
    for field_name in _STEP2_REQUIRED_LIST_FIELDS:
        if not isinstance(packet.get(field_name), list):
            reasons.append(f"Step 2 {field_name} must be a list.")
    return (not reasons), reasons


def _evaluate_step3_structure(packet: Mapping[str, Any] | None) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not isinstance(packet, Mapping):
        return False, ["missing or malformed Step 3 audited packet."]
    for field_name in _STEP3_REQUIRED_LIST_FIELDS:
        if not isinstance(packet.get(field_name), list):
            reasons.append(f"Step 3 {field_name} must be a list.")
    return (not reasons), reasons


def _has_buy_intent(
    step2_decision_packet: Mapping[str, Any] | None,
    step3_audited_packet: Mapping[str, Any] | None,
) -> bool:
    for field_name in _STEP2_BUY_INTENT_FIELDS:
        if _nonempty_list(_get(step2_decision_packet, field_name)):
            return True
    for field_name in _STEP3_BUY_INTENT_FIELDS:
        if _nonempty_list(_get(step3_audited_packet, field_name)):
            return True
    return False


def _explicit_blockers(label: str, packet: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(packet, Mapping):
        return []
    reasons: list[str] = []
    if _nonempty_list(packet.get("blocker_reasons")):
        reasons.append(f"{label}.blocker_reasons is non-empty.")
    if _nonempty_list(packet.get("fatal_errors")):
        reasons.append(f"{label}.fatal_errors is non-empty.")
    for item in _as_list(packet.get("assumptions_and_data_gaps")):
        if isinstance(item, Mapping) and (item.get("blocking") is True or item.get("actionable") is True):
            reasons.append(f"{label}.assumptions_and_data_gaps contains a blocking/actionable DATA_GAP.")
            break
    return reasons


def _any_manual_review(*sources: Mapping[str, Any] | None) -> bool:
    return any(isinstance(s, Mapping) and s.get("manual_review_required") is True for s in sources)


# --- small helpers -----------------------------------------------------------


def _truthy(packet: Mapping[str, Any] | None, key: str) -> bool:
    return isinstance(packet, Mapping) and packet.get(key) is True


def _get(packet: Mapping[str, Any] | None, key: str) -> Any:
    return packet.get(key) if isinstance(packet, Mapping) else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "not found"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable: {exc}"
    if not isinstance(payload, dict):
        return None, "not a JSON object"
    return payload, None


def _display_path(path: Path, repo_root_path: Path | None) -> str:
    if repo_root_path is not None:
        try:
            return str(path.relative_to(repo_root_path))
        except ValueError:
            pass
    return str(path)
